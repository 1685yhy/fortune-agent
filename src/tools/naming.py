"""起名建议工具规则层（批次 2 E2，cap_id: naming）：参数解析 + 补益五行 + 候选名生成 + 结果卡片。

字库与数理口径全部复用仓库既有资产（不引入新依赖、不新起引擎）：
- 推荐字库：src/engines/ming.py CHAR_LIB —— 通用常用取名字库 796 字（人工标注
  五行/性别倾向/风格标签，2026-08 按 800 字库扩充计划补全、五行均衡；
  ming.py 模块 docstring：「字库: 人工标注取名常用字 ~790 字」）。本工具不
  另造字库，避免数据口径分裂；选字均出自该通用常用字库，不引入生僻字依据。
- 五格数理：src/engines/xingming.py —— 五格剖象法 get_wuge（天格/人格/地格/
  外格/总格：单姓天格=姓笔画+1、人格=姓末笔+名首笔、地格=名笔画和（单名+1）、
  外格=总格-人格+1、总格=全名笔画和）、81 数理吉凶表 NUMEROLOGY_81（大吉/
  半吉/凶三档，1-81 全表）、三才配置 evaluate_sancai（天-人-地五行生克）、
  笔画 STROKE_TABLE（简体常用姓名用字笔画，含百家姓常见姓）。
- 补益五行：BaziEngine 排盘 wuxing 计数（天干+地支本气，src/engines/bazi.py）→
  缺（count=0）/弱（最少计数）五行 → 推荐该五行用字；用神（yongshen）喜用
  五行优先（parse_yongshen 与 ming.py _score_wuxing 同口径）。

评分口径（本工具定义，报告标注依据）：
- 五格评分 0-100 = 五格各格按 81 数理吉凶（大吉=2/半吉=1/凶=0，共 0-10 分）
  + 三才配置（大吉=2/吉=1/半吉=0/凶=0，共 0-2 分），总分 0-12 → 0-100（四舍五入）。
- 候选名生成：字库按 性别倾向 + 补益五行 + 笔画已知 + 排除负面联想字 过滤 →
  排序后的池子做双字有序配对（笔画和 5-15 适中）→ 按五格评分降序取前 limit
  （确定性，无随机；同分按池序稳定排序）。
- 无出生信息（elements=None）：不筛五行，按五格数理均衡推荐（降级路径）。

调用方（handler._tool_naming）：姓氏/性别/出生信息 → split_naming_params 解析 →
（可选）BaziEngine 排盘取五行 → 本模块生成候选 → format_naming_card 出卡片。
"""
import re
from typing import List, Optional

from src.engines.ming import CHAR_LIB, NEG_CHARS, _rule_ping, char_wuxing, parse_yongshen
from src.engines.xingming import (STROKE_TABLE, XingmingEngine, XingmingResult,
                                  get_stroke_count, get_wuge)

WX_ORDER = ["金", "木", "水", "火", "土"]  # 五行序（与 bazi.py WUXING_ORDER 同口径）
_GRID_ORDER = ["天格", "人格", "地格", "外格", "总格"]
# 81 数理吉凶三档：大吉=2 / 半吉=1 / 凶=0（NUMEROLOGY_81 全表仅此三档）
_GRID_POINTS = {"大吉": 2, "半吉": 1, "凶": 0}
# 三才配置四档：大吉=2 / 吉=1 / 半吉=0 / 凶=0（evaluate_sancai 输出口径）
_SANCAI_POINTS = {"大吉": 2, "吉": 1, "半吉": 0, "凶": 0}

# 结构化键形态：surname:/gender:/birth:（半/全角冒号与等号均收）
_KEY_RE = re.compile(r'^(surname|gender|birth)\s*[:：=＝]\s*(.*)$', re.I)


# ============================================================
# 参数解析（结构化键优先，文本标签兜底——与 hehun.split_birth_pair 同型）
# ============================================================

def split_naming_params(text) -> Optional[dict]:
    """把工具参数文本解析为 {surname, gender, birth?}；解析不出 → None。

    支持三种形态：
    1. 结构化多键（JSON 工单 serialize 产物，主路径）：
       "surname: 张\ngender: 男\nbirth: 2019年3月15日 午时 北京"
       （键前缀识别，冒号半/全角、等号均可；键序无关；birth 可省）
    2. 文本标签兜底（legacy <tool_call>）：
       "姓张，男孩，2019年3月15日 午时出生" / "张姓女宝宝"
       —— 姓X / X姓 / 姓氏:X 提取姓氏；男孩/女孩/男/女 判定性别；
       剔除姓氏短语后的剩余文本（≥4 字）作为出生信息
    3. 拆不出姓氏/性别 → None（调用方转 needs_info 追问）
    """
    if not text or not text.strip():
        return None
    raw = text.strip()
    info: dict = {}

    # 形态 1：结构化键（LLM 走 schema 必填键 surname/gender，birth 可选）
    for line in raw.splitlines():
        m = _KEY_RE.match(line.strip())
        if not m:
            continue
        key = m.group(1).lower()
        val = m.group(2).strip()
        if key == "birth":
            info["birth"] = val
        elif val:
            info[key] = val
    if info:
        return info

    # 形态 2：文本标签
    # 姓X 形式（X 后须接分隔符/结尾/的字——防 "张姓女宝宝" 把 "姓女宝" 误当 姓X、
    # 防 "姓氏：" 与 "姓张的" 被贪婪吞成 氏/张的）；
    # X姓 / 姓氏:X 形式兜底。顺序有讲究（E1 同款教训：替代项顺序先吞长前缀）。
    m = (re.search(r'姓\s*[「『“"]?([一-鿿]{1,2})(?=[，,。.、\s的之家]|$)', raw)
         or re.search(r'姓氏\s*[:：]?\s*([一-鿿]{1,2})', raw)
         or re.search(r'([一-鿿]{1,2})姓', raw))
    txt = raw
    if m:
        info["surname"] = m.group(1)
        txt = re.sub(re.escape(m.group(1)) + r'姓', '', raw, count=1)
        txt = re.sub(r'姓\s*[「『“"]?' + re.escape(m.group(1)), '', txt, count=1)
        txt = re.sub(r'姓氏\s*[:：]?\s*' + re.escape(m.group(1)), '', txt, count=1)
        txt = txt.strip("，,。.、 的之家")
        # 剩余文本须 ≥4 字且含日期特征（X年/X月）才当出生信息——
        # "男孩"/"女孩"/"帮女儿起名" 等非出生描述不误当作出生信息
        if len(txt) >= 4 and re.search(r'[0-9０-９]+\s*年|[一二三四五六七八九十]{1,2}\s*月', txt):
            info["birth"] = txt
    if re.search(r'男孩|男宝|男', txt):
        info["gender"] = "男"
    elif re.search(r'女孩|女宝|女', txt):
        info["gender"] = "女"
    return info or None


# ============================================================
# 补益五行（命局缺/弱五行 + 用神喜用）
# ============================================================

def target_elements(wuxing_counts: dict, yongshen: str = "") -> tuple:
    """命局缺/弱五行 + 用神喜用 → 推荐补益五行集合与结论文案。

    口径（报告标注依据）：
    - 用神喜用五行优先（parse_yongshen 解析出 喜X → 与 ming._score_wuxing 同口径）；
      命局缺五行同时并入补益集合（缺口当补，文案点明"且命局缺"）
    - 无用神可析：缺（count=0）优先，其次弱（最少计数且 < 最多计数），
      五行完全均衡 → 全五行均衡补益
    返回 (elements: List[str], desc: str)。
    """
    counts = {w: wuxing_counts.get(w, 0) for w in WX_ORDER}
    _, helpful = parse_yongshen(yongshen or "")
    missing = [w for w in WX_ORDER if counts[w] == 0]
    if helpful:
        targets = list(helpful)
        desc = f"用神喜「{''.join(helpful)}」"
        if missing:
            desc += f"，且命局缺「{''.join(missing)}」"
            for w in missing:
                if w not in targets:
                    targets.append(w)
    elif missing:
        targets = missing
        desc = f"命局缺「{''.join(missing)}」"
    else:
        mn, mx = min(counts.values()), max(counts.values())
        weak = [w for w in WX_ORDER if counts[w] == mn]
        if mn < mx:
            targets = weak
            desc = f"命局「{''.join(weak)}」偏弱"
        else:
            targets = list(WX_ORDER)
            desc = "五行均衡，全五行均可补益"
    return targets, desc


# ============================================================
# 候选名生成（确定性）与五格评分
# ============================================================

def build_pool(gender: str, elements: Optional[List[str]]) -> List[str]:
    """字库过滤（确定性）：性别倾向匹配 + 五行命中 + 笔画已知 + 排除负面联想字。

    gender: "男"/"女"（其余视为中性，性别倾向不限）；
    elements: None → 不筛五行（无出生信息降级路径）；空列表 → 空池。
    返回排序后的候选单字列表。
    """
    g = "m" if gender == "男" else "f" if gender == "女" else "b"
    pool = []
    for ch, info in CHAR_LIB.items():
        if ch in NEG_CHARS:
            continue
        if info["g"] not in (g, "b"):
            continue
        if get_stroke_count(ch) <= 0:
            continue
        if elements is not None and info["wx"] not in elements:
            continue
        pool.append(ch)
    return sorted(pool)


def wuge_score(result: XingmingResult) -> int:
    """五格评分 0-100：五格各格按 81 数理吉凶（大吉=2/半吉=1/凶=0，0-10 分）
    + 三才配置（大吉=2/吉=1/半吉=0/凶=0，0-2 分）→ 总分 0-12 → 0-100。"""
    total = sum(_GRID_POINTS[result.analysis[k]["吉凶"]] for k in _GRID_ORDER)
    total += _SANCAI_POINTS.get(result.sancai_ji, 0)
    return round(total * 100 / 12)


def _wuge_line(result: XingmingResult) -> str:
    """一行五格摘要：天12半吉 人28半吉 …｜三才木木木(半吉)。"""
    parts = [f"{result.wuge[k]}{result.analysis[k]['吉凶']}" for k in _GRID_ORDER]
    return " ".join(parts) + f"｜三才{result.sancai}({result.sancai_ji})"


def _meaning_line(given: str) -> str:
    """寓意行：逐字五行标注 + 字库风格点评（与 ming._rule_ping 同口径）。"""
    wx = " ".join(f"{ch}({char_wuxing(ch)})" for ch in given)
    return f"{wx}｜{_rule_ping(given)}"


def generate_candidates(surname: str, gender: str, elements: Optional[List[str]],
                        limit: int = 5) -> List[dict]:
    """生成候选双字名（确定性，无随机）：补益五行池子有序配对 →
    笔画和适中（5-15）→ 按五格评分降序取前 limit。

    返回 [{given, score, wuge_line, meaning}]；池子不足 2 字 → []。
    """
    pool = build_pool(gender, elements)
    if len(pool) < 2:
        return []
    engine = XingmingEngine()
    cands = []
    n = len(pool)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = pool[i], pool[j]
            if not (5 <= get_stroke_count(a) + get_stroke_count(b) <= 15):
                continue
            given = a + b
            r = engine.analyze(surname, given, gender)
            cands.append({"given": given, "score": wuge_score(r),
                          "wuge_line": _wuge_line(r), "meaning": _meaning_line(given),
                          "_i": i, "_j": j})
    cands.sort(key=lambda c: (-c["score"], c["_i"], c["_j"]))
    # 首字不重复（避免清一色同首字）；不足 limit 时按分数顺延补齐
    picked, seen_first = [], set()
    for c in cands:
        if c["given"][0] in seen_first:
            continue
        seen_first.add(c["given"][0])
        picked.append(c)
        if len(picked) >= limit:
            break
    if len(picked) < limit:
        for c in cands:
            if c in picked:
                continue
            picked.append(c)
            if len(picked) >= limit:
                break
    return [{"given": c["given"], "score": c["score"],
             "wuge_line": c["wuge_line"], "meaning": c["meaning"]}
            for c in picked]


# ============================================================
# 结果卡片
# ============================================================

def format_naming_card(surname: str, gender: str, elements_desc: str,
                       candidates: List[dict]) -> str:
    """起名建议卡片（紧凑排版）：补益五行结论 + 3-5 个候选名（字+五格评分+寓意）。

    elements_desc 为空 → 降级路径文案（无出生信息，仅按五格数理均衡推荐）。
    """
    lines = [f"【起名建议】姓氏：{surname}｜性别：{gender}"]
    if elements_desc:
        lines.append(f"补益五行：{elements_desc}")
    else:
        lines.append("补益五行：未提供出生信息，按五格数理均衡推荐（不涉及八字补益）")
    if not candidates:
        lines.append("暂无可推荐的候选名（可用字不足，请放宽条件）。")
        return "\n".join(lines)
    for idx, cand in enumerate(candidates, 1):
        lines.append(f"{idx}. {surname}{cand['given']} —— 五格评分 {cand['score']}/100")
        lines.append(f"   五格：{cand['wuge_line']}")
        lines.append(f"   寓意：{cand['meaning']}")
    lines.append("说明：候选用字均出自通用常用字库（按五行分类、避开生僻字）；"
                 "如需结合生肖/偏旁等再调整，可继续告诉我。")
    return "\n".join(lines)
