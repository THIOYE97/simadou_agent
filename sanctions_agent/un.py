"""
UN Security Council Consolidated Sanctions List fetcher.
Source: https://scsanctions.un.org/resources/xml/en/consolidated.xml
"""
import logging
from typing import List
from xml.etree import ElementTree as ET

import httpx

from .config import UN_LIST_URL
from .http_client import fetch_bytes
from .models import EntityType, RiskLevel, SanctionEntity, SanctionName
from .normalizer import normalize, tokenize

logger = logging.getLogger(__name__)


def fetch() -> List[SanctionEntity]:
    logger.info("UN – downloading consolidated list …")
    content = fetch_bytes(UN_LIST_URL, source="UN")
    if content is None:
        return []

    root = ET.fromstring(content)
    entities: List[SanctionEntity] = []

    for individual in root.iter("INDIVIDUAL"):
        uid = (individual.findtext("DATAID") or "").strip()
        first = (individual.findtext("FIRST_NAME") or "").strip()
        second = (individual.findtext("SECOND_NAME") or "").strip()
        third = (individual.findtext("THIRD_NAME") or "").strip()
        fourth = (individual.findtext("FOURTH_NAME") or "").strip()
        primary_raw = " ".join(filter(None, [first, second, third, fourth]))

        if not primary_raw:
            continue

        country = (
            individual.findtext("NATIONALITY/VALUE")
            or individual.findtext("INDIVIDUAL_ADDRESS/COUNTRY_OF_BIRTH")
            or individual.findtext("INDIVIDUAL_ADDRESS/COUNTRY")
        )

        names = _build_individual_names(primary_raw, individual)

        entities.append(
            SanctionEntity(
                source="UN",
                source_id=f"UN-{uid}",
                entity_type=EntityType.INDIVIDUAL,
                primary_name=primary_raw,
                country_focus=country,
                risk_level=RiskLevel.CRITICAL,  # UN list = highest risk
                names=names,
            )
        )

    for entity in root.iter("ENTITY"):
        uid = (entity.findtext("DATAID") or "").strip()
        primary_raw = (entity.findtext("FIRST_NAME") or "").strip()

        if not primary_raw:
            continue

        country = (
            entity.findtext("ENTITY_ADDRESS/COUNTRY")
            or entity.findtext("ENTITY_ADDRESS/CITY")
        )

        names = _build_entity_names(primary_raw, entity)

        entities.append(
            SanctionEntity(
                source="UN",
                source_id=f"UN-{uid}",
                entity_type=EntityType.ENTITY,
                primary_name=primary_raw,
                country_focus=country,
                risk_level=RiskLevel.CRITICAL,
                names=names,
            )
        )

    logger.info("UN – parsed %d entities", len(entities))
    return entities


def _build_individual_names(primary_raw: str, node: ET.Element) -> List[SanctionName]:
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
    for alias in node.iter("INDIVIDUAL_ALIAS"):
        quality = (alias.findtext("QUALITY") or "").lower()
        alias_name = (alias.findtext("ALIAS_NAME") or "").strip()
        if alias_name:
            anorm = normalize(alias_name)
            names.append(
                SanctionName(
                    name_raw=alias_name,
                    name_normalized=anorm,
                    name_tokens=tokenize(anorm),
                    is_primary=False,
                    name_type="low_quality" if "low" in quality else "aka",
                )
            )
    return names


def _build_entity_names(primary_raw: str, node: ET.Element) -> List[SanctionName]:
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
    for alias in node.iter("ENTITY_ALIAS"):
        quality = (alias.findtext("QUALITY") or "").lower()
        alias_name = (alias.findtext("ALIAS_NAME") or "").strip()
        if alias_name:
            anorm = normalize(alias_name)
            names.append(
                SanctionName(
                    name_raw=alias_name,
                    name_normalized=anorm,
                    name_tokens=tokenize(anorm),
                    is_primary=False,
                    name_type="low_quality" if "low" in quality else "aka",
                )
            )
    return names
