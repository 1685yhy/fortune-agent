#!/usr/bin/env python3
"""规则层生成器：**从语料统计结果**产出 80+ 条梦境模式（brief §2）。

输入（全部由 stats_elements.py / public_domain.py 产出，均为真实语料统计）：
- reports/element_freq_top.csv     元素覆盖率排名
- reports/element_symbols.json     每元素的核心象征候选（语料共现实词）
- clean/public_domain_quotes.jsonl 公版古籍条文（传统释义来源）
- data/competitor_data/zgjm_*.jsonl 站点自有分类（→ 规则「类型」的数据来源）

产出：
- src/engines/dream_rules.py   生成的规则模块（入库、可复现、带统计依据）
- reports/rule_table.csv       规则清单（含覆盖量），便于人工复核

设计口径：
- **类型**：优先取语料来源站点的自有分类（dongwu→动物类 等，站点分类是数据
  不是我们编的）；无站点分类时按吉凶/情绪统计归入 吉兆类/警示类/中性类。
- **核心象征**：取该元素语料正文共现最高的实词（已滤语料套话）。
- **情绪基调**：由该元素语料的吉/凶判词倾向 + 高频情绪词合成。
- **传统释义**：优先引公版古籍条文；无则取该元素语料中最高频的判词句。
- **覆盖量**：含该元素的语料条目数（真实统计值，可为 0——见「交通驾驶族」）。
- **match 正则**：每个候选分支都 ≥2 字（k49 教训：单字会前缀误伤）；
  单字元素（蛇/水/车…）用 n-gram 统计出的 ≥2 字组合成 pattern。

用法：
    python scripts/k55_dream/build_rules.py
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
from scripts.k55_dream.stats_elements import (  # noqa: E402
    META_STOPWORDS, load_entries, normalize_core,
)

REPORTS = DATA_ROOT / "reports"
OUT_MODULE = Path(__file__).resolve().parents[2] / "src" / "engines" / "dream_rules.py"
OUT_TABLE = REPORTS / "rule_table.csv"

# 站点自有分类 → 我们对外呈现的「梦境类型」（站点分类是数据，映射表是口径）
SITE_CAT_TO_TYPE = {
    "dongwu": "动物类", "zhiwu": "植物类", "wupin": "物品类",
    "shenghuo": "生活类", "ziran": "自然类", "guishen": "鬼神类",
    "jianzhu": "建筑类", "renwu": "人物类", "huodong": "活动类",
    "yunfujiemeng": "孕育类", "mengjing": "心理类", "wenhua": "文化类",
    "health": "健康类", "qita": "其他类",
}

# 叙述性动词/虚词：语料里高频但不是「梦境意象」，进规则层只会产生假命中
# （实测清单：变成/参加/发生/得到/看到/送给/交谈/出来/成为/没有/开始…）
NARRATIVE_STOP = {
    "变成", "参加", "发生", "得到", "看到", "送给", "交谈", "出来", "成为",
    "没有", "开始", "继续", "进行", "出现", "遇到", "发现", "告诉", "知道",
    "觉得", "感觉", "认为", "希望", "方向", "事情", "时候", "地方", "东西",
    "问题", "情况", "身上", "里面", "外面", "旁边", "一起", "可以", "应该",
    "什么", "怎么", "因为", "所以", "但是", "而且", "并且", "或者", "还是",
    "就是", "不是", "就是", "一样", "非常", "特别", "真的", "好像", "突然",
    "一直", "已经", "正在", "之后", "之前", "现在", "最近", "很多", "很多",
    "话", "时", "事", "空", "成", "梦", "夢", "人", "好", "多", "大", "小",
    # 触发词/元词：绝不能变成规则条目（「梦见」当正则会命中一切梦境文本）
    "梦见", "梦到", "梦见了", "做梦", "解梦", "周公", "梦境", "预兆", "征兆",
}

# brief 明令必须覆盖的交通/驾驶族（控制方真实梦例所在族）。
# 覆盖量取自真实语料统计（含新增爬取的真实网友梦境），统计不到就如实记 0。
MANDATORY_DRIVING = {
    "开车": {"match": ["开车", "驾驶", "自驾", "开夜车", "开快车"],
             "type": "现代类", "tone": "掌控感与失控担忧"},
    "车祸": {"match": ["车祸", "出车祸", "撞车", "翻车", "撞人", "被撞", "追尾"],
             "type": "现代类", "tone": "惊吓与安全焦虑"},
    "停车": {"match": ["停车", "停车位", "泊车", "倒车", "车位"],
             "type": "现代类", "tone": "秩序与掌控需求"},
    "找不到车": {"match": ["找不到车", "车不见", "车没了", "车丢了", "忘了车停"],
                 "type": "现代类", "tone": "失控与遗失焦虑"},
    "迷路": {"match": ["迷路", "找不到路", "找不到回", "走错路", "认不得路", "绕不出去"],
             "type": "压力类", "tone": "方向感缺失与焦虑"},
    "赶不上车": {"match": ["赶不上", "没赶上车", "错过车", "误车", "误机", "来不及",
                        "坐过站", "迟到", "赶车", "赶飞机"],
                 "type": "压力类", "tone": "时间压迫与错失恐惧"},
    "堵车": {"match": ["堵车", "塞车", "堵在路上", "堵在路"],
             "type": "现代类", "tone": "停滞与无力感"},
    "坐车": {"match": ["坐车", "坐公交", "坐地铁", "乘公交", "坐火车", "坐飞机", "搭车"],
             "type": "现代类", "tone": "被动与随波"},
}

# 吉凶判定词（语料判词口径，用于从真实语料统计吉凶倾向）
JI_RE = re.compile(r"(大吉|吉利|吉兆|吉凶指数\d+【?大吉|好运|发财|得财|进财|升官|富贵|"
                   r"喜事|顺利|成功|贵子|添丁|有财|主吉|大吉昌)")
XIONG_RE = re.compile(r"(大凶|不祥|凶兆|灾祸|倒霉|损失|破财|疾病|病痛|死亡|丧事|口舌|"
                      r"官司|离别|不顺|小人|血光|主凶|凶事|有灾|患病)")
SENT_SPLIT_RE = re.compile(r"[。！？!?\n]")


def log(msg: str) -> None:
    print(f"{now_iso()} {msg}", flush=True)


# ── 载入统计产物 ────────────────────────────────────────────────────
def load_stats() -> tuple:
    top = list(csv.DictReader(open(REPORTS / "element_freq_top.csv", encoding="utf-8")))
    symbols = json.loads((REPORTS / "element_symbols.json").read_text(encoding="utf-8"))
    full = {}
    for line in (REPORTS / "element_freq_full.tsv").read_text(encoding="utf-8").splitlines()[1:]:
        el, c = line.split("\t")
        full[el] = int(c)
    return top, symbols, full


def load_site_categories() -> dict:
    """站点自有分类 → {元素: 类型}（数据来源：competitor_data 的 category 字段）。"""
    out: dict = {}
    for p in sorted((Path(__file__).resolve().parents[2] / "data" / "competitor_data").glob("*.jsonl")):
        try:
            for line in p.open(encoding="utf-8"):
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                cat, q = d.get("category"), (d.get("query") or "")
                if not cat or cat not in SITE_CAT_TO_TYPE:
                    continue
                el = normalize_core(q)
                if not el:
                    continue
                out.setdefault(el[:6], SITE_CAT_TO_TYPE[cat])
        except Exception:
            continue
    return out


def load_classic_quotes() -> dict:
    """{元素: [公版古籍条文]}（用于「一句传统释义」）。"""
    out: dict = defaultdict(list)
    p = DATA_ROOT / "clean" / "public_domain_quotes.jsonl"
    if not p.exists():
        return out
    for line in p.open(encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        el = (d.get("element") or "").strip()
        if el:
            out[el].append({"text": d["content"], "book": d.get("book", "")})
    return out


def load_ngram_map() -> dict:
    """{元素: [(n-gram, 频次)]}（从 element_freq_top.csv 的 top_ngrams 列）。

    必须走 csv.DictReader：样本列含引号内换行，按行切会解析错位。
    """
    out = {}
    with open(REPORTS / "element_freq_top.csv", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            grams = []
            for g in (row["top_ngrams"] or "").split(","):
                m = re.match(r"(.+?)\((\d+)\)$", g)
                if m and len(m.group(1)) >= 2:
                    grams.append((m.group(1), int(m.group(2))))
            out[row["element"]] = grams
    return out


# ── 语料扫描：每元素的判词句 / 吉凶倾向 ──────────────────────────────
def scan_corpus(elements: set) -> tuple:
    entries = load_entries()
    seen, uniq = set(), []
    for e in entries:
        core = normalize_core(e["title"])
        if core and core not in seen:
            seen.add(core)
            uniq.append(e)
    log(f"[scan] 去重语料 {len(uniq)} 条，目标元素 {len(elements)} 个")

    sentences: dict = defaultdict(Counter)
    ji: Counter = Counter()
    xiong: Counter = Counter()
    for e in uniq:
        core, text = e["core"] if "core" in e else normalize_core(e["title"]), e["content"]
        hits = [el for el in elements if el in core]
        if not hits:
            continue
        for s in SENT_SPLIT_RE.split(text or ""):
            s = s.strip()
            if not (6 <= len(s) <= 120):
                continue
            for el in hits:
                if el in s and re.search(r"[主有宜忌吉凶]", s):
                    sentences[el][s] += 1
        for el in hits:
            if JI_RE.search(core + (text or "")[:400]):
                ji[el] += 1
            if XIONG_RE.search(core + (text or "")[:400]):
                xiong[el] += 1
    return uniq, sentences, ji, xiong


def luck_from_counts(n_ji: int, n_xiong: int) -> str:
    """真实语料吉/凶判词计数 → 吉凶基线（与既有 DREAM_LUCK_RANK 词表一致）。"""
    tot = n_ji + n_xiong
    if tot == 0:
        return "吉多于凶"          # 语料无判词信号：保守取中性偏吉（不吓人）
    r = n_ji / tot
    if r >= 0.8:
        return "大吉"
    if r >= 0.62:
        return "吉"
    if r >= 0.42:
        return "吉多于凶"
    if r >= 0.25:
        return "凶多于吉"
    return "凶"


def tone_from(n_ji: int, n_xiong: int, symbols: list) -> str:
    """情绪基调：由语料吉凶倾向 + 象征词合成（数据导出，非人工撰写）。"""
    tot = n_ji + n_xiong
    r = (n_ji / tot) if tot else 0.55
    if r >= 0.62:
        base = "偏吉、期待与安抚"
    elif r >= 0.42:
        base = "吉凶掺半、观望"
    elif r >= 0.25:
        base = "偏凶、警惕与压力"
    else:
        base = "警示、焦虑与不安"
    focus = "、".join(symbols[:2])
    return f"{base}（语料关注点：{focus}）" if focus else base


def main() -> int:
    ap = argparse.ArgumentParser(description="生成解梦规则层（80+ 条，语料统计得出）")
    ap.add_argument("--top", type=int, default=140)
    ap.add_argument("--min-coverage", type=int, default=40)
    args = ap.parse_args()

    top, symbols_map, full = load_stats()
    site_cat = load_site_categories()
    classics = load_classic_quotes()
    ngrams = load_ngram_map()
    log(f"[load] Top 表 {len(top)} 行；站点分类映射 {len(site_cat)} 元素；"
        f"古籍引文 {len(classics)} 元素")

    # 候选：Top 表中覆盖量达标、且不在叙述性停用词里
    cands = []
    for r in top:
        el = r["element"]
        if el in NARRATIVE_STOP or len(el) < 1:
            continue
        if int(r["coverage"]) < args.min_coverage:
            continue
        cands.append(el)
    cands = cands[:args.top]
    log(f"[pick] 语料 Top 候选 {len(cands)} 个（覆盖 ≥{args.min_coverage}）")

    elements = set(cands) | set(MANDATORY_DRIVING)
    uniq, sentences, ji, xiong = scan_corpus(elements)

    # 交通驾驶族的覆盖量：在**真实语料**里现算（含新增爬取的真实网友梦境文本）
    real_text_hits: Counter = Counter()
    for e in uniq:
        blob = (e["title"] or "") + (e.get("content") or "")[:200]
        for name, spec in MANDATORY_DRIVING.items():
            if any(m in blob for m in spec["match"]):
                real_text_hits[name] += 1

    rules = []
    for el in cands:
        cov = full.get(el, 0)
        syms = [s for s in symbols_map.get(el, []) if s != el][:6]
        if not syms:
            syms = ["情境变化", "现实压力"]
        ptype = site_cat.get(el) or site_cat.get(el[:2])
        if not ptype:
            r = (ji[el] / (ji[el] + xiong[el])) if (ji[el] + xiong[el]) else 0.5
            ptype = "吉兆类" if r >= 0.62 else ("警示类" if r < 0.35 else "中性类")
        # match 正则：≥2 字分支（k49）；单字元素用 n-gram 补上下文。
        # n-gram 过滤：只取出现 ≥3 次且长度 2~3 的（4 字 n-gram 常跨越短语边界，
        # 实测会产出「天开车」「友开车」这类分词碎片，命中率低还易误伤）。
        alts = [el] if len(el) >= 2 else []
        for g, c in sorted(ngrams.get(el, []), key=lambda x: -x[1]):
            if c < 3 or not (2 <= len(g) <= 3) or g == el or g in alts:
                continue
            alts.append(g)
            if len(alts) >= 5:
                break
        if not alts:
            continue
        alts = sorted(set(alts), key=len, reverse=True)[:5]
        gloss = ""
        for q in classics.get(el, [])[:1]:
            gloss = f"《{q['book']}》：{q['text']}"
        if not gloss and sentences[el]:
            gloss = f"{sentences[el].most_common(1)[0][0]}（语料高频判词）"
        if not gloss:
            gloss = f"语料中含「{el}」的梦境共 {cov} 条，传统判词以吉凶两向并存"
        rules.append({
            "name": el, "match": "|".join(re.escape(a) for a in alts),
            "type": ptype, "symbols": syms,
            "tone": tone_from(ji[el], xiong[el], syms),
            "luck": luck_from_counts(ji[el], xiong[el]),
            "gloss": gloss, "coverage": cov,
            "evidence": {"ji": ji[el], "xiong": xiong[el],
                         "sentences": len(sentences[el])},
            "source": "corpus_stats",
        })

    # 强制族（brief 明令必须覆盖）：**无论语料是否已生成同名条目，都以 brief
    # 口径的 match/type/tone 覆盖**——语料统计版会掺入分词碎片（实测「开车」拿到
    # 「朋友开车/天开车/友开车」这类 n-gram），而这一族是控制方实测梦例所在族，
    # 匹配面必须干净、可预期。覆盖量仍取真实统计值。
    by_name = {r["name"]: r for r in rules}
    for name, spec in MANDATORY_DRIVING.items():
        if name in by_name:
            r = by_name[name]
            r["match"] = "|".join(re.escape(a) for a in spec["match"])
            r["type"] = spec["type"]
            r["tone"] = spec["tone"]
            r["coverage"] = max(r["coverage"], real_text_hits.get(name, 0))
            r["source"] = "corpus_stats+mandatory_driving_family"
            r["evidence"]["real_dream_text_hits"] = real_text_hits.get(name, 0)
            continue
        cov = real_text_hits.get(name, 0)
        syms = [s for s in symbols_map.get(name, []) if s != name][:6] or \
               [s for s in symbols_map.get("车", []) if s != "车"][:6]
        gloss = ""
        for q in classics.get(name, [])[:1]:
            gloss = f"《{q['book']}》：{q['text']}"
        if not gloss and sentences.get(name):
            gloss = f"{sentences[name].most_common(1)[0][0]}（语料高频判词）"
        if not gloss:
            gloss = ("词典式语料对该族覆盖很低（现实驾驶类梦境是现代新增题材，"
                     "古典梦书成书时无此类），释义取自真实网友梦境语料的高频情境")
        rules.append({
            "name": name, "match": "|".join(re.escape(a) for a in spec["match"]),
            "type": spec["type"], "symbols": syms, "tone": spec["tone"],
            "luck": luck_from_counts(ji.get(name, 0), xiong.get(name, 0)),
            "gloss": gloss, "coverage": cov,
            "evidence": {"ji": ji.get(name, 0), "xiong": xiong.get(name, 0),
                         "real_dream_text_hits": cov},
            "source": "corpus_stats+mandatory_driving_family",
        })

    # 去重（按 name）+ 排序（覆盖量降序，强制族置前）
    seen, uniq_rules = set(), []
    for r in rules:
        if r["name"] in seen:
            continue
        seen.add(r["name"])
        uniq_rules.append(r)
    uniq_rules.sort(key=lambda r: (r["name"] not in MANDATORY_DRIVING, -r["coverage"]))
    log(f"[rules] 生成规则 {len(uniq_rules)} 条（其中强制交通族 {len(MANDATORY_DRIVING)} 条）")

    # 写 CSV 清单
    with open(OUT_TABLE, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "type", "coverage", "luck", "tone", "symbols",
                    "match", "gloss", "ji", "xiong"])
        for r in uniq_rules:
            w.writerow([r["name"], r["type"], r["coverage"], r["luck"], r["tone"],
                        "、".join(r["symbols"]), r["match"], r["gloss"],
                        r["evidence"].get("ji", 0), r["evidence"].get("xiong", 0)])

    # 写 Python 模块
    lines = [
        '"""解梦规则层（**自动生成，勿手改**）。',
        "",
        f"由 scripts/k55_dream/build_rules.py 于 {now_iso()} 从真实语料统计生成：",
        "- 元素清单与覆盖量：reports/element_freq_top.csv（去重语料 "
        f"{len(uniq)} 条）",
        "- 核心象征：reports/element_symbols.json（语料共现实词）",
        "- 传统释义：clean/public_domain_quotes.jsonl（公版古籍条文）优先，",
        "  否则取语料高频判词句",
        "- 类型：站点自有分类（data/competitor_data 的 category 字段）优先",
        "- 吉凶：语料判词词频统计（JI/XIONG 正则计数）",
        "",
        "约束：match 每个分支均 ≥2 字（k49 教训：单字条目会前缀误伤）。",
        '"""',
        "",
        "# Hall & Van de Castle 梦境内容常模（来源：dreams.ucsc.edu/Norms，",
        "# 男性 500 梦 / 女性 491 梦 / 合计 991 梦；本仓库 evidence 已存档该页）",
        "HVDC_NORMS = {",
        '    "source": "Hall & Van de Castle norms, dreams.ucsc.edu/Norms (991 dreams)",',
    ]
    for k, v in [
        ("male_female_percent", "57%"), ("familiarity_percent", "52%"),
        ("friends_percent", "35%"), ("animal_percent", "5%"),
        ("aggression_per_char", ".28"), ("friendliness_per_char", ".21"),
        ("sexuality_per_char", ".04"),
        ("dreams_with_aggression", "45%"), ("dreams_with_friendliness", "40%"),
        ("dreams_with_sexuality", "8%"), ("dreams_with_misfortune", "35%"),
        ("dreams_with_success", "11%"), ("dreams_with_failure", "12%"),
        ("indoor_setting_percent", "55%"), ("familiar_setting_percent", "69%"),
        ("negative_emotions_percent", "80%"),
    ]:
        lines.append(f'    "{k}": "{v}",')
    lines += [
        "}",
        "",
        "# 「现实投影」口径（产品层：传统释义与现代视角分层呈现）",
        "REALITY_PROJECTION = {",
        '    "continuity": "梦主要是清醒生活的延续与复现，多数梦境元素来自做梦者'
        '近期的现实经历、关切与情绪（连续性假设，Hall 的梦内容研究传统）",',
        '    "typical_dreams": "被追、坠落、考试、掉牙等『典型梦』在人群中的出现'
        '比例其实很低（约 1% 量级），大部分人并不会频繁梦到它们——所以不必因'
        '梦到某个传统『凶兆』而恐慌",',
        '    "negative_bias": "常模显示梦中负面情绪占比高达 80%（HVDC 常模），'
        '负面梦是常态而非预兆",',
        '    "norm_ref": "HVDC_NORMS",',
        "}",
        "",
        "# ── 规则条目（按覆盖量排序，交通驾驶族置前） ──────────────────",
        "DREAM_PATTERN_RULES = [",
    ]
    for r in uniq_rules:
        lines.append("    {")
        lines.append(f'        "name": {r["name"]!r},')
        lines.append(f'        "match": {r["match"]!r},')
        lines.append(f'        "type": {r["type"]!r},')
        lines.append(f'        "symbols": {r["symbols"]!r},')
        lines.append(f'        "tone": {r["tone"]!r},')
        lines.append(f'        "luck": {r["luck"]!r},')
        lines.append(f'        "gloss": {r["gloss"]!r},')
        lines.append(f'        "coverage": {r["coverage"]!r},')
        lines.append(f'        "source": {r["source"]!r},')
        lines.append("    },")
    lines += ["]", "", f"RULE_COUNT = {len(uniq_rules)}", ""]
    OUT_MODULE.write_text("\n".join(lines), encoding="utf-8")
    log(f"[rules] 模块 → {OUT_MODULE}（{len(uniq_rules)} 条）")

    for r in uniq_rules[:15]:
        log(f"    {r['name']}\t{r['type']}\t覆盖{r['coverage']}\t{r['luck']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
