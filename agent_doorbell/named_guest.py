"""One consented photo and a name, enrolled for local recognition and one simulated visit."""
import base64
import hashlib
import json
import re
from datetime import datetime, timedelta
import uuid

from .enrollment_bridge import MAX_PHOTO
from .guest_window import resolve_guest_window
from .local_vision import FACE_MODEL, MODELS, FaceError, shrink_jpeg


def parse_named_payload(raw):
    if len(raw) > 2_000_000:
        raise ValueError('Named photo exceeds the bridge bound')
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError('Named photo payload was not JSON') from None
    base = {'schema_version', 'name', 'sha256', 'photo_base64'}
    if not isinstance(payload, dict) or not base <= set(payload) <= base | {'window_from', 'window_until', 'window_date', 'consent'}:
        raise ValueError('Named photo payload has unexpected fields')
    extra = set(payload) - base - {'consent'}
    if extra and not {'window_from', 'window_until'} <= extra:
        raise ValueError('Named photo payload has unexpected fields')
    if payload.get('schema_version') != 1:
        raise ValueError('Named photo payload was not accepted')
    name = payload.get('name')
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 60:
        raise ValueError('Use a visitor name of 1 to 60 characters')
    if not isinstance(payload.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', payload['sha256']):
        raise ValueError('Named photo digest is invalid')
    try:
        photo = base64.b64decode(payload['photo_base64'], validate=True)
    except (ValueError, TypeError):
        raise ValueError('Named photo encoding is invalid') from None
    if not photo or len(photo) > MAX_PHOTO or hashlib.sha256(photo).hexdigest() != payload['sha256']:
        raise ValueError('Named photo digest or size is invalid')
    window = None
    if extra:
        window = {'from': payload['window_from'], 'until': payload['window_until']}
        if 'window_date' in extra:
            if not isinstance(payload['window_date'], str):
                raise ValueError('Use a YYYY-MM-DD date')
            window['date'] = payload['window_date']
        resolve_guest_window(datetime.now().astimezone(), 'UTC', window['from'], window['until'], window.get('date'))
    return name.strip(), photo, window


def pass_bounds(now, window):
    """Prefer the visit window when it fits the existing simulated-pass limits."""
    if window is None:
        return now, now + timedelta(hours=23)
    start, end, _label = window
    if (now - timedelta(minutes=15) <= start <= now + timedelta(days=7)
            and start < end <= start + timedelta(hours=24) and end > now):
        return start, end
    if start <= now < end and end <= now + timedelta(hours=24):
        return now, end
    return now, now + timedelta(hours=1)


def enroll_named_photo(store, vision, access, name, photo, consent=None, window=None, prepared=None):
    """Store one local reference and one simulated pass. This does not actuate a lock."""
    del consent
    name = name.strip()
    if not name or len(name) > 60:
        raise ValueError('Use a visitor name of 1 to 60 characters')
    if prepared is None:
        jpeg, vector, _timings = vision.enroll(photo)
    else:
        jpeg, vector = prepared
    if not isinstance(vector, list) or not 2 <= len(vector) <= 2048:
        raise ValueError('The photo did not produce a usable local face reference')
    profile = store.add_profile(name, '')
    visitor = None
    try:
        store.add_face_sample(profile['id'], jpeg, vector, MODELS[FACE_MODEL][1])
        now = access.clock()
        start, end = pass_bounds(now, window)
        visitor = access.pass_request(uuid.uuid4().hex, name, 1, start, end)
        approved = access.decide_pass(visitor['id'], visitor['digest'], True, 'approve')
        access.register_named_guest(profile['id'], approved['visitor_id'], approved['id'], name, window)
    except Exception:
        store.delete_profile(profile['id'])
        if visitor is not None:
            try:
                access.decide_pass(visitor['id'], visitor['digest'], True, 'revoke')
            except Exception:
                pass
        raise
    return {'enrolled': True, 'profile_id': profile['id'], 'name': name, 'face_samples': 1,
            'window': None if window is None else window[2],
            'identity_proven': False, 'real_actuation': False, 'hardware_confirmed': False,
            'pass_simulation_only': True}
