"""Outbound command queue: one local command, one short webhook reply."""
import asyncio
import base64
import hashlib
import ipaddress
import json
import os
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from threading import Thread

from PIL import Image
import pytest

from agent_doorbell.access_simulation import AccessSimulation
from agent_doorbell.bots import BotInput, BotService, command_reply_payload
from agent_doorbell.enrollment_bridge import MAX_PHOTO
from agent_doorbell.named_guest import FaceError
from agent_doorbell.outbound_commands import (
    BODY_LIMIT, COMMAND_PHOTO_LIMIT, PAIRING_ENV, POLL_SECONDS, QUEUE_ENV, QUEUE_KEY_ENV, TIMEOUT,
    CommandRefused, OutboundCommands, fetch_public_bytes, http_exchange,
)
from agent_doorbell.store import Store
from tests.test_access_simulation import Clock, CountLock

KEY = "fake-pairing-secret"
QUEUE = "http://localhost:9/commands"
ROUTINE = "https://bot.example/routine"
LATER = "2026-10-07T18:00:00+00:00"
EARLIER = "2026-10-07T11:00:00+00:00"


def jpeg_bytes():
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "blue").save(buffer, "JPEG")
    return buffer.getvalue()


class Vision:
    def enroll(self, raw):
        return raw, [0.2, 0.4, 0.6], {}


def command(command_name, **fields):
    body = {"id": fields.pop("id", "cmd-" + command_name), "expires_at": fields.pop("expires_at", LATER),
            "command": command_name, **fields}
    return json.dumps({"commands": [body]}).encode()


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv(PAIRING_ENV, KEY)
    monkeypatch.setenv(QUEUE_ENV, QUEUE)
    clock = Clock()
    store = Store(tmp_path)
    access = AccessSimulation(store, CountLock(), clock, guest_timezone="Asia/Kolkata")
    sent = []

    def transport(provider, endpoint, method, path, payload, headers):
        sent.append((provider, endpoint, method, path, payload, headers))
        return {}

    bots = BotService(store, lambda *_args: None, transport=transport)
    bots.save(BotInput(provider="grok", endpoint=ROUTINE, destination="Owning Bot"))
    calls = []

    def exchange(url, headers, timeout, allow_loopback, limit, bodies=None):
        calls.append((url, headers, timeout, allow_loopback, limit))
        body = exchange.bodies.pop(0)
        return 200, {}, body

    exchange.bodies = [b'{"commands": []}']
    poller = OutboundCommands(store, access, Vision(), bots, clock=clock, exchange=exchange)
    return poller, access, store, sent, calls, exchange, clock


def test_poller_idles_when_the_queue_url_is_unset(harness, monkeypatch):
    poller, _access, _store, sent, calls, _exchange, _clock = harness
    monkeypatch.delenv(QUEUE_ENV, raising=False)
    assert 20 <= POLL_SECONDS <= 30 and TIMEOUT == 8

    async def run():
        poller.exchange = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("polled"))
        poller.start()
        await asyncio.sleep(0.05)
        await poller.close()

    asyncio.run(run())
    assert poller.poll_once() == {"idle": True, "executed": False}
    assert sent == [] and calls == []


def test_queue_key_is_the_bearer_when_set(harness, monkeypatch):
    poller, _access, _store, sent, calls, _exchange, _clock = harness
    monkeypatch.setenv(QUEUE_KEY_ENV, "fake-queue-bearer")
    result = poller.poll_once()
    assert result["empty"] is True and result["executed"] is False
    assert calls[0][1]["Authorization"] == "Bearer fake-queue-bearer"
    assert sent == []


def test_routine_key_is_the_bearer_when_the_queue_key_is_unset(harness, monkeypatch):
    poller, _access, _store, sent, calls, _exchange, _clock = harness
    monkeypatch.delenv(QUEUE_KEY_ENV, raising=False)
    result = poller.poll_once()
    assert result["empty"] is True and result["executed"] is False
    assert calls[0][1]["Authorization"] == "Bearer " + KEY
    assert sent == []


def test_empty_queue_sends_the_pairing_bearer_and_does_no_work(harness):
    poller, access, store, sent, calls, _exchange, _clock = harness
    result = poller.poll_once()
    assert result["empty"] is True and result["executed"] is False
    url, headers, timeout, allow_loopback, limit = calls[0]
    assert url == QUEUE and headers["Authorization"] == "Bearer " + KEY
    assert timeout == TIMEOUT and allow_loopback is True and limit == BODY_LIMIT
    assert sent == [] and access.active_pins() == [] and store.profiles() == []


def test_the_routine_webhook_url_is_not_polled(harness, monkeypatch):
    poller, _access, _store, sent, calls, _exchange, _clock = harness
    monkeypatch.setenv(QUEUE_ENV, ROUTINE)
    result = poller.poll_once()
    assert result["reason"] == "routine_url" and result["executed"] is False
    assert calls == [] and sent == []


def test_add_guest_uses_a_generated_fixture_and_stays_a_simulated_probable_match(harness):
    poller, access, store, sent, _calls, exchange, _clock = harness
    photo = jpeg_bytes()
    digest = hashlib.sha256(photo).hexdigest()
    exchange.bodies = [command(
        "add-guest", id="guest-1", name="Guest Fixture", photo_base64=base64.b64encode(photo).decode(),
        photo_sha256=digest, window={"from": "17:00", "until": "19:00", "date": "2026-10-07"})]
    result = poller.poll_once()
    assert result["executed"] is True and result["reply_text"] == "Guest added."
    payload = sent[0][4]
    assert payload["reply_text"] == "Guest added." and payload["images"] is False
    assert payload["stage"] == "synthetic_test" and "embedding" not in json.dumps(payload)
    assert photo not in json.dumps(payload).encode() and digest not in json.dumps(payload)
    assert sent[0][1] == ROUTINE and sent[0][5]["Authorization"] == "Bearer " + KEY
    profile = store.profiles()[0]
    assert profile["name"] == "Guest Fixture" and access.snapshot()["named_guests"] == [
        {"name": "Guest Fixture", "window": "5:00-7:00 PM"}]
    opened = access.admit_named_guest("ab" * 16, profile["id"])
    assert opened["identity_proven"] is False and opened["real_actuation"] is False
    assert opened["hardware_confirmed"] is False and opened["server_confirmed"] is True
    assert store.events() == []


def test_photo_url_checks_the_digest_and_refuses_a_private_redirect(harness):
    poller, _access, store, sent, _calls, exchange, _clock = harness
    photo = jpeg_bytes()
    digest = hashlib.sha256(photo).hexdigest()
    poller.fetch_photo = lambda url: photo if url == "https://cdn.example/guest.jpg" else (_ for _ in ()).throw(
        AssertionError("unexpected photo url"))
    exchange.bodies = [command(
        "add-guest", id="guest-url", name="Guest Fixture", photo_url="https://cdn.example/guest.jpg",
        photo_sha256=digest)]
    assert poller.poll_once()["reply_text"] == "Guest added."
    exchange.bodies = [command(
        "add-guest", id="guest-bad-digest", name="Other Fixture", photo_url="https://cdn.example/guest.jpg",
        photo_sha256="ab" * 32)]
    refused = poller.poll_once()
    assert refused["executed"] is False and refused["reason"] == "digest"
    assert [profile["name"] for profile in store.profiles()] == ["Guest Fixture"]
    assert sent[-1][4]["reply_text"] == "Refused: photo does not match photo_sha256."

    calls = []

    def redirect(url, headers, timeout, allow_loopback, limit):
        calls.append((url, headers, allow_loopback))
        assert "Authorization" not in headers
        return 302, {"Location": "https://" + str(ipaddress.ip_address(0x7F000001)) + "/hidden.jpg"}, b""

    with pytest.raises(CommandRefused) as caught:
        fetch_public_bytes("https://cdn.example/guest.jpg", redirect)
    assert caught.value.code == "photo_redirect_refused" and len(calls) == 1
    with pytest.raises(CommandRefused):
        fetch_public_bytes("https://localhost/guest.jpg", redirect)
    assert len(calls) == 1


def test_issue_pin_reply_contains_the_pin_once_and_the_database_keeps_only_a_hash(harness, tmp_path):
    poller, access, _store, sent, _calls, exchange, _clock = harness
    exchange.bodies = [command("issue-pin", id="pin-1", label="Parcel", minutes=60)]
    result = poller.poll_once()
    reply = result["reply_text"]
    pin = re.fullmatch(r"PIN (\d{6}), good for 60 minutes\.", reply).group(1)
    assert reply.count(pin) == 1 and sent[0][4]["reply_text"] == reply
    assert "embedding" not in json.dumps(sent[0][4]) and sent[0][4]["images"] is False
    database = sqlite3.connect(tmp_path / "doorbell.sqlite3")
    database.row_factory = sqlite3.Row
    columns = [row[1] for row in database.execute("PRAGMA table_info(guest_pins)")]
    assert "pin" not in columns and {"salt", "pin_hash", "label", "single_use"} <= set(columns)
    stored = database.execute("SELECT label, status, salt, pin_hash FROM guest_pins").fetchone()
    assert stored["label"] == "Parcel" and stored["status"] == "active"
    assert pin not in stored["salt"] and pin not in stored["pin_hash"]
    for table in database.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        for row in database.execute(f'SELECT * FROM "{table[0]}"'):
            for key in row.keys():
                if key in {"salt", "pin_hash"}:
                    continue
                if isinstance(row[key], str):
                    assert pin not in row[key]
    assert database.execute("SELECT COUNT(*) FROM bot_deliveries").fetchone()[0] == 0
    assert access.active_pins() == [{"label": "Parcel", "expires_at": access.active_pins()[0]["expires_at"]}]
    assert "pin" not in access.active_pins()[0]


def test_revoke_drops_a_pin_label_and_a_named_guest(harness):
    poller, access, store, sent, _calls, exchange, _clock = harness
    exchange.bodies = [
        command("issue-pin", id="pin-2", label="Parcel", minutes=30),
        command("revoke", id="revoke-pin", label="Parcel"),
        command("add-guest", id="guest-2", name="Guest Fixture",
                photo_base64=base64.b64encode(jpeg_bytes()).decode()),
        command("revoke", id="revoke-guest", name="Guest Fixture"),
    ]
    issued = poller.poll_once()
    pin = re.search(r"\d{6}", issued["reply_text"]).group(0)
    assert poller.poll_once()["reply_text"] == "Revoked."
    assert access.active_pins() == [] and pin not in sent[-1][4]["reply_text"]
    assert poller.poll_once()["reply_text"] == "Guest added."
    assert poller.poll_once()["reply_text"] == "Revoked."
    assert store.profiles() == [] and access.snapshot()["named_guests"] == []
    assert access.snapshot()["passes"][0]["status"] in {"revoked", "expired", "dismissed"}


def test_expired_and_replayed_ids_are_refused(harness):
    poller, access, store, sent, _calls, exchange, _clock = harness
    exchange.bodies = [
        command("issue-pin", id="once", label="Parcel", minutes=15, expires_at=EARLIER),
        command("issue-pin", id="once", label="Parcel", minutes=15),
        command("issue-pin", id="fresh", label="Parcel", minutes=15),
        command("issue-pin", id="fresh", label="Parcel", minutes=15),
    ]
    expired = poller.poll_once()
    assert expired["reason"] == "expired" and expired["executed"] is False
    assert sent[0][4]["reply_text"] == "Refused: command expired." and access.active_pins() == []
    replayed_expired = poller.poll_once()
    assert replayed_expired["reason"] == "replay" and access.active_pins() == []
    issued = poller.poll_once()
    assert issued["executed"] is True and len(access.active_pins()) == 1
    before = len(sent)
    assert poller.poll_once()["reason"] == "replay"
    assert len(sent) == before and len(access.active_pins()) == 1
    assert store.events() == []


def test_oversized_body_and_photo_are_refused(harness):
    poller, _access, store, sent, _calls, exchange, _clock = harness
    exchange.bodies = [b"{" + b"x" * (BODY_LIMIT + 1)]
    assert poller.poll_once()["reason"] == "oversized" and sent == []
    photo = b"\xff\xd8\xff" + b"\x00" * (COMMAND_PHOTO_LIMIT - 2)
    exchange.bodies = [command(
        "add-guest", id="big-photo", name="Guest Fixture",
        photo_base64=base64.b64encode(photo).decode())]
    refused = poller.poll_once()
    assert refused["executed"] is False and refused["reason"] == "oversized"
    assert store.profiles() == [] and sent[0][4]["reply_text"] == "Refused: photo is larger than 5 MB."
    assert MAX_PHOTO == 1_500_000 and COMMAND_PHOTO_LIMIT >= 5_000_000


def _grant(store):
    with store.connection() as db:
        return db.execute(
            "SELECT window_daily, window_from, window_until, window_label FROM named_guest_grants").fetchone()


def test_dateless_window_is_daily_and_a_dated_window_is_not(harness):
    poller, access, store, _sent, _calls, exchange, clock = harness
    photo = base64.b64encode(jpeg_bytes()).decode()
    exchange.bodies = [command(
        "add-guest", id="daily-guest", name="Guest Fixture", photo_base64=photo,
        window={"from": "17:00", "until": "23:59"})]
    added = poller.poll_once()
    assert added["executed"] is True and added["reply_text"] == "Guest added."
    grant = _grant(store)
    assert grant["window_daily"] == 1 and grant["window_from"] == "17:00" and grant["window_until"] == "23:59"
    profile_id = store.profiles()[0]["id"]
    assert access.admit_named_guest("ab" * 16, profile_id)["device_label"] == "DONE"
    clock.advance(days=1)
    assert access.admit_named_guest("cd" * 16, profile_id)["device_label"] == "DONE"
    clock.value = clock.value.replace(hour=4)
    blocked = access.admit_named_guest("ef" * 16, profile_id)
    assert "outside allowed window" in blocked["text"] and blocked["identity_proven"] is False


def test_dated_window_stays_on_that_date(harness):
    poller, access, store, _sent, _calls, exchange, clock = harness
    photo = base64.b64encode(jpeg_bytes()).decode()
    exchange.bodies = [command(
        "add-guest", id="dated-guest", name="Dated Fixture", photo_base64=photo,
        window={"from": "17:00", "until": "19:00", "date": "2026-10-07"})]
    dated = poller.poll_once()
    assert dated["executed"] is True and dated["reply_text"] == "Guest added."
    grant = _grant(store)
    assert grant["window_daily"] == 0
    clock.advance(days=1)
    outside = access.admit_named_guest("12" * 16, store.profiles()[0]["id"])
    assert "outside allowed window" in outside["text"] and outside["device_label"] != "DONE"


def test_one_megabyte_photo_is_not_refused_for_size(harness):
    poller, _access, store, sent, _calls, exchange, _clock = harness
    photo = b"\xff\xd8\xff" + b"\x00" * (1_000_000 - 3)
    assert len(photo) == 1_000_000 and len(photo) < MAX_PHOTO
    exchange.bodies = [command(
        "add-guest", id="one-meg", name="Guest Fixture", photo_base64=base64.b64encode(photo).decode())]
    result = poller.poll_once()
    assert result["executed"] is True and result["reply_text"] == "Guest added."
    assert store.profiles() and "larger" not in sent[-1][4]["reply_text"].lower()


def test_photo_larger_than_enrollment_size_is_resized(harness, monkeypatch):
    poller, _access, store, _sent, _calls, exchange, _clock = harness
    monkeypatch.setattr("agent_doorbell.outbound_commands.MAX_PHOTO", 8_000)
    image = Image.frombytes("RGB", (320, 240), os.urandom(320 * 240 * 3))
    buffer = BytesIO()
    image.save(buffer, "JPEG", quality=95)
    photo = buffer.getvalue()
    assert MAX_PHOTO == 1_500_000 and len(photo) > 8_000
    exchange.bodies = [command(
        "add-guest", id="resize-photo", name="Guest Fixture",
        photo_base64=base64.b64encode(photo).decode())]
    result = poller.poll_once()
    assert result["executed"] is True and result["reply_text"] == "Guest added."
    assert store.profiles()[0]["face_samples"] == 1


def test_omitted_sha256_is_accepted(harness):
    poller, _access, store, _sent, _calls, exchange, _clock = harness
    exchange.bodies = [command(
        "add-guest", id="no-digest", name="Guest Fixture",
        photo_base64=base64.b64encode(jpeg_bytes()).decode())]
    result = poller.poll_once()
    assert result["executed"] is True and result["reply_text"] == "Guest added."
    assert store.profiles()[0]["face_samples"] == 1


def test_photo_list_of_two_enrolls_and_six_is_refused(harness):
    poller, _access, store, sent, _calls, exchange, _clock = harness
    encoded = base64.b64encode(jpeg_bytes()).decode()
    exchange.bodies = [command("add-guest", id="two-photos", name="Guest Fixture", photos=[encoded, encoded])]
    added = poller.poll_once()
    assert added["executed"] is True and added["reply_text"] == "Guest added."
    assert store.profiles()[0]["face_samples"] == 2
    exchange.bodies = [command(
        "add-guest", id="six-photos", name="Other Fixture", photos=[encoded] * 6)]
    refused = poller.poll_once()
    assert refused["executed"] is False and refused["reason"] == "too_many"
    assert sent[-1][4]["reply_text"] == "Refused: at most 5 photos."
    assert [profile["name"] for profile in store.profiles()] == ["Guest Fixture"]


def test_no_face_refusal_includes_the_reason(harness):
    poller, _access, store, sent, _calls, exchange, _clock = harness

    class NoFace:
        def enroll(self, _raw):
            raise FaceError("No clear face found. Choose a front-facing photo.")

    poller.vision = NoFace()
    exchange.bodies = [command(
        "add-guest", id="no-face", name="Guest Fixture",
        photo_base64=base64.b64encode(jpeg_bytes()).decode())]
    refused = poller.poll_once()
    reply = sent[0][4]["reply_text"]
    assert refused["executed"] is False and reply.startswith("Refused:") and "no face" in reply.lower()
    assert reply != "Command refused." and store.profiles() == []


def test_ring_post_does_not_grow_a_reply_field(harness):
    _poller, _access, store, _sent, _calls, _exchange, _clock = harness
    payload = command_reply_payload("Revoked.")
    assert payload["reply_text"] == "Revoked." and payload["images"] is False
    assert set(payload) == {
        "schema_version", "trust", "event", "history", "policy", "images", "stage", "reply_text"}
    with store.connection() as db:
        db.execute("UPDATE bot_connections SET verified=1, confirmed=1 WHERE provider='grok'")
    bots = BotService(store, lambda *_args: None)
    bots.save(BotInput(provider="grok", endpoint=ROUTINE, destination="Owning Bot", enabled=True))
    event = store.add_event("ring", "physical")
    posted = json.loads(bots.job(bots.enqueue("grok", event))["payload"])
    assert "reply_text" not in posted and posted["stage"] == "visit"


def test_local_queue_requires_the_bearer(tmp_path, monkeypatch):
    monkeypatch.setenv(PAIRING_ENV, KEY)
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get("Authorization"))
            if self.headers.get("Authorization") != "Bearer " + KEY:
                raw = b'{"detail":"unauthorized"}'
                self.send_response(401)
            else:
                raw = self.server.bodies.pop(0)
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("localhost", 0), Handler)
    server.bodies = [b'{"commands": []}', command("issue-pin", id="socket-pin", label="Parcel", minutes=60)]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv(QUEUE_ENV, "http://localhost:" + str(server.server_address[1]) + "/commands")
        clock = Clock()
        store = Store(tmp_path)
        access = AccessSimulation(store, CountLock(), clock, guest_timezone="Asia/Kolkata")
        sent = []
        bots = BotService(store, lambda *_args: None, transport=lambda *args: sent.append(args) or {})
        bots.save(BotInput(provider="grok", endpoint=ROUTINE, destination="Owning Bot"))
        poller = OutboundCommands(store, access, Vision(), bots, clock=clock, exchange=http_exchange)
        monkeypatch.setenv(PAIRING_ENV, "wrong-fake-key")
        assert poller.poll_once()["reason"] == "queue_rejected" and access.active_pins() == []
        monkeypatch.setenv(PAIRING_ENV, KEY)
        assert poller.poll_once()["empty"] is True
        issued = poller.poll_once()
        assert issued["executed"] is True and re.fullmatch(r"PIN \d{6}, good for 60 minutes\.", issued["reply_text"])
        assert seen == ["Bearer wrong-fake-key", "Bearer " + KEY, "Bearer " + KEY]
        assert sent[0][1] == ROUTINE and sent[0][4]["reply_text"] == issued["reply_text"]
    finally:
        server.shutdown()
        server.server_close()


def test_contract_and_new_files_have_no_lan_host_or_secret():
    root = Path(__file__).parents[1]
    paths = [
        root / "agent_doorbell/outbound_commands.py",
        root / "docs/OUTBOUND_COMMANDS.md",
        root / "docs/GROK_BOT_GUEST.md",
        root / "tests/test_outbound_commands.py",
        root / ".env.example",
    ]
    text = "\n".join(path.read_text() for path in paths)
    assert "DOORBELL_GROK_COMMAND_QUEUE_URL" in text
    assert "reply_text" in text and "add-guest" in text and "issue-pin" in text and "revoke" in text
    assert "QUEUE_HOST" in text and "Authorization: Bearer" in text
    for needle in ("." + "local", "kaku" + "ji", "192" + ".168."):
        assert needle not in text
    assert not re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
