"""Narrative Service -- LLM-generated natural language for all fortune types.

Every API endpoint returns structured engine data + LLM narrative.
This service is the narrative engine behind all premium responses.
"""
import logging
from typing import Optional

import src.llm.prompts as prompts

logger = logging.getLogger(__name__)


class NarrativeService:
    """Generates warm, insightful narratives from structured fortune data."""

    def __init__(self, llm):
        self.llm = llm  # FortuneLLM instance

    # ------------------------------------------------------------------
    # Public methods -- one per fortune type
    # ------------------------------------------------------------------

    def hehun(self, result_dict: dict) -> str:
        """Hehun narrative -- warm, personal, specific advice.

        Input: {total_score, wuxing:{score,detail,complement,deficiency},
                shengxiao:{type,score,relation,shengxiao_1,shengxiao_2},
                rizhu:{score,detail,rizhi_relation,rigan_relation}}
        """
        prompt = self._build_hehun_prompt(result_dict)
        return self._call_llm(prompt, extra_system=prompts.NARRATIVE_HEHUN)

    def qimen(self, chart_dict: dict, question: str) -> str:
        """Qimen narrative -- strategic, insightful timing advice."""
        prompt = self._build_qimen_prompt(chart_dict, question)
        return self._call_llm(prompt, extra_system=prompts.NARRATIVE_QIMEN)

    def xingming(self, result_dict: dict) -> str:
        """Xingming narrative -- personal, nuanced name interpretation."""
        prompt = self._build_xingming_prompt(result_dict)
        return self._call_llm(prompt, extra_system=prompts.NARRATIVE_XINGMING)

    def hourly(self, data_dict: dict) -> str:
        """Hourly fortune narrative -- concise daily guidance summary."""
        prompt = self._build_hourly_prompt(data_dict)
        return self._call_llm(prompt, extra_system=prompts.NARRATIVE_HOURLY)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call_llm(self, prompt: str, extra_system: str = "") -> str:
        """Central LLM call with graceful degradation."""
        try:
            result = self.llm.analyze(
                chart_data=prompt,
                references=[],
                user_question="请生成一段温暖、有见地的分析叙述",
                use_pro=False,
                extra_system_prompt=extra_system,
            )
            return result.response.strip()
        except Exception as e:
            logger.warning("Narrative generation failed: %s", e)
            return ""  # graceful degradation -- narrative is additive

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    def _build_hehun_prompt(self, d: dict) -> str:
        wx = d.get("wuxing", {})
        sx = d.get("shengxiao", {})
        rz = d.get("rizhu", {})
        return (
            f"【合婚数据】\n"
            f"总分：{d.get('total_score', 0)}/100\n\n"
            f"【五行互补】\n"
            f"分数：{wx.get('score', 0)}\n"
            f"分析：{wx.get('detail', '')}\n"
            f"互补项：{', '.join(wx.get('complement', []))}\n"
            f"不足：{wx.get('deficiency', '')}\n\n"
            f"【生肖配对】\n"
            f"类型：{sx.get('type', '')}\n"
            f"分数：{sx.get('score', 0)}\n"
            f"关系：{sx.get('relation', '')}\n"
            f"双方生肖：{sx.get('shengxiao_1', '')} vs {sx.get('shengxiao_2', '')}\n\n"
            f"【日柱关系】\n"
            f"分数：{rz.get('score', 0)}\n"
            f"日支关系：{rz.get('rizhi_relation', '')}\n"
            f"日干关系：{rz.get('rigan_relation', '')}\n"
            f"详细：{rz.get('detail', '')}\n\n"
            f"请根据以上合婚数据，生成一段温暖有见地的分析叙述。"
        )

    def _build_qimen_prompt(self, d: dict, question: str) -> str:
        palaces = d.get("palaces", [])
        palace_lines = []
        for p in palaces[:5]:  # top 5 palaces for narrative context
            palace_lines.append(
                f"{p.get('palace', '')}: "
                f"八神={p.get('bashen', '')} 九星={p.get('jiuxing', '')} "
                f"八门={p.get('bamen', '')} 天盘={p.get('tianpan', '')} 地盘={p.get('dipan', '')}"
            )
        return (
            f"【奇门遁甲盘面】\n"
            f"遁局：{d.get('dun_type', '')} {d.get('ju_number', 0)}局\n"
            f"值符：{d.get('zhifu_star', '')}，值使：{d.get('zhishi_door', '')}\n"
            f"节气：{d.get('solar_term', '')} {d.get('yuan', '')}元\n"
            f"四柱：{d.get('bazi', '')}\n\n"
            f"【宫位简览】\n"
            f"{chr(10).join(palace_lines)}\n\n"
            f"【用户问题】\n"
            f"{question}\n\n"
            f"请根据以上奇门盘面，针对用户问题给出战略指导和时机建议。"
        )

    def _build_xingming_prompt(self, d: dict) -> str:
        wuge = d.get("wuge", {})
        analysis = d.get("analysis", {})
        wx = d.get("wuxing", {})
        return (
            f"【姓名学数据】\n"
            f"姓名：{d.get('surname', '')}{d.get('given_name', '')}\n"
            f"性别：{d.get('gender', '')}\n\n"
            f"【五格数理】\n"
            f"天格：{wuge.get('tianGe', {}).get('value', '')} ({wuge.get('tianGe', {}).get('quality', '')})\n"
            f"人格：{wuge.get('renGe', {}).get('value', '')} ({wuge.get('renGe', {}).get('quality', '')})\n"
            f"地格：{wuge.get('diGe', {}).get('value', '')} ({wuge.get('diGe', {}).get('quality', '')})\n"
            f"外格：{wuge.get('waiGe', {}).get('value', '')} ({wuge.get('waiGe', {}).get('quality', '')})\n"
            f"总格：{wuge.get('zongGe', {}).get('value', '')} ({wuge.get('zongGe', {}).get('quality', '')})\n\n"
            f"【三才配置】\n"
            f"{d.get('sancai', '')} ({d.get('sancai_ji', '')})\n\n"
            f"【综合结论】\n"
            f"{d.get('overall', '')}\n\n"
            f"请根据以上姓名学数据，生成一段有文化内涵的姓名解读。"
        )

    def _build_hourly_prompt(self, d: dict) -> str:
        slots = d.get("slots", [])
        best = d.get("best_hours", [])
        worst = d.get("worst_hours", [])
        slot_lines = []
        for s in slots[:6]:  # top 6 slots for brevity
            slot_lines.append(
                f"{s.get('name', '')}({s.get('time', '')}) "
                f"{s.get('rating_label', '')} - {s.get('reason', '')}"
            )
        return (
            f"【时辰运势】\n"
            f"日期：{d.get('date', '')}\n"
            f"日干支：{d.get('day_ganzhi', '')}\n"
            f"用户日主：{d.get('user_day_master', '')}\n\n"
            f"【时段简览】\n"
            f"{chr(10).join(slot_lines)}\n\n"
            f"【最佳时段】\n"
            f"{'、'.join(best) if best else '无特别最佳时段'}\n\n"
            f"【注意时段】\n"
            f"{'、'.join(worst) if worst else '无特别凶时'}\n\n"
            f"请根据以上数据，生成一段精炼的每日运势播报（150-250字）。"
        )
