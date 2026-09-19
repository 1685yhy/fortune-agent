#!/usr/bin/env python3
"""单字意象的**搭配词统计**（k58 M-1：match 分支必须从语料统计出来，不许人工挑）。

背景：规则表 46/263 条是单字名（蛇/水/火…），而它们的 match 里只有少数几条
n-gram，用户最常打的**裸「梦见X」**形态命中不了（实测三项非空 1/46 ≈ 2%）。
k49 又明令 match 分支不得是裸单字（会前缀误伤：`火` 命中 `火腿`）。

本模块提供三样**数据产物**（都可复现、带覆盖量）：
1. `head_forms(X)`：`梦见X`/`梦到X`/`梦见了X` 三种触发形态在语料标题里的真实条数；
2. `collocations(X)`：含 X 的 2~3 字搭配在**语料标题**里的覆盖条数（Top-N）；
3. `guard_chars(X)`：**边界护栏**——X 后面紧跟哪些字时，X 只是更长词的前缀而非该意象。
   两个来源都是数据：
   - **通用词典**（jieba 词典，498k 词条）：以 X 开头的二字词的第二个字
     （实测 `水杯/火腿/车门` 都在词典里 → 护栏能挡住这些对抗样本）；
   - **本批语料**：以 X 开头的标题核心串里、不属于 X 搭配词的那些的第二个字。

用法：
    python scripts/k55_dream/collocation.py --elements 蛇 水 火 车 --top 8
    python scripts/k55_dream/collocation.py --all-single --out reports/single_char_collocations.json
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.k55_dream.crawl_lib import DATA_ROOT, now_iso  # noqa: E402
from scripts.k55_dream.stats_elements import load_entries, normalize_core  # noqa: E402

REPORTS = DATA_ROOT / "reports"
# 头部形态的**结构化边界护栏**（k58 r2，I-2）：X 后面必须紧跟
# 分隔符/句末/常见虚词 —— 白名单式判定，**不依赖词典是否收录**
# （实测 jieba 词典根本没收录 水立方/水逆/猫咖/猫山王/水信玄饼 这类新词与网语，
#   靠词典护栏永远挡不住它们；而白名单只看「X 后面那个字是不是虚词/标点」，
#   对任何新造复合词都成立）。
TRAILING_BOUNDARY = (
    r"[，,。！？!?、；;：:\s]|$|"
    r"了|的|在|是|和|跟|与|把|被|让|给|向|从|对|有|我|你|他|她|它|们|"
    r"很|太|都|也|还|就|又|再|但|而|或|及|吗|呢|吧|啊|呀|着|过|"
    r"了|得"
    # 注意：「来/去/到/等/看/说/想/会/要」**不进白名单** —— 它们会组成
    # 马来/去年/头等 这类**别的词**（审查 m-1 实测：梦见马来→马、梦见头等→头）
    # 注意：**方位名词不进白名单**（里/上/下/中/前/后/边/面）——
    # 它们会和 X 组成复合词（马上=立即、水里、山上、心中），放行就会把
    # 「梦见马上」误判成马梦（实测）。这些形态交给搭配分支（水里/山上…）。
)

LEXICON_MIN_FREQ = 10       # jieba 词典里取多少频次以上的词做护栏（保留：作为辅助证据）
# （k58：100 会漏掉「水杯」(freq=15) 这类常用词 → 护栏失效、出现前缀误伤；
#   10 能覆盖水杯/车门/火腿，而护栏只作用于**头部形态**分支，不影响搭配分支）
COLLOC_MIN_COUNT = 3        # 搭配词至少覆盖 3 条语料
COLLOC_MAX_LEN = 3


def log(msg: str) -> None:
    print(f"{now_iso()} {msg}", flush=True)


# ── 语料侧统计 ──────────────────────────────────────────────────────
def corpus_stats(elements: set) -> dict:
    """一次扫全语料，产出每个元素的 head 计数 / 搭配 / 语料护栏。"""
    entries = load_entries()
    head = Counter()                 # 裸形态：核心串 == X
    hits = Counter()                 # 含 X 的条目数（标题核心串维度）
    colloc = defaultdict(Counter)    # X -> Counter(2~3 字搭配 -> 条目数)
    starts = defaultdict(Counter)    # X -> Counter(以 X 开头的更长核心串 -> 条目数)
    forms = defaultdict(Counter)     # X -> Counter(触发形态 -> 含该形态的条目数)
    standalone = Counter()           # X -> X 在核心串里**独立成词**（jieba 分词）的条目数

    import jieba
    jieba.initialize()
    for e in entries:
        core = normalize_core(e["title"])
        if not core:
            continue
        content = e.get("content") or ""
        core_tokens = None
        for X in elements:
            if X not in core and X not in content:
                continue
            # 「独立成词」统计：X 在核心串里是**独立 token**（不是更长词的一部分）
            # —— 这是区分「具体意象」与「词缀碎片」的数据判据：
            # 菜/鞋/肉 在语料里独立成词（买菜/鞋/肉），而 面/身/子 几乎只作为
            # 面条/身上/面子 的一部分出现（实测 面 独立成词 ≈0）。
            if X in core:
                if core_tokens is None:
                    core_tokens = set(jieba.lcut(core))
                if X in core_tokens:
                    standalone[X] += 1
            # 触发形态覆盖量：在**语料正文**里数三种用户口语形态
            # （「梦见X」在标题里是规范形；「梦到X/梦见了X」只在正文里出现）
            for trig in ("梦见", "梦到", "梦见了"):
                if f"{trig}{X}" in content or f"{trig}{X}" in (e["title"] or ""):
                    forms[X][f"{trig}{X}"] += 1
            if X not in core:
                continue
            hits[X] += 1
            if core == X:
                head[X] += 1
                continue
            if len(core) >= 2 and core.startswith(X):
                starts[X][core] += 1
            n = len(core)
            for size in (2, 3):
                for i in range(0, max(0, n - size + 1)):
                    g = core[i:i + size]
                    if X in g:
                        colloc[X][g] += 1
    log(f"[colloc] 语料扫描完成：目标元素 {len(elements)} 个")
    return {"head": head, "hits": hits, "colloc": colloc, "starts": starts,
            "forms": forms, "standalone": standalone}


def head_forms(entry: dict, X: str) -> dict:
    """触发形态的覆盖量 —— **全部实数**，来自语料正文的出现条数。

    （初版对「梦到X/梦见了X」用了「按同源打折」的估算，那不是统计 —— 已改。）
    覆盖量为 0 的形态仍保留分支（用户口语里确实会这么打），但如实标 0。
    """
    f = entry["forms"].get(X, Counter())
    return {form: f.get(form, 0) for form in (f"梦见{X}", f"梦到{X}", f"梦见了{X}")}


def collocations(entry: dict, X: str, top: int = 5, guard: set = None,
                 exclude_names: set = None) -> list:
    """含 X 的 2~3 字搭配，按覆盖条数降序（覆盖 >= COLLOC_MIN_COUNT）。

    k58 关键过滤：**X 后面紧跟护栏字的搭配直接丢弃** —— 那说明该串是「另一个东西」
    （火车/酒店/山羊/水杯…），不该作为 X 的搭配分支；否则头部护栏挡了，
    搭配分支还是会把它放进来（实测就是这样漏的）。
    """
    guard = guard or set()
    exclude_names = exclude_names or set()
    c = entry["colloc"].get(X, Counter())
    out = []
    for g, n in c.most_common():
        if n < COLLOC_MIN_COUNT or len(g) > COLLOC_MAX_LEN:
            continue
        # 只有「护栏字 + 该串本身是通用词典词」才丢 —— 这样
        # 「火车/酒店/山羊/猫头鹰」被丢掉（另一个东西），而
        # 「狗咬/蛇咬/狗追」这类**同意象的动作搭配**保留（它们不是词典词）。
        if (len(g) > len(X) and g[len(X)] in guard and is_lexicon_word(g)):
            continue
        # 形态/碎片过滤（k58 质检）：
        # ① 与头部形态重复的（梦见X）不再作为搭配分支；
        # ② 只保留「通用词典词」或「以 X 开头」的搭配 —— 这样
        #    「萄酒」（葡萄酒的碎片）、「我手」「我钱」这类跨词碎片被丢掉，
        #    而「水里/在水里/蛇咬/被狗咬」保留。
        if g in (f"梦见{X}", f"梦到{X}", f"梦见了{X}"):
            continue
        if not (g.startswith(X) or is_lexicon_word(g)):
            continue
        # 非 X 开头的搭配若**以另一个元素打头**（猫头鹰 以「猫」打头、葡萄酒不属于此类），
        # 说明它是那个元素的物件 → 丢掉（M-b：梦见猫头鹰 不该命中 鹰）。
        # 用「打头的字是不是已登记元素」判定，是数据判据、不是语义猜测；
        # 「老鹰」以「老」打头而「老」不是元素 → 保留（梦见老鹰 仍然命中 鹰）。
        if not g.startswith(X) and g[0] in exclude_names:
            continue
        # 该搭配若本身就是**另一条规则**（车祸/开车/火车…），由那条规则负责 ——
        # 否则同一段文本会被两条规则重复命中，吉凶合成还会被跨条目词频带偏
        # （实测：梦见出车祸了 命中 ['车','车祸'] → 综合 luck 从「中性」变成「凶多于吉」）。
        if g in exclude_names:
            continue
        out.append((g, n))
        if len(out) >= top:
            break
    return out


def jieba_guard(X: str, min_freq: int = LEXICON_MIN_FREQ) -> set:
    """通用词典护栏：以 X 开头的二字词的第二个字（这些字跟在 X 后面时 X 只是前缀）。"""
    try:
        import jieba
        jieba.initialize()
        freq = jieba.dt.FREQ
    except Exception:
        return set()
    return {w[1] for w, f in freq.items()
            if len(w) == 2 and w.startswith(X) and f >= min_freq}


LOOKAHEAD_RE = re.compile(r"\(\?=[^)]*\)")


def split_branches(match: str) -> list:
    """把 match 正则拆成「分支」列表（**先剥掉 lookahead 护栏组**再按 | 切）。

    护栏的字符类里本身含 `|`（`(?=[，,。！？…]|$|了|的|…)`），
    直接 `match.split("|")` 会把护栏内容当成一个个分支，误报「单字分支」
    （k55 的 k49 不变式测试就是这么被新格式打破的）。
    """
    return [b for b in LOOKAHEAD_RE.sub("", match or "").split("|") if b]


def is_lexicon_word(w: str, min_freq: int = LEXICON_MIN_FREQ) -> bool:
    """该串是否是通用词典里的**独立词**（→ 它指的是另一个东西，不是 X 的搭配）。"""
    try:
        import jieba
        jieba.initialize()
        return jieba.dt.FREQ.get(w, 0) >= min_freq
    except Exception:
        return False


def guard_chars(entry: dict, X: str, colloc_list: list) -> dict:
    """护栏字集合 = 词典来源 ∪ 语料来源（**都按数据门槛取字**）。

    - 词典来源：jieba 里以 X 开头的二字词的第二个字（freq ≥ LEXICON_MIN_FREQ）
      → 「水杯/火车/酒店/车门/火腿」这类**别的东西**都能被挡住；
    - 语料来源：语料里以 X 开头、且自己就是独立词条（heads ≥ 1）的更长核心串的第二个字
      → 语料自己认定「这是另一个梦对象」。

    护栏**不减去搭配词**（k58 修正）：护栏只作用于**头部形态**分支
    （`梦见X`）——被挡掉时，只要 X 后面跟的是真·搭配，搭配分支仍然命中。
    """
    lex = jieba_guard(X)
    corp = {c[len(X)] for c, n in entry["starts"].get(X, Counter()).items()
            if n >= 1 and len(c) > len(X)}
    return {"lexicon": sorted(lex), "corpus": sorted(corp)}


def build_match(X: str, entry: dict, top_colloc: int = 5,
                exclude_names: set = None) -> dict:
    """组装一条单字元素的 match（全部 ≥2 字分支）+ 证据。"""
    g = guard_chars(entry, X, None)
    colloc_list = collocations(entry, X, top=top_colloc,
                               guard=set(g["lexicon"]) | set(g["corpus"]),
                               exclude_names=exclude_names)
    forms = head_forms(entry, X)
    branches = []
    for form, cov in forms.items():
        # 头部形态：**结构化边界护栏（白名单）**——只认裸形态（X 后紧跟标点/句末/虚词）。
        # 不依赖词典：实测 jieba 根本没收 水立方/水逆/猫咖/猫山王 这类新词网语，
        # 词典式负向护栏永远挡不住；白名单只看「X 后面那个字是不是虚词」，对新词恒成立。
        branches.append({"branch": f"{form}(?={TRAILING_BOUNDARY})",
                         "kind": "head_form", "coverage": cov})
    for gram, cov in colloc_list:
        branches.append({"branch": gram, "kind": "collocation", "coverage": cov})
    return {
        "element": X,
        "match": "|".join(b["branch"] for b in branches),
        "branches": branches,
        "guard": {"mode": "trailing_boundary", "allowed": TRAILING_BOUNDARY,
                  "lexicon": g["lexicon"], "corpus": g["corpus"]},
        "head_count": entry["head"].get(X, 0),
        "entry_hits": entry["hits"].get(X, 0),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="单字意象搭配词统计")
    ap.add_argument("--elements", nargs="*", default=[])
    ap.add_argument("--all-single", action="store_true",
                    help="对规则表里全部单字元素做统计")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--out", default=str(REPORTS / "single_char_collocations.json"))
    args = ap.parse_args()

    elements = set(args.elements)
    if args.all_single or not elements:
        from src.engines.dream_rules import DREAM_PATTERN_RULES
        elements |= {r["name"] for r in DREAM_PATTERN_RULES if len(r["name"]) == 1}
    # 传入 allow_multi：head_count 对所有元素都算（多字元素也用于 I-4 标签一致性）
    entry = corpus_stats(elements)

    out = {}
    for X in sorted(elements):
        out[X] = build_match(X, entry, top_colloc=args.top)

    Path(args.out).write_text(json.dumps(
        {"generated_at": now_iso(), "elements": out,
         "params": {"top_colloc": args.top, "lexicon_min_freq": LEXICON_MIN_FREQ,
                    "colloc_min_count": COLLOC_MIN_COUNT}},
        ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"[colloc] 落盘 {len(out)} 个元素 → {args.out}")

    csv_path = REPORTS / "single_char_collocations.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["element", "head_count", "entry_hits", "match", "guard_lexicon", "guard_corpus"])
        for X, d in out.items():
            w.writerow([X, d["head_count"], d["entry_hits"], d["match"],
                        "".join(d["guard"]["lexicon"]), "".join(d["guard"]["corpus"])])

    for X in sorted(elements)[:12]:
        d = out[X]
        log(f"  {X}: head={d['head_count']} hits={d['entry_hits']} "
            f"branches={[(b['branch'][:12], b['coverage']) for b in d['branches'][:4]]} "
            f"guard={''.join(d['guard']['lexicon'])[:14]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
