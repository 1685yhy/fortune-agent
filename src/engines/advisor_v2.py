"""自适应AI行动建议引擎 - 使用LLM动态生成个性化建议。

替换硬编码的 advisor.py 规则表，基于用户完整命盘 + 当前处境，
由 LLM 实时生成千人千面的行动建议。
"""
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

from src.engines.bazi import BaziEngine, BaziResult

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
# 名人库匹配 (Sprint 6 前置)
# ============================================================

class CelebrityMatcher:
    """从1005人名人库中找到与用户命盘最相似的TOP3。

    在初始化时预计算所有名人的八字信息并缓存，
    后续匹配只需 O(n) 的相似度计算即可完成。
    """

    def __init__(self, data_path: Optional[str] = None):
        self._engine = BaziEngine()
        self._celebrities: List[dict] = []
        if data_path is None:
            # 尝试默认路径
            candidates = [
                "data/celebrity_cases.json",
                "/home/a/fortune-agent/data/celebrity_cases.json",
                str(Path(__file__).parent.parent.parent / "data" / "celebrity_cases.json"),
            ]
            for p in candidates:
                if Path(p).exists():
                    data_path = p
                    break
        if data_path and Path(data_path).exists():
            self._load_and_precompute(data_path)
            logger.info(f"名人库加载完成: {len(self._celebrities)} 人")
        else:
            logger.warning(f"名人库文件未找到: {data_path}")

    # ----------------------------------------------------------
    # 加载与预计算
    # ----------------------------------------------------------

    def _load_and_precompute(self, data_path: str) -> None:
        """加载名人数据并预计算八字信息。"""
        raw = json.loads(Path(data_path).read_text(encoding="utf-8"))
        for entry in raw:
            try:
                bazi_info = self._compute_celebrity_bazi(entry)
                if bazi_info:
                    self._celebrities.append(bazi_info)
            except Exception as e:
                logger.debug(f"计算名人八字失败: {entry.get('name', '?')} - {e}")

    def _compute_celebrity_bazi(self, entry: dict) -> Optional[dict]:
        """计算一位名人的八字信息。

        出生时间缺省时默认为子时（0点）。
        """
        name = entry.get("name", "")
        birth = entry.get("birth", "")
        events = entry.get("events", [])

        parsed = self._parse_birth(birth)
        if not parsed:
            return None

        year, month, day, hour, minute, city, gender = parsed
        try:
            result = self._engine.calculate(year, month, day, hour, minute, city, gender)
            return {
                "name": name,
                "birth": birth,
                "day_pillar": result.bazi[2],  # 日柱
                "day_master": result.day_master,
                "geju": result.geju,
                "yongshen": result.yongshen,
                "wuxing": result.wuxing,
                "events": events,
            }
        except Exception:
            return None

    def _parse_birth(self, birth_str: str) -> Optional[Tuple[int, int, int, int, int, str, str]]:
        """解析出生字符串为 (年,月,日,时,分,城市,性别)。

        支持格式:
          '1964年9月10日 时辰不详 杭州 男'
          '1955年2月24日 19:15 旧金山 男'
          '2000年11月28日 时辰不详 怀化 男'
        """
        try:
            # 提取年月日
            date_match = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", birth_str)
            if not date_match:
                return None
            year = int(date_match.group(1))
            month = int(date_match.group(2))
            day = int(date_match.group(3))

            # 提取时间（缺省为子时）
            hour = 0
            minute = 0
            time_match = re.search(r"(\d{1,2}):(\d{2})", birth_str)
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2))

            # 提取性别
            gender = "男"
            if "女" in birth_str:
                gender = "女"

            # 提取城市（简单取括号外最后一个词）
            city = "北京"
            # 去掉年份时间部分，取最后的词
            remaining = re.sub(r"\d{4}年.*?\d{1,2}日", "", birth_str)
            remaining = re.sub(r"\d{1,2}:\d{2}", "", remaining)
            remaining = remaining.strip().rstrip("。")
            # 取剩余文本中最后一个2-3字词作为城市
            parts = re.findall(r"[一-鿿]{2,4}", remaining)
            if parts:
                # 取倒数第2个（倒数第一个通常是性别）
                for p in reversed(parts):
                    if p not in ("男", "女", "时辰不详", "不详"):
                        city = p
                        break

            return (year, month, day, hour, minute, city, gender)
        except Exception:
            return None

    # ----------------------------------------------------------
    # 匹配
    # ----------------------------------------------------------

    def find_top_matches(self, user_bazi: BaziResult, top_k: int = 3) -> List[dict]:
        """找到与用户命盘最相似的 top_k 个名人。

        返回列表，每个元素:
          { "name": str, "similarity": float, "geju": str, "day_pillar": str }
        """
        if not self._celebrities:
            return []

        user_info = {
            "day_pillar": user_bazi.bazi[2],
            "day_master": user_bazi.day_master,
            "geju": user_bazi.geju,
            "yongshen": user_bazi.yongshen,
        }

        scored = []
        for celeb in self._celebrities:
            score = self._similarity_score(user_info, celeb)
            scored.append((score, celeb))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:top_k]

        return [
            {
                "name": item[1]["name"],
                "similarity": round(item[0] * 100),
                "geju": item[1]["geju"],
                "day_pillar": item[1]["day_pillar"],
                "day_master": item[1]["day_master"],
                "events": item[1].get("events", []),
            }
            for item in top
            if item[0] > 0
        ]

    def _similarity_score(self, user: dict, celeb: dict) -> float:
        """计算用户与名人的相似度得分（0.0 ~ 1.0）。"""
        score = 0.0

        # 1. 日柱完全相同（天干地支都匹配）→ 强相似
        if user.get("day_pillar") == celeb.get("day_pillar"):
            score += 0.40
        # 日柱天干相同 → 中等相似
        elif user.get("day_pillar") and celeb.get("day_pillar"):
            if user["day_pillar"][0] == celeb["day_pillar"][0]:
                score += 0.20

        # 2. 格局相同
        if user.get("geju") and celeb.get("geju"):
            if user["geju"] == celeb["geju"]:
                score += 0.25

        # 3. 用神主五行相同
        user_yongshen = self._extract_main_yongshen(user.get("yongshen", ""))
        celeb_yongshen = self._extract_main_yongshen(celeb.get("yongshen", ""))
        if user_yongshen and celeb_yongshen:
            if user_yongshen == celeb_yongshen:
                score += 0.20
            elif user_yongshen in ("金", "水") and celeb_yongshen in ("金", "水"):
                score += 0.10
            elif user_yongshen in ("木", "火") and celeb_yongshen in ("木", "火"):
                score += 0.10

        # 4. 日主五行相同
        user_dm = self._extract_dm_element(user.get("day_master", ""))
        celeb_dm = self._extract_dm_element(celeb.get("day_master", ""))
        if user_dm and celeb_dm and user_dm == celeb_dm:
            score += 0.15

        return min(score, 1.0)

    @staticmethod
    def _extract_main_yongshen(yongshen_str: str) -> str:
        """从用神字符串提取主用神五行。

        "水为用神（调候优先）（喜水、木）" → "水"
        """
        m = re.match(r"([金木水火土])为用神", yongshen_str)
        return m.group(1) if m else ""

    @staticmethod
    def _extract_dm_element(day_master: str) -> str:
        """从日主字符串提取五行。

        "乙木" → "木"
        """
        m = re.search(r"([木火土金水])", day_master)
        return m.group(1) if m else ""


# ============================================================
# 自适应AI行动建议引擎
# ============================================================

class AdaptiveAdvisor:
    """自适应AI行动建议引擎。

    使用 LLM 动态生成个性化建议，取代硬编码规则表。
    """

    def __init__(self):
        self._matcher: Optional[CelebrityMatcher] = None

    @property
    def matcher(self) -> CelebrityMatcher:
        if self._matcher is None:
            self._matcher = CelebrityMatcher()
        return self._matcher

    # ----------------------------------------------------------
    # 主入口
    # ----------------------------------------------------------

    def generate(
        self,
        bazi_result: BaziResult,
        user_context: str = "",
        personality: str = "sassy",
        api_key: str = "",
    ) -> dict:
        """使用LLM动态生成个性化建议。

        Args:
            bazi_result: 八字排盘结果
            user_context: 用户当前处境/问题描述
            personality: 人格模式 (sassy/analyst/gentle)
            api_key: DeepSeek API密钥

        Returns:
            {
                "actions": [{"category":"事业","advice":"...","timing":"...","confidence":"high",
                             "concrete_steps":"...","success_metric":"..."}, ...],
                "celebrity_match": {"name":"马云","similarity":78,"geju":"...","insight":"...", ...},
                "insight": "🔥 命运对照：你的命盘和马云相似度78%...",
                "serendipity": "💡 顺便说一句（你可能没问但很重要）...",
                "daily_tip": "...",
                "style_notes": "..."
            }
        """
        personality_label = {
            "sassy": "毒舌闺蜜",
            "analyst": "理性分析师",
            "gentle": "温柔陪伴者",
        }.get(personality, "毒舌闺蜜")

        try:
            # 1. 名人匹配
            top_matches = self.matcher.find_top_matches(bazi_result)

            # 2. 构建 LLM Prompt
            prompt = self._build_prompt(bazi_result, user_context, personality_label, top_matches)

            # 3. 调用 DeepSeek Flash
            llm_output = self._call_llm(prompt, api_key)

            # 4. 解析 LLM 输出
            result = self._parse_llm_output(llm_output)

            # 5. 添加名人匹配信息
            if top_matches:
                result["celebrity_match"] = top_matches[0]
                result["celebrity_match"]["insight"] = result.get("celebrity_insight", "")
                # 移除内部用的 key
                result.pop("celebrity_insight", None)
            else:
                result["celebrity_match"] = {}

            # 6. 提取 serendipity 和 insight 顶层字段
            result["serendipity"] = result.get("serendipity", FALLBACK_SERENDIPITY)
            result["insight"] = result.get("celebrity_match", {}).get("insight", FALLBACK_INSIGHT)

            # 7. 确保所有字段都有值
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
            return self._fallback_result(top_matches if 'top_matches' in dir() else [])

    # ----------------------------------------------------------
    # Prompt 构建
    # ----------------------------------------------------------

    def _build_prompt(
        self,
        result: BaziResult,
        user_context: str,
        personality_label: str,
        top_matches: List[dict],
    ) -> str:
        """构建 LLM Prompt。"""
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

        # 名人匹配信息（含人生事件）
        celeb_str = ""
        if top_matches:
            celeb_lines = []
            for i, match in enumerate(top_matches, 1):
                line = (
                    f"  {i}. {match['name']} - 日柱: {match['day_pillar']}, "
                    f"格局: {match['geju']}, 相似度: {match['similarity']}%"
                )
                if match.get("events"):
                    events_detail = "; ".join(
                        f"{e['year']}年: {e['event']}" for e in match["events"]
                    )
                    line += f"\n     关键事件: {events_detail}"
                celeb_lines.append(line)
            celeb_str = "\n".join(celeb_lines)

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

## 用户命盘相似名人

{celeb_str if celeb_str else "（无明显相似名人）"}

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
      "advice": "具体的行动建议，2-3句话，必须结合用户的八字数据给出个性化理由",
      "timing": "最佳行动时间窗口，必须包含具体日期范围（如'2027年9月15日-10月15日'、'农历八月十五至九月初九'、'2027年立春到大暑'）",
      "confidence": "high/medium/low",
      "concrete_steps": "2-3条具体可执行步骤，用数字编号（如'1.本月联系关键人脉 2.下月准备材料 3.年底冲刺'）",
      "success_metric": "可衡量的成功指标（如'如果方向正确，3个月内你会看到收入增长20%以上'、'坚持1个月睡眠质量会有明显改善'）"
    }},
    {{
      "category": "财运",
      "advice": "具体的行动建议，结合八字五行的个性化分析",
      "timing": "最佳时间窗口，包含具体日期范围",
      "confidence": "high/medium/low",
      "concrete_steps": "具体可执行步骤",
      "success_metric": "可衡量的成功指标"
    }},
    {{
      "category": "感情",
      "advice": "具体的行动建议，结合八字五行和神煞的个性化分析",
      "timing": "最佳时间窗口，包含具体日期范围",
      "confidence": "high/medium/low",
      "concrete_steps": "具体可执行步骤",
      "success_metric": "可衡量的成功指标"
    }},
    {{
      "category": "健康",
      "advice": "具体的行动建议，结合五行失衡的个性化健康分析",
      "timing": "最佳时间窗口，包含具体日期范围",
      "confidence": "high/medium/low",
      "concrete_steps": "具体可执行步骤",
      "success_metric": "可衡量的成功指标"
    }},
    {{
      "category": "个人成长",
      "advice": "具体的行动建议，结合命局的长远发展建议",
      "timing": "最佳时间窗口，包含具体日期范围",
      "confidence": "high/medium/low",
      "concrete_steps": "具体可执行步骤",
      "success_metric": "可衡量的成功指标"
    }}
  ],
  "serendipity": "你问的是[领域]，但你的命盘同时提示了其他重要信息。格式：'💡 顺便说一句（你可能没问但很重要）：' + 换行 + 详细说明其他领域的好时机 + 换行 + 隐藏优势 + 换行 + 需注意的风险（200字以内）。如果实在没有特别信息，输出空字符串。",
  "celebrity_insight": "如果上方列出了相似名人，请写一段详细、有感染力的命运对照分析（200-400字）。要求：1) 开头用'🔥 命运对照：你的命盘和[名人]相似度[XX]%' 2) 对比你们相同的格局类型和性格特征 3) 引用名人具体的人生事件/转折点，做类比分析 4) 指出名人犯过的错误和你的独特优势 5) 以'这不是宿命论。你们的命盘像两棵同样品种的树——能不能长成参天大树，看你自己的选择。'或类似的升华结尾。如果没有相似名人，请写空字符串。",
  "daily_tip": "一句今日小建议（30字以内）",
  "style_notes": "一句话总结用户命格特点和建议（30字以内）"
}}
```

## 核心要求

1. 每条建议必须包含**具体行动** + **时间窗口（含具体日期范围）** + **置信度** + **具体步骤** + **成功指标**
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
        """调用 DeepSeek Flash API。"""
        if not api_key:
            logger.warning("API key 为空，使用备用建议")
            raise ValueError("API key is empty")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": "你是一个精通子平八字的AI命理顾问。你善于根据用户的八字命盘生成个性化的、可执行的行动建议。你只输出JSON格式数据，不输出其他文字。"},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 2500,
            "temperature": 0.8,
            "response_format": {"type": "json_object"},
        }

        resp = httpx.post(
            DEEPSEEK_API_URL,
            headers=headers,
            json=payload,
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()

        content = data["choices"][0]["message"]["content"]
        logger.info(f"LLM调用成功, tokens: {data.get('usage', {}).get('total_tokens', 0)}")
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

    def _fallback_result(self, top_matches: List[dict]) -> dict:
        """API失败时返回的备用结果。"""
        actions = []
        for domain in LIFE_DOMAINS:
            advice_list = FALLBACK_ADVICE.get(domain, ["请稍后再试。"])
            actions.append({
                "category": domain,
                "advice": advice_list[0],
                "timing": "近期",
                "confidence": "medium",
            })

        result = {
            "actions": actions,
            "celebrity_match": top_matches[0] if top_matches else {},
            "daily_tip": FALLBACK_DAILY_TIP,
            "style_notes": FALLBACK_STYLE_NOTES,
            "serendipity": FALLBACK_SERENDIPITY,
            "insight": FALLBACK_INSIGHT,
        }
        if top_matches:
            result["celebrity_match"]["insight"] = (
                f"你和{top_matches[0]['name']}的命盘格局有一定相似度，"
                f"可以关注他/她的人生经历作为参考。"
            )
            result["insight"] = result["celebrity_match"]["insight"]
        return result
