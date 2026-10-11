"""One-time guest PIN and visit window. Simulation only; the PIN is never stored."""
import json
import re
import sqlite3
from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest
from fastapi.testclient import TestClient

from agent_doorbell.access_simulation import AccessError, AccessSimulation
from agent_doorbell.app import Doorbell, create_app
from agent_doorbell.bridge_client import named_photo_body, send_guest_window, send_issue_pin, send_revoke_pin
from agent_doorbell.config import Config
from agent_doorbell.guest_window import outside_window_text, resolve_guest_window
from agent_doorbell.named_guest import enroll_named_photo, parse_named_payload
from agent_doorbell.store import Store
from tests.test_access_simulation import Clock, CountLock


def jpeg_bytes():
    buffer = BytesIO()
    Image.new('RGB', (8, 8), 'blue').save(buffer, 'JPEG')
    return buffer.getvalue()


class Vision:
    def enroll(self, raw):
        return raw, [0.2, 0.4, 0.6], {}


def service(tmp_path):
    clock = Clock()
    lock = CountLock()
    access = AccessSimulation(Store(tmp_path), lock, clock, guest_timezone='Asia/Kolkata')
    return access, lock, clock


def other_pin(pin):
    return '000000' if pin != '000000' else '111111'


def test_pin_is_returned_once_and_the_dashboard_hides_it(tmp_path):
    access, lock, clock = service(tmp_path)
    receipt = access.issue_pin(label='Parcel')
    pin = receipt['pin']
    assert re.fullmatch(r'\d{6}', pin) and receipt['single_use'] is True
    assert receipt['real_actuation'] is False and receipt['server_confirmed'] is False
    listed = access.active_pins()
    assert listed == [{'label': 'Parcel', 'expires_at': receipt['expires_at']}]
    assert 'pin' not in listed[0] and pin not in json.dumps(listed)
    assert not lock.calls
    with pytest.raises(AccessError, match='invalid_pin_window'):
        access.issue_pin(24 * 60 + 1, 'Parcel')
    with pytest.raises(AccessError, match='invalid_pin_label'):
        access.issue_pin(15, pin)


def test_wrong_pin_then_sixth_attempt_locks_the_keypad(tmp_path):
    access, lock, clock = service(tmp_path)
    pin = access.issue_pin(30, 'Parcel')['pin']
    wrong = other_pin(pin)
    for _ in range(5):
        result = access.submit_pin(wrong)
        assert result['code'] == 'pin_not_accepted' and result['device_label'] != 'DONE'
    locked = access.submit_pin(wrong)
    assert locked['code'] == 'pin_locked_out' and locked['server_confirmed'] is False
    during = access.submit_pin(pin)
    assert during['code'] == 'pin_locked_out' and during['device_label'] != 'DONE'
    assert not lock.calls
    clock.advance(minutes=15)
    opened = access.submit_pin(pin)
    assert opened['device_label'] == 'DONE' and opened['server_confirmed'] is True
    assert opened['real_actuation'] is False and opened['hardware_confirmed'] is False
    assert len(lock.calls) == 1 and lock.calls[0][0] == 'unlock'


def test_expired_revoked_and_reused_pins_do_not_open(tmp_path):
    access, lock, clock = service(tmp_path)
    expired = access.issue_pin(30, 'Parcel')
    clock.advance(minutes=31)
    assert access.submit_pin(expired['pin'])['code'] == 'pin_expired' and not lock.calls
    clock.advance(minutes=-31)
    revoked = access.issue_pin(30, 'Parcel')
    access.revoke_pin(revoked['id'])
    assert access.submit_pin(revoked['pin'])['code'] == 'pin_revoked' and not lock.calls
    assert access.active_pins() == []
    live = access.issue_pin(30, 'Parcel')
    opened = access.submit_pin(live['pin'])
    assert opened['device_label'] == 'DONE' and opened['server_confirmed'] is True
    assert opened['real_actuation'] is False and opened['hardware_confirmed'] is False
    again = access.submit_pin(live['pin'])
    assert again['code'] == 'pin_already_used' and again['device_label'] != 'DONE'
    assert len(lock.calls) == 1


def test_plaintext_pin_is_not_stored(tmp_path):
    access, _lock, _clock = service(tmp_path)
    pin = access.issue_pin(60, 'Parcel')['pin']
    database = sqlite3.connect(tmp_path / 'doorbell.sqlite3')
    columns = [row[1] for row in database.execute('PRAGMA table_info(guest_pins)')]
    assert 'pin' not in columns and {'salt', 'pin_hash', 'label', 'expires_at', 'single_use'} <= set(columns)
    label, status, salt, pin_hash = database.execute('SELECT label, status, salt, pin_hash FROM guest_pins').fetchone()
    assert label == 'Parcel' and status == 'active' and pin not in label and pin not in salt and pin not in pin_hash
    skip = {'digest', 'id', 'pass_id', 'visitor_id', 'profile_id', 'request_id', 'event_id', 'target_id', 'salt', 'pin_hash'}

    def walk(value, key=None):
        if isinstance(value, dict):
            assert 'pin' not in value and 'pin_hash' not in value and 'salt' not in value
            for child_key, child in value.items():
                walk(child, child_key)
        elif isinstance(value, list):
            for child in value:
                walk(child, key)
        elif isinstance(value, str) and key not in skip:
            assert pin not in value

    walk(access.snapshot())


def test_window_blocks_a_probable_match_until_it_is_inside(tmp_path):
    access, lock, clock = service(tmp_path)
    outside = resolve_guest_window(clock(), 'Asia/Kolkata', '13:00', '16:00', '2026-10-07')
    assert outside[2] == '1:00-4:00 PM'
    receipt = enroll_named_photo(access.store, Vision(), access, 'Guest Fixture', jpeg_bytes(), True, outside)
    assert receipt['window'] == '1:00-4:00 PM' and receipt['identity_proven'] is False
    blocked = access.admit_named_guest('ab' * 16, receipt['profile_id'])
    assert blocked['text'] == outside_window_text('1:00-4:00 PM')
    assert blocked['text'] == 'recognized guest, outside allowed window (1:00-4:00 PM)'
    assert 'certain' not in blocked['text'] and blocked['identity_proven'] is False
    assert blocked['device_label'] != 'DONE' and blocked['server_confirmed'] is False
    assert access.snapshot()['passes'][0]['status'] == 'approved' and not lock.calls
    inside = resolve_guest_window(clock(), 'Asia/Kolkata', '17:00', '19:00', '2026-10-07')
    access.set_guest_window('Guest Fixture', inside)
    guests = access.snapshot()['named_guests']
    assert guests == [{'name': 'Guest Fixture', 'window': '5:00-7:00 PM'}]
    opened = access.admit_named_guest('cd' * 16, receipt['profile_id'])
    assert opened['device_label'] == 'DONE' and opened['server_confirmed'] is True
    assert opened['real_actuation'] is False and opened['hardware_confirmed'] is False
    assert opened['identity_proven'] is False and len(lock.calls) == 1


def test_dashboard_records_the_outside_window_without_done(tmp_path):
    clock = Clock()
    app = Doorbell(Config(data_dir=tmp_path, camera='none', background=False, guest_timezone='Asia/Kolkata'))
    lock = CountLock()
    app.access = AccessSimulation(app.store, lock, clock, guest_timezone='Asia/Kolkata')
    window = resolve_guest_window(clock(), 'Asia/Kolkata', '13:00', '16:00', '2026-10-07')
    receipt = enroll_named_photo(app.store, Vision(), app.access, 'Guest Fixture', jpeg_bytes(), True, window)
    event = app.store.add_event('ring', 'browser')
    matched = {'status': 'probable_match', 'complete': True, 'profile_id': receipt['profile_id']}
    event = app.store.update_event(event['id'], local_analysis=json.dumps(matched))
    saved = app.record_named_admission(event)
    admission = saved['local_analysis']['admission']
    assert admission['text'] == 'recognized guest, outside allowed window (1:00-4:00 PM)'
    assert admission['device_label'] != 'DONE' and admission['server_confirmed'] is False
    assert admission['identity_proven'] is False and admission['real_actuation'] is False
    assert not lock.calls


def test_keypad_page_confirms_simulation_without_echoing_the_pin(tmp_path):
    app = create_app(Config(data_dir=tmp_path, camera='none', background=False))
    with TestClient(app) as client:
        issued = app.state.doorbell.access.issue_pin(20, 'Parcel')
        pin = issued['pin']
        page = client.get('/pin')
        assert page.status_code == 200 and 'Enter the guest PIN' in page.text
        home = client.get('/')
        assert 'guest-window-list' in home.text and 'guest-pin-list' in home.text
        listed = client.get('/api/access/simulation').json()['guest_pins']
        assert listed[0]['label'] == 'Parcel' and 'pin' not in listed[0] and pin not in json.dumps(listed)
        wrong = client.post('/api/guest-pin', json={'pin': other_pin(pin)})
        assert wrong.status_code == 401 and pin not in wrong.text and wrong.json()['device_label'] != 'DONE'
        opened = client.post('/api/guest-pin', json={'pin': pin})
        body = opened.json()
        assert opened.status_code == 200 and body['device_label'] == 'DONE'
        assert body['server_confirmed'] is True and body['real_actuation'] is False
        assert body['hardware_confirmed'] is False and pin not in opened.text
        assert client.get('/api/access/simulation').json()['guest_pins'] == []


def test_ssh_commands_keep_secrets_on_stdin(tmp_path, monkeypatch):
    calls = []

    class Result:
        returncode = 0
        stdout = b'{"pin":"246801","id":"%s","single_use":true,"real_actuation":false,"window":"1:00-4:00 PM","name":"Visitor"}' % (b'ab' * 16)

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return Result()

    monkeypatch.setattr('agent_doorbell.bridge_client.subprocess.run', run)
    receipt = send_issue_pin('owner@pi.example', 'trusted.example', None, 60, 'Parcel')
    args, kwargs = calls[0]
    assert receipt['pin'] == '246801'
    assert b'246801' not in kwargs['input'] and 'BatchMode=yes' in args
    assert 'StrictHostKeyChecking=yes' in args and 'IdentitiesOnly=yes' in args
    assert 'HostKeyAlias=trusted.example' in args and '_issue-pin' in args[-1]
    assert 'password' not in ' '.join(args).lower()
    send_revoke_pin('owner@pi.example', None, None, 'ab' * 16)
    assert '_revoke-pin' in calls[1][0][-1] and b'246801' not in calls[1][1]['input']
    send_guest_window('owner@pi.example', 'trusted.example', None, 'Visitor', {'from': '13:00', 'until': '16:00'})
    command = calls[2][0][-1]
    assert '_set-guest-window' in command and '13:00' not in command
    assert b'13:00' in calls[2][1]['input'] and b'16:00' in calls[2][1]['input']
    photo = tmp_path / 'guest.jpg'
    photo.write_bytes(jpeg_bytes())
    body = named_photo_body('Visitor', photo, True, {'from': '13:00', 'until': '16:00', 'date': '2026-10-07'})
    _name, _photo, spec = parse_named_payload(body)
    assert spec['from'] == '13:00' and spec['date'] == '2026-10-07'


def test_guest_contract_uses_placeholders(monkeypatch):
    monkeypatch.delenv('DOORBELL_GUEST_TIMEZONE', raising=False)
    assert Config().guest_timezone == 'Asia/Kolkata'
    contract = Path('docs/GROK_BOT_GUEST.md').read_text()
    assert 'OWNER@PI_HOST' in contract and 'TRUSTED_HOST_ALIAS' in contract and 'OWNER_SSH_KEY' in contract
    assert 'issue-pin' in contract and 'revoke-pin' in contract and 'set-guest-window' in contract
    assert 'IdentitiesOnly=yes' in contract and 'BatchMode=yes' in contract and 'StrictHostKeyChecking=yes' in contract
    assert 'Asia/Kolkata' in contract and '/pin' in contract
    assert '192.168.' not in contract and 'example-host' not in contract
