"""KG builders: construct entities and relations from various sources.

Builders produce lists of Entity/Relation objects that can be bulk-loaded
into the GraphStore.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from src.kg.schema import Entity, EntityType, Relation, RelationType

# ---------------------------------------------------------------------------
# Static data: 天干, 地支, 五行, 十神, 神煞, 格局
# ---------------------------------------------------------------------------

TIAN_GAN = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
TIAN_GAN_YIN_YANG = {
    "甲": "阳", "乙": "阴", "丙": "阳", "丁": "阴", "戊": "阳",
    "己": "阴", "庚": "阳", "辛": "阴", "壬": "阳", "癸": "阴",
}
TIAN_GAN_WU_XING = {
    "甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
    "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水",
}

DI_ZHI = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
DI_ZHI_YIN_YANG = {
    "子": "阳", "丑": "阴", "寅": "阳", "卯": "阴", "辰": "阳", "巳": "阴",
    "午": "阳", "未": "阴", "申": "阳", "酉": "阴", "戌": "阳", "亥": "阴",
}
DI_ZHI_WU_XING = {
    "子": "水", "丑": "土", "寅": "木", "卯": "木", "辰": "土", "巳": "火",
    "午": "火", "未": "土", "申": "金", "酉": "金", "戌": "土", "亥": "水",
}
# 地支藏干: 地支 -> list of 天干
DI_ZHI_CANG_GAN: dict[str, list[str]] = {
    "子": ["癸"],
    "丑": ["己", "癸", "辛"],
    "寅": ["甲", "丙", "戊"],
    "卯": ["乙"],
    "辰": ["戊", "乙", "癸"],
    "巳": ["丙", "戊", "庚"],
    "午": ["丁", "己"],
    "未": ["己", "丁", "乙"],
    "申": ["庚", "壬", "戊"],
    "酉": ["辛"],
    "戌": ["戊", "辛", "丁"],
    "亥": ["壬", "甲"],
}

WU_XING = ["金", "木", "水", "火", "土"]
WU_XING_SHENG: dict[str, list[str]] = {
    "木": ["火"],
    "火": ["土"],
    "土": ["金"],
    "金": ["水"],
    "水": ["木"],
}
WU_XING_KE: dict[str, list[str]] = {
    "木": ["土"],
    "土": ["水"],
    "水": ["火"],
    "火": ["金"],
    "金": ["木"],
}
WU_XING_SHENG_CYCLE = ["木", "火", "土", "金", "水"]  # 木生火, 火生土, ...

SHI_SHEN = [
    "比肩", "劫财", "食神", "伤官", "偏财",
    "正财", "七杀", "正官", "偏印", "正印",
]
SHI_SHEN_DESC = {
    "比肩": "与日主同五行同阴阳",
    "劫财": "与日主同五行异阴阳",
    "食神": "日主所生,同阴阳",
    "伤官": "日主所生,异阴阳",
    "偏财": "日主所克,同阴阳",
    "正财": "日主所克,异阴阳",
    "七杀": "克日主,同阴阳",
    "正官": "克日主,异阴阳",
    "偏印": "生日主,同阴阳",
    "正印": "生日主,异阴阳",
}

# 天干合化
TIAN_GAN_HE: dict[tuple[str, str], str] = {
    ("甲", "己"): "土",
    ("乙", "庚"): "金",
    ("丙", "辛"): "水",
    ("丁", "壬"): "木",
    ("戊", "癸"): "火",
}

# 地支六合
DI_ZHI_LIU_HE: dict[tuple[str, str], str] = {
    ("子", "丑"): "土",
    ("寅", "亥"): "木",
    ("卯", "戌"): "火",
    ("辰", "酉"): "金",
    ("巳", "申"): "水",
    ("午", "未"): "土",
}

# 地支三合
DI_ZHI_SAN_HE: dict[tuple[str, str, str], str] = {
    ("申", "子", "辰"): "水",
    ("亥", "卯", "未"): "木",
    ("寅", "午", "戌"): "火",
    ("巳", "酉", "丑"): "金",
}

# 地支六冲
DI_ZHI_CHONG: list[tuple[str, str]] = [
    ("子", "午"), ("丑", "未"), ("寅", "申"),
    ("卯", "酉"), ("辰", "戌"), ("巳", "亥"),
]

# 地支相刑
DI_ZHI_XING: list[tuple[str, ...]] = [
    ("子", "卯"),  # 无礼之刑
    ("寅", "巳", "申"),  # 无恩之刑
    ("丑", "未", "戌"),  # 恃势之刑
    ("辰",), ("午",), ("酉",), ("亥",),  # 自刑
]

# 地支相害
DI_ZHI_HAI: list[tuple[str, str]] = [
    ("子", "未"), ("丑", "午"), ("寅", "巳"),
    ("卯", "辰"), ("申", "亥"), ("酉", "戌"),
]

SHEN_SHA = [
    "天乙贵人", "太极贵人", "天德贵人", "月德贵人", "福星贵人",
    "文昌贵人", "国印贵人", "学堂", "词馆", "驿马",
    "桃花", "孤辰", "寡宿", "亡神", "劫煞",
    "灾煞", "勾煞", "绞煞", "华盖", "天罗地网",
    "天医", "金舆", "天喜", "红鸾", "禄神",
    "羊刃", "阴阳差错", "十恶大败", "空亡", "童子煞",
    "德秀贵人", "三奇贵人", "魁罡贵人", "红艳", "流霞",
    "元辰", "暗金的煞", "孤鸾煞", "四废", "咸池",
    "陌越", "大耗", "小耗", "龙德", "紫微",
    "禄元互换", "生成官星", "生成印星", "天赦", "月空",
]

GE_JU = [
    "正官格", "七杀格", "正印格", "偏印格", "正财格",
    "偏财格", "食神格", "伤官格", "比肩格", "劫财格",
    "建禄格", "归禄格", "日刃格", "羊刃格", "从财格",
    "从官格", "从杀格", "从旺格", "从弱格", "化气格",
    "曲直格", "炎上格", "稼穑格", "从革格", "润下格",
    "杀印相生格", "食神制杀格", "伤官配印格", "伤官见官格", "官印相生格",
    "财官双美格", "财生官格", "杀邀食制格", "枭神夺食格", "比劫夺财格",
    "金神格", "魁罡格", "六甲趋乾格", "六乙鼠贵格", "六壬趋艮格",
    "六辛朝阳格", "日贵格", "日德格", "福德格", "禄马同乡格",
    "三奇格", "天元一气格", "地元一气格", "天干顺食格", "地支连珠格",
    "两神成象格", "一将当关格", "岁德扶杀格", "岁德扶财格", "杀重身轻格",
    "身强杀浅格", "身弱财旺格", "身旺财弱格", "食神吐秀格", "印绶护身格",
    "金白水清格", "木火通明格", "水土混杂格", "火炎土燥格", "水冷金寒格",
    "木盛仁寿格", "火明礼丰格", "土厚德载格", "金主义重格", "水智润物格",
]

# 十神对应表: (日主, 其他天干) -> 十神名称
def _build_shi_shen_table() -> dict[tuple[str, str], str]:
    """Build full 十神 correspondence table.

    比肩: same wuxing, same yinyang
    劫财: same wuxing, opposite yinyang
    食神: I-generate, same yinyang
    伤官: I-generate, opposite yinyang
    偏财: I-control, same yinyang
    正财: I-control, opposite yinyang
    七杀: controls-me, same yinyang
    正官: controls-me, opposite yinyang
    偏印: generates-me, same yinyang
    正印: generates-me, opposite yinyang
    """
    table: dict[tuple[str, str], str] = {}
    # wuxing index
    wx_cycle = ["木", "火", "土", "金", "水"]  # 木生火, 火生土, ...

    for ri_zhu in TIAN_GAN:
        r_wx = TIAN_GAN_WU_XING[ri_zhu]
        r_yy = TIAN_GAN_YIN_YANG[ri_zhu]
        r_wx_idx = wx_cycle.index(r_wx)

        for other in TIAN_GAN:
            o_wx = TIAN_GAN_WU_XING[other]
            o_yy = TIAN_GAN_YIN_YANG[other]
            o_wx_idx = wx_cycle.index(o_wx)

            if r_wx == o_wx:
                # same element
                table[(ri_zhu, other)] = "比肩" if r_yy == o_yy else "劫财"
            elif wx_cycle[(r_wx_idx + 1) % 5] == o_wx:
                # I generate (生我生之物)
                # 木生火: 甲(木)生丙(火) → 食神/伤官
                table[(ri_zhu, other)] = "食神" if r_yy == o_yy else "伤官"
            elif wx_cycle[(r_wx_idx + 2) % 5] == o_wx:
                # I control (我克)
                # 木克土: 甲(木)克戊(土) → 偏财/正财
                table[(ri_zhu, other)] = "偏财" if r_yy == o_yy else "正财"
            elif wx_cycle[(r_wx_idx - 1) % 5] == o_wx:
                # generates me (生我)
                # 水生木: 癸(水)生甲(木) → 正印/偏印
                table[(ri_zhu, other)] = "偏印" if r_yy == o_yy else "正印"
            elif wx_cycle[(r_wx_idx - 2) % 5] == o_wx:
                # controls me (克我)
                # 金克木: 庚(金)克甲(木) → 七杀/正官
                table[(ri_zhu, other)] = "七杀" if r_yy == o_yy else "正官"

    return table


SHI_SHEN_TABLE = _build_shi_shen_table()


def _entity_id(prefix: str, name: str) -> str:
    return f"{prefix}_{name}"


# ---------------------------------------------------------------------------
# StaticEntityBuilder
# ---------------------------------------------------------------------------

class StaticEntityBuilder:
    """Build entities for static fortune-telling concepts.

    These include 天干 (10), 地支 (12), 五行 (5), 十神 (10),
    神煞 (~50), and 格局 (~70).
    """

    def __init__(self) -> None:
        self.entities: list[Entity] = []

    def build(self) -> list[Entity]:
        self.entities = []
        self._build_tian_gan()
        self._build_di_zhi()
        self._build_wu_xing()
        self._build_shi_shen()
        self._build_shen_sha()
        self._build_ge_ju()
        return self.entities

    def _build_tian_gan(self) -> None:
        for g in TIAN_GAN:
            self.entities.append(Entity(
                id=_entity_id("tg", g),
                type=EntityType.TIAN_GAN,
                name=g,
                properties={
                    "yin_yang": TIAN_GAN_YIN_YANG[g],
                    "wu_xing": TIAN_GAN_WU_XING[g],
                },
            ))

    def _build_di_zhi(self) -> None:
        for z in DI_ZHI:
            self.entities.append(Entity(
                id=_entity_id("dz", z),
                type=EntityType.DI_ZHI,
                name=z,
                properties={
                    "yin_yang": DI_ZHI_YIN_YANG[z],
                    "wu_xing": DI_ZHI_WU_XING[z],
                    "cang_gan": DI_ZHI_CANG_GAN[z],
                },
            ))

    def _build_wu_xing(self) -> None:
        for wx in WU_XING:
            self.entities.append(Entity(
                id=_entity_id("wx", wx),
                type=EntityType.WU_XING,
                name=wx,
                properties={},
            ))

    def _build_shi_shen(self) -> None:
        for ss in SHI_SHEN:
            self.entities.append(Entity(
                id=_entity_id("ss", ss),
                type=EntityType.SHI_SHEN,
                name=ss,
                properties={"description": SHI_SHEN_DESC.get(ss, "")},
            ))

    def _build_shen_sha(self) -> None:
        for sh in SHEN_SHA:
            self.entities.append(Entity(
                id=_entity_id("shen", sh),
                type=EntityType.SHEN_SHA,
                name=sh,
                properties={},
            ))

    def _build_ge_ju(self) -> None:
        for gj in GE_JU:
            self.entities.append(Entity(
                id=_entity_id("geju", gj),
                type=EntityType.GE_JU,
                name=gj,
                properties={},
            ))


# ---------------------------------------------------------------------------
# BaziRelationBuilder
# ---------------------------------------------------------------------------

class BaziRelationBuilder:
    """Build relations between static fortune-telling entities.

    These include 生克合冲刑害 between 天干地支, 五行属性 relations,
    and 十神对应 tables.
    """

    def __init__(self) -> None:
        self.relations: list[Relation] = []
        self._counter = 0

    def _rel_id(self) -> str:
        self._counter += 1
        return f"sr_{self._counter:04d}"

    def build(self) -> list[Relation]:
        self.relations = []
        self._counter = 0
        self._build_tg_wuxing()       # 天干->五行
        self._build_dz_wuxing()       # 地支->五行
        self._build_wuxing_sheng()    # 五行相生
        self._build_wuxing_ke()       # 五行相克
        self._build_tg_he()           # 天干五合
        self._build_dz_liuhe()        # 地支六合
        self._build_dz_sanhe()        # 地支三合
        self._build_dz_chong()        # 地支六冲
        self._build_dz_xing()         # 地支相刑
        self._build_dz_hai()          # 地支相害
        self._build_dz_canggan()      # 地支藏干
        self._build_shi_shen_rel()    # 十神对应
        return self.relations

    def _build_tg_wuxing(self) -> None:
        for g in TIAN_GAN:
            wx = TIAN_GAN_WU_XING[g]
            self.relations.append(Relation(
                id=self._rel_id(),
                from_id=_entity_id("tg", g),
                to_id=_entity_id("wx", wx),
                type=RelationType.WU_XING_SHU_XING,
                properties={"relation": "所属五行"},
            ))

    def _build_dz_wuxing(self) -> None:
        for z in DI_ZHI:
            wx = DI_ZHI_WU_XING[z]
            self.relations.append(Relation(
                id=self._rel_id(),
                from_id=_entity_id("dz", z),
                to_id=_entity_id("wx", wx),
                type=RelationType.WU_XING_SHU_XING,
                properties={"relation": "所属五行"},
            ))

    def _build_wuxing_sheng(self) -> None:
        for i, wx in enumerate(WU_XING_SHENG_CYCLE):
            next_wx = WU_XING_SHENG_CYCLE[(i + 1) % 5]
            self.relations.append(Relation(
                id=self._rel_id(),
                from_id=_entity_id("wx", wx),
                to_id=_entity_id("wx", next_wx),
                type=RelationType.SHENG,
                properties={},
            ))

    def _build_wuxing_ke(self) -> None:
        for wx, targets in WU_XING_KE.items():
            for target in targets:
                self.relations.append(Relation(
                    id=self._rel_id(),
                    from_id=_entity_id("wx", wx),
                    to_id=_entity_id("wx", target),
                    type=RelationType.KE,
                    properties={},
                ))

    def _build_tg_he(self) -> None:
        for (g1, g2), hua in TIAN_GAN_HE.items():
            self.relations.append(Relation(
                id=self._rel_id(),
                from_id=_entity_id("tg", g1),
                to_id=_entity_id("tg", g2),
                type=RelationType.HE,
                properties={"合化": hua},
            ))

    def _build_dz_liuhe(self) -> None:
        for (z1, z2), hua in DI_ZHI_LIU_HE.items():
            self.relations.append(Relation(
                id=self._rel_id(),
                from_id=_entity_id("dz", z1),
                to_id=_entity_id("dz", z2),
                type=RelationType.HE,
                properties={"合化": hua, "type": "六合"},
            ))

    def _build_dz_sanhe(self) -> None:
        for triple, hua in DI_ZHI_SAN_HE.items():
            z1, z2, z3 = triple
            label = f"{z1}{z2}{z3}合{hua}"
            self.relations.append(Relation(
                id=self._rel_id(),
                from_id=_entity_id("dz", z1),
                to_id=_entity_id("dz", z2),
                type=RelationType.SAN_HE,
                properties={"合局": label, "合化": hua, "组": f"{z1}{z2}{z3}"},
            ))
            self.relations.append(Relation(
                id=self._rel_id(),
                from_id=_entity_id("dz", z2),
                to_id=_entity_id("dz", z3),
                type=RelationType.SAN_HE,
                properties={"合局": label, "合化": hua, "组": f"{z1}{z2}{z3}"},
            ))
            self.relations.append(Relation(
                id=self._rel_id(),
                from_id=_entity_id("dz", z3),
                to_id=_entity_id("dz", z1),
                type=RelationType.SAN_HE,
                properties={"合局": label, "合化": hua, "组": f"{z1}{z2}{z3}"},
            ))

    def _build_dz_chong(self) -> None:
        for z1, z2 in DI_ZHI_CHONG:
            self.relations.append(Relation(
                id=self._rel_id(),
                from_id=_entity_id("dz", z1),
                to_id=_entity_id("dz", z2),
                type=RelationType.CHONG,
                properties={},
            ))

    def _build_dz_xing(self) -> None:
        for group in DI_ZHI_XING:
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    self.relations.append(Relation(
                        id=self._rel_id(),
                        from_id=_entity_id("dz", group[i]),
                        to_id=_entity_id("dz", group[j]),
                        type=RelationType.XING,
                        properties={"group": "".join(group)},
                    ))

    def _build_dz_hai(self) -> None:
        for z1, z2 in DI_ZHI_HAI:
            self.relations.append(Relation(
                id=self._rel_id(),
                from_id=_entity_id("dz", z1),
                to_id=_entity_id("dz", z2),
                type=RelationType.HAI,
                properties={},
            ))

    def _build_dz_canggan(self) -> None:
        for dz, cg_list in DI_ZHI_CANG_GAN.items():
            for tg in cg_list:
                self.relations.append(Relation(
                    id=self._rel_id(),
                    from_id=_entity_id("dz", dz),
                    to_id=_entity_id("tg", tg),
                    type=RelationType.ZANG_GAN,
                    properties={},
                ))

    def _build_shi_shen_rel(self) -> None:
        """Create 十神对应 relations between 天干 pairs and 十神 entities."""
        for (rizhu, other), ss_name in SHI_SHEN_TABLE.items():
            # Link: 天干A --[十神对应, {日主: B}]--> 十神
            self.relations.append(Relation(
                id=self._rel_id(),
                from_id=_entity_id("tg", rizhu),
                to_id=_entity_id("ss", ss_name),
                type=RelationType.SHI_SHEN,
                properties={"target_stem": other, "day_master": rizhu},
            ))


# ---------------------------------------------------------------------------
# ChartEntityBuilder
# ---------------------------------------------------------------------------

class ChartEntityBuilder:
    """Extract entities and relations from 问真命盘 charts.

    Reads from the SQLite wenzhen_charts.db and creates:
      - 命例 entity per chart
      - Relations from chart to its constituent 天干/地支/神煞/十神

    Auto-creates placeholder entities for 神煞 or other items not
    already in the static lists, so FK constraints are never violated.
    """

    def __init__(self, db_path: str = "data/wenzhen_charts.db"):
        self.db_path = db_path
        self.entities: list[Entity] = []
        self.relations: list[Relation] = []
        # Track known entity IDs to avoid dupes and FK issues
        self._known_ids: set[str] = set()

    def build(self, limit: int = 500) -> tuple[list[Entity], list[Relation]]:
        self.entities = []
        self.relations = []
        self._known_ids = set()
        db = Path(self.db_path)
        if not db.exists():
            return self.entities, self.relations

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM charts ORDER BY id LIMIT ?", (limit,)
        ).fetchall()
        conn.close()

        for row in rows:
            self._process_chart(dict(row))

        return self.entities, self.relations

    def _ensure_static_entity(self, entity_id: str) -> None:
        """Create a minimal placeholder if the entity ID doesn't exist yet."""
        if entity_id in self._known_ids:
            return
        self._known_ids.add(entity_id)
        # Determine type from prefix
        prefix = entity_id.split("_")[0]
        name = entity_id[len(prefix) + 1 :]
        type_map = {
            "tg": EntityType.TIAN_GAN,
            "dz": EntityType.DI_ZHI,
            "wx": EntityType.WU_XING,
            "ss": EntityType.SHI_SHEN,
            "shen": EntityType.SHEN_SHA,
            "geju": EntityType.GE_JU,
        }
        etype = type_map.get(prefix)
        if etype is not None:
            self.entities.append(Entity(
                id=entity_id,
                type=etype,
                name=name,
                properties={},
            ))

    def _add_rel(self, rel_id: str, from_id: str, to_id: str, rtype: RelationType, **props: Any) -> None:
        """Add a relation, ensuring both endpoints exist as entities."""
        self._ensure_static_entity(from_id)
        self._ensure_static_entity(to_id)
        self.relations.append(Relation(
            id=rel_id,
            from_id=from_id,
            to_id=to_id,
            type=rtype,
            properties=props,
        ))

    def _process_chart(self, row: dict[str, Any]) -> None:
        chart_id = f"chart_{row['id']}"
        chart_entity = Entity(
            id=chart_id,
            type=EntityType.MING_LI,
            name=f"命盘{row['id']}",
            properties={
                "date": row.get("date", ""),
                "time": row.get("time", ""),
                "gender": row.get("gender", ""),
                "year_pillar": row.get("year_pillar", ""),
                "month_pillar": row.get("month_pillar", ""),
                "day_pillar": row.get("day_pillar", ""),
                "hour_pillar": row.get("hour_pillar", ""),
                "day_master": row.get("day_master", ""),
                "day_branch": row.get("day_branch", ""),
                "source": "问真八字API",
            },
        )
        self.entities.append(chart_entity)
        self._known_ids.add(chart_id)

        # Link pillar 天干/地支 to static entities
        pillars = {
            "年柱": row.get("year_pillar", ""),
            "月柱": row.get("month_pillar", ""),
            "日柱": row.get("day_pillar", ""),
            "时柱": row.get("hour_pillar", ""),
        }
        for pillar_name, pillar_str in pillars.items():
            if len(pillar_str) >= 2:
                tg = pillar_str[0]
                dz = pillar_str[1]
                self._add_rel(
                    f"rel_{chart_id}_{pillar_name}_tg", chart_id, _entity_id("tg", tg),
                    RelationType.SHU_YU, role=f"{pillar_name}天干",
                )
                self._add_rel(
                    f"rel_{chart_id}_{pillar_name}_dz", chart_id, _entity_id("dz", dz),
                    RelationType.SHU_YU, role=f"{pillar_name}地支",
                )

        # Link day master
        day_master = row.get("day_master", "")
        if day_master:
            self._add_rel(
                f"rel_{chart_id}_daymaster", chart_id, _entity_id("tg", day_master),
                RelationType.SHU_YU, role="日主",
            )

        # Link 神煞
        shensha_str = row.get("shensha", "")
        if shensha_str:
            try:
                shensha_list = json.loads(shensha_str) if isinstance(shensha_str, str) else shensha_str
                for i, ss in enumerate(shensha_list):
                    self._add_rel(
                        f"rel_{chart_id}_shensha_{i}", chart_id, _entity_id("shen", ss),
                        RelationType.SHU_YU, role="神煞",
                    )
            except (json.JSONDecodeError, TypeError):
                pass

        # Link 十神
        shishen_str = row.get("shishen", "")
        if shishen_str:
            try:
                shishen_list = json.loads(shishen_str) if isinstance(shishen_str, str) else shishen_str
                for i, ss in enumerate(shishen_list):
                    self._add_rel(
                        f"rel_{chart_id}_shishen_{i}", chart_id, _entity_id("ss", ss),
                        RelationType.SHI_SHEN,
                        position=["年", "月", "日", "时"][i] if i < 4 else f"p{i}",
                    )
            except (json.JSONDecodeError, TypeError):
                pass

        # Store full_text for later retrieval
        full_text = row.get("full_text", "")
        if full_text:
            chart_entity.properties["full_text"] = full_text[:500]


# ---------------------------------------------------------------------------
# ClassicBuilder
# ---------------------------------------------------------------------------

class ClassicBuilder:
    """Extract entities and citations from knowledge text / classic books.

    Sources:
      - data/competitor_data/*.jsonl (competitor Q&A)
      - data/wenzhen/*.jsonl (full chart text)
      - data/celebrity_cases.json (celebrity case studies)
    """

    def __init__(self) -> None:
        self.entities: list[Entity] = []
        self.relations: list[Relation] = []

    def build(self, data_dir: str = "data") -> tuple[list[Entity], list[Relation]]:
        self.entities = []
        self.relations = []
        data_path = Path(data_dir)

        # Process competitor jsonl files
        comp_dir = data_path / "competitor_data"
        if comp_dir.exists():
            for f in sorted(comp_dir.glob("*.jsonl")):
                platform = f.stem.replace("_20260719", "").replace("_deep", "").replace("_max", "")
                self._process_competitor_jsonl(f, platform)

        # Process celebrity cases
        celeb_file = data_path / "celebrity_cases.json"
        if celeb_file.exists():
            self._process_celebrity_cases(celeb_file)

        return self.entities, self.relations

    def _process_competitor_jsonl(self, path: Path, platform: str) -> None:
        """Process a JSONL file of competitor Q&A data."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    query = record.get("query", "")
                    response = record.get("response", "")
                    if not response:
                        continue

                    # Create a unique entity ID
                    content_hash = hashlib.md5(response[:200].encode("utf-8")).hexdigest()[:12]
                    entity_id = f"comp_{platform}_{content_hash}"

                    entity = Entity(
                        id=entity_id,
                        type=EntityType.JING_PIN,
                        name=f"{platform}: {query[:40]}",
                        properties={
                            "platform": platform,
                            "query": query[:200],
                            "response": response[:2000],
                            "url": record.get("url", ""),
                        },
                    )
                    self.entities.append(entity)

        except (OSError, IsADirectoryError):
            pass

    def _process_celebrity_cases(self, path: Path) -> None:
        """Process celebrity case studies JSON."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for item in data:
                    name = item.get("name", "") or item.get("title", "")
                    if not name:
                        continue
                    entity_id = f"celeb_{hashlib.md5(name.encode('utf-8')).hexdigest()[:12]}"
                    entity = Entity(
                        id=entity_id,
                        type=EntityType.MING_LI,
                        name=name,
                        properties={
                            "source": "celebrity_cases",
                            "data": json.dumps(item, ensure_ascii=False)[:2000],
                        },
                    )
                    self.entities.append(entity)
        except (OSError, json.JSONDecodeError):
            pass
