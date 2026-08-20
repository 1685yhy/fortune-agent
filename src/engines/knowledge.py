"""命理知识解析引擎（问真知识库，L2-2）。

数据源（data/knowledge/）：
- 7jJ1_knowledge.json —— 问真 H5 知识库原档（ShiShenTips 十神心性取象 /
  ZhangShengTips 十二长生歌诀 / NaYinTips 纳音 / ShenShaTips 神煞，
  TianGan/DiZhi 仅含名表；VipLevelData 属会员档位，不使用）
- shishen_knowledge.json —— 十神详解（由 shishen_knowledge.js 逐字转换，
  字段 name/tip/gujue/shishen 与 7jJ1 ShiShenTips 完全一致，互为备份）
- tiangan_dizhi.json —— 天干/地支详解（7jJ1 仅含名表，本文件补齐：
  天干取《滴天髓》十干论歌诀 + 五行阴阳方位；地支为属相/藏干/节气/六合等）

对外接口：
    get_knowledge(category, name) -> dict | None
        命中返回 {name, tip, gujue?, ...各原始字段}；未命中/非法分类返回 None。
        category ∈ shishen / zhangsheng / nayin / shensha / tiangan / dizhi
    all_categories() -> dict[str, list[str]]
        各类别名称清单（供前端渲染可点文字，点名称再查 get_knowledge）。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

_KB = Path(__file__).resolve().parents[2] / "data" / "knowledge"

# 分类 → 数据源键/文件名（_load_all 装配后即 dict[分类][名称] = 条目 dict）
_SOURCES: Dict[str, tuple] = {
    "shishen": ("json", "7jJ1_knowledge.json", "ShiShenTips"),
    "zhangsheng": ("json", "7jJ1_knowledge.json", "ZhangShengTips"),
    "nayin": ("json", "7jJ1_knowledge.json", "NaYinTips"),
    "shensha": ("json", "7jJ1_knowledge.json", "ShenShaTips"),
    "tiangan": ("file", "tiangan_dizhi.json", "tian_gan"),
    "dizhi": ("file", "tiangan_dizhi.json", "di_zhi"),
}

# 十二长生条目无 tip 字段，正文在 chafa（查法说明），歌诀在 shijue → 规范化：
# tip = chafa（长文本说明），gujue = shijue（歌诀），原始字段一并保留。
_ZHANGSHENG_TIP = "chafa"
_ZHANGSHENG_GUJUE = "shijue"


@lru_cache(maxsize=1)
def _load_all() -> Dict[str, Dict[str, dict]]:
    """加载全部知识库（模块级缓存，只读一次）→ {分类: {名称: 条目}}。"""
    wenzhen = json.loads((_KB / "7jJ1_knowledge.json").read_text(encoding="utf-8"))
    shishen_js = json.loads(
        (_KB / "shishen_knowledge.json").read_text(encoding="utf-8"))
    gan_zhi = json.loads(
        (_KB / "tiangan_dizhi.json").read_text(encoding="utf-8"))

    out: Dict[str, Dict[str, dict]] = {}
    for cat, (kind, fname, key) in _SOURCES.items():
        if kind == "json":
            table: Dict[str, dict] = {
                it["name"]: dict(it)
                for it in wenzhen[key] if isinstance(it, dict)
            }
        else:  # "file"：tiangan_dizhi.json 为 {名称: 条目} 直接索引
            table = {
                name: dict(item)
                for name, item in gan_zhi.get(key, {}).items()
                if isinstance(item, dict)
            }
        # 十神：shishen_knowledge.json 为专档（与 7jJ1 内容一致），以其字段补全
        if cat == "shishen":
            for it in shishen_js:
                table[it["name"]] = {**table.get(it["name"], {}), **it}
        out[cat] = table
    return out


def get_knowledge(category: str, name: str) -> Optional[dict]:
    """按分类+名称查知识条目（精确匹配，不子串模糊）。

    命中返回 {name, tip, ...原始字段}（zhangsheng 规范化补 tip/gujue）；
    分类非法或名称未命中返回 None。
    """
    cat = (category or "").strip()
    key = (name or "").strip()
    if not cat or not key:
        return None
    item = _load_all().get(cat, {}).get(key)
    if item is None:
        return None
    out = dict(item)
    if "tip" not in out or not out.get("tip"):
        out["tip"] = out.get(_ZHANGSHENG_TIP, "")
    if _ZHANGSHENG_GUJUE in out and "gujue" not in out:
        out["gujue"] = out[_ZHANGSHENG_GUJUE]
    return out


def all_categories() -> Dict[str, List[str]]:
    """各类别名称清单：{category: [name, ...]}，供前端渲染可点文字。"""
    return {cat: list(table.keys()) for cat, table in _load_all().items()}
