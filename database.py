"""
database.py — SQLite data layer for HirePilot (multi-user).

Every method that reads or writes user data requires user_id.
No query returns rows belonging to a different user.

Tables
------
users           : authentication identity
jobs            : job applications per user
emails          : recruiter emails linked to jobs
documents       : AI-generated resumes / cover letters
user_prefs      : key-value preferences per user
discovered_jobs : auto-found jobs (deferred feature)
notifications   : per-user notification center
search_runs     : audit log (deferred feature)
referral_contacts / referral_outreach : (deferred feature)
application_queue : (deferred feature)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("hirepilot.database")

DB_PATH = os.environ.get(
    "HIREPILOT_DB_PATH",
    os.path.join(os.path.dirname(__file__), "hirepilot.db"),
)

ALL_STATUSES = [
    "Found", "Applied", "No Response",
    "Recruiter Screen", "Phone Screen", "Assessment",
    "Virtual Interview", "Technical Interview", "Final Round",
    "Offer", "Rejected",
]

STATUS_GROUP = {
    "Found": "neutral",
    "Applied": "active", "No Response": "stale",
    "Recruiter Screen": "progress", "Phone Screen": "progress",
    "Assessment": "progress", "Virtual Interview": "progress",
    "Technical Interview": "progress", "Final Round": "progress",
    "Offer": "success", "Rejected": "danger",
}


class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    # ══════════════════════════════════════════════════════════════════
    # SCHEMA MIGRATION
    # ══════════════════════════════════════════════════════════════════

    def _migrate(self):
        cur = self.conn.cursor()

        # ── users (new) ────────────────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            TEXT PRIMARY KEY,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                name          TEXT NOT NULL DEFAULT '',
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                last_login    TEXT
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")

        # ── user_prefs (per-user KV store) ─────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_prefs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                key        TEXT NOT NULL,
                value      TEXT,
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(user_id, key)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_prefs_user ON user_prefs(user_id)")

        # ── jobs ───────────────────────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id           TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                company           TEXT NOT NULL,
                role              TEXT NOT NULL,
                location          TEXT DEFAULT '',
                job_url           TEXT DEFAULT '',
                source            TEXT DEFAULT '',
                job_description   TEXT DEFAULT '',
                match_score       INTEGER,
                resume_version    TEXT DEFAULT '',
                status            TEXT DEFAULT 'Found',
                date_found        TEXT,
                date_applied      TEXT,
                recruiter_email   TEXT DEFAULT '',
                referral_contact  TEXT DEFAULT '',
                notes             TEXT DEFAULT '',
                created_at        TEXT DEFAULT (datetime('now')),
                updated_at        TEXT DEFAULT (datetime('now'))
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user   ON jobs(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(user_id, status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_score  ON jobs(user_id, match_score)")

        # ── emails ─────────────────────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS emails (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id          TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                job_id           INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
                gmail_id         TEXT,
                sender           TEXT DEFAULT '',
                subject          TEXT DEFAULT '',
                body             TEXT DEFAULT '',
                detected_status  TEXT,
                confidence_score REAL,
                received_date    TEXT,
                created_at       TEXT DEFAULT (datetime('now')),
                UNIQUE(user_id, gmail_id)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_emails_user ON emails(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_emails_job  ON emails(user_id, job_id)")

        # ── documents ──────────────────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                job_id     INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
                doc_type   TEXT NOT NULL,
                company    TEXT DEFAULT '',
                role       TEXT DEFAULT '',
                content    TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_docs_user ON documents(user_id)")

        # ── notifications ──────────────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                type       TEXT,
                title      TEXT,
                body       TEXT,
                job_id     INTEGER,
                is_read    INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, is_read)")

        # ── deferred feature tables (minimal — no active queries) ──────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS discovered_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                ext_id  TEXT,
                company TEXT NOT NULL,
                role    TEXT NOT NULL,
                job_url TEXT,
                source_type TEXT,
                match_score INTEGER,
                status  TEXT DEFAULT 'new',
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(user_id, ext_id)
            )
        """)

        self.conn.commit()
        logger.info("Database migration complete: %s", DB_PATH)

    # ══════════════════════════════════════════════════════════════════
    # USER MANAGEMENT
    # ══════════════════════════════════════════════════════════════════

    def create_user(self, email: str, password_hash: str, name: str) -> str:
        """Create a new user. Returns the new user_id (UUID hex)."""
        import secrets
        user_id = secrets.token_hex(16)
        self.conn.execute(
            "INSERT INTO users (id, email, password_hash, name) VALUES (?,?,?,?)",
            (user_id, email.lower().strip(), password_hash, name.strip()),
        )
        self.conn.commit()
        return user_id

    def get_user_by_email(self, email: str) -> Optional[Dict]:
        r = self.conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
        ).fetchone()
        return dict(r) if r else None

    def get_user(self, user_id: str) -> Optional[Dict]:
        r = self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(r) if r else None

    def update_last_login(self, user_id: str) -> None:
        self.conn.execute(
            "UPDATE users SET last_login = ? WHERE id = ?",
            (datetime.now().isoformat(sep=" ", timespec="seconds"), user_id),
        )
        self.conn.commit()

    def delete_user_and_all_data(self, user_id: str) -> None:
        """GDPR: cascade delete removes all user data via FK constraints."""
        self.conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self.conn.commit()

    # ══════════════════════════════════════════════════════════════════
    # USER PREFERENCES (per-user KV)
    # ══════════════════════════════════════════════════════════════════

    def get_user_pref(self, user_id: str, key: str, default=None):
        r = self.conn.execute(
            "SELECT value FROM user_prefs WHERE user_id = ? AND key = ?",
            (user_id, key),
        ).fetchone()
        if not r:
            return default
        try:
            return json.loads(r["value"])
        except (json.JSONDecodeError, TypeError):
            return r["value"]

    def set_user_pref(self, user_id: str, key: str, value) -> None:
        self.conn.execute(
            """INSERT INTO user_prefs (user_id, key, value, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(user_id, key) DO UPDATE
               SET value = excluded.value, updated_at = excluded.updated_at""",
            (user_id, key, json.dumps(value)),
        )
        self.conn.commit()

    # Convenience wrappers used by candidate_profile.py
    def get_pref(self, key: str, default=None, user_id: str = "") -> any:
        """Legacy single-user compat — prefer get_user_pref in new code."""
        if user_id:
            return self.get_user_pref(user_id, key, default)
        return default

    def set_pref(self, key: str, value, user_id: str = "") -> None:
        """Legacy single-user compat — prefer set_user_pref in new code."""
        if user_id:
            self.set_user_pref(user_id, key, value)

    # ══════════════════════════════════════════════════════════════════
    # JOBS
    # ══════════════════════════════════════════════════════════════════

    def add_job(
        self,
        user_id: str,
        company: str,
        role: str,
        location: str = "",
        job_url: str = "",
        source: str = "",
        job_description: str = "",
        match_score: Optional[int] = None,
        resume_version: str = "",
        status: str = "Found",
        date_found: Optional[str] = None,
        date_applied: Optional[str] = None,
        recruiter_email: str = "",
        referral_contact: str = "",
        notes: str = "",
    ) -> int:
        date_found = date_found or str(datetime.today().date())
        cur = self.conn.execute(
            """INSERT INTO jobs
               (user_id, company, role, location, job_url, source, job_description,
                match_score, resume_version, status, date_found, date_applied,
                recruiter_email, referral_contact, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (user_id, company, role, location, job_url, source, job_description,
             match_score, resume_version, status, date_found, date_applied,
             recruiter_email, referral_contact, notes),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_all_jobs(self, user_id: str, limit: int = 200) -> List[Dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()]

    def get_job(self, user_id: str, job_id: int) -> Optional[Dict]:
        r = self.conn.execute(
            "SELECT * FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id)
        ).fetchone()
        return dict(r) if r else None

    def update_job(self, user_id: str, job_id: int, **fields) -> None:
        if not fields:
            return
        # Prevent user_id spoofing
        fields.pop("user_id", None)
        fields["updated_at"] = datetime.now().isoformat(sep=" ", timespec="seconds")
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE jobs SET {cols} WHERE id = ? AND user_id = ?",
            list(fields.values()) + [job_id, user_id],
        )
        self.conn.commit()

    def update_status(self, user_id: str, job_id: int, status: str) -> None:
        self.update_job(user_id, job_id, status=status)

    def update_match_score(self, user_id: str, job_id: int, score: int) -> None:
        self.update_job(user_id, job_id, match_score=score)

    def delete_job(self, user_id: str, job_id: int) -> None:
        self.conn.execute(
            "DELETE FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id)
        )
        self.conn.commit()

    def get_pipeline_summary(self, user_id: str) -> Dict:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) as cnt FROM jobs WHERE user_id = ? GROUP BY status",
            (user_id,),
        ).fetchall()
        counts = {r["status"]: r["cnt"] for r in rows}
        total = sum(counts.values())
        active = sum(counts.get(s, 0) for s in [
            "Applied", "Recruiter Screen", "Phone Screen", "Assessment",
            "Virtual Interview", "Technical Interview", "Final Round",
        ])
        return {
            "total": total,
            "active": active,
            "offers": counts.get("Offer", 0),
            "rejected": counts.get("Rejected", 0),
            "counts": counts,
        }

    # ══════════════════════════════════════════════════════════════════
    # DOCUMENTS
    # ══════════════════════════════════════════════════════════════════

    def save_document(
        self,
        user_id: str,
        doc_type: str,
        company: str,
        role: str,
        content: str,
        job_id: Optional[int] = None,
    ) -> int:
        cur = self.conn.execute(
            """INSERT INTO documents (user_id, job_id, doc_type, company, role, content)
               VALUES (?,?,?,?,?,?)""",
            (user_id, job_id, doc_type, company, role, content),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_all_documents(self, user_id: str) -> List[Dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM documents WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()]

    def delete_document(self, user_id: str, doc_id: int) -> None:
        self.conn.execute(
            "DELETE FROM documents WHERE id = ? AND user_id = ?", (doc_id, user_id)
        )
        self.conn.commit()

    # ══════════════════════════════════════════════════════════════════
    # EMAILS
    # ══════════════════════════════════════════════════════════════════

    def add_email(
        self,
        user_id: str,
        job_id: Optional[int],
        sender: str,
        subject: str,
        body: str,
        detected_status: str = "",
        confidence_score: float = 0.0,
        received_date: Optional[str] = None,
        gmail_id: Optional[str] = None,
    ) -> int:
        received_date = received_date or datetime.now().isoformat(sep=" ", timespec="seconds")
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO emails
               (user_id, job_id, gmail_id, sender, subject, body,
                detected_status, confidence_score, received_date)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (user_id, job_id, gmail_id, sender, subject, body,
             detected_status, confidence_score, received_date),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_emails_for_job(self, user_id: str, job_id: int) -> List[Dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM emails WHERE user_id = ? AND job_id = ? ORDER BY received_date DESC",
            (user_id, job_id),
        ).fetchall()]

    # ══════════════════════════════════════════════════════════════════
    # NOTIFICATIONS
    # ══════════════════════════════════════════════════════════════════

    def get_unread_count(self, user_id: str) -> int:
        r = self.conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0",
            (user_id,),
        ).fetchone()
        return r[0] if r else 0

    def add_notification(self, user_id: str, type_: str, title: str, body: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO notifications (user_id, type, title, body) VALUES (?,?,?,?)",
            (user_id, type_, title, body),
        )
        self.conn.commit()
        return cur.lastrowid

    def mark_all_read(self, user_id: str) -> None:
        self.conn.execute(
            "UPDATE notifications SET is_read = 1 WHERE user_id = ?", (user_id,)
        )
        self.conn.commit()
