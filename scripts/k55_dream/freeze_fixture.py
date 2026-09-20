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
ELEMENTS = ["猫", "狼", "蛇", "马", "棺材", "酒", "老人", "刀", "死亡", "兔子", "鸡", "牙齿",
            # 探针元素：其「核心串」不是任一条目的核心（`梦见刀光的寓意N` 的核心是
            # 「刀光寓意N」），必须显式列入，否则 DF=10 探针不会计入 刀光 的句池
            "刀光", "铅砣",
            # 跨元素套话旋钮探针元素（I-1）
            "青石", "白石", "黑石", "灰石", "紫石", "白玉", "黑玉",
            # 强档样本量/反例阈值探针元素（I-1）
            "石砧", "木砧", "灰砧", "银锭", "锡锭"]
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
    # ⑤ DF=10 探针（阈值下侧）：同句 10 个条目 → **恰好在门槛上**、必被排除；
    #    若把 MIN_DF_FORMULA 调成 11，它会被放回计数 → 计数变化 → 必红
    *[{"title": f"梦见刀光的寓意{i}", "content": "梦见刀光，主破财。",
       "source": "fixture_crafted"} for i in range(10)],
    # ⑥ DF=9 探针（阈值上侧）：同句 9 个条目 → **恰好在其下**、必须**不被**排除；
    #    若把 MIN_DF_FORMULA 调成 9，它会被排除 → 计数变化 → 必红
    #    （用独立元素 `铅砣` 承载，避免把 `牙齿` 的唯一句从 3 抬到 4 —— 那会毁掉
    #      MIN_UNANIMOUS_FOR_STRONG=3 的上侧探针）
    *[{"title": f"梦见铅砣的征兆{i}", "content": "梦见铅砣，主破财。",
       "source": "fixture_crafted"} for i in range(9)],
    # ⑦ MIN_SAMPLE_FOR_STRONG=5 探针（大吉门槛两侧）：恰好 5 条一致 → 大吉；
    #    恰好 4 条 → 只到「吉多于凶」；把常量改成 4 或 6 都会让一侧翻转 → 必红
    {"title": "梦见石砧", "content": "梦见石砧，得财。梦见石砧，主吉。梦见石砧，大吉。"
                                    "梦见石砧，有财。梦见石砧，富贵。",
     "source": "fixture_crafted"},
    {"title": "梦见木砧", "content": "梦见木砧，得财。梦见木砧，主吉。梦见木砧，大吉。"
                                    "梦见木砧，有财。",
     "source": "fixture_crafted"},
    # ⑧ MIN_UNANIMOUS_FOR_STRONG=3 探针（零反例强档门槛的下侧）：恰好 2 条一致凶
    #    → 只到「凶多于吉」（<3 不给强档）
    {"title": "梦见灰砧", "content": "梦见灰砧，将有大凶。梦见灰砧，是不祥之兆。",
     "source": "fixture_crafted"},
    # ⑨ 跨元素套话两个旋钮（I-1 顺手：清了「只有一个取值点」的探针）
    #   · 模板 A「梦见X，主大凶」：3 个元素（青石/白石/黑石）各 DF=3 → 恰在上侧
    #     → 必判族、被排除；MIN_FORMULA_DF 提到 4 就翻 → 必红
    *[{"title": f"梦见{e}的寓意{i}", "content": f"梦见{e}，主大凶。",
       "source": "fixture_crafted"} for e in ("青石", "白石", "黑石") for i in range(3)],
    #   · 模板 B「梦见X，主口舌」：2 个元素（灰石/紫石）各 DF=3 → 恰在下侧
    #     → 不判族、进计数；MIN_FORMULA_ELEMENTS 降到 2 就翻 → 必红
    #   （措辞要与其它定制句**不同**：`梦见X，主口舌` 会与刀光那条掩码同模板 → 元素数变 3，
    #     旋钮就会落到另一侧。踩过一次，故这里用「主是非口舌」。）
    *[{"title": f"梦见{e}的说法{i}", "content": f"梦见{e}，主是非口舌。",
       "source": "fixture_crafted"} for e in ("灰石", "紫石") for i in range(3)],
    #   · 模板 C「梦见X，是不祥之兆」：2 个元素各 DF=2 → 族 DF 门槛的下侧
    #     （MIN_FORMULA_DF 降到 2 就翻 → 必红）
    *[{"title": f"梦见{e}的预兆{i}", "content": f"梦见{e}，是不祥之兆。",
       "source": "fixture_crafted"} for e in ("白玉", "黑玉") for i in range(2)],
    # ⑩ MAX_MINORITY_FOR_STRONG=2 探针（两侧）：5:2 → 强档「吉」；5:3 → 只到「吉多于凶」
    {"title": "梦见银锭", "content": "梦见银锭，得财。梦见银锭，主吉。梦见银锭，大吉。"
                                    "梦见银锭，有财。梦见银锭，富贵。"
                                    "梦见银锭，主口舌。梦见银锭，破财。",
     "source": "fixture_crafted"},
    {"title": "梦见锡锭", "content": "梦见锡锭，得财。梦见锡锭，主吉。梦见锡锭，大吉。"
                                    "梦见锡锭，有财。梦见锡锭，富贵。"
                                    "梦见锡锭，主口舌。梦见锡锭，破财。梦见锡锭，损失。",
     "source": "fixture_crafted"},
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
    elements = {B.normalize_core(e["title"]) for e in entries} | set(ELEMENTS)
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
        gloss_raw, ev_raw = B.same_scenario_gloss(el, {el: p["sents"]}, classics,
                                                 {el: p["head"]}, want_luck=luck)
        # **组装层**（r8 I-2）：产物 gloss 的最终形态产自这里 —— 夹具必须覆盖到它，
        # 否则 y1 限定语 / y2 反向降级 / y3 兜底文案三类补丁的改动夹具全绿。
        syms = ["情境变化", "现实压力"]
        # 夹具内的「覆盖量」= 标题核心串含该元素的夹具条目数（与产物口径同义、可复现）
        cov = sum(1 for e in entries if el in B.normalize_core(e["title"]))
        asm = B.assemble_rule_fields(el, cov, len(ji_keys), len(xiong_keys), luck,
                                     gloss_raw, ev_raw, "", "中性类", syms)
        out["elements"][el] = {
            "counts": {"ji": len(ji_keys), "xiong": len(xiong_keys),
                       "sentences": len(p["sents"])},
            # 组装**前**（选句层）与组装**后**（产物形态）都记 —— 覆盖边界可分辨
            "luck_raw": luck,
            "luck": asm["luck"],
            "gloss_evidence_raw": ev_raw,
            "gloss_raw": gloss_raw,
            "gloss_evidence": asm["ev_kind"],
            "gloss": asm["gloss"],
            "luck_basis": asm["luck_basis"],
            "type": asm["ptype"],
            "tone": asm["tone"],
            "symbols": asm["syms"],
            "pool_keys": sorted(p["raw"]),
            "df": {k: len(v) for k, v in sorted(p["df"].items())},
            "usable_sentences": len(p["sents"]),
            "head_sentences": len(head),
            "sentences_sample": sorted(sentences)[:6],
        }
    return out


def _crafted_sentences(prefix: str) -> list:
    """从 CRAFTED **正文**里取出句子（而不是在自检里重写一遍字面量）。

    r8 I-3：原先自检里的探针句是**字面量**，改 `CRAFTED` 正文而不改自检 → 自检仍绿
    = 自检有洞。现在一律从正文提取。
    """
    out = []
    for e in CRAFTED:
        if e["title"].startswith(prefix):
            out.extend(x.strip() for x in B.split_sentences(e["content"]) if x.strip())
    return out


def self_check(entries: list, expected: dict) -> None:
    """夹具自检：定制条目必须**真的进管线**、探针必须**真的落在阈值两侧**。

    （踩过的坑：定制条目的标题核心串与真实条目相同 → 被 `dedup_corpus` 去重丢掉，
    期望值里看不到任何注入效果 —— 那种夹具是假夹具。）
    """
    kept = {e["title"] for e in B.dedup_corpus(entries)}
    missing = [e["title"] for e in CRAFTED if e["title"] not in kept]
    assert not missing, f"定制条目被去重丢掉（标题核心串与真实条目冲突）：{missing}"

    # ① 长度门槛：从 CRAFTED 正文取那条 5 字句，当前必须**被拒**
    short = [x for x in _crafted_sentences("梦见老人") if len(x) == 5]
    assert short, "① 长度探针句没从 CRAFTED 正文里找到"
    assert not B.sentence_is_usable(short[0], "老人"), f"① {short[0]!r} 当前竟然可用"

    # ② 折叠映射：从正文取那一对仅差 `…` / `.` 的句子，当前必须是**两个**键
    pair = [x for x in _crafted_sentences("梦见棺材的寓意") if "升官" in x]
    assert len({B.sentence_key(x) for x in pair}) == len(pair) == 2, f"② 折叠探针对异常：{pair}"

    # ③ 豁免：近似古籍引文的句子必须 ①在池里 ②被判公式句（DF=12）③不在精确豁免集
    snake_keys = list(expected["elements"]["蛇"]["df"])
    near = [k for k in snake_keys if "主移徙事也" in k]
    assert near, f"③ 用例未进 蛇 句池：{snake_keys[:3]}"
    assert near[0] in expected["formula_keys"], "③ 用例未被判为公式句（DF 不足？）"
    ck = B.classic_quote_keys(fixture_classics())
    assert near[0] not in ck and any(c in near[0] and c != near[0] for c in ck), \
        "③ 用例与古籍键的关系不满足「近似但不等」"

    # ④ DF 两侧探针（I-1）：DF=12 与 DF=10 必被排除；DF=9 必须**不被**排除
    fk = expected["formula_keys"]
    df12 = {B.sentence_key(x) for x in _crafted_sentences("见兔子者皆主不祥")}
    df10 = {B.sentence_key(x) for x in _crafted_sentences("梦见刀光的寓意")}
    df9 = {B.sentence_key(x) for x in _crafted_sentences("梦见铅砣的征兆")}
    assert df12 and df12 <= set(fk), "④ DF=12 探针未被排除"
    assert df10 and df10 <= set(fk), "④ DF=10 探针未被排除（门槛下侧没钉住）"
    assert df9 and not (df9 & set(fk)), "④ DF=9 探针被排除了（门槛上侧没钉住）"

    # ⑤ 强档样本量两侧（I-1 顺手：同类「只有一个取值点」的探针）
    el = expected["elements"]
    assert el["石砧"]["luck"] == "大吉" and el["木砧"]["luck"] == "吉多于凶", \
        ("MIN_SAMPLE_FOR_STRONG=5 两侧探针失效："
         f"铁钉={el['铁钉']['luck']} 铁锤={el['铁锤']['luck']}")
    assert el["灰砧"]["luck"] == "凶多于吉", \
        f"MIN_UNANIMOUS_FOR_STRONG=3 下侧探针失效：铜锤={el['铜锤']['luck']}"
    assert el["银锭"]["luck"] == "吉" and el["锡锭"]["luck"] == "吉多于凶", \
        ("MAX_MINORITY_FOR_STRONG=2 两侧探针失效："
         f"银锁={el['银锁']['luck']} 铜锁={el['铜锁']['luck']}")

    # ⑥ 跨元素套话族：模板 A（3 元素/DF3）必判族；模板 B（2 元素/DF3）必不判族；
    #    模板 C（2 元素/DF2）必不判族 —— 三个旋钮因此各有两侧
    fk_all = set(expected["formula_keys"])
    for e in ("青石", "白石", "黑石"):
        assert el[e]["luck"] == "中性", f"⑥ 模板 A 未判族（{e} 应被排除）：{el[e]['counts']}"
    #   （注意：同一句在 N 个条目里出现 = **1 条唯一句**，故 B/C 的读数都是 0:1；
    #     敏感性落在 `counts`/`df` 列上 —— 夹具用例逐列比对，照样必红。）
    for e in ("灰石", "紫石"):
        assert el[e]["counts"]["xiong"] == 1 and el[e]["luck"] == "中性", \
            f"⑥ 模板 B 被误判族（{e}）：{el[e]['counts']}"
        assert el[e]["df"] and max(el[e]["df"].values()) == 3, f"⑥ {e} 的 DF 不是 3"
    for e in ("白玉", "黑玉"):
        assert el[e]["counts"]["xiong"] == 1 and max(el[e]["df"].values()) == 2, \
            f"⑥ 模板 C 异常（{e}）：{el[e]['counts']} {el[e]['df']}"

    # ⑦ 组装层（I-2）：夹具必须覆盖产物的 **gloss 最终形态**（限定语/兜底文案）
    assert "不代表吉凶" in el["马"]["gloss"], "⑥ 组装层未覆盖：马 的 gloss 没带限定语"
    assert el["刀"]["gloss_evidence"] == "fallback_no_same_scenario", \
        "⑥ 组装层未覆盖：刀 应走兜底分支"
    assert el["马"]["gloss"] != el["马"]["gloss_raw"], \
        "⑥ 组装层未生效：组装前后 gloss 相同（夹具又只锁到了选句层）"
    print("夹具自检通过：6 类探针（长度/折叠/豁免/DF 两侧/强档样本量两侧/组装层）均真实生效")


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
