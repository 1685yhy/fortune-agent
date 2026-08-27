"""合婚工具规则层（批次 2 E1）：双方出生参数解析 + 评级映射 + 结果卡片。

规则依据（冲合表/五行口径与引擎 src/engines/hehun.py 同源，此处只做
解析/评级/排版，不重复定义命理表）：
- 生肖六冲：子午、丑未、寅申、卯酉、辰戌、巳亥（最忌）
- 生肖三合：申子辰、亥卯未、寅午戌、巳酉丑（上等）
- 生肖六合：子丑、寅亥、卯戌、辰酉、巳申、午未（上等）
- 五行生克：木→火→土→金→水→木 相生；木克土、土克水、水克火、
  火克金、金克木 相克；日主同五行 = 比和
- 评级映射（对齐引擎 _generate_advice 档位阈值）：综合分 ≥65 →
  上等婚配；35~64 → 中等婚配；<35 → 普通婚配（冲克较多，需谨慎）

调用方（handler._tool_hehun）拿双方出生自然语言 → 本模块拆对 →
各走 _extract_bazi_info 解析 → 既有 BaziEngine 双排盘 →
既有 HehunEngine.match（src/engines/hehun.py，未改动）→ 本模块出卡片。
"""
import re
from typing import Optional, Tuple

# 结构化键形态：birth_a: X / birth_b: Y（半/全角冒号与等号均收）
_BIRTH_KEY_RE = re.compile(r'^birth_[ab]\s*[:：=＝]\s*(.*)$', re.I)
# 文本标签形态分隔符（与 _handle_hehun 同口径）：[，。,.\s]+ 后接
# 女/男方；或 [，。,.\s]* 后接 女方/对方/对象/伴侣（带前导分隔符时
# 一并吞掉，避免 "，女方" 只吞 "，女" 留下 "方" 字残渣——替代项顺序
# 有讲究：女方/对方/对象/伴侣 在前，[，。,.\s]+女/男 在后）。
_DELIM_SPLIT_RE = re.compile(
    r'[，。,\.\s]*(?:女方|对方|对象|伴侣)'
    r'|[，。,\.\s]+女'
    r'|[，。,\.\s]+男')


def split_birth_pair(params_text: str) -> Optional[Tuple[str, str]]:
    r"""把工具参数文本拆成双方出生描述，返回 (birth_a 文本, birth_b 文本)。

    支持三种形态：
    1. 结构化多键（JSON 工单 serialize 产物）：
       "birth_a: 1990年5月20日 午时 北京 男\nbirth_b: 1992年8月15日 巳时 上海 女"
       （键前缀识别，冒号半/全角、等号均可；行序无关）
    2. 文本标签（legacy <tool_call> 兜底）：
       "男1990年5月20日 午时 北京，女1992年8月15日 巳时 上海"
       ——按 女方/对方/对象/伴侣（可带前导分隔符）或 [，。,.\s]+女/男 拆两段
       （女方在前男方在后也认）；多于两段时合并尾段到第二人
    3. 拆不出两段 → None（调用方转 needs_info 追问双方出生信息）
    """
    if not params_text or not params_text.strip():
        return None
    text = params_text.strip()

    # 形态 1：结构化键（优先，LLM 走 schema 的必填键 birth_a/birth_b）
    a = b = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith("birth_a"):
            m = _BIRTH_KEY_RE.match(line)
            if m and m.group(1).strip():
                a = m.group(1).strip()
        elif line.lower().startswith("birth_b"):
            m = _BIRTH_KEY_RE.match(line)
            if m and m.group(1).strip():
                b = m.group(1).strip()
    if a and b:
        return a, b

    # 形态 2：分隔符拆两段（文本标签兜底）
    parts = [p.strip().rstrip("，。,.、 ") for p in _DELIM_SPLIT_RE.split(text) if p.strip()]
    if len(parts) >= 2:
        return parts[0], "".join(parts[1:]).rstrip("，。,.、 ")
    return None


def grade_for_score(score: int) -> str:
    """综合评分 → 总体评级（上等/中等/普通婚配）。

    阈值对齐引擎 _generate_advice 的档位（65/50/35）：
    - ≥65：上等婚配（天作之合/上等，五行生肖日柱整体相合）
    - 35~64：中等婚配（各有优劣，需相互包容磨合）
    - <35：普通婚配（冲克较多，婚姻需谨慎）
    """
    if score >= 65:
        return "上等婚配"
    if score >= 35:
        return "中等婚配"
    return "普通婚配"


def format_hehun_card(bazi_a, bazi_b, hehun_result) -> str:
    """合婚结果卡片（紧凑排版，控制每次工具调用注入的 token）。

    覆盖需求四要素：生肖冲合（六冲/三合/六合）、五行互补（双方命局
    五行强弱互补度 + 日主）、日主生克（相生/相克/比和）、总体评级与
    改善建议（引擎 _generate_advice 原文）。
    """
    sx = hehun_result.shengxiao_detail
    wx = hehun_result.bazi_match
    lines = [
        f"【合婚】A方四柱：{' '.join(bazi_a.bazi)}（日主{bazi_a.day_master}）｜"
        f"B方四柱：{' '.join(bazi_b.bazi)}（日主{bazi_b.day_master}）",
        f"生肖配对：{hehun_result.shengxiao}",
        f"五行互补：{wx.get('wuxing_1', '')} ↔ {wx.get('wuxing_2', '')} —— "
        f"{wx.get('complement_desc', '')}（{wx.get('score', 0)}/40）",
        f"日主生克：{wx.get('day_master_1', '')} × {wx.get('day_master_2', '')} —— "
        f"{wx.get('day_master_relation', '')}",
        f"日柱关系：{hehun_result.rizhu}",
        f"综合评分：{hehun_result.score}/100 —— {grade_for_score(hehun_result.score)}",
        f"改善建议：{hehun_result.advice}",
    ]
    return "\n".join(lines)
