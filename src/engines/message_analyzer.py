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
- "hehun" 双人合盘/合婚: 双人合盘/合盘/八字合婚/我和TA合不合/看看我们配不配/缘分契合/婚姻匹配/合婚配对/我们俩缘分（合盘语境）→ "hehun"
- Dream description (梦见/梦到/做梦) = "dream"
- Daily fortune / today's luck requests (今日运势/今天运气/今日宜忌/今天宜忌/今日运程/今日日历/今天适合) = "calendar"
- Life advice / guidance requests (建议/怎么办/有什么建议/帮我分析/我该怎么做/给我点建议) = "advisor"
- Pure emotional expression with NO fortune-telling request = "free_chat"
- Colloquial fortune-telling: "看下命""算一下""运气怎么样" = "bazi"
- 区分规则：问"哪个公司/行业适合我"→career；问"我像哪个名人/明星"→按普通命理咨询（bazi/advisor）处理，不要承诺名人对照
- 双人合盘规则：消息同时指向两人（我/我们 + 他/她/TA）且语义为「合/配/缘分」→ "hehun"（如"我和TA合不合""看看我们配不配""我们俩缘分如何"）

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
_FORCE_META_RE = re.compile(r"哪个|区别|对比|比较|差异")
# 六爻族（与任务族 T058-T063 全量对齐，碰撞扫描零误伤）
_LIUYAO_FORCE_RE = re.compile(r"摇卦|摇一卦|起一卦|六爻|卜卦|占一卦|算卦")
# 紫微族（与任务族 T064-T069 全量对齐，碰撞扫描零误伤）
_ZIWEI_FORCE_RE = re.compile(r"排\s*紫微|紫微(?:盘|命盘|斗数)")
# 择日：意图词 + 完整日期锚双条件（T028/T038/T100 的
# 「2026年9月15日搬家 帮我选个日子」形态）；单意图词无日期锚
# （「下个月结婚 帮我选个吉日」T030-T032 已绿）留给 LLM 分类，不误伤。
_ZERI_FORCE_RE = re.compile(
    r"选(?:个|挑个)?(?:日子|时间)|择日|择吉|挑日子|看日子|吉日|换一批|重新选")
_ZERI_DATE_ANCHOR_RE = re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日")
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

    def __init__(self, api_key: str, model: str = "deepseek-v4-flash"):
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
        if scene_hint:
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
            if (_ZERI_FORCE_RE.search(user_message)
                    and _ZERI_DATE_ANCHOR_RE.search(user_message)):
                return MessageAnalysis(needs_soothe=False, soothe_text="",
                                       emotion_label=None, intent="zeri")
            if _ZIWEI_FORCE_RE.search(user_message):
                return MessageAnalysis(needs_soothe=False, soothe_text="",
                                       emotion_label=None, intent="ziwei")

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
