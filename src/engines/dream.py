"""解梦引擎 v5 - 关键词提取修复 + 元素吉凶规则层 + 组合梦境拆分 (G4)

对外形状不变（接口契约零破坏，只加字段）：
- DreamResult：原字段不变，新增 symbols/emotions/elements/element_notes/luck_level/luck_reason
- DreamEngine.analyze(dream_text, retriever, api_key="", user_context="", bazi_info=None)
- format_dream_prompt(dream_text, dream_result, user_context="", bazi_info=None)

G4 三项修复（2026-08-29 佛滔对比实测取证，见 /tmp/dream_compare.md）：
① 关键词提取：单字意象词白名单 + 触发词「梦见」类排除 + 情绪词降权独立成 emotions
② 元素→释义→吉凶规则层：DREAM_ELEMENTS 元素库，引擎先算吉凶骨架注入 prompt
③ 组合梦境拆分：多元素分别检索合并 + 变体覆盖（补怀孕/孕妇模式）
"""
from dataclasses import dataclass, field
from typing import List, Tuple
import re


@dataclass
class DreamResult:
    original_text: str = ""
    dream_type: str = ""        # 高频梦境类型
    keywords: list = field(default_factory=list)
    interpretations: list = field(default_factory=list)
    source: str = ""
    # G4 新增字段（只加不删，旧消费点 getattr 安全）
    symbols: list = field(default_factory=list)       # 核心象征（元素库）
    emotions: list = field(default_factory=list)      # 情绪基调（情绪词提取，不参与意象检索）
    elements: list = field(default_factory=list)      # 命中的梦境元素（组合拆分依据）
    element_notes: list = field(default_factory=list)  # 每元素吉凶基线+依据（变体优先）
    luck_level: str = ""       # 综合吉凶骨架：大吉/吉/吉多于凶/凶多于吉/凶（空=未命中元素）
    luck_reason: str = ""      # 吉凶依据（古籍/佛滔判词锚点）


# ── 吉凶等级排序（保守合成用） ──────────────────────────────────────
DREAM_LUCK_RANK = {"大吉": 5, "吉": 4, "吉多于凶": 3, "凶多于吉": 2, "凶": 1}

# ── 梦境触发词/时间词黑名单：绝不进入关键词列表 ────────────────────────
# keywords[0] 恒为真实意象词；策略A 查询不再出现「梦见 梦见」垃圾
DREAM_TRIGGER_WORDS = {
    "梦见", "梦到", "做梦", "梦中", "梦境", "梦乡", "梦幻", "梦想", "解梦",
    "醒来", "惊醒", "睡醒", "睁眼", "醒",
    "昨晚", "夜里", "晚上", "今晚", "今早", "早晨", "清晨", "凌晨", "半夜",
    "深夜", "中午", "上午", "下午", "白天", "昨天", "今天", "明天", "后天",
    "前天", "今年", "明年", "去年", "现在", "如今", "当初", "曾经", "起初",
    "平时", "近来", "最近", "未来", "过去", "当时",
    "梦里", "心里", "家里", "身边", "眼前", "之前", "之后", "以前", "以后",
    "里面", "外面", "上面", "下面", "前面", "后面", "旁边", "附近", "周围",
    "途中",
}

# ── 口语疑问词：做梦者问句框架，非梦境意象，不进关键词 ──────────────────
DREAM_QUESTION_WORDS = {
    "是不是", "好不好", "要不要", "会不会", "能不能", "行不行",
    "可不可以", "有没有", "该不该",
    "意思", "啥意思", "为啥", "怎么回事",
}

# ── 情绪词：提取到 emotions 字段，禁止参与策略A/B 意象检索 ────────────
# 子串匹配（哭醒含哭、害怕含怕），在触发词黑名单之后判断
DREAM_EMOTION_WORDS = [
    "害怕", "怕", "哭", "哭醒", "哭喊", "大哭", "紧张", "焦虑", "恐慌",
    "恐惧", "惊吓", "吓", "担心", "担忧", "不安", "伤心", "难过", "生气",
    "愤怒", "烦躁", "着急", "慌张", "惊慌", "沮丧", "郁闷", "委屈", "心慌",
    "忐忑", "惊", "慌", "倒霉", "完蛋",
]

# ── 单字意象词白名单（梦境专属，宁缺毋滥，禁止通用单字） ──────────────
# len(w)>=2 过滤会丢掉「蛇/水/追」等核心单字意象；白名单单字保留
SINGLE_CHAR_DREAM_WORDS = {
    "蛇", "水", "追", "猫", "狗", "虎", "牙", "钱", "飞", "摔", "淹",
    "河", "海", "死", "鬼", "车", "路", "山", "火", "血", "孕",
    "雨", "雪", "鱼", "龙", "马", "牛", "羊", "猪", "鸡", "鸟", "狼",
    "象", "船", "桥", "树", "花", "金", "银", "刀", "剑", "枪", "龟",
    "蛙", "庙", "坟", "尸", "骨", "腿", "脚", "手", "头",
}


# ── 「梦境元素→释义→吉凶」规则层（对标佛滔量化吉凶体系） ──────────────
# 每元素：match 正则 / name 核心意象 / symbols 象征词 / luck 吉凶基线 /
#          baseline 依据（直接引古籍/佛滔判词）/ variants 常见变体
# 吉凶基线必须与佛滔同向（2026-08-29 实测判词锚点，见 /tmp/dream_compare.md §三）
DREAM_ELEMENTS = [
    {
        "name": "蛇",
        "match": r"蛇|蟒",
        "symbols": ["变化", "智慧", "欲望", "财运变动", "人际暗流", "健康隐忧", "内在力量"],
        "luck": "大吉",
        "baseline": "蛇主移徙事（敦煌梦书）；多数为吉兆，主财运与内在力量",
        "variants": [
            {"pattern": r"被蛇追|蛇追", "term": "被蛇追",
             "note": "被蛇追吉凶指数92【大吉昌】——多数吉兆，主财运变动，非灾非祸", "luck": "大吉"},
            {"pattern": r"蛇咬", "term": "蛇咬",
             "note": "蛇咬亦有吉兆之说，主钱财或子息之应", "luck": "吉"},
            {"pattern": r"蛇缠|缠蛇", "term": "蛇缠身",
             "note": "蛇缠身主贵人相助或心结自解", "luck": "吉"},
        ],
    },
    {
        "name": "水",
        "match": r"水|淹|洪|河|海|湖|雨",
        "symbols": ["财富", "情绪", "情感交流", "潜意识"],
        "luck": "大吉",
        "baseline": "发洪水者主进财（周公解梦）；大水者大富（敦煌梦书）——水主财运，清者吉财",
        "variants": [
            {"pattern": r"发大水|发洪水|洪水", "term": "发大水",
             "note": "发洪水者主进财（周公解梦）；大水者大富（敦煌梦书）", "luck": "大吉"},
        ],
    },
    {
        "name": "掉牙",
        "match": r"掉牙|掉牙齿|牙齿.{0,2}掉|牙.{0,2}脱|牙.{0,2}落|牙.{0,2}掉",
        "symbols": ["家人健康", "长辈", "损失", "分离", "健康警告", "决断力"],
        "luck": "吉多于凶",
        "baseline": "齿自落者父母凶（周公解梦），但掉牙整体为家人健康关注之兆，佛滔吉凶指数95【吉多于凶】——非凶兆，宜关注长辈健康",
        "variants": [
            {"pattern": r"掉牙.{0,6}接上|牙.{0,4}接上", "term": "掉牙又接上",
             "note": "掉牙又接上自有转机，主失而复得", "luck": "吉"},
            {"pattern": r"掉牙.{0,4}没血|牙.{0,4}没血", "term": "掉牙没血",
             "note": "掉牙没血主长辈无虞，关注健康即可", "luck": "吉多于凶"},
            {"pattern": r"牙.{0,4}掉光|牙齿全掉", "term": "牙齿掉光",
             "note": "牙齿全掉光主长辈健康需加倍关照", "luck": "凶多于吉"},
        ],
    },
    {
        "name": "考试",
        "match": r"考试|考题|高考|准考证|成绩|不及格|答卷|试卷|录取",
        "symbols": ["能力焦虑", "未完成情结", "自我怀疑", "压力"],
        "luck": "吉",
        "baseline": "梦见考试主吉，学习如鱼得水（佛滔判词体系）",
        "variants": [
            {"pattern": r"漏考|没考|没去考", "term": "漏考试",
             "note": "漏考试吉凶指数76【大吉昌】——主学业顺遂，焦虑反为吉兆", "luck": "大吉"},
            {"pattern": r"丢卷|丢试卷|卷子丢", "term": "丢卷子",
             "note": "丢卷子吉凶指数89【吉多于凶】", "luck": "吉多于凶"},
            {"pattern": r"没带准考证|忘带|准考证", "term": "考试没带准考证",
             "note": "考试没带准考证属漏考试一类，主吉多于凶，宜平常心", "luck": "吉多于凶"},
        ],
    },
    {
        "name": "被追",
        "match": r"被.{0,4}追|追杀|追赶|追我",
        "symbols": ["现实压力", "逃避心态", "未解决的问题"],
        "luck": "吉多于凶",
        "baseline": "被追反映现实压力来源与逃避心态（压力类）；被追到水里【吉多于凶】、被追游很快【大吉昌92】——压力可转化为机遇，关键在于面对",
        "variants": [
            {"pattern": r"被追.{0,4}水里|追到水里|追.{0,2}水里", "term": "被追到水里",
             "note": "被追到水里【吉多于凶】——困局中有转机", "luck": "吉多于凶"},
            {"pattern": r"被.{0,4}追.{0,4}跑不动|跑不动", "term": "被追跑不动",
             "note": "被追跑不动主现实阻力大，宜放慢脚步梳理压力", "luck": "凶多于吉"},
        ],
    },
    {
        "name": "亲人去世",
        "match": r"去世|死.{0,2}人|死人|棺材|坟墓|出殡|丧事|死掉",
        "symbols": ["长命百岁", "升官发财", "警示", "遗忘", "本能压抑"],
        "luck": "大吉",
        "baseline": "梦见亲人去世乃大吉之兆：主长命百岁、升官发财（佛滔判词体系）；死人去世吉凶指数76【大吉昌】",
        "variants": [
            {"pattern": r"亲人.{0,6}去世|去世.{0,4}亲人", "term": "亲人去世",
             "note": "梦见亲人去世乃大吉之兆——主长命百岁，或近期有升迁进财之喜", "luck": "大吉"},
            {"pattern": r"死人.{0,4}一起|亲人死人", "term": "亲人死人一起",
             "note": "亲人死人一起吉凶指数77【大吉昌】", "luck": "大吉"},
        ],
    },
    {
        "name": "怀孕",
        "match": r"怀孕|孕妇|大肚子|有喜|孕",
        "symbols": ["幸福", "物质财富", "新开始", "创造力"],
        "luck": "大吉",
        "baseline": "梦见自己怀孕，主幸福与物质财富增加（佛滔判词体系，吉凶指数99【大吉】）",
        "variants": [
            {"pattern": r"孕妇", "term": "孕妇",
             "note": "孕妇之梦主添丁进财，吉凶指数99【大吉】；组合梦境（孕妇+水等）另有专门预兆", "luck": "大吉"},
        ],
    },
    {
        "name": "飞",
        "match": r"飞|飞翔|翱翔|悬空|升空|起飞",
        "symbols": ["自由", "升迁", "事业突破", "解放", "追求理想"],
        "luck": "大吉",
        "baseline": "梦见自己飞翔主升迁与生意获利、位置升高（佛滔判词体系）；天飞翔吉凶指数90【大吉昌】",
        "variants": [
            {"pattern": r"飞不起来|飞不高|飞不上", "term": "飞不起来",
             "note": "飞不起来吉凶指数83——上升遇阻，宜稳中求进", "luck": "吉多于凶"},
            {"pattern": r"天上飞|天空飞", "term": "天飞翔",
             "note": "天飞翔吉凶指数90【大吉昌】——主升迁与生意获利", "luck": "大吉"},
        ],
    },
]


def _spans_overlap(a: tuple, b: tuple) -> bool:
    """两段文本区间是否重叠（组合梦境元素归并用）"""
    return not (a[1] <= b[0] or b[1] <= a[0])


class DreamEngine:
    """解梦引擎：用户行为研究驱动（G4：关键词提取修复 + 元素吉凶规则层 + 组合拆分）"""

    # 根据用户研究：TOP10高频梦境（90%的人都梦到过）
    # G4：补「怀孕/孕妇」模式（佛滔对比实测 type 为空 → 无策略A 检索）
    TOP_TEN_PATTERNS = {
        "蛇": ("动物类", "蛇在传统中象征变化、智慧、潜意识中的原始本能。梦见蛇常与财运变动、人际关系暗流、健康隐忧相关。"),
        "掉牙|牙齿.*掉|牙.*脱": ("身体类", "最常见的焦虑梦境之一。传统认为梦见掉牙与长辈健康、人际关系损失相关。心理学认为反映对外表或沟通能力的担忧。"),
        "追.*我|被.*追|追赶": ("压力类", "反映现实中的逃避心态。被什么追=你在逃避什么。学生考试前出现概率高出平时3倍。"),
        "水|淹|洪|海|河|湖": ("财运类", "梦见水在传统梦学中主财运。水清主吉财，水浊主口舌破财。涨大水可能预示财务大变动。"),
        "怀孕|孕妇|孕|大肚子": ("吉兆类", "梦见怀孕传统上主幸福与物质财富增加，或反映对新开始、创造力的期待。孕妇之梦多为吉兆。"),
        "飞|飘|翔|悬空": ("事业类", "飞翔梦反映对自由和突破的渴望。上升=事业上升期，坠落=感到失控。与大脑前庭器官兴奋有关。"),
        "考试|考题|高考|成绩|不及格": ("焦虑类", "典型的\"未完成情结\"。即使毕业多年仍梦见考试，反映对当前能力的自我怀疑。"),
        "死.*人|去世|棺材|鬼|坟墓|冥": ("生死类", "梦见棺材传统上主\"升官发财\"，梦见死人说话常是潜意识在处理未完成的情感。"),
        "坠落|踩空|掉下|摔下": ("身体类", "几乎人人都有过的踏空体验。与心脏供血变化、钙缺乏、睡姿有关。反映对失控的恐惧。"),
        "钱|金|银|财|捡.*钱|中奖": ("财运类", "捡钱=小财运将至，丢钱=注意财务安全。现代也反映对经济状况的焦虑。"),
        "手机.*丢|手机.*没电|手机.*坏|迟到|赶不上|赶车|误机": ("现代类", "现代人新增的高频梦境。00后出现概率是70后的5倍。反映对社交断连和信息焦虑的恐惧。"),
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

        # 2. 关键词提取（含情绪词独立）
        keywords = self._extract_keywords(dream_text)
        emotions = self._extract_emotions(dream_text)

        # 3. 梦境元素匹配（组合拆分：多元素各自独立检索；变体检索词优先）
        element_hits = self._match_elements(dream_text)

        # 4. 策略化RAG搜索
        # 性能（2026-08-17 真机排查，P1）：FAISS 完整管线（LLM 查询扩展 +
        # bge-m3 多路召回 + rerank 精排）单次实测 17~94s（warm/冷启动），
        # 解梦是「关键词式多策略召回」，若每路都走完整管线最多 22 次调用
        # = 数分钟级响应（PM 反馈解梦慢）。改为全路 cheap 召回
        # （expand=False/rerank=False，单次 ~0.04s）：
        #   - 解梦查询本身就是"梦见 蛇"式关键词，无扩展必要；
        #   - 引擎本身做多策略召回 + 去重 + 按分排序（recall 语义），
        #     精排增益远小于其耗时代价，且古籍池语义相近内容本就同源；
        #   - 检索工具 _tool_search 仍走完整管线（每轮仅 1 次，保精排质量）。
        # G4：组合拆分检索次数上限 8 次/梦境（策略A 侧），查询级去重防重复检索。
        seen_texts = set()
        all_results = []
        issued_queries = set()

        def _search(query: str, top_k: int) -> None:
            if query in issued_queries:
                return
            issued_queries.add(query)
            for r in retriever.search(query, top_k=top_k, expand=False, rerank=False):
                if r.text not in seen_texts:
                    seen_texts.add(r.text)
                    all_results.append(r)

        # 策略A: 元素检索（组合梦境按元素拆分）→ 无元素时回退高频类型
        if element_hits:
            a_terms = [h["term"] for h in element_hits]
        elif dream_type:
            a_terms = [keywords[0] if keywords else dream_type]
        else:
            a_terms = []
        for term in a_terms[:8]:
            _search(f"梦见 {term}", top_k=5)

        # 策略B: 关键词逐一搜索（基于修复后的关键词）
        for kw in keywords[:8]:
            for q in [f"梦见 {kw}", f"{kw} 梦"]:
                _search(q, top_k=3)

        # 策略C: 用户处境搜索
        if user_context:
            ctx_kw = self._extract_keywords(user_context)
            for kw in ctx_kw[:5]:
                _search(f"梦 {kw}", top_k=2)

        all_results.sort(key=lambda r: r.score, reverse=True)
        sources = list(set(r.source for r in all_results[:15]))

        # 5. 吉凶骨架 + 象征/元素/情绪字段
        symbols, elements, element_notes = [], [], []
        for h in element_hits:
            el = h["element"]
            elements.append(el["name"])
            for s in el["symbols"]:
                if s not in symbols:
                    symbols.append(s)
            v = h["variant"]
            luck = (v.get("luck") if v and v.get("luck") else el["luck"])
            note = (v.get("note") if v and v.get("note") else el["baseline"])
            element_notes.append(f"{el['name']}：吉凶={luck}，依据：{note}")
        luck_level, _ = self._combine_luck(element_hits)
        luck_reason = "；".join(element_notes)

        return DreamResult(
            original_text=dream_text,
            dream_type=dream_type,
            keywords=keywords,
            interpretations=[r.text for r in all_results[:15]],
            source="、".join(sources) if sources else "",
            symbols=symbols,
            emotions=emotions,
            elements=elements,
            element_notes=element_notes,
            luck_level=luck_level,
            luck_reason=luck_reason,
        )

    def _match_top_ten(self, text: str) -> tuple:
        """匹配TOP10高频梦境模式"""
        for pattern, (dtype, hint) in self.TOP_TEN_PATTERNS.items():
            if re.search(pattern, text):
                return dtype, hint
        return "", ""

    def _match_elements(self, text: str) -> List[dict]:
        """元素库匹配：返回命中元素列表（含变体命中与覆盖区间）。

        组合梦境拆分与归并：
        - 每元素最早命中一次；命中变体时该变体的检索词/吉凶优先；
        - 区间重叠时按长区间主导归并：变体/复合意象吸收其内部元素
          （如「被蛇追」中 被追 被 蛇 吸收，「被追到水里」中 水 被 被追 吸收），
          避免同一段文字重复计数导致吉凶合成失真。
        """
        hits = []
        for el in DREAM_ELEMENTS:
            m = re.search(el["match"], text)
            if not m:
                continue
            span = m.span()
            variant = None
            for v in el["variants"]:
                vm = re.search(v["pattern"], text)
                if vm:
                    variant = v
                    span = vm.span()
                    break
            overlapped = [h for h in hits if _spans_overlap(span, h["span"])]
            if overlapped:
                # 区间重叠：跨元素归并，长区间主导——变体/复合意象吸收其内部元素
                # （如「被追到水里」的 被追 吸收 水，而非相反）
                if span[1] - span[0] <= max(h["span"][1] - h["span"][0] for h in overlapped):
                    continue  # 自身更短或等长：被已有命中吸收
                for h in overlapped:
                    hits.remove(h)
            term = variant["term"] if variant else el["name"]
            hits.append({"element": el, "variant": variant, "span": span, "term": term})
        return hits

    @staticmethod
    def _combine_luck(hits: List[dict]) -> Tuple[str, List[str]]:
        """多元素吉凶合成：全部为吉/大吉时取最高（喜上加喜），
        存在吉多于凶及以下时取最低（保守，宁勿吓人、勿空许）。
        """
        if not hits:
            return "", []
        levels = []
        for h in hits:
            v = h["variant"]
            levels.append(v.get("luck") if v and v.get("luck") else h["element"]["luck"])
        ranks = [DREAM_LUCK_RANK[l] for l in levels]
        if min(ranks) >= DREAM_LUCK_RANK["吉"]:
            combined = max(levels, key=lambda l: DREAM_LUCK_RANK[l])
        else:
            combined = min(levels, key=lambda l: DREAM_LUCK_RANK[l])
        return combined, levels

    def _classify(self, text: str) -> Tuple[List[str], List[str]]:
        """候选词 → (关键词, 情绪词)。

        规则（G4）：
        - 触发词（梦见/梦到/做梦/醒来 + 时间词）绝不进入关键词；
        - 情绪词提取到 emotions，禁止参与意象检索；
        - 双字及以上保留，单字仅白名单意象词保留。
        """
        keywords, emotions = [], []
        for w in text:
            if not w or w in DREAM_TRIGGER_WORDS or w in DREAM_QUESTION_WORDS:
                continue
            if any(em in w for em in DREAM_EMOTION_WORDS):
                emotions.append(w)
                continue
            if len(w) >= 2 or w in SINGLE_CHAR_DREAM_WORDS:
                keywords.append(w)
        # 关键词去重保序（jieba 同词可多次出现，如「总梦见掉牙」两处掉牙）
        return list(dict.fromkeys(keywords))[:12], list(dict.fromkeys(emotions))

    def _extract_keywords(self, text: str) -> List[str]:
        """关键词提取（G4：单字白名单 + 触发词排除 + 情绪词降权）"""
        try:
            import jieba.posseg as pseg
            # 词性放宽到 n/v/a/l/s/t：发大水(l)、水里(s)、去世(t 误标) 等
            # 真实意象词不再被词性过滤丢弃
            words = pseg.lcut(text)
            candidates = [w for w, p in words if p[0] in "nvlast"]
            kws, _ = self._classify(candidates)
            return kws
        except ImportError:
            # 无 jieba 兜底：按白名单/黑名单过滤（re.findall 无词性）
            return self._fallback_keywords(text)

    def _extract_emotions(self, text: str) -> List[str]:
        """情绪词提取（G4 新增：独立字段，不参与检索）"""
        try:
            import jieba.posseg as pseg
            words = pseg.lcut(text)
            candidates = [w for w, p in words if p[0] in "nvlas"]
            _, emotions = self._classify(candidates)
            return emotions
        except ImportError:
            _, emotions = self._fallback_classify(text)
            return emotions

    def _fallback_keywords(self, text: str) -> List[str]:
        kws, _ = self._fallback_classify(text)
        return kws

    def _fallback_classify(self, text: str) -> Tuple[List[str], List[str]]:
        """无 jieba 兜底：整段汉字先剥触发词/情绪词，剩余片段按 2+ 字或
        单字白名单过滤为关键词；被剥掉的情绪词单独收集进 emotions。"""
        keywords = []
        for run in re.findall(r'[一-鿿]+', text):
            rest = run
            for w in sorted(DREAM_TRIGGER_WORDS, key=len, reverse=True):
                rest = rest.replace(w, " ")
            for em in sorted(DREAM_EMOTION_WORDS, key=len, reverse=True):
                rest = rest.replace(em, " ")
            for piece in rest.split():
                if len(piece) >= 2 or (len(piece) == 1 and piece in SINGLE_CHAR_DREAM_WORDS):
                    keywords.append(piece)
        emotions = []
        for em in DREAM_EMOTION_WORDS:
            if em and em in text and em not in emotions:
                emotions.append(em)
        return keywords[:12], emotions


def format_dream_prompt(
    dream_text: str,
    dream_result: DreamResult,
    user_context: str = "",
    bazi_info: dict = None,
) -> str:
    """构建完整解梦Prompt — 覆盖用户关心的5大问题（G4：注入吉凶基线骨架）"""
    # 兼容既有 Mock 注入路径（tests/test_bot.py 用 Mock 而非 DreamResult）：
    # 非 DreamResult 对象不展开 G4 新段，行为同修复前
    if isinstance(dream_result, DreamResult):
        elements = list(dream_result.elements)
        element_notes = list(dream_result.element_notes)
        luck_level = dream_result.luck_level
        emotions = list(dream_result.emotions)
    else:
        elements, element_notes, luck_level, emotions = [], [], "", []

    parts = ["## 解梦请求\n"]

    # 梦境描述
    parts.append(f"### 完整梦境\n{dream_text}")

    # 高频梦境类型提示（G4：直接对原文重扫，不再依赖 keywords[0] 查表）
    dream_type = getattr(dream_result, "dream_type", "")
    if dream_type:
        type_hint = ("", "")
        for pat, (dt, hint) in DreamEngine.TOP_TEN_PATTERNS.items():
            if re.search(pat, dream_text):
                type_hint = (dt, hint)
                break
        if type_hint[0]:
            parts.append(f"\n### 梦境类型\n{type_hint[0]}类高频梦境\n{type_hint[1]}")

    # 情绪基调（G4：情绪词独立维度，不参与检索但参与解读）
    if emotions:
        parts.append(
            f"\n### 情绪基调\n{'、'.join(emotions)}\n"
            "（情绪反映做梦者当下的心理状态，本身不改变吉凶判断；"
            "可用于安抚用户情绪与给出化解建议）")

    # 用户当前处境
    if user_context:
        parts.append(f"\n### 做梦者处境\n{user_context}")

    # 八字
    if bazi_info:
        parts.append(f"\n### 命理信息\n{bazi_info.get('day_master','')} {bazi_info.get('current_dayun','')}")

    # 传统解梦吉凶基线（G4：元素规则层，硬约束注入）
    if elements and luck_level:
        parts.append("\n### 传统解梦吉凶基线（来源：周公解梦 / 敦煌梦书 / 佛滔判词体系）")
        for note in element_notes:
            parts.append(f"- {note}")
        parts.append(f"综合吉凶骨架：{luck_level}")
        parts.append(
            "此骨架为传统解梦体系的硬约束：\n"
            "1. 解释必须在此骨架方向上展开与对话化，不得反转吉凶方向；\n"
            "2. 如有具体古籍依据可在骨架上补充细化，但不许与骨架矛盾；\n"
            "3. 用户直接问吉凶（如「是不是要倒霉」「好不好」）时，明确给出骨架方向，不回避、不吓唬。")

    # 古籍参考
    interpretations = getattr(dream_result, "interpretations", None) or []
    if interpretations:
        parts.append(f"\n### 古籍参考 ({len(interpretations)}条)")
        for i, text in enumerate(interpretations[:8], 1):
            parts.append(f"{i}. {text[:250]}")

    # 用户最关心的5个问题（引导LLM回答）
    parts.append(f"""
### 请从以下维度综合分析（覆盖用户最关心的5个问题）：

1. **吉凶判断**：这个梦是吉是凶？预示着什么？有没有需要注意的征兆？
2. **现实关联**：这个梦和你当前的生活处境有什么联系？
3. **反复性**：如果反复梦到，说明了什么？需要注意什么？
4. **人物象征**：梦中的人物（如果有）代表什么？
5. **化解建议**：如果是不好的梦，有什么化解方法？好的梦怎么把握？

请用亲切、专业的口吻回复，像一位有智慧的老先生在和你聊天。
结合古籍依据，但要给出切实可行的现实建议。
字数：500-800字。
禁止使用任何 emoji 表情符号（不用表情图标、不用颜文字），
只用文字与中文标点表达语气。""")

    return "\n".join(parts)
