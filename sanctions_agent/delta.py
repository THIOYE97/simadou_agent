"""
Delta engine – compares freshly fetched sanctions data against what's already
in the database and returns only the changes that need to be applied.

This avoids hammering the DB with 15 000 full upserts every day when 99%
of the list hasn't changed.

Strategy:
  • Each entity is hashed on (primary_name_normalized + sorted alias fingerprints
    + country + risk_level).
  • The hash is stored in a lightweight `sanctions_sync_meta` table.
  • On each run we compare hashes → only ADDED / MODIFIED entities are upserted.
  • REMOVED entities (present in DB but gone from source) are soft-deleted via
    an `is_active` flag added to the entities table.
"""
import hashlib
import json
import logging
from typing import Dict, List, NamedTuple, Set, Tuple

import psycopg2
import psycopg2.extras

from .config import DATABASE_URL
from .models import SanctionEntity
from .normalizer import normalize

logger = logging.getLogger(__name__)

# ── DDL ───────────────────────────────────────────────────────────────────────

_ENSURE_DELTA_SCHEMA = """
-- Lightweight hash store per source entity
CREATE TABLE IF NOT EXISTS sanctions_sync_meta (
    source_id   text        PRIMARY KEY,   -- e.g. "OFAC-12345"
    source_name text        NOT NULL,
    content_hash text       NOT NULL,
    last_seen   timestamptz NOT NULL DEFAULT now()
);

-- Soft-delete flag on entities
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='entities' AND column_name='is_active'
    ) THEN
        ALTER TABLE entities ADD COLUMN is_active boolean NOT NULL DEFAULT true;
    END IF;
END;
$$;
"""

# ── Hash ──────────────────────────────────────────────────────────────────────

def _entity_hash(ent: SanctionEntity) -> str:
    """Deterministic hash of the entity's meaningful fields."""
    alias_fingerprints = sorted(
        normalize(n.name_raw) for n in ent.names if not n.is_primary
    )
    payload = json.dumps(
        {
            "primary": normalize(ent.primary_name),
            "aliases": alias_fingerprints,
            "country": ent.country_focus or "",
            "risk": ent.risk_level.value,
            "type": ent.entity_type.value,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ── Delta result ──────────────────────────────────────────────────────────────

class DeltaResult(NamedTuple):
    to_upsert: List[SanctionEntity]   # new + modified
    removed_ids: List[str]            # source_ids no longer in the live list
    stats: Dict[str, int]             # {new, modified, unchanged, removed}


# ── Public API ────────────────────────────────────────────────────────────────

def ensure_delta_schema():
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(_ENSURE_DELTA_SCHEMA)
        conn.commit()
    logger.info("Delta schema OK")


def compute_delta(entities: List[SanctionEntity]) -> DeltaResult:
    """
    Compare *entities* (fresh from sources) against stored hashes.
    Returns what actually needs to be written.
    """
    # Build fresh hash map
    fresh: Dict[str, Tuple[str, SanctionEntity]] = {
        ent.source_id: (_entity_hash(ent), ent) for ent in entities
    }
    fresh_ids: Set[str] = set(fresh.keys())

    # Load stored hashes
    stored: Dict[str, str] = {}  # source_id → hash
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT source_id, content_hash FROM sanctions_sync_meta;")
            for row in cur.fetchall():
                stored[row[0]] = row[1]
    stored_ids: Set[str] = set(stored.keys())

    # Classify
    new_ids      = fresh_ids - stored_ids
    common_ids   = fresh_ids & stored_ids
    removed_ids  = list(stored_ids - fresh_ids)

    modified_ids = {sid for sid in common_ids if fresh[sid][0] != stored[sid]}
    unchanged    = len(common_ids) - len(modified_ids)

    to_upsert = [fresh[sid][1] for sid in (new_ids | modified_ids)]

    stats = {
        "new": len(new_ids),
        "modified": len(modified_ids),
        "unchanged": unchanged,
        "removed": len(removed_ids),
    }
    logger.info(
        "Delta – new=%d modified=%d unchanged=%d removed=%d",
        stats["new"], stats["modified"], stats["unchanged"], stats["removed"],
    )
    return DeltaResult(to_upsert=to_upsert, removed_ids=removed_ids, stats=stats)


def commit_hashes(entities: List[SanctionEntity]):
    """Persist content hashes after a successful upsert."""
    if not entities:
        return
    rows = [
        (ent.source_id, ent.source, _entity_hash(ent))
        for ent in entities
    ]
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO sanctions_sync_meta (source_id, source_name, content_hash, last_seen)
                VALUES %s
                ON CONFLICT (source_id) DO UPDATE SET
                    content_hash = EXCLUDED.content_hash,
                    last_seen    = now();
                """,
                rows,
            )
        conn.commit()
    logger.debug("Committed %d hashes", len(rows))


def soft_delete_removed(removed_ids: List[str]):
    """Mark entities no longer present in any source list as inactive."""
    if not removed_ids:
        return
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE entities SET is_active = false, updated_at = now() "
                "WHERE source_id = ANY(%s);",
                (removed_ids,),
            )
            affected = cur.rowcount
        conn.commit()
    logger.info("Soft-deleted %d removed entities", affected)
