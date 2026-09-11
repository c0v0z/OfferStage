import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Database:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS applications (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    resume_text TEXT NOT NULL,
                    jd_text TEXT NOT NULL,
                    resume_json TEXT NOT NULL,
                    job_json TEXT NOT NULL,
                    analysis_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS interviews (
                    id TEXT PRIMARY KEY,
                    application_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    questions_json TEXT NOT NULL,
                    transcript_json TEXT NOT NULL,
                    current_index INTEGER NOT NULL DEFAULT 0,
                    completed INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(application_id) REFERENCES applications(id)
                );
                CREATE TABLE IF NOT EXISTS profiles (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS experiences (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    organization TEXT NOT NULL DEFAULT '',
                    period TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'manual',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(profile_id) REFERENCES profiles(id)
                );
                CREATE INDEX IF NOT EXISTS idx_experiences_profile ON experiences(profile_id);
                CREATE TABLE IF NOT EXISTS experience_embeddings (
                    experience_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    embedding_model TEXT NOT NULL,
                    dimension INTEGER NOT NULL,
                    vector_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(experience_id) REFERENCES experiences(id)
                );
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(applications)").fetchall()}
            if "profile_id" not in columns:
                connection.execute("ALTER TABLE applications ADD COLUMN profile_id TEXT NOT NULL DEFAULT ''")
            if "retrieved_json" not in columns:
                connection.execute("ALTER TABLE applications ADD COLUMN retrieved_json TEXT NOT NULL DEFAULT '[]'")

    def save_application(
        self,
        application_id: str,
        resume_text: str,
        jd_text: str,
        payload: dict[str, Any],
        profile_id: str = "",
        retrieved_experiences: list[dict[str, Any]] | None = None,
    ) -> str:
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO applications
                (id, created_at, resume_text, jd_text, resume_json, job_json, analysis_json, profile_id, retrieved_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    application_id,
                    created_at,
                    resume_text,
                    jd_text,
                    json.dumps(payload["resume"], ensure_ascii=False),
                    json.dumps(payload["job"], ensure_ascii=False),
                    json.dumps(payload["analysis"], ensure_ascii=False),
                    profile_id,
                    json.dumps(retrieved_experiences or [], ensure_ascii=False),
                ),
            )
        return created_at

    def create_profile(self, profile_id: str, name: str) -> str:
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO profiles (id, name, created_at) VALUES (?, ?, ?)",
                (profile_id, name.strip() or "我的求职档案", created_at),
            )
        return created_at

    def get_profile(self, profile_id: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()

    def list_profiles(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                """SELECT p.*, COUNT(e.id) AS experience_count
                FROM profiles p LEFT JOIN experiences e ON e.profile_id = p.id
                GROUP BY p.id ORDER BY p.created_at DESC LIMIT ?""",
                (max(1, min(limit, 100)),),
            ).fetchall()

    def add_experience(
        self,
        experience_id: str,
        profile_id: str,
        kind: str,
        title: str,
        organization: str,
        period: str,
        text: str,
        source: str,
    ) -> str:
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO experiences
                (id, profile_id, kind, title, organization, period, text, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (experience_id, profile_id, kind, title, organization, period, text, source, created_at),
            )
        return created_at

    def list_experiences(self, profile_id: str) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM experiences WHERE profile_id = ? ORDER BY created_at DESC", (profile_id,)
            ).fetchall()

    def save_embedding(self, experience_id: str, profile_id: str, model: str, dimension: int, vector: list[float]) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO experience_embeddings
                (experience_id, profile_id, embedding_model, dimension, vector_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (experience_id, profile_id, model, dimension, json.dumps(vector), datetime.now(timezone.utc).isoformat()),
            )

    def list_embeddings(self, profile_id: str) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                """SELECT e.*, v.embedding_model, v.dimension, v.vector_json
                FROM experiences e JOIN experience_embeddings v ON v.experience_id = e.id
                WHERE e.profile_id = ?""",
                (profile_id,),
            ).fetchall()

    def get_application(self, application_id: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()

    def list_applications(self, limit: int = 20) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM applications ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 100)),)
            ).fetchall()

    def save_interview(self, session_id: str, application_id: str, questions: list[dict[str, Any]]) -> str:
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO interviews
                (id, application_id, created_at, questions_json, transcript_json)
                VALUES (?, ?, ?, ?, ?)""",
                (session_id, application_id, created_at, json.dumps(questions, ensure_ascii=False), "[]"),
            )
        return created_at

    def get_interview(self, session_id: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute("SELECT * FROM interviews WHERE id = ?", (session_id,)).fetchone()

    def update_interview(self, session_id: str, transcript: list[dict[str, Any]], current_index: int, completed: bool) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE interviews SET transcript_json = ?, current_index = ?, completed = ? WHERE id = ?""",
                (json.dumps(transcript, ensure_ascii=False), current_index, int(completed), session_id),
            )
