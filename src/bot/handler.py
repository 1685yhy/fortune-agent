"""消息处理 - 意图识别和信息收集."""
import os
import re
from typing import Optional, Tuple, Dict, Any

from src.engines.bazi import BaziEngine, BaziResult
from src.engines.ziwei import ZiweiEngine, ZiweiResult
from src.engines.liuyao import LiuyaoEngine, LiuyaoResult
from src.engines.fengshui import FengshuiEngine, FengshuiResult
from src.engines.mianxiang import MianxiangEngine, MianxiangResult
from src.engines.zeri import ZeriEngine, ZeriResult
from src.engines.dream import DreamEngine, DreamResult
from src.engines.advisor import AdvisorEngine
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

        def save_consultation(self, user_id: str, question: str, result, intent: str = "bazi") -> None:
            pass


from src.storage.session_dao import SessionDAO
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
    "dream": ["解梦", "做梦", "梦见", "梦到", "梦"],
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

    def __init__(
        self,
        engine: BaziEngine,
        ziwei_engine: ZiweiEngine,
        liuyao_engine: LiuyaoEngine,
        fengshui_engine: FengshuiEngine,
        mianxiang_engine: MianxiangEngine,
        zeri_engine: ZeriEngine,
        retriever: Retriever,
        llm: FortuneLLM,
        dao: UserDAO,
        dream_engine: DreamEngine = None,
        session_dao: SessionDAO = None,
    ):
        self.engine = engine
        self.ziwei_engine = ziwei_engine
        self.liuyao_engine = liuyao_engine
        self.fengshui_engine = fengshui_engine
        self.mianxiang_engine = mianxiang_engine
        self.zeri_engine = zeri_engine
        self.dream_engine = dream_engine
        self.retriever = retriever
        self.llm = llm
        self.dao = dao
        self.session_dao = session_dao

    def process(self, message: str, user_id: str) -> str:
        """处理用户消息，返回回复"""
        msg = message.strip()

        # Step 1: 意图识别
        intent = self._detect_intent(msg)

        # Save user message to session history
        if self.session_dao:
            self.session_dao.add_message(user_id, "user", msg, intent=intent)

        if intent is None:
            reply = self._free_chat(msg, user_id)
            # Save bot reply to session
            if self.session_dao:
                self.session_dao.add_message(user_id, "assistant", reply)
            return reply

        # Step 2: 路由到对应处理器
        handler_map = {
            "bazi": self._handle_bazi,
            "ziwei": self._handle_ziwei,
            "liuyao": self._handle_liuyao,
            "fengshui": self._handle_fengshui,
            "mianxiang": self._handle_mianxiang,
            "zeri": self._handle_zeri,
            "qimen": self._handle_qimen,
            "xingming": self._handle_xingming,
            "hehun": self._handle_hehun,
            "dream": self._handle_dream,
        }

        handler = handler_map.get(intent)
        if handler:
            try:
                reply = handler(msg, user_id)
            except Exception as e:
                reply = f"⚠️ 服务暂时不可用：{str(e)[:100]}\n\n请稍后再试或换一种命理方式。"
        else:
            reply = f"🔧 {intent} 模块暂未开放，试试：\n• 八字命理\n• 紫微斗数\n• 易经占卜\n• 风水分析"

        # Save bot reply to session
        if self.session_dao:
            self.session_dao.add_message(user_id, "assistant", reply, intent=intent)

        return reply

    # ============================================================
    # Voice input support
    # ============================================================

    def _handle_voice(self, voice_text: str = "") -> str:
        """处理语音输入。

        如果 CoW（Claude on WeChat）提供了语音→文字转写，
        则直接通过正常意图检测流程处理。
        如果没有转写文本，说明需要 CoW 语音插件支持。
        """
        if voice_text:
            return self.process(voice_text, "")

        return "🎤 语音处理需要 CoW 语音插件支持。如果您正在使用微信，" \
               "请确保已安装 CoW 语音转文字插件。"

    # ============================================================
    # Image input support
    # ============================================================

    def _handle_image(self, image_url: str = "", user_text: str = "") -> str:
        """处理图片输入。

        根据图片 URL 和用户附带文字判断分析类型：
        - 户型/风水/家居关键词 → 风水分析
        - 面相/手相/看相关键词 → 面相分析
        - 其他 → 引导用户描述
        未来可接入 AI 视觉识别接口。
        """
        if not image_url:
            return "📷 请提供图片链接以便进行分析。"

        # 检查用户附带文字中的关键词
        if any(kw in user_text for kw in ["户型", "风水", "家居", "布局", "房间"]):
            return self._handle_image_fengshui(image_url, user_text)
        elif any(kw in user_text for kw in ["面相", "手相", "看相"]):
            return self._handle_image_mianxiang(image_url, user_text)
        else:
            return self._handle_image_generic(image_url, user_text)

    def _handle_image_fengshui(self, image_url: str, user_text: str) -> str:
        """处理户型/风水图片分析"""
        direction = self._extract_direction(user_text)
        base = f"📷 已收到户型图片：{image_url}\n\n"
        base += "🔮 风水分析（图片识别功能将在后续版本接入）：\n"

        if direction:
            result = self.fengshui_engine.analyze(
                direction=direction,
                year_built=None,
                birth_year=None,
                gender=None,
            )
            base += f"  坐向：{direction}\n"
            base += f"  宅卦：{result.house_gua}\n"
            base += f"  当前运：{result.period}运\n"
            base += f"  四吉方：{' '.join(f'{k}-{v}' for k, v in result.eight_mansions.items() if k in {'生气', '天医', '延年', '伏位'})}\n"
            base += f"  四凶方：{' '.join(f'{k}-{v}' for k, v in result.eight_mansions.items() if k in {'绝命', '五鬼', '六煞', '祸害'})}\n"
        else:
            base += "  ⚠️ 未在文字描述中识别到坐向信息，请补充坐向（如：坐北朝南）以获得更精准分析。\n"

        base += "\n💡 未来将支持 AI 视觉识别，可直接解读户型图。"
        return base

    def _handle_image_mianxiang(self, image_url: str, user_text: str) -> str:
        """处理面相/手相图片分析"""
        base = f"📷 已收到图片：{image_url}\n\n"
        base += "🔮 面相分析（图片识别功能将在后续版本接入）：\n"
        base += "  请描述您看到的面部特征，例如：\n"
        base += "  👤 脸型：方脸、圆脸、瓜子脸、长脸、国字脸等\n"
        base += "  👀 眼睛：大/小、有神/无神、单眼皮/双眼皮\n"
        base += "  👃 鼻子：高挺/塌陷、鼻头大小\n"
        base += "  👄 嘴唇：厚/薄、大小\n\n"
        base += "💡 未来将支持 AI 视觉识别，可直接解读照片。"
        return base

    def _handle_image_generic(self, image_url: str, user_text: str) -> str:
        """处理通用图片分析"""
        base = f"📷 已收到图片：{image_url}\n\n"
        base += "🔮 请告诉我您想通过这张图片了解什么？\n"
        base += "  • 户型风水分析（描述：户型、风水、家居）\n"
        base += "  • 面相/手相分析（描述：面相、手相、看相）\n\n"
        base += "💡 未来将支持 AI 视觉识别，可直接解读图片内容。"
        return base

    def _detect_intent(self, msg: str) -> Optional[str]:
        # 先检查是否包含出生日期信息（4位年份+月份+日期）
        if re.search(r'\d{4}\s*[年/-]\s*\d{1,2}\s*[月/-]\s*\d{1,2}', msg):
            return "bazi"

        for intent, keywords in INTENT_KEYWORDS.items():
            for kw in keywords:
                if kw in msg:
                    return intent
        return None

    # ============================================================
    # 提取辅助方法
    # ============================================================

    def _extract_year_from_text(self, text: str) -> Optional[int]:
        """从文本中提取年份"""
        match = re.search(r'(\d{4})\s*年', text)
        return int(match.group(1)) if match else None

    def _extract_gender(self, text: str) -> Optional[str]:
        """从文本中提取性别"""
        if '男' in text:
            return "男"
        if '女' in text:
            return "女"
        return None

    def _extract_direction(self, text: str) -> Optional[str]:
        """提取风水方向/坐山信息"""
        # 坐北朝南 → 子(北)
        match = re.search(r'坐([东西南北东北西北东南西南])朝([东西南北东北西北东南西南])', text)
        if match:
            dir_map = {
                "北": "子", "南": "午", "东": "卯", "西": "酉",
                "东北": "艮", "西北": "乾", "东南": "巽", "西南": "坤",
            }
            return dir_map.get(match.group(1), match.group(1))
        # 子山午向
        match = re.search(r'([子午卯酉乾坤艮巽甲乙丙丁庚辛壬癸])山([子午卯酉乾坤艮巽甲乙丙丁庚辛壬癸])向', text)
        if match:
            return match.group(1)
        # 简单方向词
        for d in ["北", "南", "东", "西", "东北", "西北", "东南", "西南"]:
            if d in text:
                return d
        return None

    def _extract_date(self, text: str) -> Optional[Tuple[int, int, int]]:
        """从文本中提取日期 (年,月,日)"""
        match = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', text)
        if match:
            return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        match = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', text)
        if match:
            return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        return None

    def _extract_purpose(self, text: str) -> str:
        """提取择日用途"""
        purpose_keywords = [
            ("嫁娶", ["结婚", "嫁娶", "订婚", "婚"]),
            ("开业", ["开业", "开张", "开市", "开工"]),
            ("搬家", ["搬家", "入宅", "乔迁", "迁居"]),
            ("出行", ["出行", "旅游", "旅行", "出差"]),
            ("动土", ["动土", "建房", "破土", "奠基"]),
        ]
        for purpose, keywords in purpose_keywords:
            for kw in keywords:
                if kw in text:
                    return purpose
        return ""

    def _get_question_after_keywords(self, text: str, keywords: list) -> str:
        """去除关键词后获取用户实际提问"""
        cleaned = text
        for kw in keywords:
            cleaned = cleaned.replace(kw, "")
        return cleaned.strip()

    # ============================================================
    # 八字 (Bazi) - existing flow preserved exactly
    # ============================================================

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

        # 2. 秒回安抚（在LLM分析前生成，最终拼接到回复开头）
        instant_reply = self._gen_instant_reply(result)

        # 3. 保存用户数据
        self.dao.save_user_bazi(user_id, {
            "year": year, "month": month, "day": day,
            "hour": hour, "minute": minute,
            "city": city, "gender": gender,
            "bazi": result.bazi,
        })
        self.dao.save_consultation(user_id, question, result)

        # 4. 检索古籍
        search_query = f"{result.day_master} {question}"
        refs = self.retriever.search(search_query, category="bazi", top_k=15)

        # 5. LLM分析
        analysis = self.llm.analyze(result, refs, question)

        # 6. 生成命盘图片
        chart_url = ""
        try:
            from src.images.bazi_chart import BaziChartGenerator
            gen = BaziChartGenerator()
            chart_path = gen.generate(result)
            filename = os.path.basename(chart_path)
            chart_url = f"http://124.221.233.214/charts/{filename}"
        except Exception as e:
            import traceback
            import logging
            logging.getLogger(__name__).warning(f"Chart generation failed: {e}\n{traceback.format_exc()}")

        # 7. 行动建议
        advice_section = ""
        try:
            advisor = AdvisorEngine()
            advice = advisor.generate_advice(result)
            has_advice = any(items for items in advice.values())
            if has_advice:
                lines = ["\n\n📌 行动建议"]
                icons = {"事业": "💼", "财运": "💰", "感情": "❤️", "健康": "🏥", "个人成长": "🌱"}
                for cat in ["事业", "财运", "感情", "健康", "个人成长"]:
                    items = advice.get(cat, [])
                    if items:
                        joined = "；".join(items[:2])
                        lines.append(f"{icons.get(cat, '')} {cat}：{joined}")
                advice_section = "\n".join(lines)
        except Exception:
            pass

        # 8. 组合回复
        reply = analysis.response
        if advice_section:
            reply += advice_section
        if chart_url:
            reply += f"\n\n📊 命盘图片：{chart_url}"
        if instant_reply:
            reply = instant_reply + "\n\n---\n\n" + reply
        return reply

    # ------------------------------------------------------------
    # 秒回安抚 (Instant Emotional Reply)
    # ------------------------------------------------------------

    def _gen_instant_reply(self, result) -> str:
        """生成即时情绪安抚回复——在LLM深度分析前先给用户一个暖心的回应。

        格式：
        [秒回] 小友，你的八字排出来了，命盘是【...】，日主为...。
        老夫正在仔细推演你的大运流年，请容我片刻。
        先告诉你一个好消息：...。
        """
        try:
            bazi = getattr(result, "bazi", None)
            if not isinstance(bazi, (list, tuple)) or len(bazi) < 4:
                return ""
            bazi_str = " ".join(str(p) for p in bazi[:4])

            dm = getattr(result, "day_master", "")
            if not isinstance(dm, str) or not dm:
                return ""

            # 找一个积极信息
            positive = ""

            # 神煞 → 天乙贵人优先
            shensha = getattr(result, "shensha", None)
            if isinstance(shensha, (list, tuple)):
                if "天乙贵人" in shensha:
                    positive = "你命带天乙贵人，这一生逢凶化吉，总有人助"

            # 格局
            if not positive:
                geju = getattr(result, "geju", "")
                if isinstance(geju, str) and geju and geju != "普通格":
                    positive = f"你是{geju}，命格不凡，前途光明"

            # 五行最强项
            if not positive:
                wuxing = getattr(result, "wuxing", None)
                if isinstance(wuxing, dict) and wuxing:
                    strongest = max(wuxing, key=lambda k: wuxing.get(k, 0))  # type: ignore
                    count = wuxing.get(strongest, 0)
                    if count >= 2:
                        positive = f"你五行{strongest}气较旺，这是你的核心优势"

            if not positive:
                positive = "你的命格别有洞天，待老夫细细道来"

            return (
                f"[秒回] 小友，你的八字排出来了，"
                f"命盘是【{bazi_str}】，日主为{dm}。"
                f"老夫正在仔细推演你的大运流年，请容我片刻。"
                f"先告诉你一个好消息：{positive}。🍵"
            )
        except Exception:
            return ""

    # ============================================================
    # 紫微斗数 (Ziwei)
    # ============================================================

    def _handle_ziwei(self, msg: str, user_id: str) -> str:
        """处理紫微斗数请求 - 与八字相同的信息收集"""
        parsed = self._extract_bazi_info(msg)

        if parsed is None:
            saved = self.dao.get_user_bazi(user_id)
            if saved:
                return self._do_ziwei_analysis(
                    saved["year"], saved["month"], saved["day"],
                    saved["hour"], saved["minute"], saved["city"],
                    saved["gender"], msg, user_id,
                )

            return """好的，请提供出生信息排紫微斗数命盘：
📅 出生日期：年/月/日
⏰ 出生时间：几点几分
📍 出生地点：省份/城市
👤 性别：男/女

💡 示例：1990年5月20日 下午3点 北京 男"""

        year, month, day, hour, minute, city, gender = parsed
        return self._do_ziwei_analysis(
            year, month, day, hour, minute, city, gender, msg, user_id,
        )

    def _do_ziwei_analysis(
        self, year, month, day, hour, minute, city, gender, question, user_id,
    ) -> str:
        """执行紫微斗数分析"""
        try:
            result = self.ziwei_engine.calculate(year, month, day, hour, minute, city, gender)
            self.dao.save_consultation(user_id, question, result, intent="ziwei")
            search_query = f"紫微斗数 {result.ming_gong} {question}"
            refs = self.retriever.search(search_query, category="ziwei", top_k=15)
            if not refs:
                refs = self.retriever.search(search_query, top_k=15)  # fallback: any category
            chart_str = self._format_ziwei_chart(result)
            analysis = self.llm.analyze(chart_str, refs, question)

            # 生成紫微斗数命盘图片
            chart_url = ""
            try:
                from src.images.ziwei_chart_html import ZiweiChartHTML
                gen = ZiweiChartHTML()
                chart_path = gen.generate(result)
                filename = os.path.basename(chart_path)
                chart_url = f"http://124.221.233.214/charts/{filename}"
            except Exception as e:
                import traceback
                import logging
                logging.getLogger(__name__).warning(f"Ziwei chart generation failed: {e}\n{traceback.format_exc()}")

            reply = analysis.response
            if chart_url:
                reply += f"\n\n📊 命盘图片：{chart_url}"
            return reply
        except Exception as e:
            return f"⚠️ 紫微斗数排盘暂时不可用：{str(e)[:100]}\n\n请稍后重试或改用八字分析。"

    def _format_ziwei_chart(self, r: ZiweiResult) -> str:
        """格式化紫微斗数命盘为文本"""
        lines = ["紫微斗数命盘："]
        lines.append(f"命宫：{r.ming_gong}  身宫：{r.shen_gong}  五行局：{r.wuxing_ju}")

        sihua = '  '.join(f"{k}:{v}" for k, v in r.sihua.items() if v)
        lines.append(f"四化：{sihua}")

        for name in ["命宫", "兄弟", "夫妻", "子女", "财帛", "疾厄",
                      "迁移", "交友", "官禄", "田宅", "福德", "父母"]:
            info = r.palaces.get(name)
            if info:
                stars = '、'.join(info.stars) if info.stars else '无主星'
                aux = '、'.join(info.aux_stars) if info.aux_stars else ''
                line = f"{name}({info.dizhi})：{stars}"
                if aux:
                    line += f"  辅星：{aux}"
                lines.append(line)

        dayun_items = [f"{age}岁-{palace}" for age, palace, dz in r.dayun[:8]]
        lines.append(f"大限：{' → '.join(dayun_items)}")

        return '\n'.join(lines)

    # ============================================================
    # 六爻 (Liuyao)
    # ============================================================

    def _handle_liuyao(self, msg: str, user_id: str) -> str:
        """处理六爻占卜请求"""
        # 提取所问之事
        question = self._get_question_after_keywords(msg, [
            "六爻", "占卜", "起卦", "起一卦", "卜卦", "算卦", "看看", "帮我",
        ])
        if not question:
            question = "一般运势"
        return self._do_liuyao_analysis(question, msg, user_id)

    def _do_liuyao_analysis(self, question: str, original_msg: str, user_id: str) -> str:
        """执行六爻占卜"""
        try:
            result = self.liuyao_engine.cast(method="random", question=question)
            self.dao.save_consultation(user_id, original_msg, result, intent="liuyao")
            refs = self.retriever.search(
                f"六爻 {result.original_hexagram} {question}", category="yijing", top_k=15)
            if not refs:
                refs = self.retriever.search(f"六爻 {question}", top_k=15)
            chart_str = self._format_liuyao_chart(result)
            analysis = self.llm.analyze(chart_str, refs, question)
            return analysis.response
        except Exception as e:
            return f"⚠️ 六爻起卦暂时不可用：{str(e)[:100]}\n\n请稍后重试。"

    def _format_liuyao_chart(self, r: LiuyaoResult) -> str:
        """格式化六爻占卜结果为文本"""
        lines = ["六爻占卜结果："]
        lines.append(f"所问之事：{r.question or '一般运势'}")
        lines.append(f"本卦：{r.original_hexagram}")
        if r.changed_hexagram and r.changed_hexagram != r.original_hexagram:
            lines.append(f"变卦：{r.changed_hexagram}")
        lines.append(f"所属宫：{r.palace}({r.palace_wuxing})")
        if r.changing_lines:
            lines.append(f"动爻：{', '.join(f'第{i+1}爻' for i in r.changing_lines)}")
        else:
            lines.append("静卦（无动爻）")

        # 六爻详情
        yao_names = ["初爻", "二爻", "三爻", "四爻", "五爻", "上爻"]
        lines.append("\n六爻详情：")
        for i, line_info in enumerate(r.lines):
            yao_type = line_info.get("yao_type", "")
            yao_label = f"{yao_type}" if yao_type else ""
            lines.append(
                f"  {yao_names[i]}{yao_label}：{line_info['type']} "
                f"{line_info['liuqin']} 地支{line_info['dizhi']}"
            )

        return '\n'.join(lines)

    # ============================================================
    # 风水 (Fengshui)
    # ============================================================

    def _handle_fengshui(self, msg: str, user_id: str) -> str:
        """处理风水分析请求"""
        direction = self._extract_direction(msg)
        birth_year = self._extract_year_from_text(msg)
        gender = self._extract_gender(msg)

        if not direction:
            return """请告诉我您房子的坐向（朝向）：

📍 示例1：坐北朝南（子山午向）
📍 示例2：朝东的房子
📍 示例3：西北朝向

可选信息（更精准分析）：
📅 房子建于哪一年？
👤 您的出生年份和性别（用于命卦计算）"""

        return self._do_fengshui_analysis(direction, birth_year, gender, msg, user_id)

    def _do_fengshui_analysis(
        self, direction, birth_year, gender, question, user_id,
    ) -> str:
        """执行风水分析"""
        # 1. 分析
        result = self.fengshui_engine.analyze(
            direction=direction,
            year_built=birth_year,
            birth_year=birth_year,
            gender=gender,
        )

        # 2. 保存
        self.dao.save_consultation(user_id, question, result, intent="fengshui")

        # 3. 检索古籍
        search_query = f"风水 {result.house_gua} {question}"
        refs = self.retriever.search(search_query, category="fengshui", top_k=15)

        # 4. LLM分析（带错误处理）
        try:
            chart_str = self._format_fengshui_chart(result)
            analysis = self.llm.analyze(chart_str, refs, question)

            # 生成风水九宫飞星图
            chart_url = ""
            try:
                from src.images.fengshui_chart_html import FengshuiChartHTML
                gen = FengshuiChartHTML()
                chart_path = gen.generate(result)
                filename = os.path.basename(chart_path)
                chart_url = f"http://124.221.233.214/charts/{filename}"
            except Exception as e:
                import traceback
                import logging
                logging.getLogger(__name__).warning(f"Fengshui chart generation failed: {e}\n{traceback.format_exc()}")

            reply = analysis.response
            if chart_url:
                reply += f"\n\n📊 风水九宫图：{chart_url}"
            return reply
        except Exception as e:
            return f"⚠️ 风水分析暂时不可用：{str(e)[:100]}\n\n请稍后重试。"

    def _format_fengshui_chart(self, r: FengshuiResult) -> str:
        """格式化风水分析结果为文本"""
        lines = ["风水分析结果："]
        lines.append(f"宅卦：{r.house_gua}  当前运：{r.period}运")
        if r.person_gua:
            lines.append(f"命卦：{r.person_gua}")

        # 八宅吉凶
        auspicious = {k: v for k, v in r.eight_mansions.items()
                      if k in {"生气", "天医", "延年", "伏位"}}
        inauspicious = {k: v for k, v in r.eight_mansions.items()
                        if k in {"绝命", "五鬼", "六煞", "祸害"}}
        lines.append(f"四吉方：{' '.join(f'{k}-{v}' for k, v in auspicious.items())}")
        lines.append(f"四凶方：{' '.join(f'{k}-{v}' for k, v in inauspicious.items())}")

        # 飞星表
        lines.append("\n玄空飞星（九宫）：")
        palace_cn = {"坎": "北", "坤": "西南", "震": "东", "巽": "东南",
                     "中": "中", "乾": "西北", "兑": "西", "艮": "东北", "离": "南"}
        for palace in ["坎", "坤", "震", "巽", "中", "乾", "兑", "艮", "离"]:
            if palace in r.flying_stars:
                fs = r.flying_stars[palace]
                lines.append(f"  {palace_cn.get(palace, palace)}：运{fs['运星']} 山{fs['山星']} 向{fs['向星']}")

        return '\n'.join(lines)

    # ============================================================
    # 面相手相 (Mianxiang)
    # ============================================================

    def _handle_mianxiang(self, msg: str, user_id: str) -> str:
        """处理面相分析请求"""
        description = self._get_question_after_keywords(msg, [
            "面相", "手相", "看相", "看看", "帮我看看",
        ])

        if len(description) <= 3:
            return """请描述您的面部/手部特征，例如：

👤 脸型：方脸、圆脸、瓜子脸、长脸、国字脸等
👀 眼睛：大/小、有神/无神、单眼皮/双眼皮
👃 鼻子：高挺/塌陷、鼻头大小
👄 嘴唇：厚/薄、大小
💡 示例：方脸，额头饱满，眼睛大而有神，鼻梁高挺，嘴唇厚实"""

        return self._do_mianxiang_analysis(description, msg, user_id)

    def _do_mianxiang_analysis(self, description: str, original_msg: str, user_id: str) -> str:
        """执行面相分析"""
        # 1. 面相分析
        result = self.mianxiang_engine.analyze(description=description)

        # 2. 保存
        self.dao.save_consultation(user_id, original_msg, result, intent="mianxiang")

        # 3. 检索古籍
        refs = self.retriever.search(
            f"面相 {result.face_type} {description}",
            category="mianxiang", top_k=15,
        )

        # 4. LLM分析
        chart_str = self._format_mianxiang_chart(result)
        analysis = self.llm.analyze(chart_str, refs, original_msg)

        return analysis.response

    def _format_mianxiang_chart(self, r: MianxiangResult) -> str:
        """格式化面相分析结果为文本"""
        lines = ["面相分析结果："]
        lines.append(f"脸型：{r.face_type}")

        zones = '  '.join(f"{k}{v}" for k, v in r.three_zones.items())
        lines.append(f"三停：{zones}")

        for name, val in r.five_mountains.items():
            short = name.split('(')[0]
            lines.append(f"  {short}：{val}")

        for name, val in r.features.items():
            short = name.split('(')[0]
            lines.append(f"  {short}：{val}")

        lines.append(f"\n综合评定：{r.overall[:200]}")
        return '\n'.join(lines)

    # ============================================================
    # 择日 (Zeri)
    # ============================================================

    def _handle_zeri(self, msg: str, user_id: str) -> str:
        """处理择日请求"""
        date_info = self._extract_date(msg)
        purpose = self._extract_purpose(msg)

        if not date_info:
            return """请告诉我您想查询的日期和用途：

📅 日期：哪一年哪一天？
🎯 用途：用于什么？

💡 示例1：2026年8月15日适合结婚吗？
💡 示例2：我要在2026年10月1日搬家，这天好吗？"""

        return self._do_zeri_analysis(date_info, purpose, msg, user_id)

    def _do_zeri_analysis(self, date_info, purpose, question, user_id) -> str:
        """执行择日分析"""
        year, month, day = date_info

        # 1. 择日
        result = self.zeri_engine.select(year, month, day, purpose=purpose)

        # 2. 保存
        self.dao.save_consultation(user_id, question, result, intent="zeri")

        # 3. 检索古籍
        search_query = f"择日 {result.jianchu} {purpose or '吉日'}"
        refs = self.retriever.search(search_query, category="zeri", top_k=15)

        # 4. LLM分析
        chart_str = self._format_zeri_chart(result, year, month, day)
        analysis = self.llm.analyze(chart_str, refs, question)

        return analysis.response

    def _format_zeri_chart(self, r: ZeriResult, year: int, month: int, day: int) -> str:
        """格式化择日结果为文本"""
        lines = ["择日分析结果："]
        lines.append(f"日期：{year}年{month}月{day}日")
        lines.append(f"建除十二神：{r.jianchu}")
        lines.append(f"二十八宿：{r.ershibaxiu}（{r.xiu_jixiong}）")
        lines.append(f"冲：{r.chong}")
        lines.append(f"宜：{'、'.join(r.yi)}")
        lines.append(f"忌：{'、'.join(r.ji)}")
        lines.append(f"综合判定：{r.overall}")
        return '\n'.join(lines)

    # ============================================================
    # 奇门遁甲 (Qimen) - RAG + LLM only, no dedicated engine
    # ============================================================

    def _handle_qimen(self, msg: str, user_id: str) -> str:
        """处理奇门遁甲咨询"""
        question = self._get_question_after_keywords(msg, [
            "奇门", "遁甲", "奇门遁甲", "看看", "帮我",
        ])
        if not question:
            question = "奇门遁甲运筹"

        # 1. 检索古籍
        refs = self.retriever.search(f"奇门遁甲 {question}", category="qimen", top_k=15)

        # 2. LLM分析（无排盘数据）
        chart_str = f"奇门遁甲咨询：{question}"
        analysis = self.llm.analyze(chart_str, refs, question)

        return analysis.response

    # ============================================================
    # 姓名学 (Xingming) - RAG + LLM only, no dedicated engine
    # ============================================================

    def _handle_xingming(self, msg: str, user_id: str) -> str:
        """处理姓名学咨询"""
        question = self._get_question_after_keywords(msg, [
            "起名", "改名", "名字", "姓名", "看看", "帮我",
        ])

        if not question or len(question) < 2:
            return """请告诉我您想分析的名字或起名需求：

💡 示例1：分析"张伟"这个名字好不好
💡 示例2：给2026年出生的龙宝宝起名，姓王
💡 示例3：想改名字，有什么建议"""

        # 提取名字（从消息中提取连续2-4个中文字符作为名字候选）
        name_match = re.search(r'[""「『]([一-鿿]{2,4})[""」』]', msg)
        if not name_match:
            name_match = re.search(r'分析?([一-鿿]{2,4})', msg)
        name = name_match.group(1) if name_match else ""

        # 1. 检索古籍
        refs = self.retriever.search(f"姓名学 {name} {question}", category="xingming", top_k=15)

        # 2. LLM分析
        chart_str = f"姓名咨询：{name} 问题：{question}"
        analysis = self.llm.analyze(chart_str, refs, question)

        return analysis.response

    # ============================================================
    # 合婚配对 (Hehun) - RAG + LLM only, no dedicated engine
    # ============================================================

    def _handle_hehun(self, msg: str, user_id: str) -> str:
        """处理合婚配对咨询"""
        question = self._get_question_after_keywords(msg, [
            "合婚", "配对", "婚姻匹配", "配不配", "看看", "帮我",
        ])

        if not question or len(question) < 2:
            return """请告诉我双方的信息来进行配对分析：

💡 示例1：男1990年属马，女1993年属鸡，配不配？
💡 示例2：男 1990年5月20日 北京，女 1992年8月15日 上海"""

        # 1. 检索古籍
        refs = self.retriever.search(f"合婚配对 {question}", category="hehun", top_k=15)

        # 2. 尝试提取双方八字信息
        info_a = self._extract_bazi_info(msg)
        info_b = None
        remainder = msg
        if info_a:
            # Try to find second person's info
            match = re.search(r'男.*?([\d年月日时分点\s男女]{5,})', msg)
            if match:
                remainder = msg.replace(match.group(0), '', 1)
            remaining_bazi = self._extract_bazi_info(remainder)
            if remaining_bazi:
                info_b = remaining_bazi

        chart_str = f"合婚配对咨询：{question}"
        if info_a:
            chart_str += f"\n第一人：{info_a[0]}年{info_a[1]}月{info_a[2]}日 {info_a[3]}时 {info_a[5]} {info_a[6]}"
        if info_b:
            chart_str += f"\n第二人：{info_b[0]}年{info_b[1]}月{info_b[2]}日 {info_b[3]}时 {info_b[5]} {info_b[6]}"

        analysis = self.llm.analyze(chart_str, refs, question)

        return analysis.response

    # ============================================================
    # 解梦 (Dream) - engine + RAG + LLM
    # ============================================================

    def _handle_dream(self, msg: str, user_id: str) -> str:
        """处理解梦请求 - 提取完整梦境 + 处境"""
        # 去掉触发词，保留完整描述
        for kw in ["帮我解梦", "解梦", "做梦"]:
            msg = msg.replace(kw, "", 1)
        dream_text = msg.strip()

        if not dream_text or len(dream_text) < 2:
            return """🌙 请描述您的梦境，我来为您解梦：

您可以详细说说：
• 梦里发生了什么？
• 梦里有什么情绪和感觉？
• 最近有什么特别担心或关注的事情吗？

💡 例如：「梦见一条大蟒蛇在追我，我很害怕，最近工作压力大，老板总刁难我」"""

        return self._do_dream_analysis(dream_text, user_id)

    def _do_dream_analysis(self, dream_text: str, user_id: str) -> str:
        """执行解梦分析 - 完整上下文"""
        # 1. 分离梦境描述 和 用户处境
        user_context = ""
        context_keywords = ["最近", "因为", "担心", "害怕", "焦虑", "压力", "工作", "感情", "家庭", "钱", "身体",
                            "老板", "同事", "朋友", "父母", "老公", "老婆", "男朋友", "女朋友", "孩子"]
        for kw in context_keywords:
            idx = dream_text.find(kw)
            if idx > 5:  # 关键词在文本后半部分，可能是处境描述
                user_context = dream_text[idx:]
                dream_text = dream_text[:idx].strip()
                break

        # 2. 获取用户八字
        bazi_info = None
        try:
            saved = self.dao.get_user_bazi(user_id)
            if saved:
                bazi_info = {"day_master": f"{saved.get('bazi', ['?'])[2]}", "current_dayun": "当前"}
        except Exception:
            pass

        # 3. 引擎分析 + RAG检索
        api_key = getattr(self.llm, 'api_key', '') if self.llm else ''
        if self.dream_engine:
            result = self.dream_engine.analyze(dream_text, self.retriever, api_key, user_context, bazi_info)
        else:
            result = DreamResult()

        self.dao.save_consultation(user_id, dream_text, result, intent="dream")

        # 4. 构建完整 Prompt 并调用 LLM
        from src.engines.dream import format_dream_prompt
        prompt = format_dream_prompt(dream_text, result, user_context, bazi_info)
        analysis = self.llm.analyze(prompt, result.interpretations, dream_text)

        # 5. 组合回复
        return self._format_dream_response(dream_text, result, analysis.response)

    def _get_dream_text(self, msg: str) -> str:
        """从消息中提取梦境描述"""
        for kw in ["解梦", "做梦", "梦见", "梦到", "梦"]:
            msg = msg.replace(kw, "", 1)
        return msg.strip()
        for kw in keywords:
            cleaned = cleaned.replace(kw, "")
        cleaned = cleaned.strip()
        # If nothing remains, the whole message might be the dream description
        if not cleaned:
            cleaned = msg
        return cleaned

    def _format_dream_for_llm(self, result: DreamResult, dream_text: str) -> str:
        """格式化解梦信息供LLM分析"""
        lines = ["周公解梦分析："]
        lines.append(f"梦境：{dream_text}")
        if result.symbols:
            lines.append(f"核心象征：{'、'.join(result.symbols)}")
        if result.emotions:
            lines.append(f"情绪基调：{result.emotions}")
        if result.interpretations:
            lines.append("古籍参考：")
            for i, interp in enumerate(result.interpretations[:5], 1):
                lines.append(f"  {i}. {interp[:300]}")
        return '\n'.join(lines)

    def _format_dream_response(self, dream_text: str, result: DreamResult, llm_analysis: str) -> str:
        """格式化最终回复"""
        reply = "🌙 周公解梦\n\n"
        reply += f"您梦见了：{dream_text}\n"

        if result.interpretations:
            reply += "\n📖 古籍记载：\n"
            for i, interp in enumerate(result.interpretations[:3], 1):
                reply += f"  {i}. {interp[:200]}\n"

        reply += f"\n🔮 AI解读：\n{llm_analysis}"
        return reply

    # ============================================================
    # 帮助信息
    # ============================================================

    def _conversational_chat(self, history: list, user_id: str) -> str:
        """多轮对话 - 带完整上下文的自然聊天。

        优先使用 API 调用方传递的显式历史（history 参数），
        若历史较短（仅当前消息），则补充会话存储中的历史记录。
        """
        # 提取当前用户消息
        current_msg = ""
        for m in history:
            if m["role"] == "user":
                current_msg = m["content"]

        # 如果只有一条消息且是问候，快速回复
        if len(history) <= 1:
            if current_msg.strip() in ('',' ','?','？'):
                return '您好！我是易理明灯AI命理顾问。直接告诉我您的出生日期，我帮您看八字。'
            if re.search(r'\d{4}', current_msg) or re.search(r'[男女]', current_msg):
                return '看起来您可能在提供出生信息。请按格式告诉我：\n📅 出生年月日\n⏰ 几点几分\n📍 出生城市\n👤 性别\n\n例如：1990年5月20日 下午3点 北京 男'

        # 构建传给 LLM 的历史消息
        llm_history = list(history)

        # 如果显式历史只有一条消息，尝试从会话存储中补充更早的上下文
        if len(history) <= 1 and self.session_dao:
            session_ctx = self.session_dao.get_context_for_llm(user_id, history_limit=15)
            if len(session_ctx) > len(history):
                # 用会话历史替换，但确保当前用户消息在最后
                llm_history = [m for m in session_ctx if m["role"] != "user" or m["content"] != current_msg]
                llm_history.append({"role": "user", "content": current_msg})

        # Save user message to session
        if self.session_dao and current_msg:
            self.session_dao.add_message(user_id, "user", current_msg)

        # 多轮对话：把历史消息传给 LLM
        try:
            reply = self.llm.chat_conversation(llm_history)
        except Exception:
            reply = '老夫在此，小友有何困惑尽管道来。'

        # Save assistant reply to session
        if self.session_dao:
            self.session_dao.add_message(user_id, "assistant", reply)

        return reply

    def _free_chat(self, msg: str, user_id: str) -> str:
        """自由对话：没有命中任何命理意图时，直接用 LLM 自然聊天。"""
        if msg.strip() in ('',' ','?','？'):
            return '您好！我是易理明灯AI命理顾问。直接告诉我您的出生日期，我帮您看八字。'

        # 如果消息含数字或年份，可能是用户尝试提供出生信息，引导一下
        if re.search(r'\d{4}', msg) or re.search(r'[男女]', msg):
            return '看起来您可能在提供出生信息。请按格式告诉我：\n📅 出生年月日（阳历/阴历）\n⏰ 几点几分\n📍 出生城市\n👤 性别\n\n例如：1990年5月20日 下午3点 北京 男'

        # 所有其他消息 → 用 LLM 自然对话
        try:
            if self.session_dao:
                # 加载最近对话历史，传给LLM以获得上下文感知的回复
                history = self.session_dao.get_context_for_llm(user_id, history_limit=15)
                if len(history) > 1:
                    return self.llm.chat_conversation(history)
            # 无历史或历史不足时，用单消息模式
            result = self.llm.chat(msg)
            return result.response
        except Exception:
            return '老夫在此，小友有何困惑尽管道来。若要看八字，请告知您的出生年月日时。'

def _get_welcome_message() -> str:
    return """🌟 欢迎来到「易理明灯」！
我是您的专属AI命理顾问，以《滴天髓》《三命通会》等古籍为依据，用现代技术为您解读传统命理。

━━━━━━━━━━━━━━━
🔮 我能帮您做什么？
━━━━━━━━━━━━━━━

🔹 **八字命理** — 看命格、事业、财运、婚姻
    试试：帮我看八字 1990年5月20日 下午3点 北京 男

🔹 **紫微斗数** — 十二宫详解、流年大限
    试试：帮我排紫微斗数

🔹 **易经占卜** — 具体事情问卦
    试试：帮我算个卦 这次跳槽能成吗

🔹 **风水分析** — 家居布局、坐向吉凶
    试试：我家大门朝南 帮我看看风水

🔹 **择日** — 婚嫁、开业、搬家吉日
    试试：帮我找个搬家的好日子 2026年8月

🔹 **面相手相** — 五官五行分析
    试试：帮我分析面相 我是圆脸

🔹 **奇门遁甲** — 运筹决策、方位吉凶
🔹 **姓名学** — 起名改名、姓名分析
🔹 **合婚配对** — 婚姻匹配、缘分分析
🔹 **周公解梦** — 梦境解析、预兆解读

━━━━━━━━━━━━━━━
💬 您也可以直接问我任何命理相关的问题，我会尽力为您解答！"""

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
• 周公解梦 — 梦境解析、预兆解读

💬 直接告诉我想算什么就行！
例如：「帮我看看八字 1990年5月20日15点 北京 男」"""
