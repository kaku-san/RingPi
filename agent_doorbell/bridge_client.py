"""Portable local-Mac → existing SSH → Pi loopback enrollment client.

Keys stay on the Pi. Photo bytes travel only on stdin, never shell arguments.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import urllib.request
import uuid

from .config import Config
from .enrollment_bridge import BridgeAuth, ImportInput, MAX_BODY, MAX_PHOTO, parse, signature
from .guest_window import resolve_guest_window
from .local_vision import clean_image
from .named_guest import enroll_named_photo, parse_named_payload


def private_read(path, maximum, require_private=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode): raise ValueError('Choose a regular local file')
        if require_private and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ValueError('Bundle must be an owner-only regular file')
        raw = stream.read(maximum + 1)
    if len(raw) > maximum: raise ValueError('Selected file exceeds the supported size')
    return raw


def prepare(name, start, end, photos, output):
    if len(photos) != 5: raise ValueError('Choose exactly five distinct local JPEG/PNG photos')
    references = []
    for path in photos:
        raw = clean_image(private_read(path, 8_000_000))
        if len(raw) > MAX_PHOTO: raise ValueError('Normalized photo exceeds the bridge bound')
        references.append({'sha256': hashlib.sha256(raw).hexdigest(), 'data_base64': base64.b64encode(raw).decode()})
    if len({row['sha256'] for row in references}) != 5: raise ValueError('Choose five distinct photos')
    envelope = {'schema_version': 1, 'request_id': uuid.uuid4().hex, 'visitor_name': name,
                'purpose': 'temporary_visitor_pass', 'valid_from': start, 'valid_until': end, 'references': references}
    encoded = json.dumps(envelope, separators=(',', ':')).encode()
    parse(encoded, ImportInput)
    if len(encoded) > MAX_BODY: raise ValueError('Bundle exceeds the bridge bound')
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = output.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Choose an owner-only bundle directory')
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream: stream.write(encoded)
    return {'request_id': envelope['request_id'], 'reference_count': 5, 'stored_locally': True,
            'transferred': False, 'enrolled': False, 'bundle': str(output)}


def allowed_request(scope, method, route):
    if scope == 'status': return method == 'GET' and route == '/api/enrollment/bridge'
    if scope == 'proposal': return method == 'POST' and route == '/api/enrollment/bridge/proposals'
    return scope == 'owner' and ((method == 'GET' and re.fullmatch(r'/api/enrollment/bridge/proposals/[0-9a-f]{32}', route))
          or (method == 'POST' and re.fullmatch(r'/api/enrollment/bridge/proposals/[0-9a-f]{32}/decision', route)))


def remote_forward(scope, method, route, body):
    if not allowed_request(scope, method, route): raise ValueError('Request outside the enrollment bridge scope')
    if len(body) > (MAX_BODY if scope == 'proposal' else 8192): raise ValueError('Request exceeds its bound')
    headers = {'Content-Type': 'application/json'}
    if scope != 'status':
        auth = BridgeAuth.from_file(None, lambda: datetime.now(timezone.utc), Config().enrollment_bridge_policy)
        if scope not in auth.keys: raise ValueError('Bridge is disabled; owner setup is required on the Pi')
        timestamp, nonce = str(int(datetime.now(timezone.utc).timestamp())), uuid.uuid4().hex
        headers.update({'X-Doorbell-Time': timestamp, 'X-Doorbell-Nonce': nonce,
                        'X-Doorbell-Signature': signature(auth.keys[scope], method, route, timestamp, nonce, body)})
    http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request('http://127.0.0.1:8877' + route, data=body if method=='POST' else None,
                                     headers=headers, method=method)
    try:
        with http.open(request, timeout=30) as response: result = response.read(131073)
    except urllib.error.HTTPError as error:
        # The scoped server returns only static error codes, never images or headers.
        safe = json.loads(error.read(8192))
        raise ValueError('Pi refused enrollment operation: ' + str(safe.get('detail', 'request_failed'))) from None
    if len(result) > 131072: raise ValueError('Response exceeds the bounded metadata read')
    return json.loads(result)


def load_service_data_dir():
    """Read only the service data-directory setting. Values are never printed."""
    path = Path.home() / '.config' / 'agent-doorbell' / 'environment'
    if not path.is_file():
        return
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.removeprefix('export ').strip()
        if key != 'DOORBELL_DATA_DIR':
            continue
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ['DOORBELL_DATA_DIR'] = value
        return


def window_fields(start, end, day):
    if (start is None) != (end is None):
        raise ValueError('Provide both --from and --until, or neither')
    if start is None:
        return None
    spec = {'from': start, 'until': end}
    if day:
        spec['date'] = day
    resolve_guest_window(datetime.now(timezone.utc), 'UTC', start, end, day)
    return spec


def named_photo_body(name, photo, consent=None, window=None):
    del consent
    raw = clean_image(private_read(photo, 8_000_000))
    if len(raw) > MAX_PHOTO:
        raise ValueError('Normalized photo exceeds the bridge bound')
    payload = {'schema_version': 1, 'name': name.strip(),
               'sha256': hashlib.sha256(raw).hexdigest(), 'photo_base64': base64.b64encode(raw).decode()}
    if window:
        payload['window_from'] = window['from']
        payload['window_until'] = window['until']
        if window.get('date'):
            payload['window_date'] = window['date']
    encoded = json.dumps(payload, separators=(',', ':')).encode()
    parse_named_payload(encoded)
    return encoded


def send_named(target, alias, body, identity=None):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9_.:-]+', target):
        raise ValueError('Use user@known-pi-host')
    if alias and not re.fullmatch(r'[A-Za-z0-9_.:-]+', alias):
        raise ValueError('Invalid trusted host alias')
    parse_named_payload(body)
    options = ['-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'UpdateHostKeys=no',
               '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes', '-o', 'RequestTTY=no',
               '-o', 'ConnectTimeout=10']
    if identity is not None:
        info = identity.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Owner-only existing SSH key required')
        options += ['-o', 'IdentitiesOnly=yes', '-i', str(identity)]
    if alias:
        options += ['-o', 'HostKeyAlias=' + alias]
    command = 'exec "$HOME/agent-doorbell/.venv/bin/python" -m agent_doorbell.bridge_client _enroll-named'
    result = subprocess.run(['ssh', *options, target, command], input=body, stdout=subprocess.PIPE, timeout=120)
    if result.returncode:
        raise ValueError('SSH enrollment did not complete; no password prompt and no automatic retry')
    if len(result.stdout) > 8192:
        raise ValueError('SSH result exceeds bounded metadata')
    try:
        receipt = json.loads(result.stdout)
    except ValueError:
        raise ValueError('SSH result was not the expected metadata') from None
    if not isinstance(receipt, dict) or receipt.get('enrolled') is not True or receipt.get('real_actuation') is not False:
        raise ValueError('Pi refused named enrollment')
    return receipt


def ssh_options(alias, identity, identities_only=False):
    options = ['-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'UpdateHostKeys=no',
               '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes', '-o', 'RequestTTY=no',
               '-o', 'ConnectTimeout=10']
    if identities_only or identity is not None:
        options += ['-o', 'IdentitiesOnly=yes']
    if identity is not None:
        info = identity.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Owner-only existing SSH key required')
        options += ['-i', str(identity)]
    if alias:
        options += ['-o', 'HostKeyAlias=' + alias]
    return options


def ssh_json(target, alias, identity, command, body):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9_.:-]+', target):
        raise ValueError('Use user@known-pi-host')
    if alias and not re.fullmatch(r'[A-Za-z0-9_.:-]+', alias):
        raise ValueError('Invalid trusted host alias')
    result = subprocess.run(['ssh', *ssh_options(alias, identity, True), target, command],
                            input=body, stdout=subprocess.PIPE, timeout=120)
    if result.returncode:
        raise ValueError('SSH bridge did not complete; no password prompt and no automatic retry')
    if len(result.stdout) > 8192:
        raise ValueError('SSH result exceeds bounded metadata')
    try:
        receipt = json.loads(result.stdout)
    except ValueError:
        raise ValueError('SSH result was not the expected metadata') from None
    if not isinstance(receipt, dict) or receipt.get('real_actuation') is not False:
        raise ValueError('Pi refused the guest operation')
    return receipt


def enroll_named_locally():
    load_service_data_dir()
    from .access_simulation import AccessSimulation
    from .local_vision import LocalVision
    from .store import Store
    config = Config()
    raw = sys.stdin.buffer.read(2_000_001)
    name, photo, window_spec = parse_named_payload(raw)
    store = Store(config.data_dir)
    vision = LocalVision(config.data_dir / 'models')
    if not vision.status['available']:
        raise ValueError('Local face models are not available on this Pi')
    access = AccessSimulation(store, guest_timezone=config.guest_timezone)
    window = None
    if window_spec:
        window = resolve_guest_window(access.clock(), config.guest_timezone, window_spec['from'],
                                      window_spec['until'], window_spec.get('date'))
    return enroll_named_photo(store, vision, access, name, photo, True, window)


def _remote_json(maximum=4096):
    raw = sys.stdin.buffer.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('Guest command exceeds its bound')
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError('Guest command was not JSON') from None
    if not isinstance(payload, dict):
        raise ValueError('Guest command was not JSON')
    return payload


def _local_access():
    load_service_data_dir()
    from .access_simulation import AccessSimulation
    from .store import Store
    config = Config()
    return AccessSimulation(Store(config.data_dir), guest_timezone=config.guest_timezone), config


def issue_pin_locally():
    from .access_simulation import AccessError
    payload = _remote_json()
    if set(payload) - {'minutes', 'label'}:
        raise ValueError('PIN request has unexpected fields')
    access, _config = _local_access()
    try:
        return access.issue_pin(payload.get('minutes', 60), payload.get('label', 'Guest'))
    except AccessError as error:
        raise ValueError(error.code) from None


def revoke_pin_locally():
    from .access_simulation import AccessError
    payload = _remote_json()
    if set(payload) != {'id'}:
        raise ValueError('PIN revoke has unexpected fields')
    access, _config = _local_access()
    try:
        receipt = access.revoke_pin(payload['id'])
    except AccessError as error:
        raise ValueError(error.code) from None
    receipt.pop('pin', None)
    return receipt


def set_guest_window_locally():
    from .access_simulation import AccessError
    payload = _remote_json()
    if set(payload) - {'name', 'window_from', 'window_until', 'window_date'}:
        raise ValueError('Guest window has unexpected fields')
    if not {'name', 'window_from', 'window_until'} <= set(payload):
        raise ValueError('Guest window needs a name, start and end')
    access, config = _local_access()
    window = resolve_guest_window(access.clock(), config.guest_timezone, payload['window_from'],
                                  payload['window_until'], payload.get('window_date'))
    try:
        return access.set_guest_window(payload['name'], window)
    except AccessError as error:
        raise ValueError(error.code) from None


def send_issue_pin(target, alias, identity, minutes, label):
    if type(minutes) is not int or not 1 <= minutes <= 24 * 60:
        raise ValueError('PIN expiry must be from 1 minute through 24 hours')
    if not isinstance(label, str) or not label.strip() or len(label.strip()) > 60:
        raise ValueError('Use a PIN label of 1 to 60 characters')
    body = json.dumps({'minutes': minutes, 'label': label}, separators=(',', ':')).encode()
    command = 'exec "$HOME/agent-doorbell/.venv/bin/python" -m agent_doorbell.bridge_client _issue-pin'
    receipt = ssh_json(target, alias, identity, command, body)
    pin = receipt.get('pin')
    if not isinstance(pin, str) or not re.fullmatch(r'\d{6}', pin) or receipt.get('single_use') is not True:
        raise ValueError('Pi refused pin issue')
    return receipt


def send_revoke_pin(target, alias, identity, pin_id):
    if not re.fullmatch(r'[0-9a-f]{32}', pin_id):
        raise ValueError('Invalid pin id')
    body = json.dumps({'id': pin_id}, separators=(',', ':')).encode()
    command = 'exec "$HOME/agent-doorbell/.venv/bin/python" -m agent_doorbell.bridge_client _revoke-pin'
    receipt = ssh_json(target, alias, identity, command, body)
    for secret in ('pin', 'pin_hash', 'salt'):
        receipt.pop(secret, None)
    return receipt


def send_guest_window(target, alias, identity, name, window):
    payload = {'name': name, 'window_from': window['from'], 'window_until': window['until']}
    if window.get('date'):
        payload['window_date'] = window['date']
    body = json.dumps(payload, separators=(',', ':')).encode()
    command = 'exec "$HOME/agent-doorbell/.venv/bin/python" -m agent_doorbell.bridge_client _set-guest-window'
    return ssh_json(target, alias, identity, command, body)


def send(target, alias, scope, method, route, body):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9_.:-]+', target): raise ValueError('Use user@known-pi-host')
    if alias and not re.fullmatch(r'[A-Za-z0-9_.:-]+', alias): raise ValueError('Invalid trusted host alias')
    if not allowed_request(scope, method, route): raise ValueError('Request outside the enrollment bridge scope')
    if len(body) > (MAX_BODY if scope == 'proposal' else 8192): raise ValueError('Request exceeds its bound')
    options = ['-o', 'StrictHostKeyChecking=yes', '-o', 'UpdateHostKeys=no', '-o', 'ForwardAgent=no',
               '-o', 'ClearAllForwardings=yes', '-o', 'RequestTTY=no', '-o', 'ConnectTimeout=10']
    if alias: options += ['-o', 'HostKeyAlias=' + alias]
    command = 'exec "$HOME/agent-doorbell/.venv/bin/python" -m agent_doorbell.bridge_client _forward ' + ' '.join((scope, method, route))
    result = subprocess.run(['ssh', *options, target, command], input=body, stdout=subprocess.PIPE, timeout=60)
    if result.returncode: raise ValueError('SSH bridge did not complete; authenticate only in your Terminal or inspect its static error')
    if len(result.stdout) > 131072: raise ValueError('SSH result exceeds bounded metadata')
    try: return json.loads(result.stdout)
    except ValueError: raise ValueError('SSH result was not the expected metadata; no automatic retry') from None


def send_restricted(target, alias, identity, body):
    from .proposal_gate import COMMAND
    if not re.fullmatch(r'doorbell-proposer@[A-Za-z0-9_.:-]+', target):
        raise ValueError('Use the separately provisioned doorbell-proposer identity')
    if alias and not re.fullmatch(r'[A-Za-z0-9_.:-]+', alias): raise ValueError('Invalid trusted host alias')
    parse(body, ImportInput)
    if len(body) > MAX_BODY: raise ValueError('Proposal exceeds its bound')
    info = identity.lstat()  # metadata only; never read private-key contents
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Owner-only dedicated credential required; no default-key fallback')
    options = ['-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'UpdateHostKeys=no',
               '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes', '-o', 'RequestTTY=no',
               '-o', 'ConnectTimeout=10', '-o', 'IdentitiesOnly=yes', '-o', 'ControlMaster=no',
               '-o', 'ControlPath=none', '-o', 'ControlPersist=no']
    if alias: options += ['-o', 'HostKeyAlias=' + alias]
    # SSH consumes the approved credential; this client never opens key contents.
    options += ['-i', str(identity)]
    result = subprocess.run(['/usr/bin/ssh', '-F', '/dev/null', *options, target, COMMAND],
                            input=body, stdout=subprocess.PIPE, timeout=60)
    if result.returncode: raise ValueError('Restricted proposal did not complete; no owner-account fallback')
    if len(result.stdout) > 65_536: raise ValueError('Oversized restricted receipt')
    receipt = json.loads(result.stdout)
    if not isinstance(receipt, dict) or 'detail' in receipt: raise ValueError('Proposal refused')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare', help='create an owner-selected local bundle; no network or enrollment')
    p.add_argument('--name', required=True); p.add_argument('--valid-from', required=True); p.add_argument('--valid-until', required=True)
    p.add_argument('--photo', type=Path, action='append', required=True); p.add_argument('--output', type=Path, required=True)
    for operation in ('check', 'submit', 'submit-restricted', 'review', 'approve', 'revoke', 'dismiss'):
        p = commands.add_parser(operation)
        p.add_argument('--ssh-target', required=True); p.add_argument('--host-key-alias')
        if operation in {'submit', 'submit-restricted'}:
            p.add_argument('--bundle', type=Path, required=True); p.add_argument('--confirm-transfer', action='store_true')
        if operation == 'submit-restricted': p.add_argument('--identity-file', type=Path, required=True)
        if operation in {'review', 'approve', 'revoke', 'dismiss'}: p.add_argument('--proposal-id', required=True)
        if operation in {'approve', 'revoke', 'dismiss'}: p.add_argument('--digest', required=True)
    p = commands.add_parser('enroll-named', help='send one consented photo and a name over existing SSH')
    p.add_argument('--ssh-target', required=True)
    p.add_argument('--host-key-alias')
    p.add_argument('--identity-file', type=Path)
    p.add_argument('--name', required=True)
    p.add_argument('--photo', type=Path, required=True)
    p.add_argument('--from', dest='window_from')
    p.add_argument('--until', dest='window_until')
    p.add_argument('--date')
    for operation in ('issue-pin', 'revoke-pin', 'set-guest-window'):
        p = commands.add_parser(operation)
        p.add_argument('--ssh-target', required=True)
        p.add_argument('--host-key-alias')
        p.add_argument('--identity-file', type=Path)
        if operation == 'issue-pin':
            p.add_argument('--minutes', type=int, default=60)
            p.add_argument('--label', default='Guest')
        elif operation == 'revoke-pin':
            p.add_argument('--id', required=True)
        else:
            p.add_argument('--name', required=True)
            p.add_argument('--from', dest='window_from', required=True)
            p.add_argument('--until', dest='window_until', required=True)
            p.add_argument('--date')
    p = commands.add_parser('_enroll-named', help=argparse.SUPPRESS)
    p = commands.add_parser('_issue-pin', help=argparse.SUPPRESS)
    p = commands.add_parser('_revoke-pin', help=argparse.SUPPRESS)
    p = commands.add_parser('_set-guest-window', help=argparse.SUPPRESS)
    p = commands.add_parser('_forward', help=argparse.SUPPRESS)
    p.add_argument('scope'); p.add_argument('method'); p.add_argument('route')
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            result = prepare(args.name, args.valid_from, args.valid_until, args.photo, args.output)
        elif args.command == 'enroll-named':
            result = send_named(args.ssh_target, args.host_key_alias,
                                named_photo_body(args.name, args.photo, window=window_fields(args.window_from, args.window_until, args.date)),
                                args.identity_file)
        elif args.command == 'issue-pin':
            result = send_issue_pin(args.ssh_target, args.host_key_alias, args.identity_file, args.minutes, args.label)
        elif args.command == 'revoke-pin':
            result = send_revoke_pin(args.ssh_target, args.host_key_alias, args.identity_file, args.id)
        elif args.command == 'set-guest-window':
            result = send_guest_window(args.ssh_target, args.host_key_alias, args.identity_file, args.name,
                                       window_fields(args.window_from, args.window_until, args.date))
        elif args.command == '_enroll-named':
            result = enroll_named_locally()
        elif args.command == '_issue-pin':
            result = issue_pin_locally()
        elif args.command == '_revoke-pin':
            result = revoke_pin_locally()
        elif args.command == '_set-guest-window':
            result = set_guest_window_locally()
        elif args.command == 'submit-restricted':
            if not args.confirm_transfer: raise ValueError('Approve the exact bundle transfer')
            result = send_restricted(args.ssh_target, args.host_key_alias, args.identity_file,
                                     private_read(args.bundle, MAX_BODY, True))
        elif args.command == '_forward':
            raw = sys.stdin.buffer.read(MAX_BODY + 1)
            result = remote_forward(args.scope, args.method, args.route, raw)
        else:
            scope, method, route, raw = 'status', 'GET', '/api/enrollment/bridge', b''
            if args.command == 'submit':
                if not args.confirm_transfer: raise ValueError('Approve the exact five-photo/name transfer before submitting')
                scope, method, route = 'proposal', 'POST', '/api/enrollment/bridge/proposals'
                raw = private_read(args.bundle, MAX_BODY, True); parse(raw, ImportInput)
            elif args.command != 'check':
                if not re.fullmatch(r'[0-9a-f]{32}', args.proposal_id): raise ValueError('Invalid proposal ID')
                scope, route = 'owner', '/api/enrollment/bridge/proposals/' + args.proposal_id
                if args.command != 'review':
                    if not re.fullmatch(r'[0-9a-f]{64}', args.digest): raise ValueError('Invalid reviewed digest')
                    method, route = 'POST', route + '/decision'
                    raw = json.dumps({'payload_digest': args.digest, 'decision': args.command}).encode()
            result = send(args.ssh_target, args.host_key_alias, scope, method, route, raw)
        print(json.dumps(result, indent=2))
    except Exception:
        # File errors, SSH bodies and model/tool exception text never expose secrets or photos.
        raise SystemExit('Bridge operation did not complete. Review the bounded Pi receipt and owner approvals; no automatic retry.') from None


if __name__ == '__main__':
    main()
