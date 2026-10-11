import asyncio
from io import BytesIO
import json
import shutil
import time
import urllib.error
import urllib.request
from PIL import Image, ImageDraw
from .store import now


class CameraUnavailable(Exception):
    def __init__(self, state, message):
        self.state, self.message = state, message
        super().__init__(message)


class Camera:
    def __init__(self, mode="auto", camera_share_url="http://127.0.0.1:8000"):
        self.binary = shutil.which("rpicam-still") or shutil.which("libcamera-still")
        self.mode = ("rpicam" if self.binary else "none") if mode == "auto" else mode
        self.camera_share_url = camera_share_url.rstrip("/")
        self.lock = asyncio.Lock()
        self.jpeg = None
        self.last_capture = 0.0
        self.captured_at = None
        self.sequence = 0
        self.last_capture_ms = None
        self.state = "ready" if self.mode == "synthetic" else "unverified" if self.binary and self.mode == "rpicam" else "offline"
        self.detail = "Camera test fixture only" if self.mode == "synthetic" else "Take a snapshot to check the camera" if self.state == "unverified" else "No supported Pi camera command found"

    @property
    def status(self):
        return {"state": self.state, "detail": self.detail, "captured_at": self.captured_at,
                "sequence": self.sequence, "simulated": self.mode == "synthetic",
                "mode": self.mode, "has_frame": self.jpeg is not None}

    def _check_camera_share(self):
        if not self.camera_share_url:
            return
        try:
            with urllib.request.urlopen(self.camera_share_url + "/status", timeout=0.5) as response:
                state = json.loads(response.read(16384)).get("state", "").upper()
            if state not in {"READY", "DONE", "ERROR"}:
                raise CameraUnavailable("busy", "Another local session is using or reserving the camera. Retrying later.")
        except urllib.error.URLError as exc:
            # An absent application cannot be using its camera. A stalled guard must fail closed.
            if isinstance(exc.reason, ConnectionRefusedError):
                return
            raise CameraUnavailable("busy", "Camera sharing check did not respond. The existing local session has priority.") from exc
        except (TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise CameraUnavailable("busy", "Camera sharing state is unavailable. The existing local session has priority.") from exc

    def _synthetic(self):
        image = Image.new("RGB", (960, 540), "#24372f")
        draw = ImageDraw.Draw(image)
        draw.rectangle((325, 60, 635, 505), fill="#48644e", outline="#9bc6a5", width=3)
        draw.ellipse((590, 275, 604, 289), fill="#e5c998")
        draw.rectangle((20, 20, 535, 57), fill="#eab96f")
        draw.text((32, 31), "SYNTHETIC CAMERA FIXTURE - NOT A REAL VISITOR", fill="black")
        draw.text((30, 510), now(), fill="white")
        data = BytesIO()
        image.save(data, "JPEG", quality=80)
        return data.getvalue()

    async def capture(self, max_age=0.8):
        async with self.lock:
            if self.jpeg and time.monotonic() - self.last_capture < max_age:
                return self.jpeg
            started = time.perf_counter()
            try:
                if self.mode == "synthetic":
                    jpeg = await asyncio.to_thread(self._synthetic)
                elif self.mode != "rpicam" or not self.binary:
                    raise CameraUnavailable("offline", "Camera unavailable. Ring alerts and history still work.")
                else:
                    await asyncio.to_thread(self._check_camera_share)
                    process = await asyncio.create_subprocess_exec(
                        self.binary, "--nopreview", "--timeout", "350", "--width", "960",
                        "--height", "540", "--quality", "80", "--output", "-",
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    try:
                        jpeg, error = await asyncio.wait_for(process.communicate(), timeout=5)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        if process.returncode is None:
                            process.kill()  # Only this app's own capture subprocess.
                        await process.communicate()
                        raise
                    if process.returncode:
                        busy = any(word in error.lower() for word in [b"busy", b"acquire", b"in use"])
                        raise CameraUnavailable("busy" if busy else "offline", "Camera is busy in another application" if busy else "Camera capture failed. Check camera connection and driver.")
                if len(jpeg) > 5_000_000:
                    raise CameraUnavailable("offline", "Camera frame was larger than expected")
                with Image.open(BytesIO(jpeg)) as image:
                    image.verify()
                self.jpeg, self.last_capture, self.captured_at = jpeg, time.monotonic(), now()
                self.sequence += 1
                self.last_capture_ms = round((time.perf_counter()-started)*1000,1)
                self.state, self.detail = "online", "Synthetic test fixture" if self.mode == "synthetic" else "Fresh snapshot captured locally"
                return jpeg
            except CameraUnavailable as exc:
                self.state, self.detail = exc.state, exc.message
                raise
            except (OSError, ValueError, asyncio.TimeoutError) as exc:
                self.state, self.detail = "offline", "Camera did not produce a usable frame"
                raise CameraUnavailable(self.state, self.detail) from exc
