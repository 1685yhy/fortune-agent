#!/usr/bin/env python3
"""冻结小型语料夹具（k58 r7 裁决二：让**口径改动**能被测试抓到）。

**为什么需要**：随包规则表是「提交进库的产物」，没有任何用例从语料重算 ——
于是改分句器 / 改 `sentence_is_usable` 门槛 / 加标点归一映射 / 拆开同源 /
把豁免改成模糊匹配，**锁全绿、测试子集也全绿**，口径会静默漂移（审查实测）。
本脚本把一小段语料**冻结入库**（`tests/data/k58_fixture_corpus.jsonl`），
并把当前管线的输出冻结成期望值（`tests/data/k58_fixture_expected.json`）——
之后任何口径改动都会让 `tests/test_k58_fixture_pipeline.py` 变红。

夹具构成 = **真实语料切片**（下列元素的相关条目，按标题排序取前 N，确定性）
+ **少量定制条目**（`source=fixture_crafted`，专门让四类植入**必红**：
①4~5 字短句（测 `sentence_is_usable` 长度门槛）
②仅靠标点区分的一对句（测折叠映射，用当前未归一化的 `…`）
③与古籍引文**近似但不等**的句子（测豁免是否变成模糊匹配）
④DF≥10 的重复句（测公式句判据））。

⚠️ **重新冻结需要重新审批**：本夹具是「已审查口径」的快照，改动它等于改口径 ——
重跑本脚本会重写期望值，必须同步更新报告并重新过审。

用法（只在**有意**重新冻结时跑）：
  TMPDIR=/dev/shm nice -n 10 python3 scripts/k55_dream/freeze_fixture.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import scripts.k55_dream.build_rules as B  # noqa: E402

B.ELEMENTS_ORDER = {el: i for i, el in enumerate(
    ["猫", "狼", "蛇", "马", "棺材", "酒", "老人", "刀", "死亡", "兔子", "鸡", "牙齿"])}

FIX_DIR = ROOT / "tests" / "data"
FIX_CORPUS = FIX_DIR / "k58_fixture_corpus.jsonl"
FIX_EXPECT = FIX_DIR / "k58_fixture_expected.json"
FIX_CLASSICS = FIX_DIR / "k58_fixture_classics.json"

# 夹具元素：覆盖各类机制（模板句 / 古籍引文 / 折叠 / 公式句 / 强档 / 长尾）
ELEMENTS = ["猫", "狼", "蛇", "马", "棺材", "酒", "老人", "刀", "死亡", "兔子", "鸡", "牙齿"]
CONTENT_CAP = 3000           # 夹具内正文截断上限（控制入库体积；确定性）

# 定制条目（source=fixture_crafted）——每条都为「让某类植入**必红**」而存在。
# 注意：标题的**归一核心串**不能与真实切片里的条目重复（管线会按核心串去重、只留首条），
# 故除 `老人`（见 CRAFTED_HEAD_ELEMENTS，真实 head 切片跳过它）外都用非 head 标题。
CRAFTED_HEAD_ELEMENTS = {"老人"}
CRAFTED = [
    # ① 长度门槛（L5）：head 条目里放一条 **5 字**判词句 `望老人凶兆`
    #    （当前 6 字下限会拒；若门槛降成 4/5 就会被收进句池 → 计数变化 → 必红）
    {"title": "梦见老人",
     "content": "望老人凶兆。梦见老人，大吉，主寿年绵永，财帛丰盈。",
     "source": "fixture_crafted"},
    # ② 折叠映射（L6）：两句**仅差 `…` / `...`**（当前 PUNCT_FOLD_TABLE 不含 `…`）
    #    → 若有人加一条 `…` 归一映射，两句并成一句 → 唯一句数与计数变化 → 必红
    {"title": "梦见棺材的寓意",
     "content": "梦见棺材，主升官…发财。梦见棺材，主升官.发财。",
     "source": "fixture_crafted"},
    # ③ 豁免不得模糊匹配：与古籍引文「梦见蛇，主移徙事」**近似但不等**
    #    → 若豁免改成包含/前缀匹配，本句会被放行 → 蛇 的计数变化 → 必红
    *[{"title": f"梦见许多蛇的预兆{i}",
       "content": "梦见蛇，主移徙事也，主口舌。",
       "source": "fixture_crafted"} for i in range(12)],
    # ④ 公式句判据（DF≥10）：同句出现在 **12 个不同条目**里 → 必判公式句、排除出计数
    *[{"title": f"见兔子者皆主不祥{i}", "content": "见兔子者，皆主不祥。",
       "source": "fixture_crafted"} for i in range(12)],
]


def build_corpus() -> list:
    """真实切片（确定性）+ 定制条目。

    每个元素取：① 内容最长的**词条即该元素**条目（走 head 路径，不施加 same_scenario）
    ② 内容最长的 2 条「标题含该元素但核心不是它」的条目（走 same_scenario 路径）。
    排序键 = (-正文长度, 标题) → 完全确定性（不依赖语料迭代顺序）。
    """
    entries = B.load_entries()
    uniq = B.dedup_corpus(entries)
    picked, seen_titles = [], set()

    def take(e):
        if e["title"] in seen_titles or len(picked) >= 400:
            return
        seen_titles.add(e["title"])
        picked.append({"title": e["title"], "content": e["content"][:CONTENT_CAP],
                       "source": e["source"]})

    for el in ELEMENTS:
        heads = sorted((e for e in uniq if B.normalize_core(e["title"]) == el),
                       key=lambda e: (-len(e["content"]), e["title"]))
        skip_head = el in CRAFTED_HEAD_ELEMENTS
        for e in ([] if skip_head else heads[:2]):
            take(e)
        others = sorted((e for e in uniq
                         if el in B.normalize_core(e["title"])
                         and B.normalize_core(e["title"]) != el),
                        key=lambda e: (-len(e["content"]), e["title"]))
        for e in others[:2]:
            take(e)
    picked.sort(key=lambda x: (B.ELEMENTS_ORDER.get(B.normalize_core(x["title"])[:3], 9),
                               x["title"]))
    picked.extend(CRAFTED)
    return picked


def fixture_classics() -> dict:
    """夹具用古籍引文库（只取夹具元素相关的引文，保证可溯源且体积小）。"""
    all_q = B.load_classic_quotes()
    out = {}
    for el in ELEMENTS:
        if el in all_q:
            out[el] = [{"text": q["text"], "book": q["book"]} for q in all_q[el]]
    # 定制：与「梦见蛇，主移徙事」近似但**不等**的句子不得被豁免（见测试）
    out.setdefault("蛇", [])
    return out


def run_pipeline(entries: list, classics: dict) -> dict:
    """管线核心（与生成器同一批函数）：语料 → 同源句池 → 折叠 → 公式句 → 档位 → 释义。"""
    elements = {e["title"] for e in entries} | set(ELEMENTS)
    uniq, pools = B.element_scope_pool(elements, entries=entries)
    ckeys = B.classic_quote_keys(classics)
    formula = B.formula_sentence_keys(pools, classic_keys=ckeys)
    out = {"formula_keys": sorted(formula),
           "pool_key_fn": "sentence_key",
           "elements": {}}
    for el in ELEMENTS:
        if el not in pools:
            continue
        p = pools[el]
        ji_keys, xiong_keys = B.count_direction_sentences(
            [p["raw"][k] for k in p["raw"]], exclude_keys=formula)
        luck = B.luck_from_counts(len(ji_keys), len(xiong_keys))
        sentences = dict(p["sents"])
        head = dict(p["head"])
        gloss, ev = B.same_scenario_gloss(el, {el: p["sents"]}, classics,
                                         {el: p["head"]}, want_luck=luck)
        out["elements"][el] = {
            "counts": {"ji": len(ji_keys), "xiong": len(xiong_keys),
                       "sentences": len(p["sents"])},
            "luck": luck,
            "gloss_evidence": ev,
            "gloss": gloss,
            "pool_keys": sorted(p["raw"]),
            "df": {k: len(v) for k, v in sorted(p["df"].items())},
            "usable_sentences": len(p["sents"]),
            "head_sentences": len(head),
            "sentences_sample": sorted(sentences)[:6],
        }
    return out


def self_check(entries: list, expected: dict) -> None:
    """夹具自检：定制条目必须**真的进管线**，否则「植入必红」是空话。

    （踩过的坑：定制条目的标题核心串与真实条目相同 → 被 `dedup_corpus` 去重丢掉，
    期望值里看不到任何注入效果 —— 那种夹具是假夹具。）
    """
    kept = {e["title"] for e in B.dedup_corpus(entries)}
    missing = [e["title"] for e in CRAFTED if e["title"] not in kept]
    assert not missing, f"定制条目被去重丢掉（标题核心串与真实条目冲突）：{missing}"
    # ③ 豁免模糊匹配用例：该句必须①真的在 蛇 的句池里 ②被公式句判据排除（DF=12）
    #    ③**不含**在精确豁免集合里，但④**包含**某古籍键作为子串（模糊版会放行）
    snake_keys = list(expected["elements"]["蛇"]["df"])
    near = [k for k in snake_keys if "主移徙事也" in k]
    assert near, f"③ 用例未进 蛇 句池：{snake_keys[:3]}"
    assert near[0] in expected["formula_keys"], "③ 用例未被判为公式句（DF 不足？）"
    ck = B.classic_quote_keys(fixture_classics())
    assert near[0] not in ck, "③ 用例被**精确**豁免了 —— 无法区分模糊匹配"
    assert any(c in near[0] and c != near[0] for c in ck), "③ 用例不含任何古籍键子串 —— 模糊版也抓不到"
    # ② 折叠用例：`…` 与 `.` 当前必须是**两个**键
    assert B.sentence_key("梦见棺材，主升官…发财") != B.sentence_key("梦见棺材，主升官.发财"), \
        "② 用例的两个变体已经折叠成同一个键 —— 折叠植入将无法变红"
    # ① 长度门槛用例：5 字句当前必须**被拒**（否则门槛改动不会让它变红）
    assert not B.sentence_is_usable("望老人凶兆", "老人"), \
        "① 用例的 5 字句当前竟然可用 —— 门槛植入将无法变红"
    # ④ 公式句用例：必被排除
    assert "见兔子者,皆主不祥" in expected["formula_keys"], "④ 公式句用例未被排除"
    # ② 折叠用例：`…` 与 `...` 当前必须是**两个**键（否则折叠植入无法变红）
    assert B.sentence_key("梦见棺材，主升官…发财") != B.sentence_key("梦见棺材，主升官...发财"), \
        "② 用例的两个变体已经折叠成同一个键 —— 折叠植入将无法变红"
    print("夹具自检通过：4 类植入用例均真实生效")


def main() -> int:
    FIX_DIR.mkdir(parents=True, exist_ok=True)
    entries = build_corpus()
    classics = fixture_classics()
    expected = run_pipeline(entries, classics)
    self_check(entries, expected)
    FIX_CLASSICS.write_text(
        json.dumps(classics, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    FIX_CORPUS.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n", encoding="utf-8")
    FIX_EXPECT.write_text(
        json.dumps({"note": "k58 r7 冻结期望值：由 scripts/k55_dream/freeze_fixture.py 生成；"
                            "重新冻结 = 改口径，必须重新过审",
                    "corpus": FIX_CORPUS.name,
                    "element_count": len(expected["elements"]),
                    "formula_keys": expected["formula_keys"],
                    "elements": expected["elements"]},
                   ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"夹具语料 {len(entries)} 条 → {FIX_CORPUS}")
    print(f"期望值：元素 {len(expected['elements'])} 个、公式句 {len(expected['formula_keys'])} 条 "
          f"→ {FIX_EXPECT}；夹具引文库 → {FIX_CLASSICS}")
    for el, d in expected["elements"].items():
        print(f"  {el:<4} {d['luck']:<6} {d['counts']['ji']}:{d['counts']['xiong']} "
              f"池{d['counts']['sentences']:<3} [{d['gloss_evidence']}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
