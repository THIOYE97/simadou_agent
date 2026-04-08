from dataclasses import dataclass, field
from typing import List, Optional
from enum import Enum


class EntityType(str, Enum):
    INDIVIDUAL = "individual"
    ENTITY = "entity"        # organisation / company
    VESSEL = "vessel"
    AIRCRAFT = "aircraft"
    UNKNOWN = "unknown"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class SanctionName:
    name_raw: str
    name_normalized: str
    name_tokens: List[str]
    is_primary: bool
    name_type: str   # e.g. "primary", "alias", "aka", "maiden", ...


@dataclass
class SanctionEntity:
    source: str                      # "UN" | "OFAC" | "EU"
    source_id: str                   # original list ID
    entity_type: EntityType
    primary_name: str
    country_focus: Optional[str]
    risk_level: RiskLevel
    names: List[SanctionName] = field(default_factory=list)
    programs: List[str] = field(default_factory=list)
    remarks: Optional[str] = None
