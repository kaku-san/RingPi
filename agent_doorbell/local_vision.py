"""Local OpenCV YuNet/SFace identity matching and NanoDet object detection."""
from io import BytesIO
import hashlib
import json
import math
from pathlib import Path
import threading
import time

from PIL import Image, ImageOps

from .object_labels import OBJECT_CLASSES

SOURCE_REVISION = "47534e27c9851bb1128ccc0102f1145e27f23f98"
MODELS = {
    "face_detection_yunet_2023mar.onnx": ("face_detection_yunet", "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4", 232589, "MIT"),
    "face_recognition_sface_2021dec.onnx": ("face_recognition_sface", "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79", 38696353, "Apache-2.0"),
    "object_detection_nanodet_2022nov.onnx": ("object_detection_nanodet", "4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186", 3800954, "Apache-2.0"),
}
FACE_MODEL = "face_recognition_sface_2021dec.onnx"
# Measured on the decoded frame. A 960-wide capture is not scaled down first.
# 70px is about 7% of that width, so a clear frontal face around a quarter of the frame passes.
MIN_FACE_SIDE = 70
# Laplacian variance of the face crop. A clear door-distance face measured 37.6; 40 rejected it.
MIN_LAPLACIAN = 30.0


def assess_face_gate(face_side, laplacian, frame_width):
    """Usable when the face is large and sharp enough on the measured frame."""
    if not frame_width or face_side < MIN_FACE_SIDE or laplacian < MIN_LAPLACIAN:
        return "low_quality"
    return "usable"


class FaceError(Exception):
    pass


def clean_image(raw):
    if len(raw) > 8_000_000:
        raise FaceError("Choose a JPEG or PNG under 8 MB")
    try:
        with Image.open(BytesIO(raw)) as image:
            if image.format not in {"JPEG", "PNG"} or image.width * image.height > 20_000_000 or getattr(image, "n_frames", 1) != 1:
                raise FaceError("Choose one JPEG or PNG photo under 20 megapixels")
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((1280, 1280))
            output = BytesIO(); image.save(output, "JPEG", quality=90)
            return output.getvalue()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise FaceError("The file is not a supported readable photo") from None


def shrink_jpeg(raw, limit):
    """Longest side down, JPEG, until the bytes fit the enrollment limit."""
    jpeg = clean_image(raw)
    if len(jpeg) <= limit:
        return jpeg
    with Image.open(BytesIO(jpeg)) as image:
        image = image.convert("RGB")
        side = max(image.size)
        quality = 75
        while len(jpeg) > limit and side >= 160:
            side = max(160, int(side * 0.75))
            copy = image.copy()
            copy.thumbnail((side, side))
            output = BytesIO()
            copy.save(output, "JPEG", quality=quality)
            jpeg = output.getvalue()
            if side == 160 and quality <= 40:
                break
            if side == 160:
                quality = 40
    if len(jpeg) > limit:
        raise FaceError("Choose a JPEG or PNG under 8 MB")
    return jpeg


class LocalVision:
    threshold = 0.50
    uncertain_threshold = 0.35
    margin = 0.10

    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.Lock()
        self.detector = self.recognizer = self.objects = None
        self.cv = self.np = None
        self.detail = "Local models not installed; run scripts/install-local-models.py on the Pi"
        self.last_timings = {}
        try:
            import cv2
            import numpy as np
            self.cv, self.np = cv2, np
            cv2.setNumThreads(2)
            valid = set()
            for name, (_, checksum, size, _) in MODELS.items():
                path = self.directory / name
                if path.is_file() and path.stat().st_size == size and hashlib.sha256(path.read_bytes()).hexdigest() == checksum:
                    valid.add(name)
            if "face_detection_yunet_2023mar.onnx" in valid and FACE_MODEL in valid:
                self.detector = cv2.FaceDetectorYN.create(str(self.directory / "face_detection_yunet_2023mar.onnx"), "", (320, 320), 0.9, 0.3, 5000)
                self.recognizer = cv2.FaceRecognizerSF.create(str(self.directory / FACE_MODEL), "")
            if "object_detection_nanodet_2022nov.onnx" in valid:
                from .vendor.nanodet import NanoDet
                self.objects = NanoDet(str(self.directory / "object_detection_nanodet_2022nov.onnx"), prob_threshold=0.50)
            if self.detector:
                self.detail = "YuNet + SFace ready; probable enrolled matches only"
        except Exception:
            self.detail = "OpenCV or verified local models unavailable"

    @property
    def status(self):
        return {"available": self.detector is not None and self.recognizer is not None,
                "face_detector": "YuNet 2023mar", "face_embeddings": "SFace 2021dec",
                "object_detector": "NanoDet 2022nov", "objects_available": self.objects is not None,
                "object_classes": list(OBJECT_CLASSES), "detail": self.detail, "local": True,
                "match_threshold": self.threshold, "ambiguity_margin": self.margin,
                "calibrated_for_this_door": False, "last_timings_ms": self.last_timings}

    def decode(self, raw):
        image = self.cv.imdecode(self.np.frombuffer(raw, self.np.uint8), self.cv.IMREAD_COLOR)
        if image is None:
            raise FaceError("No readable image")
        downscaled = max(image.shape[:2]) > 960
        if downscaled:
            factor = 960 / max(image.shape[:2])
            image = self.cv.resize(image, (round(image.shape[1]*factor), round(image.shape[0]*factor)))
        return image, downscaled

    def _measurements(self, image, detection_ms, face=None, downscaled=False):
        measurements = {"yunet": round(detection_ms, 1), "measured_width": int(image.shape[1]),
                        "measured_height": int(image.shape[0]), "downscaled": 1 if downscaled else 0}
        if face is None:
            return measurements
        x, y, w, h = face[:4]
        crop = image[max(0,int(y)):min(image.shape[0],int(y+h)), max(0,int(x)):min(image.shape[1],int(x+w))]
        laplacian = self.cv.Laplacian(self.cv.cvtColor(crop, self.cv.COLOR_BGR2GRAY), self.cv.CV_64F).var() if crop.size else 0
        measurements.update(face_side=round(float(min(w, h)), 1), laplacian=round(float(laplacian), 2),
                            detector_score=round(float(face[-1]), 4))
        return measurements

    def extract(self, image, downscaled=False):
        if not self.detector or not self.recognizer:
            raise FaceError(self.detail)
        started = time.perf_counter()
        self.detector.setInputSize((image.shape[1], image.shape[0]))
        _, faces = self.detector.detect(image)
        detection_ms = (time.perf_counter() - started) * 1000
        if faces is None or len(faces) == 0:
            return None, "no_face", self._measurements(image, detection_ms, downscaled=downscaled)
        if len(faces) != 1:
            return None, "multiple_faces", self._measurements(image, detection_ms, downscaled=downscaled)
        face = faces[0]
        measurements = self._measurements(image, detection_ms, face, downscaled)
        if assess_face_gate(measurements["face_side"], measurements["laplacian"], measurements["measured_width"]) != "usable":
            return None, "low_quality", measurements
        started = time.perf_counter()
        feature = self.recognizer.feature(self.recognizer.alignCrop(image, face)).reshape(-1).astype(float)
        norm = self.np.linalg.norm(feature)
        if not self.np.isfinite(feature).all() or not norm:
            raise FaceError("Could not produce a usable face embedding")
        measurements.update(sface=round((time.perf_counter()-started)*1000, 1))
        return feature / norm, "usable", measurements

    def enroll(self, raw):
        jpeg = clean_image(raw)
        if not self.cv:
            raise FaceError(self.detail)
        with self.lock:
            image, downscaled = self.decode(jpeg)
            vector, quality, timings = self.extract(image, downscaled)
        messages = {"no_face": "No clear face found. Choose a front-facing photo.", "multiple_faces": "Choose a photo containing exactly one person.",
                    "low_quality": "Face is too small or blurry. Choose a sharper, closer photo."}
        if vector is None:
            raise FaceError(messages[quality])
        return jpeg, vector.tolist(), timings

    def recognize_photo(self, raw, samples):
        """Test an owner-chosen photo without storing it or running object detection."""
        jpeg = clean_image(raw)
        if not self.cv:
            raise FaceError(self.detail)
        with self.lock:
            image, downscaled = self.decode(jpeg)
            vector, quality, timings = self.extract(image, downscaled)
            result = self.match(vector, samples) if vector is not None else {
                "status": quality, "profile_id": None, "similarity": None}
        return {**result, "quality": quality, "timings_ms": timings}

    def match(self, vector, samples):
        scores = {}
        for sample in samples:
            if sample["model"] != MODELS[FACE_MODEL][1]:
                continue
            candidate = self.np.asarray(sample["embedding"], dtype=float)
            if candidate.shape != vector.shape or not self.np.isfinite(candidate).all():
                continue
            score = float(self.np.dot(vector, candidate))
            scores[sample["profile_id"]] = max(score, scores.get(sample["profile_id"], -1))
        ranked = sorted(scores.items(), key=lambda item:item[1], reverse=True)
        if not ranked:
            return {"status": "not_enrolled", "profile_id": None, "similarity": None}
        profile, best = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else -1
        status = "probable_match" if best >= self.threshold and best-second >= self.margin else "uncertain" if best >= self.uncertain_threshold else "unknown"
        return {"status": status, "profile_id": profile if status == "probable_match" else None,
                "similarity": round(best, 3), "margin": round(best-second,3)}

    def match_guest(self, vector, guest_samples):
        """Compare one embedding with temporary guest references. The result cannot grant entry."""
        adapted = []
        for sample in guest_samples:
            if sample.get("kind") != "temporary_guest" or not isinstance(sample.get("visitor_id"), str):
                continue
            adapted.append({"profile_id": sample["visitor_id"], "embedding": sample.get("embedding"),
                            "model": sample.get("model")})
        matched = self.match(vector, adapted)
        return {"status": matched["status"], "visitor_id": matched["profile_id"],
                "similarity": matched["similarity"], "margin": matched.get("margin"),
                "identity_proven": False, "authorizes_entry": False}

    def analyze(self, raw, samples, guest_samples=None):
        started = time.perf_counter()
        result = {"status": "unavailable", "profile_id": None, "similarity": None, "objects": [], "timings_ms": {}}
        if not self.cv:
            return result
        with self.lock:
            image, downscaled = self.decode(raw)
            vector = None
            quality = "unavailable"
            if self.detector:
                vector, quality, timings = self.extract(image, downscaled)
                result.update(self.match(vector, samples) if vector is not None else {"status": quality})
                for key in ("face_side", "laplacian", "detector_score", "measured_width", "measured_height", "downscaled"):
                    if key in timings:
                        result[key] = timings[key]
                result["timings_ms"].update({key: timings[key] for key in ("yunet", "sface") if key in timings})
            if guest_samples is not None:
                if vector is not None:
                    result["guest"] = self.match_guest(vector, guest_samples)
                else:
                    guest_status = quality if quality in {"no_face", "multiple_faces", "low_quality"} else "unavailable"
                    result["guest"] = {"status": guest_status, "visitor_id": None, "similarity": None,
                                       "margin": None, "identity_proven": False, "authorizes_entry": False}
            if self.objects:
                object_started = time.perf_counter()
                factor = min(416/image.shape[1],416/image.shape[0])
                width,height = max(1,int(image.shape[1]*factor)),max(1,int(image.shape[0]*factor))
                resized = self.cv.resize(image,(width,height),interpolation=self.cv.INTER_AREA)
                padded = self.np.zeros((416,416,3),self.np.uint8)
                top,left=(416-height)//2,(416-width)//2
                padded[top:top+height,left:left+width]=resized
                detections = self.objects.infer(padded)
                objects = []
                for detection in detections:
                    confidence, class_id = float(detection[4]), float(detection[5])
                    if (math.isfinite(class_id) and class_id.is_integer()
                            and 0 <= class_id < len(OBJECT_CLASSES)
                            and math.isfinite(confidence) and 0.50 <= confidence <= 1):
                        objects.append({"label": OBJECT_CLASSES[int(class_id)],
                                        "confidence": round(confidence, 3)})
                result["person_count"] = min(10, sum(obj["label"] == "person" for obj in objects))
                result["objects"] = sorted(objects, key=lambda obj: obj["confidence"], reverse=True)[:10]
                result["timings_ms"]["nanodet"] = round((time.perf_counter()-object_started)*1000,1)
        result["timings_ms"]["analysis_total"] = round((time.perf_counter()-started)*1000,1)
        self.last_timings = result["timings_ms"]
        return result
