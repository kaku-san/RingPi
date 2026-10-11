"""Generated-image runner checks; no model files, SSH, camera or external calls."""
from io import BytesIO
from pathlib import Path
import asyncio
import runpy

from PIL import Image
import pytest
from agent_doorbell.captions import CaptionError

benchmark = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/benchmark-local-captions.py"))["synthetic_trial"]


class FixtureProvider:
    process = None

    def __init__(self, fail_on=None, timeout=False):
        self.fail_on, self.timeout = fail_on, timeout
        self.calls = 0
        self.closed = []

    async def caption(self, jpeg):
        self.calls += 1
        with Image.open(BytesIO(jpeg)) as image:
            assert image.format == "JPEG" and image.size == (512, 384)
            assert image.getpixel((120, 180))[2] > 150
            assert image.getpixel((350, 180))[0] > 150
        if self.calls == self.fail_on:
            raise asyncio.TimeoutError if self.timeout else CaptionError("fixture failure")
        return "A blue square and a red circle on a pale background."

    async def close(self, force=False):
        self.closed.append(force)


def test_generated_caption_trial_uses_one_provider_for_cold_then_warm_and_stops_it(tmp_path):
    provider = FixtureProvider()
    result = asyncio.run(benchmark(tmp_path, provider=provider))
    assert [trial["mode"] for trial in result["trials"]] == ["cold", "warm"]
    assert all(trial["completed"] for trial in result["trials"])
    assert all(trial["elapsed_seconds"] >= 0 for trial in result["trials"])
    assert result["deadline_seconds_each"] == 30 and result["cpu_threads"] == 2
    assert not result["caption_accuracy_verified"]
    assert all(trial["smoke_text_accepted"] for trial in result["trials"])
    assert "scene accuracy" in result["smoke_gate"]
    assert all(result[field] is False for field in ("camera_capture", "bot_send", "enrollment", "actuation"))
    assert provider.calls == 2 and provider.closed == [True] and result["owned_runtime_stopped"]


def test_generated_caption_trial_rejects_degenerate_text_separately_from_accuracy(tmp_path):
    class Degenerate(FixtureProvider):
        async def caption(self, jpeg):
            await FixtureProvider.caption(self, jpeg)
            return " 1"
    provider = Degenerate()
    result = asyncio.run(benchmark(tmp_path, provider=provider))
    assert [trial["mode"] for trial in result["trials"]] == ["cold", "warm"]
    assert all(trial["completed"] and trial["caption_unverified"] == " 1" for trial in result["trials"])
    assert all(trial["smoke_text_accepted"] is False for trial in result["trials"])
    assert result["caption_accuracy_verified"] is False


@pytest.mark.parametrize("fail_on,timeout", [(1, False), (1, True), (2, False)])
def test_generated_caption_trial_stops_after_failure_and_reaps_provider(tmp_path, fail_on, timeout):
    provider = FixtureProvider(fail_on, timeout)
    result = asyncio.run(benchmark(tmp_path, provider=provider))
    assert provider.calls == fail_on and provider.closed == [True]
    assert len(result["trials"]) == fail_on and not result["trials"][-1]["completed"]
    assert ("deadline" in result["trials"][-1]["issue"]) == timeout
    assert result["owned_runtime_stopped"] and not result["bot_send"]
