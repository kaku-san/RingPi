"""Explicit installation of pinned, licensed local OpenCV models. Never downloads on app startup."""
import argparse
import hashlib
from pathlib import Path
import os
import tempfile
import shutil
import urllib.request
from agent_doorbell.config import Config
from agent_doorbell.local_vision import MODELS, SOURCE_REVISION

parser = argparse.ArgumentParser()
parser.add_argument("--directory", type=Path)
args = parser.parse_args()
directory = args.directory or Config().data_dir / "models"
directory.mkdir(parents=True, exist_ok=True, mode=0o700)
licenses = Path(__file__).resolve().parents[1] / "docs" / "licenses"
for name in ["YuNet-MIT.txt", "SFace-Apache-2.0.txt", "NanoDet-Apache-2.0.txt"]:
    if (licenses / name).is_file():
        shutil.copyfile(licenses / name, directory / name)
        (directory / name).chmod(0o600)
for name, (folder, checksum, size, license_name) in MODELS.items():
    target = directory / name
    if target.is_file() and target.stat().st_size == size and hashlib.sha256(target.read_bytes()).hexdigest() == checksum:
        print(name, "already verified"); continue
    url = f"https://media.githubusercontent.com/media/opencv/opencv_zoo/{SOURCE_REVISION}/models/{folder}/{name}"
    print("Installing", name, "bytes", size, "license", license_name)
    temporary = None
    try:
        with urllib.request.urlopen(url, timeout=60) as response, tempfile.NamedTemporaryFile(dir=directory, delete=False) as output:
            temporary = Path(output.name); total = 0; digest = hashlib.sha256()
            while block := response.read(1024*1024):
                total += len(block)
                if total > size:
                    raise RuntimeError("Model exceeds pinned size")
                digest.update(block); output.write(block)
        if total != size or digest.hexdigest() != checksum:
            raise RuntimeError("Model checksum does not match official pinned weights")
        temporary.chmod(0o600); os.replace(temporary,target)
    finally:
        if temporary and temporary.exists(): temporary.unlink()
print("Models verified. Restart only agent-doorbell to load them.")
