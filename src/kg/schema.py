"""Knowledge Graph schema: Entity & Relation type definitions for fortune-telling KG."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EntityType(str, Enum):
    """Entity types in the fortune-telling knowledge graph."""

    TIAN_GAN = "天干"
    DI_ZHI = "地支"
    WU_XING = "五行"
    SHI_SHEN = "十神"
    SHEN_SHA = "神煞"
    GE_JU = "格局"
    JING_DIAN = "经典"
    MING_LI = "命例"
    JING_PIN = "竞品答案"

    def __str__(self) -> str:
        return self.value


class RelationType(str, Enum):
    """Relation types in the fortune-telling knowledge graph."""

    SHENG = "生"
    KE = "克"
    HE = "合"
    SAN_HE = "三合"
    CHONG = "冲"
    XING = "刑"
    HAI = "害"
    SHI_SHEN = "十神对应"
    GU_JI_CHU_CHU = "古籍出处"
    GE_JU_GUAN_LIAN = "格局关联"
    ZANG_GAN = "藏干"
    WU_XING_SHU_XING = "五行属性"
    SHU_XING_SHI_SHEN = "属性十神"
    XIANG_SI_AN_LI = "相似案例"
    SHU_YU = "属于"

    def __str__(self) -> str:
        return self.value


@dataclass
class Entity:
    """A node in the knowledge graph."""

    id: str
    type: EntityType
    name: str
    properties: dict[str, Any] = field(default_factory=dict)

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Entity):
            return self.id == other.id
        return NotImplemented


@dataclass
class Relation:
    """An edge in the knowledge graph."""

    id: str
    from_id: str
    to_id: str
    type: RelationType
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class Triple:
    """Subject-predicate-object triple for human-readable graph output."""

    subject: Entity
    predicate: str
    object: Entity

    def __str__(self) -> str:
        return f"({self.subject.name})--[{self.predicate}]-->({self.object.name})"
