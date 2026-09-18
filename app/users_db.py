"""User accounts and sessions -- Postgres in production, SQLite locally.

Unlike the recipe library (db.py), this data is written at runtime (signups,
logins) and needs to survive a restart. A free-tier web service's local disk
does not (Render, and most free hosts, wipe it on every redeploy/restart/
idle spin-down) -- so in production this points at a real Postgres instance
via DATABASE_URL. Locally, with no DATABASE_URL set, it falls back to the
same SQLite file the recipe library uses, so local dev needs no setup.
"""

import os
import sqlite3
from pathlib import Path

DATABASE_URL = os.environ.get("DATABASE_URL")
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras

# Deliberately a separate file from the recipe library (db.py's whats_cooking.db):
# that file is committed to git so the recipe library ships with the deploy,
# and this one -- local test signups -- should never end up in that commit.
SQLITE_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "local_users.db"

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT,
    age INTEGER,
    content_preference TEXT,
    preferred_cuisine TEXT,
    dietary_restriction TEXT,
    drink_preference TEXT,
    skill_level TEXT,
    appliance TEXT,
    language TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""

_POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT,
    age INTEGER,
    content_preference TEXT,
    preferred_cuisine TEXT,
    dietary_restriction TEXT,
    drink_preference TEXT,
    skill_level TEXT,
    appliance TEXT,
    language TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);
"""


def _sqlite_connection() -> sqlite3.Connection:
    SQLITE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _pg_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def init_users_db() -> None:
    if USE_POSTGRES:
        conn = _pg_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(_POSTGRES_SCHEMA)
            conn.commit()
        finally:
            conn.close()
    else:
        conn = _sqlite_connection()
        try:
            conn.executescript(_SQLITE_SCHEMA)
            conn.commit()
        finally:
            conn.close()


def create_user(email: str, password_hash: str, preferences: dict) -> int:
    appliance_str = ",".join(preferences["appliance"])
    values = (
        email,
        password_hash,
        preferences["name"],
        preferences["age"],
        preferences["content_preference"],
        preferences["preferred_cuisine"],
        preferences["dietary_restriction"],
        preferences["drink_preference"],
        preferences["skill_level"],
        appliance_str,
        preferences["language"],
    )

    if USE_POSTGRES:
        conn = _pg_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (email, password_hash, name, age, content_preference, preferred_cuisine, "
                    "dietary_restriction, drink_preference, skill_level, appliance, language) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    values,
                )
                new_id = cur.fetchone()["id"]
            conn.commit()
            return new_id
        finally:
            conn.close()

    conn = _sqlite_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO users (email, password_hash, name, age, content_preference, preferred_cuisine, "
            "dietary_restriction, drink_preference, skill_level, appliance, language, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            values,
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_user_by_email(email: str) -> dict | None:
    if USE_POSTGRES:
        conn = _pg_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE email = %s", (email,))
                row = cur.fetchone()
        finally:
            conn.close()
    else:
        conn = _sqlite_connection()
        try:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        finally:
            conn.close()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    if USE_POSTGRES:
        conn = _pg_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
                row = cur.fetchone()
        finally:
            conn.close()
    else:
        conn = _sqlite_connection()
        try:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        finally:
            conn.close()
    return dict(row) if row else None


def create_session(user_id: int, token: str, ttl_days: int = 30) -> None:
    if USE_POSTGRES:
        conn = _pg_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO sessions (token, user_id, expires_at) "
                    "VALUES (%s, %s, NOW() + (%s * INTERVAL '1 day'))",
                    (token, user_id, ttl_days),
                )
            conn.commit()
        finally:
            conn.close()
        return

    conn = _sqlite_connection()
    try:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) "
            "VALUES (?, ?, datetime('now'), datetime('now', ?))",
            (token, user_id, f"+{ttl_days} days"),
        )
        conn.commit()
    finally:
        conn.close()


def get_session_user(token: str) -> dict | None:
    if USE_POSTGRES:
        conn = _pg_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id "
                    "WHERE sessions.token = %s AND sessions.expires_at > NOW()",
                    (token,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
    else:
        conn = _sqlite_connection()
        try:
            row = conn.execute(
                "SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id "
                "WHERE sessions.token = ? AND sessions.expires_at > datetime('now')",
                (token,),
            ).fetchone()
        finally:
            conn.close()
    return dict(row) if row else None


def delete_session(token: str) -> None:
    if USE_POSTGRES:
        conn = _pg_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM sessions WHERE token = %s", (token,))
            conn.commit()
        finally:
            conn.close()
        return

    conn = _sqlite_connection()
    try:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()
