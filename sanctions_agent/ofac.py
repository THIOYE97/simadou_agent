"""
OFAC SDN & Consolidated Sanctions List fetcher.
Parses the official OFAC XML (SDN Advanced format).
Docs: https://home.treasury.gov/policy-issues/financial-sanctions/specially-designated-nationals-and-blocked-persons-list-sdn-human-readable-lists
"""
import logging
from typing import List
from xml.etree import ElementTree as ET

from .config import OFAC_SDN_URL
from .http_client import fetch_bytes
from .models import EntityType, RiskLevel, SanctionEntity, SanctionName
from .normalizer import normalize, tokenize, detect_name_type

logger = logging.getLogger(__name__)

_NS = {
    "o": "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN"
}

_TYPE_MAP = {
    "individual": EntityType.INDIVIDUAL,
    "entity": EntityType.ENTITY,
    "vessel": EntityType.VESSEL,
    "aircraft": EntityType.AIRCRAFT,
}


def _resolve_ns(root: ET.Element) -> dict:
    """Auto-detect namespace from root tag."""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0].lstrip("{")
        return {"o": ns}
    return {}


def fetch() -> List[SanctionEntity]:
    logger.info("OFAC – downloading SDN list …")
    content = fetch_bytes(OFAC_SDN_URL, source="OFAC")
    if content is None:
        return []

    root = ET.fromstring(content)
    ns = _resolve_ns(root)

    def t(tag: str) -> str:
        return f"{{{ns['o']}}}{tag}" if ns else tag

    entities: List[SanctionEntity] = []

    for entry in root.iter(t("sdnEntry")):
        uid = (entry.findtext(t("uid")) or "").strip()
        raw_type = (entry.findtext(t("sdnType")) or "").strip().lower()
        entity_type = _TYPE_MAP.get(raw_type, EntityType.UNKNOWN)

        # Primary name
        fn = (entry.findtext(t("firstName")) or "").strip()
        ln = (entry.findtext(t("lastName")) or "").strip()
        primary_raw = f"{fn} {ln}".strip() if fn else ln

        if not primary_raw:
            continue

        # Country
        country = None
        for nat in entry.iter(t("nationality")):
            country = nat.findtext(t("country")) or nat.findtext(t("uid"))
            if country:
                break
        if not country:
            for addr in entry.iter(t("address")):
                country = addr.findtext(t("country"))
                if country:
                    break

        # Programs → risk heuristic
        programs = [
            p.text.strip()
            for p in entry.iter(t("program"))
            if p.text
        ]
        risk_level = _risk_from_programs(programs)

        # Names
        names: List[SanctionName] = []
        pnorm = normalize(primary_raw)
        names.append(
            SanctionName(
                name_raw=primary_raw,
                name_normalized=pnorm,
                name_tokens=tokenize(pnorm),
                is_primary=True,
                name_type="primary",
            )
        )

        for aka in entry.iter(t("aka")):
            aka_type = (aka.findtext(t("type")) or "").strip()
            aka_fn = (aka.findtext(t("firstName")) or "").strip()
            aka_ln = (aka.findtext(t("lastName")) or "").strip()
            aka_raw = f"{aka_fn} {aka_ln}".strip() if aka_fn else aka_ln
            if aka_raw:
                anorm = normalize(aka_raw)
                names.append(
                    SanctionName(
                        name_raw=aka_raw,
                        name_normalized=anorm,
                        name_tokens=tokenize(anorm),
                        is_primary=False,
                        name_type=detect_name_type(aka_raw, aka_type),
                    )
                )

        entities.append(
            SanctionEntity(
                source="OFAC",
                source_id=f"OFAC-{uid}",
                entity_type=entity_type,
                primary_name=primary_raw,
                country_focus=country,
                risk_level=risk_level,
                names=names,
                programs=programs,
            )
        )

    logger.info("OFAC – parsed %d entities", len(entities))
    return entities


def _risk_from_programs(programs: List[str]) -> RiskLevel:
    """
    Map OFAC program codes to our internal risk level.
    This is a heuristic – adjust to your compliance policy.
    """
    HIGH_RISK = {
        "SDGT", "NPWMD", "SYRIA", "DPRK", "IRAN", "HAMAS",
        "HEZBOLLAH", "IRISL", "UKRAINE-EO13685",
    }
    for p in programs:
        if any(h in p.upper() for h in HIGH_RISK):
            return RiskLevel.CRITICAL
    return RiskLevel.HIGH  # all OFAC designations are at least high
