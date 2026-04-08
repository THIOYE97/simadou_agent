"""
EU Financial Sanctions File (FSF) fetcher.
Source: https://webgate.ec.europa.eu/fsd/fsf
Format: XML (FullSanctionsList_1_1)
"""
import logging
from typing import List, Optional
from xml.etree import ElementTree as ET

import httpx

from .config import EU_LIST_URL
from .http_client import fetch_bytes
from .models import EntityType, RiskLevel, SanctionEntity, SanctionName
from .normalizer import normalize, tokenize

logger = logging.getLogger(__name__)

_SUBJECT_TYPE_MAP = {
    "person": EntityType.INDIVIDUAL,
    "enterprise": EntityType.ENTITY,
    "bank": EntityType.ENTITY,
    "vessel": EntityType.VESSEL,
    "aircraft": EntityType.AIRCRAFT,
}


def fetch() -> List[SanctionEntity]:
    logger.info("EU – downloading FSF list …")
    content = fetch_bytes(EU_LIST_URL, source="EU")
    if content is None:
        return []

    root = ET.fromstring(content)
    entities: List[SanctionEntity] = []

    for sanction in root.iter("sanctionEntity"):
        uid = sanction.get("euReferenceNumber") or sanction.get("logicalId") or ""

        # Subject type
        raw_type = ""
        st_node = sanction.find("subjectType")
        if st_node is not None:
            raw_type = (st_node.get("classificationCode") or "").lower()
        entity_type = _SUBJECT_TYPE_MAP.get(raw_type, EntityType.UNKNOWN)

        # Names
        names: List[SanctionName] = []
        primary_raw: Optional[str] = None

        for name_alias in sanction.iter("nameAlias"):
            is_primary = name_alias.get("strong", "false").lower() == "true"
            full_name = (name_alias.get("wholeName") or "").strip()
            if not full_name:
                # Try to build from parts
                parts = [
                    name_alias.get("firstName") or "",
                    name_alias.get("middleName") or "",
                    name_alias.get("lastName") or "",
                ]
                full_name = " ".join(p.strip() for p in parts if p.strip())

            if not full_name:
                continue

            if is_primary and primary_raw is None:
                primary_raw = full_name

            fnorm = normalize(full_name)
            names.append(
                SanctionName(
                    name_raw=full_name,
                    name_normalized=fnorm,
                    name_tokens=tokenize(fnorm),
                    is_primary=is_primary,
                    name_type="primary" if is_primary else "aka",
                )
            )

        if not names:
            continue
        if primary_raw is None:
            # Fallback: first name becomes primary
            names[0].is_primary = True
            primary_raw = names[0].name_raw

        # Country
        country = None
        for citizenship in sanction.iter("citizenship"):
            country = citizenship.get("countryIso2Code") or citizenship.get("country")
            if country:
                break
        if not country:
            for addr in sanction.iter("address"):
                country = addr.get("countryIso2Code") or addr.get("country")
                if country:
                    break

        # Regulation → risk (all EU FSF = high, ISIL/Al-Qaida = critical)
        risk = RiskLevel.HIGH
        for reg in sanction.iter("regulation"):
            prog = (reg.get("programme") or reg.get("numberTitle") or "").upper()
            if any(k in prog for k in ("ISIL", "AL-QA", "DAESH", "TALIBAN")):
                risk = RiskLevel.CRITICAL
                break

        entities.append(
            SanctionEntity(
                source="EU",
                source_id=f"EU-{uid}",
                entity_type=entity_type,
                primary_name=primary_raw,
                country_focus=country,
                risk_level=risk,
                names=names,
            )
        )

    logger.info("EU – parsed %d entities", len(entities))
    return entities
