#!/usr/bin/env python3
"""同构句族（模板句）检测：**判词模板能不能当元素证据**（k58 r5 收口，控制方要求）。

起因：`马` 的唯一凶句「梦见马者，主大凶」到底是**关于马的真判词**，还是站点的
**批量套话**（`梦见X者，主大凶` 这种把 X 换掉就能套几百个元素的模板）？这个问题
的价值不在 `马` 一条，而在**口径**：**模板句不得充当判词证据** —— k60 在 140 万条上
重建整张表时，这类套话的占比只会更高。

判据（同构句族 = 站点模板）：
  1. 把判词句里的**元素词**掩成占位符 `X`（同一个元素在句中的出现全部掩掉，标点归一）；
  2. 该归一模板被 **≥ `--min-elements` 个不同元素**使用（措辞逐字相同的句子出现在多个
     元素下 = 不是针对某个元素的判词）；
  3. 且出现在 **≥ `--min-docs` 个不同条目**里（≥ `--min-docs` 文档频率）。
两条**同时**满足 → 判为模板族句，按口径应**从唯一句计数里排除**。

本脚本**只做检测与影响量化，不改表、不改计数口径**（口径是否排除模板句由控制方裁决；
k60 换语料时用本脚本重跑即可）。

用法：
  TMPDIR=/dev/shm nice -n 10 python3 scripts/k55_dream/template_family_audit.py \
      [--min-elements 3] [--min-docs 3] [--top 30]
输出：
  <reports>/template_family_audit.json / .csv（模板族清单）
  <reports>/template_family_impact.csv（排除模板句后**档位变化**的规则清单）
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import scripts.k55_dream.build_rules as B  # noqa: E402
from src.engines.dream_rules import DREAM_PATTERN_RULES  # noqa: E402

REPORT_DIR = os.environ.get("K55_REPORT_DIR",
                            "/mnt/d/fortune-data/books/k55_dream/reports")
PUNCT_NORM = str.maketrans({"，": ",", "；": ";", "：": ":", "！": "!", "？": "?", "、": ","})


# 粗骨架：把**判词本身**也掩成 V（回答「句式是不是套话」，与细模板互补）
VERDICT_MASK_RE = re.compile(
    r"(大吉|大凶|不祥|吉利|吉兆|凶兆|吉|凶|有凶|有吉|得财|发财|破财|"
    r"幸福|顺利|不顺|不利|小心|谨慎)")


def norm_skeleton(tpl: str) -> str:
    """细模板 → 粗骨架（判词掩成 V）。"""
    return VERDICT_MASK_RE.sub("V", tpl)


def norm_template(sent: str, el: str) -> str:
    """把句子里的元素词掩成 X（标点归一、去空白）→ 归一模板。"""
    s = sent.translate(PUNCT_NORM)
    s = re.sub(r"\s+", "", s)
    s = s.replace(el, "X")
    return s


def collect(members: set) -> tuple:
    """扫语料 → 同场景句池（含来源条目 id）与判词句（= 计数总体）。"""
    entries = B.load_entries()
    seen, uniq = set(), []
    for e in entries:
        core = B.normalize_core(e["title"])
        if core and core not in seen:
            seen.add(core)
            uniq.append(e)
    pool = defaultdict(set)                       # el -> {sentence}
    judge = []                                    # (el, sentence, doc_id)
    for doc_id, e in enumerate(uniq):
        core, text = B.normalize_core(e["title"]), e["content"]
        hits = [el for el in members if el in core]
        if not hits:
            continue
        raw = B.split_sentences(text)
        raw = [re.sub(r"[\(（](©|&|版权|来源)[^)）]*[)）]", "", s).strip() for s in raw]
        raw = [re.sub(r"^\s*[0-9０-９]+\s*[.、)）]\s*", "", s).strip() for s in raw]
        for el in hits:
            for s in raw:
                if not (B.same_scenario(s, el) and B.sentence_is_usable(s, el)):
                    continue
                pool[el].add(s)
                j, x = bool(B.JI_STRONG.search(s)), bool(B.XIONG_STRONG.search(s))
                if (j and not x) or (x and not j):        # 判词句 = 计数总体
                    judge.append((el, s, doc_id))
    return pool, judge


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-elements", type=int, default=3,
                    help="同一归一模板被多少个**不同元素**使用 → 判为模板族")
    ap.add_argument("--min-docs", type=int, default=3, help="模板族的最小文档频率")
    ap.add_argument("--top", type=int, default=30, help="打印前 N 个模板族")
    ap.add_argument("--probe", default="马,猫,老鼠,蛇,孔雀",
                    help="逐条列出这些元素的判词句及其模板的元素数/条目数（回答「它是不是套话」）")
    args = ap.parse_args()

    members = {r["name"] for r in DREAM_PATTERN_RULES}
    pool, judge = collect(members)

    tpl_elems = defaultdict(set)      # 模板 -> {元素}
    tpl_docs = defaultdict(set)       # 模板 -> {条目 id}
    tpl_sents = defaultdict(set)      # 模板 -> {原句}
    sk_elems = defaultdict(set)       # 粗骨架 -> {元素}
    sk_docs = defaultdict(set)        # 粗骨架 -> {条目 id}
    sk_ex = defaultdict(set)          # 粗骨架 -> {原句}
    for el, s, doc in judge:
        t = norm_template(s, el)
        tpl_elems[t].add(el)
        tpl_docs[t].add(doc)
        tpl_sents[t].add(s)
        k = norm_skeleton(t)
        sk_elems[k].add(el)
        sk_docs[k].add(doc)
        sk_ex[k].add(s)
    templates = [
        {"template": t, "elements": len(tpl_elems[t]), "docs": len(tpl_docs[t]),
         "variants": len(tpl_sents[t]), "examples": sorted(tpl_sents[t])[:3],
         "elements_sample": sorted(tpl_elems[t])[:8]}
        for t in tpl_docs
    ]
    templates.sort(key=lambda x: (-x["docs"], -x["elements"], x["template"]))
    family = [x for x in templates
              if x["elements"] >= args.min_elements and x["docs"] >= args.min_docs]

    # 影响量化：把模板族句从唯一句计数里排除后，档位会怎么变
    fam_sents = {s for x in family for s in tpl_sents[x["template"]]}
    fam_by_el = defaultdict(set)
    for x in family:
        for el in tpl_elems[x["template"]]:
            fam_by_el[el] |= (tpl_sents[x["template"]] & pool[el])

    skeletons = [
        {"skeleton": k, "elements": len(sk_elems[k]), "docs": len(sk_docs[k]),
         "examples": sorted(sk_ex[k])[:4], "elements_sample": sorted(sk_elems[k])[:10]}
        for k in sk_docs
    ]
    skeletons.sort(key=lambda x: (-x["elements"], -x["docs"], x["skeleton"]))

    def narrow_of(sents) -> tuple:
        a = sum(1 for s in sents if (B.JI_STRONG.search(s) and not B.XIONG_STRONG.search(s)))
        b = sum(1 for s in sents if (B.XIONG_STRONG.search(s) and not B.JI_STRONG.search(s)))
        return a, b

    band_changes, strong_lost, drift_only = [], [], []
    for r in DREAM_PATTERN_RULES:
        el = r["name"]
        # 只对**靠判词计数话事**的档位算影响（强制族由 brief 指定，不算）
        if "mandatory" in r["source"] or r["luck"] in ("提醒类",):
            continue
        sents = pool[el]
        n_ji, n_xg = narrow_of(sents)                       # 不做排除（= 现状复算）
        e_ji, e_xg = narrow_of(sents - fam_by_el[el])       # 排除模板族句
        # ⚠️ 对照：raw/ 是活文件（k60 在抓），**不排除任何句**时复算也可能与表内不同
        #    → 那部分是「语料漂移」，不是模板句造成的。两者必须分开报，否则归因错。
        if r["luck"] != B.luck_from_counts(n_ji, n_xg):
            drift_only.append({
                "name": el, "luck_table": r["luck"],
                "luck_recount_no_exclusion": B.luck_from_counts(n_ji, n_xg),
                "narrow_table": f"{r['counts']['ji']}:{r['counts']['xiong']}",
                "narrow_recount": f"{n_ji}:{n_xg}",
            })
        now, after = r["luck"], B.luck_from_counts(e_ji, e_xg)
        if now != after:
            band_changes.append({
                "name": el, "luck_now": now, "luck_without_templates": after,
                "narrow_now": f"{r['counts']['ji']}:{r['counts']['xiong']}",
                "narrow_recount_now": f"{n_ji}:{n_xg}",
                "narrow_without": f"{e_ji}:{e_xg}",
                "template_sents_removed": len(fam_by_el[el]),
            })
            if now in ("大吉", "吉", "凶") and after not in ("大吉", "吉", "凶"):
                strong_lost.append(el)
    band_changes.sort(key=lambda x: (x["luck_without_templates"], x["name"]))
    drift_only.sort(key=lambda x: x["name"])
    # **隔离后的模板效应**：同一份活语料快照上「排除模板句」与「不排除」的差
    # （上面的 band_changes 对比的是**表内档位**，因此把「语料漂移」也算进去了 —— 必须隔离）
    isolated = []
    for r in DREAM_PATTERN_RULES:
        el = r["name"]
        if "mandatory" in r["source"] or r["luck"] in ("提醒类",):
            continue
        a = B.luck_from_counts(*narrow_of(pool[el]))
        b = B.luck_from_counts(*narrow_of(pool[el] - fam_by_el[el]))
        if a != b:
            isolated.append({"name": el, "luck_recount": a, "luck_without_templates": b,
                             "narrow_recount": ":".join(map(str, narrow_of(pool[el]))),
                             "narrow_without": ":".join(map(str, narrow_of(pool[el] - fam_by_el[el]))),
                             "template_sents_removed": len(fam_by_el[el])})
    isolated.sort(key=lambda x: x["name"])

    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(os.path.join(REPORT_DIR, "template_family_audit.json"), "w", encoding="utf-8") as f:
        json.dump({
            "criteria": {"min_elements": args.min_elements, "min_docs": args.min_docs},
            "judge_sentences": len(judge),
            "judge_unique_templates": len(templates),
            "family_templates": len(family),
            "family_sentence_instances": sum(x["docs"] for x in family),
            "family_share_of_judge": round(
                sum(x["docs"] for x in family) / max(1, len(judge)), 4),
            "drift_only_band_changes": len(drift_only),
            "band_changes_with_template_exclusion": len(band_changes),
            "isolated_template_effect": isolated,
            "isolated_template_effect_count": len(isolated),
            "probe": {el: [
                {"sentence": s,
                 "template": norm_template(s, el),
                 "template_elements": len(tpl_elems[norm_template(s, el)]),
                 "template_docs": len(tpl_docs[norm_template(s, el)]),
                 "elements": sorted(tpl_elems[norm_template(s, el)])[:8]}
                for s in sorted(pool[el])
                if (B.JI_STRONG.search(s) and not B.XIONG_STRONG.search(s))
                or (B.XIONG_STRONG.search(s) and not B.JI_STRONG.search(s))
            ] for el in args.probe.split(",") if el in pool},
            "templates": templates[:200],
            "skeletons": skeletons[:200],
        }, f, ensure_ascii=False, indent=2)
    with open(os.path.join(REPORT_DIR, "template_family_impact.csv"), "w",
              encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["name", "luck_now", "luck_without_templates",
                                          "narrow_now", "narrow_recount_now",
                                          "narrow_without", "template_sents_removed"])
        w.writeheader()
        w.writerows(band_changes)
    with open(os.path.join(REPORT_DIR, "template_family_drift_control.csv"), "w",
              encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["name", "luck_table", "luck_recount_no_exclusion",
                                          "narrow_table", "narrow_recount"])
        w.writeheader()
        w.writerows(drift_only)

    print(f"判词句实例 {len(judge)} 条；归一模板 {len(templates)} 个；"
          f"判为**模板族** {len(family)} 个（元素 ≥{args.min_elements} 且条目 ≥{args.min_docs}）")
    print(f"模板族句实例占判词总体：{sum(x['docs'] for x in family)}/{len(judge)}"
          f" = {sum(x['docs'] for x in family) / max(1, len(judge)):.1%}")
    print(f"\n【细模板·逐字套话】判为族 {len(family)} 个")
    print(f"{'模板':<34}{'元素数':<7}{'条目数':<7}示例")
    for x in family[:args.top]:
        print(f"{x['template'][:32]:<34}{x['elements']:<7}{x['docs']:<7}{x['examples'][0][:38]}")
    print(f"\n【细模板·未被判为族但最接近】（元素数 ≥2 的次高）")
    near = [x for x in templates if x not in family and x["elements"] >= 2]
    near.sort(key=lambda x: (-x["elements"], -x["docs"]))
    for x in near[:12]:
        print(f"{x['template'][:32]:<34}{x['elements']:<7}{x['docs']:<7}{x['examples'][0][:38]}")
    print(f"\n【粗骨架·句式套话】共 {len(skeletons)} 个骨架，前 {min(12, len(skeletons))} 个：")
    print(f"{'骨架':<40}{'元素数':<7}{'条目数':<7}示例")
    for x in skeletons[:12]:
        print(f"{x['skeleton'][:38]:<40}{x['elements']:<7}{x['docs']:<7}{x['examples'][0][:34]}")
    print(f"\n【对照·语料漂移】不做任何排除、用当前活语料复算：档位与表内不同 "
          f"{len(drift_only)} 条（这部分**不是**模板句造成的）"
          f"{[x['name'] for x in drift_only[:12]]}")
    print(f"【排除模板族句·未隔离漂移】档位变化 {len(band_changes)} 条；"
          f"其中从强档掉下来 {len(strong_lost)} 条：{strong_lost[:20]}")
    print(f"【隔离后的**纯模板效应**（同一快照上排除 vs 不排除）】{len(isolated)} 条："
          f"{[(x['name'], x['luck_recount'], '→', x['luck_without_templates']) for x in isolated]}")
    print("\n【点名探针】这些元素的判词句**是不是套话**（模板被几个元素/条目用过）：")
    for el in args.probe.split(","):
        if el not in pool:
            continue
        for s in sorted(pool[el]):
            j, x = bool(B.JI_STRONG.search(s)), bool(B.XIONG_STRONG.search(s))
            if (j and not x) or (x and not j):
                t = norm_template(s, el)
                print(f"  {el:<5}{'吉' if j and not x else '凶'} "
                      f"元素{len(tpl_elems[t])} 条目{len(tpl_docs[t]):<4} "
                      f"{sorted(tpl_elems[t])[:5]} ← {s[:40]}")
    print(f"结果 → {REPORT_DIR}/template_family_audit.json / template_family_impact.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
