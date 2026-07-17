"""解梦引擎 v4 - 基于用户行为研究 + 完整上下文 + TOP10高频梦"""
from dataclasses import dataclass, field
from typing import List, Optional
import re


@dataclass
class DreamResult:
    original_text: str = ""
    dream_type: str = ""        # 高频梦境类型
    keywords: list = field(default_factory=list)
    interpretations: list = field(default_factory=list)
    source: str = ""


class DreamEngine:
    """解梦引擎：用户行为研究驱动"""

    # 根据用户研究：TOP10高频梦境（90%的人都梦到过）
    TOP_TEN_PATTERNS = {
        "蛇": ("动物类", "蛇在传统中象征变化、智慧、潜意识中的原始本能。梦见蛇常与财运变动、人际关系暗流、健康隐忧相关。"),
        "掉牙|牙齿.*掉|牙.*脱": ("身体类", "最常见的焦虑梦境之一。传统认为梦见掉牙与长辈健康、人际关系损失相关。心理学认为反映对外表或沟通能力的担忧。"),
        "追.*我|被.*追|追赶": ("压力类", "反映现实中的逃避心态。被什么追=你在逃避什么。学生考试前出现概率高出平时3倍。"),
        "水|淹|洪|海|河|湖": ("财运类", "梦见水在传统梦学中主财运。水清主吉财，水浊主口舌破财。涨大水可能预示财务大变动。"),
        "飞|飘|翔|悬空": ("事业类", "飞翔梦反映对自由和突破的渴望。上升=事业上升期，坠落=感到失控。与大脑前庭器官兴奋有关。"),
        "考试|考题|高考|成绩|不及格": ("焦虑类", "典型的\"未完成情结\"。即使毕业多年仍梦见考试，反映对当前能力的自我怀疑。"),
        "死.*人|去世|棺材|鬼|坟墓|冥": ("生死类", "梦见棺材传统上主\"升官发财\"，梦见死人说话常是潜意识在处理未完成的情感。"),
        "坠落|踩空|掉下|摔下": ("身体类", "几乎人人都有过的踏空体验。与心脏供血变化、钙缺乏、睡姿有关。反映对失控的恐惧。"),
        "钱|金|银|财|捡.*钱|中奖": ("财运类", "捡钱=小财运将至，丢钱=注意财务安全。现代也反映对经济状况的焦虑。"),
        "手机.*丢|手机.*没电|手机.*坏|迟到|赶不上|赶车|误机": ("现代类", "现代人新增的高频梦境。00后出现概率是70后的5倍。反映对社交断联和信息焦虑的恐惧。"),
    }

    # 用户最关心的5个问题
    COMMON_CONCERNS = [
        "这个梦是吉是凶？预示着好事还是坏事？",
        "这个梦和我最近的生活有什么关系？",
        "反复做同样的梦说明什么？",
        "梦到的人和现实中的人有关系吗？",
        "这个梦会不会真的发生？怎么化解不好的梦？",
    ]

    def analyze(
        self,
        dream_text: str,
        retriever,
        api_key: str = "",
        user_context: str = "",
        bazi_info: dict = None,
    ) -> DreamResult:
        # 1. 识别高频梦境类型
        dream_type, type_hint = self._match_top_ten(dream_text)

        # 2. 关键词提取
        keywords = self._extract_keywords(dream_text)

        # 3. 策略化RAG搜索
        seen_texts = set()
        all_results = []

        # 策略A: 高频梦境专用搜索
        if dream_type:
            for r in retriever.search(f"梦见 {keywords[0] if keywords else dream_type}", top_k=5):
                if r.text not in seen_texts:
                    seen_texts.add(r.text)
                    all_results.append(r)

        # 策略B: 关键词逐一搜索
        for kw in keywords[:8]:
            for q in [f"梦见 {kw}", f"{kw} 梦"]:
                for r in retriever.search(q, top_k=3):
                    if r.text not in seen_texts:
                        seen_texts.add(r.text)
                        all_results.append(r)

        # 策略C: 用户处境搜索
        if user_context:
            ctx_kw = self._extract_keywords(user_context)
            for kw in ctx_kw[:5]:
                for r in retriever.search(f"梦 {kw}", top_k=2):
                    if r.text not in seen_texts:
                        seen_texts.add(r.text)
                        all_results.append(r)

        all_results.sort(key=lambda r: r.score, reverse=True)
        sources = list(set(r.source for r in all_results[:15]))

        return DreamResult(
            original_text=dream_text,
            dream_type=dream_type,
            keywords=keywords,
            interpretations=[r.text for r in all_results[:15]],
            source="、".join(sources) if sources else "",
        )

    def _match_top_ten(self, text: str) -> tuple:
        """匹配TOP10高频梦境模式"""
        for pattern, (dtype, hint) in self.TOP_TEN_PATTERNS.items():
            if re.search(pattern, text):
                return dtype, hint
        return "", ""

    def _extract_keywords(self, text: str) -> List[str]:
        try:
            import jieba.posseg as pseg
            words = pseg.lcut(text)
            return [w for w, p in words if p.startswith(('n','v','a')) and len(w) >= 2][:12]
        except ImportError:
            return re.findall(r'[一-鿿]{2,}', text)[:12]


def format_dream_prompt(
    dream_text: str,
    dream_result: DreamResult,
    user_context: str = "",
    bazi_info: dict = None,
) -> str:
    """构建完整解梦Prompt — 覆盖用户关心的5大问题"""

    parts = ["## 解梦请求\n"]

    # 梦境描述
    parts.append(f"### 完整梦境\n{dream_text}")

    # 高频梦境类型提示
    if dream_result.dream_type:
        type_hint = DreamEngine.TOP_TEN_PATTERNS.get(
            dream_result.keywords[0] if dream_result.keywords else "", ("", ""))
        if not type_hint[0]:
            for pat, (dt, hint) in DreamEngine.TOP_TEN_PATTERNS.items():
                if re.search(pat, dream_text):
                    type_hint = (dt, hint)
                    break
        if type_hint[0]:
            parts.append(f"\n### 梦境类型\n{type_hint[0]}类高频梦境")

    # 用户当前处境
    if user_context:
        parts.append(f"\n### 做梦者处境\n{user_context}")

    # 八字
    if bazi_info:
        parts.append(f"\n### 命理信息\n{bazi_info.get('day_master','')} {bazi_info.get('current_dayun','')}")

    # 古籍参考
    if dream_result.interpretations:
        parts.append(f"\n### 古籍参考 ({len(dream_result.interpretations)}条)")
        for i, text in enumerate(dream_result.interpretations[:8], 1):
            parts.append(f"{i}. {text[:250]}")

    # 用户最关心的5个问题（引导LLM回答）
    parts.append(f"""
### 请从以下维度综合分析（覆盖用户最关心的5个问题）：

1. **吉凶判断**：这个梦是吉是凶？预示着什么？有没有需要注意的征兆？
2. **现实关联**：这个梦和你当前的生活处境有什么联系？
3. **反复性**：如果反复梦到，说明了什么？需要注意什么？
4. **人物象征**：梦中的人物（如果有）代表什么？
5. **化解建议**：如果是不好的梦，有什么化解方法？好的梦怎么把握？

请用亲切、专业的口吻回复，像一位有智慧的老先生在和年轻人聊天。
结合古籍依据，但要给出切实可行的现实建议。
字数：500-800字。""")

    return "\n".join(parts)
