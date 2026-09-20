#!/usr/bin/env python3
"""强档成色敏感性检查（k58 r5，控制方裁决二）。

背景：r5 新增「**零反例 且 唯一句 ≥3** 即可给强档」条款（`MIN_UNANIMOUS_FOR_STRONG`），
用来把 `猫`（唯一句 0:4）这类**一致无死角**的规则从温和档扶正。控制方指出风险：

  「零反例」依赖**词表的灵敏度** —— 计数用的是**窄词表**（判词术语 JI_STRONG/XIONG_STRONG），
  探测器看不见的负面表述会被误当成「真的没有反例」。

所以这里用**宽词表**（`sentence_polarity`，即一致性校验用的那份：含否定式 +
损失/不利/慎防/生死/是非/口舌 等泛化负面词）当**反例嗅探器**，对同一批句池做敏感性检查：

  · 宽表方向一致 **且反例为 0** → 标 **稳健**（零反例在宽表下也成立）
  · 宽表方向一致 **但嗅出反向反例** → 标 **反例（零反例不成立）**（强档成色打折）
  · 宽表方向**相反** → 标 **依赖窄词表**（这条强档只活在窄词表里）

注意：宽词表**不适合当计数仪表**（它会把 `黄金`/`老虎`/`拉屎` 误判成凶，
见 r5 报告诚实披露 4），这里只把它当**单向的**反例嗅探器用。

用法：
  TMPDIR=/dev/shm nice -n 10 python3 scripts/k55_dream/strong_band_sensitivity.py
输出：
  <reports>/strong_band_sensitivity.json / .csv
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import scripts.k55_dream.build_rules as B  # noqa: E402
from src.engines.dream_rules import DREAM_PATTERN_RULES  # noqa: E402

REPORT_DIR = os.environ.get("K55_REPORT_DIR",
                            "/mnt/d/fortune-data/books/k55_dream/reports")
STRONG = ("大吉", "吉", "凶")


def element_scope_pool(elements: set) -> dict:
    """与 build_rules 同口径的**句池**：元素是句子主角（same_scenario）且句子可用。"""
    entries = B.load_entries()
    seen, uniq = set(), []
    for e in entries:
        core = B.normalize_core(e["title"])
        if core and core not in seen:
            seen.add(core)
            uniq.append(e)
    pool = defaultdict(set)
    for e in uniq:
        core, text = B.normalize_core(e["title"]), e["content"]
        hits = [el for el in elements if el in core]
        if not hits:
            continue
        raw = B.split_sentences(text)
        raw = [re.sub(r"[\(（](©|&|版权|来源)[^)）]*[)）]", "", s).strip() for s in raw]
        raw = [re.sub(r"^\s*[0-9０-９]+\s*[.、)）]\s*", "", s).strip() for s in raw]
        for el in hits:
            for s in raw:
                if B.same_scenario(s, el) and B.sentence_is_usable(s, el):
                    pool[el].add(s)
    return pool


def narrow_counts(sents) -> tuple:
    """窄词表读数（= 表内 counts 的口径）：判词术语，单向优先。"""
    ji = xiong = 0
    for s in sents:
        j, x = bool(B.JI_STRONG.search(s)), bool(B.XIONG_STRONG.search(s))
        if j and not x:
            ji += 1
        elif x and not j:
            xiong += 1
    return ji, xiong


def wide_counts(sents) -> tuple:
    """宽词表读数（审查口径 / 一致性校验仪）：含否定式与泛化负面词。"""
    ji = xiong = 0
    for s in sents:
        p = B.sentence_polarity(s)
        if p == "吉":
            ji += 1
        elif p == "凶":
            xiong += 1
    return ji, xiong


def main() -> int:
    names = {r["name"] for r in DREAM_PATTERN_RULES}
    pool = element_scope_pool(names)

    rows = []
    for r in DREAM_PATTERN_RULES:
        if r["luck"] not in STRONG:
            continue
        n_ji, n_xiong = r["counts"]["ji"], r["counts"]["xiong"]
        tot = n_ji + n_xiong
        minority = min(n_ji, n_xiong)
        # 「靠零反例条款进强档」= 达不到 5 句门槛、靠 minority==0 且 tot>=3 扶正
        by_unanimous_clause = (minority == 0 and tot >= B.MIN_UNANIMOUS_FOR_STRONG
                               and tot < B.MIN_SAMPLE_FOR_STRONG)
        sents = pool.get(r["name"], set())
        w_ji, w_xiong = wide_counts(sents)
        pol = B.luck_polarity(r["luck"])
        wide_pol = ("吉" if w_ji > w_xiong else "凶" if w_xiong > w_ji else "")
        # 成色判定（控制方裁决二：宽词表当**反例嗅探器**，不当计数仪表）
        if wide_pol and wide_pol != pol:
            verdict = "依赖窄词表"          # 宽表方向与档位相反
        elif (w_ji if pol == "凶" else w_xiong) > 0:   # 反例 = 与档位**相反**那侧
            verdict = "反例（零反例不成立）"  # 方向一致，但宽表嗅出反向证据
        else:
            verdict = "稳健"
        rows.append({
            "name": r["name"], "luck": r["luck"],
            "narrow": {"ji": n_ji, "xiong": n_xiong, "unique": tot},
            "wide": {"ji": w_ji, "xiong": w_xiong,
                     "unique": len(sents)},
            "by_unanimous_clause": by_unanimous_clause,
            "wide_minority": (w_ji if pol == "凶" else w_xiong),
            "verdict": verdict,
            "narrow_counts_field_basis": r.get("counts", {}).get("basis", "unique_sentence"),
        })
    rows.sort(key=lambda x: (not x["by_unanimous_clause"], x["verdict"], x["name"]))

    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(os.path.join(REPORT_DIR, "strong_band_sensitivity.json"), "w", encoding="utf-8") as f:
        json.dump({"strong_rules": rows}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(REPORT_DIR, "strong_band_sensitivity.csv"), "w",
              encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "luck", "narrow_ji", "narrow_xiong", "narrow_unique",
                    "wide_ji", "wide_xiong", "wide_unique",
                    "by_unanimous_clause", "verdict"])
        for x in rows:
            w.writerow([x["name"], x["luck"], x["narrow"]["ji"], x["narrow"]["xiong"],
                        x["narrow"]["unique"], x["wide"]["ji"], x["wide"]["xiong"],
                        x["wide"]["unique"], int(x["by_unanimous_clause"]), x["verdict"]])

    clause = [x for x in rows if x["by_unanimous_clause"]]
    print(f"强档规则 {len(rows)} 条；其中靠「零反例」条款进强档 {len(clause)} 条")
    print(f"{'规则':<8}{'档位':<6}{'窄词表':<12}{'宽词表':<12}{'条款':<6}判定")
    for x in clause:
        print(f"{x['name']:<8}{x['luck']:<6}"
              f"{x['narrow']['ji']}:{x['narrow']['xiong']:<9}"
              f"{x['wide']['ji']}:{x['wide']['xiong']:<9}"
              f"{'是' if x['by_unanimous_clause'] else '否':<6}{x['verdict']}")
    for tag in ("依赖窄词表", "反例（零反例不成立）"):
        sub = [x for x in rows if x["verdict"] == tag]
        print(f"全部强档里「{tag}」{len(sub)} 条：{[x['name'] for x in sub]}")
    robust = [x for x in rows if x["verdict"] == "稳健"]
    print(f"「稳健」{len(robust)}/{len(rows)} 条")
    print(f"结果 → {REPORT_DIR}/strong_band_sensitivity.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
