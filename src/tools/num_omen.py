"""数字吉凶工具规则层（批次 2 E5，cap_id: num_omen）：参数解析 + 位数场景识别 +
81 数理查表（尾号/整体）+ 改善建议 + 结果卡片。最轻量工具（纯查表，不调引擎）。

数理口径（本工具定义，报告标注依据；81 数理表本体复用仓库既有资产，不另造表）：
- 81 数理表：src/engines/xingming.py NUMEROLOGY_81（1-81 全表：大吉/半吉/凶三档，
  含运势名与性格事业分析）——与批次 2 E2 起名工具五格评分同源同表复用，
  保证 E5 与 E2 的 81 数理口径一致。
- 尾号五行：xingming.py DIGIT_TO_WUXING（1,2木 3,4火 5,6土 7,8金 9,0水）——同源复用。
- 数理归约：n = value % 81，余 0 取 81（数理表 1-81 全表可及；数值 < 81 时即原值）。
- 尾号数理：取数字串末两位整数（不足两位取全部）归约——手机尾号/车牌尾号/楼层/
  门牌的通行讨论口径（尾号 66/88/404…）：尾号 4 → 数理4、尾号 88 → 88%81=7。
- 整体数理：全部数字之和归约——号码吉凶「数字总和法」通行做法。
- 位数场景识别（关键词优先，无关键词按长度）：
  · 手机号：11 位纯数字（国内手机号）；关键词 手机/电话（"号码"泛称不参与
    关键词——"这号码咋样"语境下按位数判定，防门牌/楼层误判）
  · 车牌：数字 5-7 位（车牌号 5-6 位 + 地区段）；关键词 车牌/牌照/号牌/车号
  · 门牌：数字 1-4 位；关键词 门牌/房间/室/栋/单元/号房
  · 楼层：数字 1-2 位；关键词 楼层/层/楼（"楼"置门牌关键词之后——"3号楼501室"
    含 室 先归门牌）
  · 8-10 位无关键词 → 其他号码（座机等）；>20 位 → 调用方拒绝（非实际号码）
- 解析容错：数字串剔除一切非数字字符（横线/空格/字母/全角数字归一），空 → None。
- 改善建议口径：
  · 避开 4 连：连续 ≥3 个「4」→ 谐音"死"+数理 4 凶，选号避开；连续 ≥4 个其他
    数字 → 重复过多单调显张扬
  · 选择数理：大吉数理号从 NUMEROLOGY_81 程序化推导（吉凶=="大吉"），尾号为凶时
    建议改选尾号使末两位归约落在大吉数理上；整体为凶时建议以吉尾号对冲。
- 口径说明（卡片附注）：数理吉凶与谐音联想口径不同（如尾号 66 数理为凶但谐音
  "顺顺"），本工具以 81 数理表为准，不做谐音裁决。

调用方（handler._tool_num_omen）：数字文本 → parse_num_params 解析 →
analyze_number 查表 → format_num_card 出卡片。全程无引擎调用（纯查表）。
"""
import re
from typing import Optional

from src.engines.xingming import DIGIT_TO_WUXING, NUMEROLOGY_81

# 结构化键形态：number:/context:（半/全角冒号与等号均收）
_KEY_RE = re.compile(r'^(number|context)\s*[:：=＝]\s*(.*)$', re.I)
# 全角数字归一 + 非数字字符剔除
_FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")
_STRIP_RE = re.compile(r'[^0-9]')
# 连续 ≥3 个 4 / 连续 ≥4 个其他数字（4 连避讳 + 重复单调提示）
_BAD4_RE = re.compile(r'4{3,}')
_REPEAT_RE = re.compile(r'([0-35-9])\1{3,}')

# 场景关键词（词序即优先级；"楼"置门牌关键词之后——"3号楼501室"含 室 先归门牌）
_CONTEXT_KEYWORDS = [
    ("手机号", ("手机", "电话")),
    ("车牌", ("车牌", "牌照", "号牌", "车号")),
    ("门牌", ("门牌", "房间", "室", "栋", "单元", "号房")),
    ("楼层", ("楼层", "层", "楼")),
]


def _strip_digits(s: str) -> str:
    """数字提取：全角数字归一 + 剔除一切非数字字符（横线/空格/字母…）。"""
    return _STRIP_RE.sub("", str(s or "").translate(_FULLWIDTH))


def detect_context(text: str, digits: str) -> str:
    """场景识别：关键词优先（词序即优先级），无关键词按位数兜底
    （11 位手机号、5-7 位车牌、1-4 位门牌/楼层、其余其他号码）。"""
    for ctx, words in _CONTEXT_KEYWORDS:
        if any(w in text for w in words):
            return ctx
    n = len(digits)
    if n == 11:
        return "手机号"
    if 5 <= n <= 7:
        return "车牌"
    if 1 <= n <= 4:
        return "门牌/楼层"
    return "其他号码"


def parse_num_params(text) -> Optional[dict]:
    """把工具参数文本解析为 {number, context?}；解析不出 → None。

    支持三种形态：
    1. 结构化多键（JSON 工单 serialize_params 产物，主路径）：
       "number: 13812345678\ncontext: 手机号"（number 必填，context 可选；
       context 缺省按位数自动识别）——单键工单 serialize 产物是纯数字串，走形态 3。
    2. 文本标签兜底（legacy <tool_call>）：
       "帮我看看手机号 138-1234-5678 吉不吉" / "8楼" / "京A88888 这个车牌"
       ——非数字字符（含全角）全部剔除得数字串，场景关键词/位数识别 context。
    3. 拆不出数字 → None（调用方转 needs_info 追问）。
    """
    if not text or not text.strip():
        return None
    raw = text.strip()
    info: dict = {}

    # 形态 1：结构化键
    for line in raw.splitlines():
        m = _KEY_RE.match(line.strip())
        if not m:
            continue
        key = m.group(1).lower()
        val = m.group(2).strip()
        if val:
            info[key] = val
    if info:
        raw_val = info["number"]
        info["number"] = _strip_digits(info["number"])
        if not info.get("number"):
            return None
        # context 缺省：从原始键值文本识关键词（如 "number: 8楼" → 楼层），
        # 无关键词再按位数兜底（如 "number: 13812345678" → 手机号）
        info.setdefault("context", detect_context(raw_val, info["number"]))
        return info

    # 形态 2/3：文本标签兜底——剔除全部非数字字符后按关键词/位数识别场景
    digits = _strip_digits(raw)
    if not digits:
        return None
    return {"number": digits, "context": detect_context(raw, digits)}


def _reduce_to_81(n: int) -> int:
    """数理归约：n % 81，余 0 取 81（数理表 1-81 全表可及；数值 < 81 时即原值）。"""
    return (n % 81) or 81


def great_auspicious_numbers() -> list:
    """大吉数理号：从 NUMEROLOGY_81 程序化推导（吉凶=="大吉"），不另维护列表。"""
    return sorted(n for n, (ji, _t, _m) in NUMEROLOGY_81.items() if ji == "大吉")


def analyze_number(digits: str, context: str = "") -> dict:
    """数字串 → 尾号/整体数理查表结果（纯查表，确定性）。

    口径见模块 docstring：尾号取末两位（不足两位取全部）、整体取数字和，
    均归约至 1-81 后查 NUMEROLOGY_81（吉凶 + 运势名 + 性格事业分析）；
    尾号附加数字五行（DIGIT_TO_WUXING 同源复用）。
    """
    tail = digits[-2:] if len(digits) >= 2 else digits
    tail_int = int(tail) if tail else 0
    tail_num = _reduce_to_81(tail_int)
    overall_num = _reduce_to_81(sum(int(c) for c in digits))
    tail_ji, tail_title, tail_meaning = NUMEROLOGY_81[tail_num]
    over_ji, over_title, over_meaning = NUMEROLOGY_81[overall_num]
    bad4 = _BAD4_RE.search(digits)
    repeat = _REPEAT_RE.search(digits)
    return {
        "tail_digits": tail,
        "tail_int": tail_int,
        "tail_num": tail_num,
        "tail_ji": tail_ji,
        "tail_title": tail_title,
        "tail_meaning": tail_meaning,
        "tail_wuxing": " ".join(f"{c}{DIGIT_TO_WUXING[int(c)]}" for c in tail),
        "digit_sum": sum(int(c) for c in digits),
        "overall_num": overall_num,
        "overall_ji": over_ji,
        "overall_title": over_title,
        "overall_meaning": over_meaning,
        "bad_chain4": bad4.group(0) if bad4 else None,
        "repeat_warn": repeat.group(0) if repeat else None,
    }


def improvement_suggestions(info: dict) -> list:
    """改善建议（确定性）：4 连避讳 / 尾号凶换号 / 整体凶对冲 / 均吉保持。"""
    tips = []
    if info["bad_chain4"]:
        tips.append(f"避开「4 连」：号码含连续「{info['bad_chain4']}」，谐音与数理 4 均不吉，"
                    "选号时避免连续 3 个以上 4。")
    if info["repeat_warn"]:
        tips.append(f"数字「{info['repeat_warn'][0]}」连续出现 4 次以上（{info['repeat_warn']}），"
                    "重复过多易显单调张扬，可适度搭配其他数字。")
    if info["tail_ji"] == "凶":
        great = great_auspicious_numbers()
        tips.append(f"尾号「{info['tail_digits']}」数理为凶：建议换尾号。改选时让末两位归约后"
                    f"落在大吉数理上（大吉数理：{'/'.join(map(str, great))}），"
                    "如尾号 86（→数理5）、21（→数理21）、68（→数理68）。")
    if info["overall_ji"] == "凶":
        tips.append("整体数理为凶：数字和偏凶。可考虑更换号码，或在保留号码时选大吉数理"
                    "尾号（对冲整体偏凶的影响）。")
    if not tips:
        tips.append("整体与尾号数理均吉：保持即可；仍可留意避免 4 连等谐音避讳。")
    return tips


def format_num_card(info: dict, number: str, context: str = "") -> str:
    """数字吉凶卡片（紧凑排版）：场景/号码 + 尾号数理（吉凶/运势/含义/五行）
    + 整体数理 + 吉凶总评 + 改善建议 + 口径说明。"""
    lines = [
        f"【数字吉凶】场景：{context or '未指定'}｜号码：{number}",
        f"尾号 {info['tail_digits']} → 数理 {info['tail_num']}"
        f"（{info['tail_ji']}·{info['tail_title']}）：{info['tail_meaning']}"
        f"｜尾号五行：{info['tail_wuxing']}",
        f"整体数理：数字和 {info['digit_sum']} → 数理 {info['overall_num']}"
        f"（{info['overall_ji']}·{info['overall_title']}）：{info['overall_meaning']}",
        f"吉凶总评：整体{info['overall_ji']}、尾号{info['tail_ji']}",
        "改善建议：",
    ]
    lines.extend(f"  · {t}" for t in improvement_suggestions(info))
    lines.append("说明：81 数理与起名工具同源（src/engines/xingming.py NUMEROLOGY_81 "
                 "同表复用）；尾号取末两位、整体取数字和，均归约至 1-81（余 0 取 81）。"
                 "数理吉凶与谐音联想口径不同（如尾号 66 数理为凶但谐音'顺顺'），"
                 "本工具以数理表为准。")
    return "\n".join(lines)
