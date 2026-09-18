"""SQLite recipe library: facts only (dish name, serves, ingredients).

Never stores instructional prose from ingested books -- see legal note in the
build spec. Steps are always generated live, per request, per user.

User accounts and sessions live in app/users_db.py, not here -- that data is
read-write at runtime and needs to survive a real deploy (Postgres in
production), unlike this file's recipes, which are read-only after ingestion
and ship bundled with the app either way. See users_db.py for why.
"""

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "whats_cooking.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dish_name TEXT NOT NULL,
    dish_name_normalized TEXT NOT NULL,
    serves INTEGER NOT NULL,
    ingredients_json TEXT NOT NULL,
    diet TEXT,
    cuisine TEXT,
    style_tag TEXT,
    source_book TEXT,
    type_tags TEXT
);
CREATE INDEX IF NOT EXISTS idx_dish_name_normalized ON recipes(dish_name_normalized);
"""


def _normalize(name: str) -> str:
    return name.strip().lower()


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(_SCHEMA)
        # migrate existing databases created before type_tags existed
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(recipes)")}
        if "type_tags" not in existing_cols:
            conn.execute("ALTER TABLE recipes ADD COLUMN type_tags TEXT")
        conn.commit()
    finally:
        conn.close()


def _row_to_recipe(row: sqlite3.Row) -> dict:
    return {
        "dish_name": row["dish_name"],
        "serves": row["serves"],
        "ingredients": json.loads(row["ingredients_json"]),
        "diet": row["diet"],
        "cuisine": row["cuisine"],
    }


def lookup_recipe(dish_name: str) -> dict | None:
    key = _normalize(dish_name)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM recipes WHERE dish_name_normalized = ? "
            "OR dish_name_normalized LIKE ? OR ? LIKE '%' || dish_name_normalized || '%' "
            "ORDER BY RANDOM() LIMIT 1",
            (key, f"%{key}%", key),
        ).fetchone()
    finally:
        conn.close()

    return _row_to_recipe(row) if row else None


def lookup_recipe_by_tag(tag: str, diet: str | None = None, cuisine: str | None = None) -> dict | None:
    """Random recipe matching `tag`. If `diet`/`cuisine` are given, prefer a
    match that also fits them (a signed-in user's stored preference biasing an
    otherwise-generic search) but fall back to any tag match if the library
    doesn't happen to have that combination -- a preference should never turn
    "curry" into "no results".
    """
    conn = get_connection()
    try:
        if diet or cuisine:
            conditions = ["',' || type_tags || ',' LIKE ?"]
            params: list[str] = [f"%,{tag},%"]
            if diet:
                conditions.append("diet = ?")
                params.append(diet)
            if cuisine:
                conditions.append("cuisine = ?")
                params.append(cuisine)
            row = conn.execute(
                f"SELECT * FROM recipes WHERE {' AND '.join(conditions)} ORDER BY RANDOM() LIMIT 1",
                params,
            ).fetchone()
            if row:
                return _row_to_recipe(row)

        row = conn.execute(
            "SELECT * FROM recipes WHERE ',' || type_tags || ',' LIKE ? "
            "ORDER BY RANDOM() LIMIT 1",
            (f"%,{tag},%",),
        ).fetchone()
    finally:
        conn.close()

    return _row_to_recipe(row) if row else None


def lookup_recipe_by_diet(diet: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM recipes WHERE diet = ? ORDER BY RANDOM() LIMIT 1",
            (diet,),
        ).fetchone()
    finally:
        conn.close()

    return _row_to_recipe(row) if row else None


def lookup_recipe_filtered(
    tags: list[str] | None = None, diet: str | None = None, cuisine: str | None = None
) -> dict | None:
    """Progressive-fallback lookup for the quiz: tries the fullest match first
    (all given tags + diet + cuisine), then relaxes one constraint at a time
    (cuisine, then diet, then secondary tags) until something is found, and
    finally falls back to any recipe at all -- a quiz should never dead-end.
    """
    tags = tags or []
    conn = get_connection()
    try:
        def try_query(tag_subset: list[str], use_diet: bool, use_cuisine: bool) -> sqlite3.Row | None:
            conditions = ["',' || type_tags || ',' LIKE ?" for _ in tag_subset]
            params: list[str] = [f"%,{t},%" for t in tag_subset]
            if use_diet and diet:
                conditions.append("diet = ?")
                params.append(diet)
            if use_cuisine and cuisine:
                conditions.append("cuisine = ?")
                params.append(cuisine)
            if conditions:
                query = f"SELECT * FROM recipes WHERE {' AND '.join(conditions)} ORDER BY RANDOM() LIMIT 1"
            else:
                query = "SELECT * FROM recipes ORDER BY RANDOM() LIMIT 1"
            return conn.execute(query, params).fetchone()

        attempts = [
            (tags, True, True),
            (tags, False, True),
            (tags, True, False),
            (tags, False, False),
        ]
        if len(tags) > 1:
            attempts.append((tags[:1], True, True))
            attempts.append((tags[:1], False, False))
        attempts.append(([], False, False))  # absolute fallback: anything at all

        for tag_subset, use_diet, use_cuisine in attempts:
            row = try_query(tag_subset, use_diet, use_cuisine)
            if row:
                return _row_to_recipe(row)
        return None
    finally:
        conn.close()


def lookup_recipe_by_cuisine(cuisine: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM recipes WHERE cuisine = ? ORDER BY RANDOM() LIMIT 1",
            (cuisine,),
        ).fetchone()
    finally:
        conn.close()

    return _row_to_recipe(row) if row else None


def insert_recipe(
    dish_name: str,
    serves: int,
    ingredients: list[dict],
    diet: str | None,
    cuisine: str | None,
    style_tag: str | None,
    source_book: str | None,
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO recipes (dish_name, dish_name_normalized, serves, ingredients_json, diet, cuisine, style_tag, source_book) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (dish_name, _normalize(dish_name), serves, json.dumps(ingredients), diet, cuisine, style_tag, source_book),
        )
        conn.commit()
    finally:
        conn.close()


def count_recipes() -> int:
    conn = get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
    finally:
        conn.close()


def iter_all_recipes() -> list[dict]:
    """id, dish_name, and ingredients for every recipe -- used by the tag backfill script."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT id, dish_name, ingredients_json FROM recipes").fetchall()
    finally:
        conn.close()
    return [
        {"id": r["id"], "dish_name": r["dish_name"], "ingredients": json.loads(r["ingredients_json"])}
        for r in rows
    ]


def set_type_tags(recipe_id: int, tags: list[str]) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE recipes SET type_tags = ? WHERE id = ?",
            (",".join(tags), recipe_id),
        )
        conn.commit()
    finally:
        conn.close()
