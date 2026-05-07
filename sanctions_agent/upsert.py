"""
Database operations – wipe & reload sanctions entities by source.

Strategy: for each source (UN / OFAC / EU) that returned entities,
wipe everything currently in the DB for that source, then insert the
fresh batch. Each source is processed in its own transaction, so a
failure in one source does not affect the others.
"""
import logging
from collections import defaultdict
from typing import Dict, List

import psycopg2

from .config import DATABASE_URL
from .models import SanctionEntity

logger = logging.getLogger(__name__)


# ── Value mappers ─────────────────────────────────────────────────────────────

def map_entity_type_for_db(value: str) -> str:
    mapping = {
        "individual": "person",
        "entity": "company",
        "vessel": "company",
        "aircraft": "company",
        "unknown": "company",
    }
    return mapping.get((value or "").lower(), "company")


def map_risk_level_for_db(value: str) -> str:
    mapping = {
        "low": "LOW",
        "medium": "MEDIUM",
        "high": "HIGH",
        "critical": "HIGH",
        "unknown": "MEDIUM",
    }
    return mapping.get((value or "").lower(), "MEDIUM")


def map_name_type_for_db(value: str, is_primary: bool) -> str:
    if is_primary:
        return "PRIMARY"
    raw = (value or "").lower()
    mapping = {
        "aka": "AKA",
        "alias": "ALIAS",
        "primary": "PRIMARY",
        "low_quality": "AKA",
        "weak": "AKA",
        "alternative": "ALIAS",
        "other": "ALIAS",
        "unknown": "ALIAS",
        "translit": "TRANSLIT",
    }
    return mapping.get(raw, "ALIAS")


# ── Schema bootstrapping ──────────────────────────────────────────────────────

_ENSURE_SOURCE_COLS = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='entities' AND column_name='source_name'
    ) THEN
        ALTER TABLE entities ADD COLUMN source_name text;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='entities' AND column_name='source_id'
    ) THEN
        ALTER TABLE entities ADD COLUMN source_id text;
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS ix_entities_source_name
    ON entities (source_name);
CREATE UNIQUE INDEX IF NOT EXISTS uq_entities_source
    ON entities (source_name, source_id);
"""

_FK_INTROSPECT = """
SELECT
    tc.table_name        AS dependent_table,
    kcu.column_name      AS dependent_column,
    rc.delete_rule       AS on_delete
FROM information_schema.table_constraints     tc
JOIN information_schema.key_column_usage      kcu USING (constraint_schema, constraint_name)
JOIN information_schema.referential_constraints rc USING (constraint_schema, constraint_name)
JOIN information_schema.constraint_column_usage ccu USING (constraint_schema, constraint_name)
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND ccu.table_name  = 'entities'
  AND ccu.column_name = 'id';
"""


def _conn():
    return psycopg2.connect(DATABASE_URL)


def ensure_schema():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_ENSURE_SOURCE_COLS)
        conn.commit()
    logger.info("DB – schema checks done")
    _log_entity_fks()


def _log_entity_fks():
    """Inspect FKs pointing at entities.id and warn if RESTRICT/NO ACTION."""
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_FK_INTROSPECT)
                rows = cur.fetchall()
    except Exception as exc:
        logger.warning("Could not introspect FKs on entities.id: %s", exc)
        return

    if not rows:
        logger.info("FK check – no foreign keys reference entities.id (wipe is safe)")
        return

    for table, col, on_delete in rows:
        level = logger.warning if on_delete in ("NO ACTION", "RESTRICT") else logger.info
        level(
            "FK check – %s.%s -> entities.id (ON DELETE %s)",
            table, col, on_delete,
        )


# ── Wipe & reload ─────────────────────────────────────────────────────────────

_WIPE_NAMES_FOR_SOURCE = """
DELETE FROM entity_names
WHERE entity_id IN (SELECT id FROM entities WHERE source_name = %s);
"""

_WIPE_ENTITIES_FOR_SOURCE = "DELETE FROM entities WHERE source_name = %s;"

_INSERT_ENTITY = """
INSERT INTO entities (
    id, entity_type, primary_name, country_focus, risk_level,
    source_name, source_id, created_at, updated_at
)
VALUES (
    uuid_generate_v4(), %(entity_type)s, %(primary_name)s,
    %(country_focus)s, %(risk_level)s,
    %(source_name)s, %(source_id)s,
    now(), now()
)
RETURNING id;
"""

_INSERT_NAME = """
INSERT INTO entity_names (
    entity_id, name_raw, name_normalized, name_tokens,
    is_primary, name_type, created_at
)
VALUES (
    %(entity_id)s, %(name_raw)s, %(name_normalized)s,
    %(name_tokens)s, %(is_primary)s, %(name_type)s,
    now()
);
"""


def wipe_and_insert(entities: List[SanctionEntity]) -> dict:
    """
    Group *entities* by source and, for each source:
      1. Delete all rows currently associated with that source_name.
      2. Insert the fresh batch.
    Each source runs in its own transaction; a failure isolates to that source.
    """
    stats = {
        "inserted":      0,
        "errors":        0,
        "sources_wiped": 0,
        "by_source":     {},  # source -> {"inserted": n, "deleted": n}
    }

    grouped: Dict[str, List[SanctionEntity]] = defaultdict(list)
    for ent in entities:
        grouped[ent.source].append(ent)

    for source, source_entities in grouped.items():
        deleted = 0
        inserted = 0
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(_WIPE_NAMES_FOR_SOURCE, (source,))
                    cur.execute(_WIPE_ENTITIES_FOR_SOURCE, (source,))
                    deleted = cur.rowcount

                    for ent in source_entities:
                        cur.execute(
                            _INSERT_ENTITY,
                            {
                                "entity_type":   map_entity_type_for_db(ent.entity_type.value),
                                "primary_name":  ent.primary_name,
                                "country_focus": ent.country_focus,
                                "risk_level":    map_risk_level_for_db(ent.risk_level.value),
                                "source_name":   ent.source,
                                "source_id":     ent.source_id,
                            },
                        )
                        entity_uuid = cur.fetchone()[0]

                        for name in ent.names:
                            cur.execute(
                                _INSERT_NAME,
                                {
                                    "entity_id":       entity_uuid,
                                    "name_raw":        name.name_raw,
                                    "name_normalized": name.name_normalized,
                                    "name_tokens":     name.name_tokens,
                                    "is_primary":      name.is_primary,
                                    "name_type":       map_name_type_for_db(
                                        name.name_type, name.is_primary,
                                    ),
                                },
                            )
                        inserted += 1
                conn.commit()

            stats["inserted"]      += inserted
            stats["sources_wiped"] += 1
            stats["by_source"][source] = {"inserted": inserted, "deleted": deleted}
            logger.info(
                "%s – wiped %d, inserted %d", source, deleted, inserted,
            )

        except Exception as exc:
            logger.error("Wipe/reload failed for source %s: %s", source, exc)
            stats["errors"] += 1
            stats["by_source"][source] = {"inserted": 0, "deleted": 0, "error": str(exc)}

    return stats
