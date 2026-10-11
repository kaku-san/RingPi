"""Reviewed temporary reference imports over an opt-in SSH/loopback boundary.

No cloud attachment API, public callback, permanent enrollment or lock adapter.
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import threading
from typing import Literal

from PIL import Image
from pydantic import AwareDatetime, BaseModel, Field, ValidationError

from .access_simulation import AccessError, digest, stamp
from .local_vision import clean_image, FaceError, FACE_MODEL, MODELS

MAX_BODY = 10_100_000
MAX_PHOTO = 1_500_000


class ReferenceInput(BaseModel):
    model_config = {'extra': 'forbid'}
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    data_base64: str = Field(max_length=2_000_000)


class ImportInput(BaseModel):
    model_config = {'extra': 'forbid'}
    schema_version: Literal[1]
    request_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    visitor_name: str = Field(min_length=1, max_length=60)
    purpose: Literal['temporary_visitor_pass']
    valid_from: AwareDatetime
    valid_until: AwareDatetime
    references: list[ReferenceInput] = Field(min_length=5, max_length=5)


class ImportDecision(BaseModel):
    model_config = {'extra': 'forbid'}
    payload_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    decision: Literal['approve', 'dismiss', 'revoke']


def parse(data, model):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('duplicate key')
            result[key] = value
        return result
    try:
        return model.model_validate(json.loads(data, object_pairs_hook=unique))
    except (ValueError, ValidationError, TypeError):
        # Never echo image bytes, arbitrary tool text or credential headers.
        raise AccessError('invalid_enrollment_bundle', 422) from None


def fixture_references():
    """Five owned generated images, deliberately containing no people/faces."""
    photos = []
    for index in range(5):
        image = Image.new('RGB', (96, 96), (40 + index * 30, 80, 150))
        image.paste((180, 160, 40 + index * 20), (12 + index * 4, 12, 72, 72))
        stream = BytesIO(); image.save(stream, 'JPEG', quality=90)
        raw = stream.getvalue()
        photos.append({'sha256': hashlib.sha256(raw).hexdigest(),
                       'data_base64': base64.b64encode(raw).decode()})
    return photos


def signature(key, method, path, timestamp, nonce, body):
    message = '\n'.join((method, path, timestamp, nonce, hashlib.sha256(body).hexdigest()))
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


class BridgeAuth:
    """Separate proposal and owner scopes; keys stay in an owner-only Pi file."""
    def __init__(self, store, clock, keys=None):
        self.store, self.clock, self.keys = store, clock, keys or {}
        self.issue = None
        self.restricted = None

    @classmethod
    def from_file(cls, store, clock, path):
        auth = cls(store, clock)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd) as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise ValueError('unsafe policy')
                raw = stream.read(8193)
                if len(raw) > 8192: raise ValueError('oversized policy')
                policy = json.loads(raw)
            if not isinstance(policy, dict): raise ValueError('invalid policy')
            if policy.get('enabled') is not True: return auth
            if set(policy) == {'enabled', 'owner_key', 'proposer_uid', 'proposer_gid', 'proposal_socket'}:
                uid, gid, path = policy['proposer_uid'], policy['proposer_gid'], policy['proposal_socket']
                key = policy['owner_key']
                if (type(uid) is not int or not 0 < uid < 2**31 or uid == os.getuid()
                        or type(gid) is not int or not 0 < gid < 2**31 or gid == os.getgid()
                        or not isinstance(path, str) or not path.startswith('/') or '..' in Path(path).parts
                        or len(path.encode()) > 100 or not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{64}', key)):
                    raise ValueError('invalid restricted policy')
                auth.keys = {'owner': key}
                auth.restricted = {'uid': uid, 'gid': gid, 'socket': path}
                return auth
            keys = {scope: policy.get(scope + '_key') for scope in ('proposal', 'owner')}
            if (set(policy) != {'enabled', 'proposal_key', 'owner_key'}
                    or any(not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{64}', key) for key in keys.values())
                    or keys['proposal'] == keys['owner']):
                raise ValueError('invalid policy')
            auth.keys = keys
        except FileNotFoundError:
            pass
        except (ValueError, OSError, TypeError):
            auth.issue = 'Private bridge policy unavailable or invalid; ingress disabled'
        return auth

    def verify(self, scope, method, path, headers, body, client_host):
        if scope not in self.keys: raise AccessError('enrollment_bridge_disabled', 503)
        if client_host not in {'127.0.0.1', '::1'}: raise AccessError('bridge_requires_ssh_loopback', 403)
        timestamp = headers.get('x-doorbell-time', '')
        nonce = headers.get('x-doorbell-nonce', '')
        supplied = headers.get('x-doorbell-signature', '')
        if (not re.fullmatch(r'\d{10}', timestamp) or not re.fullmatch(r'[0-9a-f]{32}', nonce)
                or not re.fullmatch(r'[0-9a-f]{64}', supplied)
                or abs(int(self.clock().timestamp()) - int(timestamp)) > 60):
            raise AccessError('bridge_authentication_required', 403)
        expected = signature(self.keys[scope], method, path, timestamp, nonce, body)
        if not hmac.compare_digest(expected, supplied): raise AccessError('bridge_authentication_required', 403)
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM enrollment_nonces WHERE expires_at<=?', (stamp(self.clock()),))
            if db.execute('SELECT COUNT(*) FROM enrollment_nonces').fetchone()[0] >= 1000:
                raise AccessError('bridge_request_limit')
            try:
                db.execute('INSERT INTO enrollment_nonces VALUES (?,?)',
                           (scope + ':' + nonce, stamp(self.clock() + timedelta(seconds=121))))
            except Exception as error:
                import sqlite3
                if isinstance(error, sqlite3.IntegrityError): raise AccessError('bridge_request_replayed') from None
                raise


class EnrollmentBridge:
    def __init__(self, store, access, vision, policy_path, clock=None):
        self.store, self.access, self.vision = store, access, vision
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.RLock()
        self.cleanup_issue = None
        self.directory = store.directory / 'temporary-references'
        with store.connection() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS enrollment_imports(id TEXT PRIMARY KEY, digest TEXT NOT NULL,
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL, status TEXT NOT NULL,
                fixture INTEGER NOT NULL, payload TEXT NOT NULL, result TEXT);
              CREATE TABLE IF NOT EXISTS enrollment_reference_samples(proposal_id TEXT NOT NULL,
                reference_index INTEGER NOT NULL, embedding TEXT NOT NULL, model TEXT NOT NULL,
                PRIMARY KEY(proposal_id,reference_index));
              CREATE TABLE IF NOT EXISTS enrollment_nonces(id TEXT PRIMARY KEY, expires_at TEXT NOT NULL);
            ''')
            interrupted = [row['id'] for row in db.execute("SELECT id FROM enrollment_imports WHERE status='reviewing'")]
            db.execute("UPDATE enrollment_imports SET status='interrupted',result=? WHERE status='reviewing'",
                       (json.dumps({'code': 'review_interrupted', 'real_actuation': False}),))
        self.auth = BridgeAuth.from_file(store, self.clock, policy_path)
        for identifier in interrupted:
            self.revoke_pass(identifier)
            self.erase_references(identifier)

    def guest_reference_samples(self):
        """Active temporary embeddings for door comparison. This does not actuate a lock."""
        now = stamp(self.clock())
        with self.store.connection() as db:
            rows = db.execute('''SELECT s.embedding, s.model, p.visitor_id
                FROM enrollment_reference_samples s
                JOIN enrollment_imports i ON i.id = s.proposal_id
                JOIN access_passes p ON p.request_id = i.id
                WHERE i.status='approved' AND p.status='approved' AND p.valid_from<=? AND p.valid_until>?''',
                (now, now)).fetchall()
        samples = []
        for row in rows:
            if row['model'] == 'owned_generated_fixture':
                continue
            try:
                embedding = json.loads(row['embedding'])
            except json.JSONDecodeError:
                continue
            if not isinstance(embedding, list) or not embedding:
                continue
            samples.append({'kind': 'temporary_guest', 'visitor_id': row['visitor_id'],
                            'embedding': embedding, 'model': row['model']})
        return samples

    def status(self):
        return {'enabled': bool(self.auth.keys), 'transport': 'restricted_ssh_unix_peer' if self.auth.restricted else 'owner_ssh_to_pi_loopback',
                'public_callback': False, 'scopes': ['proposal', 'owner'],
                'restricted_proposer_configured': bool(self.auth.restricted),
                'photos_per_proposal': 5, 'purpose': 'temporary_visitor_pass',
                'permanent_enrollment': False, 'real_actuation': False,
                'policy_issue': self.auth.issue, 'cleanup_issue': self.cleanup_issue,
                'cloud_attachment_export': 'requires_permitted_local_files'}

    @staticmethod
    def row(row):
        if row is None: raise AccessError('enrollment_proposal_not_found', 404)
        item = dict(row)
        item['payload'] = json.loads(item['payload'])
        item['result'] = json.loads(item['result']) if item.get('result') else None
        item['fixture'] = bool(item['fixture'])
        item['real_actuation'] = False
        return item

    def revoke_pass(self, identifier):
        with self.store.connection() as db:
            row = db.execute('SELECT id,digest,status FROM access_passes WHERE request_id=?', (identifier,)).fetchone()
        if row and row['status'] in {'pending', 'approved'}:
            self.access.decide_pass(row['id'], row['digest'], True, 'revoke')

    def erase_references(self, identifier):
        if not re.fullmatch(r'[0-9a-f]{32}', identifier): raise AccessError('invalid_proposal_id', 422)
        if self.directory.exists() or self.directory.is_symlink(): self.check_directory()
        with self.store.connection() as db:
            db.execute('DELETE FROM enrollment_reference_samples WHERE proposal_id=?', (identifier,))
        folder = self.directory / identifier
        if folder.is_symlink(): folder.unlink()
        elif folder.is_dir(): shutil.rmtree(folder)

    def check_directory(self):
        info = self.directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise AccessError('private_reference_directory_invalid')

    def cleanup(self):
        with self.lock, self.store.connection() as db:
            now = stamp(self.clock())
            rows = db.execute("SELECT i.id,i.status,p.status AS pass_status FROM enrollment_imports i LEFT JOIN access_passes p ON p.request_id=i.id WHERE (i.status='pending' AND i.expires_at<=?) OR (i.status='approved' AND (json_extract(i.payload,'$.valid_until')<=? OR p.status IN ('used','revoked','expired','uncertain','failed','dismissed')))", (now, now)).fetchall()
            for row in rows:
                state = 'consumed' if row['pass_status']=='used' else 'revoked' if row['pass_status']=='revoked' else 'expired'
                db.execute('UPDATE enrollment_imports SET status=? WHERE id=?', (state, row['id']))
            db.commit()
            # Retry terminal cleanup after an I/O failure instead of losing the
            # reference path once its proposal is no longer pending/approved.
            terminal = db.execute("SELECT id FROM enrollment_imports WHERE status IN ('consumed','revoked','dismissed','expired','failed','interrupted')").fetchall()
            for row in terminal:
                self.revoke_pass(row['id'])
                self.erase_references(row['id'])

    def proposal(self, identifier, fixture_only=False):
        self.cleanup()
        with self.store.connection() as db:
            item = self.row(db.execute('SELECT * FROM enrollment_imports WHERE id=?', (identifier,)).fetchone())
        if fixture_only and not item['fixture']: raise AccessError('enrollment_proposal_not_found', 404)
        return item

    def fixtures(self):
        self.cleanup()
        with self.store.connection() as db:
            return [self.row(row) for row in db.execute('SELECT * FROM enrollment_imports WHERE fixture=1 ORDER BY created_at DESC LIMIT 30')]

    def ingest(self, body, fixture=False):
        if len(body) > MAX_BODY: raise AccessError('enrollment_bundle_too_large', 413)
        envelope = parse(body, ImportInput)
        now = self.clock()
        name = envelope.visitor_name.strip()
        if not name: raise AccessError('invalid_visitor_name', 422)
        decoded, references = [], []
        owned_fixtures = {row['sha256'] for row in fixture_references()} if fixture else None
        for index, reference in enumerate(envelope.references):
            try: raw = base64.b64decode(reference.data_base64, validate=True)
            except ValueError: raise AccessError('invalid_reference_encoding', 422) from None
            if (not raw or len(raw) > MAX_PHOTO or base64.b64encode(raw).decode() != reference.data_base64
                    or hashlib.sha256(raw).hexdigest() != reference.sha256):
                raise AccessError('reference_digest_or_size_invalid', 422)
            if fixture and reference.sha256 not in owned_fixtures:
                raise AccessError('simulation_requires_owned_generated_images', 422)
            try: jpeg = clean_image(raw)
            except FaceError: raise AccessError('unsupported_reference_photo', 422) from None
            if len(jpeg) > MAX_PHOTO: raise AccessError('reference_digest_or_size_invalid', 422)
            decoded.append(jpeg)
            references.append({'index': index, 'source_sha256': reference.sha256,
                               'jpeg_sha256': hashlib.sha256(jpeg).hexdigest(), 'bytes': len(jpeg)})
        if len({row['jpeg_sha256'] for row in references}) != 5:
            raise AccessError('five_distinct_references_required', 422)
        payload = {'visitor_name': name, 'purpose': envelope.purpose, 'references': references,
                   'valid_from': stamp(envelope.valid_from), 'valid_until': stamp(envelope.valid_until),
                   'action': 'unlock', 'max_uses': 1, 'fixture': fixture, 'real_actuation': False}
        hashed = digest(payload)
        with self.lock, self.store.connection() as db:
            self.cleanup()
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT * FROM enrollment_imports WHERE id=?', (envelope.request_id,)).fetchone()
            if existing:
                if existing['digest'] != hashed: raise AccessError('replay_payload_changed')
                return self.row(existing)
            if (not now - timedelta(minutes=15) <= envelope.valid_from <= now + timedelta(days=7)
                    or not envelope.valid_from < envelope.valid_until <= envelope.valid_from + timedelta(hours=24)
                    or envelope.valid_until <= now):
                raise AccessError('invalid_pass_window', 422)
            if (db.execute('SELECT COUNT(*) FROM enrollment_imports').fetchone()[0] >= 100
                    or db.execute("SELECT COUNT(*) FROM enrollment_imports WHERE status='pending'").fetchone()[0] >= 10):
                raise AccessError('enrollment_proposal_limit')
            self.directory.mkdir(exist_ok=True, mode=0o700)
            self.check_directory()
            folder = self.directory / envelope.request_id
            if folder.exists(): raise AccessError('reference_directory_already_exists')
            folder.mkdir(mode=0o700)
            try:
                for index, jpeg in enumerate(decoded):
                    fd = os.open(folder / f'{index}.jpg', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    with os.fdopen(fd, 'wb') as stream: stream.write(jpeg)
                db.execute('INSERT INTO enrollment_imports VALUES (?,?,?,?,?,?,?,NULL)',
                           (envelope.request_id, hashed, stamp(now), stamp(min(now+timedelta(minutes=5), envelope.valid_until)),
                            'pending', int(fixture), json.dumps(payload)))
            except Exception:
                shutil.rmtree(folder)
                raise
            return self.row(db.execute('SELECT * FROM enrollment_imports WHERE id=?', (envelope.request_id,)).fetchone())

    def decide(self, identifier, body, fixture_only=False):
        decision = parse(body, ImportDecision)
        with self.lock:
            item = self.proposal(identifier, fixture_only)
            if not hmac.compare_digest(item['digest'], decision.payload_digest): raise AccessError('review_digest_changed')
            if item['status'] == 'reviewing': raise AccessError('enrollment_review_in_progress')
            if item['status'] == 'approved' and decision.decision == 'approve': return item
            if item['status'] not in {'pending', 'approved'}: raise AccessError('enrollment_proposal_not_active')
            if decision.decision != 'approve':
                with self.store.connection() as db:
                    db.execute('UPDATE enrollment_imports SET status=? WHERE id=?',
                               ('revoked' if decision.decision == 'revoke' else 'dismissed', identifier))
                self.revoke_pass(identifier)
                self.erase_references(identifier)
                return self.proposal(identifier, fixture_only)
            with self.store.connection() as db:
                claimed = db.execute("UPDATE enrollment_imports SET status='reviewing' WHERE id=? AND status='pending' AND expires_at>?", (identifier, stamp(self.clock()))).rowcount
            if not claimed: raise AccessError('enrollment_proposal_not_active')
            try:
                samples = []
                self.check_directory()
                if (self.directory / identifier).is_symlink(): raise AccessError('private_reference_directory_invalid')
                for reference in item['payload']['references']:
                    fd = os.open(self.directory / identifier / f"{reference['index']}.jpg", os.O_RDONLY | os.O_NOFOLLOW)
                    with os.fdopen(fd, 'rb') as stream: raw = stream.read(MAX_PHOTO + 1)
                    if hashlib.sha256(raw).hexdigest() != reference['jpeg_sha256']:
                        raise AccessError('reference_changed_after_review')
                    if item['fixture']:
                        samples.append((reference['index'], [], 'owned_generated_fixture'))
                    else:
                        try: _, vector, _ = self.vision.enroll(raw)
                        except FaceError: raise AccessError('reference_face_not_usable', 422) from None
                        if not 2 <= len(vector) <= 2048 or not all(math.isfinite(value) for value in vector):
                            raise AccessError('reference_face_not_usable', 422)
                        samples.append((reference['index'], vector, MODELS[FACE_MODEL][1]))
                if stamp(self.clock()) >= item['expires_at']: raise AccessError('enrollment_review_expired')
                window = item['payload']
                visitor_pass = self.access.pass_request(identifier, window['visitor_name'], 5,
                    datetime.fromisoformat(window['valid_from']), datetime.fromisoformat(window['valid_until']))
                visitor_pass = self.access.decide_pass(visitor_pass['id'], visitor_pass['digest'], True, 'approve')
                result = {'code': 'temporary_references_approved', 'pass_id': visitor_pass['id'],
                          'visitor_id': visitor_pass['visitor_id'], 'reference_count': 5,
                          'enrollment': 'owned_generated_fixture' if item['fixture'] else 'local_temporary_references',
                          'pass_simulation_only': True, 'real_actuation': False,
                          'arrival_owner_approval_required': False}
                with self.store.connection() as db:
                    db.execute('BEGIN IMMEDIATE')
                    for index, vector, model in samples:
                        db.execute('INSERT INTO enrollment_reference_samples VALUES (?,?,?,?)',
                                   (identifier, index, json.dumps(vector), model))
                    db.execute("UPDATE enrollment_imports SET status='approved',result=? WHERE id=? AND status='reviewing'", (json.dumps(result), identifier))
                return self.proposal(identifier, fixture_only)
            except Exception as error:
                safe_codes = {'reference_changed_after_review', 'private_reference_directory_invalid',
                              'reference_face_not_usable', 'enrollment_review_expired', 'invalid_pass_window',
                              'simulation_history_limit', 'pass_not_pending', 'pass_not_active'}
                safe_error = isinstance(error, AccessError) and error.code in safe_codes
                code = error.code if safe_error else 'temporary_enrollment_failed'
                with self.store.connection() as db:
                    db.execute("UPDATE enrollment_imports SET status='failed',result=? WHERE id=?", (json.dumps({'code': code, 'real_actuation': False}), identifier))
                self.revoke_pass(identifier)
                self.erase_references(identifier)
                if safe_error: raise
                raise AccessError('temporary_enrollment_failed') from None
