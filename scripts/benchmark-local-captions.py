"""Explicit cold/warm caption trial on generated shapes; no capture or Bot delivery."""
import argparse
import asyncio
from io import BytesIO
import json
from pathlib import Path
import runpy
import subprocess
import time

from PIL import Image, ImageDraw
from agent_doorbell.captions import CaptionError, LlamaCaptionProvider, caption_smoke_acceptable


async def synthetic_trial(directory, runtime=None, provider=None):
    image = Image.new('RGB', (512, 384), '#f1f1f1')
    draw = ImageDraw.Draw(image)
    draw.rectangle((65, 120, 190, 245), fill='#1956cc')
    draw.ellipse((285, 105, 425, 245), fill='#dc352a')
    data = BytesIO()
    image.save(data, 'JPEG', quality=85)
    provider = provider if provider is not None else LlamaCaptionProvider(directory, runtime)
    trials = []
    peak_rss = 0
    temperature = Path('/sys/class/thermal/thermal_zone0/temp')
    def thermal():
        try: return round(int(temperature.read_text().strip()) / 1000, 1)
        except (OSError, ValueError): return None
    async def sample():
        nonlocal peak_rss
        while True:
            process = provider.process
            if process and process.returncode is None:
                rss = resident_bytes(process.pid)
                if rss:
                    peak_rss = max(peak_rss, rss)
            await asyncio.sleep(.1)
    monitor = asyncio.create_task(sample())
    before = thermal()
    try:
        for label in ('cold', 'warm'):
            start = time.monotonic()
            try:
                text = await asyncio.wait_for(provider.caption(data.getvalue()), timeout=30)
            except asyncio.TimeoutError:
                trials.append({'mode': label, 'completed': False, 'elapsed_seconds': round(time.monotonic()-start, 3), 'issue': '30-second whole-caption deadline exceeded'})
                break
            except (CaptionError, OSError, ValueError, KeyError, TypeError, IndexError):
                trials.append({'mode': label, 'completed': False, 'elapsed_seconds': round(time.monotonic()-start, 3), 'issue': 'Pinned local runtime did not return a usable caption'})
                break
            usable = caption_smoke_acceptable(text)
            trials.append({'mode': label, 'completed': True, 'elapsed_seconds': round(time.monotonic()-start, 3),
                           'caption_unverified': text[:1000], 'smoke_text_accepted': usable})
    finally:
        after = thermal()
        await provider.close(force=True)
        monitor.cancel()
        try: await monitor
        except asyncio.CancelledError: pass
    return {'input': 'locally generated 512x384 JPEG of a blue square and a red circle; no people or camera',
            'trials': trials, 'peak_runtime_rss_bytes': peak_rss or None,
            'temperature_before_C': before, 'temperature_after_C': after,
            'deadline_seconds_each': 30, 'cpu_threads': 2,
            'caption_accuracy_verified': False,
            'smoke_gate': 'nondegenerate text only; scene accuracy is a separate, unverified question',
            'camera_capture': False,
            'bot_send': False, 'enrollment': False, 'actuation': False,
            'owned_runtime_stopped': provider.process is None}


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    helper = runpy.run_path(str(Path(__file__).with_name("caption-setup.py")))
    try:
        directory, runtime = helper["paths"](helper["private_lines"](
            Path.home() / ".config/agent-doorbell/environment"))
        result = asyncio.run(synthetic_trial(directory, runtime))
    except (OSError, ValueError):
        raise SystemExit("Caption configuration could not be inspected; no capture or Bot send occurred.") from None
    print(json.dumps(result, indent=2))
    accepted = len(result["trials"]) == 2 and all(
        item["completed"] and item.get("smoke_text_accepted") for item in result["trials"])
    return 0 if accepted else 1


def resident_bytes(pid):
    """Prefer Linux VmHWM/VmRSS. On macOS, sample current RSS with ps."""
    try:
        for line in Path('/proc', str(pid), 'status').read_text().splitlines():
            if line.startswith(('VmRSS:', 'VmHWM:')):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        raw = subprocess.check_output(['ps', '-o', 'rss=', '-p', str(pid)], text=True, timeout=2).strip()
        return int(raw.split()[0]) * 1024 if raw else None
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
