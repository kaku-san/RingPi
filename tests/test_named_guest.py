"""One consented photo enrolls a named guest and a later probable match is simulation only."""
import json
import os
from io import BytesIO
from pathlib import Path

from PIL import Image

from agent_doorbell.access_simulation import AccessSimulation
from agent_doorbell.app import Doorbell
from agent_doorbell.bridge_client import load_service_data_dir, named_photo_body, send_named
from agent_doorbell.config import Config
from agent_doorbell.named_guest import enroll_named_photo, parse_named_payload
from agent_doorbell.store import Store
from tests.test_access_simulation import Clock, CountLock


class Vision:
    def enroll(self, raw):
        return raw, [0.2, 0.4, 0.6], {}


def jpeg_bytes():
    buffer = BytesIO()
    Image.new('RGB', (8, 8), 'blue').save(buffer, 'JPEG')
    return buffer.getvalue()


def service(tmp_path):
    clock = Clock()
    lock = CountLock()
    access = AccessSimulation(Store(tmp_path), lock, clock)
    return access, lock


def test_named_photo_enrolls_under_that_name_and_a_probable_match_is_simulated(tmp_path):
    access, lock = service(tmp_path)
    receipt = enroll_named_photo(access.store, Vision(), access, 'Guest Fixture', jpeg_bytes(), True)
    assert receipt['enrolled'] is True and receipt['identity_proven'] is False and receipt['real_actuation'] is False
    assert receipt['face_samples'] == 1 and not lock.calls
    profile = access.store.profiles()[0]
    assert profile['name'] == 'Guest Fixture' and profile['id'] == receipt['profile_id']
    assert access.store.face_samples()[0]['profile_id'] == profile['id']
    household = access.store.add_profile('Household', '')
    assert access.admit_named_guest('ab' * 16, household['id']) is None and not lock.calls
    event_id = 'cd' * 16
    done = access.admit_named_guest(event_id, profile['id'])
    assert done['device_label'] == 'DONE' and done['server_confirmed'] is True
    assert done['real_actuation'] is False and done['hardware_confirmed'] is False and done['identity_proven'] is False
    assert len(lock.calls) == 1 and lock.calls[0][0] == 'unlock'
    again = access.admit_named_guest(event_id, profile['id'])
    assert again['device_label'] == 'DONE' and len(lock.calls) == 1
    second = access.admit_named_guest('ef' * 16, profile['id'])
    assert second['device_label'] != 'DONE' and len(lock.calls) == 1


def test_incomplete_or_household_match_does_not_show_done(tmp_path):
    app = Doorbell(Config(data_dir=tmp_path, camera='none', background=False))
    lock = CountLock()
    app.access = AccessSimulation(app.store, lock)
    receipt = enroll_named_photo(app.store, Vision(), app.access, 'Guest Fixture', jpeg_bytes(), True)
    event = app.store.add_event('ring', 'browser')
    uncertain = {'status': 'uncertain', 'complete': True, 'profile_id': receipt['profile_id']}
    event = app.store.update_event(event['id'], local_analysis=json.dumps(uncertain))
    assert app.record_named_admission(event) is None and not lock.calls
    matched = {'status': 'probable_match', 'complete': True, 'profile_id': receipt['profile_id']}
    event = app.store.update_event(event['id'], local_analysis=json.dumps(matched))
    saved = app.record_named_admission(event)
    admission = saved['local_analysis']['admission']
    assert admission['device_label'] == 'DONE' and admission['server_confirmed'] is True
    assert admission['identity_proven'] is False and admission['real_actuation'] is False
    assert admission['hardware_confirmed'] is False and len(lock.calls) == 1


def test_ssh_payload_stays_on_stdin(tmp_path, monkeypatch):
    photo = tmp_path / 'guest.jpg'
    photo.write_bytes(jpeg_bytes())
    body = named_photo_body('Guest', photo)
    parsed_name, parsed_photo, window = parse_named_payload(body)
    assert window is None and parsed_name == 'Guest' and parsed_photo.startswith(b'\xff\xd8')
    calls = []

    class Result:
        returncode = 0
        stdout = b'{"enrolled": true, "real_actuation": false, "identity_proven": false}'

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return Result()

    monkeypatch.setattr('agent_doorbell.bridge_client.subprocess.run', run)
    receipt = send_named('owner@pi.example', 'trusted.example', body)
    args, kwargs = calls[0]
    assert receipt['enrolled'] is True
    assert kwargs['input'] == body and b'photo_base64' not in ' '.join(args).encode()
    assert 'StrictHostKeyChecking=yes' in args and 'BatchMode=yes' in args
    assert 'HostKeyAlias=trusted.example' in args and '_enroll-named' in args[-1]
    assert 'password' not in ' '.join(args).lower()
    with __import__('pytest').raises(ValueError):
        send_named('owner@pi.example;id', None, body)


def test_environment_loader_keeps_other_settings_out(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    folder = home / '.config' / 'agent-doorbell'
    folder.mkdir(parents=True)
    (folder / 'environment').write_text('DOORBELL_GROK_KEY=secret-value\nDOORBELL_DATA_DIR=/srv/doorbell-data\n')
    monkeypatch.setattr('agent_doorbell.bridge_client.Path.home', lambda: home)
    monkeypatch.delenv('DOORBELL_DATA_DIR', raising=False)
    monkeypatch.delenv('DOORBELL_GROK_KEY', raising=False)
    load_service_data_dir()
    assert os.environ['DOORBELL_DATA_DIR'] == '/srv/doorbell-data'
    assert 'DOORBELL_GROK_KEY' not in os.environ
    contract = Path('docs/GROK_BOT_GUEST.md').read_text()
    assert 'OWNER@PI_HOST' in contract and 'StrictHostKeyChecking=yes' in contract
    assert '192.168.' not in contract and 'example-host' not in contract
