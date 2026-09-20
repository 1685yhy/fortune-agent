#!/usr/bin/env python3
"""k49 误伤评估（k58 M-1 验收）：含该字但**不是该意象**的输入不得被误命中。

对抗集分三类：
- `different_object`：该字只是更长词的词头，词义是**另一个东西**
  （水杯/火腿/车门…）→ **必须不命中**该字对应规则；
- `absurd`：荒谬/玩梗串（蛇精病…）→ 允许命中（字面确实含该意象），但不许崩；
- `legit`：确实是该意象的口语形态（梦见大蛇/梦见被蛇追…）→ **必须命中**，
  用来防止「护栏把该命中的也挡了」的假阴性。

用法：
    python scripts/k55_dream/adversarial_check.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.k55_dream.crawl_lib import DATA_ROOT, now_iso  # noqa: E402

# (输入, 期望不命中的规则名)；期望命中的放 LEGIT
DIFFERENT_OBJECT = [
    ("梦见水杯", "水"), ("梦见水果", "水"), ("梦见水泥", "水"),
    ("梦见水龙头", "水"), ("梦见水饺", "水"), ("梦见矿泉水", "水"),
    ("梦见火腿", "火"), ("梦见火车", "火"), ("梦见火锅", "火"),
    ("梦见火星", "火"), ("梦见火药", "火"), ("梦见打火机", "火"),
    ("梦见车门", "车"), ("梦见车站", "车"), ("梦见车牌", "车"),
    ("梦见车票", "车"), ("梦见车间", "车"),
    ("梦见狗肉", "狗"), ("梦见狗熊", "狗"), ("梦见狗屁", "狗"),
    ("梦见马路", "马"), ("梦见马上", "马"), ("梦见马匹", "马"),
    ("梦见酒店", "酒"), ("梦见酒精", "酒"), ("梦见酒席", "酒"),
    ("梦见鱼雷", "鱼"), ("梦见鱼翅", "鱼"), ("梦见鱼苗", "鱼"),
    ("梦见猫头鹰", "猫"),
    ("梦见鬼话", "鬼"), ("梦见鬼子", "鬼"),
    ("梦见手巾", "手"), ("梦见手册", "手"), ("梦见手术", "手"),
    ("梦见山顶", "山"), ("梦见山羊", "山"),
    ("梦见河马", "河"), ("梦见河堤", "河"),
    ("梦见钱币", "钱"),
    ("梦见刀法", "刀"), ("梦见刀子嘴", "刀"),
]
# k58 r2（I-2）：**新词/罕见复合**（jieba 词典根本没收录的那类）——
# 基线 0 命中，必须保持 0；期望「一个规则都不命中」（含「改后命中别的字」这一类）。
NOVEL_COMPOUNDS = [
    "梦见水立方", "梦见水逆", "梦见水煮鱼", "梦见水信玄饼",
    "梦见火币", "梦见火烈鸟", "梦见狗獾", "梦见狗粮",
    "梦见手办", "梦见手冲咖啡", "梦见猫砂", "梦见猫咖", "梦见猫山王",
    "梦见鱼香肉丝", "梦见马卡龙", "梦见车模", "梦见山葵", "梦见山竹",
    "梦见河粉", "梦见猫头鹰",
]
# 期望「完全无命中」的输入（这些字只是更长词的前缀/后缀，不是梦的意象）
NO_HIT_AT_ALL = [
    ("梦见猫头鹰", None),      # M-b：曾误命中 猫，改后一度误命中 鹰
    ("梦见水立方", None), ("梦见水逆", None), ("梦见火币", None),
    ("梦见狗粮", None), ("梦见手办", None), ("梦见猫砂", None),
    ("梦见鱼香肉丝", None), ("梦见马卡龙", None), ("梦见车模", None),
]
ABSURD = [("梦见蛇精病", "蛇"), ("梦见火星人", "火")]
LEGIT = [
    ("梦见蛇", "蛇"), ("梦见大蛇", "蛇"), ("梦见被蛇追", "蛇"),
    ("梦见水", "水"), ("梦见发大水", "水"),
    ("梦见火", "火"), ("梦见大火", "火"),
    ("梦见车", "车"), ("梦见开车", "车"),
    ("梦见狗", "狗"), ("梦见被狗咬", "狗"),
    ("梦见鱼", "鱼"), ("梦见抓鱼", "鱼"),
]


class _NullRetriever:
    def search(self, query, top_k=5, **kw):
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description="k49 误伤评估")
    ap.add_argument("--out", default=str(DATA_ROOT / "reports" / "adversarial_check.json"))
    args = ap.parse_args()

    from src.engines.dream import DreamEngine
    engine = DreamEngine()

    def hit_names(text: str) -> list:
        return [h["rule"]["name"] for h in engine.match_patterns(text)]

    fp, ok, absurd = [], 0, []
    nov = [{"input": t, "hit": hit_names(t)} for t in NOVEL_COMPOUNDS]
    no_hit = [{"input": t, "hit": hit_names(t)} for t, _ in NO_HIT_AT_ALL]
    for text, banned in DIFFERENT_OBJECT:
        names = hit_names(text)
        if banned in names:
            fp.append({"input": text, "banned": banned, "hit": names})
        else:
            ok += 1
    for text, name in ABSURD:
        absurd.append({"input": text, "hit": hit_names(text)})

    # 正当形态的判据：**被覆盖**即可（规则层命中任意规则，或引擎元素层命中），
    # 不要求必须命中某个特定规则名 —— 例如「梦见大蛇」由「大蛇」规则覆盖、
    # 「梦见开车」由「开车」规则覆盖，都是正确行为（初版按规则名比，报了假漏）。
    fn = []
    for text, expect in LEGIT:
        names = hit_names(text)
        res = engine.analyze(text, _NullRetriever())
        covered = bool(names) or bool(res.elements) or (
            res.dream_type and res.symbols and res.tones)
        if not covered:
            fn.append({"input": text, "expect": expect, "hit": names,
                       "elements": res.elements})

    result = {
        "generated_at": now_iso(),
        "different_object_total": len(DIFFERENT_OBJECT),
        "different_object_clean": ok,
        "false_positives": fp,
        "novel_compounds": nov,
        "novel_compounds_hit": [x for x in nov if x["hit"]],
        "no_hit_at_all_violations": [x for x in no_hit if x["hit"]],
        "absurd_cases": absurd,
        "legit_total": len(LEGIT),
        "missed_legit": fn,
    }
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"对抗集（含该字非该意象）：{len(DIFFERENT_OBJECT)} 条，误命中 {len(fp)} 条")
    for x in fp:
        print(f"  ✗ {x['input']} → 误命中 {x['banned']}（全部命中：{x['hit']}）")
    print(f"正当口语形态：{len(LEGIT)} 条，未被覆盖 {len(fn)} 条")
    for x in fn:
        print(f"  ✗ {x['input']} → 期望覆盖 {x['expect']}，规则命中 {x['hit']}，元素 {x.get('elements')}")
    print(f"新词/罕见复合（I-2 对抗集）：{len(NOVEL_COMPOUNDS)} 条，命中 {len(result['novel_compounds_hit'])} 条")
    for x in result["novel_compounds_hit"]:
        print(f"  ✗ {x['input']} → {x['hit']}")
    print(f"期望完全无命中：{len(NO_HIT_AT_ALL)} 条，违反 {len(result['no_hit_at_all_violations'])} 条")
    for x in result["no_hit_at_all_violations"]:
        print(f"  ✗ {x['input']} → {x['hit']}")
    print(f"荒谬串：{len(ABSURD)} 条（允许命中）")
    for x in absurd:
        print(f"  · {x['input']} → {x['hit']}")
    print(f"结果 → {args.out}")
    return 0 if (not fp and not fn and not result["novel_compounds_hit"]
                 and not result["no_hit_at_all_violations"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
