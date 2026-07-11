"""消息处理 - 意图识别和信息收集."""
import re
from typing import Optional, Tuple, Dict, Any

from src.engines.bazi import BaziEngine, BaziResult
from src.engines.ziwei import ZiweiEngine, ZiweiResult
from src.engines.liuyao import LiuyaoEngine, LiuyaoResult
from src.engines.fengshui import FengshuiEngine, FengshuiResult
from src.engines.mianxiang import MianxiangEngine, MianxiangResult
from src.engines.zeri import ZeriEngine, ZeriResult
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
    ):
        self.engine = engine
        self.ziwei_engine = ziwei_engine
        self.liuyao_engine = liuyao_engine
        self.fengshui_engine = fengshui_engine
        self.mianxiang_engine = mianxiang_engine
        self.zeri_engine = zeri_engine
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
        }

        handler = handler_map.get(intent)
        if handler:
            return handler(msg, user_id)

        return f"🔧 {intent} 模块开发中，敬请期待！"

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
        # 1. 排盘
        result = self.ziwei_engine.calculate(year, month, day, hour, minute, city, gender)

        # 2. 保存
        self.dao.save_consultation(user_id, question, result, intent="ziwei")

        # 3. 检索古籍
        search_query = f"紫微斗数 {result.ming_gong} {question}"
        refs = self.retriever.search(search_query, category="ziwei", top_k=15)

        # 4. LLM分析
        chart_str = self._format_ziwei_chart(result)
        analysis = self.llm.analyze(chart_str, refs, question)

        return analysis.response

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
        # 1. 起卦
        result = self.liuyao_engine.cast(method="random", question=question)

        # 2. 保存
        self.dao.save_consultation(user_id, original_msg, result, intent="liuyao")

        # 3. 检索古籍
        refs = self.retriever.search(
            f"六爻 {result.original_hexagram} {question}",
            category="liuyao", top_k=15,
        )

        # 4. LLM分析
        chart_str = self._format_liuyao_chart(result)
        analysis = self.llm.analyze(chart_str, refs, question)

        return analysis.response

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

        # 4. LLM分析
        chart_str = self._format_fengshui_chart(result)
        analysis = self.llm.analyze(chart_str, refs, question)

        return analysis.response

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
    # 帮助信息
    # ============================================================

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
