"""Bounded local frame work and conservative, data-only burst identity evidence."""
import asyncio
import math
import time

from .guest_recognition import GUEST_STATES, aggregate_guest
from .local_vision import LocalVision
from .object_labels import OBJECT_LABELS


MAX_BURST_FRAMES = 5
FACE_STATES = {"probable_match", "uncertain", "unknown", "not_enrolled", "no_face",
               "low_quality", "multiple_faces", "unavailable", "enrollment_deleted"}
TIMING_KEYS = {"yunet", "sface", "nanodet", "analysis_total", "camera_acquisition", "queue_wait"}
FACE_METRICS = ("face_side", "laplacian", "detector_score", "measured_width", "measured_height", "downscaled")


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _metric(item, key):
    value = number(item.get(key))
    return value if value is not None and value >= 0 else -1


def _clarity(item):
    usable = item.get("quality") == "usable"
    present = _metric(item, "face_side") >= 0 or _metric(item, "laplacian") >= 0
    return (1 if usable else 0, 1 if present else 0, _metric(item, "laplacian"),
            _metric(item, "face_side"), _metric(item, "detector_score"), item.get("index", 0))


def best_frame_index(frames):
    """Index of the clearest eligible face, else the sharpest detected face."""
    if not frames:
        return None
    return max(frames, key=_clarity).get("index")


class FrameAnalysisQueue:
    """One running model call plus one waiting frame; overflow is explicit data.

    Cancelling a capture does not release a still-running native OpenCV call.
    The worker finishes that call before taking another, and skips cancelled jobs.
    """
    def __init__(self, analyze):
        self.analyze = analyze
        self.queue = asyncio.Queue(maxsize=2)
        self.outstanding = 0
        self.task = None
        self.closed = False

    async def submit(self, frame):
        if self.closed or self.outstanding >= 2:
            return {"status": "unavailable", "error": "analysis_busy", "objects": [], "timings_ms": {}}
        future = asyncio.get_running_loop().create_future()
        self.outstanding += 1
        self.queue.put_nowait((frame, future, time.monotonic()))
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run())
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            future.cancel()
            raise

    async def _run(self):
        while not self.queue.empty():
            frame, future, queued_at = self.queue.get_nowait()
            try:
                if future.cancelled() or self.closed:
                    if not future.done():
                        future.cancel()
                    continue
                wait_ms = round((time.monotonic() - queued_at) * 1000, 1)
                try:
                    result = await asyncio.to_thread(self.analyze, frame)
                    result = dict(result)
                    result["timings_ms"] = dict(result.get("timings_ms") or {}, queue_wait=wait_ms)
                except Exception:
                    result = {"status": "unavailable", "error": "analysis_failed", "objects": [],
                              "timings_ms": {"queue_wait": wait_ms}}
                if not future.done():
                    future.set_result(result)
            finally:
                self.outstanding -= 1
                self.queue.task_done()

    async def close(self):
        self.closed = True
        if self.task:
            await self.task


class BurstRecognition:
    def __init__(self, expected_frames):
        if not 1 <= expected_frames <= MAX_BURST_FRAMES:
            raise ValueError("A burst must contain between one and five frames")
        self.expected_frames = expected_frames
        self.frames = []

    def add(self, index, analysis, camera_ms):
        """Whitelist diagnostics: never retain raw vectors, names, or error text."""
        if len(self.frames) >= self.expected_frames or index != len(self.frames):
            raise ValueError("Each captured frame is analyzed once, in order")
        status = analysis.get("status")
        if status not in FACE_STATES:
            status = "unavailable"
        timings = {key: value for key, value in (analysis.get("timings_ms") or {}).items()
                   if key in TIMING_KEYS and number(value) is not None and value >= 0}
        if number(camera_ms) is not None and camera_ms >= 0:
            timings["camera_acquisition"] = camera_ms
        objects = [{"label": obj["label"], "confidence": obj["confidence"]}
                   for obj in analysis.get("objects", [])[:10]
                   if obj.get("label") in OBJECT_LABELS and number(obj.get("confidence")) is not None
                   and .5 <= obj["confidence"] <= 1]
        count = number(analysis.get("person_count"))
        person_count = max(sum(obj["label"] == "person" for obj in objects),
                           min(10, int(count)) if count is not None and count >= 0 else 0)
        similarity, margin = number(analysis.get("similarity")), number(analysis.get("margin"))
        # The same per-frame acceptance gates apply; aggregation never lowers them.
        if status == "probable_match" and (not analysis.get("profile_id") or similarity is None
                or not LocalVision.threshold <= similarity <= 1 or margin is None or not LocalVision.margin <= margin <= 2):
            status = "uncertain"
        item = {"index": index, "status": status,
                "quality": "usable" if status in {"probable_match", "uncertain", "unknown", "not_enrolled"} else status,
                "profile_id": analysis.get("profile_id") if status == "probable_match" else None,
                "similarity": similarity, "margin": margin, "person_count": person_count,
                "objects": objects, "timings_ms": timings}
        item["person_status"] = analysis.get("person_status") if analysis.get("person_status") in {
            "detected", "not_detected", "unavailable"} else "unavailable"
        if analysis.get("error") in {"analysis_busy", "analysis_failed", "analysis_interrupted"}:
            item["error"] = analysis["error"]
        for key in FACE_METRICS:
            value = number(analysis.get(key))
            if value is not None and value >= 0:
                item[key] = value
        guest = analysis.get("guest") if isinstance(analysis.get("guest"), dict) else None
        if guest:
            gstatus = guest.get("status") if guest.get("status") in GUEST_STATES else "unavailable"
            visitor = guest.get("visitor_id") if gstatus == "probable_match" and isinstance(guest.get("visitor_id"), str) else None
            if guest.get("identity_proven") or guest.get("authorizes_entry"):
                gstatus, visitor = "uncertain", None
            item["guest"] = {"status": gstatus, "visitor_id": visitor,
                             "similarity": number(guest.get("similarity")) if gstatus == "probable_match" else None,
                             "identity_proven": False, "authorizes_entry": False}
        self.frames.append(item)

    def result(self, enrolled_ids, complete=False):
        # Revalidate all earlier evidence after every frame, including deleted enrollment.
        for item in self.frames:
            if item["profile_id"] and item["profile_id"] not in enrolled_ids:
                item.update(status="enrollment_deleted", quality="enrollment_deleted",
                            profile_id=None, similarity=None, margin=None)
        states = {item["status"] for item in self.frames}
        matches = [item for item in self.frames if item["status"] == "probable_match"]
        people = {item["profile_id"] for item in matches}
        final = complete and len(self.frames) == self.expected_frames
        if "multiple_faces" in states:
            status, reason = "multiple_faces", "multiple_faces"
        elif any(item["person_count"] > 1 for item in self.frames):
            status, reason = "uncertain", "multiple_people"
        elif len(people) > 1:
            status, reason = "uncertain", "identity_disagreement"
        elif matches and states & {"unknown", "uncertain", "not_enrolled", "enrollment_deleted"}:
            status, reason = "uncertain", "conflicting_face_evidence"
        elif matches and ("unavailable" in states or (complete and not final)):
            status, reason = "uncertain", "analysis_incomplete"
        elif matches:
            status, reason = ("probable_match", "usable_faces_agree") if final else ("pending", "burst_in_progress")
        else:
            status = next((state for state in ("enrollment_deleted", "uncertain", "unknown", "not_enrolled",
                           "unavailable", "low_quality", "no_face") if state in states), "unavailable")
            reason = "analysis_incomplete" if "unavailable" in states else "no_accepted_match"
        objects = {}
        for item in self.frames:
            for obj in item["objects"]:
                objects.setdefault(obj["label"], obj)
        timings = {key: round(sum(item["timings_ms"].get(key, 0) for item in self.frames), 1)
                   for key in TIMING_KEYS if any(key in item["timings_ms"] for item in self.frames)}
        accepted = status == "probable_match"
        chosen = best_frame_index(self.frames)
        if accepted:
            ranked = sorted(matches, key=_clarity)
            has_quality = any(_metric(item, "laplacian") >= 0 or _metric(item, "face_side") >= 0 for item in matches)
            if has_quality:
                similarity, margin = ranked[-1]["similarity"], ranked[-1]["margin"]
            else:
                similarity = min(item["similarity"] for item in matches)
                margin = min(item["margin"] for item in matches)
        else:
            similarity = margin = None
        person_status = "detected" if any(item["person_status"] == "detected" or item["quality"] in {
            "usable", "low_quality", "multiple_faces"} for item in self.frames) else (
            "not_detected" if any(item["person_status"] == "not_detected" for item in self.frames) else "unavailable")
        guest = aggregate_guest(self.frames, final, self.expected_frames)
        return {"status": status, "profile_id": matches[0]["profile_id"] if accepted else None,
                "guest": guest,
                "similarity": similarity, "margin": margin,
                "objects": list(objects.values())[:10], "timings_ms": timings, "person_status": person_status,
                "frames": [dict(item) for item in self.frames], "expected_frames": self.expected_frames,
                "complete": final, "aggregation": {"rule": "best_eligible_face", "reason": reason,
                    "chosen_frame": chosen,
                    "usable_frames": sum(item["quality"] == "usable" for item in self.frames),
                    "probable_frames": len(matches)},
                "identity_proven": False}
