import asyncio
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import time
from threading import Event
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
import pytest

from agent_doorbell.app import Doorbell, create_app
from agent_doorbell.bots import BotInput
from agent_doorbell.camera import Camera, CameraUnavailable
from agent_doorbell.config import Config
from agent_doorbell.store import Store
from agent_doorbell.vision import MotionDetector, PersonDetector


def frame(color="black", rectangle=None):
    image = Image.new("RGB", (320, 180), color)
    if rectangle:
        ImageDraw.Draw(image).rectangle(rectangle, fill="white")
    output = BytesIO()
    image.save(output, "JPEG", quality=95)
    return output.getvalue()


@pytest.fixture
def config(tmp_path):
    return Config(data_dir=tmp_path, camera="synthetic", detector="none", background=False,
                  burst_frames=3, burst_interval=0.01)


@pytest.fixture
def client(config):
    with TestClient(create_app(config)) as client:
        yield client


def wait_capture(client, event_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        event = client.get(f"/api/events/{event_id}").json()
        if event["capture_status"] != "pending":
            return event
        time.sleep(0.015)
    raise AssertionError("Capture did not complete")


def test_ring_delivered_before_camera_finishes(config):
    class SlowCamera(Camera):
        async def capture(self, max_age=0):
            await asyncio.sleep(0.6)
            return frame()
    camera = SlowCamera("synthetic")
    with TestClient(create_app(config, camera=camera)) as client:
        start = time.monotonic()
        response = client.post("/api/ring", json={"source": "browser"})
        elapsed = time.monotonic() - start
        assert response.status_code == 201
        assert elapsed < 0.3, f"Ring waited {elapsed:.3f}s for a 0.6s camera"
        event = response.json()["event"]
        assert event["capture_status"] == "pending"
        assert client.get("/api/events").json()["events"][0]["id"] == event["id"]


def test_snapshot_burst_and_persistence(client, config):
    event = client.post("/api/ring", json={"source": "browser"}).json()["event"]
    completed = wait_capture(client, event["id"])
    assert completed["frames"] == 3
    assert completed["capture_status"] == "complete"
    assert completed["simulated"] is True
    assert completed["person_status"] == "unavailable"
    assert datetime.fromisoformat(completed["occurred_at"]).tzinfo is not None
    for index, path in enumerate(completed["media"]):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        with Image.open(BytesIO(response.content)) as image:
            assert image.size == (960, 540)
    assert Store(config.data_dir).event(event["id"])["frames"] == 3


@pytest.mark.parametrize("objects,person,face,frames,expected,status", [
    (["person"], "detected", "low_quality", 1, "A person was detected. unknown", "ready"),
    (["person"], "detected", "no_face", 1, "A person was detected. unknown", "ready"),
    (["backpack","person"], "detected", "unknown", 1, "A person and a backpack were detected. unknown", "ready"),
    (["cat"], "not_detected", "no_face", 1, "A cat was detected.", "ready"),
    ([], "not_detected", "no_face", 1, None, "no_confident_observations"),
    ([], "unavailable", "unavailable", 0, None, "unavailable"),
])
def test_local_description_persists_without_a_bot(config, objects, person, face, frames, expected, status):
    store=Store(config.data_dir)
    event=store.add_event("test","browser",simulated=True)
    store.update_event(event["id"],capture_status="complete" if frames else "offline",frames=frames,
                       person_status=person,face_status=face,local_analysis=json.dumps({"objects":[{"label":label} for label in objects]}))
    with store.connection() as db:
        stored=db.execute("SELECT description,description_status FROM events WHERE id=?",(event["id"],)).fetchone()
        assert tuple(stored)==(expected,status)
    with TestClient(create_app(config)) as client:
        returned=client.get('/api/events/'+event['id']).json()
        assert returned['description']==expected and returned['description_status']==status
        assert returned['description_source']=='local_detections'
        assert not any(bot['enabled'] for bot in client.get('/api/bots').json()['bots'])
        assert 'dog' not in (returned['description'] or '')
        assert client.get('/api/events').json()['events'][0]['description']==expected


def test_local_description_arrives_after_first_frame_before_burst_finishes(config):
    release=Event()
    class PausedBurstCamera(Camera):
        calls=0
        async def capture(self,max_age=0):
            self.calls+=1
            if self.calls>1:await asyncio.to_thread(release.wait,2)
            return frame()
    with TestClient(create_app(config,camera=PausedBurstCamera('synthetic'))) as client:
        bell=client.app.state.doorbell
        bell.local_vision.objects=True
        bell.local_vision.analyze=lambda *args:{'status':'low_quality','objects':[{'label':'person','confidence':.8}],'timings_ms':{}}
        event=client.post('/api/ring',json={'source':'browser'}).json()['event']
        try:
            for _ in range(50):
                returned=client.get('/api/events/'+event['id']).json()
                if returned['description']:break
                time.sleep(.01)
            assert returned['description'].startswith('A person was detected.')
            assert returned['frames']==1 and returned['capture_status']=='pending'
        finally:release.set()


def test_legacy_events_receive_local_descriptions_on_migration(config):
    store=Store(config.data_dir)
    event=store.add_event('test','browser',simulated=True)
    store.update_event(event['id'],frames=1,capture_status='complete',person_status='detected',face_status='no_face')
    with store.connection() as db:
        db.execute('ALTER TABLE events DROP COLUMN description')
        db.execute('ALTER TABLE events DROP COLUMN description_status')
    returned=Store(config.data_dir).event(event['id'])
    assert returned['description']=="A person was detected. unknown"
    assert returned['description_status']=='ready'


def test_ring_duplicate_suppression(client):
    first = client.post("/api/ring", json={"source": "browser"}).json()
    second = client.post("/api/ring", json={"source": "browser"}).json()
    assert second["duplicate"] is True
    assert first["event"]["id"] == second["event"]["id"]
    physical = client.post("/api/ring", json={"source": "physical"}).json()
    assert physical["duplicate"] is False
    assert physical["event"]["kind"] == "ring"
    assert len(client.get("/api/events").json()["events"]) == 2


def enable_fixture_bot(doorbell, provider="grok"):
    body = BotInput(provider=provider, endpoint="https://bot.example/routine", destination="Fixture Doorbell",
                    delivery="dashboard" if provider == "hermes" else "owning_bot_chat",
                    session_id="fixture-session" if provider == "hermes" else "")
    doorbell.bots.save(body)
    with doorbell.store.connection() as db:
        db.execute("UPDATE bot_connections SET verified=1,confirmed=1 WHERE provider=?", (provider,))
    doorbell.bots.save(body.model_copy(update={"enabled": True}))
    return doorbell.bots.connection(provider)["revision"]


def test_paired_browser_ring_sends_two_stages_only_to_confirmed_bot(config, monkeypatch):
    monkeypatch.setenv("DOORBELL_GROK_ROUTINE_KEY", "fake-grok-test-key")
    monkeypatch.setenv("DOORBELL_HERMES_API_KEY", "fake-hermes-test-key")
    app = create_app(config)
    with TestClient(app) as client:
        doorbell = app.state.doorbell
        revision = enable_fixture_bot(doorbell)
        enable_fixture_bot(doorbell, "hermes")
        local = client.post("/api/ring", json={"source": "browser"}).json()["event"]
        wait_capture(client, local["id"])
        body = {"provider": "grok", "revision": revision, "confirmed": True}
        event = client.post("/api/ring/test-with-bot", json=body).json()["event"]
        completed = wait_capture(client, event["id"])
        assert completed["kind"] == "test" and completed["source"] == "browser_bot"
        assert client.post("/api/ring/test-with-bot", json=body).json()["duplicate"]
        jobs = [doorbell.bots.job(job["id"]) for job in doorbell.bots.deliveries()
                if str(job["event_id"]).startswith(event["id"])]
        payloads = [json.loads(job["payload"]) for job in jobs]
        assert len(jobs) == 1 and {job["provider"] for job in jobs} == {"grok"}
        assert {p["stage"] for p in payloads} == {"visit"}
        assert {p["event"]["id"] for p in payloads} == {event["id"]}
        assert all(p["event"]["kind"] == "test" and p["images"] is False for p in payloads)
        assert all(p["event"]["observations"] == "unknown" for p in payloads)
        assert all("enrolled_name_untrusted" not in p["event"] and len(p["history"]) <= 5 for p in payloads)
        assert all(not job["synthetic"] for job in jobs)  # This test uses the local camera, unlike setup acknowledgement.


def test_paired_browser_ring_requires_enabled_current_setup_before_capture(config, monkeypatch):
    monkeypatch.setenv("DOORBELL_GROK_ROUTINE_KEY", "fake-grok-test-key")
    app = create_app(config)
    with TestClient(app) as client:
        body = {"provider": "grok", "revision": "a" * 32, "confirmed": True}
        assert client.post("/api/ring/test-with-bot", json=body).status_code == 409
        revision = enable_fixture_bot(app.state.doorbell)
        assert client.post("/api/ring/test-with-bot", json=body).status_code == 409
        body["revision"] = revision
        body["confirmed"] = False
        assert client.post("/api/ring/test-with-bot", json=body).status_code == 422
        body["confirmed"] = True
        monkeypatch.setenv("DOORBELL_GROK_ROUTINE_KEY", "changed-fake-key")
        assert client.post("/api/ring/test-with-bot", json=body).status_code == 409
        assert not app.state.doorbell.store.events() and not app.state.doorbell.bots.deliveries()


def test_paired_ring_analysis_does_not_follow_changed_destination(config, monkeypatch):
    monkeypatch.setenv("DOORBELL_GROK_ROUTINE_KEY", "fake-grok-test-key")
    started, released = Event(), Event()
    class HeldCamera(Camera):
        async def capture(self, max_age=0):
            started.set()
            await asyncio.to_thread(released.wait, 2)
            return frame()
    app = create_app(config, camera=HeldCamera("synthetic"))
    with TestClient(app) as client:
        doorbell = app.state.doorbell
        revision = enable_fixture_bot(doorbell)
        event = client.post("/api/ring/test-with-bot", json={"provider": "grok", "revision": revision, "confirmed": True}).json()["event"]
        assert started.wait(1)
        doorbell.bots.save(BotInput(provider="grok", endpoint="https://bot.example/replacement", destination="Changed Bot"))
        enable_fixture_bot(doorbell)  # Re-enable even the original fields under a new revision.
        released.set()
        wait_capture(client, event["id"])
        jobs = doorbell.bots.deliveries()
        assert jobs == []


def test_paired_ring_offline_camera_still_queues_same_event_analysis(config, monkeypatch):
    monkeypatch.setenv("DOORBELL_GROK_ROUTINE_KEY", "fake-grok-test-key")
    config.camera = "none"
    app = create_app(config)
    with TestClient(app) as client:
        doorbell = app.state.doorbell
        revision = enable_fixture_bot(doorbell)
        event = client.post("/api/ring/test-with-bot", json={"provider": "grok", "revision": revision, "confirmed": True}).json()["event"]
        completed = wait_capture(client, event["id"])
        assert completed["capture_status"] == "offline" and completed["simulated"] is False
        payloads = [json.loads(doorbell.bots.job(job["id"])["payload"]) for job in doorbell.bots.deliveries()]
        visit = next(p for p in payloads if p["stage"] == "visit")
        assert len(payloads) == 1 and visit["event"]["id"] == event["id"]
        assert visit["event"]["capture_status"] == "offline" and visit["event"]["frames"] == 0
        assert visit["event"]["observations"] == "unknown"


def test_offline_camera_does_not_block_ring(config):
    config.camera = "none"
    with TestClient(create_app(config)) as client:
        event = client.post("/api/ring", json={"source": "physical"}).json()["event"]
        completed = wait_capture(client, event["id"])
        assert completed["capture_status"] == "offline"
        assert completed["frames"] == 0
        assert completed["camera_error"]
        assert client.post("/api/camera/snapshot").status_code == 503
        assert client.get("/api/camera/latest.jpg").status_code == 404


def test_active_share_session_never_opens_camera():
    class GuardResponse:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, *args): return b'{"state":"PLACE_PALM"}'
    camera = Camera("rpicam")
    camera.binary = "/not/executed/rpicam-still"
    with patch("urllib.request.urlopen", return_value=GuardResponse()), patch("asyncio.create_subprocess_exec") as run:
        with pytest.raises(CameraUnavailable, match="reserving the camera"):
            asyncio.run(camera.capture())
        run.assert_not_called()
    assert camera.status["state"] == "busy"


def test_unreachable_share_guard_fails_closed():
    camera = Camera("rpicam")
    camera.binary = "/not/executed/rpicam-still"
    with patch("urllib.request.urlopen", side_effect=TimeoutError), patch("asyncio.create_subprocess_exec") as run:
        with pytest.raises(CameraUnavailable):
            asyncio.run(camera.capture())
        run.assert_not_called()


def test_motion_zone_ignores_changes_outside_zone():
    detector = MotionDetector()
    zone = {"x": 0.5, "y": 0, "width": 0.5, "height": 1}
    assert detector.score(frame(), zone) == 0
    assert detector.score(frame(rectangle=(0, 0, 120, 179)), zone) < 0.005
    assert detector.score(frame(rectangle=(170, 0, 319, 179)), zone) > 0.7


def test_motion_needs_persistence_and_honors_cooldown(config):
    async def scenario():
        config.burst_frames = 1
        bell = Doorbell(config)
        bell.store.save_settings({"motion_enabled": True, "motion_threshold": 0.05, "cooldown_seconds": 20})
        assert await bell.process_motion(frame("black")) is None
        assert await bell.process_motion(frame("white")) is None
        event = await bell.process_motion(frame("black"))
        assert event["kind"] == "motion"
        assert await bell.process_motion(frame("white")) is None
        await asyncio.gather(*bell.tasks)
        assert len(bell.store.events()) == 1
        bell.store.save_settings({"motion_enabled": False})
        bell.last_event_time = -1e9
        assert await bell.process_motion(frame("black")) is None
    asyncio.run(scenario())


def test_zone_validation_and_settings_persistence(client, config):
    settings = client.get("/api/status").json()["settings"]
    settings["zone"] = {"x": 0.8, "y": 0, "width": 0.5, "height": 1}
    assert client.put("/api/settings", json=settings).status_code == 422
    settings["zone"] = {"x": 0.25, "y": 0, "width": 0.5, "height": 1}
    settings["retention_days"] = 14
    assert client.put("/api/settings", json=settings).status_code == 200
    assert Store(config.data_dir).settings()["retention_days"] == 14


def test_profiles_corrections_and_profile_removal(client):
    profile = client.post("/api/profiles", json={"name": "Avery", "note": "Neighbour"}).json()
    event = client.post("/api/ring", json={"source": "browser"}).json()["event"]
    wait_capture(client, event["id"])
    path = f'/api/events/{event["id"]}'
    response = client.patch(path, json={"person_id": profile["id"], "acknowledged": True, "note": "Parcel"})
    assert response.json()["person_status"] == "manual"
    assert response.json()["person_name"] == "Avery"
    assert client.patch(path, json={"person_id": "missing"}).status_code == 404
    assert client.patch(path, json={"person_status": "manual"}).status_code == 422
    assert client.patch(path, json={"person_status": "uncertain"}).json()["person_id"] is None
    client.patch(path, json={"person_id": profile["id"]})
    assert client.patch(path, json={"person_id": None}).json()["person_status"] == "unknown"
    client.patch(path, json={"person_id": profile["id"]})
    assert client.delete(f'/api/profiles/{profile["id"]}').status_code == 204
    assert client.get(path).json()["person_status"] == "unknown"


@pytest.mark.parametrize("correction", ["manual", "unknown", "uncertain"])
def test_detector_does_not_overwrite_a_human_correction(config, correction):
    started, release = Event(), Event()

    class SlowDetector(PersonDetector):
        def detect(self, jpeg):
            started.set()
            assert release.wait(3), "Test did not release detector"
            return "detected"

    with TestClient(create_app(config, detector=SlowDetector("none"))) as client:
        event = client.post("/api/ring", json={"source": "browser"}).json()["event"]
        try:
            assert started.wait(2)
            profile = client.post("/api/profiles", json={"name": "Avery"}).json()
            change = {"person_id": profile["id"]} if correction == "manual" else {"person_status": correction}
            assert client.patch(f'/api/events/{event["id"]}', json=change).status_code == 200
        finally:
            release.set()
        assert wait_capture(client, event["id"])["person_status"] == correction


def test_summary_counts_beyond_history_page_and_uses_local_midnight(client):
    store = client.app.state.doorbell.store
    for index in range(105):
        store.add_event("ring" if index < 103 else "motion", "fixture")
    old = store.add_event("ring", "fixture")
    with store.connection() as db:
        db.execute("UPDATE events SET occurred_at='2000-01-01T00:00:00.000+00:00', acknowledged=1 WHERE id=?", (old["id"],))
    assert len(client.get("/api/events?limit=100").json()["events"]) == 100
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert client.get("/api/events/summary", params={"since": since}).json() == {"total": 105, "rings": 103, "motion": 2, "unread": 105}
    # 05:30 in this offset is midnight UTC, so the old event is included exactly at the boundary.
    assert client.get("/api/events/summary", params={"since": "2000-01-01T05:30:00+05:30"}).json()["total"] == 106
    assert client.get("/api/events/summary", params={"since": "2000-01-01T05:30:00.001+05:30"}).json()["total"] == 105
    assert client.get("/api/events/summary", params={"since": "2000-01-01T00:00:00"}).status_code == 422


def test_proposals_require_explicit_single_approval(client):
    proposal = client.post("/api/agent/proposals", json={"text": "Please leave the parcel by the door."}).json()
    assert proposal["status"] == "pending"
    context = client.get("/api/agent/context").json()
    assert context["status"]["agent"]["connected"] is False
    assert "real door control is unconfigured; access workflows are local simulations" in context["constraints"]
    assert context["status"]["access_simulation"]["real_actuation_enabled"] is False
    endpoint = f'/api/agent/proposals/{proposal["id"]}/decision'
    assert client.post(endpoint, json={"decision": "approved"}).json()["status"] == "approved"
    assert client.post(endpoint, json={"decision": "approved"}).status_code == 409


def test_delete_and_retention_remove_local_media(client, config):
    event = client.post("/api/ring", json={"source": "browser"}).json()["event"]
    wait_capture(client, event["id"])
    assert (config.data_dir / "events" / event["id"]).exists()
    store = client.app.state.doorbell.store
    with store.connection() as db:
        db.execute("UPDATE events SET occurred_at=? WHERE id=?", ((datetime.now(timezone.utc) - timedelta(days=8)).isoformat(), event["id"]))
    store.cleanup(7)
    assert store.event(event["id"]) is None
    assert not (config.data_dir / "events" / event["id"]).exists()
    assert client.get(f'/api/events/{event["id"]}/frames/0').status_code == 404


def test_invalid_requests_and_cross_origin_writes(client):
    assert client.post("/api/ring", json={"source": "cloud"}).status_code == 422
    assert client.post("/api/ring", json={"source": "browser"}, headers={"Origin": "https://unrelated.example"}).status_code == 403
    assert client.get("/api/events/unknown/frames/0").status_code == 404
    assert client.post("/api/profiles", json={"name": "   "}).status_code == 422
    assert client.get("/").headers["content-security-policy"].startswith("default-src 'self'")
    assert client.get("/api/events").headers["cache-control"] == "no-store"


def test_restarting_marks_unfinished_captures(config):
    first = Store(config.data_dir)
    event = first.add_event("ring", "physical")
    assert Store(config.data_dir).event(event["id"])["capture_status"] == "interrupted"


def test_optional_hog_runs_without_loading_a_large_model():
    pytest.importorskip("cv2")
    detector = PersonDetector("hog")
    assert detector.status["available"] is True
    assert detector.detect(frame("black")) == "not_detected"
    assert detector.status["identity_matching"] is False


def test_event_bus_publishes_ring_before_capture(config):
    async def scenario():
        bell = Doorbell(config)
        queue = asyncio.Queue()
        bell.bus.clients.add(queue)
        event, duplicate = await bell.ring("physical")
        name, delivered = queue.get_nowait()
        assert name == "ring" and delivered["id"] == event["id"] and not duplicate
        assert delivered["frames"] == 0
        await bell.close()
    asyncio.run(scenario())
