"""自适应AI行动建议引擎 - 使用LLM动态生成个性化建议。

替换硬编码的 advisor.py 规则表，基于用户完整命盘 + 当前处境，
由 LLM 实时生成千人千面的行动建议。
"""
import json
import logging
from typing import Dict, List, Optional

from src.engines.bazi import BaziResult
from src.utils.text_clean import strip_emoji

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

# k63（人设统一·用户拍板）：建议卡风格改为单一豆包式口吻，与主链 system prompt
# （src/bot/handler.py "你是易理明灯，一位懂命理的温暖朋友。说话像豆包…"）同口径。
# 前身：k11-B 按性别分支的 persona（女→毒舌闺蜜 / 男·未知→理性分析师），
# 主链早已统一豆包口吻而本链未跟上 → 同一用户在主回复与建议卡上听到两种人格。
# 本常量取代原 style_instructions 映射（含无人引用的"温柔陪伴者"死分支）。
# 注意：本条只约束**口吻**；**称谓**另由下方「称谓硬规则」段 + fact_guard.scrub_turn
# 输出后校验器负责（两者互相独立，勿合并）。
STYLE_INSTRUCTION = (
    "你是易理明灯，一位懂命理的温暖朋友。说话像豆包：口语化、有温度、"
    "自然不端着，把专业术语（五行、十神、神煞、大运等）讲成大白话。"
    "直接专业作答，禁止油滑/套近乎开场白（如「哈哈」「挺有意思」）；"
    "严格紧扣用户问题，用户没问的（名人相似、旁支话题）不主动展开。"
    "语气始终温暖平等、就事论事：不挖苦、不嘲讽、不贬低用户，"
    "实话也要好好说。"
)
FALLBACK_SERENDIPITY = ""
FALLBACK_INSIGHT = ""

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-flash"


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
        # k63（人设统一）：口吻不再按性别分支——统一豆包式（STYLE_INSTRUCTION），
        # 与主链 system prompt 同口径。性别只影响**称谓**（见 _build_prompt 的
        # 「称谓硬规则」段 + fact_guard.scrub_turn 输出后校验器），不影响口吻。
        try:
            # 1. 构建 LLM Prompt（无名人匹配段）
            prompt = self._build_prompt(bazi_result, user_context)

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

            # k11-B/C（输出后校验器·建议卡字段层）：称谓（男/未知命女性词）与
            # 神煞白名单（本盘引擎全集）去词兜底——prompt 事实纪律之外的第二道，
            # 纯规则零 LLM。消费点（handler 5675/5771、7364 为真实链路；api/advisor
            # REST 为既有死路径——narrative NameError 已于 k11-r1 修复，是否接入
            # 待 k11b 评估）：凡到达 generate() 的调用即被此单点覆盖。
            try:
                from src.utils.fact_guard import scrub_turn
                _g = getattr(bazi_result, "gender", "") or ""
                _allow = list(getattr(bazi_result, "shensha", None) or [])
                for _a in result.get("actions") or []:
                    if isinstance(_a, dict):
                        for _k in ("advice", "timing", "concrete_steps",
                                   "success_metric"):
                            if isinstance(_a.get(_k), str):
                                _a[_k] = scrub_turn(_a[_k], _g, _allow)
                for _k in ("serendipity", "daily_tip", "style_notes"):
                    if isinstance(result.get(_k), str):
                        result[_k] = scrub_turn(result[_k], _g, _allow)
            except Exception:
                pass  # scrub 是增强：异常静默，不阻塞建议返回

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
    ) -> str:
        """构建 LLM Prompt（名人匹配段已移除，2026-08-09）。

        k63：persona 参数（原"毒舌闺蜜"/"理性分析师"按性别分支）已删除，
        口吻恒为 STYLE_INSTRUCTION（豆包式，与主链一致）；性别仅经 _gender_cn
        影响称谓段。
        """
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

        # k11-B：性别（引擎归一：男/女/unknown；主链 client.py:648 同构先例）
        _g = str(getattr(result, "gender", "") or "").strip().lower()
        _gender_cn = {"男": "男", "女": "女", "male": "男", "female": "女"}.get(
            _g, "未知（请用中性表述，勿假设性别）")
        # k11-A：确定性事实包（当前年龄/当前大运段/换运年份/出生档案——引擎
        # result.current_stage 单一事实源，bazi_formatter 单一渲染，两链共用）
        try:
            from src.engines.bazi_formatter import format_fact_pack_block
            fact_block = format_fact_pack_block(result)
        except Exception:
            fact_block = ""
        # k11-D：引擎方向要点（确定性基线）——与择业工具卡同表同函数
        # （src/tools/career_dir.py：行业五行映射 INDUSTRY_WUXING / 方位
        # ELEMENT_DIRECTION / 喜用 helpful_elements / 忌神 forbidden_elements /
        # 日主强弱 day_master_strength_of），防口径分裂。
        direction_block = ""
        try:
            from src.tools.career_dir import (helpful_elements, forbidden_elements,
                                              day_master_strength_of,
                                              forbidden_reason, INDUSTRY_WUXING,
                                              ELEMENT_DIRECTION)
            _hl = helpful_elements(result)
            _fb = forbidden_elements(result)
            _st = day_master_strength_of(result)
            _lines = [f"日主强弱：{_st}"]
            if _hl:
                _lines.append("喜用五行（应补/宜从事之五行）："
                              + "、".join(_hl))
            if _fb:
                _lines.append("忌神五行（{reason}）：{fb}".format(
                    reason=forbidden_reason(_st), fb="、".join(_fb)))
            _lines.append("行业五行映射（命理通识，与择业卡同表）：")
            for _wx in ("金", "木", "水", "火", "土"):
                _labels = "、".join(label for label, _ in INDUSTRY_WUXING.get(_wx, []))
                if _labels:
                    _lines.append(f"  {_wx} → {_labels}")
            _lines.append("方位映射：" + "、".join(
                f"{w}→{ELEMENT_DIRECTION[w]}" for w in ("木", "火", "土", "金", "水")
                if w in ELEMENT_DIRECTION))
            direction_block = "\n".join(_lines)
        except Exception:
            pass  # 基线缺失不阻塞（prompt 其余部分照常）

        prompt = f"""重要：当前年份是{current_year}年。所有时间建议必须以{current_year}年之后的具体日期为准。
你是一位精通子平八字的命理顾问，现在需要为一位用户生成个性化的行动建议。

## 用户命盘数据

性别：{_gender_cn}
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

{STYLE_INSTRUCTION}
"""
        # k11-B：称谓硬规则（男/未知 → 中性）——独立追加段。k63 起口吻已全局统一
        # 豆包式（不再随性别分支），本段只负责**称谓**：禁止把用户当异性/闺蜜称呼、
        # 禁止以女性身份自称；与口吻解耦，勿因"口吻统一了"而删除（k11-B 事故：
        # 男命收到「醒醒吧姐妹」，本段是 prompt 侧第一道，scrub_turn 是第二道）。
        if _gender_cn not in ("男", "女") or _gender_cn == "男":
            prompt += """
## 称谓硬规则（必须遵守）

用户性别：男 或 未知。所有建议正文一律使用中性称谓（「你」「朋友」「这位朋友」）；
严禁任何女性向称谓或闺蜜口吻（如「姐妹」「闺蜜」「亲爱的」「姑娘」「集美」等），
严禁以女性身份自称。语气保持专业、直接、温暖即可。"""
        if fact_block:
            prompt += "\n\n" + fact_block + """
涉及「今年几岁/现在走哪步大运/几岁换运/哪个年龄段」的判断只许引用上方事实包数值，
禁止自行推算或另起口径。"""
        if direction_block:
            prompt += """
## 引擎方向要点（确定性基线——建议方向不得与下列事实冲突或反转）

""" + direction_block + """

【硬约束条款】以上为排盘引擎与命理规则层确定性产出的方向基线：
1. 建议的补泄/行业/方位方向不得与基线相悖（例：喜用补 X 时不得主荐大补忌神五行的
   方向；某行业五行归类一律按映射表，不得自定口径后与表冲突）；
2. 五行盛衰以事实包五行分布为准（例：缺某五行 = 平衡要点，不得说成充足或无关）；
3. 基线未覆盖处可正常展开命理分析，但任何展开不得推翻基线的既有结论；
4. 行业建议给出方向的同时，若该方向属忌神/官杀压力类，可如实提示压力与风险，
   但不得把「压力行业」说成「更旺你」而反转基线。"""

        prompt += """

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
  "serendipity": "你问的是[领域]，但你的命盘同时提示了其他重要信息。格式：'顺便说一句（你可能没问但很重要）：' + 简要说明其他领域的好时机与需注意的风险（80字以内）。如果实在没有特别信息，输出空字符串。",
  "daily_tip": "一句今日小建议（30字以内）",
  "style_notes": "一句话总结用户命格特点和建议（30字以内）"
}}
```

## 核心要求

1. 每条建议必须包含**具体行动** + **时间窗口（含具体日期范围）** + **置信度**（简洁精炼，不要输出多余字段）
2. 5个领域必须全部覆盖：事业、财运、感情、健康、个人成长
3. 建议必须基于用户的实际命盘数据，不能是通用模板。要引用具体的五行、十神、神煞、大运流年来支撑分析
4. 时间窗口要具体到日期范围（如"2027年9月15日至10月15日"），不能只说季节或月份
5. 风格必须符合上方「说话风格要求」（温暖、口语化、把术语讲成大白话）
6. 如果用户问了特定领域（如"事业"），也要通过serendipity提示其他领域的重要信息
7. **只输出JSON，不要其他任何文字**
8. 回复中不使用任何 emoji 表情符号（所有字段均不得包含 emoji）"""

        return prompt

    # ----------------------------------------------------------
    # LLM 调用
    # ----------------------------------------------------------

    def _call_llm(self, prompt: str, api_key: str) -> str:
        """调用 DeepSeek Flash API（Anthropic 兼容端点 + thinking disabled）。

        Bugfix: 原生 /v1/chat/completions 下 deepseek-flash 是推理模型，
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
        # emoji 强收敛：统一模型层已 strip，此处再兜底（直调点最终输出统一过 strip_emoji）
        return strip_emoji(content)

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
