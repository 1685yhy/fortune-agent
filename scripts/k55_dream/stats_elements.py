#!/usr/bin/env python3
"""语料元素词频统计 —— 规则层的**唯一依据**（brief §2：不许人工拍脑袋写）。

输入（全部为**爬来的真实语料**，原始目录只读）：
- /mnt/d/fortune-data/books/zonghe/{12880_dreams.txt, zgjmorg_dreams.txt,
  周公解梦_现代.txt, 周公解梦大全.txt}
- /mnt/d/fortune-data/books/zonghe/{dreams_verified.jsonl,
  dreams_scraped_verified.jsonl}
- k55_dream/raw/{sosuo_meng_list,sosuo_meng_detail,haomengwang_detail}.jsonl

方法（为什么不是「整条标题计数」）：
词典式语料的标题几乎条条不同（「梦见蛇咬人」「梦见蛇缠身」…），按整条标题
计数会出现「几千个元素各出现 1 次」的废统计。因此改为：
1. 标题去 `梦见` 前缀 / 疑问后缀 / 助词 → 核心串（如「蛇咬人」）；
2. jieba 分词取实词（n/v/a）+ 单字意象词 → 候选元素；
3. **覆盖率 = 含该元素的核心串条数**（子串匹配），这才是「该元素能覆盖多少
   条真实语料」的口径；
4. 同时统计含元素的 2~4 字 n-gram 频次（单字元素生成 ≥2 字 pattern 用，
   遵守 k49 教训：规则层不许出现单字条目）。

用法：
    python scripts/k55_dream/stats_elements.py --top 300
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

from scripts.k55_dream.crawl_lib import DATA_ROOT, now_iso, read_jsonl  # noqa: E402

CORPUS_DIR = Path("/mnt/d/fortune-data/books/zonghe")
OUT_DIR = DATA_ROOT / "reports"

PREFIX_RE = re.compile(r"^(梦见|梦到|梦见了|梦到了|做梦梦见|做梦梦到|梦)")
SUFFIX_RE = re.compile(
    r"(是怎么回事|是什么意思|是什么预兆|代表着什么|代表什么|意味着什么|"
    r"怎么回事|怎么办|有什么预兆|什么征兆|好不好|怎么样|的意思|的含义|"
    r"是什么|预示着|预兆|含义|意思|好吗|行吗|是凶兆还是吉兆|是吉兆还是凶兆"
    r"|会怎么样|有什么含义)+[？?]?$"
)
PARTICLE_RE = re.compile(r"(自己|别人|他人|有人|我们|他们|家中|家里|的|了|着)+")
STOP_ELEMENTS = {
    "什么", "怎么", "为什么", "如何", "哪些", "多少", "可以", "应该", "时候",
    "地方", "东西", "事情", "一个人", "朋友", "别人", "代表", "预示", "做梦",
    "含义", "意思", "预兆", "征兆", "解析", "分析", "解释",
}
CJK_RE = re.compile(r"[一-鿿]+")
# 语料「格式性元词」：词典式正文里高频出现但与意象无关的套话
# （梦见/解梦/周公/可能/表示/意味着/方面/关注…）。不做此过滤时「核心象征」
# 会全被这些词占满（实测），它们是语料体裁的产物，不是梦的象征。
META_STOPWORDS = {
    "梦见", "梦到", "解梦", "周公", "梦者", "做梦", "梦境", "梦乡", "梦魇",
    "可能", "表示", "意味着", "说明", "反映", "象征", "代表", "体现", "显示",
    "方面", "关注", "相关", "关系", "情况", "时候", "问题", "出现", "进行",
    "什么", "自己", "我们", "你们", "他们", "一个", "一种", "一些", "一直",
    "可以", "应该", "需要", "就是", "还是", "或者", "因为", "所以", "但是",
    "如果", "这样", "那样", "由于", "以及", "并且", "而且", "之后", "之前",
    "现在", "最近", "近期", "未来", "提示", "暗示", "预示", "建议", "注意",
    "小心", "内心", "心理", "潜意识", "情绪", "状态", "运势", "吉凶", "好坏",
    "非常", "十分", "比较", "有些", "很多", "更加", "变得", "开始", "继续",
    "工作", "生活", "事情", "东西", "地方", "时间", "过程", "结果", "影响",
    "别人", "他人", "身边", "周围", "身边人", "做梦者", "梦者", "自己",
    # 泛化词 / 繁体变体（同样与意象无关，混进「核心象征」只会稀释信息）
    "希望", "方向", "网友", "具体", "程度", "事物", "最好", "充满", "得到",
    "出来", "发生", "见到", "失去", "人间", "对方", "女性", "男性", "最好",
    "夢者", "夢境", "夢见", "梦见", "夢", "发生", "有所",
    # 语料出处类词（引用标注混进正文导致，与意象无关）
    "敦煌", "原版", "案例", "大全", "查询", "整理", "小编", "提供", "以下",
    "上面", "下面", "权威", "出处", "转载", "声明", "娱乐",
}
EMOTION_MARK = re.compile(
    r"(害怕|恐惧|紧张|焦虑|伤心|难过|生气|愤怒|哭|怕|高兴|开心|喜悦|兴奋|"
    r"幸福|温暖|平静|舒服|着急|慌张)")
LUCK_MARK = {
    "吉": re.compile(r"(大吉|吉利|吉兆|好运|发财|得财|进财|升官|富贵|喜事|顺利|成功|贵子)"),
    "凶": re.compile(r"(大凶|不祥|凶兆|灾祸|倒霉|损失|破财|疾病|病痛|死亡|丧事|口舌|官司|离别|不顺|小人)"),
}


def log(msg: str) -> None:
    print(f"{now_iso()} {msg}", flush=True)


# ── 语料载入 ────────────────────────────────────────────────────────
def load_entries() -> list:
    out = []

    def add(title, content, source):
        t = (title or "").strip()
        if not t:
            return
        out.append({"title": t, "content": (content or "").strip(), "source": source})

    for fname in ("12880_dreams.txt", "zgjmorg_dreams.txt"):
        p = CORPUS_DIR / fname
        if not p.exists():
            log(f"[skip] 缺语料 {p}")
            continue
        n0 = len(out)
        for raw in re.split(r"\n\n+", p.read_text(encoding="utf-8", errors="replace")):
            e = raw.strip()
            if not e.startswith("梦见"):
                continue
            m = re.match(r"^(梦见[^:：]{1,40})[:：]\s*(.*)$", e, re.S)
            add(m.group(1) if m else e[:40], m.group(2) if m else e, fname)
        log(f"[load] {fname}: +{len(out)-n0}")

    p = CORPUS_DIR / "周公解梦_现代.txt"
    if p.exists():
        n0 = len(out)
        for line in p.read_text(encoding="utf-8", errors="replace").split("\n"):
            line = line.strip()
            if not line.startswith("梦见"):
                continue
            m = re.match(r"^(梦见[^:：]{1,40})[:：]\s*(.*)$", line)
            add(m.group(1) if m else line[:40], m.group(2) if m else line, p.name)
        log(f"[load] {p.name}: +{len(out)-n0}")

    for fname in ("dreams_verified.jsonl", "dreams_scraped_verified.jsonl"):
        p = CORPUS_DIR / fname
        if not p.exists():
            continue
        n0 = len(out)
        for rec in read_jsonl(p):
            add(rec.get("title"), rec.get("content"), fname)
        log(f"[load] {fname}: +{len(out)-n0}")

    raw_dir = DATA_ROOT / "raw"
    for fname in ("sosuo_meng_list.jsonl", "sosuo_meng_detail.jsonl",
                  "haomengwang_detail.jsonl"):
        p = raw_dir / fname
        if not p.exists():
            log(f"[skip] 未抓取到 {p.name}")
            continue
        n0 = len(out)
        for rec in read_jsonl(p):
            add(rec.get("title"), rec.get("content"), rec.get("source") or fname)
        log(f"[load] {fname}: +{len(out)-n0}")

    log(f"[load] 合计 {len(out)} 条（未去重）")
    return out


def normalize_core(title: str) -> str:
    t = PREFIX_RE.sub("", (title or "").strip())
    t = re.split(r"[:：,，。！？!?\s]", t)[0]
    t = SUFFIX_RE.sub("", t)
    t = PARTICLE_RE.sub("", t)
    return t.strip("的了着在是有个")


def title_key(core: str) -> str:
    """去重键：按核心串去重（同一元素的不同问法只算一次）。"""
    return re.sub(r"[^一-鿿A-Za-z0-9]", "", core)


# ── 候选元素抽取 ────────────────────────────────────────────────────
def extract_candidates(cores: list) -> tuple:
    """返回 (word_freq, char_word_freq)。

    - word_freq：2 字及以上实词（n/v/a/z/s），数据驱动；
    - char_word_freq：**单字词**，且必须是分词器判为实词（n/v/a）的独立词
      ——直接用「汉字出现频次」会把 在/上/一/被/和 这类虚词顶上榜首，
      那是噪声不是意象（实测：不做此过滤时 Top25 全是虚词）。
    """
    import jieba.posseg as pseg

    word_freq: Counter = Counter()
    char_word_freq: Counter = Counter()
    for core in cores:
        for w, p in pseg.lcut(core):
            if p[0] not in "nvazs":
                continue
            if len(w) >= 2:
                if w not in STOP_ELEMENTS:
                    word_freq[w] += 1
            elif len(w) == 1 and p[0] == "n" and CJK_RE.fullmatch(w):
                # 单字只留**名词**：单字动词/形容词（大/小/到/生/吃/打/开/去…）
                # 在梦里不是意象而是叙述用词，实测会霸榜且毫无解读价值；
                # 「飞/追/死」这类动作意象由 2 字词（飞翔/追赶/去世）与
                # n-gram 覆盖，规则层本就要求 ≥2 字（k49 教训）。
                char_word_freq[w] += 1
    return word_freq, char_word_freq


def main() -> int:
    ap = argparse.ArgumentParser(description="解梦语料元素词频统计")
    ap.add_argument("--top", type=int, default=300)
    ap.add_argument("--min-coverage", type=int, default=20)
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    entries = load_entries()

    # 去重（同一核心串只算一次，避免同一元素被多来源重复计数）
    seen, uniq = set(), []
    for e in entries:
        core = normalize_core(e["title"])
        k = title_key(core)
        if not k or k in seen:
            continue
        seen.add(k)
        uniq.append({**e, "core": core})
    log(f"[stats] 去重后条目 {len(uniq)}")

    cores = [e["core"] for e in uniq]
    word_freq, char_word_freq = extract_candidates(cores)
    log(f"[stats] 分词实词 {len(word_freq)} 个；单字实词 {len(char_word_freq)} 个")

    # 候选池 = 高频实词 ∪ 高频单字意象（单字是否入选看覆盖量，数据说话）
    candidates = set(w for w, c in word_freq.items() if c >= args.min_coverage)
    candidates |= set(ch for ch, c in char_word_freq.items()
                      if c >= args.min_coverage)
    log(f"[stats] 候选元素（覆盖 ≥{args.min_coverage}）{len(candidates)} 个")

    # 覆盖率 = 含该元素的核心串条数（子串匹配）
    cover: Counter = Counter()
    luck: dict = defaultdict(Counter)
    emotion: dict = defaultdict(Counter)
    samples: dict = defaultdict(list)
    for e in uniq:
        core, text = e["core"], e["content"]
        for cand in candidates:
            if cand in core:
                cover[cand] += 1
                if text:
                    for k, rx in LUCK_MARK.items():
                        if rx.search(text):
                            luck[cand][k] += 1
                    em = EMOTION_MARK.search(text)
                    if em:
                        emotion[cand][em.group(1)] += 1
                    if len(samples[cand]) < 3 and len(text) >= 20:
                        samples[cand].append({"text": text[:200], "source": e["source"]})

    # 核心象征候选：元素对应语料正文里的高频实词（统计得出，非人工撰写）
    import jieba.posseg as pseg
    elem_docs: dict = defaultdict(list)
    for e in uniq:
        for cand in candidates:
            if cand in e["core"] and e["content"]:
                elem_docs[cand].append(e["content"])
    symbols: dict = {}
    for el, docs in elem_docs.items():
        cnt: Counter = Counter()
        for doc in docs[:400]:                  # 每元素最多扫 400 篇，够统计
            for w, p in pseg.lcut(doc[:600]):
                if (len(w) >= 2 and p[0] in "nvaz" and w != el
                        and w not in STOP_ELEMENTS and w not in META_STOPWORDS):
                    cnt[w] += 1
        symbols[el] = [w for w, _ in cnt.most_common(12)]
    json.dump(symbols, open(OUT_DIR / "element_symbols.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    log(f"[stats] 核心象征候选表 → element_symbols.json（{len(symbols)} 元素）")

    # 含元素的 2~4 字 n-gram（单字元素生成 ≥2 字 pattern 用）
    all_ngrams: Counter = Counter()
    for core in cores:
        for n in (2, 3, 4):
            for i in range(0, len(core) - n + 1):
                all_ngrams[core[i:i + n]] += 1
    ngrams: dict = defaultdict(Counter)
    for el in cover:
        ngrams[el] = Counter({g: c for g, c in all_ngrams.items() if el in g})

    ranked = cover.most_common()
    total = len(uniq) or 1

    csv_path = OUT_DIR / "element_freq_top.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "element", "coverage", "share", "luck_ji", "luck_xiong",
                    "top_emotion", "top_ngrams", "sample"])
        for i, (el, c) in enumerate(ranked[:args.top], 1):
            ng = ",".join(f"{k}({v})" for k, v in ngrams[el].most_common(6))
            w.writerow([i, el, c, f"{c/total:.4%}", luck[el].get("吉", 0),
                        luck[el].get("凶", 0),
                        (emotion[el].most_common(1)[0][0] if emotion[el] else ""),
                        ng,
                        (samples[el][0]["text"][:120] if samples[el] else "")])

    with open(OUT_DIR / "element_freq_full.tsv", "w", encoding="utf-8") as f:
        f.write("element\tcoverage\n")
        for el, c in ranked:
            f.write(f"{el}\t{c}\n")

    json.dump(
        {
            "generated_at": now_iso(),
            "entries_raw": len(entries),
            "entries_dedup": len(uniq),
            "by_source": dict(Counter(e["source"] for e in entries)),
            "candidates": len(candidates),
            "top60": [{"element": el, "coverage": c, "share": round(c / total, 5)}
                      for el, c in ranked[:60]],
            "single_char_top30": [{"element": el, "coverage": c,
                                   "ngrams": [k for k, _ in ngrams[el].most_common(8)]}
                                  for el, c in ranked if len(el) == 1][:30],
        },
        open(OUT_DIR / "element_stats.json", "w", encoding="utf-8"),
        ensure_ascii=False, indent=2,
    )

    log(f"[stats] Top-N 表 → {csv_path}")
    for el, c in ranked[:25]:
        log(f"    {el}\t{c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
