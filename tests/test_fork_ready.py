"""Fork checks: button pin, documented payloads, and the leak scan."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from agent_doorbell.bots import POLICY, command_reply_payload, safe_event
from agent_doorbell.demo_alerts import (
    MOTION_LABEL, MOTION_POLICY, MOTION_UNCERTAINTY, SUMMARY_LABEL, SUMMARY_POLICY,
    SUMMARY_UNCERTAINTY, motion_observation_payload,
)

ROOT = Path(__file__).resolve().parents[1]


def blocks():
    text = (ROOT / "examples/grok-bot-routine.md").read_text()
    found = {}
    for title, body in re.findall(r"(?m)^## ([^\n]+)\n+```json\n(.*?)```", text, re.S):
        found[title] = json.loads(body)
    return found, text


def test_routine_example_matches_webhook_and_enrollment_receipt():
    found, text = blocks()
    assert set(found) == {"visit", "motion", "summary", "reply_text", "enrollment receipt"}
    sample = {
        "id": "a", "occurred_at": "t", "kind": "ring", "source": "physical",
        "capture_status": "ready", "frames": 1, "person_status": "probable_match",
        "face_status": "probable_match", "person_name": "Ada", "face_similarity": 0.70,
        "simulated": False,
    }
    expected = safe_event(sample, True)
    expected["caption"] = "A blue square and a red circle."
    visit = found["visit"]
    assert visit["stage"] == "visit" and visit["policy"] == POLICY and visit["images"] is False
    assert visit["history"] == []
    assert set(visit["event"]) == set(expected)
    for key, value in expected.items():
        if key in {"id", "occurred_at"}:
            assert visit["event"][key]
            continue
        assert visit["event"][key] == value
    assert visit["event"]["observations"] == "Probably Ada (0.70)"
    assert "unknown" in text and "match_name" in text and "match_score" in text
    motion = motion_observation_payload(
        {"id": "a", "occurred_at": "t", "capture_status": "ready", "frames": 1,
         "person_status": "detected", "face_status": "low_quality", "simulated": False},
        {"present_seconds": 10, "threshold_seconds": 10, "operator_check": False},
    )
    assert set(found["motion"]) == set(motion)
    assert set(found["motion"]["event"]) == set(motion["event"])
    assert set(found["motion"]["observation"]) == set(motion["observation"])
    assert found["motion"]["observation"]["label"] == MOTION_LABEL
    assert found["motion"]["observation"]["uncertainty"] == MOTION_UNCERTAINTY
    assert found["motion"]["policy"] == MOTION_POLICY
    summary = found["summary"]
    assert set(summary) == {"schema_version", "trust", "history", "policy", "images", "stage", "summary"}
    assert summary["stage"] == "summary" and summary["policy"] == SUMMARY_POLICY
    assert summary["summary"]["label"] == SUMMARY_LABEL
    assert summary["summary"]["uncertainty"] == SUMMARY_UNCERTAINTY
    assert set(summary["summary"]["counts"]) == {"rings", "motion_events", "known_guests", "unknown"}
    reply = command_reply_payload("Guest added.")
    assert set(found["reply_text"]) == set(reply)
    assert found["reply_text"]["reply_text"] == "Guest added."
    assert found["reply_text"]["stage"] == "synthetic_test" and found["reply_text"]["images"] is False
    assert found["reply_text"]["policy"] == POLICY and found["reply_text"]["history"] == []
    assert set(found["reply_text"]["event"]) == set(reply["event"])
    assert "match_name" not in found["reply_text"]["event"]
    receipt = found["enrollment receipt"]
    assert set(receipt) == {
        "enrolled", "profile_id", "name", "face_samples", "window",
        "identity_proven", "real_actuation", "hardware_confirmed", "pass_simulation_only",
    }
    blob = json.dumps(receipt)
    assert "photo" not in blob and "consent" not in blob
    assert "consent" in text and "expiry" in text and "name" in text
    for stage in ("visit", "motion", "summary"):
        assert f'stage "{stage}"' in text
    assert "reply_text" in text
    assert 'DOORBELL_GROK_COMMAND_QUEUE_URL' in text and 'DOORBELL_GROK_COMMAND_QUEUE_KEY' in text
    for stage in ("ring", "analysis", "caption"):
        assert f'stage "{stage}"' not in text
        assert f'"stage": "{stage}"' not in text


def test_button_unit_requires_configured_pin():
    env = os.environ.copy()
    env.pop("DOORBELL_BUTTON_BCM", None)
    missing = subprocess.run(
        [sys.executable, str(ROOT / "scripts/gpio_button.py")], env=env, capture_output=True, text=True,
    )
    assert missing.returncode != 0 and "DOORBELL_BUTTON_BCM" in missing.stderr
    env["DOORBELL_BUTTON_BCM"] = "17"
    reserved = subprocess.run(
        [sys.executable, str(ROOT / "scripts/gpio_button.py")], env=env, capture_output=True, text=True,
    )
    assert reserved.returncode != 0 and "reserved" in reserved.stderr
    unit = (ROOT / "deploy/agent-doorbell-button.service").read_text()
    assert "scripts/gpio_button.py" in unit and "DOORBELL_BUTTON_BCM" in unit
    assert "--pin" not in unit and not re.search(r"DOORBELL_BUTTON_BCM=\d", unit)


def test_leak_check_accepts_this_tree_and_rejects_samples():
    subprocess.run([sys.executable, str(ROOT / "scripts/leak-check.py"), "--self-test"], check=True, cwd=ROOT)
    subprocess.run([sys.executable, str(ROOT / "scripts/leak-check.py")], check=True, cwd=ROOT)


def test_ci_runs_backend_browser_and_leak_scan():
    workflow = (ROOT / ".github/workflows/test.yml").read_text()
    assert "scripts/test.sh backend" in workflow
    assert "scripts/test.sh browser" in workflow
    assert "scripts/leak-check.py" in workflow
