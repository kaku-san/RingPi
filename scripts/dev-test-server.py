"""Explicit synthetic-camera server for automated tests; never a hardware demonstration."""
import os
from pathlib import Path
import tempfile
import uvicorn
from agent_doorbell.app import create_app
from agent_doorbell.config import Config

directory = Path(os.environ.get("DOORBELL_TEST_DATA_DIR", tempfile.mkdtemp(prefix="doorbell-browser-test-")))
app = create_app(Config(data_dir=directory, camera="synthetic", detector="none", background=True,
                        burst_interval=0.08, monitor_interval=1,
                        enrollment_bridge_policy=directory/'absent-policy.json'))
uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("DOORBELL_TEST_PORT", "8878")))
