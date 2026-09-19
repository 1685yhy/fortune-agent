#!/usr/bin/env python3
"""极性一致性**独立复核**（k58 r3 A-1）：用审查方的词表口径扫全表。

与 `build_rules.sentence_polarity` 相互独立（这里故意不复用它的实现），
用于回答「改后还有几条 gloss 与 luck 反向」。

判定：
- 逐条规则取 luck 极性（吉/凶/无方向）与 gloss 极性（本模块词表）；
- 两者都出方向且相反 → **反向**；
- 句子里吉凶词都出现且**句末转向正面**（末句极性为吉）→ 记为「相混（句末转正）」，可接受。

用法：
    python scripts/k55_dream/polarity_audit.py
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.k55_dream.crawl_lib import DATA_ROOT, now_iso  # noqa: E402

# 审查口径词表（比 build_rules 的实现更宽，含「光明/发展/兴旺/不佳/窝火/波折/
# 量入为出/欠顺/谨慎/多小心」等）
POS_WORDS = (
    "大吉", "吉利", "吉兆", "吉凶指数", "好运", "发财", "得财", "进财", "升官",
    "富贵", "喜事", "顺利", "成功", "贵子", "添丁", "有财", "主吉", "大吉昌",
    "光明", "发展", "兴旺", "顺遂", "幸福", "美满", "高升", "如意", "发达",
    "转运", "财运", "喜讯", "和睦", "健康",
    # r4（I-1）：补**判词位置的裸「吉」**与「大富」等 —— 旧词表没有它们，
    # 所以扫不到「梦见马，吉；乘行，大富」这类引文（"0 条反向"是词表决定的假绿）
    "大富", "大贵", "得利", "有喜",
)
# 裸「吉」必须锚定在分隔符之间（否则会命中 吉尔吉斯/吉祥物 这类无关串）
BARE_JI_RE = re.compile(r"(?<![一-鿿])吉(?![一-鿿])")
BARE_XIONG_RE = re.compile(r"(?<![一-鿿])凶(?![一-鿿])")

NEG_WORDS = (
    "大凶", "不祥", "凶兆", "灾祸", "倒霉", "损失", "破财", "疾病", "病痛",
    "死亡", "丧事", "口舌", "官司", "离别", "不顺", "小人", "血光", "主凶",
    "凶事", "有灾", "患病", "不佳", "窝火", "波折", "欠顺", "谨慎", "小心",
    "量入为出", "吃亏", "争吵", "矛盾", "担忧", "麻烦", "阻碍", "困难",
    # r4（I-1）：补负面表述
    "不幸", "不吉", "不利", "不测", "灾厄",
)
NEG_PREFIX_RE = re.compile(r"(不|没|未|难以|无法|避免|别|勿)\s*(很|太|会|能|要|可)?\s*$")
SENT_SPLIT_RE = re.compile(r"[。！？!?\n]")


def polarity(text: str) -> str:
    """吉 / 凶 / 空（双向混合且句末转正 → 吉；否则空）。"""
    pos = neg = False
    if BARE_JI_RE.search(text or ""):
        pos = True
    if BARE_XIONG_RE.search(text or ""):
        neg = True
    for w in POS_WORDS:
        for m in re.finditer(re.escape(w), text or ""):
            if NEG_PREFIX_RE.search(text[max(0, m.start() - 3):m.start()]):
                neg = True
            else:
                pos = True
    for w in NEG_WORDS:
        for m in re.finditer(re.escape(w), text or ""):
            if NEG_PREFIX_RE.search(text[max(0, m.start() - 3):m.start()]):
                pos = True          # 「不凶」→ 正面
            else:
                neg = True
    if pos and neg:
        # 句末转向正面则算「相混可接受」（审查口径），否则算无方向
        sents = [s for s in SENT_SPLIT_RE.split(text or "") if s.strip()]
        last = sents[-1] if sents else ""
        tail_pos = any(w in last for w in POS_WORDS)
        tail_neg = any(w in last for w in NEG_WORDS)
        return "吉" if tail_pos and not tail_neg else ""
    return "吉" if pos else ("凶" if neg else "")


def luck_polarity(luck: str) -> str:
    if luck in ("大吉", "吉", "吉多于凶"):
        return "吉"
    if luck in ("凶", "凶多于吉"):
        return "凶"
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="gloss/luck 极性一致性独立复核")
    ap.add_argument("--out", default=str(DATA_ROOT / "reports" / "polarity_audit.json"))
    args = ap.parse_args()

    from src.engines.dream_rules import DREAM_PATTERN_RULES

    directional, conflicts, mixed = 0, [], []
    for r in DREAM_PATTERN_RULES:
        lp = luck_polarity(r["luck"])
        gp = polarity(r["gloss"])
        if not lp:
            continue
        directional += 1
        if gp and gp != lp:
            conflicts.append({"name": r["name"], "luck": r["luck"],
                              "gloss_polarity": gp, "gloss": r["gloss"][:60]})
    out = {"generated_at": now_iso(), "rules": len(DREAM_PATTERN_RULES),
           "directional_rules": directional,
           "conflicts": conflicts, "conflict_count": len(conflicts)}
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"全表 {len(DREAM_PATTERN_RULES)} 条；出方向 {directional} 条")
    print(f"**反向（审查口径）：{len(conflicts)} 条**")
    for c in conflicts:
        print(f"  ✗ {c['name']}: luck={c['luck']} gloss极性={c['gloss_polarity']} | {c['gloss']}")
    print(f"明细 → {args.out}")
    return 0 if not conflicts else 1


if __name__ == "__main__":
    raise SystemExit(main())
