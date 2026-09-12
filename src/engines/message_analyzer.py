"""Combined message analyzer — detects emotion + intent in ONE Flash call.

Replaces two sequential AI calls (EmotionSoother + IntentClassifier) with
a single call that handles both. Cuts free-chat latency from ~9s to ~5s.

The combined prompt is carefully designed to maintain accuracy on both
tasks while halving API calls.
"""
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Optional, Tuple

import httpx

# Task 5（批次 1）：意图枚举行同源——build_intent_enum_line()（注册表）为唯一事实源，
# COMBINED_PROMPT 中占位符在模块加载时替换。capability_registry 不 import 本模块，无循环。
from src.bot.capability_registry import build_intent_enum_line


@dataclass
class MessageAnalysis:
    """Combined result: emotion detection + intent classification.

    阶段 1 理解升级（方案 v5，2026-08-09）：
    - secondary_needs: 主意图之外的附加需求（如 ["bazi","company_match","comfort"]）
    - facts: 关键事实（gender/subject/关系等；subject: self=本人 other=帮他人排）
    - missing_info: 回答问题还缺的关键信息（如 ["birth_time","company_name"]）
    - needs_search: 是否需要实时信息（公司/行业/时事类=true，纯命理古籍=false）
    解析失败时全部走默认值（intent 单值），行为与升级前一致，不崩。
    """
    needs_soothe: bool
    soothe_text: str
    emotion_label: Optional[str]
    intent: Optional[str]  # None = free_chat
    is_sharing: bool = False  # True = user is telling a personal story
    secondary_needs: list = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    missing_info: list = field(default_factory=list)
    needs_search: bool = False
    # 批次 2 E6（工具 AI 决策通道）：工具场景提示——规则门控命中时
    # intent=None + scene_hint=cap_id，直达工具链（默认 None，向后兼容，
    # 全部既有构造点无需改动）
    scene_hint: Optional[str] = None


COMBINED_PROMPT = """You are a message analyzer for a Chinese fortune-telling AI (易理明灯).
You are given the user's recent conversation context (if any) plus the current message.
Use the context to judge intent and emotion more accurately — e.g. if the user already
provided birth info in a previous turn, this turn is likely a follow-up analysis request,
not a new information collection.
Analyze and return BOTH:

## 1. Emotional Analysis
- needs_soothe: true if the user is expressing distress/sadness/anxiety/anger/heartbreak/confusion and needs emotional acknowledgment BEFORE any fortune-telling analysis. false for neutral questions, birth info, casual greetings.
- If needs_soothe: write a BRIEF (1-2 sentence), warm, modern Chinese acknowledgment specific to their situation. NO classical/文言文. NO "小友""老夫".
- If NOT needs_soothe: soothe_text = ""

## 2. Intent Classification
__INTENT_ENUM__

Rules:
- Pure birth statement (year-month-day, NO question/intent words) = "bazi"
- Birth date PLUS a real question about the person = classify by the question's topic, NOT just "bazi" (e.g. "1990年5月20日…哪个公司最配" = "career", "1990年5月20日…今年运势" = "bazi")
- "career" 事业适配: 职业选择/公司选择/行业适配/跳槽/择业/事业发展类问题（哪个公司/行业适合我、适合什么工作、事业怎么发展、跳槽好不好、去哪个城市发展）
- "hehun" 双人合盘/合婚: 双人合盘/合盘/八字合婚/我和TA合不合/看看我们配不配/缘分契合/婚姻匹配/合婚配对/我们俩缘分（合盘语境）→ "hehun"（**必须同时指向两个人**——消息里没有第二个人/没有"我们"时**不得**判 hehun：单人问自己的婚姻/姻缘，问运势/流年归 "bazi"，问状况/建议归 "advisor"）
- Dream description (梦见/梦到/做梦) = "dream"
- Daily fortune / today's luck requests (今日运势/今天运气/今日宜忌/今天宜忌/今日运程/今日日历/今天适合) = "calendar"
- Life advice / guidance requests (建议/怎么办/有什么建议/帮我分析/我该怎么做/给我点建议) = "advisor"
- Pure emotional expression with NO fortune-telling request = "free_chat"
- Colloquial fortune-telling: "看下命""算一下""运气怎么样" = "bazi"
- 区分规则：问"哪个公司/行业适合我"→career；问"我像哪个名人/明星"→按普通命理咨询（bazi/advisor）处理，不要承诺名人对照
- 双人合盘规则：消息同时指向两人（我/我们 + 他/她/TA）且语义为「合/配/缘分」→ "hehun"（如"我和TA合不合""看看我们配不配""我们俩缘分如何"）；反之，消息只指向用户自己一个人（无第二人/无"我们"）时，婚姻/姻缘问题属单人咨询 → "advisor"，**不要**判 hehun（判成 hehun 会落到"给我双方生辰"的死胡同）

## 4. Career 意图判定要点
- 只要问题指向职业/公司/行业/事业适配（哪怕附带出生日期），必须判为 "career"
- "配"在职业语境（跟哪个公司配/适合什么工作）→ career；"配"在婚恋语境（配对/合不合）→ hehun

## 3. Sharing Detection (心事树洞)
- is_sharing: true ONLY if user is telling a personal story with emotional depth
- Sharing signals: messages with "我" + emotional words + personal situation (not a request)
- NOT sharing (must return false):
  * Knowledge questions: "告诉我X", "X是什么意思", "X代表什么", "有没有人X"
  * Service requests: "帮我看看X", "帮我算X", "请帮我看X", "想看X"
  * Urgent requests: "在线等", "急", "追分"
  * Direct fortune requests, birth date info, short greetings

## 5. 附加信息提取（阶段 1 理解升级，方案 v5）
- secondary_needs: 主意图之外用户还提到的其他需求，列表形式（如 ["财运","失眠安抚"]）。
  主意图已在 intent 字段，不要重复放；无附加需求 = []
- facts: 消息中能确定的关键事实（只放明确的，不确定不猜）：
  * gender: "男"/"女"（消息里明确说了才放；没说=不填）
  * subject: "self"（本人）/ "other"（帮别人排盘，如"我妹妹""我老公""我朋友"）
  * 其他明确事实如关系（老公/妹妹/朋友）、城市等，用简短键名
- missing_info: 回答用户的问题还缺哪些关键信息（如出生时辰 birth_time、
  具体公司/行业名 company_name），没有缺= []
- needs_search: 用户的问题是否需要实时信息才能答好（公司/行业现状/时事/人物
  近况类=true；纯命理古籍/排盘=false），不确定默认 false

## 6. 工具场景分类指导（批次 2 E6：LLM 是调度员、工具是工具库）
- 涉及以下场景的问题，即使符合上面 hehun/career/xingming/calendar 等意图规则
  的描述，也一律归 "free_chat"（这类请求会进入对话工具链，由 AI 按工具说明书
  选工具执行确定性计算，不要分给引擎 intent）：
  * 数字吉凶：手机号/手机号码/车牌/门牌/尾号/号码吉凶
  * 合婚配对：合不合/合婚/八字合/配不配/般配/生辰合
  * 起名改名：起名/取名/改名/宝宝叫/孩子叫
  * 流年流月：流年/流月/明年运势/逐月运势
  * 择业方位：适合做什么/适合什么行业/职业方向/择业/行业选择/找工作/换工作

Return ONLY JSON:
{"needs_soothe": bool, "soothe_text": "安抚文本或空", "emotion": "情绪标签",
 "intent": "意图分类", "is_sharing": bool, "secondary_needs": ["附加需求"],
 "facts": {"gender": "男/女", "subject": "self/other", "...": "..."},
 "missing_info": ["缺失信息"], "needs_search": bool}"""

# Task 5（批次 1）：模块加载时把枚举行占位符替换为注册表生成行（生成结果与原文
# 一字不差，由 tests/test_capability_registry.py::test_combined_prompt_enum_same_source 锚定）
COMBINED_PROMPT = COMBINED_PROMPT.replace("__INTENT_ENUM__", build_intent_enum_line())

# 批次 2 E6（工具 AI 决策通道）：工具场景词规则门控表（对标豆包/元宝
# 「LLM 是调度员、工具是工具库」）。场景词命中 → analyze() 直接返回
# intent=None + scene_hint=cap_id（0 LLM 确定性，与 D5 快路径门控同族），
# 直达工具链（_free_chat 工具清单注入 + _run_tool_loop）→ LLM 按工具说明
# 书选工具执行；无工单时 handler 按 SCENE_DEFAULT_ENGINE 默认引擎兜底。
#
# 词表设计（防误伤，逐词评估见 task-E6-report.md）：
# - 全部为多字专属词，避免「名字/号码/运势/婚/名」等泛词单用；
# - 各场景词表之间无交集（消息命中唯一 cap_id）；
# - 不收录「排盘/八字/出生日期」类词——排盘类请求必须保留 bazi 快路径。
TOOL_SCENE_WORDS: dict = {
    "num_omen": ("手机号", "手机号码", "车牌", "门牌", "尾号", "号码吉凶", "数字吉凶"),
    "hehun": ("合不合", "合婚", "八字合", "配不配", "般配", "生辰合"),
    # R1-3（T045 修复）：口语起名动词族（"给孩子起个名"）此前只命中
    # LLM 意图分类（3/3 全部 0 工具调用——LLM 直接输出排盘卡片），
    # 补入场景词表 → 0 LLM 确定性走 naming 场景 → 工具兜底（0 LLM）。
    "naming": ("起名", "取名", "改名", "宝宝叫", "孩子叫",
               "起个名", "起个名字", "取个名", "取个名字", "起名字", "取名字"),
    "fortune_cycle": ("流年", "流月", "明年运势", "逐月运势"),
    "career_dir": ("适合做什么", "适合什么行业", "职业方向", "择业",
                   "行业选择", "找工作", "换工作"),
}

# R1-3 意图强路由（0 LLM 确定性；analyze() 中置于 BIRTH_DATE_PATTERN
# 快路径之前）：评测实锤三类请求被 LLM 意图分类错域路由——
# - 六爻摇卦 → 奇门遁甲局（T058 出奇门实锤）；- 紫微排盘 → 八字排盘
# （T064 被 bazi 快路径掐走）；- 择日带完整日期 → 建档引导/calendar
# （T100 实锤）。改关键词确定性强路由，杜绝 LLM 波动（3/3 全错、1/3
# 才稳定的真实命中率）。
# _FORCE_META_RE 元门控：比较/差异类问句（哪个/区别/对比/比较/差异）
# 是讨论不是排盘请求（"紫微斗数和八字哪个准"不得强制紫微）。
# k38-M2（审查实测）：门控的「哪个」本意是比较类问句，却把择日措辞
# 「（这个|那个|哪个）日子」一并拦成死词（`_ZERI_FORCE_RE` 的该分支永不生效，
# 「2026年10月1日搬家，哪个日子好」被拦）。豁免紧跟「日子」的「哪个」——
# 这是择日请求的措辞，不是比较讨论；其余「哪个」（"哪个准"/"哪个更适合我"）
# 仍走门控。
_FORCE_META_RE = re.compile(r"哪个(?!日子)|区别|对比|比较|差异")
# 六爻族（与任务族 T058-T063 全量对齐，碰撞扫描零误伤）
_LIUYAO_FORCE_RE = re.compile(r"摇卦|摇一卦|起一卦|六爻|卜卦|占一卦|算卦")
# 紫微族（与任务族 T064-T069 全量对齐，碰撞扫描零误伤）
_ZIWEI_FORCE_RE = re.compile(r"排\s*紫微|紫微(?:盘|命盘|斗数)")
# 择日（k38 扩词 + 放开无日期锚，T029/T033/T034/T035/T036/T037 六条同根）：
# 原词表只覆盖「选(个)日子/择日/择吉/挑日子/看日子/吉日」，漏掉真实口语里的
# 「挑个时间」（T029）/「好日子」（T035/T037）/「这个日子」（T034/T036）
# → 请求落到 LLM 意图分类 → calendar → 「请先设置八字」建档引导兜底。
_ZERI_FORCE_RE = re.compile(
    r"选(?:个|几个)?(?:日子|时间|好日子)|"
    r"挑(?:个|几个)?(?:日子|时间|好日子)|"
    r"择日|择吉|挑日子|看日子|好日子|吉日|换一批|重新选|"
    r"(?:这个|那个|哪个)日子")
_ZERI_DATE_ANCHOR_RE = re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日")
# 择日泛问词：通用评价问词（「行不行/好不好/合适吗」）——**绝不单独强路由**
# （会把无关问句拽进择日，如「2026年5月20日他对我好不好」），只在
# 「日期锚 + 场景词」双条件下参与判定。
_ZERI_WEAK_ASK_RE = re.compile(r"行不行|好不好|合适吗|合不合适|宜不宜")
# 择日场景词：与 handler.ZERI_SCENE_SYNONYMS（7 场景）+ 场景词族同源，
# 仅用于「无日期锚」时的第二条件（T033「下个月搬家 帮我选个日子」形态）。
_ZERI_SCENE_RE = re.compile(
    r"嫁娶|结婚|婚礼|订婚|搬家|入宅|乔迁|迁居|开业|开张|开市|开店|"
    r"晋升|升职|加薪|升迁|竞聘|述职|入职|求职|面试|谈薪|升官|"
    r"出行|旅游|旅行|出差|开工|动土|建房|破土|奠基|"
    r"提车|买车|购车|签约|签合同|过户")

# T046 姓名分析确定性强路由：LLM 把「『李沐宸』这个名字怎么样」判成 advisor
# → `_handle_advisor` 无档案分支「想为你生成专属建议，需要先了解你的命盘哦～」
# 死胡同（产品 `_handle_xingming` 五格/笔画/数理/五行 能力齐备却够不着）。
#
# 触发面刻意收窄（防误伤优先）：**必须有一个"姓名槽位"**——
#   「名字/姓名」后 ≤6 字内紧跟评价词（「这个名字怎么样」「姓名好不好」），
# 再叠加 ② 评价/分析问词 且 ③ **不含**起名/改名动作族。
# 反例锁定（不得命中）：`帮我看看名字笔画`（只有泛词「名字」+「看看」，
# 无姓名槽位——旧宽松口径会把它路由到 `_handle_xingming`，姓名抽取退化取
# 「帮我」两字当名字分析）；`给宝宝起个名`（要候选名 → naming 工具链）。
#
# k38-I1（审查实测·产品误伤）：原 ① 的「引号/书名号括出任意 2-4 字」分支
# **已删除**——《三大队》（电影）/《活着》（书）/「易宝支付」（公司）/「AI」
# 这类引号内容全被当成姓名槽位，与 `_XINGMING_VERDICT_RE` 的「怎么样/分析」
# 组合后 0 LLM 判 intent=xingming → 对电影名/公司名做五格三才分析（`_handle_xingming`
# 的姓名抽取正则不认《》，兜底会抓到「电影」两字当名字）。该分支对目标句
# （T046「帮我看看『李沐宸』这个名字怎么样」）**不是必需**——`_XINGMING_NEAR_RE`
# 单独即可命中（有「名字」槽位），故删除是零收益损失的纯误伤源。
_XINGMING_VERDICT_RE = re.compile(
    r"怎么样|好不好|如何|分析|测测|测一测|看看|评分|打分|寓意|含义")
_XINGMING_NEAR_RE = re.compile(
    r"(?:名字|姓名)[^，。！？；\n]{0,6}"
    r"(?:怎么样|好不好|如何|分析|测测|看看|评分|打分|寓意|含义)")
_XINGMING_NAMING_RE = re.compile(
    r"起名|取名|改名|宝宝叫|孩子叫|起个名|取个名|起名字|取名字")

# T076 单人婚姻询问（未建档引导建档）：LLM prompt 旧规则「婚姻匹配/合婚配对
# → hehun」未区分单/双人，把「帮我看看我的婚姻状况」判成 hehun →
# `_handle_hehun` 无双方生辰死胡同（「给我双方生辰即可直接测算」）。
# 单人婚姻询问 → advisor（无档案走建档引导「出生年月日时/性别」，有档案走
# 命盘建议）；**双人语境仍走 hehun**（scene_hint/LLM 意图），不误伤真合盘。
#
# 触发面刻意收窄（T022 现状回归保护）：**带时间维度锚（运势/运程/流年/今年/
# 明年）的婚姻问句不在此路由内**——「帮我看看我的婚姻运势」属运势域问句，
# 由其既有路径处理（T022 当前绿）；本路由只接「婚姻/姻缘 + 状况/分析」类
# 单人咨询（T076「帮我看看我的婚姻状况」形态）。
_SINGLE_MARRIAGE_RE = re.compile(r"婚姻|姻缘")
_MARRIAGE_TIMELINE_RE = re.compile(r"运势|运程|流年|今年|明年|本月|这个月")
_SECOND_PERSON_RE = re.compile(
    r"他|她|TA|对方|对象|伴侣|我们|我俩|我和|两人|俩人|双方|"
    r"男朋友|女朋友|男友|女友|老公|老婆|未婚夫|未婚妻")
# 编译一次（词全为中文，re.escape 防御未来加词含正则元字符）
_TOOL_SCENE_CHECKS = [
    (cap_id, re.compile("|".join(re.escape(w) for w in words)))
    for cap_id, words in TOOL_SCENE_WORDS.items()
]


def match_tool_scene(text: str) -> Optional[str]:
    """工具场景词匹配：命中返回 cap_id（dict 插入序首个命中；词表跨场景
    无交集，顺序不影响结果），未命中返回 None。"""
    for cap_id, pattern in _TOOL_SCENE_CHECKS:
        if pattern.search(text):
            return cap_id
    return None


def _entity_qa_beats_scene(msg: str, scene_hint: str) -> bool:
    """T104（k38，行54 事故同款）：实体 QA 优先于场景词。

    用户问「**易宝支付**这家公司**靠不靠谱**」时必须走联网检索引导拿来源痕迹
    （PM 实诉事故：AI 甩锅「你自己查证」）；而 `TOOL_SCENE_WORDS["career_dir"]`
    含场景词「换工作」——实体问句常伴随「我正考虑换工作过去」，场景词先命中就
    把请求劫持成纯择业工具卡（回复完全没有实体名与来源痕迹）。

    判定复用既有单一事实源 `src.rag.search_trigger`（k11b 实体层）：命名实体
    （公司/机构/品牌名）+ 实体问词（强=靠不靠谱/评价/待遇…，弱=怎么样/如何）
    → 实体 QA，让位给 LLM 意图 + needs_search 引导（与 T105 腾讯同路径）。
    只对 career_dir 生效（唯一含这类实体问句伴生词的场景词表），其余场景词
    （起名/合婚/号码/流年）保持原 0 LLM 确定性直达；判定异常 → 不拦截
    （保持原确定性行为，不劣化）。

    k38-M3（审查实测）：**只认强问词**（`strong`）——弱问词（怎么样/如何）会让
    实体抽取的误抽串（「我想换工作，去一家互联网公司怎么样」→ '一家互联网'；
    「我想换工作去银行，怎么样」→ '换工作去银行' 跨动词短语误抽）也触发守卫，
    把真·择业请求从确定性直达拽进 LLM 且以垃圾串作检索词。T104 目标句是强
    问词（靠不靠谱=strong），弱问词 + 场景词保持 k38 前的场景路由（零回归，
    残留在报告登记）。
    """
    if scene_hint != "career_dir":
        return False
    try:
        from src.rag.search_trigger import extract_entity_mentions, has_entity_ask
        strong, _weak = has_entity_ask(msg)
        if not strong:
            return False
        return bool(extract_entity_mentions(msg))
    except Exception:
        return False


# ── k38-RI4b（复审实测·档案污染）：生辰时间谓词 ──────────────────────
# 未来日期 = 婚期/预产期/行程等非生辰语境。此前 BIRTH_DATE_PATTERN 只认日期
# 形状、不判过去/未来：实测「2026年10月1日结婚，帮我看看我的婚姻」→
# intent=bazi + partial_birth={'year':2026,…} → 婚期被当生辰排盘/建档
# （违「档案为单一事实源」铁律）。analyzer 路由与 handler（`_extract_partial_birth`
# / `_handle_bazi`）共用下方 `MessageAnalyzer.birth_dates_all_future`（负视图）
# 与 `birth_date_candidate`（正视图）——同一时间谓词覆盖两条路径。
_DATE_MONTH_DAY_RE = re.compile(r'(\d{1,2})\s*[月/-]\s*(\d{1,2})')


def _birth_predicate_today(current_year: Optional[int] = None) -> date:
    """时间谓词的「今天」基准：current_year 显式传入时用该年 + 真实月日
    （与 handler._extract_partial_birth 的 current_year 口径一致）。"""
    today = date.today()
    if current_year is None or current_year == today.year:
        return today
    try:
        return today.replace(year=current_year)
    except ValueError:                  # 2/29 → 基准年非闰年
        return date(current_year, 2, 28)


def _birth_match_in_future(matched_text: str, today: date) -> bool:
    """BIRTH_DATE_PATTERN 命中串的日期是否严格晚于 today。

    年份 > 今年 → 未来；年份 = 今年 → 比月日；年份 < 今年 → 非未来。
    月日解析不出（如中文数字月「十一月」）且年份 = 今年 → 保守当「非未来」
    （宁可放行真生辰，不误杀；跨年未来日期已被年份分支拦住）。
    """
    ym = re.match(r'\s*(\d{4})', matched_text)
    if not ym:
        return False
    year = int(ym.group(1))
    if year != today.year:
        return year > today.year
    md = _DATE_MONTH_DAY_RE.search(matched_text)
    if not md:
        return False
    return (int(md.group(1)), int(md.group(2))) > (today.month, today.day)


class MessageAnalyzer:
    """Single-pass message analyzer: emotion + intent in one Flash call."""

    # Fast path: 纯生日陈述（不含任何意图词）→ bazi 立即返回
    # D7（2026-08-24 生产实测）：口语长句生辰（阴历/农历 + 中文数字月 +
    # 阿拉伯数字日，如"1999年阴历三月28"）也必须命中，否则降级/无 LLM 链路
    # 把完整出生信息误判为自由聊天。INTENT_HINT_PATTERN 门控不变。
    # D7 补漏（批次 2 B3-20，2026-08-27）：月名交替原为单字符类
    # [一…十冬腊正]，'十一月/十二月' 取'十'后 [月/-] 匹配'一/二'失败 → 整句
    # miss（快判 + 降级 _rule_analyze 同病）。补 十一|十二；十月/冬月/腊月
    # 单字符路径不受影响。
    BIRTH_DATE_PATTERN = re.compile(
        r'\d{4}\s*[年/-]\s*(?:农历|阴历|闰)?\s*'
        r'(?:\d{1,2}|十一|十二|[一二三四五六七八九十冬腊正])\s*[月/-]\s*'
        r'(?:\d{1,2}|初?[一二三四五六七八九十廿卅]+)'
    )
    # 意图提示词（fast path 陷阱修复）：消息含这些词说明不只是报生日
    # （可能问事业/公司适配等），即使有生日也必须走 AI 分类，
    # 否则"1990年5月20日…哪个公司最配"会被掐成纯排盘 bazi
    # 批次 2 D5（2026-08-27，择日工具链可达性修复）：追加择日场景/意图词
    # （与 handler ZERI_SCENE_SYNONYMS / ZERI_INTENT_WORDS / _extract_purpose
    # 择日词表同源，勿加"排盘/八字/出生日期"类词——排盘类请求必须保留快路径）：
    # 此前『2026年9月15日搬家 帮我选个日子』命中 BIRTH_DATE_PATTERN 且无意图词
    # → 快路径 0 LLM 确定性判 intent="bazi" → 用户要择日却得排盘卡片（D4 生产
    # 缺陷）。择日词入表后请求落入 LLM 意图分类（deepseek 输出 zeri）→
    # _handle_zeri 引擎产出具体日期。
    INTENT_HINT_PATTERN = re.compile(
        r'适合|发展|工作|公司|职业|事业|配|像谁|相似|去哪|怎么样|好吗|能|会|合|缘|'
        # 择日：场景词（搬家/开业/嫁娶/出行/开工/晋升/提车/签约 族）
        r'搬家|入宅|乔迁|迁居|开业|开张|开市|开店|结婚|婚礼|订婚|嫁娶|'
        r'出行|旅游|旅行|出差|开工|动土|建房|破土|奠基|晋升|升职|'
        r'提车|买车|购车|签约|签合同|过户|'
        # 择日：意图词（选日子/择日/吉日 族）
        r'择日|择吉|选日子|选个日子|选个时间|挑日子|挑个日子|挑个时间|'
        r'看日子|好日子|吉日|哪天|换一批|重新选'
    )

    @classmethod
    def birth_dates_all_future(cls, msg: str,
                               current_year: Optional[int] = None) -> bool:
        """k38-RI4b 时间谓词（单一事实源，两条路径共用）：消息**有**完整日期且
        **全部**落在未来 → 婚期/预产期/行程等非生辰语境，不得当生辰。

        实测（复审）：「2026年10月1日结婚，帮我看看我的婚姻」此前被判 bazi +
        partial_birth={'year':2026,…} → 婚期被当生辰排盘/建档（档案污染，违
        「档案为单一事实源」铁律）。调用方据此拒绝"当生辰"：

        - `MessageAnalyzer.analyze` 婚姻强路由 → 不判 bazi（落 advisor）；
        - `MessageHandler._extract_partial_birth` → 不提取（F2 累积/
          `_redirect_single_marriage` 同源）；
        - `MessageHandler._handle_bazi` → `_extract_bazi_info` 结果作废（不入排盘/
          建档；该提取器补默认时辰/城市，未来日期同样能凑出完整盘）。

        无完整日期（部分生辰「我1990年生的」/中文数字年「一九七六年三月初三」）
        → False（不拦）；多个命中只要有一个不在未来（「我1990年5月20日出生，
        2026年10月1日结婚」）→ False（按 1990 那条走）。月日不可解析（中文数字
        月）且年份=今年 → 保守当非未来（宁可放行真生辰，不误杀）。
        """
        if not msg or not cls.BIRTH_DATE_PATTERN.search(msg):
            return False
        today = _birth_predicate_today(current_year)
        for m in cls.BIRTH_DATE_PATTERN.finditer(msg):
            if not _birth_match_in_future(m.group(0), today):
                return False
        return True

    @classmethod
    def birth_date_candidate(cls, msg: str,
                             current_year: Optional[int] = None) -> bool:
        """k38-RI4b 正视图：消息能否当「生辰陈述」（= 有完整日期 且 非全在未来）。
        analyzer 强路由用；语义与取值见 `birth_dates_all_future`（同一时间谓词）。"""
        return (bool(cls.BIRTH_DATE_PATTERN.search(msg or ""))
                and not cls.birth_dates_all_future(msg, current_year))

    def __init__(self, api_key: str, model: str = "deepseek-flash"):
        self.api_key = api_key
        self.model = model
        self._cache: Dict[str, MessageAnalysis] = {}

    def analyze(self, user_message: str, history: Optional[list] = None) -> MessageAnalysis:
        """Analyze message for emotion + intent in one pass.

        AI 原生改造（Phase 1）：history 为最近 2-3 轮对话（[{role, content}, ...]），
        注入分析 prompt 让意图/情绪判断更准（方案 2.1 短期记忆）。

        Returns MessageAnalysis with both results.
        Uses cache for repeated messages.
        """
        if not user_message:
            return MessageAnalysis(needs_soothe=False, soothe_text="",
                                   emotion_label=None, intent=None)

        # 批次 2 E6（工具 AI 决策通道）：工具场景词规则门控 → intent=None +
        # scene_hint=cap_id 直达工具链（0 LLM 确定性，见模块级 TOOL_SCENE_WORDS）。
        # 置于 BIRTH_DATE_PATTERN 快路径之前：含日期+场景词的请求
        # （如「1990年5月20日 想给孩子起名」）不被纯生日快路径掐成 bazi
        # （D4 同族缺陷）；纯生日陈述（无场景词）仍走下方 0 LLM 快路径。
        scene_hint = match_tool_scene(user_message)
        if scene_hint and not _entity_qa_beats_scene(user_message, scene_hint):
            return MessageAnalysis(needs_soothe=False, soothe_text="",
                                   emotion_label=None, intent=None,
                                   scene_hint=scene_hint)

        # R1-3（T058/T064/T100 修复·意图强路由）：关键词确定性意图（0 LLM，
        # 见模块级 _*_FORCE_RE 注释）。元门控：比较类问句不强制。放在
        # BIRTH_DATE_PATTERN 快路径之前——"1990年5月20日…帮我排紫微盘"
        # 必须路由 ziwei 而非被纯生日快路径掐成 bazi（T064 3/3 实锤）。
        if not _FORCE_META_RE.search(user_message):
            if _LIUYAO_FORCE_RE.search(user_message):
                return MessageAnalysis(needs_soothe=False, soothe_text="",
                                       emotion_label=None, intent="liuyao")
            # 择日三档判定（k38 扩词 + 放开无日期锚，见词表注释）：
            # ① 强意图词 + 日期锚（R1-3 原档：T028/T038/T100 形态）
            # ② 泛问词 + 日期锚 + 场景词（T034「…这个日子行不行」形态）
            # ③ 强意图词 + 场景词（无日期锚：T033/T035 形态）→ _handle_zeri
            #    无日期分支确定性返回「请告诉我您想查询的日期和用途」（零风险）
            _z_strong = _ZERI_FORCE_RE.search(user_message)
            _z_anchor = _ZERI_DATE_ANCHOR_RE.search(user_message)
            _z_scene = _ZERI_SCENE_RE.search(user_message)
            if ((_z_strong and _z_anchor)
                    or (_z_anchor and _z_scene
                        and _ZERI_WEAK_ASK_RE.search(user_message))
                    or (_z_strong and _z_scene)):
                return MessageAnalysis(needs_soothe=False, soothe_text="",
                                       emotion_label=None, intent="zeri")
            if _ZIWEI_FORCE_RE.search(user_message):
                return MessageAnalysis(needs_soothe=False, soothe_text="",
                                       emotion_label=None, intent="ziwei")
            # T046 姓名分析（姓名槽位 + 评价词，起名/改名动作族除外，见词表注释）
            if (_XINGMING_NEAR_RE.search(user_message)
                    and _XINGMING_VERDICT_RE.search(user_message)
                    and not _XINGMING_NAMING_RE.search(user_message)):
                return MessageAnalysis(needs_soothe=False, soothe_text="",
                                       emotion_label=None, intent="xingming")
            # T076 单人婚姻询问 → advisor（双人语境仍走 hehun；带运势锚的不
            # 在此路由内——见词表注释，T022 回归保护）
            # k38-I4（审查实测）：消息**自带完整生辰**时不得判 advisor——
            # advisor 无档案分支是固定建档引导「请提供你的出生信息：出生年月日时、
            # 出生地、性别」，用户刚说完生辰 → 自相矛盾，且 persons 零写入
            # （F2 渐进累积只在 bazi 路径跑）。改判 bazi：`_handle_bazi` 按消息
            # 排盘/建档（完整信息 → 直接排盘落档），与 F2 累积同路径。
            # 部分生辰（只给年份/年龄等）由 handler 侧 `_extract_partial_birth`
            # 兜底改判（见 handler._redirect_single_marriage），此处不重复实现
            # 部分信息提取（单一事实源）。
            # k38-RI4b（复审实测·档案污染）：自带生辰的判定改用时间谓词
            # `birth_date_candidate`（= BIRTH_DATE_PATTERN 命中 + 日期不在未来）
            # ——实测「2026年10月1日结婚，帮我看看我的婚姻」此前按日期形状判
            # bazi，婚期被当生辰排盘/建档；未来日期（婚期/预产期/行程）不得
            # 当生辰，落回 advisor（无档案走 T076 建档引导，不污染 persons）。
            if (_SINGLE_MARRIAGE_RE.search(user_message)
                    and not _SECOND_PERSON_RE.search(user_message)
                    and not _MARRIAGE_TIMELINE_RE.search(user_message)):
                if self.birth_date_candidate(user_message):
                    return MessageAnalysis(needs_soothe=False, soothe_text="",
                                           emotion_label=None, intent="bazi")
                return MessageAnalysis(needs_soothe=False, soothe_text="",
                                       emotion_label=None, intent="advisor")

        # Fast path 收紧：只有"纯生日陈述"（无任何意图词）才直接判 bazi；
        # 含意图词（适合/公司/职业/配/像谁…）即使有生日也必须走 AI 分类
        # （如"1990年5月20日…哪个公司最配"应判 career，而不是被掐成纯排盘）
        if (self.BIRTH_DATE_PATTERN.search(user_message)
                and not self.INTENT_HINT_PATTERN.search(user_message)):
            return MessageAnalysis(needs_soothe=False, soothe_text="",
                                   emotion_label=None, intent="bazi")

        # 缓存 key 包含上下文片段，避免同文案不同语境互相污染
        _ctx_tail = "".join(str(m.get("content", ""))[-60:]
                            for m in (history or [])[-4:])
        msg_hash = hashlib.md5(
            (user_message + "|ctx:" + _ctx_tail).encode()
        ).hexdigest()
        if msg_hash in self._cache:
            return self._cache[msg_hash]

        if not self.api_key:
            return MessageAnalysis(needs_soothe=False, soothe_text="",
                                   emotion_label=None, intent=None)

        try:
            user_content = user_message[:500]
            if history:
                ctx_lines = ["（最近对话上下文，仅用于辅助判断）"]
                for m in history[-6:]:
                    role = "用户" if m.get("role") == "user" else "助手"
                    content = str(m.get("content", ""))[:150]
                    ctx_lines.append(f"{role}: {content}")
                user_content = "\n".join(ctx_lines) + "\n\n当前消息: " + user_content
            messages = [
                {"role": "system", "content": COMBINED_PROMPT},
                {"role": "user", "content": user_content},
            ]
            # Bugfix: 改用 Anthropic 兼容端点 + thinking disabled（同 src/llm/client.py），
            # 避免推理模型占满 max_tokens 导致 content 为空 / 超时
            from src.llm.client import deepseek_anthropic_completion
            content = deepseek_anthropic_completion(
                self.api_key, messages, model=self.model,
                max_tokens=250, temperature=0.3, timeout=30.0,
            )
            result = self._parse_response(content)
        except Exception:
            result = MessageAnalysis(needs_soothe=False, soothe_text="",
                                     emotion_label=None, intent=None)

        self._cache[msg_hash] = result
        return result

    def _parse_response(self, content: str) -> MessageAnalysis:
        """Parse combined JSON response."""
        # 贪婪匹配最后一个 }（阶段 1 JSON 含嵌套对象 facts，非贪婪会提前截断）
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                needs = bool(data.get("needs_soothe", False))
                soothe = str(data.get("soothe_text", ""))[:300]
                emotion = data.get("emotion", "")
                emotion = str(emotion)[:20] if emotion else None
                intent = data.get("intent", "free_chat")
                valid = {"bazi", "ziwei", "liuyao", "fengshui", "zeri",
                         "mianxiang", "qimen", "xingming", "hehun", "dream",
                         "calendar", "xuetang", "advisor", "career",
                         "free_chat"}
                if intent not in valid:
                    intent = "free_chat"
                if needs and not soothe:
                    needs = False
                is_sharing = bool(data.get("is_sharing", False))
                # 阶段 1 理解升级（方案 v5）：附加需求/关键事实/缺失信息/联网需求，
                # 解析失败一律用默认值（向后兼容：旧格式 JSON 也能正常解析）
                secondary_needs = data.get("secondary_needs") or []
                if not isinstance(secondary_needs, list):
                    secondary_needs = []
                secondary_needs = [str(x)[:50] for x in secondary_needs[:5]]
                facts = data.get("facts") or {}
                if not isinstance(facts, dict):
                    facts = {}
                missing_info = data.get("missing_info") or []
                if not isinstance(missing_info, list):
                    missing_info = []
                missing_info = [str(x)[:50] for x in missing_info[:5]]
                needs_search = bool(data.get("needs_search", False))
                return MessageAnalysis(needs_soothe=needs, soothe_text=soothe,
                                       emotion_label=emotion,
                                       intent=None if intent == "free_chat" else intent,
                                       is_sharing=is_sharing,
                                       secondary_needs=secondary_needs,
                                       facts=facts,
                                       missing_info=missing_info,
                                       needs_search=needs_search)
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        return MessageAnalysis(needs_soothe=False, soothe_text="",
                               emotion_label=None, intent=None)

    def clear_cache(self):
        self._cache.clear()
