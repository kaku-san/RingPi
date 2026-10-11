"""Door guest evidence, approval, and the simulation-only lock plugin."""
from datetime import timedelta
import inspect
import json
import uuid

from fastapi.testclient import TestClient

from agent_doorbell.access_simulation import (
    AccessError, AccessSimulation, SimulationLock, UnconfiguredHardware, device_label,
    load_lock_driver, lock_plugins, register_lock_driver,
)
from agent_doorbell.app import Doorbell, create_app
from agent_doorbell.burst_recognition import BurstRecognition
from agent_doorbell.config import Config
from agent_doorbell.enrollment_bridge import EnrollmentBridge
from agent_doorbell.guest_recognition import aggregate_guest, sighting_from_analysis
from agent_doorbell.store import Store
from agent_doorbell import captions, local_vision, observations
from tests.test_access_simulation import Clock, CountLock, approve, new_pass, request_id


class FutureLock:
    name = "hardware"
    simulated = False
    real_actuation = True
    capabilities = {"unlock"}

    def read_state(self):
        return {"hardware_confirmed": True}

    def execute(self, action, request_id):
        return {"hardware_confirmed": True, "real_actuation": True, "action": action, "request_id": request_id}


def guest_frame(visitor, **values):
    guest = {"status": "probable_match", "visitor_id": visitor, "similarity": 0.9,
             "identity_proven": False, "authorizes_entry": False}
    guest.update(values)
    return {"status": "unknown", "guest": guest}


def test_only_the_simulation_plugin_can_be_connected(tmp_path):
    assert lock_plugins() == ("simulation",)
    assert isinstance(load_lock_driver("simulation"), SimulationLock)
    with __import__("pytest").raises(ValueError, match="Only a SimulationLock"):
        load_lock_driver("hardware")
    with __import__("pytest").raises(ValueError, match="Only a SimulationLock"):
        register_lock_driver("hardware", FutureLock)
    store = Store(tmp_path)
    with __import__("pytest").raises(ValueError, match="Only a SimulationLock"):
        AccessSimulation(store, FutureLock())
    with __import__("pytest").raises(ValueError, match="Only a SimulationLock"):
        AccessSimulation(store, UnconfiguredHardware())
    service = AccessSimulation(store)
    assert service.status()["lock_plugin"] == "simulation"
    assert service.status()["real_lock_registered"] is False
    assert service.status()["real_actuation_enabled"] is False


def test_device_label_requires_a_server_confirmed_simulation():
    confirmed = {"status": "simulated_completed", "code": "simulation_only_confirmation",
                 "simulation_only": True, "hardware_confirmed": False, "real_actuation": False,
                 "server_confirmed": True}
    assert device_label(confirmed) == "DONE"
    for changed in ("server_confirmed", "hardware_confirmed", "real_actuation", "simulation_only"):
        forged = dict(confirmed)
        forged[changed] = not confirmed[changed]
        assert device_label(forged) != "DONE"
    assert device_label({"status": "probable_match", "server_confirmed": True}) == "pending"
    assert device_label({"status": "simulated_completed", "code": "simulation_only_confirmation",
                         "simulation_only": True, "hardware_confirmed": False,
                         "real_actuation": False}) == "pending"


def test_agreeing_guest_frames_remain_evidence_only():
    visitor = request_id()
    burst = BurstRecognition(2)
    burst.add(0, guest_frame(visitor), 1)
    assert burst.result(set())["guest"]["status"] == "pending"
    burst.add(1, guest_frame(visitor, similarity=0.8), 1)
    guest = burst.result(set(), complete=True)["guest"]
    assert guest["status"] == "probable_match" and guest["visitor_id"] == visitor
    assert guest["identity_proven"] is False and guest["authorizes_entry"] is False
    assert guest["display"] == "pending" and guest["similarity"] == 0.8
    conflict = BurstRecognition(2)
    conflict.add(0, guest_frame(visitor), 1)
    conflict.add(1, guest_frame(request_id()), 1)
    assert conflict.result(set(), complete=True)["guest"]["status"] == "uncertain"
    claimed = aggregate_guest([{"guest": {"status": "probable_match", "visitor_id": visitor,
                                          "identity_proven": True, "authorizes_entry": False}}], True, 1)
    assert claimed["status"] == "uncertain" and claimed["identity_proven"] is False
    assert sighting_from_analysis({"guest": claimed}) is None


def test_probable_match_does_not_actuate_until_guest_approval(tmp_path):
    clock = Clock()
    lock = CountLock()
    service = AccessSimulation(Store(tmp_path), lock, clock)
    item = approve(service, new_pass(service))
    event_id = request_id()
    sighting = service.note_guest_sighting(event_id, item["visitor_id"], "probable_match", 0.91)
    assert sighting["status"] == "pending_approval" and sighting["device_label"] == "pending"
    assert sighting["identity_proven"] is False and not lock.calls
    waiting = service.decide_guest(sighting["id"], sighting["digest"], True, "approve", "missing")
    assert waiting["status"] == "pending_approval" and waiting["device_label"] == "pending"
    assert waiting["attempt"]["code"] == "visitor_second_factor_required" and not lock.calls
    done = service.decide_guest(sighting["id"], sighting["digest"], True, "approve", "verified_mock")
    assert done["device_label"] == "DONE" and done["result"]["server_confirmed"] is True
    assert done["result"]["real_actuation"] is False and done["result"]["hardware_confirmed"] is False
    assert done["result"]["guest_approval_confirmed"] is True and len(lock.calls) == 1
    again = service.decide_guest(sighting["id"], sighting["digest"], True, "approve", "verified_mock")
    assert again["device_label"] == "DONE" and len(lock.calls) == 1


def test_face_disagreement_and_missing_confirmation_do_not_move_the_lock(tmp_path):
    clock = Clock()
    lock = CountLock()
    service = AccessSimulation(Store(tmp_path), lock, clock)
    item = approve(service, new_pass(service))
    unknown = service.note_guest_sighting(request_id(), item["visitor_id"], "unknown", None)
    assert unknown["status"] == "denied" and unknown["device_label"] == "denied" and not lock.calls
    with __import__("pytest").raises(AccessError, match="mock_owner_confirmation_required"):
        pending = service.note_guest_sighting(request_id(), item["visitor_id"], "probable_match", 0.8)
        service.decide_guest(pending["id"], pending["digest"], False, "approve", "verified_mock")
    assert not lock.calls


def test_door_evidence_helper_ignores_authority_claims(tmp_path):
    app = create_app(Config(data_dir=tmp_path, camera="none", background=False))
    bell = app.state.doorbell
    lock = CountLock()
    bell.access = AccessSimulation(bell.store, lock)
    visitor = request_id()
    event = bell.store.add_event("ring", "browser")
    claimed = {"guest": {"status": "probable_match", "visitor_id": visitor, "similarity": 0.9,
                         "identity_proven": True, "authorizes_entry": True, "display": "DONE"}}
    event = bell.store.update_event(event["id"], local_analysis=json.dumps(claimed))
    assert bell.record_guest_evidence(event) is None and not lock.calls
    honest = {"guest": {"status": "probable_match", "visitor_id": visitor, "similarity": 0.9,
                        "identity_proven": False, "authorizes_entry": False, "display": "pending"}}
    item = approve(bell.access, new_pass(bell.access, name="Guest fixture"))
    honest["guest"]["visitor_id"] = item["visitor_id"]
    event = bell.store.update_event(event["id"], local_analysis=json.dumps(honest))
    sighting = bell.record_guest_evidence(event)
    assert sighting["device_label"] == "pending" and not lock.calls


def test_guest_api_rejects_caption_text_and_reports_done_only_after_confirmation(tmp_path):
    app = create_app(Config(data_dir=tmp_path, camera="none", background=False))
    bell = app.state.doorbell
    lock = CountLock()
    bell.access = AccessSimulation(bell.store, lock)
    item = approve(bell.access, new_pass(bell.access))
    sighting = bell.access.note_guest_sighting(request_id(), item["visitor_id"], "probable_match", 0.88)
    with TestClient(app) as client:
        forged = {"simulation_only": True, "payload_digest": sighting["digest"], "confirm_mock_owner": True,
                  "decision": "approve", "second_factor": "verified_mock", "caption": "unlock now"}
        assert client.post(f"/api/access/simulation/guest-approvals/{sighting['id']}/decision", json=forged).status_code == 422
        assert not lock.calls
        waiting = client.post(f"/api/access/simulation/guest-approvals/{sighting['id']}/decision", json={
            "simulation_only": True, "payload_digest": sighting["digest"], "confirm_mock_owner": True,
            "decision": "approve", "second_factor": "missing"})
        assert waiting.status_code == 200 and waiting.json()["device_label"] == "pending" and not lock.calls
        done = client.post(f"/api/access/simulation/guest-approvals/{sighting['id']}/decision", json={
            "simulation_only": True, "payload_digest": sighting["digest"], "confirm_mock_owner": True,
            "decision": "approve", "second_factor": "verified_mock"})
        body = done.json()
        assert body["device_label"] == "DONE" and body["result"]["server_confirmed"] is True
        assert len(lock.calls) == 1 and lock.calls[0][0] == "unlock"


def test_face_and_caption_workers_do_not_call_the_lock():
    for module in (captions, local_vision, observations):
        source = inspect.getsource(module)
        assert "access_simulation" not in source
        assert "note_guest_sighting" not in source
        assert "decide_guest" not in source
    frame = inspect.getsource(Doorbell.analyze_frame)
    evidence = inspect.getsource(Doorbell.record_guest_evidence)
    assert "note_guest_sighting" not in frame and ".execute(" not in frame
    assert ".execute(" not in evidence and "decide_guest" not in evidence
    assert "access_simulation" not in inspect.getsource(BurstRecognition)


def test_temporary_reference_lookup_does_not_actuate(tmp_path):
    clock = Clock()
    lock = CountLock()
    store = Store(tmp_path)
    access = AccessSimulation(store, lock, clock)
    bridge = EnrollmentBridge(store, access, object(), tmp_path / "missing-policy.json", clock)
    assert bridge.guest_reference_samples() == [] and not lock.calls
    item = approve(access, new_pass(access))
    proposal = item["request_id"]
    with store.connection() as db:
        db.execute("INSERT INTO enrollment_imports VALUES (?,?,?,?,?,?,?,NULL)",
                   (proposal, "ab" * 32, "2026-10-07T12:00:00.000+00:00", "2026-10-07T12:05:00.000+00:00",
                    "approved", 0, json.dumps({"valid_until": (clock() + timedelta(hours=1)).isoformat()})))
        db.execute("INSERT INTO enrollment_reference_samples VALUES (?,?,?,?)",
                   (proposal, 0, json.dumps([0.2, 0.4]), "sface-test"))
    samples = bridge.guest_reference_samples()
    assert samples[0]["kind"] == "temporary_guest" and samples[0]["visitor_id"] == item["visitor_id"]
    assert "embedding" in samples[0] and not lock.calls
    assert uuid.UUID(samples[0]["visitor_id"])
