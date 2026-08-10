"""自适应AI行动建议引擎 - 使用LLM动态生成个性化建议。

替换硬编码的 advisor.py 规则表，基于用户完整命盘 + 当前处境，
由 LLM 实时生成千人千面的行动建议。
"""
import json
import logging
from typing import Dict, List, Optional

from src.engines.bazi import BaziResult

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================

LIFE_DOMAINS = ["事业", "财运", "感情", "健康", "个人成长"]

# 备用建议（API 失败时使用）
FALLBACK_ADVICE = {
    domain: [
        f"{domain}方面的建议需要结合你的具体处境来分析。",
        "人生充满可能性，保持开放的心态面对每一天。",
    ]
    for domain in LIFE_DOMAINS
}

FALLBACK_DAILY_TIP = "今天宜保持平静，不宜冲动决策。花点时间关注自己的内心需求。"
FALLBACK_STYLE_NOTES = "每个人的命格都是独特的，建议根据自身情况灵活调整。"
FALLBACK_SERENDIPITY = ""
FALLBACK_INSIGHT = ""

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"


# ============================================================
# 名人库匹配（已彻底移除 2026-08-09，用户确认方案 v5 选 A）
# 原 CelebrityMatcher 每次对话加载 950 人并预计算八字，且未问"像谁"也输出
# 名人对照（用户实测差评点：答非所问"和李连杰相似"）。全部删除。
# 命例相似度引擎 similarity.py 同步停用（不再被任何代码调用）。
# ============================================================


# ============================================================
# 自适应AI行动建议引擎
# ============================================================

class AdaptiveAdvisor:
    """自适应AI行动建议引擎。

    使用 LLM 动态生成个性化建议，取代硬编码规则表。
    名人匹配已移除（2026-08-09 方案 v5 A）：不再加载/输出任何名人对照。
    """

    # ----------------------------------------------------------
    # 主入口
    # ----------------------------------------------------------

    def generate(
        self,
        bazi_result: BaziResult,
        user_context: str = "",
        api_key: str = "",
    ) -> dict:
        """使用LLM动态生成个性化建议。

        Args:
            bazi_result: 八字排盘结果
            user_context: 用户当前处境/问题描述
            api_key: DeepSeek API密钥

        Returns:
            {
                "actions": [{"category":"事业","advice":"...","timing":"...","confidence":"high",
                             "concrete_steps":"...","success_metric":"..."}, ...],
                "serendipity": "💡 顺便说一句（你可能没问但很重要）...",
                "daily_tip": "...",
                "style_notes": "..."
            }
        """
        personality_label = "毒舌闺蜜"

        try:
            # 1. 构建 LLM Prompt（无名人匹配段）
            prompt = self._build_prompt(bazi_result, user_context, personality_label)

            # 2. 调用 DeepSeek Flash
            llm_output = self._call_llm(prompt, api_key)

            # 3. 解析 LLM 输出
            result = self._parse_llm_output(llm_output)

            # 4. 兜底字段（celebrity_match 恒为空 dict，兼容旧调用方取值）
            result.setdefault("celebrity_match", {})
            result["serendipity"] = result.get("serendipity", FALLBACK_SERENDIPITY)
            result["insight"] = FALLBACK_INSIGHT

            # 5. 确保所有字段都有值
            result.setdefault("daily_tip", FALLBACK_DAILY_TIP)
            result.setdefault("style_notes", FALLBACK_STYLE_NOTES)
            if not result.get("actions"):
                result["actions"] = [
                    {"category": d, "advice": FALLBACK_ADVICE[d][0], "timing": "近期", "confidence": "medium"}
                    for d in LIFE_DOMAINS
                ]

            return result

        except Exception as e:
            logger.error(f"AdaptiveAdvisor 生成失败: {e}", exc_info=True)
            return self._fallback_result()

    # ----------------------------------------------------------
    # Prompt 构建
    # ----------------------------------------------------------

    def _build_prompt(
        self,
        result: BaziResult,
        user_context: str,
        personality_label: str,
    ) -> str:
        """构建 LLM Prompt（名人匹配段已移除，2026-08-09）。"""
        from datetime import datetime
        current_year = datetime.now().year
        # 格式化命盘数据
        bazi_str = " ".join(result.bazi)
        dayun_str = " → ".join(f"{age}岁{ganzhi}" for age, ganzhi in result.dayun[:6])
        shishen_str = " ".join(result.shishen)
        shensha_str = "、".join(result.shensha) if result.shensha else "无"
        nayin_str = "、".join(result.nayin)
        liunian_items = sorted(result.liunian.items())
        liunian_str = "、".join(f"{y}年: {gz}" for y, gz in liunian_items[:5])
        wuxing_breakdown = "、".join(f"{wx}:{count}" for wx, count in result.wuxing.items())

        # 性格风格说明
        style_instructions = {
            "毒舌闺蜜": (
                "风格：说话犀利、直接、带点小毒舌，像闺蜜一样说实话。"
                "可以用网络用语，让用户先笑再思考。"
                "该怼就怼，但真心为用户好。"
            ),
            "理性分析师": (
                "风格：数据化、结构化、理性客观。"
                "用概率和百分比说话，严谨专业。"
                "像麦肯锡顾问一样给建议。"
            ),
            "温柔陪伴者": (
                "风格：温暖、共情、接纳。"
                "先理解感受再给建议，给予安全感和赋能感。"
                "像心理咨询师一样温柔而坚定。"
            ),
        }.get(personality_label, "")

        prompt = f"""重要：当前年份是{current_year}年。所有时间建议必须以{current_year}年之后的具体日期为准。
你是一位精通子平八字的命理顾问，现在需要为一位用户生成个性化的行动建议。

## 用户命盘数据

八字四柱：{bazi_str}
日主：{result.day_master}
纳音：{nayin_str}
五行分布：{wuxing_breakdown}
十神：{shishen_str}
格局：{result.geju}
用神：{result.yongshen}
神煞：{shensha_str}
大运：{dayun_str}
流年（近5年）：{liunian_str}

## 用户当前处境

{user_context if user_context else "（用户未提供具体处境，请按一般情况给建议）"}

## 说话风格要求

当前模式：{personality_label}
{style_instructions}

## 输出格式要求

请**只输出**一个JSON对象（不要markdown代码块标记，不要其他文字），格式如下：

```json
{{
  "actions": [
    {{
      "category": "事业",
      "advice": "具体的行动建议，1-2句话，精炼有力，必须结合用户的八字数据给出个性化理由",
      "timing": "最佳行动时间窗口，必须包含具体日期范围（如'2027年9月15日-10月15日'、'农历八月十五至九月初九'、'2027年立春到大暑'）",
      "confidence": "high/medium/low"
    }},
    {{
      "category": "财运",
      "advice": "具体的行动建议，结合八字五行的个性化分析",
      "timing": "最佳时间窗口，包含具体日期范围",
      "confidence": "high/medium/low"
    }},
    {{
      "category": "感情",
      "advice": "具体的行动建议，结合八字五行和神煞的个性化分析",
      "timing": "最佳时间窗口，包含具体日期范围",
      "confidence": "high/medium/low"
    }},
    {{
      "category": "健康",
      "advice": "具体的行动建议，结合五行失衡的个性化健康分析",
      "timing": "最佳时间窗口，包含具体日期范围",
      "confidence": "high/medium/low"
    }},
    {{
      "category": "个人成长",
      "advice": "具体的行动建议，结合命局的长远发展建议",
      "timing": "最佳时间窗口，包含具体日期范围",
      "confidence": "high/medium/low"
    }}
  ],
  "serendipity": "你问的是[领域]，但你的命盘同时提示了其他重要信息。格式：'💡 顺便说一句（你可能没问但很重要）：' + 简要说明其他领域的好时机与需注意的风险（80字以内）。如果实在没有特别信息，输出空字符串。",
  "daily_tip": "一句今日小建议（30字以内）",
  "style_notes": "一句话总结用户命格特点和建议（30字以内）"
}}
```

## 核心要求

1. 每条建议必须包含**具体行动** + **时间窗口（含具体日期范围）** + **置信度**（简洁精炼，不要输出多余字段）
2. 5个领域必须全部覆盖：事业、财运、感情、健康、个人成长
3. 建议必须基于用户的实际命盘数据，不能是通用模板。要引用具体的五行、十神、神煞、大运流年来支撑分析
4. 时间窗口要具体到日期范围（如"2027年9月15日至10月15日"），不能只说季节或月份
5. 风格要符合当前模式的要求
6. 如果用户问了特定领域（如"事业"），也要通过serendipity提示其他领域的重要信息
7. **只输出JSON，不要其他任何文字**"""

        return prompt

    # ----------------------------------------------------------
    # LLM 调用
    # ----------------------------------------------------------

    def _call_llm(self, prompt: str, api_key: str) -> str:
        """调用 DeepSeek Flash API（Anthropic 兼容端点 + thinking disabled）。

        Bugfix: 原生 /v1/chat/completions 下 deepseek-v4-flash 是推理模型，
        reasoning_content 占满 max_tokens 导致 content 为空或 30s 读超时
        （"The read operation timed out"）；改用与 src/llm/client.py 一致的
        端点与配置，内容稳定返回。
        """
        if not api_key:
            logger.warning("API key 为空，使用备用建议")
            raise ValueError("API key is empty")

        from src.llm.client import deepseek_anthropic_completion
        content = deepseek_anthropic_completion(
            api_key,
            [
                {"role": "system", "content": "你是一个精通子平八字的AI命理顾问。你善于根据用户的八字命盘生成个性化的、可执行的行动建议。你只输出JSON格式数据，不输出其他文字。"},
                {"role": "user", "content": prompt},
            ],
            model=DEEPSEEK_MODEL,
            max_tokens=3000,
            temperature=0.8,
            timeout=45.0,
        )
        logger.info("LLM调用成功")
        return content

    # ----------------------------------------------------------
    # 解析 LLM 输出
    # ----------------------------------------------------------

    def _parse_llm_output(self, raw: str) -> dict:
        """解析LLM返回的JSON字符串。

        兼容以下情况：
        - 纯净的JSON字符串
        - 被 ```json ... ``` 包裹
        - 被 ``` ... ``` 包裹（不带json标记）
        """
        text = raw.strip()

        # 去掉可能的 markdown 代码块标记
        if text.startswith("```"):
            # 找到第一个换行，去掉第一行（```json 或 ```）
            first_nl = text.find("\n")
            if first_nl != -1:
                text = text[first_nl + 1:]
            # 去掉最后的 ```
            if text.endswith("```"):
                text = text[:-3].strip()
            elif text.endswith("```"):
                text = text[:-3].strip()

        # 尝试解析JSON
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # 如果直接解析失败，尝试从文本中提取JSON对象
            data = self._extract_json(text)

        if not isinstance(data, dict):
            raise ValueError("LLM输出不是有效的JSON对象")

        return data

    def _extract_json(self, text: str) -> dict:
        """从文本中尝试提取JSON对象。"""
        # 尝试找到第一个 { 到最后一个 }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        raise ValueError(f"无法解析LLM输出为JSON: {text[:200]}")

    # ----------------------------------------------------------
    # 备用结果（API 失败时）
    # ----------------------------------------------------------

    def _fallback_result(self) -> dict:
        """API失败时返回的备用结果（名人匹配已移除，celebrity_match 恒为空）。"""
        actions = []
        for domain in LIFE_DOMAINS:
            advice_list = FALLBACK_ADVICE.get(domain, ["请稍后再试。"])
            actions.append({
                "category": domain,
                "advice": advice_list[0],
                "timing": "近期",
                "confidence": "medium",
            })

        return {
            "actions": actions,
            "celebrity_match": {},
            "daily_tip": FALLBACK_DAILY_TIP,
            "style_notes": FALLBACK_STYLE_NOTES,
            "serendipity": FALLBACK_SERENDIPITY,
            "insight": FALLBACK_INSIGHT,
        }
