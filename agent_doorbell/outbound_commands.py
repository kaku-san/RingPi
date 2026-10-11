"""Poll a separate command queue and post one short reply on the existing webhook.

The routine webhook URL is never polled. With the queue URL unset, this loop makes no outbound call.
"""
import asyncio
import base64
import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from .bots import PROVIDERS, BotError, validate_url
from .enrollment_bridge import MAX_PHOTO
from .guest_window import resolve_guest_window
from .named_guest import FACE_MODEL, MODELS, FaceError, enroll_named_photo, shrink_jpeg
from .store import now

QUEUE_ENV = "DOORBELL_GROK_COMMAND_QUEUE_URL"
QUEUE_KEY_ENV = "DOORBELL_GROK_COMMAND_QUEUE_KEY"
PAIRING_ENV = PROVIDERS["grok"]["secret_env"]
POLL_SECONDS = 25
TIMEOUT = 8
COMMAND_PHOTO_LIMIT = 5 * 1024 * 1024
BODY_LIMIT = 36_000_000
_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_REDIRECTS = {301, 302, 303, 307, 308}
_FIELDS = {"id", "expires_at", "command", "name", "label", "minutes", "window",
           "photo_sha256", "photo_base64", "photo_url", "photos"}
_REFUSALS = {
    "digest": "Refused: photo does not match photo_sha256.",
    "expired": "Refused: command expired.",
    "oversized": "Refused: photo is larger than 5 MB.",
    "invalid": "Refused: command was not understood.",
    "no_face": "Refused: no face found in photo.",
    "too_many": "Refused: at most 5 photos.",
    "photo_rejected": "Refused: photo is not a readable JPEG.",
    "unreadable": "Refused: photo is not a readable JPEG.",
    "refused": "Refused: command was not accepted.",
    "reply_failed": "Refused: reply was not delivered.",
}


def refusal_text(code=None, detail=None):
    if detail:
        text = str(detail).strip().rstrip(".")
        if text.lower().startswith("refused:"):
            text = text.split(":", 1)[1].strip().rstrip(".")
        if text:
            text = text[0].lower() + text[1:]
        else:
            text = "command was not accepted"
        return f"Refused: {text}."
    return _REFUSALS.get(code, "Refused: command was not accepted.")


class CommandRefused(Exception):
    def __init__(self, code, command_id=None, reply=None):
        self.code = code
        self.command_id = command_id
        self.reply = reply or refusal_text(code)
        super().__init__(code)


def _present(item, key):
    return key in item and item[key] is not None


def _loopback_host(host):
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False
    ip = getattr(ip, "ipv4_mapped", None) or ip
    return ip.is_loopback


def _target(url, allow_loopback, resolve):
    if not isinstance(url, str) or len(url) > 600 or any(ord(char) < 33 for char in url):
        raise CommandRefused("queue_target_refused")
    try:
        parts = urlsplit(url)
    except ValueError:
        raise CommandRefused("queue_target_refused") from None
    host = (parts.hostname or "").lower()
    loopback = allow_loopback and parts.scheme == "http" and _loopback_host(host)
    if not loopback:
        try:
            parts, port, address = validate_url(url, "grok", resolve=resolve)
        except BotError:
            raise CommandRefused("queue_target_refused") from None
        return parts, port, [address] if address else []
    if parts.username or parts.password or parts.query or parts.fragment or not host:
        raise CommandRefused("queue_target_refused")
    try:
        port = parts.port if parts.port is not None else 80
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise CommandRefused("queue_target_refused") from None
    if not resolve:
        return parts, port, []
    try:
        found = {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    except OSError:
        raise CommandRefused("queue_unreachable") from None
    if not found or not all(_loopback_host(item) for item in found):
        raise CommandRefused("queue_target_refused")
    return parts, port, sorted(found)


def http_exchange(url, headers, timeout, allow_loopback, limit):
    """One request. Redirects are returned to the caller and are not followed here."""
    parts, port, addresses = _target(url, allow_loopback, resolve=True)
    if not addresses:
        raise CommandRefused("queue_unreachable")
    for address in addresses:
        connection = http.client.HTTPSConnection(parts.hostname, port, timeout=timeout) if parts.scheme == "https" else (
            http.client.HTTPConnection(parts.hostname, port, timeout=timeout))
        try:
            connection.sock = socket.create_connection((address, port), timeout=timeout)
            if parts.scheme == "https":
                connection.sock = ssl.create_default_context().wrap_socket(
                    connection.sock, server_hostname=parts.hostname)
            connection.request("GET", parts.path or "/", headers=headers)
            response = connection.getresponse()
            if response.status in _REDIRECTS:
                location = response.getheader("Location")
                return response.status, {"Location": location or ""}, b""
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise CommandRefused("oversized")
            return response.status, {}, raw
        except CommandRefused:
            raise
        except (OSError, http.client.HTTPException):
            continue
        finally:
            connection.close()
    raise CommandRefused("queue_unreachable")


def fetch_public_bytes(url, exchange=http_exchange, timeout=TIMEOUT):
    """Fetch a photo outbound. A redirect is used only when the next URL stays public HTTPS."""
    current = url
    for hop in range(4):
        try:
            validate_url(current, "grok", resolve=False)
        except BotError:
            raise CommandRefused("photo_redirect_refused") from None
        status, headers, body = exchange(current, {"Accept": "image/jpeg"}, timeout, False, COMMAND_PHOTO_LIMIT)
        if status in _REDIRECTS:
            location = headers.get("Location") or ""
            if not location or hop == 3:
                raise CommandRefused("photo_redirect_refused")
            current = urljoin(current, location)
            continue
        if status != 200:
            raise CommandRefused("photo_fetch_failed")
        if len(body) > COMMAND_PHOTO_LIMIT:
            raise CommandRefused("oversized")
        if not body.startswith(b"\xff\xd8\xff"):
            raise CommandRefused("photo_rejected")
        return body
    raise CommandRefused("photo_redirect_refused")


def _name(value, refuse):
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        refuse("invalid")
    text = value.strip()
    if not text or len(text) > 60:
        refuse("invalid")
    return text


def _window(value, refuse):
    if not isinstance(value, dict) or set(value) - {"from", "until", "date"} or not {"from", "until"} <= set(value):
        refuse("invalid")
    if not all(isinstance(value[key], str) for key in value):
        refuse("invalid")
    return {"from": value["from"], "until": value["until"], **({"date": value["date"]} if "date" in value else {})}


def _decode_command_photo(value, refuse):
    if not isinstance(value, str):
        refuse("invalid")
    try:
        photo = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        refuse("invalid")
    if not photo or not photo.startswith(b"\xff\xd8\xff"):
        refuse("invalid")
    if len(photo) > COMMAND_PHOTO_LIMIT:
        refuse("oversized")
    return photo


def parse_command(item, moment):
    if not isinstance(item, dict):
        raise CommandRefused("invalid")
    command_id = item.get("id") if isinstance(item.get("id"), str) and _ID.fullmatch(item.get("id")) else None

    def refuse(code):
        raise CommandRefused(code, command_id)

    if set(item) - _FIELDS or not command_id:
        refuse("invalid")
    try:
        expires = datetime.fromisoformat(str(item.get("expires_at", "")).replace("Z", "+00:00"))
    except ValueError:
        refuse("invalid")
    if expires.tzinfo is None:
        refuse("invalid")
    if expires <= moment:
        refuse("expired")
    command = item.get("command")
    if command not in {"add-guest", "issue-pin", "revoke"}:
        refuse("invalid")
    parsed = {"id": command_id, "command": command}
    if command == "add-guest":
        if any(_present(item, key) for key in ("label", "minutes")):
            refuse("invalid")
        parsed["name"] = _name(item.get("name"), refuse)
        has_bytes = _present(item, "photo_base64")
        has_list = _present(item, "photos")
        has_url = _present(item, "photo_url")
        if sum((has_bytes, has_list, has_url)) != 1:
            refuse("invalid")
        if has_bytes:
            parsed["photos"] = [_decode_command_photo(item["photo_base64"], refuse)]
        elif has_list:
            photos = item["photos"]
            if not isinstance(photos, list) or not photos:
                refuse("invalid")
            if len(photos) > 5:
                refuse("too_many")
            parsed["photos"] = [_decode_command_photo(entry, refuse) for entry in photos]
        else:
            if not isinstance(item["photo_url"], str) or len(item["photo_url"]) > 600:
                refuse("invalid")
            parsed["photo_url"] = item["photo_url"]
        if _present(item, "photo_sha256"):
            if not isinstance(item["photo_sha256"], str) or not _SHA.fullmatch(item["photo_sha256"]):
                refuse("invalid")
            parsed["photo_sha256"] = item["photo_sha256"]
        if _present(item, "window"):
            parsed["window"] = _window(item["window"], refuse)
        return parsed
    if command == "issue-pin":
        if any(_present(item, key) for key in ("name", "window", "photo_base64", "photos", "photo_url", "photo_sha256")):
            refuse("invalid")
        minutes = 60 if not _present(item, "minutes") else item["minutes"]
        if type(minutes) is not int or not 1 <= minutes <= 24 * 60:
            refuse("invalid")
        parsed["minutes"] = minutes
        parsed["label"] = "Guest" if not _present(item, "label") else _name(item["label"], refuse)
        return parsed
    if _present(item, "label") == _present(item, "name"):
        refuse("invalid")
    if any(_present(item, key) for key in ("minutes", "window", "photo_base64", "photos", "photo_url", "photo_sha256")):
        refuse("invalid")
    if _present(item, "label"):
        parsed["label"] = _name(item["label"], refuse)
    else:
        parsed["name"] = _name(item["name"], refuse)
    return parsed


def first_command(body):
    if len(body) > BODY_LIMIT:
        raise CommandRefused("oversized")
    try:
        data = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CommandRefused("invalid") from None
    if not isinstance(data, dict) or set(data) != {"commands"} or not isinstance(data["commands"], list):
        raise CommandRefused("invalid")
    if not data["commands"]:
        return None
    return data["commands"][0]


class OutboundCommands:
    def __init__(self, store, access, vision, bots, clock=None, reply=None, exchange=http_exchange):
        self.store, self.access, self.vision, self.bots = store, access, vision, bots
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.reply = reply
        self.exchange = exchange
        self.task = None
        with store.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS outbound_command_ids (
                id TEXT PRIMARY KEY, seen_at TEXT NOT NULL)""")

    def queue_url(self):
        return os.environ.get(QUEUE_ENV, "").strip()

    def _secret(self, name):
        key = os.environ.get(name, "")
        if not isinstance(key, str) or not key or any(char.isspace() for char in key):
            return ""
        return key

    def pairing_key(self):
        return self._secret(PAIRING_ENV)

    def queue_key(self):
        return self._secret(QUEUE_KEY_ENV) or self.pairing_key()

    def routine_endpoint(self):
        connection = self.bots.connection("grok")
        if not connection:
            return ""
        return (connection["config"].get("endpoint") or "").rstrip("/")

    def can_reply(self):
        if not self.pairing_key():
            return False
        if self.reply is not None:
            return True
        endpoint = self.routine_endpoint()
        return bool(endpoint) and endpoint != self.queue_url().rstrip("/")

    def claim(self, command_id):
        with self.store.connection() as db:
            if db.execute("SELECT 1 FROM outbound_command_ids WHERE id=?", (command_id,)).fetchone():
                return False
            db.execute("INSERT INTO outbound_command_ids VALUES (?,?)", (command_id, now()))
        return True

    def fetch_queue(self, url, key):
        status, _headers, body = self.exchange(
            url, {"Authorization": "Bearer " + key, "Accept": "application/json"}, TIMEOUT, True, BODY_LIMIT)
        if status in _REDIRECTS:
            raise CommandRefused("queue_redirect_refused")
        if status != 200:
            raise CommandRefused("queue_rejected")
        return body

    def fetch_photo(self, url):
        return fetch_public_bytes(url, self.exchange, TIMEOUT)

    def send_reply(self, text):
        if self.reply is not None:
            self.reply(text)
            return
        self.bots.post_reply(text)

    def safe_reply(self, text):
        try:
            self.send_reply(text)
        except Exception:
            return

    def _fit_photo(self, photo, command_id):
        if len(photo) > COMMAND_PHOTO_LIMIT:
            raise CommandRefused("oversized", command_id)
        if not photo.startswith(b"\xff\xd8\xff"):
            raise CommandRefused("photo_rejected", command_id)
        if len(photo) <= MAX_PHOTO:
            return photo
        try:
            return shrink_jpeg(photo, MAX_PHOTO)
        except FaceError:
            raise CommandRefused("unreadable", command_id) from None

    def _received_photos(self, parsed):
        if "photos" in parsed:
            photos = list(parsed["photos"])
        else:
            photos = [self.fetch_photo(parsed["photo_url"])]
        digest = parsed.get("photo_sha256")
        if digest and hashlib.sha256(photos[0]).hexdigest() != digest:
            raise CommandRefused("digest", parsed["id"])
        return [self._fit_photo(photo, parsed["id"]) for photo in photos]

    def _usable_faces(self, photos, command_id):
        usable = []
        no_face = 0
        other = None
        for photo in photos:
            try:
                jpeg, vector, _timings = self.vision.enroll(photo)
            except FaceError as error:
                message = str(error).lower()
                if "no clear face" in message or "no face" in message:
                    no_face += 1
                else:
                    other = error
                continue
            if not isinstance(vector, list) or not 2 <= len(vector) <= 2048:
                other = ValueError("the photo did not produce a usable local face reference")
                continue
            usable.append((jpeg, vector))
        if usable:
            return usable, no_face
        if no_face and other is None:
            raise CommandRefused("no_face", command_id)
        if isinstance(other, FaceError):
            raise CommandRefused("face", command_id, refusal_text(detail=str(other)))
        if other is not None:
            raise CommandRefused("face", command_id, refusal_text(detail=str(other)))
        raise CommandRefused("no_face", command_id)

    def execute(self, parsed):
        from .access_simulation import AccessError
        try:
            if parsed["command"] == "add-guest":
                photos = self._received_photos(parsed)
                usable, no_face = self._usable_faces(photos, parsed["id"])
                window = None
                if "window" in parsed:
                    window = resolve_guest_window(
                        self.access.clock(), self.access.guest_timezone, parsed["window"]["from"],
                        parsed["window"]["until"], parsed["window"].get("date"))
                receipt = enroll_named_photo(
                    self.store, self.vision, self.access, parsed["name"], usable[0][0], True, window,
                    prepared=usable[0])
                for jpeg, vector in usable[1:]:
                    try:
                        self.store.add_face_sample(receipt["profile_id"], jpeg, vector, MODELS[FACE_MODEL][1])
                    except ValueError:
                        break
                if receipt.get("identity_proven") or receipt.get("real_actuation") or not receipt.get("pass_simulation_only"):
                    raise CommandRefused("refused", parsed["id"])
                text = "Guest added. Some photos had no face." if no_face else "Guest added."
                return text, None
            if parsed["command"] == "issue-pin":
                receipt = self.access.issue_pin(parsed["minutes"], parsed["label"])
                pin_id = receipt["id"]
                try:
                    text = f"PIN {receipt['pin']}, good for {parsed['minutes']} minutes."
                except Exception:
                    try:
                        self.access.revoke_pin(pin_id)
                    except Exception:
                        pass
                    raise CommandRefused("refused", parsed["id"]) from None

                def cleanup(identifier=pin_id):
                    try:
                        self.access.revoke_pin(identifier)
                    except Exception:
                        return
                return text, cleanup
            if "label" in parsed:
                self.access.revoke_pin_by_label(parsed["label"])
            else:
                self.access.revoke_named_guest(parsed["name"])
            return "Revoked.", None
        except CommandRefused:
            raise
        except FaceError as error:
            message = str(error)
            code = "no_face" if "no clear face" in message.lower() or "no face" in message.lower() else "face"
            reply = refusal_text("no_face") if code == "no_face" else refusal_text(detail=message)
            raise CommandRefused(code, parsed["id"], reply) from None
        except AccessError as error:
            raise CommandRefused("refused", parsed["id"], refusal_text(detail=error.code.replace("_", " "))) from None
        except (ValueError, OSError) as error:
            raise CommandRefused("refused", parsed["id"], refusal_text(detail=str(error))) from None

    def _refuse_once(self, error):
        if error.command_id and self.claim(error.command_id):
            self.safe_reply(error.reply)

    def poll_once(self):
        url = self.queue_url()
        if not url or not self.pairing_key():
            return {"idle": True, "executed": False}
        if url.rstrip("/") == self.routine_endpoint():
            return {"idle": True, "executed": False, "reason": "routine_url"}
        if not self.can_reply():
            return {"idle": True, "executed": False, "reason": "reply_destination_missing"}
        try:
            body = self.fetch_queue(url, self.queue_key())
            item = first_command(body)
        except CommandRefused as error:
            return {"idle": False, "executed": False, "reason": error.code}
        if item is None:
            return {"idle": False, "executed": False, "empty": True}
        try:
            parsed = parse_command(item, self.clock())
        except CommandRefused as error:
            self._refuse_once(error)
            return {"idle": False, "executed": False, "reason": error.code}
        if not self.claim(parsed["id"]):
            return {"idle": False, "executed": False, "reason": "replay"}
        try:
            text, cleanup = self.execute(parsed)
        except CommandRefused as error:
            self.safe_reply(error.reply)
            return {"idle": False, "executed": False, "reason": error.code}
        except Exception:
            self.safe_reply(refusal_text(detail="command could not be completed"))
            return {"idle": False, "executed": False, "reason": "refused"}
        try:
            self.send_reply(text)
        except Exception:
            if cleanup:
                cleanup()
            self.safe_reply(refusal_text("reply_failed"))
            return {"idle": False, "executed": False, "reason": "reply_failed"}
        return {"idle": False, "executed": True, "reply_text": text, "command": parsed["command"]}

    async def worker(self):
        while True:
            try:
                await asyncio.to_thread(self.poll_once)
            except Exception:
                pass
            await asyncio.sleep(POLL_SECONDS)

    def start(self):
        self.task = asyncio.create_task(self.worker())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
