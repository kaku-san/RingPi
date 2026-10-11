import asyncio
import json
from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo

from PIL import Image

from agent_doorbell.app import Doorbell
from agent_doorbell.bots import BotInput
from agent_doorbell.config import Config
from agent_doorbell.demo_alerts import (
    PII_KEYS, PresenceTracker, contains_pii_key, quiet_hours_active, summary_counts,
)


def jpeg(color="black"):
    output = BytesIO()
    Image.new("RGB", (320, 180), color).save(output, "JPEG", quality=80)
    return output.getvalue()


def clock(hour, minute=0):
    return datetime(2026, 10, 8, hour, minute, tzinfo=ZoneInfo("Asia/Kolkata"))


def settings(**changes):
    base = {"quiet_hours_enabled": True, "quiet_hours_start": "22:00", "quiet_hours_end": "07:00",
            "timezone": "Asia/Kolkata"}
    base.update(changes)
    return base


def covering(now):
    start = now.hour
    end = (start + 2) % 24
    return settings(quiet_hours_start=f"{start:02d}:00", quiet_hours_end=f"{end:02d}:00")


def outside(now):
    start = (now.hour + 3) % 24
    end = (now.hour + 4) % 24
    return settings(quiet_hours_start=f"{start:02d}:00", quiet_hours_end=f"{end:02d}:00")


def enable_bot(bell, monkeypatch):
    monkeypatch.setenv("DOORBELL_GROK_ROUTINE_KEY", "fake-grok-test-key")
    body = BotInput(provider="grok", endpoint="https://bot.example/routine", destination="Fixture Bot")
    bell.bots.save(body)
    with bell.store.connection() as db:
        db.execute("UPDATE bot_connections SET verified=1,confirmed=1 WHERE provider='grok'")
    bell.bots.save(body.model_copy(update={"enabled": True}))


def payloads(bell):
    return [json.loads(bell.bots.job(row["id"])["payload"]) for row in bell.bots.deliveries()]


def test_quiet_window_uses_the_local_clock_and_crosses_midnight():
    assert quiet_hours_active(settings(), clock(23, 30))
    assert quiet_hours_active(settings(), clock(6, 59))
    assert not quiet_hours_active(settings(), clock(7, 0))
    assert not quiet_hours_active(settings(), clock(12, 0))
    assert not quiet_hours_active(settings(), clock(21, 59))
    assert not quiet_hours_active(settings(quiet_hours_enabled=False), clock(23, 30))


def test_presence_threshold_and_cooldown():
    tracker = PresenceTracker()
    assert not tracker.consider(True, 0, 10, 300)
    assert not tracker.consider(True, 9.9, 10, 300)
    assert tracker.consider(True, 10, 10, 300)
    assert not tracker.consider(True, 11, 10, 300)
    assert not tracker.consider(True, 309.9, 10, 300)
    assert tracker.consider(True, 310, 10, 300)
    assert not tracker.consider(False, 400, 10, 300)
    assert not tracker.consider(True, 400, 10, 300)
    assert not tracker.consider(True, 410, 10, 300)  # Ten new seconds, still inside the five-minute cooldown.
    assert tracker.consider(True, 610, 10, 300)


def test_person_alert_saves_a_snapshot_and_waits_out_the_cooldown(tmp_path, monkeypatch):
    async def scenario():
        bell = Doorbell(Config(data_dir=tmp_path, camera="synthetic", detector="none", background=False))
        enable_bot(bell, monkeypatch)
        bell.store.save_settings({"person_alerts_enabled": True, "person_alert_seconds": 10,
                                  "person_alert_cooldown_seconds": 300, "quiet_hours_enabled": False})
        analysis = {"status": "unknown", "person_status": "detected",
                    "objects": [{"label": "person", "confidence": 0.8}], "timings_ms": {}}
        assert await bell.note_person_presence(jpeg(), now=0, detected=True, analysis=analysis) is None
        assert await bell.note_person_presence(jpeg(), now=9.9, detected=True, analysis=analysis) is None
        event = await bell.note_person_presence(jpeg(), now=10, detected=True, analysis=analysis)
        assert event["kind"] == "motion" and event["source"] == "person_present" and event["frames"] == 1
        assert (bell.store.media / event["id"] / "0.jpg").is_file()
        assert await bell.note_person_presence(jpeg(), now=20, detected=True, analysis=analysis) is None
        assert len(bell.store.events(kind="motion")) == 1
        again = await bell.note_person_presence(jpeg(), now=310, detected=True, analysis=analysis)
        assert again and again["id"] != event["id"]
        sent = [item for item in payloads(bell) if item["stage"] == "motion"]
        assert len(sent) == 2
        assert {item["stage"] for item in sent} == {"motion"}
        assert sent[0]["observation"]["uncertainty"] == "A person may have been present."
        assert sent[0]["images"] is False and sent[0]["history"] == []
        assert contains_pii_key(sent[0]) is False
        await bell.close()
    asyncio.run(scenario())


def test_zone_motion_does_not_send_a_motion_webhook(tmp_path, monkeypatch):
    async def scenario():
        bell = Doorbell(Config(data_dir=tmp_path, camera="synthetic", detector="none", background=False, burst_frames=1))
        enable_bot(bell, monkeypatch)
        bell.store.save_settings({"motion_enabled": True, "motion_threshold": 0.05, "cooldown_seconds": 20,
                                  "person_alerts_enabled": False})
        assert await bell.process_motion(jpeg("black")) is None
        assert await bell.process_motion(jpeg("white")) is None
        event = await bell.process_motion(jpeg("black"))
        assert event["kind"] == "motion" and event["source"] == "motion_zone"
        await asyncio.gather(*list(bell.tasks))
        assert payloads(bell) == []
        await bell.close()
    asyncio.run(scenario())


def test_quiet_hours_suppress_webhooks_except_a_probable_known_face(tmp_path, monkeypatch):
    async def scenario():
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        bell = Doorbell(Config(data_dir=tmp_path, camera="synthetic", detector="none", background=False, burst_frames=1))
        enable_bot(bell, monkeypatch)
        bell.store.save_settings(covering(now))
        event, _duplicate = await bell.ring("physical")
        await asyncio.gather(*list(bell.tasks))
        assert bell.store.event(event["id"])["kind"] == "ring"
        assert payloads(bell) == []
        motion = await bell.record_person_alert(jpeg(), {"status": "unknown", "person_status": "detected", "objects": []}, 10)
        assert motion["source"] == "person_present"
        assert payloads(bell) == []
        matched = bell.store.update_event(motion["id"], person_status="probable_match", face_status="probable_match")
        bell.bots.enqueue_event(matched, stage="motion", motion_meta={"present_seconds": 10, "threshold_seconds": 10})
        held = bell.store.add_event("ring", "physical")
        assert bell.bots.enqueue("grok", held, stage="caption", caption_text="Blue square and red circle.") is None
        matched_caption = bell.store.update_event(held["id"], person_status="probable_match", face_status="probable_match")
        assert bell.bots.enqueue("grok", matched_caption, stage="caption", caption_text="Blue square and red circle.")
        profile = bell.store.add_profile("Fixture", "")
        bell.store.add_face_sample(profile["id"], jpeg(), [0.2, 0.3], "fixture-model")
        bell.frame_analysis.analyze = lambda frame: {
            "status": "probable_match", "profile_id": profile["id"], "similarity": 0.9, "margin": 0.4,
            "objects": [{"label": "person", "confidence": 0.8}], "person_status": "detected", "timings_ms": {}}
        bell.last_rings.clear()
        ring, duplicate = await bell.ring("physical")
        assert duplicate is False
        await asyncio.gather(*list(bell.tasks))
        body = payloads(bell)
        ring_stages = {item["stage"] for item in body if item.get("event", {}).get("id") == ring["id"]}
        assert ring_stages == {"visit"}
        visit = next(item for item in body if item.get("event", {}).get("id") == ring["id"])
        assert visit["event"]["match_name"] == "Fixture"
        assert visit["event"]["observations"] == "Probably Fixture (0.90)"
        motion_stages = [item for item in body if item["stage"] == "motion"]
        assert len(motion_stages) == 1 and motion_stages[0]["event"]["person_status"] == "probable_match"
        bell.store.save_settings(outside(datetime.now(ZoneInfo("Asia/Kolkata"))))
        plain = bell.store.add_event("ring", "physical")
        assert bell.bots.enqueue(bell.bots.status()["bots"][0]["provider"], plain, stage="ring")
        await bell.close()
    asyncio.run(scenario())


def test_summary_counts_have_no_pii_and_stages_stay_distinct(tmp_path, monkeypatch):
    async def scenario():
        bell = Doorbell(Config(data_dir=tmp_path, camera="synthetic", detector="none", background=False, burst_frames=1))
        enable_bot(bell, monkeypatch)
        known = bell.store.add_event("ring", "physical")
        bell.store.update_event(known["id"], person_status="probable_match", face_status="probable_match")
        unknown = bell.store.add_event("test", "browser")
        bell.store.update_event(unknown["id"], person_status="detected", face_status="unknown")
        bell.store.add_event("motion", "motion_zone")
        bell.store.add_event("ring", "physical")
        result = await bell.send_daily_summary(force=True)
        assert result["stage"] == "summary"
        assert result["counts"] == {"rings": 3, "motion_events": 1, "known_guests": 1, "unknown": 1}
        summary = next(item for item in payloads(bell) if item["stage"] == "summary")
        assert summary["images"] is False and summary["history"] == []
        assert set(summary["summary"]["counts"]) == {"rings", "motion_events", "known_guests", "unknown"}
        assert contains_pii_key(summary) is False
        assert "Fixture" not in json.dumps(summary)
        again = await bell.send_daily_summary(force=True)
        assert len([item for item in payloads(bell) if item["stage"] == "summary"]) == 1
        assert again["deliveries"] == result["deliveries"]
        fresh = bell.store.add_event("ring", "physical")
        bell.store.update_event(fresh["id"], caption_status="ready")
        bell.bots.enqueue_event(fresh, stage="visit", caption_text="Blue square and red circle.")
        motion = bell.store.add_event("motion", "person_present")
        bell.store.update_event(motion["id"], person_status="detected", face_status="unknown", frames=1,
                                capture_status="complete")
        bell.bots.enqueue_event(motion, stage="motion", motion_meta={"present_seconds": 10, "threshold_seconds": 10})
        stages = {}
        for item in payloads(bell):
            stages[item["stage"]] = item
        assert set(stages) >= {"visit", "motion", "summary"}
        assert "ring" not in stages and "analysis" not in stages and "caption" not in stages
        assert stages["motion"]["stage"] == "motion"
        assert stages["summary"]["stage"] == "summary"
        assert stages["visit"]["event"]["caption"] == "Blue square and red circle."
        keys = [row["event_id"] for row in bell.bots.deliveries()]
        assert len(keys) == len(set(keys))
        assert any(key.endswith(":visit") for key in keys)
        assert not any(key.endswith(":analysis") or key.endswith(":caption") for key in keys)
        assert any(key.endswith(":motion") for key in keys)
        assert any(key.endswith(":summary") for key in keys)
        assert all(key not in PII_KEYS for key in keys)
        await bell.close()
    asyncio.run(scenario())


def test_summary_count_helper_does_not_copy_event_fields():
    counts = summary_counts([
        {"kind": "ring", "person_status": "probable_match", "person_name": "Should Stay Local"},
        {"kind": "motion", "person_status": "detected", "face_status": "unknown"},
    ])
    assert counts == {"rings": 1, "motion_events": 1, "known_guests": 1, "unknown": 1}
    assert "person_name" not in counts
