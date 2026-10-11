from dataclasses import dataclass, field
import os
from pathlib import Path


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get(
        "DOORBELL_DATA_DIR", "~/.local/share/agent-doorbell")).expanduser())
    camera: str = field(default_factory=lambda: os.environ.get("DOORBELL_CAMERA", "auto"))
    detector: str = field(default_factory=lambda: os.environ.get("DOORBELL_DETECTOR", "none"))
    camera_share_url: str = field(default_factory=lambda: os.environ.get(
        "DOORBELL_CAMERA_SHARE_URL", "http://127.0.0.1:8000"))
    burst_frames: int = 3
    burst_interval: float = 1.0
    monitor_interval: float = 2.0
    background: bool = True
    local_captions: bool = field(default_factory=lambda: os.environ.get("DOORBELL_LOCAL_CAPTIONS", "0") == "1")
    caption_runtime: Path | None = field(default_factory=lambda: Path(os.environ["DOORBELL_LLAMA_SERVER"]).expanduser()
                                       if os.environ.get("DOORBELL_LLAMA_SERVER") else None)
    caption_deadline_seconds: float = field(default_factory=lambda: float(os.environ.get("DOORBELL_CAPTION_DEADLINE_SECONDS", "30")))
    enrollment_bridge_policy: Path = field(default_factory=lambda: Path(os.environ.get(
        "DOORBELL_ENROLLMENT_BRIDGE_POLICY", "~/.config/agent-doorbell/enrollment-bridge.json")).expanduser())
    guest_timezone: str = field(default_factory=lambda: os.environ.get("DOORBELL_GUEST_TIMEZONE", "Asia/Kolkata"))


DEFAULT_SETTINGS = {
    "motion_enabled": False,
    "motion_threshold": 0.045,
    "cooldown_seconds": 20,
    "retention_days": 7,
    "zone": {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8},
    "person_alerts_enabled": False,
    "person_alert_seconds": 10,
    "person_alert_cooldown_seconds": 300,
    "quiet_hours_enabled": False,
    "quiet_hours_start": "22:00",
    "quiet_hours_end": "07:00",
    "timezone": "Asia/Kolkata",
    "summary_enabled": True,
    "summary_time": "21:00",
}
