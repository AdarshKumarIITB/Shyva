"""SQLite persistence for classification dossiers, legacy session projections, and API cache."""
from __future__ import annotations

import json
import aiosqlite

from app.config import DATABASE_PATH
from app.domain.dossier import ClassificationDossier


async def init_db():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'intake',
                product_family TEXT,
                origin_country TEXT,
                destination_country TEXT,
                product_facts TEXT,
                pending_questions TEXT,
                classification TEXT,
                duty_stack TEXT,
                audit_trail TEXT
            );

            CREATE TABLE IF NOT EXISTS classification_dossiers (
                dossier_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL,
                current_state TEXT NOT NULL,
                product_family TEXT,
                origin_country TEXT,
                destination_country TEXT,
                effective_date TEXT,
                dossier_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS api_cache (
                cache_key TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                response TEXT NOT NULL,
                cached_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
            CREATE INDEX IF NOT EXISTS idx_dossiers_status ON classification_dossiers(status);
            CREATE INDEX IF NOT EXISTS idx_dossiers_state ON classification_dossiers(current_state);
            CREATE INDEX IF NOT EXISTS idx_cache_source ON api_cache(source);

            CREATE TABLE IF NOT EXISTS agent_sessions (
                session_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'running',
                session_json TEXT NOT NULL
            );
            """
        )
        await db.commit()


async def save_dossier(dossier: ClassificationDossier):
    payload = dossier.model_dump(mode="json")
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO classification_dossiers
               (dossier_id, created_at, updated_at, status, current_state, product_family,
                origin_country, destination_country, effective_date, dossier_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                dossier.dossier_id,
                dossier.created_at,
                dossier.updated_at,
                dossier.status,
                str(dossier.current_state),
                dossier.product_family,
                dossier.measure_context.origin_country,
                dossier.measure_context.destination_regime,
                dossier.measure_context.effective_date,
                json.dumps(payload),
            ),
        )
        projection = _legacy_projection_from_dossier(dossier)
        await db.execute(
            """INSERT OR REPLACE INTO sessions
               (session_id, created_at, status, product_family, origin_country,
                destination_country, product_facts, pending_questions,
                classification, duty_stack, audit_trail)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                projection["session_id"],
                projection["created_at"],
                projection["status"],
                projection.get("product_family"),
                projection.get("origin_country"),
                projection.get("destination_country"),
                json.dumps(projection.get("product_facts")),
                json.dumps(projection.get("pending_questions")),
                json.dumps(projection.get("classification")),
                json.dumps(projection.get("duty_stack")),
                json.dumps(projection.get("audit_trail")),
            ),
        )
        await db.commit()


async def load_dossier(dossier_id: str) -> ClassificationDossier | None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT dossier_json FROM classification_dossiers WHERE dossier_id = ?",
            (dossier_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return ClassificationDossier.model_validate(json.loads(row[0]))


async def save_session(session: dict):
    """Legacy wrapper retained for compatibility with older code paths."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO sessions
               (session_id, created_at, status, product_family, origin_country,
                destination_country, product_facts, pending_questions,
                classification, duty_stack, audit_trail)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session["session_id"],
                session["created_at"],
                session["status"],
                session.get("product_family"),
                session.get("origin_country"),
                session.get("destination_country"),
                json.dumps(session.get("product_facts")),
                json.dumps(session.get("pending_questions")),
                json.dumps(session.get("classification")),
                json.dumps(session.get("duty_stack")),
                json.dumps(session.get("audit_trail")),
            ),
        )
        await db.commit()


async def load_session(session_id: str) -> dict | None:
    dossier = await load_dossier(session_id)
    if dossier:
        return _legacy_projection_from_dossier(dossier)

    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cursor.description]
        data = dict(zip(cols, row))
        for key in ("product_facts", "pending_questions", "classification", "duty_stack", "audit_trail"):
            if data.get(key):
                data[key] = json.loads(data[key])
        return data


async def cache_api_response(cache_key: str, source: str, response: dict):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO api_cache (cache_key, source, response) VALUES (?, ?, ?)",
            (cache_key, source, json.dumps(response)),
        )
        await db.commit()


async def get_cached_response(cache_key: str) -> dict | None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT response FROM api_cache WHERE cache_key = ?", (cache_key,)
        )
        row = await cursor.fetchone()
        if row:
            return json.loads(row[0])
    return None


def _legacy_projection_from_dossier(dossier: ClassificationDossier) -> dict:
    return {
        "session_id": dossier.dossier_id,
        "created_at": dossier.created_at,
        "status": dossier.status,
        "product_family": dossier.product_family,
        "origin_country": dossier.measure_context.origin_country,
        "destination_country": dossier.measure_context.destination_regime,
        "product_facts": dossier.product_facts.model_dump(mode="json"),
        "pending_questions": [q.model_dump(mode="json") for q in dossier.pending_questions],
        "classification": dossier.classification.model_dump(mode="json") if dossier.classification else None,
        "duty_stack": dossier.duty_stack.model_dump(mode="json") if dossier.duty_stack else None,
        "audit_trail": dossier.audit_trail.model_dump(mode="json"),
        "current_state": str(dossier.current_state),
        "assumptions": [a.model_dump(mode="json") for a in dossier.assumptions],
        "digit_locks": [l.model_dump(mode="json") for l in dossier.digit_locks],
        "candidate_paths": [p.model_dump(mode="json") for p in dossier.candidate_paths],
        "decision_ledger": [e.model_dump(mode="json") for e in dossier.decision_ledger],
    }
