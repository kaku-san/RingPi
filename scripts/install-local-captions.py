"""Explicit owner-run, pinned CPU-only caption installer. No sudo/system changes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request

from agent_doorbell.caption_model import MODEL_FILES, MODEL_NAME, MODEL_REPO, MODEL_REVISION, RUNTIME_REVISION
from agent_doorbell.captions import CaptionError, LlamaCaptionProvider, file_digest


def custom_runtime(directory, runtime):
    return runtime is not None and os.path.abspath(runtime) != os.path.abspath(directory / 'runtime/build/bin/llama-server')


def installation_status(directory, runtime=None):
    provider = LlamaCaptionProvider(directory, runtime)
    present = available = None
    try:
        present = provider.runtime.is_file() and all((directory / name).is_file() for name in MODEL_FILES)
        available = provider.status['available']
        provider.verify_files()
    except CaptionError as error:
        ready, issue = False, str(error)
    except (ValueError, OSError, AttributeError, TypeError, IndexError):
        ready, issue = False, 'Pinned caption files or provenance could not be verified.'
    else:
        ready, issue = True, None
    return {'files_present': present, 'files_available': available,
            'pinned_files_verified': ready, 'verification_issue': issue,
            'runtime_selection': 'custom' if custom_runtime(directory, runtime) else 'managed',
            'install_supported': not custom_runtime(directory, runtime)}


def download_model(url, target, size, expected):
    if target.is_file() and target.stat().st_size == size and file_digest(target) == expected:
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
            temporary = Path(output.name)
            os.chmod(temporary, 0o600)
            digest = hashlib.sha256(); count = 0
            request = urllib.request.Request(url, headers={"User-Agent": "agent-doorbell-pinned-model-install"})
            with urllib.request.urlopen(request, timeout=30) as response:
                while block := response.read(1_048_576):
                    count += len(block)
                    if count > size:
                        raise ValueError("Pinned download exceeds its expected size")
                    digest.update(block); output.write(block)
            output.flush(); os.fsync(output.fileno())
        if count != size or digest.hexdigest() != expected:
            raise ValueError("Pinned model size/checksum verification failed")
        os.replace(temporary, target)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def install(directory, runtime=None):
    if custom_runtime(directory, runtime):
        raise ValueError('DOORBELL_LLAMA_SERVER or --runtime selects a custom runtime. --install only builds the managed default; '
                         'provision the reviewed custom runtime separately or explicitly select the managed default. '
                         'No files or configuration changed.')
    tools = {name: shutil.which(name) for name in ("cmake", "git", "c++", "make")}
    missing = [name for name, path in tools.items() if not path]
    if missing:
        raise ValueError("Missing build tools: " + ", ".join(missing) + ". Install these separately, then rerun. No model downloaded or system package changed.")
    marker = directory / ".agent-doorbell-captions-managed"
    if directory.is_symlink() or (directory.exists() and any(directory.iterdir()) and not marker.is_file()):
        raise ValueError("Refusing to replace an unmanaged caption directory")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    if shutil.disk_usage(directory).free < 1_073_741_824:
        raise ValueError("Allow at least 1 GiB free for the model, source and runtime build")
    marker.write_text("Pinned Agent Doorbell local caption files\n"); marker.chmod(0o600)
    runtime = directory / "runtime"; source = runtime / "source"; build = runtime / "build"
    runtime.mkdir(exist_ok=True, mode=0o700)
    if not source.exists():
        source.mkdir(mode=0o700)
        subprocess.run([tools["git"], "init", str(source)], check=True)
        subprocess.run([tools["git"], "-C", str(source), "remote", "add", "origin", "https://github.com/ggml-org/llama.cpp.git"], check=True)
    origin = subprocess.check_output([tools["git"], "-C", str(source), "remote", "get-url", "origin"], text=True).strip()
    if origin != "https://github.com/ggml-org/llama.cpp.git":
        raise ValueError("Runtime source is not the pinned official repository")
    subprocess.run([tools["git"], "-C", str(source), "fetch", "--depth=1", "origin", RUNTIME_REVISION], check=True)
    if subprocess.check_output([tools["git"], "-C", str(source), "status", "--porcelain"], text=True).strip():
        raise ValueError("Runtime source has local changes; refusing to overwrite them")
    subprocess.run([tools["git"], "-C", str(source), "checkout", "--detach", RUNTIME_REVISION], check=True)
    revision = subprocess.check_output([tools["git"], "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if revision != RUNTIME_REVISION:
        raise ValueError("Runtime source revision mismatch")
    subprocess.run([tools["cmake"], "-S", str(source), "-B", str(build), "-G", "Unix Makefiles",
                    "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_SHARED_LIBS=OFF",
                    "-DGGML_METAL=OFF", "-DGGML_CUDA=OFF", "-DGGML_VULKAN=OFF", "-DGGML_BLAS=OFF",
                    "-DGGML_OPENMP=OFF", "-DGGML_CPU_KLEIDIAI=OFF", "-DGGML_RPC=OFF",
                    "-DLLAMA_OPENSSL=OFF", "-DLLAMA_SUBPROCESS=OFF", "-DLLAMA_BUILD_TESTS=OFF",
                    "-DLLAMA_BUILD_EXAMPLES=OFF", "-DLLAMA_BUILD_APP=OFF", "-DLLAMA_BUILD_SERVER=ON", "-DLLAMA_BUILD_UI=OFF",
                    "-DLLAMA_USE_PREBUILT_UI=OFF"], check=True)
    subprocess.run([tools["cmake"], "--build", str(build), "--config", "Release", "--target", "llama-server", "-j", "2"], check=True)
    binary = build / "bin/llama-server"
    if not binary.is_file():
        raise ValueError("Build did not produce llama-server")
    provenance = runtime / "provenance.json"
    provenance.write_text(json.dumps({"revision": revision, "binary_sha256": file_digest(binary),
                                     "source": "https://github.com/ggml-org/llama.cpp", "license": "MIT", "cpu_only": True}, indent=2))
    provenance.chmod(0o600)
    shutil.copyfile(source / "LICENSE", runtime / "llama.cpp-MIT.txt")
    for name, (size, expected) in MODEL_FILES.items():
        print("PINNED_MODEL", name, size, flush=True)
        download_model(f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/{name}", directory / name, size, expected)
    license_path = Path(__file__).resolve().parents[1] / "docs/licenses/NanoDet-Apache-2.0.txt"
    shutil.copyfile(license_path, directory / "SmolVLM-Apache-2.0.txt")
    (directory / "MODEL-NOTICE.txt").write_text(
        f"{MODEL_NAME}\nApache-2.0\nOriginal: https://huggingface.co/HuggingFaceTB/SmolVLM-256M-Instruct\n"
        f"GGUF maintainer source: https://huggingface.co/{MODEL_REPO}/tree/{MODEL_REVISION}\n"
        "279 MB model/projector disk size does not establish runtime RAM or Pi performance.\n")
    LlamaCaptionProvider(directory).verify_files()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="explicitly download and build reviewed local caption dependencies")
    parser.add_argument("--data-dir", default=os.environ.get("DOORBELL_DATA_DIR", "~/.local/share/agent-doorbell"))
    parser.add_argument("--runtime", default=os.environ.get("DOORBELL_LLAMA_SERVER"),
                        help="inspect this reviewed runtime; installation only supports the managed default")
    args = parser.parse_args()
    directory = Path(args.data_dir).expanduser() / "captions"
    runtime = Path(args.runtime).expanduser() if args.runtime else None
    print(json.dumps({"model": MODEL_NAME, "model_revision": MODEL_REVISION, "runtime_revision": RUNTIME_REVISION,
                      "download_bytes": sum(size for size, _ in MODEL_FILES.values()),
                      "tools": {tool: bool(shutil.which(tool)) for tool in ("cmake", "git", "c++", "make")},
                      **installation_status(directory, runtime),
                      "capture": False, "model_run": False, "system_packages_changed": False}), flush=True)
    if args.install:
        os.umask(0o077)
        try:
            install(directory, runtime)
        except (ValueError, OSError, subprocess.CalledProcessError) as error:
            raise SystemExit(str(error)) from None
        print("PINNED_LOCAL_CAPTION_FILES_READY. Enable DOORBELL_LOCAL_CAPTIONS=1 in the private service environment after review. No capture or inference performed.")


if __name__ == "__main__":
    main()
