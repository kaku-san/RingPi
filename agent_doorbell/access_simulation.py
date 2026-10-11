"""Local-only access workflow fixtures. No real lock, credentials or Grok ingress."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import hashlib
import hmac
import json
import math
import re
import secrets
import threading
from typing import Protocol
import uuid

from .guest_window import outside_window_text


class AccessError(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)


class HardwareAdapter(Protocol):
    """Future reviewed hardware boundary: state and an action-bound confirmation."""
    def read_state(self): ...
    def execute(self, action, request_id): ...


class LockDriver(Protocol):
    """Plug-in boundary for a later lock.

    This release registers only the simulation driver. A future driver must
    report action-bound confirmation and must not be called by face or caption
    workers. hardware_confirmed true is never treated as success here.
    """
    name: str
    simulated: bool
    real_actuation: bool
    capabilities: set
    def read_state(self): ...
    def execute(self, action, request_id): ...


_LOCK_PLUGINS = {}


def lock_plugins():
    return tuple(sorted(_LOCK_PLUGINS))


def load_lock_driver(name="simulation"):
    if name != "simulation" or name not in _LOCK_PLUGINS:
        raise ValueError("Only a SimulationLock fixture can be connected")
    driver = _LOCK_PLUGINS[name]()
    if not isinstance(driver, SimulationLock) or driver.simulated is not True or driver.real_actuation is not False:
        raise ValueError("Only a SimulationLock fixture can be connected")
    return driver


def register_lock_driver(name, factory):
    """Reserved for a later reviewed hardware driver. Refused in this release."""
    raise ValueError("Only a SimulationLock fixture can be connected")


def device_label(result):
    """DONE is allowed only for a server-confirmed simulation result."""
    if not isinstance(result, dict):
        return "pending"
    if (result.get("status") == "simulated_completed"
            and result.get("code") == "simulation_only_confirmation"
            and result.get("simulation_only") is True
            and result.get("hardware_confirmed") is False
            and result.get("real_actuation") is False
            and result.get("server_confirmed") is True):
        return "DONE"
    if result.get("status") in {"denied", "failed", "rejected", "dismissed"}:
        return "denied"
    if result.get("status") in {"uncertain", "expired", "revoked"}:
        return "uncertain"
    return "pending"


class UnconfiguredHardware:
    def read_state(self):
        return {'state': 'unknown', 'hardware_confirmed': False}
    def execute(self, action, request_id):
        raise AccessError('hardware_unconfigured')


class UnconfiguredGrokTransport:
    def receive(self, envelope):
        raise AccessError('grok_return_transport_unconfigured')


class MockOwnerVerification:
    """Fixture verification only. This is not authentication of a real owner."""
    kind = 'simulation_mock'
    def verify(self, stored_digest, provided_digest, confirmed):
        return confirmed is True and hmac.compare_digest(stored_digest, provided_digest)


class MockVisitorVerification:
    def verify(self, visitor_id, face_status, face_visitor_id, second_factor):
        if face_status != 'probable_match' or face_visitor_id != visitor_id:
            raise AccessError('visitor_face_not_accepted')
        if second_factor != 'verified_mock':
            raise AccessError('visitor_second_factor_required')


class SimulationLock:
    """Pure memory fixture. The only lock plugin this release will connect."""
    name = "simulation"
    simulated = True
    real_actuation = False
    capabilities = {'lock', 'unlock'}
    def __init__(self):
        self.state = 'locked'
        self.door_position = 'unknown'
    def read_state(self):
        return {'simulated_state': self.state, 'simulated_door_position': self.door_position,
                'hardware_state': 'unknown', 'hardware_confirmed': False,
                'capabilities': sorted(self.capabilities)}
    def execute(self, action, request_id):
        if action not in self.capabilities:
            raise AccessError('unsupported_capability')
        if self.state not in {'locked', 'unlocked'}:
            raise AccessError('lock_state_unknown')
        self.state = 'locked' if action == 'lock' else 'unlocked'
        return {'simulated_state': self.state, 'simulated_door_position': self.door_position,
                'simulation_confirmed': True, 'hardware_confirmed': False, 'action': action, 'request_id': request_id}


_LOCK_PLUGINS["simulation"] = SimulationLock


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec='milliseconds')


def digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class AccessSimulation:
    """Persistent proposal/pass ledger, connected exclusively to a memory fixture.

    Approvals and visitor factors in this class are explicitly mocked. There is no
    selection of a real adapter, inbound webhook or face-event hook. A guest PIN is a
    salted one-time keypad fallback that consumes the same simulated pass.
    """
    def __init__(self, store, simulator=None, clock=None, guest_timezone='Asia/Kolkata'):
        self.store = store
        if simulator is None:
            simulator = load_lock_driver("simulation")
        elif (not isinstance(simulator, SimulationLock) or simulator.simulated is not True
              or simulator.real_actuation is not False):
            raise ValueError('Only a SimulationLock fixture can be connected')
        self.simulator = simulator
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.guest_timezone = guest_timezone or 'Asia/Kolkata'
        self.owner = MockOwnerVerification()
        self.visitor = MockVisitorVerification()
        self.hardware = UnconfiguredHardware()
        self.transport = UnconfiguredGrokTransport()
        self.lock = threading.RLock()
        with store.connection() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS access_commands (id TEXT PRIMARY KEY, digest TEXT NOT NULL,
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL, status TEXT NOT NULL,
                payload TEXT NOT NULL, result TEXT);
              CREATE TABLE IF NOT EXISTS access_visitors (id TEXT PRIMARY KEY, name TEXT NOT NULL,
                mock_reference_count INTEGER NOT NULL, created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS access_passes (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
                digest TEXT NOT NULL, visitor_id TEXT NOT NULL, created_at TEXT NOT NULL,
                valid_from TEXT NOT NULL, valid_until TEXT NOT NULL, approval_until TEXT NOT NULL,
                status TEXT NOT NULL, payload TEXT NOT NULL, result TEXT);
              CREATE TABLE IF NOT EXISTS access_arrivals (id TEXT PRIMARY KEY, pass_id TEXT NOT NULL,
                digest TEXT NOT NULL, created_at TEXT NOT NULL, result TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS access_audit (id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at TEXT NOT NULL, kind TEXT NOT NULL, target_id TEXT NOT NULL,
                status TEXT NOT NULL, code TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS access_guest_approvals (id TEXT PRIMARY KEY,
                event_id TEXT UNIQUE NOT NULL, digest TEXT NOT NULL, visitor_id TEXT NOT NULL,
                created_at TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL, result TEXT);
              CREATE TABLE IF NOT EXISTS named_guest_grants (profile_id TEXT PRIMARY KEY,
                visitor_id TEXT NOT NULL, pass_id TEXT NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL,
                window_start TEXT, window_end TEXT, window_label TEXT,
                window_from TEXT, window_until TEXT, window_daily INTEGER);
              CREATE TABLE IF NOT EXISTS guest_pins (id TEXT PRIMARY KEY, label TEXT NOT NULL,
                salt TEXT NOT NULL, pin_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL, single_use INTEGER NOT NULL, status TEXT NOT NULL,
                pass_id TEXT NOT NULL, visitor_id TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS guest_pin_attempts (id INTEGER PRIMARY KEY CHECK (id = 1),
                failures INTEGER NOT NULL, locked_until TEXT);
            ''')
            grant_columns = {row['name'] for row in db.execute('PRAGMA table_info(named_guest_grants)')}
            for column, kind in (
                    ('window_start', 'TEXT'), ('window_end', 'TEXT'), ('window_label', 'TEXT'),
                    ('window_from', 'TEXT'), ('window_until', 'TEXT'), ('window_daily', 'INTEGER')):
                if column not in grant_columns:
                    db.execute(f'ALTER TABLE named_guest_grants ADD COLUMN {column} {kind}')
            db.execute('INSERT OR IGNORE INTO guest_pin_attempts (id, failures, locked_until) VALUES (1, 0, NULL)')
            # Never retry an action interrupted by a restart, even in simulation.
            for table in ('access_commands', 'access_passes'):
                for row in db.execute(f"SELECT id FROM {table} WHERE status='executing'").fetchall():
                    result = self.outcome('uncertain', 'restart_interrupted')
                    db.execute(f'UPDATE {table} SET status=?,result=? WHERE id=?', ('uncertain', json.dumps(result), row['id']))
                    self.audit(db, 'restart', row['id'], 'uncertain', 'restart_interrupted')
            for row in db.execute('SELECT id,pass_id,result FROM access_arrivals').fetchall():
                if json.loads(row['result']).get('status') == 'executing':
                    result = self.outcome('uncertain', 'restart_interrupted', pass_id=row['pass_id'])
                    db.execute('UPDATE access_arrivals SET result=? WHERE id=?', (json.dumps(result), row['id']))

    def outcome(self, status, code, **values):
        return {'status': status, 'code': code, 'simulation_only': True,
                'hardware_confirmed': False, 'real_actuation': False, **values}

    def audit(self, db, kind, target_id, status, code):
        db.execute('INSERT INTO access_audit(occurred_at,kind,target_id,status,code) VALUES (?,?,?,?,?)',
                   (stamp(self.clock()), kind, target_id, status, code))
        db.execute('DELETE FROM access_audit WHERE id IN (SELECT id FROM access_audit ORDER BY id DESC LIMIT -1 OFFSET 1000)')

    def expire(self, db):
        now = stamp(self.clock())
        for row in db.execute("SELECT id FROM access_commands WHERE status='pending' AND expires_at<=?", (now,)).fetchall():
            db.execute("UPDATE access_commands SET status='expired' WHERE id=?", (row['id'],))
            self.audit(db, 'command', row['id'], 'expired', 'proposal_expired')
        for row in db.execute("SELECT id FROM access_passes WHERE (status='approved' AND valid_until<=?) OR (status='pending' AND (approval_until<=? OR valid_until<=?))", (now, now, now)).fetchall():
            db.execute("UPDATE access_passes SET status='expired' WHERE id=?", (row['id'],))
            self.audit(db, 'pass', row['id'], 'expired', 'pass_expired')
        for row in db.execute("SELECT id FROM guest_pins WHERE status='active' AND expires_at<=?", (now,)).fetchall():
            db.execute("UPDATE guest_pins SET status='expired' WHERE id=?", (row['id'],))
            self.audit(db, 'pin', row['id'], 'expired', 'pin_expired')
        db.commit()

    def reject(self, db, kind, target_id, code):
        self.audit(db, kind, target_id, 'rejected', code)
        db.commit()
        raise AccessError(code)

    @staticmethod
    def row(row):
        if row is None:
            raise AccessError('simulation_item_not_found', 404)
        item = dict(row)
        item['payload'] = json.loads(item['payload'])
        item['result'] = json.loads(item['result']) if item.get('result') else None
        item['simulation_only'] = True
        return item

    @staticmethod
    def _hex_id(value):
        return isinstance(value, str) and len(value) == 32 and all(character in '0123456789abcdef' for character in value)

    def guest_view(self, item):
        item = dict(item)
        basis = item.get('result') if isinstance(item.get('result'), dict) else {'status': item.get('status')}
        item['device_label'] = device_label(basis)
        item['identity_proven'] = False
        item['real_actuation'] = False
        return item

    def capacity(self, db, table):
        if db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] >= 200:
            raise AccessError('simulation_history_limit')

    def status(self):
        return {'simulation_only': True, 'real_actuation_enabled': False,
                'lock': self.simulator.read_state(), 'physical_door_position': 'unknown',
                'hardware': 'unconfigured', 'grok_return_transport': 'unconfigured',
                'owner_authentication': 'unconfigured', 'visitor_second_factor': 'unconfigured',
                'photo_ingress': 'unconfigured', 'temporary_references': 'mock_metadata_only',
                'owner_checks': 'simulation_mock', 'max_pass_window_hours': 24,
                'arrival_owner_approval_required': False, 'lock_plugin': self.simulator.name,
                'lock_plugins': list(lock_plugins()), 'real_lock_registered': False,
                'device_done_rule': 'server_confirmed_simulation_only'}

    def snapshot(self):
        with self.lock, self.store.connection() as db:
            self.expire(db)
            now_stamp = stamp(self.clock())
            return {'status': self.status(),
                'commands': [self.row(r) for r in db.execute('SELECT * FROM access_commands ORDER BY created_at DESC LIMIT 30')],
                'passes': [self.row(r) for r in db.execute('SELECT * FROM access_passes ORDER BY created_at DESC LIMIT 30')],
                'guest_approvals': [self.guest_view(self.row(r)) for r in db.execute('SELECT * FROM access_guest_approvals ORDER BY created_at DESC LIMIT 30')],
                'named_guests': [{'name': row['name'], 'window': row['window_label']} for row in db.execute(
                    'SELECT name, window_label FROM named_guest_grants ORDER BY name COLLATE NOCASE')],
                'guest_pins': [{'label': row['label'], 'expires_at': row['expires_at']} for row in db.execute(
                    "SELECT label, expires_at FROM guest_pins WHERE status='active' AND expires_at>? ORDER BY expires_at", (now_stamp,))],
                'audit': [dict(r) | {'simulation_only': True} for r in db.execute('SELECT * FROM access_audit ORDER BY id DESC LIMIT 50')]}

    def command(self, request_id, action):
        if action not in {'lock', 'unlock', 'open'}:
            raise AccessError('unsupported_command', 422)
        payload = {'action': action, 'simulation_only': True}
        hashed = digest(payload)
        with self.lock, self.store.connection() as db:
            self.expire(db)
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT * FROM access_commands WHERE id=?', (request_id,)).fetchone()
            if existing:
                if existing['digest'] != hashed: self.reject(db, 'command', request_id, 'replay_payload_changed')
                return self.row(existing)
            self.capacity(db, 'access_commands')
            now = self.clock()
            db.execute('INSERT INTO access_commands VALUES (?,?,?,?,?,?,NULL)',
                       (request_id, hashed, stamp(now), stamp(now+timedelta(minutes=5)), 'pending', json.dumps(payload)))
            self.audit(db, 'command', request_id, 'pending', 'owner_review_required')
            return self.row(db.execute('SELECT * FROM access_commands WHERE id=?', (request_id,)).fetchone())

    def apply(self, action, request_id):
        try:
            state = self.simulator.read_state()
            if action not in state['capabilities']:
                raise AccessError('unsupported_capability')
            if state['simulated_state'] not in {'locked', 'unlocked'}:
                raise AccessError('lock_state_unknown')
            result = self.simulator.execute(action, request_id)
            expected = {'lock': 'locked', 'unlock': 'unlocked'}
            if (result.get('simulation_confirmed') is not True or result.get('hardware_confirmed') is not False
                    or result.get('request_id') != request_id or result.get('action') != action
                    or (action in expected and result.get('simulated_state') != expected[action])
                    or (action == 'open' and result.get('simulated_door_position') != 'open')):
                self.simulator.state = 'unknown'
                return self.outcome('uncertain', 'simulation_confirmation_missing')
            return self.outcome('simulated_completed', 'simulation_only_confirmation',
                                action=action, request_id=request_id, simulated_state=result['simulated_state'],
                                simulated_door_position=result.get('simulated_door_position', 'unknown'))
        except AccessError as error:
            if error.code in {'unsupported_capability', 'lock_state_unknown'}:
                return self.outcome('failed', error.code)
            self.simulator.state = 'unknown'
            return self.outcome('uncertain', 'simulation_failed')
        except Exception:
            self.simulator.state = 'unknown'
            return self.outcome('uncertain', 'simulation_failed')

    def decide_command(self, request_id, provided_digest, confirmed, decision):
        with self.lock, self.store.connection() as db:
            self.expire(db)
            item = self.row(db.execute('SELECT * FROM access_commands WHERE id=?', (request_id,)).fetchone())
            if not self.owner.verify(item['digest'], provided_digest, confirmed):
                self.reject(db, 'command', request_id, 'mock_owner_confirmation_required')
            self.expire(db)
            item = self.row(db.execute('SELECT * FROM access_commands WHERE id=?', (request_id,)).fetchone())
            if item['status'] == 'expired': raise AccessError('proposal_expired')
            if item['status'] != 'pending': return item
            if decision not in {'approve', 'dismiss'}: raise AccessError('invalid_decision', 422)
            result = self.outcome('dismissed', 'owner_dismissed')
            if decision == 'approve':
                changed = db.execute("UPDATE access_commands SET status='executing' WHERE id=? AND status='pending' AND expires_at>?", (request_id, stamp(self.clock()))).rowcount
                db.commit()
                if not changed:
                    self.expire(db)
                    return self.row(db.execute('SELECT * FROM access_commands WHERE id=?', (request_id,)).fetchone())
                result = self.apply(item['payload']['action'], request_id)
            allowed = 'executing' if decision == 'approve' else 'pending'
            changed = db.execute('UPDATE access_commands SET status=?,result=? WHERE id=? AND status=?', (result['status'], json.dumps(result), request_id, allowed)).rowcount
            if not changed:
                return self.row(db.execute('SELECT * FROM access_commands WHERE id=?', (request_id,)).fetchone())
            self.audit(db, 'command', request_id, result['status'], result['code'])
            return self.row(db.execute('SELECT * FROM access_commands WHERE id=?', (request_id,)).fetchone())

    def pass_request(self, request_id, name, mock_reference_count, valid_from, valid_until):
        now = self.clock()
        if valid_from.tzinfo is None or valid_until.tzinfo is None:
            raise AccessError('invalid_pass_window', 422)
        if not name.strip() or len(name.strip()) > 60 or not 1 <= mock_reference_count <= 5:
            raise AccessError('invalid_temporary_visitor', 422)
        base = {'visitor_name': name.strip(), 'mock_reference_count': mock_reference_count,
                'reference_source': 'simulation_fixture', 'valid_from': stamp(valid_from), 'valid_until': stamp(valid_until),
                'action': 'unlock', 'max_uses': 1, 'simulation_only': True}
        with self.lock, self.store.connection() as db:
            self.expire(db)
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT * FROM access_passes WHERE request_id=?', (request_id,)).fetchone()
            if existing:
                original = json.loads(existing['payload']); original.pop('visitor_id')
                if digest(original) != digest(base): self.reject(db, 'pass', existing['id'], 'replay_payload_changed')
                return self.row(existing)
            if (not now-timedelta(minutes=15) <= valid_from <= now+timedelta(days=7)
                    or not valid_from < valid_until <= valid_from+timedelta(hours=24) or valid_until <= now):
                raise AccessError('invalid_pass_window', 422)
            self.capacity(db, 'access_passes')
            visitor_id, pass_id = uuid.uuid4().hex, uuid.uuid4().hex
            payload = dict(base, visitor_id=visitor_id)
            db.execute('INSERT INTO access_visitors VALUES (?,?,?,?)', (visitor_id, name.strip(), mock_reference_count, stamp(now)))
            db.execute('INSERT INTO access_passes VALUES (?,?,?,?,?,?,?,?,?,?,NULL)',
                       (pass_id, request_id, digest(payload), visitor_id, stamp(now), stamp(valid_from), stamp(valid_until),
                        stamp(min(now+timedelta(minutes=5), valid_until)), 'pending', json.dumps(payload)))
            self.audit(db, 'pass', pass_id, 'pending', 'advance_owner_review_required')
            return self.row(db.execute('SELECT * FROM access_passes WHERE id=?', (pass_id,)).fetchone())

    def decide_pass(self, pass_id, provided_digest, confirmed, decision):
        with self.lock, self.store.connection() as db:
            self.expire(db)
            item = self.row(db.execute('SELECT * FROM access_passes WHERE id=?', (pass_id,)).fetchone())
            if not self.owner.verify(item['digest'], provided_digest, confirmed): self.reject(db, 'pass', pass_id, 'mock_owner_confirmation_required')
            self.expire(db)
            item = self.row(db.execute('SELECT * FROM access_passes WHERE id=?', (pass_id,)).fetchone())
            if decision not in {'approve', 'revoke', 'dismiss'}: raise AccessError('invalid_decision', 422)
            if item['status'] == 'executing': raise AccessError('pass_execution_in_progress')
            if item['status'] in {'used', 'uncertain', 'failed', 'expired', 'revoked', 'dismissed'}:
                if decision == 'approve': raise AccessError('pass_not_pending')
                return item
            if decision == 'approve':
                if item['status'] == 'approved': return item
                state, code = 'approved', 'advance_owner_approval_simulated'
            else:
                state, code = ('revoked', 'owner_revoked') if decision == 'revoke' else ('dismissed', 'owner_dismissed')
            result = self.outcome(state, code, owner_verification='simulation_mock', max_uses=1)
            if decision == 'approve':
                now = stamp(self.clock())
                changed = db.execute("UPDATE access_passes SET status=?,result=? WHERE id=? AND status='pending' AND approval_until>? AND valid_until>?", (state, json.dumps(result), pass_id, now, now)).rowcount
            else:
                changed = db.execute("UPDATE access_passes SET status=?,result=? WHERE id=? AND status IN ('pending','approved')", (state, json.dumps(result), pass_id)).rowcount
            if not changed: raise AccessError('pass_state_changed')
            self.audit(db, 'pass', pass_id, state, code)
            return self.row(db.execute('SELECT * FROM access_passes WHERE id=?', (pass_id,)).fetchone())

    def arrival(self, pass_id, request_id, face_status, face_visitor_id, second_factor):
        payload = {'pass_id': pass_id, 'face_status': face_status, 'face_visitor_id': face_visitor_id, 'second_factor': second_factor}
        hashed = digest(payload)
        with self.lock, self.store.connection() as db:
            self.expire(db)
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM access_arrivals WHERE id=?', (request_id,)).fetchone()
            if previous:
                if previous['digest'] != hashed: self.reject(db, 'arrival', pass_id, 'replay_payload_changed')
                return json.loads(previous['result'])
            self.capacity(db, 'access_arrivals')
            pending = self.outcome('executing', 'arrival_in_progress', pass_id=pass_id)
            reserved = db.execute('INSERT OR IGNORE INTO access_arrivals VALUES (?,?,?,?,?)',
                                 (request_id, pass_id, hashed, stamp(self.clock()), json.dumps(pending))).rowcount
            db.commit()
            if not reserved:
                previous = db.execute('SELECT * FROM access_arrivals WHERE id=?', (request_id,)).fetchone()
                if previous['digest'] != hashed: self.reject(db, 'arrival', pass_id, 'replay_payload_changed')
                return json.loads(previous['result'])
            try:
                item = self.row(db.execute('SELECT * FROM access_passes WHERE id=?', (pass_id,)).fetchone())
                if item['status'] != 'approved': raise AccessError('pass_not_active')
                if item['result'].get('owner_verification') != 'simulation_mock': raise AccessError('owner_authorization_missing')
                if stamp(self.clock()) < item['valid_from']: raise AccessError('pass_not_yet_valid')
                self.visitor.verify(item['visitor_id'], face_status, face_visitor_id, second_factor)
                if self.simulator.read_state()['simulated_state'] not in {'locked', 'unlocked'}: raise AccessError('lock_state_unknown')
                now = stamp(self.clock())
                changed = db.execute("UPDATE access_passes SET status='executing' WHERE id=? AND status='approved' AND valid_from<=? AND valid_until>?", (pass_id, now, now)).rowcount
                db.commit()
                if not changed:
                    self.expire(db)
                    raise AccessError('pass_not_active')
                result = self.apply('unlock', request_id)
                state = 'used' if result['status'] == 'simulated_completed' else 'uncertain'
                result = dict(result, pass_id=pass_id, arrival_owner_approval_required=False,
                              attempted_at=stamp(self.clock()), used_at=stamp(self.clock()) if state=='used' else None)
                changed = db.execute("UPDATE access_passes SET status=?,result=? WHERE id=? AND status='executing'", (state, json.dumps(result), pass_id)).rowcount
                if not changed: result = self.outcome('uncertain', 'pass_state_changed', pass_id=pass_id)
            except AccessError as error:
                result = self.outcome('denied', error.code, pass_id=pass_id)
            db.execute('UPDATE access_arrivals SET result=? WHERE id=?', (json.dumps(result), request_id))
            self.audit(db, 'arrival', pass_id, result['status'], result['code'])
            return result

    def register_named_guest(self, profile_id, visitor_id, pass_id, name, window=None):
        if not self._hex_id(profile_id) or not self._hex_id(visitor_id) or not self._hex_id(pass_id):
            raise AccessError('invalid_named_guest', 422)
        name = name.strip()
        if not name or len(name) > 60:
            raise AccessError('invalid_named_guest', 422)
        window_start = window_end = window_label = window_from = window_until = None
        window_daily = 0
        if window is not None:
            window_start, window_end, window_label = window
            if window_end <= window_start or not window_label:
                raise AccessError('invalid_guest_window', 422)
            if getattr(window, 'daily', False):
                window_daily = 1
                window_from, window_until = window.start_text, window.end_text
            window_start, window_end = stamp(window_start), stamp(window_end)
        with self.lock, self.store.connection() as db:
            db.execute('''INSERT INTO named_guest_grants
                (profile_id, visitor_id, pass_id, name, created_at, window_start, window_end, window_label,
                 window_from, window_until, window_daily)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                       (profile_id, visitor_id, pass_id, name, stamp(self.clock()),
                        window_start, window_end, window_label, window_from, window_until, window_daily))
        return {'profile_id': profile_id, 'visitor_id': visitor_id, 'pass_id': pass_id,
                'window': window_label, 'identity_proven': False, 'real_actuation': False}

    def set_guest_window(self, name, window):
        """Attach a visit window to one enrolled guest. This does not actuate a lock."""
        name = name.strip() if isinstance(name, str) else ''
        if not name or len(name) > 60:
            raise AccessError('invalid_named_guest', 422)
        start, end, label = window
        if end <= start or not label:
            raise AccessError('invalid_guest_window', 422)
        daily = 1 if getattr(window, 'daily', False) else 0
        window_from = window.start_text if daily else None
        window_until = window.end_text if daily else None
        with self.lock, self.store.connection() as db:
            rows = db.execute('SELECT profile_id FROM named_guest_grants WHERE name=?', (name,)).fetchall()
            if not rows:
                raise AccessError('guest_not_found', 404)
            if len(rows) != 1:
                raise AccessError('ambiguous_guest_name', 409)
            db.execute('''UPDATE named_guest_grants
                SET window_start=?, window_end=?, window_label=?, window_from=?, window_until=?, window_daily=?
                WHERE profile_id=?''',
                       (stamp(start), stamp(end), label, window_from, window_until, daily, rows[0]['profile_id']))
            self.audit(db, 'guest', rows[0]['profile_id'], 'window', 'guest_window_set')
        return {'name': name, 'window': label, 'identity_proven': False, 'real_actuation': False,
                'hardware_confirmed': False, 'simulation_only': True}

    def guest_windows(self):
        with self.lock, self.store.connection() as db:
            rows = db.execute('SELECT profile_id, window_label FROM named_guest_grants').fetchall()
        return {row['profile_id']: row['window_label'] for row in rows if row['window_label']}

    def _outside_window(self, profile_id, label):
        text = outside_window_text(label or 'scheduled window')
        result = self.outcome('uncertain', 'outside_allowed_window', text=text,
                              identity_proven=False, server_confirmed=False)
        result['device_label'] = device_label(result)
        with self.lock, self.store.connection() as db:
            self.audit(db, 'guest', profile_id, 'uncertain', 'outside_allowed_window')
        return result

    def _renew_daily_pass(self, profile_id, grant, now, start, end):
        """Issue today's simulated pass when the stored one belongs to another day."""
        with self.lock, self.store.connection() as db:
            row = db.execute('SELECT valid_from, valid_until FROM access_passes WHERE id=?',
                             (grant['pass_id'],)).fetchone()
        if row is not None:
            valid_from = datetime.fromisoformat(row['valid_from'])
            valid_until = datetime.fromisoformat(row['valid_until'])
            if start - timedelta(minutes=15) <= valid_from and valid_until <= end + timedelta(minutes=1):
                return grant
        from .named_guest import pass_bounds
        bounds_start, bounds_end = pass_bounds(now, (start, end, grant['window_label']))
        visitor = self.pass_request(uuid.uuid4().hex, grant['name'], 1, bounds_start, bounds_end)
        approved = self.decide_pass(visitor['id'], visitor['digest'], True, 'approve')
        with self.lock, self.store.connection() as db:
            db.execute('UPDATE named_guest_grants SET visitor_id=?, pass_id=? WHERE profile_id=?',
                       (approved['visitor_id'], approved['id'], profile_id))
        renewed = dict(grant)
        renewed['visitor_id'] = approved['visitor_id']
        renewed['pass_id'] = approved['id']
        return renewed

    def admit_named_guest(self, event_id, profile_id):
        """Simulate entry for one bridge-enrolled guest. Household profiles are ignored."""
        if not self._hex_id(event_id) or not self._hex_id(profile_id):
            return None
        with self.lock, self.store.connection() as db:
            grant = db.execute('''SELECT visitor_id, pass_id, name, window_start, window_end, window_label,
                window_from, window_until, window_daily
                FROM named_guest_grants WHERE profile_id=?''', (profile_id,)).fetchone()
        if grant is None:
            return None
        grant = dict(grant)
        if grant['window_daily'] and grant['window_from'] and grant['window_until']:
            now = self.clock()
            try:
                zone = ZoneInfo(self.guest_timezone)
            except Exception:
                zone = ZoneInfo('Asia/Kolkata')
            local = now.astimezone(zone)
            start = datetime.combine(local.date(), time.fromisoformat(grant['window_from']), zone)
            end = datetime.combine(local.date(), time.fromisoformat(grant['window_until']), zone)
            if not (start <= local < end):
                return self._outside_window(profile_id, grant['window_label'])
            try:
                grant = self._renew_daily_pass(profile_id, grant, now, start, end)
            except AccessError:
                pass
        elif grant['window_start'] and grant['window_end']:
            now = self.clock()
            start = datetime.fromisoformat(grant['window_start'])
            end = datetime.fromisoformat(grant['window_end'])
            if not (start <= now < end):
                return self._outside_window(profile_id, grant['window_label'])
        result = self.arrival(grant['pass_id'], event_id, 'probable_match', grant['visitor_id'], 'verified_mock')
        server_confirmed = (result.get('status') == 'simulated_completed'
                            and result.get('code') == 'simulation_only_confirmation'
                            and result.get('simulation_only') is True
                            and result.get('hardware_confirmed') is False
                            and result.get('real_actuation') is False)
        stored = dict(result, identity_proven=False, server_confirmed=server_confirmed)
        stored['device_label'] = device_label(stored)
        return stored

    def note_guest_sighting(self, event_id, visitor_id, face_status, similarity=None):
        """Record door evidence. A probable match does not prove identity or move the lock."""
        if not self._hex_id(event_id) or not self._hex_id(visitor_id):
            raise AccessError('invalid_guest_sighting', 422)
        if face_status not in {'probable_match', 'unknown', 'uncertain', 'no_face', 'low_quality', 'multiple_faces'}:
            raise AccessError('invalid_guest_sighting', 422)
        if isinstance(similarity, bool) or not isinstance(similarity, (int, float)) or not math.isfinite(similarity):
            similarity = None
        else:
            similarity = round(float(similarity), 3)
            if not 0 <= similarity <= 1:
                similarity = None
        payload = {'event_id': event_id, 'visitor_id': visitor_id, 'face_status': face_status,
                   'similarity': similarity, 'identity_proven': False, 'authorizes_entry': False,
                   'simulation_only': True}
        hashed = digest(payload)
        with self.lock, self.store.connection() as db:
            self.expire(db)
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT * FROM access_guest_approvals WHERE event_id=?', (event_id,)).fetchone()
            if existing:
                if existing['digest'] != hashed:
                    self.reject(db, 'guest', event_id, 'replay_payload_changed')
                return self.guest_view(self.row(existing))
            self.capacity(db, 'access_guest_approvals')
            approval_id = uuid.uuid4().hex
            pending = face_status == 'probable_match'
            status = 'pending_approval' if pending else 'denied'
            result = None if pending else json.dumps(self.outcome(
                'denied', 'visitor_face_not_accepted', identity_proven=False, server_confirmed=False))
            db.execute('INSERT INTO access_guest_approvals VALUES (?,?,?,?,?,?,?,?)',
                       (approval_id, event_id, hashed, visitor_id, stamp(self.clock()), status, json.dumps(payload), result))
            self.audit(db, 'guest', approval_id, status,
                       'guest_approval_required' if pending else 'visitor_face_not_accepted')
            return self.guest_view(self.row(db.execute('SELECT * FROM access_guest_approvals WHERE id=?', (approval_id,)).fetchone()))

    def decide_guest(self, approval_id, provided_digest, confirmed, decision, second_factor):
        """Owner gate for one door sighting. The lock runs only after this approval."""
        if decision not in {'approve', 'dismiss'}:
            raise AccessError('invalid_decision', 422)
        if second_factor not in {'missing', 'rejected', 'verified_mock'}:
            raise AccessError('invalid_decision', 422)
        pass_id = None
        payload = None
        with self.lock:
            with self.store.connection() as db:
                self.expire(db)
                item = self.guest_view(self.row(db.execute(
                    'SELECT * FROM access_guest_approvals WHERE id=?', (approval_id,)).fetchone()))
                if not self.owner.verify(item['digest'], provided_digest, confirmed):
                    self.reject(db, 'guest', approval_id, 'mock_owner_confirmation_required')
                item = self.guest_view(self.row(db.execute(
                    'SELECT * FROM access_guest_approvals WHERE id=?', (approval_id,)).fetchone()))
                if item['status'] != 'pending_approval':
                    return item
                if decision == 'dismiss':
                    result = self.outcome('dismissed', 'owner_dismissed', identity_proven=False, server_confirmed=False)
                    db.execute("UPDATE access_guest_approvals SET status='dismissed',result=? WHERE id=? AND status='pending_approval'",
                               (json.dumps(result), approval_id))
                    self.audit(db, 'guest', approval_id, 'dismissed', 'owner_dismissed')
                    return self.guest_view(self.row(db.execute(
                        'SELECT * FROM access_guest_approvals WHERE id=?', (approval_id,)).fetchone()))
                payload = item['payload']
                if payload.get('identity_proven') or payload.get('authorizes_entry') or payload.get('face_status') != 'probable_match':
                    result = self.outcome('denied', 'visitor_face_not_accepted', identity_proven=False, server_confirmed=False)
                    db.execute("UPDATE access_guest_approvals SET status='denied',result=? WHERE id=? AND status='pending_approval'",
                               (json.dumps(result), approval_id))
                    self.audit(db, 'guest', approval_id, 'denied', 'visitor_face_not_accepted')
                    return self.guest_view(self.row(db.execute(
                        'SELECT * FROM access_guest_approvals WHERE id=?', (approval_id,)).fetchone()))
                if second_factor != 'verified_mock':
                    self.audit(db, 'guest', approval_id, 'pending_approval', 'visitor_second_factor_required')
                    view = self.guest_view(self.row(db.execute(
                        'SELECT * FROM access_guest_approvals WHERE id=?', (approval_id,)).fetchone()))
                    view['attempt'] = {'code': 'visitor_second_factor_required', 'device_label': 'pending'}
                    view['device_label'] = 'pending'
                    return view
                passes = db.execute("SELECT id FROM access_passes WHERE visitor_id=? AND status='approved'",
                                    (payload['visitor_id'],)).fetchall()
                if len(passes) != 1:
                    code = 'ambiguous_guest_pass' if len(passes) > 1 else 'pass_not_active'
                    result = self.outcome('denied', code, identity_proven=False, server_confirmed=False)
                    db.execute("UPDATE access_guest_approvals SET status='denied',result=? WHERE id=? AND status='pending_approval'",
                               (json.dumps(result), approval_id))
                    self.audit(db, 'guest', approval_id, 'denied', code)
                    return self.guest_view(self.row(db.execute(
                        'SELECT * FROM access_guest_approvals WHERE id=?', (approval_id,)).fetchone()))
                pass_id = passes[0]['id']
            result = self.arrival(pass_id, uuid.uuid4().hex, payload['face_status'], payload['visitor_id'], second_factor)
            server_confirmed = (result.get('status') == 'simulated_completed'
                                and result.get('code') == 'simulation_only_confirmation'
                                and result.get('simulation_only') is True
                                and result.get('hardware_confirmed') is False
                                and result.get('real_actuation') is False)
            stored = dict(result, identity_proven=False, authorizes_entry=False,
                          server_confirmed=server_confirmed, guest_approval_confirmed=True)
            stored['device_label'] = device_label(stored)
            guest_status = 'approved' if server_confirmed else 'uncertain' if result.get('status') == 'uncertain' else 'denied'
            with self.store.connection() as db:
                changed = db.execute(
                    "UPDATE access_guest_approvals SET status=?,result=? WHERE id=? AND status='pending_approval'",
                    (guest_status, json.dumps(stored), approval_id)).rowcount
                if changed:
                    self.audit(db, 'guest', approval_id, guest_status, stored.get('code', 'guest_decision'))
                return self.guest_view(self.row(db.execute(
                    'SELECT * FROM access_guest_approvals WHERE id=?', (approval_id,)).fetchone()))

    def complete_simulated_pass(self, pass_id, request_id):
        """Consume one approved simulated pass without a face factor. Simulation only."""
        if not self._hex_id(pass_id) or not self._hex_id(request_id):
            raise AccessError('invalid_pin', 422)
        payload = {'pass_id': pass_id, 'factor': 'guest_pin'}
        hashed = digest(payload)
        with self.lock, self.store.connection() as db:
            self.expire(db)
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM access_arrivals WHERE id=?', (request_id,)).fetchone()
            if previous:
                if previous['digest'] != hashed:
                    self.reject(db, 'arrival', pass_id, 'replay_payload_changed')
                return json.loads(previous['result'])
            self.capacity(db, 'access_arrivals')
            pending = self.outcome('executing', 'arrival_in_progress', pass_id=pass_id)
            reserved = db.execute('INSERT OR IGNORE INTO access_arrivals VALUES (?,?,?,?,?)',
                                 (request_id, pass_id, hashed, stamp(self.clock()), json.dumps(pending))).rowcount
            db.commit()
            if not reserved:
                previous = db.execute('SELECT * FROM access_arrivals WHERE id=?', (request_id,)).fetchone()
                if previous['digest'] != hashed:
                    self.reject(db, 'arrival', pass_id, 'replay_payload_changed')
                return json.loads(previous['result'])
            try:
                item = self.row(db.execute('SELECT * FROM access_passes WHERE id=?', (pass_id,)).fetchone())
                if item['status'] != 'approved':
                    raise AccessError('pass_not_active')
                if item['result'].get('owner_verification') != 'simulation_mock':
                    raise AccessError('owner_authorization_missing')
                if stamp(self.clock()) < item['valid_from']:
                    raise AccessError('pass_not_yet_valid')
                if self.simulator.read_state()['simulated_state'] not in {'locked', 'unlocked'}:
                    raise AccessError('lock_state_unknown')
                now = stamp(self.clock())
                changed = db.execute("UPDATE access_passes SET status='executing' WHERE id=? AND status='approved' AND valid_from<=? AND valid_until>?",
                                    (pass_id, now, now)).rowcount
                db.commit()
                if not changed:
                    self.expire(db)
                    raise AccessError('pass_not_active')
                result = self.apply('unlock', request_id)
                state = 'used' if result['status'] == 'simulated_completed' else 'uncertain'
                result = dict(result, pass_id=pass_id, arrival_owner_approval_required=False,
                              attempted_at=stamp(self.clock()), used_at=stamp(self.clock()) if state == 'used' else None)
                changed = db.execute("UPDATE access_passes SET status=?,result=? WHERE id=? AND status='executing'",
                                    (state, json.dumps(result), pass_id)).rowcount
                if not changed:
                    result = self.outcome('uncertain', 'pass_state_changed', pass_id=pass_id)
            except AccessError as error:
                result = self.outcome('denied', error.code, pass_id=pass_id)
            db.execute('UPDATE access_arrivals SET result=? WHERE id=?', (json.dumps(result), request_id))
            self.audit(db, 'arrival', pass_id, result['status'], result['code'])
            return result

    @staticmethod
    def _pin_label(label):
        text = 'Guest' if label is None else label
        if not isinstance(text, str):
            raise AccessError('invalid_pin_label', 422)
        text = text.strip()
        if not text or len(text) > 60 or re.fullmatch(r'\d{6}', text):
            raise AccessError('invalid_pin_label', 422)
        return text

    @staticmethod
    def _pin_key(pin, salt):
        return hashlib.scrypt(pin.encode('ascii'), salt=salt, n=2**14, r=8, p=1, dklen=32)

    def _pin_locked(self, db):
        row = db.execute('SELECT failures, locked_until FROM guest_pin_attempts WHERE id=1').fetchone()
        if row is None:
            db.execute('INSERT INTO guest_pin_attempts (id, failures, locked_until) VALUES (1, 0, NULL)')
            return False
        until = row['locked_until']
        if until and until > stamp(self.clock()):
            return True
        if until:
            db.execute('UPDATE guest_pin_attempts SET failures=0, locked_until=NULL WHERE id=1')
        return False

    def _pin_fail(self, db):
        row = db.execute('SELECT failures FROM guest_pin_attempts WHERE id=1').fetchone()
        failures = (0 if row is None else int(row['failures'])) + 1
        locked_until = stamp(self.clock() + timedelta(minutes=15)) if failures >= 5 else None
        db.execute('UPDATE guest_pin_attempts SET failures=?, locked_until=? WHERE id=1', (failures, locked_until))
        return failures

    def _pin_view(self, code):
        result = self.outcome('denied', code, identity_proven=False, server_confirmed=False)
        result['device_label'] = device_label(result)
        return result

    def _match_pin(self, pin, rows):
        if pin is None:
            self._pin_key('000000', b'\x00' * 16)
            return None
        found = None
        for row in rows:
            digest = self._pin_key(pin, bytes.fromhex(row['salt']))
            if hmac.compare_digest(digest, bytes.fromhex(row['pin_hash'])) and found is None:
                found = row
        if not rows:
            self._pin_key(pin, b'\x00' * 16)
        return found

    def _reject_pin(self, code):
        with self.lock, self.store.connection() as db:
            if self._pin_locked(db):
                self.audit(db, 'pin', 'keypad', 'denied', 'pin_locked_out')
                return self._pin_view('pin_locked_out')
            self._pin_fail(db)
            self.audit(db, 'pin', 'keypad', 'denied', code)
        return self._pin_view(code)

    def issue_pin(self, minutes=60, label='Guest'):
        """Create one simulated pass and return a random PIN once. Only the salted hash is stored."""
        if type(minutes) is not int or not 1 <= minutes <= 24 * 60:
            raise AccessError('invalid_pin_window', 422)
        label = self._pin_label(label)
        pin = f'{secrets.randbelow(1_000_000):06d}'
        if pin in label:
            pin = f'{(int(pin) + 1) % 1_000_000:06d}'
        salt = secrets.token_bytes(16)
        now = self.clock()
        expires = now + timedelta(minutes=minutes)
        visitor = self.pass_request(uuid.uuid4().hex, label, 1, now, expires)
        try:
            approved = self.decide_pass(visitor['id'], visitor['digest'], True, 'approve')
            pin_id = uuid.uuid4().hex
            with self.lock, self.store.connection() as db:
                db.execute('''INSERT INTO guest_pins
                    (id, label, salt, pin_hash, created_at, expires_at, single_use, status, pass_id, visitor_id)
                    VALUES (?,?,?,?,?,?,1,'active',?,?)''',
                           (pin_id, label, salt.hex(), self._pin_key(pin, salt).hex(), stamp(now), stamp(expires),
                            approved['id'], approved['visitor_id']))
                self.audit(db, 'pin', pin_id, 'active', 'pin_issued')
        except Exception:
            try:
                self.decide_pass(visitor['id'], visitor['digest'], True, 'revoke')
            except Exception:
                pass
            raise
        return {'id': pin_id, 'pin': pin, 'label': label, 'expires_at': stamp(expires), 'single_use': True,
                'real_actuation': False, 'hardware_confirmed': False, 'server_confirmed': False,
                'simulation_only': True, 'identity_proven': False}

    def active_pins(self):
        return self.snapshot()['guest_pins']

    def revoke_pin(self, pin_id):
        if not self._hex_id(pin_id):
            raise AccessError('invalid_pin', 422)
        with self.lock, self.store.connection() as db:
            row = db.execute('SELECT label, expires_at, status, pass_id FROM guest_pins WHERE id=?', (pin_id,)).fetchone()
            if row is None:
                raise AccessError('pin_not_found', 404)
            label, expires_at, status, pass_id = row['label'], row['expires_at'], row['status'], row['pass_id']
            if status == 'active':
                db.execute("UPDATE guest_pins SET status='revoked' WHERE id=? AND status='active'", (pin_id,))
                self.audit(db, 'pin', pin_id, 'revoked', 'pin_revoked')
                status = 'revoked'
            stored = db.execute('SELECT * FROM access_passes WHERE id=?', (pass_id,)).fetchone()
        if stored is not None and stored['status'] in {'pending', 'approved'}:
            try:
                self.decide_pass(pass_id, self.row(stored)['digest'], True, 'revoke')
            except AccessError:
                pass
        return {'id': pin_id, 'label': label, 'expires_at': expires_at, 'status': status,
                'real_actuation': False, 'hardware_confirmed': False, 'simulation_only': True}

    def revoke_pin_by_label(self, label):
        """Revoke every active PIN with this label. The plaintext PIN is not read back."""
        label = self._pin_label(label)
        with self.lock, self.store.connection() as db:
            self.expire(db)
            ids = [row['id'] for row in db.execute(
                "SELECT id FROM guest_pins WHERE label=? AND status='active'", (label,))]
        if not ids:
            raise AccessError('pin_not_found', 404)
        return [self.revoke_pin(pin_id) for pin_id in ids]

    def revoke_named_guest(self, name):
        """Remove one named guest grant and its simulated pass. This does not actuate a lock."""
        name = name.strip() if isinstance(name, str) else ''
        if not name or len(name) > 60:
            raise AccessError('invalid_named_guest', 422)
        with self.lock, self.store.connection() as db:
            rows = db.execute(
                'SELECT profile_id, pass_id FROM named_guest_grants WHERE name=?', (name,)).fetchall()
            if not rows:
                raise AccessError('guest_not_found', 404)
            if len(rows) != 1:
                raise AccessError('ambiguous_guest_name', 409)
            profile_id, pass_id = rows[0]['profile_id'], rows[0]['pass_id']
            stored = db.execute('SELECT * FROM access_passes WHERE id=?', (pass_id,)).fetchone()
        if stored is not None and stored['status'] in {'pending', 'approved'}:
            try:
                self.decide_pass(pass_id, self.row(stored)['digest'], True, 'revoke')
            except AccessError:
                pass
        with self.lock, self.store.connection() as db:
            db.execute('DELETE FROM named_guest_grants WHERE profile_id=?', (profile_id,))
            self.audit(db, 'guest', profile_id, 'revoked', 'guest_grant_removed')
        self.store.delete_profile(profile_id)
        return {'name': name, 'revoked': True, 'identity_proven': False, 'real_actuation': False,
                'hardware_confirmed': False, 'simulation_only': True}

    def submit_pin(self, pin):
        """Check one keypad entry. DONE only after the existing simulated pass is confirmed."""
        offered = pin if isinstance(pin, str) and re.fullmatch(r'\d{6}', pin) else None
        with self.lock, self.store.connection() as db:
            self.expire(db)
            if self._pin_locked(db):
                self.audit(db, 'pin', 'keypad', 'denied', 'pin_locked_out')
                return self._pin_view('pin_locked_out')
            rows = [dict(row) for row in db.execute('SELECT * FROM guest_pins')]
        matched = self._match_pin(offered, rows)
        if matched is None:
            return self._reject_pin('pin_not_accepted')
        if matched['status'] == 'revoked':
            return self._reject_pin('pin_revoked')
        if matched['status'] == 'used':
            return self._reject_pin('pin_already_used')
        if matched['status'] != 'active' or matched['single_use'] != 1 or matched['expires_at'] <= stamp(self.clock()):
            return self._reject_pin('pin_expired')
        with self.lock, self.store.connection() as db:
            if self._pin_locked(db):
                self.audit(db, 'pin', 'keypad', 'denied', 'pin_locked_out')
                return self._pin_view('pin_locked_out')
            changed = db.execute(
                "UPDATE guest_pins SET status='used' WHERE id=? AND status='active' AND single_use=1 AND expires_at>?",
                (matched['id'], stamp(self.clock()))).rowcount
            if changed:
                self.audit(db, 'pin', matched['id'], 'used', 'pin_accepted')
        if not changed:
            return self._reject_pin('pin_not_accepted')
        result = self.complete_simulated_pass(matched['pass_id'], uuid.uuid4().hex)
        server_confirmed = (result.get('status') == 'simulated_completed'
                            and result.get('code') == 'simulation_only_confirmation'
                            and result.get('simulation_only') is True
                            and result.get('hardware_confirmed') is False
                            and result.get('real_actuation') is False)
        stored = dict(result, identity_proven=False, server_confirmed=server_confirmed)
        stored['device_label'] = device_label(stored)
        if server_confirmed:
            with self.lock, self.store.connection() as db:
                db.execute('UPDATE guest_pin_attempts SET failures=0, locked_until=NULL WHERE id=1')
        elif stored.get('device_label') == 'DONE':
            stored['device_label'] = 'pending'
            stored['server_confirmed'] = False
        return stored
