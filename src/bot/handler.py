"""消息处理 - 意图识别和信息收集."""
import re
from typing import Optional, Tuple

from src.engines.bazi import BaziEngine, BaziResult
from src.rag.retriever import Retriever, ChunkResult
from src.llm.client import FortuneLLM, AnalysisResult

# Task 6 (storage/dao.py) may not exist yet — make import mock-friendly
try:
    from src.storage.dao import UserDAO
except ImportError:

    class UserDAO:  # type: ignore
        """Stub: replace with real UserDAO when Task 6 is implemented."""

        def get_user_bazi(self, user_id: str) -> Optional[dict]:
            return None

        def save_user_bazi(self, user_id: str, data: dict) -> None:
            pass

        def save_consultation(self, user_id: str, question: str, result) -> None:
            pass


from .formatter import split_long_message, format_error, format_loading

INTENT_KEYWORDS = {
    "bazi": ["八字", "命理", "命格", "运势", "算命", "排盘", "四柱"],
    "ziwei": ["紫微", "斗数", "十二宫"],
    "liuyao": ["六爻", "卦", "占卜", "起卦"],
    "fengshui": ["风水", "阳宅", "阴宅", "家居", "布局", "房间"],
    "zeri": ["择日", "吉日", "搬家", "开业", "结婚日子", "选日子"],
    "mianxiang": ["面相", "手相", "看相"],
    "qimen": ["奇门", "遁甲"],
    "xingming": ["名字", "起名", "姓名", "改名"],
    "hehun": ["合婚", "配对", "配不配", "婚姻匹配"],
}

# 八字信息提取
BAZI_EXTRACT_PATTERNS = [
    # 1990年5月20日 15点 北京 男
    r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日.*?(\d{1,2})\s*[点时:：]\s*(\d{0,2}).*?([男女])',
    # 1990-05-20 15:00 北京 男
    r'(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2}).*?([男女])',
]


class MessageHandler:
    """消息处理器"""

    def __init__(self, engine: BaziEngine, retriever: Retriever, llm: FortuneLLM, dao: UserDAO):
        self.engine = engine
        self.retriever = retriever
        self.llm = llm
        self.dao = dao

    def process(self, message: str, user_id: str) -> str:
        """处理用户消息，返回回复"""
        msg = message.strip()

        # Step 1: 意图识别
        intent = self._detect_intent(msg)

        if intent is None:
            return self._help_message()

        # Step 2: 信息收集 (八字需要出生信息)
        if intent == "bazi":
            return self._handle_bazi(msg, user_id)

        return f"🔧 {intent} 模块开发中，敬请期待！"

    def _detect_intent(self, msg: str) -> Optional[str]:
        for intent, keywords in INTENT_KEYWORDS.items():
            for kw in keywords:
                if kw in msg:
                    return intent
        return None

    def _handle_bazi(self, msg: str, user_id: str) -> str:
        """处理八字请求"""
        parsed = self._extract_bazi_info(msg)

        if parsed is None:
            # 检查是否有已保存的信息
            saved = self.dao.get_user_bazi(user_id)
            if saved:
                return self._do_bazi_analysis(
                    saved["year"], saved["month"], saved["day"],
                    saved["hour"], saved["minute"], saved["city"],
                    saved["gender"], msg, user_id,
                )

            return """好的，请提供以下信息：
📅 出生日期：年/月/日（阳历还是阴历？）
⏰ 出生时间：几点几分
📍 出生地点：省份/城市
👤 性别：男/女

💡 示例：1990年5月20日 下午3点 北京 男"""

        year, month, day, hour, minute, city, gender = parsed
        return self._do_bazi_analysis(
            year, month, day, hour, minute, city, gender, msg, user_id,
        )

    COMMON_CITIES = {"北京", "上海", "广州", "深圳", "天津", "重庆", "杭州", "南京",
                     "成都", "武汉", "西安", "苏州", "长沙", "郑州", "青岛", "大连",
                     "厦门", "宁波", "福州", "合肥", "沈阳", "哈尔滨", "昆明", "贵阳",
                     "南宁", "海口", "兰州", "银川", "西宁", "拉萨", "乌鲁木齐",
                     "呼和浩特", "石家庄", "太原", "济南", "南昌"}

    def _extract_bazi_info(self, msg: str) -> Optional[Tuple]:
        """从消息中提取八字信息"""
        for pattern in BAZI_EXTRACT_PATTERNS:
            match = re.search(pattern, msg)
            if match:
                groups = match.groups()
                year = int(groups[0])
                month = int(groups[1])
                day = int(groups[2])
                hour = int(groups[3]) if groups[3] else 0
                minute = int(groups[4]) if len(groups) > 4 and groups[4] else 0
                gender = groups[-1]

                # 下午/晚上 → 24小时制转换
                if re.search(r'(下午|晚上|傍晚|夜间)', msg):
                    if 1 <= hour <= 12:
                        hour += 12
                elif re.search(r'^(上午|早上|早晨|凌晨)', msg):
                    pass  # 保持原样

                # 从消息中尝试提取城市
                city_match = re.search(r'([一-鿿]{2,4}(?:市|省))', msg)
                if city_match:
                    city = city_match.group(1)
                else:
                    # 后备：匹配已知城市名称（无后缀）
                    city_match = re.search(r'({})'.format('|'.join(self.COMMON_CITIES)), msg)
                    city = city_match.group(1) if city_match else "北京"
                return (year, month, day, hour, minute, city, gender)
        return None

    def _do_bazi_analysis(
        self, year, month, day, hour, minute, city, gender, question, user_id,
    ) -> str:
        """执行八字分析"""
        # 1. 排盘
        result = self.engine.calculate(year, month, day, hour, minute, city, gender)

        # 2. 保存用户数据
        self.dao.save_user_bazi(user_id, {
            "year": year, "month": month, "day": day,
            "hour": hour, "minute": minute,
            "city": city, "gender": gender,
            "bazi": result.bazi,
        })
        self.dao.save_consultation(user_id, question, result)

        # 3. 检索古籍
        search_query = f"{result.day_master} {question}"
        refs = self.retriever.search(search_query, category="bazi", top_k=15)

        # 4. LLM分析
        analysis = self.llm.analyze(result, refs, question)

        return analysis.response

    def _help_message(self) -> str:
        return """🔮 命理助手

我能帮您：
• 八字命理 — 看命格、运势、事业、婚姻
• 紫微斗数 — 十二宫详解
• 易经占卜 — 具体事情问卦
• 风水分析 — 家居布局指导
• 择日 — 婚嫁、开业吉日
• 面相手相 — 通过面相手相看运势性格
• 奇门遁甲 — 运筹决策、方位吉凶
• 姓名学 — 起名改名、姓名分析
• 合婚配对 — 婚姻匹配、缘分分析

💬 直接告诉我想算什么就行！
例如：「帮我看看八字 1990年5月20日15点 北京 男」"""
