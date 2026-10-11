from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import json
import re
from pathlib import Path
import shutil
import sqlite3
import uuid

from .config import DEFAULT_SETTINGS
from .observations import local_description


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Store:
    def __init__(self, directory: Path):
        self.directory = directory
        self.media = directory / "events"
        self.faces = directory / "faces"
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.media.mkdir(exist_ok=True, mode=0o700)
        self.faces.mkdir(exist_ok=True, mode=0o700)
        self.path = directory / "doorbell.sqlite3"
        with self.connection() as db:
            db.executescript("""
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL, kind TEXT NOT NULL,
                source TEXT NOT NULL, capture_status TEXT NOT NULL DEFAULT 'pending',
                camera_error TEXT, frames INTEGER NOT NULL DEFAULT 0,
                person_status TEXT NOT NULL DEFAULT 'unavailable', person_id TEXT,
                detector TEXT, motion_score REAL, note TEXT NOT NULL DEFAULT '',
                acknowledged INTEGER NOT NULL DEFAULT 0, simulated INTEGER NOT NULL DEFAULT 0
              );
              CREATE INDEX IF NOT EXISTS event_time ON events(occurred_at DESC);
              CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, name TEXT NOT NULL,
                note TEXT NOT NULL, created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, event_id TEXT,
                text TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending');
              CREATE TABLE IF NOT EXISTS face_samples(id TEXT PRIMARY KEY, profile_id TEXT NOT NULL,
                embedding TEXT NOT NULL, model TEXT NOT NULL, created_at TEXT NOT NULL);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(events)")}
            for column, kind in {"face_status": "TEXT", "face_similarity": "REAL", "local_analysis": "TEXT",
                                 "description": "TEXT", "description_status": "TEXT NOT NULL DEFAULT 'pending'",
                                 "caption": "TEXT", "caption_status": "TEXT NOT NULL DEFAULT 'not_configured'",
                                 "caption_error": "TEXT", "caption_model": "TEXT", "caption_latency_ms": "REAL"}.items():
                if column not in columns:
                    db.execute(f"ALTER TABLE events ADD COLUMN {column} {kind}")
        self.path.chmod(0o600)
        with self.connection() as db:
            db.execute("UPDATE events SET capture_status='interrupted', camera_error='Capture interrupted by restart' WHERE capture_status='pending'")
            db.execute("UPDATE events SET caption_status='interrupted',caption_error='Caption interrupted by restart; local observations remain available.' WHERE caption_status IN ('queued','running')")
            self.refresh_descriptions(db)

    @staticmethod
    def refresh_descriptions(db, event_id=None):
        query = "SELECT e.*, p.name AS person_name FROM events e LEFT JOIN profiles p ON e.person_id=p.id" + (
            " WHERE e.id=?" if event_id else "")
        for row in db.execute(query, (event_id,) if event_id else ()):
            event = dict(row)
            event["local_analysis"] = json.loads(event["local_analysis"]) if event["local_analysis"] else None
            text, status = local_description(event)
            if text != event["description"] or status != event["description_status"]:
                db.execute("UPDATE events SET description=?,description_status=? WHERE id=?", (text, status, event["id"]))

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def settings(self):
        result = json.loads(json.dumps(DEFAULT_SETTINGS))
        with self.connection() as db:
            for row in db.execute("SELECT key,value FROM settings"):
                if row["key"] in result:
                    result[row["key"]] = json.loads(row["value"])
        return result

    def save_settings(self, values):
        with self.connection() as db:
            for key, value in values.items():
                if key in DEFAULT_SETTINGS:
                    db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))
        return self.settings()

    @staticmethod
    def event_dict(row):
        if row is None:
            return None
        item = dict(row)
        item["acknowledged"] = bool(item["acknowledged"])
        item["simulated"] = bool(item["simulated"])
        item["media"] = [f'/api/events/{item["id"]}/frames/{index}' for index in range(item["frames"])]
        item["description_source"] = "local_detections"
        item["local_analysis"] = json.loads(item["local_analysis"]) if item.get("local_analysis") else None
        return item

    def add_event(self, kind, source, simulated=False, motion_score=None):
        event_id = uuid.uuid4().hex
        with self.connection() as db:
            db.execute("INSERT INTO events(id,occurred_at,kind,source,simulated,motion_score) VALUES (?,?,?,?,?,?)",
                       (event_id, now(), kind, source, int(simulated), motion_score))
        return self.event(event_id)

    def event(self, event_id):
        with self.connection() as db:
            row = db.execute("SELECT e.*, p.name AS person_name FROM events e LEFT JOIN profiles p ON e.person_id=p.id WHERE e.id=?", (event_id,)).fetchone()
        return self.event_dict(row)

    def events(self, limit=50, offset=0, kind=None):
        where, args = ("WHERE e.kind=?", [kind]) if kind else ("", [])
        with self.connection() as db:
            rows = db.execute(f"SELECT e.*,p.name AS person_name FROM events e LEFT JOIN profiles p ON e.person_id=p.id {where} ORDER BY e.occurred_at DESC LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
        return [self.event_dict(row) for row in rows]

    def update_event(self, event_id, **values):
        allowed = {"capture_status", "camera_error", "frames", "person_status", "person_id", "detector", "note", "acknowledged", "face_status", "face_similarity", "local_analysis",
                   "caption", "caption_status", "caption_error", "caption_model", "caption_latency_ms"}
        values = {k: v for k, v in values.items() if k in allowed}
        if values:
            with self.connection() as db:
                db.execute("UPDATE events SET " + ",".join(f"{k}=?" for k in values) + " WHERE id=?", [*values.values(), event_id])
                self.refresh_descriptions(db, event_id)
        return self.event(event_id)

    def summary(self, since):
        with self.connection() as db:
            counts = dict(db.execute("""SELECT COUNT(*) AS total,
                COALESCE(SUM(kind != 'motion'),0) AS rings,
                COALESCE(SUM(kind = 'motion'),0) AS motion
                FROM events WHERE occurred_at >= ?""", (since,)).fetchone())
            counts["unread"] = db.execute("SELECT COUNT(*) FROM events WHERE acknowledged=0").fetchone()[0]
        return counts

    def events_between(self, start, end):
        with self.connection() as db:
            rows = db.execute("""SELECT e.*, p.name AS person_name FROM events e
                LEFT JOIN profiles p ON e.person_id=p.id
                WHERE e.occurred_at>=? AND e.occurred_at<=? ORDER BY e.occurred_at ASC LIMIT 500""",
                              (start, end)).fetchall()
        return [self.event_dict(row) for row in rows]

    def private_setting(self, key):
        if key in DEFAULT_SETTINGS:
            return None
        with self.connection() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    def save_private_setting(self, key, value):
        if key in DEFAULT_SETTINGS or not isinstance(key, str) or len(key) > 80:
            raise ValueError("Private setting key is reserved")
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))
        return value

    def delete_event(self, event_id):
        if not self.event(event_id):
            return False
        with self.connection() as db:
            db.execute("DELETE FROM proposals WHERE event_id=?", (event_id,))
            db.execute("DELETE FROM events WHERE id=?", (event_id,))
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='bot_deliveries'").fetchone():
                db.execute("DELETE FROM bot_deliveries WHERE event_id IN (?,?,?,?,?)",
                           (event_id, event_id+":visit", event_id+":analysis", event_id+":caption", event_id+":motion"))
        shutil.rmtree(self.media / event_id, ignore_errors=True)
        return True

    def cleanup(self, retention_days=7, max_events=500):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        with self.connection() as db:
            expired = [r[0] for r in db.execute("SELECT id FROM events WHERE occurred_at < ? AND capture_status != 'pending'", (cutoff,))]
            overflow = [r[0] for r in db.execute("SELECT id FROM events WHERE capture_status != 'pending' ORDER BY occurred_at DESC LIMIT -1 OFFSET ?", (max_events,))]
        for event_id in set(expired + overflow):
            self.delete_event(event_id)

    def profiles(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT p.*, (SELECT COUNT(*) FROM face_samples f WHERE f.profile_id=p.id) AS face_samples FROM profiles p ORDER BY name COLLATE NOCASE")]

    def add_profile(self, name, note):
        item = {"id": uuid.uuid4().hex, "name": name, "note": note, "created_at": now()}
        with self.connection() as db:
            db.execute("INSERT INTO profiles VALUES (:id,:name,:note,:created_at)", item)
        return item

    def delete_profile(self, profile_id):
        with self.connection() as db:
            if not db.execute("SELECT 1 FROM profiles WHERE id=?", (profile_id,)).fetchone():
                return False
        self.delete_face_samples(profile_id)
        with self.connection() as db:
            db.execute("UPDATE events SET person_id=NULL, person_status='unknown' WHERE person_id=?", (profile_id,))
            self.refresh_descriptions(db)
            return bool(db.execute("DELETE FROM profiles WHERE id=?", (profile_id,)).rowcount)

    def update_profile(self, profile_id, name, note):
        with self.connection() as db:
            if not db.execute("UPDATE profiles SET name=?,note=? WHERE id=?", (name, note, profile_id)).rowcount:
                return None
        return next((profile for profile in self.profiles() if profile["id"] == profile_id), None)

    def face_samples(self):
        with self.connection() as db:
            rows = db.execute("SELECT profile_id,embedding,model FROM face_samples").fetchall()
        return [{**dict(row), "embedding": json.loads(row["embedding"])} for row in rows]

    def add_face_sample(self, profile_id, jpeg, vector, model):
        with self.connection() as db:
            if not db.execute("SELECT 1 FROM profiles WHERE id=?", (profile_id,)).fetchone():
                raise ValueError("Profile no longer exists")
            if db.execute("SELECT COUNT(*) FROM face_samples WHERE profile_id=?", (profile_id,)).fetchone()[0] >= 5 or db.execute("SELECT COUNT(*) FROM face_samples").fetchone()[0] >= 100:
                raise ValueError("Enrollment limit reached: five photos per person, 100 total")
            sample_id = uuid.uuid4().hex
            folder = self.faces / profile_id
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = folder / (sample_id + ".jpg")
            path.write_bytes(jpeg); path.chmod(0o600)
            db.execute("INSERT INTO face_samples VALUES (?,?,?,?,?)", (sample_id, profile_id, json.dumps(vector), model, now()))
        return sample_id

    def delete_face_samples(self, profile_id):
        if not re.fullmatch(r"[0-9a-f]{32}", profile_id):
            return  # Filesystem paths use only locally generated profile IDs.
        with self.connection() as db:
            db.execute("DELETE FROM face_samples WHERE profile_id=?", (profile_id,))
            db.execute("UPDATE events SET person_id=NULL,person_status='unknown',face_status='enrollment_deleted' WHERE person_id=? AND person_status='probable_match'", (profile_id,))
            for row in db.execute("SELECT id,local_analysis FROM events WHERE local_analysis IS NOT NULL").fetchall():
                analysis = json.loads(row["local_analysis"])
                changed = analysis.get("profile_id") == profile_id
                for frame in analysis.get("frames", []):
                    if frame.get("profile_id") == profile_id:
                        frame.update(status="enrollment_deleted", quality="enrollment_deleted", profile_id=None,
                                     similarity=None, margin=None)
                        changed = True
                if changed:
                    analysis.update(status="enrollment_deleted", profile_id=None, similarity=None, margin=None)
                    if analysis.get("aggregation"):
                        analysis["aggregation"]["reason"] = "enrollment_deleted"
                        analysis["aggregation"]["probable_frames"] = sum(
                            frame.get("status") == "probable_match" for frame in analysis.get("frames", []))
                    db.execute("UPDATE events SET local_analysis=?,face_status='enrollment_deleted',face_similarity=NULL WHERE id=?",
                               (json.dumps(analysis), row["id"]))
            self.refresh_descriptions(db)
        shutil.rmtree(self.faces / profile_id, ignore_errors=True)

    def proposal(self, proposal_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        return dict(row) if row else None

    def proposals(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM proposals ORDER BY created_at DESC LIMIT 30")]

    def add_proposal(self, text, source, event_id):
        item = {"id": uuid.uuid4().hex, "text": text, "source": source,
                "event_id": event_id, "created_at": now(), "status": "pending"}
        with self.connection() as db:
            db.execute("INSERT INTO proposals VALUES (:id,:event_id,:text,:source,:created_at,:status)", item)
        return item

    def decide_proposal(self, proposal_id, status):
        with self.connection() as db:
            db.execute("UPDATE proposals SET status=? WHERE id=? AND status='pending'", (status, proposal_id))
        return self.proposal(proposal_id)
