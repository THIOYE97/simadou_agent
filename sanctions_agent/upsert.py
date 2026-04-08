"""
Database operations – upsert sanctions entities into the Render PostgreSQL DB.
"""
import logging
from typing import List

import psycopg2

from .config import DATABASE_URL
from .models import SanctionEntity

logger = logging.getLogger(__name__)


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
    raw = (value or "").lower()

    if is_primary:
        return "PRIMARY"

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

CREATE UNIQUE INDEX IF NOT EXISTS uq_entities_source
    ON entities (source_name, source_id);
"""


def _conn():
    return psycopg2.connect(DATABASE_URL)


def ensure_schema():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_ENSURE_SOURCE_COLS)
        conn.commit()
    logger.info("DB – schema checks done")


_UPSERT_ENTITY = """
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
ON CONFLICT (source_name, source_id) DO UPDATE SET
    entity_type   = EXCLUDED.entity_type,
    primary_name  = EXCLUDED.primary_name,
    country_focus = EXCLUDED.country_focus,
    risk_level    = EXCLUDED.risk_level,
    updated_at    = now()
RETURNING id;
"""

_DELETE_NAMES = "DELETE FROM entity_names WHERE entity_id = %s;"

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


def upsert_entities(entities: List[SanctionEntity]) -> dict:
    stats = {"inserted": 0, "updated": 0, "errors": 0}

    with _conn() as conn:
        cur = conn.cursor()
        try:
            for ent in entities:
                try:
                    cur.execute(
                        _UPSERT_ENTITY,
                        {
                            "entity_type": map_entity_type_for_db(ent.entity_type.value),
                            "primary_name": ent.primary_name,
                            "country_focus": ent.country_focus,
                            "risk_level": map_risk_level_for_db(ent.risk_level.value),
                            "source_name": ent.source,
                            "source_id": ent.source_id,
                        },
                    )
                    row = cur.fetchone()
                    entity_uuid = row[0]

                    cur.execute(_DELETE_NAMES, (entity_uuid,))

                    for name in ent.names:
                        cur.execute(
                            _INSERT_NAME,
                            {
                                "entity_id": entity_uuid,
                                "name_raw": name.name_raw,
                                "name_normalized": name.name_normalized,
                                "name_tokens": name.name_tokens,
                                "is_primary": name.is_primary,
                                "name_type": map_name_type_for_db(
                                    name.name_type,
                                    name.is_primary,
                                ),
                            },
                        )

                    stats["inserted"] += 1

                except Exception as exc:
                    logger.error(
                        "DB upsert error for %s (%s): %s",
                        ent.source_id,
                        ent.primary_name,
                        exc,
                    )
                    stats["errors"] += 1
                    conn.rollback()
                    cur = conn.cursor()
                    continue

            conn.commit()
        finally:
            cur.close()

    return stats