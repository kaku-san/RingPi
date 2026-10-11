from io import BytesIO
from PIL import Image, ImageChops, ImageStat


class MotionDetector:
    """Frame difference within a normalized region; not person recognition."""
    def __init__(self):
        self.previous = None
        self.signature = None
        self.streak = 0

    def reset(self):
        self.previous = None
        self.signature = None
        self.streak = 0

    def score(self, jpeg, zone):
        image = Image.open(BytesIO(jpeg)).convert("L").resize((160, 90))
        x, y = round(zone["x"] * 160), round(zone["y"] * 90)
        width, height = max(1, round(zone["width"] * 160)), max(1, round(zone["height"] * 90))
        image = image.crop((x, y, min(160, x + width), min(90, y + height)))
        signature = tuple(zone.values())
        if self.previous is None or signature != self.signature:
            score = 0.0
        else:
            score = ImageStat.Stat(ImageChops.difference(image, self.previous)).mean[0] / 255
        self.previous, self.signature = image, signature
        return score

    def triggered(self, score, threshold):
        self.streak = self.streak + 1 if score >= threshold else 0
        return self.streak >= 2


class PersonDetector:
    def __init__(self, mode="none"):
        self.mode = mode
        self.hog = None
        self.reason = "No person detector configured"
        if mode == "hog":
            try:
                import cv2
                self.hog = cv2.HOGDescriptor()
                self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
                self.reason = "Experimental CPU pedestrian detector"
            except (ImportError, AttributeError, RuntimeError):
                self.reason = "OpenCV person detector is unavailable"

    @property
    def status(self):
        return {"available": self.hog is not None, "name": "OpenCV HOG" if self.hog else None,
                "detail": self.reason, "identity_matching": False}

    def detect(self, jpeg):
        if self.hog is None:
            return "unavailable"
        import cv2
        import numpy as np
        frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return "unavailable"
        width = min(480, frame.shape[1])
        height = round(frame.shape[0] * width / frame.shape[1])
        if width < 64 or height < 128:
            return "unavailable"
        frame = cv2.resize(frame, (width, height))
        boxes, _ = self.hog.detectMultiScale(frame, winStride=(8, 8), padding=(8, 8), scale=1.08)
        return "detected" if len(boxes) else "not_detected"
