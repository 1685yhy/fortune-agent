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
#
# r2 整改（控制方 2026-09-18 裁决）：
# ① **luck 一律「中性」**，不许按语料词频自动判吉——「梦见车祸是吉」是信任事故。
#    语料里这些词确实常出现在吉向句子里，但那些判词谈的是**别的场景**
#    （如「梦见老人出车祸…虽有财运可得」），拿词频自动推吉凶是错的。
# ② **match 收窄到车辆语义**：「迟到/来不及」不是车（实测「梦见上学迟到了」
#    被误判成赶不上车），已移出本族，另立独立规则（见 MANDATORY_EXTRA）。
# ③ gloss 必须取**同场景句**，取不到走诚实兜底（见 same_scenario_gloss）。
MANDATORY_DRIVING = {
    "开车": {"match": ["开车", "驾驶", "自驾", "开夜车", "开快车"],
             "type": "现代类", "tone": "提醒：掌控感与失控担忧", "luck": "中性"},
    "车祸": {"match": ["车祸", "出车祸", "撞车", "翻车", "撞人", "被撞", "追尾"],
             "type": "现代类", "tone": "提醒：惊吓与安全焦虑", "luck": "中性"},
    "停车": {"match": ["停车", "停车位", "泊车", "倒车", "车位"],
             "type": "现代类", "tone": "提醒：秩序与掌控需求", "luck": "中性"},
    "找不到车": {"match": ["找不到车", "车不见", "车没了", "车丢了", "忘了车停"],
                 "type": "现代类", "tone": "提醒：失控与遗失焦虑", "luck": "中性"},
    "迷路": {"match": ["迷路", "找不到路", "找不到回", "走错路", "认不得路", "绕不出去"],
             "type": "压力类", "tone": "提醒：方向感缺失与焦虑", "luck": "中性"},
    "赶不上车": {"match": ["赶不上车", "没赶上车", "错过车", "误车", "误机",
                        "坐过站", "赶车", "赶飞机", "错过班车", "错过火车",
                        "错过航班", "错过地铁", "没赶上"],
                 "type": "压力类", "tone": "提醒：时间压迫与错失恐惧", "luck": "中性"},
    "堵车": {"match": ["堵车", "塞车", "堵在路上", "堵在路"],
             "type": "现代类", "tone": "提醒：停滞与无力感", "luck": "中性"},
    "坐车": {"match": ["坐车", "坐公交", "坐地铁", "乘公交", "坐火车", "坐飞机", "搭车"],
             "type": "现代类", "tone": "提醒：被动与随波", "luck": "中性"},
}

# 从交通族里**拆出来**的独立规则（r2 I-3）：「迟到/来不及」表达的是时间压迫，
# 不含车辆语义，塞进「赶不上车」会误伤（实测「梦见上学迟到了」）。
MANDATORY_EXTRA = {
    "迟到": {"match": ["迟到", "来不及", "赶时间", "时间不够", "误点", "拖延"],
             "type": "压力类", "tone": "提醒：时间压力与自责", "luck": "提醒类"},
}

# 规则候选的停用词 = 叙述词 ∪ 体裁性元词（r2 I-4）。
# 元词表来自 stats_elements.META_STOPWORDS（其注释原文就是「语料体裁的产物，
# 不是梦的象征」），初版只把它用在 symbols 上，没用在规则候选上 → 工作/表示/
# 生活/说明/关系/可能/方面/象征/女性/运势/心理/梦者 这些词混进了规则表。
RULE_STOPWORDS = set(META_STOPWORDS) | set(NARRATIVE_STOP) | {
    "意味", "受到", "看见", "认识", "起来", "感到", "作为", "关于", "对于",
    "以及", "或者", "如果", "虽然", "然后", "于是", "终于", "结果", "原因",
    "状态", "程度", "方式", "内容", "部分", "以上", "以下", "其中", "其他",
    "一切", "所有", "各种", "一种", "这个", "那个", "这样", "那样",
}


# 吉凶判定词（语料判词口径，用于从真实语料统计吉凶倾向）
JI_RE = re.compile(r"(大吉|吉利|吉兆|吉凶指数\d+【?大吉|好运|发财|得财|进财|升官|富贵|"
                   r"喜事|顺利|成功|贵子|添丁|有财|主吉|大吉昌)")
XIONG_RE = re.compile(r"(大凶|不祥|凶兆|灾祸|倒霉|损失|破财|疾病|病痛|死亡|丧事|口舌|"
                      r"官司|离别|不顺|小人|血光|主凶|凶事|有灾|患病)")
SENT_SPLIT_RE = re.compile(r"[。！？!?\n]")
# 标题/疑问式句子 + 页面套话（都无解读内容，选中当 gloss 等于没给依据）
TITLE_JUNK_RE = re.compile(
    r"(好不好|是什么意思|是什么预兆|代表着什么|代表什么|意味着什么|怎么回事|"
    r"怎么办|有什么预兆|什么征兆|的解析|请看下面|是什么意思呢)[？?。]?$"
    r"|(希望能为网友答疑解惑|走出迷途|转载请注明|周公解梦权威解梦|由.{0,10}整理|"
    r"小编|权威解梦|免费查询|本文来源|点击查看|扫一扫|关注我们)")


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
    """站点自有分类 → {元素: 类型}。

    数据来源（都是站点自己的分类，不是我们编的）：
    1. data/competitor_data/*.jsonl 的 `category` 字段（query → 分类）；
    2. k55 新抓的好梦网详情记录里的 `category`（页面所属栏目目录）。
    """
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
                if el:
                    out.setdefault(el[:6], SITE_CAT_TO_TYPE[cat])
        except Exception:
            continue
    # k55 抓取产物（好梦网栏目分类）
    hmw = DATA_ROOT / "raw" / "haomengwang_detail.jsonl"
    if hmw.exists():
        for line in hmw.open(encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            cat = d.get("category")
            if not cat or cat not in SITE_CAT_TO_TYPE:
                continue
            el = normalize_core(d.get("title") or "")
            if el:
                out.setdefault(el[:6], SITE_CAT_TO_TYPE[cat])
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
            # content = 「<条文>。（《书名》·转录未校勘）」—— 出处由 gloss 统一标注，
            # 这里剥掉，免得「《敦煌本梦书》（转录未校勘）记载：…（《敦煌本梦书》·转录未校勘）」
            text = re.sub(r"。（《[^》]+》[^）]*）\s*$", "", d["content"])
            out[el].append({"text": text, "book": d.get("book", "")})
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

    # r3 I-A 修法：**句池与计数共用同一 scope**。
    # 旧实现的句池多了一道 `[主有宜忌吉凶]` 字面过滤，而 ji/xiong 计数走
    # same_scenario() 拼出的 scope **没有这道过滤** → 「梦见刀，不祥之兆，会面临
    # 困难。」这类判词（不含「主/有/宜/忌/吉/凶」字面）只进计数、不进句池 →
    # gloss 找不到 → 假兜底（「语料里没有匹配到…」）+ luck 被强制中性，
    # 与自身 type/tone 自相矛盾。现在两者共用 scope，不变式：
    # **fallback_no_same_scenario ⟹ ji+xiong == 0**（已加测试）。
    sentences: dict = defaultdict(Counter)       # 同场景句池（与计数同源）
    head_sentences: dict = defaultdict(Counter)  # **词条就是该元素**的同场景句
    ji: Counter = Counter()
    xiong: Counter = Counter()
    for e in uniq:
        core, text = normalize_core(e["title"]), e["content"]
        hits = [el for el in elements if el in core]
        if not hits:
            continue
        is_head = core in elements          # 「梦见X」的标题正好就是这个元素
        all_sents = [s.strip() for s in SENT_SPLIT_RE.split(text or "")]
        all_sents = [s for s in all_sents if 6 <= len(s) <= 120]
        # 去掉「标题/疑问式」句子（页面把标题重复进正文，如「梦见了异性好不好」
        # 「梦见X是什么意思」）——它们不含解读内容，选中当 gloss 等于没给依据
        all_sents = [re.sub(r"[\(（](©|&|版权|来源)[^)）]*[)）]", "", s).strip()
                     for s in all_sents]
        all_sents = [s for s in all_sents
                     if 6 <= len(s) <= 120 and not TITLE_JUNK_RE.search(s)]
        for el in hits:
            # 同场景证据：词条即该元素（整条都算），或句子的主角是该元素
            scope_sents = all_sents if is_head else \
                [s for s in all_sents if same_scenario(s, el)]
            if not scope_sents:
                continue
            for s in scope_sents:
                sentences[el][s] += 1
                if is_head:
                    head_sentences[el][s] += 1
            blob = " ".join(scope_sents)
            if JI_RE.search(blob):
                ji[el] += 1
            if XIONG_RE.search(blob):
                xiong[el] += 1
    log(f"[scan] 同场景句池覆盖 {len(sentences)} 个元素；"
        f"其中「词条即元素」覆盖 {len(head_sentences)} 个")
    return uniq, sentences, ji, xiong, head_sentences


# 同场景判词：句子里的元素必须是**这句梦的主角**（起式即 梦见X／梦X／见X…），
# 而不是「梦见老人出车祸」这种元素只是配角、判词谈的是别的场景的句子。
SAME_SCENARIO_PREFIXES = ("梦见", "梦到", "梦", "见")


def same_scenario(sentence: str, el: str) -> bool:
    """元素必须是这句梦的**主角且不被复合词吞掉**。

    r2 加严（第一版仍不够）：只要求「句子以梦见+元素开头」会把
    「梦见**开车撞人**…」当成「开车」的同场景判词、把「梦见**水泥**…」
    当成「水」的（实测两例都出现了）。
    现在要求元素后面紧跟**分隔符或常见虚词**——即元素在该句里是完整的词，
    而不是更长复合词/别的场景的一部分（开车撞人 → 归「开车撞人」那个场景，
    不归「开车」）。
    """
    s = sentence.strip()
    tail = r"(?:了|着|过|的|者|时|后|之)?"
    delim = r"[，,。：:；;！？!?、\s]|$"
    for p in SAME_SCENARIO_PREFIXES:
        if re.match(rf"^{p}{re.escape(el)}{tail}(?:{delim})", s):
            return True
    return False


# 一致性检查用的**更宽极性词表**（r3 I-B 加严）：
# 计数口径（JI_RE/XIONG_RE）只认判词术语，漏掉了「不很顺利」「慎防小人」这类
# 表述 —— 实测「异性」luck=大吉 却配「代表**不很顺利**」（否定式，JI_RE 里的
# 「顺利」被反向使用）。否定前缀 + 明确负面词一并纳入，只用于**一致性判定**。
NEG_PREFIX_RE = re.compile(r"(不|没|未|难以|无法|避免|别|勿)\s*(很|太|会|能|要|可)?\s*$")
EXTRA_NEG_RE = re.compile(r"(损失|不利|不顺|慎防|小心|谨慎|警惕|防范|挫折|失败|"
                          r"纠纷|忧伤|烦恼|灾|病痛|愁|破坏|障碍|阻碍|是非|口舌)")


def sentence_polarity(s: str, extended: bool = True) -> str:
    """句子的吉凶极性：吉/凶/空（同时含两向或都不含 → 空）。

    extended=True（默认，用于一致性校验）：识别否定式与更多负面表述。
    """
    pos = False
    negated_pos = False
    for m in JI_RE.finditer(s):
        prefix = s[max(0, m.start() - 3):m.start()]
        if NEG_PREFIX_RE.search(prefix):
            negated_pos = True          # 「不很顺利」= 反向使用吉词 → 计入负面证据
        else:
            pos = True
    neg = bool(XIONG_RE.search(s)) or negated_pos
    if extended:
        neg = neg or bool(EXTRA_NEG_RE.search(s))
        for m in XIONG_RE.finditer(s):
            prefix = s[max(0, m.start() - 3):m.start()]
            if NEG_PREFIX_RE.search(prefix):     # 「不凶」这类反向否定
                neg = False
    if pos and neg:
        return ""            # 双向混合 → 视为无极性（不判冲突）
    return "吉" if pos else ("凶" if neg else "")


def luck_polarity(luck: str) -> str:
    if luck in ("大吉", "吉", "吉多于凶"):
        return "吉"
    if luck in ("凶", "凶多于吉"):
        return "凶"
    return ""


def same_scenario_gloss(el: str, sentences: dict, classics: dict,
                        head_sentences: dict = None,
                        want_luck: str = "") -> tuple:
    """取**同场景**释义依据；取不到则诚实兜底。

    证据档次（从强到弱，写进 gloss_evidence 字段，可复核）：
    1. `classic_quote`          —— 该元素的古籍引文（敦煌本梦书/梦林玄解/断梦秘书…）
    2. `head_entry`             —— 语料里**词条就是该元素**（「梦见X」）的条目判词句
    3. `corpus_same_scenario`   —— 句子的主角是该元素（梦见X…）的语料判词句
    4. `fallback_no_same_scenario` —— 都没有：明说是泛化倾向，不做吉凶判断

    r2 I-2：旧实现直接取「含该元素」的最高频句，会拿别的场景顶
    （车祸 → 「梦见老人出车祸…虽有财运可得」；水 → 「梦见水泥…」），
    并把 luck 带偏。
    r3 I-B：再加**极性一致性**——优先取与聚合 luck 同向的句子，避免
    「传统倾向=吉多于凶」与「释义依据=…是凶兆」同时出现在一行里打对台。
    """
    want_luck_pol = luck_polarity(want_luck)
    def pick(pool, kind, tag):
        """从池里挑与 want_luck **同向**的句子；无同向则退无极性，再退反向。"""
        ranked = {"same": [], "none": [], "opp": []}
        for sent, _ in pool.most_common(40):
            pol = sentence_polarity(sent)
            if not pol or not want_luck_pol:
                ranked["none" if not pol else "same"].append((sent, kind, tag))
            elif pol == want_luck_pol:
                ranked["same"].append((sent, kind, tag))
            else:
                ranked["opp"].append((sent, kind, tag))
        for key in ("same", "none", "opp"):
            if ranked[key]:
                sent, k, t = ranked[key][0]
                return f"{sent}（{t}）", k
        return None

    for q in classics.get(el, [])[:1]:
        # 古籍引文也做极性校验：反向时不用（避免「luck=吉」配「不祥」引文）
        if want_luck_pol and sentence_polarity(q["text"]) not in ("", want_luck_pol):
            continue
        return f"《{q['book']}》（转录未校勘）记载：{q['text']}", "classic_quote"
    for pool, kind, tag in (((head_sentences or {}).get(el, Counter()), "head_entry",
                             f"语料「梦见{el}」词条原文"),
                            (sentences.get(el, Counter()), "corpus_same_scenario",
                             "语料同场景判词")):
        got = pick(pool, kind, tag)
        if got:
            return got
    return ("", "fallback_no_same_scenario")


# 吉凶语义字：fallback（无同场景依据）的规则不得带这类「象征词」——
# 否则「车祸」的核心象征里会出现「吉祥」（来自别的场景的共现），
# 与「不做吉凶判断」自相矛盾（r2 I-2 同类问题）。
LUCK_SEMANTIC_RE = re.compile(r"[吉凶祥瑞福禄寿财喜祸灾煞克破败亡死病]")


def drop_luck_semantic(symbols: list) -> list:
    return [s for s in symbols if not LUCK_SEMANTIC_RE.search(s)]


def mandatory_gloss(el: str, sentences: dict, classics: dict, head_sentences: dict = None,
                    reason: str = "现实驾驶/事故类梦境是现代新增题材，"
                                  "古典梦书成书时无此类") -> tuple:
    """强制族（交通/事故/时间压力）的释义依据：**只认同场景**，否则诚实兜底。

    r2 I-2 实测错误：旧实现给「车祸」取到的句子是「梦见老人出车祸，得此梦，
    虽有财运可得…」——含「车祸」但讲的是别人的场景，被当成了车祸梦的判词。
    """
    g, ev = same_scenario_gloss(el, sentences, classics, head_sentences)
    if g:
        return g, ev
    return ("本批语料里没有匹配到与「" + el + "」同场景的吉凶判词句"
            f"（{reason}；判定口径见 build_rules.same_scenario）；"
            "本条不做吉凶判断，只作情境提醒，释义来自该情境的普遍心理描述",
            "fallback_no_same_scenario")


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


def tone_from(n_ji: int, n_xiong: int, symbols: list, luck: str = "") -> str:
    """情绪基调：由语料吉凶倾向 + 象征词合成（数据导出，非人工撰写）。

    r3 I-B：luck 为无方向档时不带吉凶形容词（否则「中性」结论会配一句
    「偏吉、期待与安抚」的基调，同一行里自相矛盾）。
    """
    if luck in ("中性", "提醒类", "fallback_no_same_scenario"):
        focus = "、".join(symbols[:2])
        return f"情境关注（语料关注点：{focus}）" if focus else "情境关注"
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

    # 候选：Top 表中覆盖量达标、且**不是叙述性/体裁性元词**。
    # r2 I-4：初版只过滤 NARRATIVE_STOP，漏了 META_STOPWORDS（工作/表示/生活/
    # 说明/关系/可能/方面/象征/女性/运势/心理/梦者…）——那些词在注释里就写明
    # 「是语料体裁的产物，不是梦的象征」，却混进了规则表，被写进给 LLM 的 notes。
    # 现在两张表合并生效，并把剔除清单落盘（可复核，不是静默丢弃）。
    excluded = []
    cands = []
    for r in top:
        el = r["element"]
        if not el:
            continue
        if el in RULE_STOPWORDS:
            excluded.append({"element": el, "coverage": int(r["coverage"]),
                             "reason": "narrative_or_meta_word"})
            continue
        if int(r["coverage"]) < args.min_coverage:
            continue
        cands.append(el)
    cands = cands[:args.top]
    (REPORTS / "rule_exclusions.json").write_text(
        json.dumps({"generated_at": now_iso(),
                    "stopwords": sorted(RULE_STOPWORDS),
                    "excluded_from_rules": excluded}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    log(f"[pick] 语料 Top 候选 {len(cands)} 个（覆盖 ≥{args.min_coverage}）；"
        f"按元词/叙述词剔除 {len(excluded)} 个（清单见 rule_exclusions.json）")

    elements = set(cands) | set(MANDATORY_DRIVING)
    uniq, sentences, ji, xiong, head_sentences = scan_corpus(elements)

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
        # 聚合词频给出的方向 → 用它作为「想要的方向」去挑同向句（r3 I-B）
        luck = luck_from_counts(ji[el], xiong[el])
        gloss, ev_kind = same_scenario_gloss(el, sentences, classics,
                                             head_sentences, want_luck=luck)
        luck_basis = "语料同场景判词词频"
        if ev_kind == "fallback_no_same_scenario":
            # 无同场景证据（不变式：此分支 ji+xiong 必为 0）→ 不给方向
            luck = "中性"
            luck_basis = "无同场景判词证据（ji+xiong=0）→ 中性"
            gloss = (f"本批语料里没有匹配到与「{el}」同场景的吉凶判词句"
                     f"（含该元素的条目 {cov} 条，但判词句谈的是其他场景/复合情境）；"
                     f"此处只给泛化倾向，不构成吉凶判断")
        else:
            # 一致性校验：gloss 极性若与聚合 luck 反向（同向句确实不存在），
            # **降为中性**而不是让结论与依据打对台（r3 I-B 口径：
            # 换同向句优先，换不到才弃权——尽量保留传统判词信息，但绝不输出矛盾对）
            gp, lp = sentence_polarity(gloss), luck_polarity(luck)
            if gp and lp and gp != lp:
                luck = "中性"
                luck_basis = f"gloss 极性({gp}) 与聚合词频({lp}) 反向且无同向句 → 降为中性"
        # type 与 luck 不许并存矛盾（刀：type=警示类 + luck=中性 那种）
        if luck in ("中性", "提醒类") and ptype in ("吉兆类", "警示类"):
            ptype = "中性类"
        rules.append({
            "name": el, "match": "|".join(re.escape(a) for a in alts),
            "type": ptype, "symbols": syms,
            "tone": tone_from(ji[el], xiong[el], syms, luck),
            "luck": luck, "luck_basis": luck_basis,
            "gloss": gloss, "coverage": cov,
            # M-3：覆盖量口径必须写进数据（两种口径不能混着看）
            "coverage_basis": "标题核心串含该元素的去重语料条数",
            "gloss_evidence": ev_kind,
            "symbols_basis": "corpus_cooccurrence",
            "evidence": {"ji": ji[el], "xiong": xiong[el],
                         "sentences": len(sentences[el])},
            "source": "corpus_stats",
        })

    # 强制族（brief 明令必须覆盖）：**无论语料是否已生成同名条目，都以 brief
    # 口径的 match/type/tone 覆盖**——语料统计版会掺入分词碎片（实测「开车」拿到
    # 「朋友开车/天开车/友开车」这类 n-gram），而这一族是控制方实测梦例所在族，
    # 匹配面必须干净、可预期。覆盖量仍取真实统计值。
    by_name = {r["name"]: r for r in rules}
    for family, table in (("mandatory_driving_family", MANDATORY_DRIVING),
                          ("mandatory_time_pressure", MANDATORY_EXTRA)):
        for name, spec in table.items():
            if name in by_name:
                r = by_name[name]
                r["match"] = "|".join(re.escape(a) for a in spec["match"])
                r["type"] = spec["type"]
                r["tone"] = spec["tone"]
                # r2 I-2：吉凶**由 brief 口径指定**（交通/事故族＝中性或提醒类），
                # 不再由语料词频自动推——那些判词讲的是别的场景。
                r["luck"] = spec["luck"]
                r["coverage"] = max(r["coverage"], real_text_hits.get(name, 0))
                r["source"] = f"corpus_stats+{family}"
                r["coverage_basis"] = "标题核心串含该元素的去重语料条数（与真实梦境正文命中数取较大）"
                r["symbols_basis"] = "corpus_cooccurrence" if symbols_map.get(name) else "family_tone_derived"
                r["evidence"]["real_dream_text_hits"] = real_text_hits.get(name, 0)
                g, ev = mandatory_gloss(
                    name, sentences, classics, head_sentences,
                    "现实驾驶/事故类梦境是现代新增题材，古典梦书成书时无此类"
                    if family == "mandatory_driving_family"
                    else "时间压力类梦境没有对应的古典判词")
                r["gloss"], r["gloss_evidence"] = g, ev
                r["luck_basis"] = "brief 指定（交通/事故/时间压力族不做吉凶推断）"
                # 该族既然不做吉凶推断，核心象征里就不该出现吉凶语义词
                # （车祸原本带着「吉祥、财运」，与「中性」自相矛盾）
                r["symbols"] = drop_luck_semantic(r["symbols"]) or r["symbols"]
                continue
            cov = real_text_hits.get(name, 0)
            syms = [s for s in symbols_map.get(name, []) if s != name][:6]
            if not syms:
                if family == "mandatory_driving_family":
                    syms = [s for s in symbols_map.get("车", []) if s != "车"][:6]
                else:
                    # 时间压力族在语料里没有共现象征可用：直接由该族 tone 派生
                    # （tone 是 brief 口径给的），并标注 basis，避免读者误以为是统计值
                    syms = [w for w in re.split(r"[：与、]", spec["tone"]) if w][1:]
            gloss, ev_kind = mandatory_gloss(
                name, sentences, classics, head_sentences,
                "现实驾驶/事故类梦境是现代新增题材，古典梦书成书时无此类"
                if family == "mandatory_driving_family"
                else "时间压力类梦境没有对应的古典判词")
            syms = drop_luck_semantic(syms) or syms
            rules.append({
                "name": name, "match": "|".join(re.escape(a) for a in spec["match"]),
                "type": spec["type"], "symbols": syms, "tone": spec["tone"],
                "luck": spec["luck"],
                "gloss": gloss, "coverage": cov,
                "coverage_basis": "真实梦境正文命中条数（该族在词典式语料里覆盖极低）",
                "symbols_basis": "corpus_cooccurrence" if symbols_map.get(name) else "family_tone_derived",
                "gloss_evidence": ev_kind,
                "evidence": {"ji": ji.get(name, 0), "xiong": xiong.get(name, 0),
                             "real_dream_text_hits": cov},
                "source": f"corpus_stats+{family}",
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
        # M-3：coverage 有两种口径（标题核心串 / 真实梦境正文命中），
        # 必须在表内标注，不能让两种口径混着看。
        w.writerow(["name", "type", "coverage", "coverage_basis", "luck", "luck_basis",
                    "tone", "symbols", "symbols_basis", "match", "gloss",
                    "gloss_evidence", "ji", "xiong"])
        for r in uniq_rules:
            luck_basis = r.get("luck_basis") or (
                "brief 指定（交通/事故/时间压力族不做吉凶推断）"
                if "mandatory" in r["source"] else "语料同场景判词词频")
            w.writerow([r["name"], r["type"], r["coverage"],
                        r.get("coverage_basis", "标题核心串含该元素的去重语料条数"),
                        r["luck"], luck_basis, r["tone"],
                        "、".join(r["symbols"]), r.get("symbols_basis", ""),
                        r["match"], r["gloss"],
                        r.get("gloss_evidence", ""),
                        r["evidence"].get("ji", 0), r["evidence"].get("xiong", 0)])

    # 写 Python 模块
    lines = [
        '"""解梦规则层（**自动生成，勿手改**）。',
        "",
        f"由 scripts/k55_dream/build_rules.py 于 {now_iso()} 从真实语料统计生成：",
        "- 元素清单与覆盖量：reports/element_freq_top.csv（去重语料 "
        f"{len(uniq)} 条）",
        "- 核心象征：reports/element_symbols.json（语料共现实词）",
        "- 传统释义：clean/public_domain_quotes.jsonl（**古籍引文，转录未校勘**）",
        "  优先，且必须**同场景**；取不到走诚实兜底（gloss_evidence 字段标注来源档次）",
        "- 类型：站点自有分类（data/competitor_data 的 category 字段）优先",
        "- 吉凶：语料**同场景**判词词频统计；无同场景证据时取「中性」不做方向判断；",
        "  交通/事故/时间压力族由 brief 口径直接指定（中性/提醒类），不由词频推断",
        "- 覆盖量：字段 coverage 的口径见 coverage_basis（两种口径不可混看）",
        "",
        "约束：match 每个分支均 ≥2 字（k49 教训：单字条目会前缀误伤）；",
        "叙述性/体裁性元词不得成为规则条目（见 build_rules.RULE_STOPWORDS）。",
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
        lines.append(f'        "coverage_basis": {r.get("coverage_basis", "")!r},')
        lines.append(f'        "gloss_evidence": {r.get("gloss_evidence", "")!r},')
        lines.append(f'        "symbols_basis": {r.get("symbols_basis", "")!r},')
        lines.append(f'        "luck_basis": {r.get("luck_basis", "")!r},')
        # 计数随模块落库：不变式（fallback ⟹ ji+xiong==0）可离线断言，
        # 不依赖 /mnt/d 的统计产物
        lines.append(f'        "counts": {dict(r["evidence"])!r},')
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
