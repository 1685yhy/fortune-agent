# src/engine/synth.py
"""多体系合成层（阶段4 Task 1）：跨体系共识/分歧/不可比较三分类。

核心原则（设计文档第7章）：只做**可判定的**共识/分歧——跨体系可比较的事实断言
按「可比较键」归一化比对；无公共键的体系组合如实列入不可比较；**绝不硬造共识**。

可比较键（本阶段两个）：
1. 五行：八字用神五行（穷通宝鉴格言「用」字后首个天干）/ 紫微五行局 /
   奇门局五行（取值符星五行，值符为遁局之统帅）/ 六壬三传五行（取众数，并列取先出现）/
   六爻用事五行（世爻地支五行）
2. 时间：八字流年年份 / 紫微大限起始年份（大限步骤存在于推演链时）。
   六壬三传时机：当前推演链无三传应期步骤，无可抽取的时间断言，如实不纳入。

归一化口径：
- 每体系每键取「首个断言」为规范值（按步骤顺序），避免流年列表等多值噪音；
  其余同键断言保留在事实要点中供报告展示，不参与比较。
- 时间键统一取起始年份（unit=年份）：八字流年 2026:丙午 → 2026；紫微大限 2026-2035 → 2026。

判定（确定性）：
- 共识：某键上 ≥2 体系规范值相同 → 列出参与体系与各自出处（evidence）。
- 分歧：某键上 ≥2 体系给出不同规范值 → 两说并存，各带 source，note 固定。
- 不可比较：无公共可比较键的体系对 → 如实说明（含各自键清单/无事实要点），不硬造。
- 空输入 → 空结果不崩。

产出不做解释性断语；llm 参数预留（多体系报告段在 Task 2 接入）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 天干/地支 → 五行（确定性查表）
GAN_WUXING = {
    "甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
    "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水",
}
ZHI_WUXING = {
    "子": "水", "丑": "土", "寅": "木", "卯": "木", "辰": "土",
    "巳": "火", "午": "火", "未": "土", "申": "金", "酉": "金",
    "戌": "土", "亥": "水",
}
GANS = "甲乙丙丁戊己庚辛壬癸"
ZHI = "子丑寅卯辰巳午未申酉戌亥"
WUXING_CHARS = "水火木金土"

# 体系代码 → 中文名（报告/不可比较文案用）
SYSTEM_NAMES = {
    "bazi": "八字", "ziwei": "紫微", "liuyao": "六爻",
    "qimen": "奇门", "liuren": "六壬",
}

# 可比较键（比较顺序固定，保证输出确定性）
KEYS = ("五行", "时间")

# 分歧固定说明（设计文档第7章：两说并存，不挑一弃一）
DIVERGE_NOTE = "两说并存，各带出处，由用户结合实际情况权衡"


@dataclass
class SystemResult:
    """单体系推演产物（合成层输入）。"""
    system: str              # "bazi"/"ziwei"/"liuyao"/"qimen"/"liuren"
    chain: object            # DeductionChain（该体系推演链）
    analysis: str = ""       # LLM 综合输出（可空）
    citations: list = field(default_factory=list)  # 出处列表（可空）


@dataclass
class SynthResult:
    """合成结果：共识 / 分歧 / 不可比较三分类。"""
    systems: list[str]                 # 参与体系代码列表
    consensus: list[dict] = field(default_factory=list)     # [{"point","systems","evidence"}]
    divergences: list[dict] = field(default_factory=list)   # [{"topic","views","note"}]
    unresolved: list[str] = field(default_factory=list)     # 不可比较说明（如实）


# ---------- 事实抽取（确定性，口径见模块 docstring） ----------

def _truncate(text: str, limit: int = 60) -> str:
    """长文本截断（要点展示用，不影响比较值）。"""
    return text if len(text) <= limit else text[:limit] + "…"


def _wuxing_of_gan(gan: str) -> str:
    return GAN_WUXING.get(gan, "")


def _wuxing_of_zhi(zhi: str) -> str:
    return ZHI_WUXING.get(zhi, "")


def _extract_bazi(steps: list) -> list[dict]:
    """八字：五行=穷通宝鉴「用」字后首个天干；时间=流年年份。"""
    facts = []
    for s in steps:
        rule, fact, output, source = s.rule, s.fact, s.output, s.source
        # 五行：穷通宝鉴查表格言（qiongtong_table 步骤）
        if rule.startswith("qiongtong_table"):
            import re
            m = re.search(f"用([{GANS}])", output or "")
            if m:
                facts.append({"key": "五行", "value": _wuxing_of_gan(m.group(1)),
                              "text": _truncate(output), "source": source})
        # 时间：大运流年步骤的流年（事实字段含 2026:丙午 格式）
        if rule.startswith("大运流年"):
            import re
            years = re.findall(r"流年 (\d{4}):(\S{2})", f"{fact} {output}")
            for y, gz in years[:3]:
                facts.append({"key": "时间", "value": y,
                              "text": f"流年 {y}:{gz}", "source": source})
    return facts


def _extract_ziwei(steps: list) -> list[dict]:
    """紫微：五行=五行局首字；时间=大限步骤起始年份（推演链存在大限步骤时）。

    五行优先取专门五行局步骤（rule 含 wuxing_ju，文本干净如"水二局"）；
    排盘/断语要点步骤中嵌入的五行局仅作兜底。
    """
    facts = []
    import re
    for s in steps:
        rule, fact, output, source = s.rule, s.fact, s.output, s.source
        if "大限" in rule:
            m = re.search(r"(\d{4})-(\d{4})", f"{fact} {output}")
            if m:
                facts.append({"key": "时间", "value": m.group(1),
                              "text": _truncate(output), "source": source})
    for s in steps:
        rule, fact, output, source = s.rule, s.fact, s.output, s.source
        if "wuxing_ju" not in rule:
            continue
        m = re.search(f"([{WUXING_CHARS}])[二三四五六]局", f"{fact} {output}")
        if m:
            facts.append({"key": "五行", "value": m.group(1),
                          "text": _truncate(output), "source": source})
            return facts
    for s in steps:
        rule, fact, output, source = s.rule, s.fact, s.output, s.source
        if "wuxing_ju" in rule:
            continue
        m = re.search(f"([{WUXING_CHARS}])[二三四五六]局", f"{fact} {output}")
        if m:
            facts.append({"key": "五行", "value": m.group(1),
                          "text": _truncate(output), "source": source})
            break
    return facts


def _extract_liuyao(steps: list) -> list[dict]:
    """六爻：五行=世爻地支五行（用事五行；世爻无地支则无五行可判）。"""
    facts = []
    for s in steps:
        rule, fact, output, source = s.rule, s.fact, s.output, s.source
        if rule.startswith("liuyao.liuqin_of"):
            import re
            m = re.search(rf"世爻地支\s*([{ZHI}])", f"{fact} {output}")
            if m:
                facts.append({"key": "五行", "value": _wuxing_of_zhi(m.group(1)),
                              "text": _truncate(output), "source": source})
    return facts


def _extract_qimen(steps: list) -> list[dict]:
    """奇门：五行=值符星五行（口径：奇门局五行取值符星五行）。"""
    facts = []
    for s in steps:
        rule, fact, output, source = s.rule, s.fact, s.output, s.source
        import re
        m = re.search(f"值符星：?([一-龥]{{1,3}})（([{WUXING_CHARS}])）",
                      f"{fact} {output}")
        if m:
            facts.append({"key": "五行", "value": m.group(2),
                          "text": _truncate(output), "source": source})
    return facts


def _extract_liuren(steps: list) -> list[dict]:
    """六壬：五行=三传五行众数（并列取先出现）。"""
    facts = []
    for s in steps:
        rule, fact, output, source = s.rule, s.fact, s.output, s.source
        import re
        m = re.search(r"三传五行:\[([^\]]+)\]", f"{fact} {output}")
        if m:
            elems = re.findall(f"[{WUXING_CHARS}]", m.group(1))
            if elems:
                from collections import Counter
                counts = Counter(elems)
                top = max(counts.values())
                value = next(e for e in elems if counts[e] == top)
                facts.append({"key": "五行", "value": value,
                              "text": _truncate(output), "source": source})
    return facts


_SYSTEM_PREFIXES = {
    "bazi": ("排盘引擎", "shishen", "geju", "qiongtong_table", "shensha", "大运流年"),
    "ziwei": ("ziwei.",),
    "liuyao": ("liuyao.",),
    "qimen": ("qimen.",),
    "liuren": ("liuren.",),
}


def extract_facts(system: str, chain) -> list[dict]:
    """从推演链步骤中提取事实性要点（可比较键标注 + 普通要点）。

    返回 list[dict]：{"key": "五行"|"时间"|"", "value": 归一化值|None,
    "text": 原文要点, "source": 出处}。key 为空串的要点不参与比较（仅展示）。
    """
    steps = getattr(chain, "steps", []) or []
    extractors = {"bazi": _extract_bazi, "ziwei": _extract_ziwei,
                  "liuyao": _extract_liuyao, "qimen": _extract_qimen,
                  "liuren": _extract_liuren}
    comparable = extractors.get(system, lambda steps: [])(steps)

    # 普通要点：体系前缀步骤的 output（排除断语要点汇总步，避免重复噪音）
    prefixes = _SYSTEM_PREFIXES.get(system, ())
    plain = []
    for s in steps:
        if not prefixes or not s.rule.startswith(prefixes):
            continue
        if "断语要点" in s.rule:
            continue
        text = _truncate(s.output) if s.output else ""
        if text and text not in [p["text"] for p in plain]:
            plain.append({"key": "", "value": None, "text": text, "source": s.source})

    # 去重：同键同值只保留首个（可比较键）；普通要点按步骤顺序追加
    seen = set()
    out = []
    for f in comparable:
        if f["key"] and (f["key"], f["value"]) not in seen:
            seen.add((f["key"], f["value"]))
            out.append(f)
    out.extend(plain)
    return out


# ---------- 合成判定（确定性） ----------

def synthesize(results: list[SystemResult], llm=None) -> SynthResult:
    """跨体系合成：共识 / 分歧 / 不可比较三分类。

    llm 参数预留（本层不做解释性断语；多体系报告段在 report.compose_multi_report 接入）。
    """
    if not results:
        return SynthResult(systems=[])

    systems = [r.system for r in results]

    # 1. 抽取各体系事实要点，并取「每体系每键首个断言」为规范值
    canon: dict[str, dict[str, dict]] = {}
    for r in results:
        seen: dict[str, dict] = {}
        for f in extract_facts(r.system, r.chain):
            if f["key"] and f["key"] not in seen:
                seen[f["key"]] = f
        canon[r.system] = seen

    # 2. 共识：某键上 ≥2 体系规范值相同
    consensus = []
    for key in KEYS:
        by_value: dict[str, list] = {}
        for r in results:
            f = canon[r.system].get(key)
            if f:
                by_value.setdefault(f["value"], []).append((r.system, f))
        for value, members in by_value.items():
            if len(members) >= 2:
                consensus.append({
                    "point": f"{key}一致：{value}",
                    "systems": [m[0] for m in members],
                    "evidence": [{"system": s, "text": f["text"], "source": f["source"]}
                                 for s, f in members],
                })

    # 3. 分歧：某键上 ≥2 体系给出不同规范值（两说并存，各带出处）
    divergences = []
    for key in KEYS:
        by_value: dict[str, list] = {}
        for r in results:
            f = canon[r.system].get(key)
            if f:
                by_value.setdefault(f["value"], []).append((r.system, f))
        distinct = list(by_value.items())
        if len(distinct) >= 2:
            divergences.append({
                "topic": f"{key}不同：{'、'.join(v for v, _ in distinct)}",
                "views": [{"system": s, "view": v, "source": f["source"]}
                          for v, members in distinct for s, f in members],
                "note": DIVERGE_NOTE,
            })

    # 4. 不可比较：无公共可比较键的体系对（如实说明，不硬造共识）
    unresolved = []
    for i in range(len(results)):
        for j in range(i + 1, len(results)):
            a, b = results[i].system, results[j].system
            ka, kb = set(canon[a]), set(canon[b])
            if ka & kb:
                continue
            da = "、".join(ka) if ka else "无事实要点"
            db = "、".join(kb) if kb else "无事实要点"
            unresolved.append(
                f"{SYSTEM_NAMES.get(a, a)}与{SYSTEM_NAMES.get(b, b)}无公共比较维度"
                f"（{a}:{da}；{b}:{db}），不硬造共识")

    return SynthResult(systems=systems, consensus=consensus,
                       divergences=divergences, unresolved=unresolved)
