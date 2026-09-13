"""消息处理 - 意图识别和信息收集."""
import json
import logging
import os
import re
from datetime import date, timedelta
import time
from typing import Optional, Tuple, Any, Callable, Union

logger = logging.getLogger(__name__)

from src.engines.bazi import BaziEngine, BaziResult
from src.engines.ziwei import ZiweiEngine, ZiweiResult
from src.engines.liuyao import LiuyaoEngine, LiuyaoResult
from src.engines.fengshui import FengshuiEngine, FengshuiResult
from src.engines.mianxiang import MianxiangEngine, MianxiangResult
from src.engines.zeri import ZeriEngine, ZeriResult, ZODIAC_MAP
from src.engines.dream import DreamEngine, DreamResult
from src.engines.hehun import HehunEngine
from src.tools.hehun import format_hehun_card, split_birth_pair  # 批次 2 E1 合婚工具规则层
from src.engines.qimen import QimenEngine
from src.engines.xingming import XingmingEngine, get_stroke_count
from src.tools.naming import (format_naming_card, generate_candidates,  # 批次 2 E2 起名工具规则层
                              split_naming_params, target_elements)
from src.tools.fortune_cycle import (format_cycle_card, parse_cycle_params,  # 批次 2 E3 流月流年工具规则层
                                     parse_target_month, parse_target_year)
from src.tools.career_dir import format_career_card, parse_career_params  # 批次 2 E4 择业/方位匹配工具规则层
from src.tools.num_omen import (analyze_number, format_num_card,  # 批次 2 E5 数字吉凶工具规则层
                                parse_num_params)
from src.engines.message_analyzer import MessageAnalyzer, MessageAnalysis
# T076（k38）：单人婚姻判定词表与意图路由**同一事实源**（message_analyzer 定义，
# 本处只消费不复制——防两处词表分裂，数据一致性铁律）
from src.engines.message_analyzer import (
    _MARRIAGE_TIMELINE_RE as _SINGLE_MARRIAGE_TIMELINE_RE,
    _SECOND_PERSON_RE, _SINGLE_MARRIAGE_RE)
from src.bot.record_query import _MEMBER_PAY_WORDS  # R1-1：支付词单一事实源（与会员直读守卫同表，防分裂）
try:
    from src.engines.advisor_v2 import AdaptiveAdvisor
    HAS_ADVISOR_V2 = True
except ImportError:
    from src.engines.advisor import AdvisorEngine
    HAS_ADVISOR_V2 = False
from src.rag.retriever import Retriever, ChunkResult
from src.llm.client import FortuneLLM, AnalysisResult
from src.config import is_experience_mode
from src.security.admin import is_admin_user  # k36 A28：超管白名单判据（单一事实源）

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
from src.storage.preference_dao import PreferenceDAO, UserPreferences
from src.storage.member_dao import MemberDAO
from src.storage.member_dao import MemberDAO
from src.storage.conversation_memory import ConversationMemory
from src.storage.chart_dao import ChartDAO
from src.storage.birth_profile import (
    get_user_birth_profile, profile_fingerprint, to_solar_date)
from src.utils.cache import ResponseCache, is_cacheable
from src.ml.quality_predictor import QualityPredictor
from src.memory.user_memory import UserMemory, format_birth_line
# 命例相似度引擎已停用（2026-08-09 方案 v5 选 A 彻底移除，见 src/engines/similarity.py 注释）
from .formatter import split_long_message, format_error, format_loading
# B2-11（P1 #2 残留）：单消息降级分支（无 session_dao）的 system 提示——
# CHAT_PROMPT（JSON 工单教学）+ [可用工具清单]，与主链首轮同口径
# （B2-13：旧 _web_tool_guide_line 死导入已清理，无任何消费方）
from src.llm.prompts import CHAT_PROMPT

# AI 原生对话系统（Phase 1）— <tool_call> 标签解析与工具执行
# 意图识别已完全由 LLM 承担（_analyze_message），不再有任何硬编码关键词表。
from .tool_calls import (
    parse_tool_calls,
    parse_native_tool_use_blocks,
    strip_tool_calls,
    ToolCall,
    ToolResult,
    TOOL_REGISTRY,
    MAX_TOOL_ITERATIONS,
    SEARCH_UNAVAILABLE_HINT,
    RETRIEVAL_UNAVAILABLE_HINT,
    serialize_params,
)

# 批次 1（spec 1.1/1.2）：统一能力注册表分派（单一事实源）
from src.bot.capability_registry import (
    CAPABILITY_BY_NAME,
    CAPABILITIES,
    validate_params,
    build_tool_description,
)

# E2-1 对话消息卡片化：卡片标记生成与判定（纯函数，见 task-e2-server-brief）
from .card_mark import detect_card_type, wrap_card, strip_card_decor_for_llm

# v8 阶段 3（过程体验）：工具调用事件文案（思考路径逐步点亮）
_TOOL_EVENT_LABELS = {
    "排盘": "正在排盘…",
    "检索": "正在查阅古籍…",
    "解梦": "正在翻阅梦兆典籍…",
    "风水": "正在勘察风水…",
    "择日": "正在择吉日…",
    "查记录": "正在查你的记录…",
    "合婚": "正在合婚配对…",
    "起名": "正在斟酌名字…",
    "流月流年": "正在推演流月流年…",
    "择业": "正在分析适配行业…",
    "数字吉凶": "正在查看数字吉凶…",
}

# 批次 2 E6（工具 AI 决策通道）：工具场景 → 默认引擎兜底映射。
# process() intent=None 分支：scene_hint 请求经工具链后 LLM 未输出任何工单
# （tool_log 无 calls）→ 按此表回退既有引擎处理器（质量下限护栏：工具链
# 不触发时体验不降级；兜底回复继续走 _maybe_wrap_card / welcome / 落库流程）。
# R1-2（评测 T094/T095/T039 修复·路由漏发）：hehun/career_dir/num_omen 三
# 场景改为确定性执行对应工具（_scene_*_fallback，0 LLM）——原兜底行为：
# num_omen 无引擎（LLM 自由回复=把工具调用 JSON 当回复文本输出，T094 实锤）、
# career_dir 走 _handle_career（=重排命盘，回复是命盘卡片而非择业分析，T095
# 实锤）、hehun 走 _handle_hehun（LLM 散文回复缺「男方」回显且 L1 零调用，
# T039 实锤）。改走工具后：L1 记录真实工具调用 + 回复为结构化卡片。
# 键与 message_analyzer.TOOL_SCENE_WORDS 对齐。
# R1-3（评测 T026/T045 修复·链路工具漏发）：naming/fortune_cycle 两场景
# 改走确定性工具兜底（_scene_*_fallback，0 LLM，与 career_dir 同型）——
# 原兜底：fortune_cycle 走 _handle_advisor（LLM「AI 行动建议」散文，L1
# 期望 fortune_cycle 工具实际零调用，T026 3/3 实锤）、naming 走
# _handle_xingming（LLM 排盘卡片，naming 工具零调用，T045 3/3 实锤）。
# 改走工具后：L1 记录真实工具调用（fortune_cycle 需 birth 键 / naming 需
# surname+gender 键，与契约 partial 匹配）+ 回复为结构化卡片。
SCENE_DEFAULT_ENGINE = {
    "hehun": "_scene_hehun_fallback",
    "naming": "_scene_naming_fallback",
    "fortune_cycle": "_scene_fortune_cycle_fallback",
    "career_dir": "_scene_career_dir_fallback",
    "num_omen": "_scene_num_omen_fallback",
}

# R1-2（评测 T007 修复·工具回显泄漏）：回复层工具 schema/参数 JSON 泄漏检测。
# - _TOOL_DESC_ECHO_RE：工具说明书模板行（"排盘（bazi_chart）：…"）——
#   build_tool_description 逐字形态（中文名 + 全角括号 + 英文 cap_id）；
# - _JSON_PARAM_LEAK_RE：参数 JSON 泄漏（{"birth": …}）——回复层任何情况
#   不得出现工具参数 JSON（评测契约 neg "{"；产品侧同理，回显=半成品回复）。
_TOOL_DESC_ECHO_RE = re.compile(r'[一-龥]{2,8}（[a-z_]{2,30}）')
# k39 S3：合婚结构化工单里一方留空（"birth_b:" 无值）——键语法与
# src/tools/hehun.py `_BIRTH_KEY_RE` 同口径（半/全角冒号与等号）
_HEHUN_EMPTY_KEY_RE = re.compile(r'(?:^|\n)\s*birth_[ab]\s*[:：=＝]\s*(?=\n|$)')
_JSON_PARAM_LEAK_RE = re.compile(r'\{\s*[\'"一-龥]')

# G1 性别契约归一（male/female 兼容中文；单一事实源——引擎/纠正判定/
# 档案出生串拼装/日历排盘同源复用，见 _is_gender_correction/
# _gen_gender_correction_ack/_fmt_birth_text/_handle_calendar）
_GENDER_CN = {"男": "男", "女": "女", "male": "男", "female": "女"}

# ============================================================
# k40 返工（Critical-1 + Important-1）：口语性别词的「主语感知」判据
# ============================================================
# 判据不是「前面有没有某个称谓词」（逐词白名单关不严：妹妹/姐姐/闺蜜/太太/
# 嫂子/表妹/表姐/前女友…永远补不完），而是**句法主语**——这个性别词描述的是
# 用户本人（自述）还是第三人（他人）：
#   ① 子句句首 = 自指代词(+语气副词)+系动词 → 本人自述：「我是一个女孩」
#      「我是个女生」——量词跟在「我是」之后不构成第三人修饰（修 Important-1：
#      改前量词排除不看主语，把「我是一个女孩」的性别声明静默丢弃）；
#   ② 子句内出现第三人主语 → 性别词属第三人：
#      - 他/她/对方 + 系动词（「她说她是女孩子」）或 + 出生年（「他1990年…」）；
#      - 领属语代词 + 名词中心语（「我妹妹」「我闺蜜」「我朋友的女儿」「你老婆」
#        「他女儿」「他的女儿」）：代词后不是系动词/副词/自指词/自述数据名词，
#        而是被修饰的名词 → 主语是那个名词；
#      - 量词/指示词短语的主语归属（「一个女孩子」「那位姑娘」「这个1990年出生
#        的女孩子」「我那位1990年出生的女孩子朋友」）：其前无自述系动词（我(是)
#        一个…）即第三人——量词与性别词之间可隔名词跑（出生年/称谓）；
#      - 子句句首为名词性中心语 + 系动词（「对象是…」「相亲对象是…」）。
#   ③ 消息级：出生信息由第三人领属语引出（「我妹妹，1990年出生的女孩子」「他的
#      女儿1990年出生的女孩子」）→ 本条消息的口语性别词一律不取（归属判定的
#      「宁漏勿误」：第三人误判的代价是 P0 档案污染）。
#   ④ 自述数据名词豁免：「我(的)+数据名词」（出生信息/生辰/八字/生日/资料/命盘…）
#      的中心语是**关于我的信息**而不是另一个人 → 主语仍是本人（审查 Important：
#      改前被判第三人 → 性别纠正被静默丢弃）。
# 默认（无任何标记）保持改前口径：取作本人性别（F2 建档/裸生辰串不缩小）。
# 为什么必须排除：被当本人性别会触发 G1 纠正 → force_gender 覆写本人档案
# 性别（P0 数据风险）；T041 的 hehun 链路也是被它抢走的。
_ORAL_FEMALE_WORDS = ("女孩", "女生", "姑娘", "丫头", "女的", "小姑娘", "闺女")
_ORAL_MALE_WORDS = ("男孩", "男生", "男的", "小伙子")
# 子句边界（主语判定窗口：性别词往前到最近一个边界）
_GENDER_CLAUSE_BOUNDARY = "，,。.！!？?；;、\n"
# 语气/时间副词（可插在自指代词与系动词之间，不改变主语）
_GENDER_ADVERB = (r'(?:也|就|其实|确实|的确|现在|目前|真的|一直|本来|'
                  r'原来|原先|最近|应该|可能|大概|通常|平时)')
# 句首可出现的连词/语气词（不改变后接主句的主语）
_GENDER_CLAUSE_LEAD = (r'(?:但是|不过|而且|然而|然后|所以|因为|如果|另外|'
                       r'还有|对了|但|而|嗯|呃|话说|哦|唉)')
# 自指代词（本人）
_GENDER_SELF_PRON = r'(?:我|本人|自己|俺)'
# 领属语代词（可带 的/家 引出一个名词中心语）：本人 + **第三人代词**。
# k40 第四轮（审查 Critical-1：「他女儿/他的女儿」族）——第三人代词本身就是
# 领属语，改前代词集只有 我/你/咱/俺/您 →「他女儿是1990年出生的女孩子」
# 判不出第三人（与「我妹妹…」同型，只差一个代词）。
_GENDER_POSSESSIVE_PRON = r'(?:我|你|咱|俺|您|他|她)'
# 自述数据名词（封闭集，与 F2 建档字段族同源）：领属语的**中心语**是「关于
# 我的数据」而非「一个人」时，主语仍是本人（「我的出生信息是1990年…男」
# 「我的生辰/八字/生日/资料/命盘…」——审查 Important 实测：改前这些自述句
# 被判第三人 → 性别纠正被静默丢弃）。
# 亲缘/称谓是开放类词表（妹妹/姐姐/闺蜜/太太/嫂子/表妹/表姐/前女友…永远补
# 不完，历史教训）——这里判的不是称谓枚举，而是**中心语的语义类别**：
# 产品自有的字段名词（我的数据）≠ 另一个人。
_GENDER_SELF_DATA_NOUN = (r'(?:出生信息|出生日期|出生时间|生辰|八字|生日|资料|'
                          r'命盘|信息|档案|个人资料|基本信息)')
# 量词短语（一个/这位/几种…）
_GENDER_QTY_PHRASE = (r'(?:一|两|三|四|五|六|七|八|九|十|几|这|那|哪|每|各|'
                      r'好|多)\s*(?:个|位|名|种|些)')
# ① 本人自述主语：句首（可带连词）+ 自指代词 (+副词) + 系动词
_GENDER_SELF_HEAD_RE = re.compile(
    r'^\s*' + _GENDER_CLAUSE_LEAD + r'?\s*' + _GENDER_SELF_PRON + r'\s*'
    + _GENDER_ADVERB + r'?\s*(?:是|就是|系|为)')
# ②a 第三人主语：他/她/对方 + 系动词（「她是个女孩子」）
# k41：补**指示代词族**（这人/这个人/那人/那个人/此人/该人）——与 他/她/对方
# 同属封闭类代词，不是称谓词表（终审 §六 提取层同族「这人1990年出生的…」）。
_GENDER_THIRD_PRON = r'(?:他|她|它|对方|这人|这个人|那人|那个人|此人|该人|TA|ta)'
_GENDER_THIRD_PERSON_RE = re.compile(
    r'(?<!其)' + _GENDER_THIRD_PRON + r'\s*' + _GENDER_ADVERB + r'?\s*'
    r'(?:是|就是|系|为)')
# ②b 第三人主语 + 出生年（「他1990年…」「对方是1992年…」「对方女儿1990年…」
#    「他的女儿1990年5月20日出生」）——k41：代词与年份之间允许一个短名词跑
#    （称谓/亲属名词：女儿/老婆/对象…开放类，不枚举），仍限**同一子句内**。
_GENDER_THIRD_PERSON_BIRTH_RE = re.compile(
    r'(?<!其)' + _GENDER_THIRD_PRON + r'(?:的|是)?'
    r'[^，,。.！!？?；;、\n]{0,6}?\d{4}\s*年')
# 领属语（我/你/咱/俺/您）后不得紧跟这些（那些是自述/非名词中心语）：
#   「我(的)性别是…」「我本人是…」「我是…」「我其实…」「我1990年…」
_GENDER_LEAD_GUARD = (
    r'(?!\s*(?:的|家)?\s*(?:性别|本人|自己))'
    r'(?!\s*(?:的|家)?\s*' + _GENDER_SELF_DATA_NOUN + r')'
    r'(?!\s*' + _GENDER_ADVERB + r')'
    r'(?!\s*(?:是|就是|系|为))'
    r'(?!\s*\d)')
# 领属语（我/你/咱/俺/您/他/她）+ 名词中心语 + 谓语线索（是/的/出生/年月日）
# ——代词后不是系动词/副词/自指词/自述数据名词，而是「被修饰的名词」→ 主语
# 是那个名词：
#   我妹妹1990年… / 我闺蜜是个女生 / 我朋友的女儿1990年5月20日…
#   他女儿是1990年出生的女孩子 / 他的女儿1990年…
# 名词中心语不得以量词短语/否定/语气助词开头（「我是一个1992年出生的女孩」
# 是自述：量词紧跟自述代词，中心语位不是名词）。
_GENDER_NOUN_RUN = r'[^，,。.！!？?；;、\n了过吧呢啊嘛不]{1,12}?'
_GENDER_POSSESSIVE_HEAD_RE = re.compile(
    _GENDER_POSSESSIVE_PRON + r'(?:的|家)?' + _GENDER_LEAD_GUARD
    + r'(?!' + _GENDER_QTY_PHRASE + r')' + _GENDER_NOUN_RUN
    + r'(?:是|就是|的|出生|\d{2,4}\s*年|\d{1,2}\s*月|\d{1,2}\s*日)')
# ③ 消息级：第三人领属语引出的出生信息（「我妹妹1990年出生的女孩子」
#    「我一个朋友1990年出生的女孩子」「我妹妹，1990年出生的女孩子」）→
#    本条消息的性别词一律不取（与 ② 同一「出生信息归属」口径）。
#    两条硬约束（评测集实测防误伤，见 tests/test_k40_e6_r3.py 邻接锁）：
#    - 领属语必须**位于子句句首**（「**帮我**排1990年5月20日的盘」「**帮我**
#      排盘，1990年…」「**我想**改个名，姓李，男，1988年…」——我 是动词宾语，
#      不是领属语 → 不得命中）；
#    - 名词中心语**不跨子句**（最多跨一个逗号/顿号）：否则「我想改个名，姓李，
#      男，1988年…」会被整段吃掉。
_GENDER_CLAUSE_ANCHOR = (r'(?:^|[，,。.！!？?；;、\n])\s*'
                         + _GENDER_CLAUSE_LEAD + r'?\s*')
_GENDER_POSSESSIVE_NP = (
    _GENDER_POSSESSIVE_PRON + r'(?:的|家)?' + _GENDER_LEAD_GUARD
    + r'(?:' + _GENDER_QTY_PHRASE + r'\s*)?'
    + r'(?!' + _GENDER_QTY_PHRASE + r')(?!\d)'
    + r'[^，,。.！!？?；;、\n了过吧呢啊嘛不]{1,16}?')
_GENDER_THIRD_BIRTH_MSG_RE = re.compile(
    _GENDER_CLAUSE_ANCHOR + _GENDER_POSSESSIVE_NP
    + r'(?:\s*[，,、]\s*|\s*)\d{4}\s*年')
# ②c 名词性中心语 + 系动词（子句句首：「对象是…」「相亲对象是…」「朋友是…」）
#     ——自指代词/副词开头的子句不算（那些走 ①）
_GENDER_CLAUSE_NOUN_COPULA_RE = re.compile(
    r'^(?!\s*(?:' + _GENDER_SELF_PRON + r'|' + _GENDER_ADVERB + r'|'
    r'人家|大家|对方|他|她|它))'
    r'[^，,。.！!？?；;、\n了过吧呢啊嘛不\s]{1,4}?(?:是|就是|系|为)')
# ②d 量词/指示词短语作主语（k40 第四轮：裸量词组 + 量词与年份分离组）——
#    子句内出现「一个/这个/那个/那位/几位 [+名词跑]」且其前**不是自述系动词**
#    → 主语是这个量词短语（第三人）：
#      「这个1990年出生的女孩子」「那位1990年出生的丫头」（裸量词：无领属语，
#       改前 message 级规则以领属语为前提 → 全丢）
#      「我那位1990年出生的女孩子朋友」（量词与性别词之间隔了出生年 → 改前
#       尾部锚定规则要求量词紧邻性别词 → 漏）
#    其前是自述代词+系动词（我(是|其实|就)是 一个）→ 本人自述：
#      「我是一个女孩」「我是一个1992年出生的女孩」（修 Important-1）。
#    判据是**量词短语的主语归属**（子句句法），不是量词词表。
_GENDER_QTY_SUBJECT_RE = re.compile(
    r'(?:一|两|三|四|五|六|七|八|九|十|几|这|那|哪|每|各|好|多)\s*'
    r'(?:个|位|名|种|些)')
_GENDER_SELF_COPULA_TAIL_RE = re.compile(
    r'(?:' + _GENDER_SELF_PRON + r')(?:\s*' + _GENDER_ADVERB + r')?\s*'
    r'(?:是|就是|系|为)\s*$')

# ②e 非自述的**名词性修饰/领属短语**（k41 末族，k40 终审 §六 建议的规则级修法）：
#    子句句首是「X的N / X家N / X介绍(的)N」（X 不是自述代词，中心语 N 不是
#    自述数据名词）→ 主语是这个名词短语（第三人）。
#    改前（e45226b 起既有、三版一致）：「房东的女儿」「邻居家女儿」「朋友介绍的
#    女孩子」这类**无代词**的领属/修饰短语全漏（旧领属语规则以代词为前提）→
#    消息里的「1990年出生的女孩子」被取作本人性别 → G1 覆盖本人档案（P0 污染，
#    k40 终审 §六 e2e 实证 3 条）。
#    判据是**句法主语归属**（名词性修饰结构），不是称谓词表——房东/邻居/同事/
#    媒人/家里…是开放类，永不枚举。
#    X 的约束（防误伤本人请求，见 tests/test_k41_gender_residual.py 邻接锁）：
#      不含数字/空白/标点（「帮我排个盘：1990年…」「排个盘 1990年…」不得被当
#      名词短语）、不含自述代词（「帮我排的盘1990年…」是本人生辰串）。
_GENDER_NP_RUN = ("[^，,。.！!？?；;、\n了过吧呢啊嘛不\\d\\s：:「」『』\"']"
                  "{1,10}?")
_GENDER_MODIFIER_MARK = r'(?:的|家|介绍(?:的|来的)?|牵线(?:的)?)'
_GENDER_FOREIGN_NP_RE = re.compile(_GENDER_NP_RUN + _GENDER_MODIFIER_MARK)
# 名词短语里的自述代词（命中即不是「第三人领属语」，交回既有规则判）
_GENDER_NP_SELF_RE = re.compile(r'我|你|咱|俺|您|本人|自己|人家|大家')

# ②f 子句句首的口语性别词 + 紧跟**出生小句**（无自述代词）→ 第三人。
#    「女孩子1990年出生的，我们合不合」（k40 终审 §六 注册变体 / k41 Critical-1）
#    ——性别词是「…出生的」这个**相对/谓词小句**所描述的人，整句无本人自述 →
#    不得当本人性别。
#    形态判据（k41 修：偏移+词形覆盖）：出生谓词（出生/生的/出生的）紧跟日期/时间
#    跑——「1990年出生的」「1990年5月20日出生的」「1992年10月1日 上海出生的」；
#    而**裸出生数据元组**（年/月/日[/时间][/地点]，无出生谓词，如「1990年5月20日
#    15:30 北京」「…北京出生」）不是小句描述，是用户供给自己的出生信息（F2 裸
#    生辰串口径，评测 T048/T050 同款「…8:00 北京出生」）→ 不判第三人（②g/默认）。
_GENDER_BIRTH_DATE_RUN = (
    r'(?:\d{4}\s*年(?:\s*\d{1,2}\s*月)?(?:\s*\d{1,2}\s*[日号])?'
    r'|\d{1,2}\s*月(?:\s*\d{1,2}\s*[日号])?)'
    r'(?:\s*\d{1,2}\s*[:：点]\s*\d{0,2})?')
# 日期与出生谓词之间的短跑（地点/修饰；不含自述/第三人代词与出生谓词本身）
_GENDER_BIRTH_SHORT_RUN = (r'[^\s\d，,。.！!？?；;、\n我你他她咱俺您它出生]{0,8}?')
_GENDER_BIRTH_CLAUSE_TAIL_RE = re.compile(
    r'\s*[，,]?\s*(?:是)?\s*' + _GENDER_BIRTH_DATE_RUN
    + r'(?:' + _GENDER_BIRTH_SHORT_RUN + r'(?:出生的|生的)'
    + r'|\s*(?:出生|生的)' + r')')
# ②g 兜底出口（k41）：裸出生数据供给——年/月/日[/时间] + 若干**短数据 token**
#    （时辰/地点/出生谓词，空白或逗号分隔，每条 ≤4 字、不含自述/第三人代词）
#    + 其后**不再有其他内容**。
#    用于区分「纯供给」（本人自述，如「女的 1990年5月20日 15:30 北京」、
#    评测 T045「…2019年3月15日 午时 北京出生」）与「供给 + 其他内容」
#    （「…北京，我们合不合」——结构不可辨，见 `_gender_ambiguous_word`）。
#    贪心匹配（非 lazy）：尽量把数据 token 吃完，剩下的才是「其他内容」；
#    长于 4 字的串（「帮我排个盘」「我们合不合」）不是数据 token → 落入「其他
#    内容」→ 触发歧义确认（宁可问一句，不静默改写/丢弃）。
_GENDER_SUPPLY_TOKEN = (r'(?:\d{1,2}\s*[:：]\s*\d{1,2}'
                        r'|[^\s，,。.！!？?；;、\n我你他她咱俺您它]{1,4})')
_GENDER_BARE_SUPPLY_RE = re.compile(
    r'\s*[，,]?\s*(?:是)?\s*' + _GENDER_BIRTH_DATE_RUN
    + r'(?:[，,]?\s*' + _GENDER_SUPPLY_TOKEN + r')?'          # 首 token
    + r'(?:[\s，,、]+' + _GENDER_SUPPLY_TOKEN + r')*')        # 后续 token：**必须**有分隔符


def _gender_clause_head(msg: str, pos: int) -> str:
    """性别词 `pos` 之前的**子句头**（往前到最近一个子句边界；上限 64 字）。

    主语判定的窗口单一实现（② 组各规则与 ②f/②g 共用；k41 抽出）。
    """
    floor = max(0, pos - 64)
    for i in range(pos - 1, floor - 1, -1):
        if msg[i] in _GENDER_CLAUSE_BOUNDARY:
            return msg[i + 1:pos]
    return msg[0:pos]


def _gender_birth_tail_kind(msg: str, tail_start: int) -> str:
    """性别词之后的出生信息形态（k41 ②f/②g 的**形态判据**，非词表）：

      ""       无出生数据（或元组之后还有其他内容且非数据形态）
      "clause" 出生小句/谓词小句（…年[/月[/日]][时间][短跑]出生的|生的|出生）
               → 性别词是这个小句描述的人 → 第三人（②f）
      "supply" **裸**出生数据供给（年/月/日[/时间][/地点][/出生谓词] 且其后
               无其他内容）→ 用户供给自己的出生信息 → 本人（F2 裸生辰串口径）
      "mixed"  裸出生数据元组 + 元组之后**还有其他内容** → 本人口语自述与
               第三人领属**结构不可辨**（②g 兜底出口：询问确认）
    """
    tail = msg[tail_start:]
    if _GENDER_BIRTH_CLAUSE_TAIL_RE.match(tail):
        return "clause"
    m = _GENDER_BARE_SUPPLY_RE.match(tail)
    if not m:
        return ""
    if not re.search(r'[0-9A-Za-z一-鿿]', tail[m.end():]):
        return "supply"
    return "mixed"


def _gender_word_subject(msg: str, pos: int, word: str = "") -> str:
    """性别词出现在 `pos` 处时，它的**主语**是本人还是第三人（k40 返工 + k41）。

    返回 "self"（本人自述）/ "third"（指代第三人）/ "ambiguous"（②g 结构不可辨，
    见 `_gender_ambiguous_word`）。判据见模块级注释（①②③组规则 + k41 的
    ②e/②f/②g，句法主语而非称谓词表）。异常一律回落 "self"（保持改前口径，
    不因判据异常改变用户可见行为）。

    `word`（可选）= 该位置上的口语性别词（k41 ②e 需要判「性别词中心语」形态：
    「朋友介绍的女孩子」的名词短语中心语就是性别词本身）。
    """
    try:
        head = _gender_clause_head(msg, pos)
        # ② 第三人主语优先（P0 宁漏勿误：有第三人主体即不得当本人性别）
        if _GENDER_THIRD_PERSON_RE.search(head):
            return "third"
        if _GENDER_THIRD_PERSON_BIRTH_RE.search(head):
            return "third"
        if _GENDER_POSSESSIVE_HEAD_RE.search(head):
            return "third"
        if _GENDER_CLAUSE_NOUN_COPULA_RE.match(head):
            return "third"
        # ②e 非自述名词性修饰/领属短语（k41 末族：「房东的女儿」「邻居家女儿」
        # 「朋友介绍的女孩子」——无代词，旧领属语规则以代词为前提 → 全漏）。
        # 中心语可以是短语自带的名词（「房东的女儿1990年…女孩子」）或**性别词
        # 本身**（「朋友介绍的女孩子」）——故把 word 拼进判定串再匹配。
        # k41 修：修饰/领属标记必须落在**性别词之前**（`_m.end() <= len(head)`）
        # ——否则「女的 1990年5月20日 15:30 北京」这类裸口语性别词会被当成
        # 「X的N」（run「女」+ mark「的」）误判第三人（Important-1 回归的第二条
        # 路径：②e），本人自述被静默丢弃。
        if head or word:
            _m = _GENDER_FOREIGN_NP_RE.match(head + word)
            if (_m and _m.end() <= len(head)
                    and not _GENDER_NP_SELF_RE.search(_m.group(0))):
                return "third"
        # ②d 量词/指示词短语的主语归属（取**最靠近性别词**的一个）：其前是
        # 自述代词+系动词（我(是)一个…）→ 自述（「我是一个女孩」）；否则
        # 主语是这个量词短语 → 第三人（「这个1990年出生的女孩子」「我和一个
        # …女孩子」「我那位1990年出生的女孩子朋友」）。量词与性别词之间可隔
        # 名词跑（出生年/称谓），不再是尾部紧邻锚定。
        _qt = None
        for _m in _GENDER_QTY_SUBJECT_RE.finditer(head):
            _qt = _m
        if _qt and not _GENDER_SELF_COPULA_TAIL_RE.search(head[:_qt.start()]):
            return "third"
        # ②f 子句句首的口语性别词 + 紧跟**出生小句**（k41：终审 §六 注册变体
        # 「女孩子1990年出生的」；Critical-1 = 词形覆盖「女孩子/男孩子」+ 偏移）
        # → 第三人；②g 裸数据元组 + 其他内容 → 结构不可辨（询问确认）。
        if word and not head.strip():
            _kind = _gender_birth_tail_kind(msg, pos + len(word))
            if _kind == "clause":
                return "third"
            if _kind == "mixed":
                return "ambiguous"
        return "self"
    except Exception:
        return "self"


def _gender_oral_words_in(msg: str):
    """消息里所有口语性别词的出现（位置，词）——供主语判定与守卫共用。

    k41 修（Critical-1 的**词形覆盖**）：产出**最大词形**——
      ① 后缀形态合并：「女孩子/男孩子/女孩儿/男孩儿」是词表词 + 子/儿（词法，
         不是新词表词）：命中「女孩」时若其后紧跟 子/儿，token 延到「女孩子」，
         否则 `pos+len(word)` 偏移落在「子」上 → ②f 整体失效（Critical-1 根因）；
      ② 子串去重（最长匹配优先）：「小姑娘」不再额外产出「姑娘」子串——否则同一
         位置既有 third（小姑娘）又有 self（姑娘），守卫/提取层按「任一处 self」
         放行 → P0 漏网。判据仍是词表内词形，不新增称谓词表。
    """
    _toks = []
    for w in _ORAL_FEMALE_WORDS + _ORAL_MALE_WORDS:
        for m in re.finditer(re.escape(w), msg):
            word = w
            nxt = msg[m.start() + len(w):m.start() + len(w) + 1]
            if w.endswith("孩") and nxt in ("子", "儿"):
                word = w + nxt
            _toks.append((m.start(), word))
    for pos, word in _toks:
        end = pos + len(word)
        if any(o_pos <= pos and o_pos + len(o_word) >= end
               and (o_pos, o_word) != (pos, word)
               for o_pos, o_word in _toks):
            continue          # 被更长的词形覆盖 → 不重复产出
        yield pos, word


def _gender_ambiguous_word(msg: str) -> str:
    """②g 兜底出口（k41）：消息里**结构不可辨**的口语性别词（无 → ""）。

    触发条件（三条同时成立；判据是句法结构，不涉及词表）：
      ① 口语性别词位于**子句句首**（`_gender_clause_head` 为空——与 ②f 同一
         位置条件；消息里若有自述//第三人主语则不属本类）；
      ② 其后紧跟**裸出生数据元组**（年/月/日[/时间][/地点][/出生谓词]），
         不是 ②f 的出生小句（「…出生的」判第三人，无歧义）；
      ③ 元组之后**还有其他内容**（不是纯建档供给——纯供给按 F2 裸生辰串口径
         判本人，见 Important-1「女的 1990年5月20日 15:30 北京」必须仍纠正）。

    本类里「本人口语自述」（「女孩子 1990年5月20日 15:30 北京，帮我排个盘」）
    与「第三人领属」（同句作合婚对方描述）**同形同序**，任何一侧硬判都要付
    反向代价（改写 = P0 档案污染；丢弃 = 本人自述被静默忽略）→ 交给调用方
    回一句**询问确认**（不静默改写、也不静默丢弃，k41 控制方口径的兜底出口）。
    """
    try:
        _amb = ""
        for pos, word in _gender_oral_words_in(msg):
            verdict = _gender_word_subject(msg, pos, word)
            if verdict == "self":
                return ""            # 消息含自述 → 不是「全不可辨」
            if verdict == "ambiguous":
                _amb = word
        return _amb
    except Exception:
        return ""


def _gen_gender_confirm_ask(word: str) -> str:
    """②g 询问确认文案（确定性、零 LLM）：不静默改写、也不静默丢弃。

    引导用户用**自述句式**（「我是{word}」——① 组规则直接生效）复述，
    因此无需跨轮状态：下一轮照常走 G1 纠正（重排 + 双写档案 + 回执）。
    """
    return (f"（另外确认一下：这条消息里的「{word}」说的是您本人吗？"
            f"如果是您本人，回我一句「我是{word}」，我马上按这个性别重新排盘；"
            f"如果指对方，忽略这句就好。）")


# ============================================================
# R1-1（评测 T087/T088 修复）：支付守卫词（单一事实源）
# ============================================================
# 精确词：整句等值命中即支付意图（保持原行为不变）。
# 自然支付句式（"开会员多少钱"/"帮我充一下会员"）由子串判定补足：
#   含"会员" 且 命中 _MEMBER_PAY_WORDS（与 record_query 会员直读守卫同表，
#   数据一致性铁律——支付意图判定只有一个事实源）→ 支付守卫优先于建档
#   引导/意图分析，绝不被 _handle_advisor 的建档引导覆盖。
_MEMBER_GUARD_EXACT = ("会员", "升级", "付费", "套餐", "价格", "多少钱", "续费")

# ============================================================
# R1-1（评测 T096 修复·跨用户污染）：本人运势类问句确定性判定
# ============================================================
# 命中即强制 bazi 意图（防 LLM 意图漂移到 free_chat/advisor 时用会话
# 上下文里他人出生信息冒充本人——B3-1 在线实锤"根据你1976年5月13日
# 出生的信息"）。强制后走 _handle_bazi 的 B3-1 规则1（档案优先：默认命主
# 完整出生信息直接使用，不被他人盘覆盖）；无档案 → F2 渐进式建档引导
# （与 bazi 意图行为一致，不回归建档）。
# 刻意不收时间锚/维度锚（流年/流月/明年/今年/今日/财运 等）——那些走
# fortune_cycle 工具链 / calendar 意图 / 场景路由，不被本守卫劫持。
_SELF_FORTUNE_RE = re.compile(
    r"(?:我的|本人|自己的|我最近的)(?:运势|运气|运程)|"
    r"(?:帮我看看|看看我的)(?:运势|运气|运程)")

# ============================================================
# 择吉日（Task 2）: 7 场景同义词表 + 意图词表（双条件判定）
# ============================================================
ZERI_SCENE_SYNONYMS = {
    "嫁娶": ["嫁娶", "结婚", "婚礼", "订婚"],
    "搬家": ["搬家", "入宅", "乔迁"],
    "开业": ["开业", "开张", "开店"],
    "晋升": ["晋升", "升职", "加薪", "升迁", "竞聘", "述职", "入职", "求职", "面试", "谈薪", "升官"],
    "出行": ["出行", "旅游", "出差"],
    "提车": ["提车", "买车", "购车"],
    "签约": ["签约", "签合同", "过户"],
}
ZERI_INTENT_WORDS = ["选日子", "选个日子", "择日", "哪天", "吉日", "挑个时间", "好日子",
                     "换一批", "重新选", "还有别的日子吗"]
ZERI_SCENE_QUESTION = "您是给哪件事选日子？搬家/嫁娶/开业/晋升/出行/提车/签约？"

# Task 3（思考步骤渐进展示）：思考步骤文案已迁移到各 _do_*/_handle_*
# 的真实工作里程碑处（见各处的 _emit_stream_event 调用）——首条在开工时
# 发出（用户有"开始处理"反馈），之后每完成一步真实工作推进一步。
# 不再有 INTENT_THINKING_STEPS 预置字典（旧实现：开工前一个事件循环全部打光）。
# FAISS 语义检索（生产主路径）：276 万条古籍向量库（bge-m3 1024 维，
# inner_product）。检索工具只走 FAISS；不可用/无结果时走 LLM 自然对话兜底，
# 不降级关键词检索（见 _tool_search）。
from src.rag.faiss_retriever import get_faiss_retriever
from src.book_categories import ref_text, ref_title

# 阶段 5·来源体系与引用校验（方案 §3.0/§3.2 ③）：四类来源统一角标 +
# 回答后校验（不相关引用剔除）；网络检索（智谱 Web Search，可用才宣传）
from src.rag.citation import make_citation, verify_citations, public_citation, type_label
from src.rag.web_search import search_web, web_search_available
# k11b 联网搜索语义触发：触发判定 + 来源痕迹确定性尾注（纯函数模块，无网络依赖；
# 判定=实体层/时效层/研究白名单正信号 − 金融排除 − 命理本地锚，见模块注释）
from src.rag.search_trigger import append_source_trace
from src.rag.search_trigger import decide_search as _decide_search

# 方案 B·引擎结果注入（AI 原生统一）：_handle_* 分析回复的系统尾巴
# （反馈提示/版本页脚）——润色时先剥离、润色后原样回接，防 LLM 改写
# v2026-08-17：去 emoji（PM 反馈回复 emoji 过多显 low），反馈字词保留
# （_handle_feedback 仍识别「准/不准」文本）
_FEEDBACK_PROMPT = "———\n这个分析对你有帮助吗？可回复「准」或「不准」告诉我"
# 旧版 emoji 反馈尾（_add_feedback_prompt 曾输出此格式；润色剥离/回接兼容）
_FEEDBACK_PROMPT_EMOJI = "———\n💬 这个分析对你有帮助吗？👍 有帮助  👎 不太准"

# ============================================================
# k11b 联网搜索语义触发：引擎意图域自动检索的注入/降级文案（确定性，单一事实源；
# 禁裸 JSON/工具标签——注入内容全为纯文本，走 k11 stream-guard scrub 出口）
# ============================================================
# 检索命中的使用要求段（extra_hint 追加；来源痕迹要求 LLM 执行 + 回复层尾注兜底）
_GROUND_USE_REQUIREMENT = (
    "（上述检索结果使用要求：回答中涉及该外部实体/事实的信息（背景/主营业务/规模/"
    "口碑/新闻等）须以上述检索结果为准，禁止凭记忆编造；引用检索信息处标注 [n]；"
    "回复末尾用一行注明信息来源（格式如「（信息来源：以上为网络公开搜索结果，"
    "仅供参考，具体请以官方渠道为准）」）。若检索结果为空或与问题无关：如实告诉用户"
    "你已帮忙联网查过但公开信息有限，建议以官方渠道为准（检索结果含官网/百科链接时"
    "给出）；不要叫用户自己去查证，也不要编造。)")
# 频控超限降级注（不静默不甩锅）
_GROUND_RATE_NOTE = (
    "（联网查询较频繁：本轮未发起新的联网检索）若用户问的是外部实体/事实的最新"
    "公开信息，如实告诉用户「稍等一下我再帮你查」，不要编造外部事实，也不要让用户"
    "自己去查证。)")
# 检索空/失败降级注（graceful degrade，非「自己查」甩锅）
_GROUND_EMPTY_NOTE = (
    "（系统已尝试联网检索该话题，公开渠道未找到有效结果或通道暂不可用）回答时如实"
    "向用户说明：已帮忙查过公开信息但比较有限/查无结果，建议以官方渠道为准（若检索"
    "结果里有官网/百科类链接则给出）；不要凭记忆编造外部事实，也不要推诿让用户自己"
    "去查证。)"
)


class _FaissChunk:
    """FaissRetriever 返回的 dict → ChunkResult 兼容对象（解梦引擎按 r.text 取用）。

    设计文档阶段 0：276 万 FAISS 为生产主路径检索器；本地 Retriever(27k)
    与 FaissRetriever 返回结构不同，解梦等内部检索经此适配统一。
    """

    # k24 补丁：补 title —— FAISS 结果里 title 是书名（source 只是语料 slug，
    # 如 daizhige），漏掉它则引用只能显示 slug 或「未知」。
    __slots__ = ("text", "source", "title", "score", "category")

    def __init__(self, d: dict):
        self.text = d.get("text") or ""
        self.title = d.get("title") or ""
        self.source = d.get("source") or d.get("title") or ""
        self.score = float(d.get("score") or 0.0)
        self.category = d.get("category") or ""


class _ChunkSearchAdapter:
    """FAISS dict 结果 → ChunkResult 风格对象（.search 接口兼容）。

    v2026-08-17 修复：转发 **kw（expand/rerank 等检索参数此前被静默丢弃——
    调用方传 expand=False/rerank=False 不生效，仍走 LLM 扩展+精排全管线）。
    """

    def __init__(self, faiss_retriever):
        self._fr = faiss_retriever

    def search(self, query: str, top_k: int = 5, **kw):
        return [_FaissChunk(d) for d in self._fr.search(query, top_k=top_k, **kw)]


def _ref_title_text(ref) -> tuple:
    """检索结果 → (出处, 正文)，兼容 dict 与对象两种形态（k24）。

    生产上同一条检索链会返回两类形态：FAISS 路径的 `_FaissChunk`/dict 与
    legacy 路径的 `ChunkResult`。只认 dict 的消费点会把对象形态整体丢弃 →
    「检索到了但等于没检索」。

    k24 补丁：实现收敛到 src/book_categories.py 的读取契约（ref_title/ref_text），
    本函数只做组合。此前两支优先级不一致（dict 支 title→source、对象支
    source→title），且下游还有消费点读了两边都不存在的 `.content` 字段导致
    P0 崩溃 —— 统一到一个实现后这类分叉不可能再出现。
    """
    return ref_title(ref), ref_text(ref)


def _yi_ji_render_lines(yi, ji) -> list:
    """宜/忌展示行（k26）：列表为空 → **整行不渲染**，宜/忌各自独立判断。

    2026-02-10 实测择吉忌列表为空（哨兵过滤 + K3-A3 神煞级消解后）→ 旧代码
    输出悬空的「忌：」。不新造文案（「诸事不宜」等属产品项，留台账待拍板）。
    调用方：计划路径卡片、chat 择日正文、引擎来源引用卡（三处同一实现）。
    """
    lines = []
    if yi:
        lines.append(f"宜：{'、'.join(yi)}")
    if ji:
        lines.append(f"忌：{'、'.join(ji)}")
    return lines


# L2 会话增量摘要（方案 §5.4）— 触发式滚动压缩，<summary>+<memories>
from src.bot.memory_compactor import MemoryCompactor


# ============================================================
# L1 滚动窗口（方案 §5.3）— 按 token 预算动态计算轮数
# ============================================================

# 检索结果注入的预留份额（token）
RETRIEVAL_RESERVE_TOKENS = 600


def estimate_tokens(text: str) -> int:
    """中文为主的消息 token 估算（方案 §5.3：字数/1.5 或字符数/4×1.2）。

    - CJK/全角字符：1 token ≈ 1.5 字（字数/1.5）
    - ASCII：1 token ≈ 4 字符（字符数/4×1.2 近似）
    - 空文本返回 0
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if ord(ch) > 0x2E80)
    ascii_n = len(text) - cjk
    return max(1, int(cjk / 1.5 + ascii_n / 4.0) + 1)


def _assemble_context(
    history: list,
    profile: str = "",
    summary: str = "",
    current: str = "",
    key_facts: tuple = (),
    window_limit: int = 16384,
    output_reserve: int = 4096,
) -> list:
    """L1 上下文组装（方案 §5.2/§5.3）——按 token 预算动态计算保留轮数。

    预算 = 窗口上限 - 输出预留 - 系统指令份额(计入 profile/summary) - 检索预留；
    从最新往回填完整轮次（预算内尽量多）；当前消息保底保留。

    **关键事实保底**（§5.3）：八字/用户明确关键陈述（工作/感情状态，L3 标记
    is_key）无论多旧都保留原文——以 system 消息注入，不进压缩。

    Args:
        history: [{role, content}, ...] 按时间正序的完整历史（含当前消息）
        profile: 用户画像摘要（L3 → 组装时注入）
        summary: L2 会话摘要（早期对话摘要）
        current: 当前用户消息（保底保留）
        key_facts: 关键事实原文列表（L3 is_key 条目 + 八字）
        window_limit: 上下文窗口上限（token）
        output_reserve: 输出预留（token）

    Returns:
        组装后的消息列表（system 前置 + 时间正序的历史轮次）
    """
    if not history:
        return []
    # 预算 = 窗口上限 - 输出预留 - 画像/摘要/检索份额
    used_overhead = (estimate_tokens(profile) + estimate_tokens(summary)
                     + RETRIEVAL_RESERVE_TOKENS)
    budget = max(128, window_limit - output_reserve - used_overhead)

    order: list = []
    used = 0
    for i in range(len(history) - 1, -1, -1):
        m = history[i]
        t = estimate_tokens(m.get("content", "") or "")
        if used + t > budget and order:
            break
        if used + t > budget and not order:
            # 预算极小：当前消息（L0）保底保留；同轮助手回复一并保留
            order.append(m)
            used += t
            if i - 1 >= 0 and history[i - 1].get("role") == "assistant":
                order.append(history[i - 1])
                used += estimate_tokens(history[i - 1].get("content", "") or "")
            break
        order.append(m)
        used += t

    kept = list(reversed(order))
    msgs: list = []
    if key_facts:
        msgs.append({"role": "system",
                     "content": "[长期记忆·关键事实]\n" + "\n".join(key_facts)})
    if profile:
        msgs.append({"role": "system", "content": "[用户画像]\n" + profile})
    if summary:
        msgs.append({"role": "system", "content": "[早前对话摘要]\n" + summary})
    msgs.extend(kept)
    return msgs


# L3 关键事件捕获规则（方案 §5.5：用户明确关键陈述 → event 条目，TTL 过期）
# 格式: (正则, 类型, 内容模板, ttl_days, is_key)
_KEY_EVENT_RULES = [
    (r"正在找工作|找工作|求职中|准备面试|在面试", "event", "用户正在找工作/求职中", 90, True),
    (r"换工作|跳槽|辞职|离职|被裁|裁员", "event", "用户近期换工作/跳槽", 90, True),
    (r"分手了|分手|离婚|被甩", "event", "用户经历分手/离婚", 180, True),
    (r"结婚了|结婚|订婚|领证", "event", "用户结婚/订婚", 180, True),
    (r"怀孕|备孕|要生宝宝", "event", "用户备孕/怀孕", 365, True),
    (r"住院|手术|确诊|大病", "event", "用户健康相关关键事件", 90, True),
    (r"买房|买房子|买房了", "event", "用户买房", 180, False),
    (r"搬家", "event", "用户搬家", 90, False),
]

# 自伤/自杀信号（合规审计留痕：self_harm_referral）
_SELF_HARM_RE = re.compile(r"自杀|自伤|轻生|不想活|活不下去|想死|结束生命")

# v1.2 建议卡片（suggestions）触发：用户最后一条消息是提问时生成 2-3 个追问。
# 宽松启发式（仅决定"要不要生成"，不参与回复内容；误触发只多一次小 LLM 调用）。
_QUESTION_WORDS = ("什么", "怎么", "如何", "哪", "多少", "能否", "能不能",
                   "要不要", "会不会", "是不是", "该怎么办", "建议", "吗", "呢")


def is_question(text: str) -> bool:
    """用户消息是否提问（建议卡片触发条件）。"""
    t = (text or "").strip()
    if not t:
        return False
    if "?" in t or "？" in t:
        return True
    if t[-1] in "吗呢么":
        return True
    return any(kw in t for kw in _QUESTION_WORDS)


# 八字信息提取
# 时辰 → 小时映射
# R1-3（T045 校准修复·午时时柱错）：午时 11 → 12（取时辰中点而非起点）。
# 根因：11 点（午时块起点）经真太阳时修正（北京 ≈ -23 分）落回巳时块
# （10:37 → 癸巳时），校准锚点「2019-03-15 午时 → 甲午时」被打破；12 点
# 修正后 ≈11:37 仍在午时块（甲午时）。与 src/api/birth_contract.py 的
# SHICHEN_TO_HOUR[6] 同步改（数据一致性铁律：同一口径一个事实源）。
CHINESE_HOUR_MAP = {
    "子时": 23, "丑时": 1, "寅时": 3, "卯时": 5, "辰时": 7,
    "巳时": 9, "午时": 12, "未时": 13, "申时": 15, "酉时": 17,
    "戌时": 19, "亥时": 21,
    "子": 23, "丑": 1, "寅": 3, "卯": 5, "辰": 7,
    "巳": 9, "午": 12, "未": 13, "申": 15, "酉": 17, "戌": 19, "亥": 21,
}

# L5-2（降级成本）：日主天干 → 五行（降级链路精简文案的规则要点用）
_DM_WUXING = {"甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
              "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水"}

# 中文数字 → 阿拉伯数字
_CN_NUMS = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "正": 1, "冬": 11, "腊": 12,
}
_CN_TENS = {"二十": 20, "廿": 20, "三十": 30, "卅": 30, "十": 10}

def _parse_cn_num(s):
    """Parse Chinese number string to int. 正月→1, 三月→3, 十三→13, 二十→20, 廿五→25, 三十→30"""
    s = s.strip()
    if s.isdigit():
        return int(s)
    for ten_str, ten_val in _CN_TENS.items():
        if s.startswith(ten_str):
            rest = s[len(ten_str):]
            if not rest:
                return ten_val
            if rest in _CN_NUMS:
                return ten_val + _CN_NUMS[rest]
    if s in _CN_NUMS:
        return _CN_NUMS[s]
    return None

def _parse_cn_month_day(text):
    """Try to parse Chinese lunar date like 三月初三, 六月十八, 冬月十一, 腊月廿五.

    D7（2026-08-24 生产实测）：口语长句"阴历三月28出生"——中文数字月 +
    阿拉伯数字日——此前不命中 → 排盘整体放弃。现支持：
    - 阿拉伯数字日：三月28、三月初3、三月28日
    - 闰月：闰三月28 → 返回负月（-3），调用方按 lunar-python 闰月口径转阳历
    Returns (month, day) or None；month 为负表示闰月。"""
    m = re.search(
        r'(闰)?(正月|一月|二月|三月|四月|五月|六月|七月|八月|九月|十月|'
        r'冬月|十一月|腊月|十二月|'
        r'正|一|二|三|四|五|六|七|八|九|十|冬|腊)'
        r'\s*月\s*'
        r'(初[一二三四五六七八九十]|'
        r'[一二二两三三四四五五六六七七八八九九]?十[一二三四五六七八九]?|'
        r'二十|廿[一二三四五六七八九]?|三十|卅十?|'
        r'零[一二三四五六七八九]|'
        r'[一二三四五六七八九]|\d{1,2})'
        r'\s*[日号]?',
        text
    )
    if m:
        month_str = m.group(2)
        day_str = m.group(3)
        # Parse month（闰月 → 负值，lunar-python 闰月口径）
        month_map = {"正": 1, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                     "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
                     "冬": 11, "腊": 12}
        month = month_map.get(month_str[0])
        if month is None:
            month = month_map.get(month_str.replace("月",""))
        if m.group(1):
            month = -month
        # Parse day（阿拉伯数字日：直接 int；中文数字走 _parse_cn_num）
        if day_str.isdigit():
            day = int(day_str)
        elif "初" in day_str:
            day = _parse_cn_num(day_str.replace("初", ""))
        else:
            day = _parse_cn_num(day_str)
        if month and day and abs(month) <= 12 and 1 <= day <= 31:
            return month, day
    return None

# ── F2 渐进式出生信息累积：中文数字年 + 年龄→年份推算 ──────────────
# 中文数字年份逐字转阿拉伯（"一九七六"→1976，"七六"→76；〇/零 均可）
_CN_YEAR_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
                   "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

def _cn_year_to_int(s: str) -> Optional[int]:
    """逐字转换中文数字年份字符串（不含十/百/千位字，个位"十"不出现）。"""
    n = 0
    for ch in s:
        if ch not in _CN_YEAR_DIGITS:
            return None
        n = n * 10 + _CN_YEAR_DIGITS[ch]
    return n

# 年龄命中：① 前缀词（今年/现在/已经/都/满/虚岁）引导 → "岁" 可省（"现在50"）；
# ② 无前缀 → 必须带 "岁"（"50岁"）。数字后紧跟时间/日期单位词的排除——
# "现在3点""都已经3点了"是时间不是年龄（"今年是几几年"无数字不命中）。
_AGE_PATTERNS = (
    re.compile(r'(?:今年|现在|已经|都|满|虚岁)\s*(\d{1,3})(?:\s*岁)?'),
    re.compile(r'(\d{1,3})\s*岁'),
)
# k33/A16：单位守卫补 小/号/刻——"已经3小时了"/"现在10号了"/"3刻钟" 是时间
# 不是年龄（原守卫只挡 点时：:分月日年个天周万，"3小时"会被误推成 3 岁 → 年份
# 推算 cy-3，用户看到完全错误的出生年）。
_AGE_UNIT_GUARD = set("点时：:分月日年个天周万小号刻")

def _extract_age(msg: str) -> Optional[int]:
    """提取消息中的年龄（仅阿拉伯数字，如"50岁"；不支持"五十岁"中文数字）。

    范围校验 1-130，超界不取；多条年龄命中取最后一条（最新者胜，与合并语义一致）。
    """
    best = None
    for pat in _AGE_PATTERNS:
        for m in pat.finditer(msg):
            age = int(m.group(1))
            if not (1 <= age <= 130):
                continue
            j = m.end()
            while j < len(msg) and msg[j].isspace():
                j += 1
            if j < len(msg) and msg[j] in _AGE_UNIT_GUARD:
                continue  # 数字后跟时间/日期单位 → 非年龄（"3点""2个小时"）
            if best is None or m.start() >= best[0]:
                best = (m.start(), age)
    return best[1] if best else None

# k33/A16：时辰回显的「以后/之后/过后」锚定——只在【时间表达紧邻之后】识别，
# 防别的日期后缀污染时辰回显（"我5月13日以后出生，10点" 原实现全串 search →
# 回显成"10点以后"，把用户没说的时间限定词硬塞回去）。
# 时间表达 = 数字点/时/冒号形态（可带分或"刻"）或时辰字。
_TIME_TOKEN_FOR_SUFFIX = (
    r'\d{1,2}\s*[点时:：]\s*(?:\d{0,2}\s*分?|刻)?|[子丑寅卯辰巳午未申酉戌亥]时')
_TIME_SUFFIX_ANCHOR_RE = re.compile(
    rf'(?:{_TIME_TOKEN_FOR_SUFFIX})\s*(?:以后|之后|过后)')


def _format_partial_echo(known: dict, msg: str) -> str:
    """F2：把已确认的部分出生信息拼成回显一句（信息不丢即可）。

    - year → "出生于1976年（按50岁周岁推算）"（年龄推算带说明；直接报年份的不加）
    - month+day → "5月13日"（农历闰月 → "闰3月28日"）
    - hour+minute → "10点以后"（原文含 以后/之后/过后 保留"以后"，否则"10点"；
      时辰字 → "子时"）
    - city/gender → "榆树市" / "男"
    """
    parts: list = []
    if known.get("year"):
        y = known["year"]
        if known.get("_age_used"):
            parts.append(f"出生于{y}年（按{known['_age_used']}岁周岁推算）")
        else:
            parts.append(f"出生于{y}年")
    if known.get("month") and known.get("day"):
        m, d = known["month"], known["day"]
        if m < 0:
            parts.append(f"闰{abs(m)}月{d}日")
        else:
            parts.append(f"{m}月{d}日")
    if "hour" in known:
        shichen = re.search(r'([子丑寅卯辰巳午未申酉戌亥])时', msg)
        if shichen:
            parts.append(f"{shichen.group(1)}时")
        else:
            t = f"{known['hour']}点"
            if known.get("minute"):
                t += f"{known['minute']}分"
            if _TIME_SUFFIX_ANCHOR_RE.search(msg):
                t += "以后"
            parts.append(t)
    if known.get("city"):
        parts.append(known["city"])
    if known.get("gender"):
        parts.append(known["gender"])
    return "、".join(parts)

# 时间描述 → 小时：凌晨3点→3, 早上6点→6, 中午12点→12, 下午3点→15, 晚上8点→20, 夜里23点→23
_TIME_ADJUST = {
    "凌晨": 0, "早上": 0, "早晨": 0, "上午": 0,
    "中午": 0, "正午": 0,
    "下午": 12, "傍晚": 12, "黄昏": 12,
    "晚上": 12, "夜里": 12, "夜间": 12, "半夜": 12,
}

def _parse_chinese_hour(time_str: str) -> Optional[int]:
    """Parse Chinese time period (时辰) to hour."""
    for name, hour in CHINESE_HOUR_MAP.items():
        if name in time_str:
            return hour
    return None

BAZI_EXTRACT_PATTERNS = [
    # 1990年5月20日 15点30分 北京 男
    r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日.*?(\d{1,2})\s*[点时:：]\s*(\d{0,2}).*?([男女])',
    # 1990-05-20 15:00 北京 男
    r'(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2}).*?([男女])',
    # [男女]，1989年12月11日子时，广东
    r'([男女])\s*[,，]\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日.*?([子丑寅卯辰巳午未申酉戌亥])时',
    # 1989年12月11日 子时 广东 男
    r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日.*?([子丑寅卯辰巳午未申酉戌亥])时.*?([男女])',
    # 1989年12月11日 广东 女
    r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日.*?([男女])',
]

# D9（2026-08-24 生产实测）：无档案但问事意图明确 → 先答通用命理知识，
# 末尾附建档引导（先答问题，再要档案）。按序匹配（先命中先答），
# 静态内容保证确定性兜底（无 LLM/无向量库也必回实质内容）。
_BAZI_GENERAL_KNOWLEDGE = [
    {
        "keywords": ("佩戴", "戴什么", "戴啥", "饰品", "首饰", "手串", "貔貅",
                     "水晶", "玉佩", "手链", "挂件"),
        "content": (
            "关于「佩戴调理」的通用命理常识：佩戴饰品的核心逻辑是补益命局喜用神——"
            "喜用神是八字中最需要帮扶的五行。五行对应：木喜青绿色（木质饰品、翡翠、绿幽灵），"
            "火喜红色（红绳、南红玛瑙、石榴石），土喜黄色（黄水晶、蜜蜡、黄玉），"
            "金喜白色（金银饰品、白水晶），水喜黑色（黑曜石、海蓝宝、黑玛瑙）。"
            "身弱命局（日主力量不足）宜补印比——选择生扶日主的五行材质与颜色；"
            "「财多身弱、反为财累」，财星过旺而日主弱时，不宜过早佩戴过强的招财物件。"
            "《滴天髓》讲「何知其人富，财气通门户」——身强能任财才是富的根基。"
            "稳妥做法是先固本培元：作息规律、饮食均衡、多接触喜用神方位的环境，"
            "饰品只是辅助。具体戴什么最合你，需结合你的八字喜用神精确判断。"
        ),
    },
    {
        "keywords": ("发财", "财运", "赚钱", "求财", "暴富", "偏财", "正财",
                     "快速发财", "财富", "挣", "收入"),
        "content": (
            "关于「财运」的通用命理常识：命理讲「财为养命之源」，财分正财与偏财——"
            "正财为稳定收入，宜勤宜积；偏财为大额流动之财，常与投资、机遇相关。"
            "「身弱不担财」是常见格局：日主弱而财星旺，如人小担重担，财来也守不住，"
            "反生焦虑与破耗。通用调理思路：一、先补身后求财——身弱先固本培元，"
            "身强才担得起财星；二、求财宜循序渐进，忌高风险投机——命理中暴富多对应"
            "偏财极旺的大运流年，属可遇不可求，妄求「几天几周暴富」之法反易招损；"
            "三、开源节流并重，职业技能与专业积累才是长久之财；"
            "四、可结合大运流年看财星生旺的年份做财务规划。"
            "具体到你财星旺衰、财库开闭，需要你的出生信息精确排盘后才能断言。"
        ),
    },
    {
        "keywords": ("身弱", "身强", "变强", "调理", "转运", "改运", "增运",
                     "提升运势", "运势差", "旺自己"),
        "content": (
            "关于「身弱调理」的通用命理常识：八字身弱指日主（代表自己的天干）"
            "五行力量不足，喜印（生我）比（同我）帮扶，忌财官过旺压身。"
            "调理思路：一、补印比——选择日主五行的同类与生扶元素（如木日主喜水木，"
            "可多接触水木属性的颜色、方位、行业）；二、身弱不宜硬扛重担——"
            "大事分解、借力合作，避免长期透支；三、运动作息是「补身」之本，"
            "身强才有气任财任官；四、可查大运流年印比帮身的年份重点把握。"
            "《三命通会》强调「旺弱须凭造化推」——身强身弱都要结合全盘五行流通判断，"
            "单看强弱不足以断吉凶。想让我结合你的八字看喜用神与调理方向，"
            "告诉我出生信息即可精确分析。"
        ),
    },
    {
        "keywords": ("事业", "工作", "升职", "跳槽", "创业", "职场", "职业"),
        "content": (
            "关于「事业」的通用命理常识：事业格局看官杀（事业、权力）与印星（贵人、"
            "学识）的配合：官印相生为事业顺遂之象，食伤生财为技术创业之象。"
            "通用思路：一、身强者可担官杀，宜进取开拓；身弱者宜先求印星助力——"
            "跟对贵人、补足专业，再图晋升；二、跳槽择业看大运流年官星生旺与否；"
            "三、行业五行与命局喜用相合者顺：木喜水木、火喜木火，以此类推。"
            "具体到你的格局与时机，需要出生信息排盘后判断。"
        ),
    },
    {
        "keywords": ("婚姻", "感情", "桃花", "姻缘", "恋爱", "结婚", "分手", "正缘"),
        "content": (
            "关于「感情婚姻」的通用命理常识：姻缘看夫妻星（男看财星、女看官星）"
            "与夫妻宫（日支）：夫妻星得位、夫妻宫稳则婚姻顺。"
            "通用思路：一、身弱官杀重者（常见于感情内耗、遇人不淑），宜先补身强己，"
            "自己稳定才有健康关系；二、桃花看子午卯酉及红鸾天喜，桃花旺不等于姻缘佳；"
            "三、合婚看双方日柱干支生合关系，五行互补为佳。"
            "具体到你姻缘应期，需要出生信息排盘判断。"
        ),
    },
    {
        "keywords": ("健康", "身体", "病", "失眠", "疲惫", "养生"),
        "content": (
            "关于「健康」的通用命理常识：传统命理以五行对应五脏——木主肝胆、火主心、"
            "土主脾胃、金主肺、水主肾。某一行过旺过弱，对应脏腑易失衡。"
            "通用思路：一、五行过弱者宜顺其性补养（如金弱注意肺与呼吸，宜润肺）；"
            "二、过旺者宜疏泄（如木旺注意肝胆，宜疏肝理气）；"
            "三、大运流年引动相应五行时，提前体检、规律作息。"
            "身体不适请先就医，命理只作调养参考。具体到你的体质倾向，"
            "需要出生信息排盘后判断。"
        ),
    },
]

_BAZI_GENERAL_KNOWLEDGE_FALLBACK = (
    "关于八字命理的基本常识：八字由出生年月日时的天干地支（四柱）构成，"
    "日主代表自身，五行生克与十神（正官、七杀、正印、偏印、正财、偏财、"
    "比肩、劫财、食神、伤官）组成命局格局，大运十年一换、流年逐年更替，"
    "是传统命理推演运势的基本框架。古人以《滴天髓》《三命通会》《穷通宝鉴》"
    "等典籍论命，讲究旺衰、喜忌、调候、通关。任何具体断语都必须结合个人八字——"
    "同样的提问，不同命局结论可能截然相反。告诉我你的出生信息，"
    "我可以结合你的八字给出有针对性的分析。"
)


def format_tool_results_json(results: list) -> str:
    """工具结果统一 JSON 包装（spec 1.2）：{"tool","ok","data"/"error","needs_info"}。

    以 JSON 行序列化注入 system，LLM 结构化消化（豆包式标准结果回喂）。
    """
    lines = []
    for r in results:
        body = {"tool": r.name, "ok": bool(r.ok)}
        if r.ok:
            body["data"] = r.text
        else:
            body["error"] = r.text
            if r.needs_info:
                body["needs_info"] = True
        lines.append(json.dumps(body, ensure_ascii=False))
    return "\n".join(lines)


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
        hehun_engine: HehunEngine = None,
        qimen_engine: QimenEngine = None,
        xingming_engine: XingmingEngine = None,
        session_dao: SessionDAO = None,
        member_dao: MemberDAO = None,  # P1-2: quota management
    ):
        self.engine = engine
        self.ziwei_engine = ziwei_engine
        self.liuyao_engine = liuyao_engine
        self.fengshui_engine = fengshui_engine
        self.mianxiang_engine = mianxiang_engine
        self.zeri_engine = zeri_engine
        self.dream_engine = dream_engine
        self.hehun_engine = hehun_engine
        self.qimen_engine = qimen_engine
        self.xingming_engine = xingming_engine
        self.retriever = retriever
        self.llm = llm
        self.dao = dao
        self.session_dao = session_dao
        # P1-2: Membership / quota management
        db_path = getattr(dao, 'db_path', '') if dao else ''
        self.member_dao = member_dao or (MemberDAO(db_path) if db_path else None)
        # F1: Preference learner — gets db_path from dao
        db_path = getattr(dao, 'db_path', '') if dao else ''
        self.preference_dao = PreferenceDAO(db_path) if db_path else None
        # 对话数据复用：排盘结果落库 chart_records（重看 0 重跑，三写入点统一）
        db_path = getattr(dao, 'db_path', '') if dao else ''
        self.chart_dao = ChartDAO(db_path) if db_path else None
        # Task 6 存量数据直读（档案/解梦/历史/签/名笺/灯语/择吉/晨笺/收藏/会员）：
        # RecordQuery 注入 process() 主流程，问存量数据直接读库秒回。
        # Task 9 装配层统一注入（T8 审查 ⚠️ 项修复）：轻量 DAO（qian/ming/lamp/
        # zeri/jian/fav）在装配处全部注入——线上 _q_晨笺/_q_收藏 不再恒 None；
        # 无 db_path 的构造路径保持 None（_q_* None 守卫兜底，不建表不伪造）。
        self.record_query = None
        if db_path:
            try:
                import sqlite3
                from src.storage.person_dao import PersonDAO
                from src.storage.qian_dao import QianDAO
                from src.storage.ming_dao import MingDAO
                from src.storage.lamp_dao import LampDAO
                from src.storage.zeri_dao import ZeriDAO
                from src.storage.jian_dao import JianPrefDAO
                from src.storage.favorite_dao import FavoriteDAO
                from src.bot.record_query import RecordQuery
                # 轻量 DAO 共用一条同库连接（与 main.py _zeri_conn 复用惯例一致）；
                # FavoriteDAO 构造入参为 db_path（Task 9 简报接口契约）
                _lconn = sqlite3.connect(db_path, check_same_thread=False)
                self.record_query = RecordQuery(
                    self.dao, PersonDAO(db_path), self.session_dao,
                    self.chart_dao, member_dao=self.member_dao,
                    qian_dao=QianDAO(_lconn), ming_dao=MingDAO(_lconn),
                    lamp_dao=LampDAO(_lconn), zeri_dao=ZeriDAO(_lconn),
                    jian_dao=JianPrefDAO(_lconn), fav_dao=FavoriteDAO(db_path),
                )
            except Exception as e:
                logger.warning("RecordQuery 初始化失败（降级为全流程）: %s", e)
                self.record_query = None
        # Multi-turn memory
        api_key = getattr(llm, 'api_key', '') if llm else ''
        self.memory = ConversationMemory(api_key) if api_key else None
        # L2 会话增量摘要（方案 §5.4）— 上下文超阈值触发，分块滚动压缩
        self.compactor = MemoryCompactor(api_key, model=getattr(llm, 'model', 'deepseek-flash')) if api_key else None
        # L1/L2/L3 运行时状态：工具调用日志（本轮 <tool_call> 记录，落库用）
        self._tool_logs: dict = {}
        # E2-1 对话消息卡片化：本轮卡片判定上下文（user_id → {scenario, paipan,
        # zeri, data_read}），process() 入口重置、引擎/直读路径记录、出口消费
        self._card_turn: dict = {}
        # 阶段 5：本轮引用来源注册表（user_id → [{index,type,title,text,url}]），
        # 工具执行时注册，_run_tool_loop 校验后由 API 层 pop_citations 取走
        self._citations: dict = {}
        # D2: Response cache for high-frequency queries
        self.cache = ResponseCache(max_size=500)
        # E4: ML quality predictor (online learning)
        self.quality_predictor = QualityPredictor()
        # Phase 3: User Memory System — persistent cross-session memory
        self.memory_system = UserMemory()
        # 阶段 5（方案 v5）：本轮理解出的关键事实（user_id → facts），
        # 供 save_bazi_info 做 subject=other（帮他人排盘）保护
        self._analysis_facts: dict = {}
        # Task 5: user_id -> deepNight(倾诉临时模式：不落 L2/L3 + temp 消息 24h 硬清理)
        self._deep_night: dict = {}
        # Task 2 等待时长优化：秒回安抚预生成线程池（与意图分析并行发出；
        # 进程生命周期共享，不随请求销毁）；user_id -> Future 存本轮预生成任务
        import concurrent.futures
        self._pregen_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="pregen")
        self._pregen_instant: dict = {}
        self._downgraded: dict = {}  # L5-2（I-1/I-2）：本轮回合是否走降级链路（对话额度用尽）
        # R1-2（评测 T008）：性别纠正固定回执暂存（user_id → ack），润色后
        # 幂等重挂——纠正回执（含新性别断言）必须存活于最终回复（零 LLM）
        self._gender_acks: dict = {}
        # k41（②g 兜底出口）：结构不可辨的性别声明（本人口语自述 vs 第三人领属）
        # 询问确认暂存（user_id → ask），出口幂等重挂——不静默改写也不静默丢弃
        self._gender_confirms: dict = {}
        # k11b：本轮引擎意图域已自动联网检索事实（user_id → {entity, query,
        # domains, ok, text}）——LLM 再发同 query 搜索工单时 executor 直取首查
        # 结果（防二次真实检索/引用编号分裂）；每轮 process 入口清空
        self._turn_grounded: dict = {}
        # k11b：联网检索频控护栏（护栏 3 次/60s/用户；判定不计数，只在真实
        # 发起检索前计数——见 _search_rate_ok）
        self._search_ticks: dict = {}

        # 命例相似度引擎已停用（2026-08-09 方案 v5 选 A 彻底移除）：
        # 不再初始化 SimilarityEngine（原 data/wenzhen_charts.db 44k 命例对照），
        # 未问"像谁"不再输出命例/名人对照

        # 批次 1（spec 1.1）：统一能力注册表注入执行器。tool 与 intent 分开绑定
        # （cap_id 同名如 fengshui/zeri/dream 互不覆盖，Task 2 review M-1 修复）；
        # lambda 统一签名 (params, user_id="", user_question="") -> ToolResult，
        # 执行器实现零改动（红线）。
        from src.bot.capability_registry import bind_executors
        bind_executors(
            {
                "bazi_chart": lambda p, user_id="", user_question="": self._tool_bazi(p, user_id),
                "quote_rag": lambda p, user_id="", user_question="": self._tool_search(
                    p, user_id=user_id, user_question=user_question),
                "web_search": lambda p, user_id="", user_question="": self._tool_web_search(
                    p, user_id=user_id),
                "dream": lambda p, user_id="", user_question="": self._tool_dream(p, user_id),
                "fengshui": lambda p, user_id="", user_question="": self._tool_fengshui(p),
                "zeri": lambda p, user_id="", user_question="": self._tool_zeri(p, user_id),
                "record_lookup": lambda p, user_id="", user_question="": self._tool_query_records(
                    p, user_id),
                # 批次 2 E1 合婚工具：新增绑定只做加法（既有 7 个零改动）
                "hehun": lambda p, user_id="", user_question="": self._tool_hehun(p, user_id),
                # 批次 2 E2 起名工具：新增绑定只做加法（既有 8 个零改动）
                "naming": lambda p, user_id="", user_question="": self._tool_naming(p, user_id),
                # 批次 2 E3 流月流年工具：新增绑定只做加法（既有 9 个零改动）
                "fortune_cycle": lambda p, user_id="", user_question="": self._tool_fortune_cycle(
                    p, user_id),
                # 批次 2 E4 择业/方位匹配工具：新增绑定只做加法（既有 10 个零改动）
                "career_dir": lambda p, user_id="", user_question="": self._tool_career_dir(
                    p, user_id),
                # 批次 2 E5 数字吉凶工具：新增绑定只做加法（既有 11 个零改动）
                "num_omen": lambda p, user_id="", user_question="": self._tool_num_omen(
                    p, user_id),
            },
            {
                "bazi": self._handle_bazi, "ziwei": self._handle_ziwei,
                "liuyao": self._handle_liuyao, "fengshui": self._handle_fengshui,
                "mianxiang": self._handle_mianxiang, "zeri": self._handle_zeri,
                "qimen": self._handle_qimen, "xingming": self._handle_xingming,
                "hehun": self._handle_hehun, "dream": self._handle_dream,
                "calendar": self._handle_calendar, "hourly": self._handle_hourly,
                "xuetang": self._handle_xuetang, "advisor": self._handle_advisor,
                "career": self._handle_career,
            },
        )

    # ============================================================
    # Feedback Learning (F1-F3)
    # ============================================================

    def _handle_feedback(self, msg: str, user_id: str,
                         session_id: Optional[str] = None) -> str:
        """Handle 👍/👎 feedback — learn user preferences."""
        is_positive = msg in ("👍", "好评", "准", "good")
        is_negative = msg in ("👎", "差评", "不准", "bad")
        if not is_positive and not is_negative:
            return "收到你的反馈啦～有什么想问的尽管说！"

        if not self.preference_dao:
            return "感谢反馈！" if is_positive else "收到，我会继续改进的～"

        try:
            # Get current context for learning
            prefs = self.preference_dao.get(user_id)
            current_style = prefs.preferred_style or ""

            # Detect topic from last conversation
            last_topic = ""
            last_msg = ""
            if self.session_dao:
                history = self.session_dao.get_context_for_llm(
                    user_id, history_limit=5, session_id=session_id)
                user_msgs = [m.get("content", "") for m in history if m.get("role") == "user"]
                last_msgs = " ".join(user_msgs)
                last_topic = self.preference_dao.detect_topic(last_msgs)
                if user_msgs:
                    last_msg = user_msgs[-1]

            # Learn preference (EMA)
            updated = self.preference_dao.learn(
                user_id, is_positive,
                style=current_style,
                topic=last_topic,
            )

            # E4: Train ML quality predictor
            if last_msg:
                import datetime
                emotion_label = "neutral"
                if hasattr(self, '_last_emotion_labels'):
                    emotion_label = self._last_emotion_labels.get(user_id, "neutral")
                self.quality_predictor.update(
                    message=last_msg,
                    hour=datetime.datetime.now().hour,
                    emotion=emotion_label,
                    topic=last_topic or "general",
                    response_len=0,  # We don't know the exact response that got this feedback
                    was_positive=is_positive,
                )

            if is_positive:
                reply = "感谢认可！"
            else:
                reply = "收到反馈，我会调整的～"

            if updated.is_mature and updated.accuracy_pct is not None:
                reply += f"\n📊 你的认可率：{updated.accuracy_pct}%（{updated.feedback_count}次反馈）"
                if updated.accuracy_pct >= 80:
                    reply += "\n🎯 超过80%了！我已经很了解你的偏好了，之后的回答会更贴合你的口味～"

            return reply
        except Exception:
            return "感谢反馈！" if is_positive else "收到，会继续改进～"

    def _add_feedback_prompt(self, reply: str) -> str:
        """Append unified feedback prompt."""
        return reply + "\n\n" + _FEEDBACK_PROMPT_EMOJI

    def _get_preference_hint(self, user_id: str) -> str:
        """Get preference hint for LLM prompt injection. Empty if not mature."""
        if not self.preference_dao:
            return ""
        prefs = self.preference_dao.get(user_id)
        return prefs.to_prompt_hint()

    def _get_personalized_context(self, user_id: str) -> str:
        """Get personalized context from learned preferences.

        Returns empty string if preferences not yet mature.
        """
        if not self.preference_dao:
            return ""
        prefs = self.preference_dao.get(user_id)
        if not prefs.is_mature:
            return ""
        _tn = {"wealth": "财运", "love": "感情", "career": "事业",
               "health": "健康", "growth": "个人成长"}
        _tw = {"wealth": prefs.topic_wealth, "love": prefs.topic_love,
               "career": prefs.topic_career, "health": prefs.topic_health,
               "growth": prefs.topic_growth}
        _sorted = sorted(_tw, key=_tw.get, reverse=True)
        _top3 = [_tn.get(t, t) for t in _sorted[:3]]
        return (
            f"\n## 用户偏好（从历史反馈学习）\n"
            f"- 关注话题: {'、'.join(_top3)}\n"
            f"- 偏好长度: {'简短精炼' if prefs.prefer_short else '适中详细'}\n"
            f"- 准确率: {prefs.accuracy_pct if prefs.accuracy_pct is not None else '暂无'}%\n"
            f"\n请根据以上偏好调整回答内容和详细程度。\n"
        )

    def _get_preferred_max_tokens(self, user_id: str) -> int:
        """Get preferred max_tokens based on user's length preference."""
        if not self.preference_dao:
            return 500
        prefs = self.preference_dao.get(user_id)
        if prefs.is_mature and prefs.prefer_short:
            return 250
        return 500

    # ============================================================
    # P1-2: Quota Check — free tier daily limit with warnings
    # ============================================================

    def _check_quota(self, user_id: str) -> tuple:
        """Check user's remaining quota.

        Returns:
            (remaining: int, is_limited: bool)
            remaining = -1 means unlimited (paid user).
        """
        if not self.member_dao:
            return -1, False  # No quota system = unlimited
        if is_experience_mode():
            return -1, False  # 体验模式：不限额（无限次）
        if is_admin_user(user_id):
            # k36 A28：超管豁免（ADMIN_IDS 白名单；与 chat_quota 同一判据，口径一致）
            return -1, False
        try:
            membership = self.member_dao.get_membership(user_id)
            limit = membership.get("queries_limit")
            used = membership.get("queries_used", 0)
            if limit is None:
                return -1, False  # Unlimited
            remaining = max(0, limit - used)
            return remaining, True
        except Exception:
            return -1, False

    def _consume_quota(self, user_id: str) -> None:
        """Mark one query as used."""
        if is_experience_mode():
            return  # 体验模式：不扣配额
        if self.member_dao:
            try:
                self.member_dao.use_quota(user_id)
            except Exception:
                pass

    # ============================================================
    # Phase 2: Scenario Router — structured report for known scenarios
    # ============================================================

    SCENARIO_KEYWORDS = {
        "career": ["换工作", "跳槽", "辞职", "创业", "工作", "职业", "上班", "事业"],
        "love": ["感情", "分手", "离婚", "结婚", "恋爱", "正缘", "桃花", "爱情"],
        "wealth": ["财运", "赚钱", "投资", "亏", "钱", "财", "收入", "理财"],
        "health": ["健康", "身体", "病", "手术", "体检", "住院", "养生"],
        "property": ["搬家", "买房", "房产", "装修", "迁居", "不动产"],
        "compatibility": ["合不合", "配对", "合婚", "配不配", "八字合", "匹配"],
    }

    SCENARIO_MAP = {
        "career": {
            "id": "career_change",
            "prompt_template": "我在考虑换工作，请根据我的八字分析：1)当前工作的发展空间和运势 2)跳槽的最佳时机窗口 3)适合的行业方向 4)需要注意的风险",
        },
        "love": {
            "id": "love_relationship",
            "prompt_template": "我想了解这段感情的发展前景，请根据我的八字分析：1)这段感情的缘分深浅 2)可能遇到的阻碍和挑战 3)感情发展的关键时间点 4)如何经营这段关系",
        },
        "wealth": {
            "id": "wealth_year",
            "prompt_template": "我想了解今年的财运，请根据我的八字分析：1)今年正财运和偏财运趋势 2)财运最佳的时间窗口 3)适合的投资和理财方向 4)需要注意的破财风险",
        },
        "health": {
            "id": "health_concern",
            "prompt_template": "我想了解健康方面的注意事项，请根据我的八字分析：1)命局中哪些五行偏弱，对应哪些身体部位容易出问题 2)大运流年对健康的影响 3)需要注意的年份和季节 4)日常养生保健建议",
        },
        "property": {
            "id": "property_move",
            "prompt_template": "我想了解在房产方面的运势，请根据我的八字分析：1)当前是否适合买房或搬家 2)对居住环境的风水建议 3)适合的方位和朝向 4)不动产投资的吉凶时机",
        },
        "compatibility": {
            "id": "compatibility",
            "prompt_template": "我想了解两人的缘分和配对情况，请根据双方的八字分析：1)两人的五行互补和冲突 2)感情中的主要矛盾点 3)长期相处的前景 4)如何调和彼此的差异",
        },
    }

    def _route_by_scenario(self, msg: str, user_id: str) -> Optional[dict]:
        """If the message matches a known scenario, return its structured info.

        Returns:
            dict with keys: category, id, prompt_template, or None if no match.
        """
        if not msg:
            return None

        # Check each scenario's keywords
        matches = []
        for category, keywords in self.SCENARIO_KEYWORDS.items():
            for kw in keywords:
                if kw in msg:
                    matches.append(category)
                    break  # one keyword match per category is enough

        if not matches:
            return None

        # Pick the best match: prioritize by keyword density
        best_category = max(matches, key=lambda cat: sum(1 for kw in self.SCENARIO_KEYWORDS[cat] if kw in msg))

        info = self.SCENARIO_MAP.get(best_category)
        if info:
            return {
                "category": best_category,
                **info,
            }
        return None

    # ============================================================
    # AI Message Analysis — emotion + intent in ONE call (no keywords)
    # ============================================================

    def _analyze_message(self, msg: str, user_id: str = "",
                         session_id: Optional[str] = None) -> MessageAnalysis:
        """Single AI call for emotion detection + intent classification.

        Replaces _soothe() + _detect_intent() — zero hardcoded keywords.
        One Flash call instead of two, cutting latency from ~8s to ~4s.
        AI 原生改造（Phase 1）：分析时注入最近 2-3 轮对话上下文（方案 2.1 短期记忆），
        让意图/情绪判断更准。
        """
        api_key = getattr(self.llm, 'api_key', '') if self.llm else ''
        if api_key:
            try:
                analyzer = MessageAnalyzer(api_key=api_key)
                history = None
                if user_id and self.session_dao:
                    try:
                        history = self.session_dao.get_context_for_llm(
                            user_id, history_limit=6, session_id=session_id)
                    except Exception:
                        history = None
                return analyzer.analyze(msg, history=history)
            except Exception:
                pass
        # Graceful fallback（与 MessageAnalyzer fast path 同规则：纯生日陈述才判 bazi，
        # 含意图词时兜底为 free_chat 走 LLM 自然对话，避免掐死"生日+公司适配"类问题）
        # D7：口语长句生辰（阴历/农历 + 中文数字月）同样命中 BIRTH_DATE_PATTERN
        if (MessageAnalyzer.BIRTH_DATE_PATTERN.search(msg)
                and not MessageAnalyzer.INTENT_HINT_PATTERN.search(msg)):
            return MessageAnalysis(needs_soothe=False, soothe_text="",
                                   emotion_label=None, intent="bazi")
        return MessageAnalysis(needs_soothe=False, soothe_text="",
                               emotion_label=None, intent=None)

    def _redirect_single_marriage(self, analysis, msg: str) -> None:
        """T076/k38-I4 单人婚姻询问改判（就地，无返回值）。

        条件全部成立才动（缺一不动，见 process() 内注释）：① 意图 = hehun 或
        advisor ② 消息含 婚姻/姻缘 ③ 无双人语境 ④ 无运势时间锚。命中后按消息
        是否自带出生信息分派（判定复用 F2 单一事实源 `_extract_partial_birth`，
        不重复实现部分信息提取）：

        - **有**（完整或部分）→ bazi：走排盘/建档落档路径（完整信息直接排盘，
          部分信息走 F2 渐进累积「回显已确认项 + 缺什么要什么」）——修 k38-I4
          审查实测缺陷：带完整生辰的单人婚姻问句此前判 advisor → 无档案分支回
          「请提供你的出生信息：出生年月日时…」（用户刚说过）+ persons 零写入。
        - **无** → advisor：无档案回建档引导「出生年月日时/性别」（T076 断言），
          有档案走命盘建议（优于 hehun 无双方生辰死胡同）。

        k38-RI4b（复审实测）：`_extract_partial_birth` 带时间谓词（完整日期全在
        未来 = 婚期/预产期等非生辰语境 → 返回 {}）——「2026年10月1日结婚，帮我看看
        我的婚姻」不再被判 bazi 建档。

        拆成独立方法：条件可被单测直接锁定（process() 内联判定不可单测）。
        """
        try:
            if (analysis is not None
                    and analysis.intent in ("hehun", "advisor")
                    and _SINGLE_MARRIAGE_RE.search(msg or "")
                    and not _SECOND_PERSON_RE.search(msg or "")
                    and not _SINGLE_MARRIAGE_TIMELINE_RE.search(msg or "")):
                if self._extract_partial_birth(msg or ""):
                    analysis.intent = "bazi"
                else:
                    analysis.intent = "advisor"
        except Exception:
            pass  # 判定异常 → 保持原意图（不阻断主链）

    def _rule_analyze(self, msg: str) -> MessageAnalysis:
        """降级链路意图快判（L5-2 I-1）：零 LLM 调用的规则判定，替代 _analyze_message。

        规则（复用 MessageAnalyzer fast path，与 _quick_intent 同口径）：
        - 纯生日陈述（含出生日期、无意图提示词）→ intent='bazi'（主流程走
          引擎排盘，成本可控）；
        - 其余一律 intent=None → 自由对话（_free_chat 走 GLM 精简回复）。
        情绪/安抚/附加需求等字段全部缺省（降级用户优先保障对话可用与成本）。
        """
        try:
            intent = self._quick_intent(msg) or None
        except Exception:
            intent = None
        return MessageAnalysis(needs_soothe=False, soothe_text="",
                               emotion_label=None, intent=intent)

    # ============================================================
    # Task 2 等待时长优化：秒回安抚预生成（与意图分析并行发出）
    # ============================================================

    def _quick_intent(self, msg: str) -> Optional[str]:
        """便宜的意图判定：MessageAnalyzer fast path 同规则（纯正则，无 LLM 调用）。

        仅纯生日陈述（含出生日期、无意图提示词）→ "bazi"；其余 → None
        （真实意图由主流程 _analyze_message 判定，这里不发起任何调用）。
        用于预生成门控：hehun/ziwei/自由聊天等消息即使含完整出生日期，
        也不提交预生成，避免产生被丢弃的 engine.calculate + flash 调用。
        """
        try:
            if (MessageAnalyzer.BIRTH_DATE_PATTERN.search(msg)
                    and not MessageAnalyzer.INTENT_HINT_PATTERN.search(msg)):
                return "bazi"
        except Exception:
            pass
        return None

    # ============================================================
    # Task A4: 意图分级路由（轻量路由器，预算感知）— 0 LLM 纯正则
    # ============================================================
    # 模型矩阵（现状盘点 2026-08-27，未新增模型/key）：
    #   - 主链-快聊：deepseek-flash（FortuneLLM.model，Anthropic 兼容端点）
    #     → 自由对话 / 意图分析(MessageAnalyzer) / 润色 / 秒回安抚 / 工具循环
    #   - 主链-深度：deepseek-flash（FortuneLLM.deep_model，当前与快聊同款）
    #     → 命理深度分析（use_pro）
    #   - 降级链：glm-4-flash（ZHIPU_API_KEY 免费，OpenAI 兼容端点），失败
    #     回退 deepseek → 降级用户精简对话（chat_lite，L5-1）
    #   - 零成本层（无模型）：入口缓存 / 反馈 / T5 重看盘直读 / 存量直读 /
    #     _quick_intent 预生成门控 / _rule_analyze 降级快判 / A4 简单意图直通
    # 红线对齐（调研结论）：hunyuan-lite 类轻模型不能承担工具路由决策 →
    # 不引入轻模型承担简单任务；"简单意图不经过 AI 慢推理"即本任务预算感知
    # 核心价值。多级模型选择（complex 档换更强模型）文档化为未来挂载点——
    # _score_intent_complexity 返回档位即路由决策输入，届时按档选模型即可。

    SIMPLE_GREETING_RE = re.compile(
        r'^(?:您好|你好|你好呀|哈喽|嗨|hello|hi|早上好|中午好|下午好|晚上好|早安)'
        r'[!！。.~～了啦\s]*$', re.IGNORECASE)
    SIMPLE_THANKS_RE = re.compile(
        r'^(?:谢谢|谢谢啦|感谢|多谢|辛苦了)[!！。.~～你了呀啦\s]*$')
    SIMPLE_BYE_RE = re.compile(
        r'^(?:再见|拜拜|晚安)[!！。.~～了啦\s]*$')
    SIMPLE_DATETIME_RE = re.compile(
        r'^(?:今天|现在|请问)?(?:几月几号|今天几号|几号了|现在几点|几点了|'
        r'几点钟|几点|现在时间|什么时间|今天星期几|星期几|今天周几|周几)'
        r'[!！。.~～?？呢啊了\s]*$')
    # 守卫词表（防御纵深）：锚定正则已保证整句为纯简单意图，这里再排除
    # 命理/场景意图词——防未来正则放宽时"你好，帮我算八字"被误掐为简单档
    SIMPLE_GUARD_WORDS = (
        "八字", "紫微", "斗数", "占卜", "命理", "排盘", "塔罗", "看相",
        "生肖", "运势", "解梦", "算", "测", "看", "帮", "怎么", "怎样",
        "如何", "建议", "应该",
    ) + tuple(kw for kws in SCENARIO_KEYWORDS.values() for kw in kws)

    def _simple_intent_guard_ok(self, msg: str) -> bool:
        """简单意图守卫：不含量化/意图提示词/场景/命理词（防误掐真意图）。"""
        try:
            if MessageAnalyzer.BIRTH_DATE_PATTERN.search(msg):
                return False
            if MessageAnalyzer.INTENT_HINT_PATTERN.search(msg):
                return False
            if any(w in msg for w in self.SIMPLE_GUARD_WORDS):
                return False
        except Exception:
            pass
        return True

    def _score_intent_complexity(self, msg: str) -> str:
        """意图复杂度评分（A4 轻量路由器）：返回 'simple' / 'normal' / 'complex'。

        规则（关键词/长度/意图类型三档映射）：
        - complex：长度 > 80（长文倾诉/多要求）；或长度 > 40 且命中 ≥2 个
          场景类别关键词（多意图叠加，如财运+感情+工作）；
        - simple：整句锚定命中问候/感谢/再见/日期时间之一，且守卫通过
          （不含生日陈述/意图提示词/场景关键词/命理词——"你好，帮我算八字"
          这类问候前缀+真意图不被误掐）；
        - normal：其余（生日陈述 / 单意图问题 / 自由聊天）。
        纯正则零 LLM 调用；档位只用于路由决策，不改变消息语义与内容。
        """
        try:
            msg = (msg or "").strip()
            if not msg:
                return "normal"
            if len(msg) > 80:
                return "complex"
            if len(msg) > 40:
                cats = {c for c, kws in self.SCENARIO_KEYWORDS.items()
                        if any(kw in msg for kw in kws)}
                if len(cats) >= 2:
                    return "complex"
            if (self.SIMPLE_GREETING_RE.match(msg)
                    or self.SIMPLE_THANKS_RE.match(msg)
                    or self.SIMPLE_BYE_RE.match(msg)
                    or self.SIMPLE_DATETIME_RE.match(msg)):
                if self._simple_intent_guard_ok(msg):
                    return "simple"
            return "normal"
        except Exception:
            return "normal"  # 评分异常 fail-open → 主链（宁多走一步不误掐）

    def _reply_datetime(self, msg: str) -> str:
        """日期时间确定性回答（A4 simple 档）：纯 datetime，不查农历。"""
        today = date.today()
        parts = [f"今天是 {today.year}年{today.month}月{today.day}日，星期"
                 + "一二三四五六日"[today.weekday()]]
        if re.search(r"几点|时间", msg):
            tm = time.localtime()
            parts.append(f"现在是 {tm.tm_hour:02d}:{tm.tm_min:02d}")
        return "。".join(parts) + "。"

    def _greeting_reply(self, user_id: str) -> str:
        """开场白（0 LLM）：欢迎回来（有记忆）/ 已建档引导 / 默认引导。

        A4 从 _free_chat 空消息分支抽取，简单意图快通道复用（行为不变）；
        fail-open：记忆/档案任何异常 → 回落默认文案，绝不冒泡丢回复。
        """
        try:
            if self.memory_system and self.memory_system.has_memory(user_id):
                greeting = self.memory_system.get_greeting(user_id)
                if greeting:
                    return f"欢迎回来！{greeting}"
        except Exception:
            pass
        try:
            # k18：存在性判断改走统一档案链（persons 默认档案 → bazi_info →
            # chart_records 兜底）——persons-only 建档用户（09-04 wipe 后画像
            # 清零人群）不再被误当「无八字」引导建档（T089 同类问候面）。
            saved = (self._get_user_birth_profile(user_id)
                     if self.dao else None)
            if saved:
                return '欢迎回来！您的八字信息已保存，有什么想了解的可以直接问～'
        except Exception:
            pass
        return '您好！我是易理明灯AI命理顾问。直接告诉我您的出生日期，我帮您看八字。'

    def _route_simple_intent(self, msg: str, user_id: str = "") -> Optional[str]:
        """简单意图直通快通道（A4，0 LLM）：评分=simple → 确定性回复。

        - 问候 → _greeting_reply(user_id)（个性化开场，0 LLM）
        - 感谢/再见 → 固定礼貌文案；日期时间 → datetime 确定性回答
        fail-open：评分异常/未命中/任何失败 → None 走主链慢推理，
        绝不因路由器错误让用户拿不到回复。
        """
        try:
            if self._score_intent_complexity(msg) != "simple":
                return None
            if self.SIMPLE_DATETIME_RE.match(msg):
                return self._reply_datetime(msg)
            if self.SIMPLE_GREETING_RE.match(msg):
                return self._greeting_reply(user_id)
            if self.SIMPLE_THANKS_RE.match(msg):
                return "不客气～有任何命理问题都可以随时问我。"
            if self.SIMPLE_BYE_RE.match(msg):
                return ("晚安，好梦～有需要随时找我。" if "晚安" in msg
                        else "再见！祝您一切顺利，有需要随时找我。")
        except Exception:
            pass
        return None

    def _start_pregen_instant(self, msg: str, user_id: str = ""):
        """消息含完整出生信息且意图为排盘（bazi）→ 排盘 + 秒回安抚提交后台线程。

        返回 Future（_do_bazi_analysis 消费）；非排盘意图/无出生信息/启动失败 → None。
        约束：不改变消息顺序与内容语义——秒回 LLM 调用仍先于主分析发出，
        只是准备阶段与意图分析重叠执行（一轮省 20-30s）。

        Task 2 终审修复：提交前先用 _quick_intent（MessageAnalyzer fast path，
        无 LLM 调用）判定意图——仅排盘类（bazi）才预生成；含意图提示词的
        hehun/ziwei/自由聊天等消息直接跳过（不再产生被丢弃的调用）。
        k11c：本人（非第三方）且消息年份与档案一致 → 秒回预生成随档案
        真太阳时开关排盘（与主链路 _handle_bazi 同口径，防回复引错时柱）。
        """
        try:
            # 排盘意图门控：fast path 未判 bazi（含意图词/无出生日期）→ 跳过
            if self._quick_intent(msg) != "bazi":
                return None
            parsed = self._extract_bazi_info(msg)
            if not parsed:
                return None
            year, month, day, hour, minute, city, gender = parsed
            _solar_pre = True
            if user_id and not self._is_third_party_birth_request(msg):
                try:
                    _prof = self._get_user_birth_profile(user_id)
                    if (_prof and _prof.get("year") == year
                            and _prof.get("year")):
                        _solar_pre = (_prof.get("solar_time")
                                      not in (0, "0", False))
                except Exception:
                    _solar_pre = True
            return self._pregen_pool.submit(
                self._pregen_instant_worker,
                year, month, day, hour, minute, city, gender, _solar_pre,
            )
        except Exception:
            logger.warning("秒回预生成启动失败（回退同步生成）", exc_info=True)
            return None

    def _pregen_instant_worker(self, year, month, day, hour, minute,
                               city, gender, solar_time=True) -> str:
        """后台线程：排盘 + 秒回安抚生成（与意图分析重叠执行）。

        线程安全：engine.calculate 为确定性本地计算（无共享可变状态）；
        _gen_instant_reply 仅做只读访问 + LLM 调用（httpx 线程安全）。
        """
        try:
            result = self.engine.calculate(year, month, day, hour, minute,
                                           city, gender,
                                           solar_time=solar_time)
            return self._gen_instant_reply(result)
        except Exception as e:
            logger.warning("预生成失败: %s", e)
            return ""

    def _consume_pregen_instant(self, user_id: str) -> Optional[str]:
        """取预生成的秒回安抚；未就绪/失败 → None（调用方同步兜底，行为与旧版一致）。

        Task 2 终审修复：有限等待 0.5s——worker 在途且 0.5s 内就绪时优先消费
        在途结果，避免主线程重复发 flash（一轮 2 次 LLM 调用）；超时/异常 →
        None 走同步兜底。绝不阻塞主流程超过 0.5s。
        """
        import concurrent.futures
        try:
            future = self._pregen_instant.pop(user_id, None)
            if future is None:
                return None
            return future.result(timeout=0.5)
        except concurrent.futures.TimeoutError:
            return None
        except Exception:
            return None

    # ============================================================
    # AI 原生（Phase 1）— <tool_call> 工具调用循环（方案 3.2/3.3）
    # ============================================================

    # R1-3（评测 T074 修复·无关问题误调 web_search）：金融行情词
    # （股市/行情/股票/基金/大盘/指数/股价/涨跌/炒股/收盘/开盘）显式排除——
    # 本产品无行情数据源，行情问题只能诚实说明，不得搜索不得编造。
    # k11b（2026-09-07）：原「研究类话题关键词硬门控」（_WEB_SEARCH_RESEARCH_RE）
    # 降级为语义触发判定（src/rag/search_trigger.py）的一部分——词表原文迁移到
    # 该模块 RESEARCH_WHITELIST_RE 作兜底层；判定改为实体层/时效层/白名单正信号
    # − 金融排除 − 命理本地锚（防「今年运势如何」乱搜回归）。触发判定不经频控/
    # 长度/域名护栏（护栏在执行侧：_search_rate_ok、_parse_result_domains、
    # 结果块长度钳制）。

    def _web_search_allowed(self, msg: str) -> bool:
        """消息是否允许触发联网搜索（k11b 起=语义触发判定，chat 域 tool-loop/
        needs_search 引导共用；金融行情/命理本地概念仍硬否决，T074 语义沿袭）。"""
        return _decide_search(msg, llm_needs_search=False).should_search

    def _tool_loop_analysis_hint(self, analysis: Optional[MessageAnalysis],
                                 msg: str = "") -> str:
        """阶段 2/3 最小实现：把理解 JSON（附加需求/联网需求）转成注入下一轮 LLM 的提示。

        - secondary_needs：要求逐项覆盖（多需求不遗漏）
        - needs_search 且搜索工具可用：引导 LLM 按需输出 web_search JSON 工单查证（Task 5）
        - R1-3（T074）：needs_search 仅当 _web_search_allowed(msg) 命中研究类
          白名单才注入引导——无关问题（股市行情等）不得把用户引向搜索。
        """
        if not analysis:
            return ""
        hints = []
        sn = getattr(analysis, "secondary_needs", None) or []
        if sn:
            hints.append(
                "【附加需求】用户还提到：" + "、".join(str(x) for x in sn[:5])
                + "。回复需逐项覆盖这些需求，不要遗漏。"
            )
        if getattr(analysis, "needs_search", False) and self._web_search_allowed(msg):
            try:
                from src.rag.web_search import web_search_available
                if web_search_available():
                    hints.append(
                        "【实时信息】此问题依赖实时信息（公司/行业/时事/最新数据）。"
                        "请先输出一次 <tool_calls>[{\"tool\": \"web_search\", "
                        "\"params\": {\"query\": \"具体关键词\"}}]</tool_calls> "
                        "获取实时信息，再基于搜索结果继续回答，不要凭记忆编造行业现状数据；"
                        "若搜索不可用，则明确告知用户"
                        "「实时信息暂不可用，以下按命理知识分析」。"
                    )
            except Exception:
                pass
        return "\n".join(hints)

    def _run_tool_loop(self, msg: str, user_id: str, reply: str,
                       stream_cb: Optional[Callable] = None,
                       analysis: Optional[MessageAnalysis] = None,
                       session_id: Optional[str] = None,
                       chart_draft: Optional[str] = None) -> str:
        """检测回复中的 <tool_call> 标签 → 执行工具 → 结果以 system 注入 → 再次调 LLM。

        - 最多 MAX_TOOL_ITERATIONS（2）次迭代，防死循环
        - 解析失败/LLM 调用失败：静默降级，返回原文（去标签）
        - 工具执行失败：错误注入 system 提示，对话继续

        阶段 2/3 最小实现（方案 v5）：
        - analysis.secondary_needs → 注入"附加需求逐项覆盖"（多需求不遗漏）
        - analysis.needs_search 且搜索可用 → 注入联网搜索引导（LLM 决定是否输出 web_search JSON 工单）

        stream_cb（v8 阶段 3）：工具执行前回调 ("tool", {"text": ...}) 事件
        （前端思考路径逐步点亮），后续 LLM 调用走真实流式。
        """
        # Task 2 阶段计时：整段工具循环（工具执行 + LLM 调用），统一格式便于 grep；
        # 空回复早退也输出 timing（计时覆盖异常/早退路径，宁多勿缺）
        _t0 = time.monotonic()
        if not reply:
            logger.info("[timing] stage=tool_loop duration=%.1fs",
                        time.monotonic() - _t0)
            return reply
        # L5-2 修复（降级成本）：降级链路禁用工具循环——lite prompt 已禁工具，
        # 这里做防御性门控：即使回复残留 <tool_call> 标签也不执行工具、不再调
        # LLM（原实现 read-then-act：降级用户仍可能触发检索/搜索等昂贵工具）。
        if self._downgraded.get(user_id, False):
            logger.info("[timing] stage=tool_loop duration=%.1fs (downgraded, disabled)",
                        time.monotonic() - _t0)
            return strip_tool_calls(reply) or reply
        api_key = getattr(self.llm, 'api_key', '') if self.llm else ''
        if not api_key:
            logger.info("[timing] stage=tool_loop duration=%.1fs",
                        time.monotonic() - _t0)
            return strip_tool_calls(reply) or reply

        history = None
        if self.session_dao:
            try:
                # 会话历史末尾即当前用户消息（assistant 回复尚未保存）
                history = self.session_dao.get_context_for_llm(
                    user_id, history_limit=20, session_id=session_id)
            except Exception:
                history = None

        # 阶段 2（方案 §7.2）：本轮工具调用日志（落库：tool_calls / retrieval_hit）
        executed_calls: list = []
        # Task 3B review I-1 + B1-3/B1-4：双协议并存 → 同参数重复执行防护。
        # executed_keys：本轮已执行 (name, 结构化 params) 去重表，值 = 该次执行的
        # ToolResult（原生块被去重跳过时复用其 text 回传 tool_result，保证协议完整：
        # Anthropic 协议要求每个 tool_use 必须有匹配的 tool_result；JSON 工单路径
        # 被去重跳过时复用整份结果注入 LLM，幂等语义）。
        # 去重键用结构化 params（sort_keys 序列化归一，避免字段顺序差异误判）；
        # 文本标签调用（无 params_obj）若参数本身是 JSON 则解析归一为 dict 键
        # （B1-3：非规范书写也能跨协议去重）；纯文本参数与结构化 dict 形不同、
        # 语义归一不可行（legacy 文本标签正在淘汰），按设计固化不跨协议去重。
        # 不同参数 → 重新调用合法，不误杀。
        executed_keys: dict = {}
        retrieval_hit = "unused"

        def _exec_key(c: ToolCall) -> tuple:
            """本轮执行去重键：name + 结构化 params（params_obj 优先）。"""
            if c.params_obj is not None:
                return (c.name, json.dumps(c.params_obj, sort_keys=True,
                                           ensure_ascii=False))
            p = (c.params or "").strip()
            if p.startswith("{"):
                try:
                    obj = json.loads(p)
                except json.JSONDecodeError:
                    obj = None
                if isinstance(obj, dict):
                    return (c.name, json.dumps(obj, sort_keys=True,
                                               ensure_ascii=False))
            return (c.name, c.params)
        # 阶段 5：本轮引用来源（user_id → list）由工具/处理器注册；
        # 不在本方法清空（处理器注册的引用要保留到本方法末尾统一校验）

        # Task 3B（批次 1）：deepseek 原生 tool_use 优先，JSON 工单兜底（双协议并存）。
        # 原生链状态：
        #   native_messages：原生链累积消息（含 assistant tool_use content + tool_result），
        #                     None = 未进入原生链
        #   native_pending：待执行的原生 tool_use 块（已解析为 ToolCall 列表）
        # 原生路径任何一步异常 → 立即降级走下方 JSON 工单路径（try/except 包住原生链）
        from src.bot.capability_registry import build_tool_schema_list
        from src.llm.client import (deepseek_anthropic_completion,
                                    deepseek_anthropic_messages)
        from src.utils.text_clean import strip_emoji
        # review M-1：显式门控——仅 provider == "deepseek" 走原生 tool_use 链；
        # 其余（含未设置 provider 的 GLM 主模型）一律走 JSON 工单路径，
        # 避免拿 GLM key 打 deepseek 端点（原"缺省即 deepseek"是隐患）
        use_native = getattr(self.llm, 'provider', None) == 'deepseek'
        native_messages: Optional[list] = None
        native_pending: list = []
        _llm_model = self.llm.model or "deepseek-flash"
        if not isinstance(_llm_model, str):
            _llm_model = "deepseek-flash"

        def _extract_native_text(data: dict) -> str:
            """从完整响应 dict 提取首段 text（与 deepseek_anthropic_completion 同规则）。"""
            for b in data.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "text":
                    return strip_emoji(b.get("text", "").strip())
            return ""

        for _ in range(MAX_TOOL_ITERATIONS):
            # ---- 原生 tool_use 链（Task 3B）：执行上轮块 → tool_result 回传 → 再调 LLM ----
            # MAX_TOOL_ITERATIONS=2 语义：迭代 1 = JSON 工单执行 + 带 tools 的转换调用；
            # 迭代 2 = 原生块执行/tool_result 回传；迭代 2 若再出 tool_use 块
            # （native_pending 非空）→ continue 后循环即耗尽，新块被静默丢弃
            # （不执行、不落库、不回传），与 GLM 路径行为无关。
            # B1-9 设计护栏固化：真实搜索一轮收敛为常态，迭代 2 的新块是幻觉/
            # 失控信号，丢弃是红线设计而非缺陷（测试锁死防回潮）。
            native_chain_ran = False
            native_reply_updated = False
            if native_messages is not None:
                native_chain_ran = True
                try:
                    if native_pending:
                        # 协议（Anthropic）：assistant 消息中所有 tool_use 块必须在其后
                        # 紧邻的同一条 user 消息中全部回传匹配 tool_result（不允许逐块
                        # 各发一条 user 消息）。单轮多原生块（deepseek 并行双查询高频）
                        # 若逐块回传 → 真实 API 400 "ids were found without tool_result
                        # blocks immediately after" → 整链降级。故循环内仅累积，循环后
                        # 一次性 append 单条 user 消息（content = 全部 tool_result 数组）。
                        tool_results = []
                        for c in native_pending:
                            ckey = _exec_key(c)
                            if ckey in executed_keys:
                                # review I-1：同参数本轮已执行过（JSON 工单或更早的
                                # 原生块）→ 跳过重复执行（写型工具不重复落库/不双分配
                                # 引用编号）；但 tool_use 必须回传匹配 tool_result →
                                # 复用上次结果文本（幂等语义），不发"正在…"事件、不落库
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": c.tool_use_id,
                                    "content": executed_keys[ckey].text,
                                })
                                continue
                            # R1-2（T092 no_tool 契约）：bazi 路由链路本轮已排盘
                            # （engine_draft 已在上下文），润色 LLM 再输出「排盘」
                            # 块属冗余重执行（重复执行被 L1 严格序列匹配判 FAIL，
                            # T092 实锤：期望零工具调用，实际 1 次 bazi_chart）
                            # → 不执行不记录，协议完整回传引擎原稿（零信息损失）
                            if chart_draft is not None and c.name == "排盘":
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": c.tool_use_id,
                                    "content": chart_draft,
                                })
                                continue
                            # R1-3（T074 修复·无关问题误调 web_search）：chat 域
                            # 问题（股市行情等）即使 LLM 输出搜索工单也不执行——
                            # 跳过执行（不记 executed_calls → tool_log 无 calls，
                            # 场景兜底照常），回传占位 tool_result 让 LLM 诚实
                            # 说明，不编造行情数据（与 JSON 工单路径同门控）。
                            if c.name in ("web_search", "搜索") and not self._web_search_allowed(msg):
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": c.tool_use_id,
                                    "content": "实时信息暂不可用，以下按命理知识分析。",
                                })
                                continue
                            # 与下方 JSON 工单路径完全一致的执行/事件/落库语义
                            if stream_cb is not None:
                                try:
                                    stream_cb("tool", {"text": _TOOL_EVENT_LABELS.get(
                                        c.name, f"正在{c.name}…")})
                                except Exception:
                                    pass
                            r = self._execute_tool_call(
                                c.name,
                                self._with_relative_cycle_year(
                                    c.name,
                                    c.params_obj if c.params_obj is not None else c.params,
                                    msg),
                                user_id, user_question=msg)
                            executed_keys[ckey] = r  # 供后续同参数原生块/工单去重
                            executed_calls.append({
                                "type": r.name,
                                "params": (json.dumps(c.params_obj, ensure_ascii=False)
                                           if c.params_obj is not None
                                           else (c.params or ""))[:200],
                                "hit": bool(r.ok),
                            })
                            if r.name in ("检索", "搜索"):
                                retrieval_hit = "hit" if r.ok else "miss"
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": c.tool_use_id,
                                "content": r.text,
                            })
                        native_messages.append({"role": "user",
                                                "content": tool_results})
                        native_pending = []
                    data = deepseek_anthropic_messages(
                        api_key, native_messages, model=_llm_model,
                        max_tokens=2000, temperature=0.7, timeout=60.0,
                        tools=build_tool_schema_list())
                    blocks = [b for b in (data.get("content") or [])
                              if isinstance(b, dict) and b.get("type") == "tool_use"]
                    pending = parse_native_tool_use_blocks(blocks) if blocks else []
                    if pending:
                        native_pending = pending
                        # assistant 消息必须带完整 content（含 tool_use 块），
                        # 协议要求 tool_result 匹配同一 tool_use_id
                        native_messages.append({"role": "assistant",
                                                "content": data.get("content")})
                        continue  # 下一轮开头执行块
                    native_text = _extract_native_text(data)
                    if native_text:
                        reply = native_text
                        native_reply_updated = True
                    native_messages = None  # 原生链结束 → 落入 JSON 工单解析（双协议并存）
                except Exception as e:  # noqa: BLE001 — 原生链任何一步异常 → 降级 JSON 工单
                    logger.warning("原生 tool_use 链异常，降级 JSON 工单: user=%s err=%s",
                                   user_id, str(e)[:200])
                    native_messages = None
                    native_pending = []
            # 原生链无新文本（异常/空响应）：原工单已执行过，不再重放 → 静默返回原文
            if native_chain_ran and not native_reply_updated:
                break

            # ---- 现有 JSON 工单路径（兜底，Task 3 协议完好）----
            calls = parse_tool_calls(reply)
            # R1-2（T092 no_tool 契约）：本轮已排盘（chart_draft 注入）时
            # 丢弃冗余「排盘」工单——静默无执行、无 LLM 二次生成（回复层
            # strip 兜底），引擎原稿已在上下文，零信息损失；L1 不双计。
            if chart_draft is not None:
                calls = [c for c in calls if c.name != "排盘"]
            if not calls:
                break
            logger.info("工具调用执行 user=%s calls=%s",
                        user_id, [c.name for c in calls])
            visible = strip_tool_calls(reply)
            results = []
            for c in calls:
                ckey = _exec_key(c)
                if ckey in executed_keys:
                    # B1-4：JSON 工单轮内自重复（同一轮 reply 内相同工单）/与更早
                    # 执行（原生块或同轮工单）同参数 → 跳过重复执行（写型工具不
                    # 重复落库/不双分配引用编号），复用首次结果整份注入 LLM
                    # （幂等语义，与原生链去重一致：不发"正在…"事件、不落库）
                    results.append(executed_keys[ckey])
                    continue
                # R1-3（T074 修复·无关问题误调 web_search）：chat 域问题不执行
                # 搜索工单（同原生链门控）——占位结果注入 LLM 循环，工具调用
                # 不落库（executed_calls 空 → tool_log 无 calls → 场景兜底照常）。
                if c.name in ("web_search", "搜索") and not self._web_search_allowed(msg):
                    results.append(ToolResult(
                        "搜索", False, "实时信息暂不可用，以下按命理知识分析。"))
                    continue
                # v8 阶段 3：工具调用前先发"思考路径"事件（前端点亮 ● → ✓）
                if stream_cb is not None:
                    try:
                        stream_cb("tool", {"text": _TOOL_EVENT_LABELS.get(
                            c.name, f"正在{c.name}…")})
                    except Exception:
                        pass
                # 批次 1（spec 1.2）：结构化工单的 dict 参数必须传进执行层，
                # 否则校验/序列化永远不会触发（文本标签兜底保持原样）
                r = self._execute_tool_call(
                    c.name,
                    self._with_relative_cycle_year(
                        c.name,
                        c.params_obj if c.params_obj is not None else c.params,
                        msg),
                    user_id, user_question=msg)
                executed_keys[ckey] = r  # 供原生块/后续工单同参数去重（review I-1/B1-4）
                results.append(r)
                executed_calls.append({
                    "type": r.name,
                    # M-1（Task 4 review）：结构化工单 params_obj 也要落库（JSON 序列化），
                    # 否则 DB tool_calls 字段只记空串丢失参数详情
                    "params": (json.dumps(c.params_obj, ensure_ascii=False)
                               if c.params_obj is not None else (c.params or ""))[:200],
                    "hit": bool(r.ok),
                })
                if r.name in ("检索", "搜索"):
                    retrieval_hit = "hit" if r.ok else "miss"

            messages = [{"role": "system", "content":
                "你是易理明灯，请基于工具执行结果继续自然地完成你的回复。"
                "回答纪律：直接专业作答，禁止油滑/套近乎开场白；"
                "严格紧扣用户问题，用户没问的（名人相似、旁支话题）不得主动展开。"
                + self._tool_loop_analysis_hint(analysis, msg)
                + "\n\n" + build_tool_description()}]
            if history:
                messages.extend(history)
            else:
                messages.append({"role": "user", "content": msg})
            if visible:
                messages.append({"role": "assistant", "content": visible})
            results_text = format_tool_results_json(results)
            messages.append({
                "role": "system",
                "content": (
                    "[工具执行结果]\n" + results_text +
                    "\n\n请基于以上结果继续完成你的回复（把结果消化成自然语言，"
                    "不要提 <tool_call> 标签）。\n"
                    "引用规则：资料带编号 [n] 时，**只引用与用户问题直接相关的内容**，"
                    "凡引用资料中的内容，必须在相关陈述后标注编号"
                    "（如「古籍《X》载：…[1]」）；"
                    "不相关的内容忽略，不要引用、不要编造出处。\n"
                    "如果工具提示缺少信息，就自然地向用户询问缺失的信息。"
                    "不要再输出任何工具调用标记（如确有新工具需要，"
                    "按 <tool_calls> JSON 工单格式输出）。"
                ),
            })
            # 合并相邻 system 消息（回复无可见文字时会产生连续 system，部分端点不兼容）
            merged = []
            for m in messages:
                if merged and m["role"] == "system" and merged[-1]["role"] == "system":
                    merged[-1] = {"role": "system",
                                  "content": merged[-1]["content"] + "\n\n" + m["content"]}
                else:
                    merged.append(m)
            messages = merged
            try:
                if use_native:
                    # deepseek provider：带 tools 拿完整响应 dict，原生块优先执行
                    data = deepseek_anthropic_messages(
                        api_key, messages, model=_llm_model,
                        max_tokens=2000, temperature=0.7, timeout=60.0,
                        tools=build_tool_schema_list())
                    blocks = [b for b in (data.get("content") or [])
                              if isinstance(b, dict) and b.get("type") == "tool_use"]
                    pending = parse_native_tool_use_blocks(blocks) if blocks else []
                    if pending:
                        # 转原生链：assistant 消息带 tool_use 块，下一轮执行
                        native_pending = pending
                        native_messages = messages + [
                            {"role": "assistant", "content": data.get("content")}]
                        continue
                    new_reply = _extract_native_text(data)
                else:
                    # GLM/其他 provider：保持现状（流式文本路径）
                    # k11-E：流式转发前包 chunk 级工具 JSON 过滤（1732 先流式
                    # 1747 后 strip 的竞态——JSON 若先出已到前端，此处拦截）
                    new_reply = deepseek_anthropic_completion(
                        api_key, messages, model=_llm_model,
                        max_tokens=2000, temperature=0.7, timeout=60.0,
                        stream_cb=self._wrap_ctx_scrub_cb(stream_cb, user_id),
                    )
            except Exception:
                break  # LLM 调用失败 → 静默降级返回原文
            if not new_reply:
                break
            reply = new_reply

        # ---- k37 S5：工具循环触顶收尾（MAX_TOOL_ITERATIONS=2 不改、末轮新块
        # 仍不执行/不落库/不回传——B1-9 红线零变化）。此前触顶后 pending 被静默
        # 丢弃，用户拿到的是上一轮引导句，已执行的工具结果没被消化成回答。
        # 现在追加**恰一次**不带 tools 的收尾调用，把工具结果写成回答
        # （native_pending 非空 ⟺ 循环是在末轮 continue 时耗尽的唯一路径；
        # 其余 break 出口均已带新文本或已把 native_pending 清空）。
        if native_pending and native_messages:
            try:
                closing_messages = list(native_messages)
                if closing_messages and closing_messages[-1].get("role") == "assistant":
                    # 末条 assistant 带未执行的 tool_use 块：协议要求每个 tool_use
                    # 必须紧跟匹配 tool_result，而这些块按红线不执行 → 整条丢弃
                    # （不补假 tool_result），再请模型基于已执行结果收尾。
                    closing_messages = closing_messages[:-1]
                closing_instruction = (
                    "以上是工具执行结果，请直接据此用自然语言回答用户的问题，"
                    "不要再发起任何工具调用。")
                # k37 审查 I-1（Important）：收尾指令必须作为 text 块**并入**上一条
                # user 消息（其 content 是 tool_result 数组），而**不是**新起第二条
                # user 消息。规范形状 = assistant(tool_use) / user(tool_result + text)
                # ——Anthropic 对连续同角色消息报 400（"roles must alternate…"，
                # Bedrock 至今拒绝，第一方才做自动合并）；若新起一条 user，末三条会
                # 成为 assistant / user(tool_result) / user(text)，异常被下方 except
                # 吞成 warning → 收尾在线上静默失效（S5 等于没修）。故此处严格保持
                # 单条 user：tool_result 块在前、指令 text 块在后。
                if closing_messages and closing_messages[-1].get("role") == "user":
                    last_user = dict(closing_messages[-1])
                    _content = last_user.get("content")
                    if isinstance(_content, list):
                        blocks = list(_content)          # 拷贝：不改动 native_messages
                    elif _content:
                        blocks = [{"type": "text", "text": _content}]
                    else:
                        blocks = []
                    blocks.append({"type": "text", "text": closing_instruction})
                    last_user["content"] = blocks
                    closing_messages[-1] = last_user
                else:
                    # 防御：末条非 user（且末尾 assistant 已丢）时退化为单条 user，
                    # 仍不与前一条同角色（前一条此时不可能是 user）。
                    closing_messages.append({
                        "role": "user",
                        "content": [{"type": "text", "text": closing_instruction}],
                    })
                data = deepseek_anthropic_messages(
                    api_key, closing_messages, model=_llm_model,
                    max_tokens=2000, temperature=0.7, timeout=60.0,
                    tools=None)
                closing_text = _extract_native_text(data)
                if closing_text:
                    reply = closing_text
            except Exception as exc:  # noqa: BLE001 — 收尾失败不阻断：返回原文（原语义）
                logger.warning("工具循环触顶收尾调用失败: user=%s type=%s",
                               user_id, type(exc).__name__)

        # 阶段 2（方案 §7.2）：本轮工具调用日志（落库：tool_calls / retrieval_hit）
        self._tool_logs[user_id] = {
            "calls": executed_calls,
            "retrieval_hit": retrieval_hit,
        }

        # 兜底：去掉残留标签
        cleaned = strip_tool_calls(reply)
        # 阶段 5·回答后引用校验（方案 §3.2 ③）：不相关 [n] 剔除，来源收窄
        cleaned = self._verify_and_keep(user_id, cleaned, msg)
        logger.info("[timing] stage=tool_loop duration=%.1fs",
                    time.monotonic() - _t0)
        return cleaned or reply

    # ============================================================
    # 阶段 5·引用来源注册（方案 §3.0/§3.2）：工具结果统一带来源类型
    # ============================================================

    def _alloc_citations(self, user_id: str, count: int) -> int:
        """为本轮工具结果分配连续引用编号，返回起始编号（1-based）。"""
        bucket = self._citations.setdefault(user_id, [])
        return len(bucket) + 1

    def _append_citations(self, user_id: str, items: list) -> None:
        """注册本轮来源条目（调用方已用 _alloc_citations 分配好 index）。"""
        bucket = self._citations.setdefault(user_id, [])
        bucket.extend(items)

    def pop_citations(self, user_id: str) -> list:
        """API 层取走本轮校验后的引用来源列表（取走即清空，防泄漏到下轮）。"""
        return self._citations.pop(user_id, None) or []

    def _verify_and_keep(self, user_id: str, reply: str, question: str) -> str:
        """回答后引用校验（方案 §3.2 ③）：不相关 [n] 剔除标记，来源列表收窄。"""
        citations = self._citations.get(user_id) or []
        if not citations:
            return reply
        try:
            cleaned, kept = verify_citations(reply, question, citations)
        except Exception:  # noqa: BLE001 — 校验失败放行
            cleaned, kept = reply, citations
        self._citations[user_id] = kept
        return cleaned or reply

    # ============================================================
    # 方案 B·引擎结果注入（AI 原生统一）：有意图的问题也走
    # LLM 自主生成 + 工具循环 + 引用注册（方案 §3.0/§3.2，修复硬路由）
    # ============================================================

    def _register_engine_citation(self, user_id: str, text: str, title: str = "",
                                  source: str = "引擎") -> None:
        """阶段 5·来源体系 ②：引擎计算结果注册为 engine 来源（排盘/卦象/运势…）。"""
        if not text:
            return
        idx = self._alloc_citations(user_id, 1)
        self._append_citations(user_id, [make_citation(
            idx, "engine", text[:600],
            title=(title or "引擎分析")[:120], source=(source or "引擎")[:120],
        )])

    def _register_book_citations(self, user_id: str, refs: list, limit: int = 5,
                                 title: str = "古籍参考", source: str = "古籍库") -> None:
        """阶段 5·来源体系 ①：处理器内部检索到的古籍片段注册为 book 来源。

        refs: retriever.search 返回的 ChunkResult 列表（也兼容 dict / 纯文本）。
        """
        if not refs:
            return
        items = []
        start = self._alloc_citations(user_id, min(limit, len(refs)))
        for i, ref in enumerate(list(refs)[:limit], start=start):
            # k24 补丁二：本函数此前自行 isinstance 归一化且**只读 source** ——
            # 与「读取检索结果只走 ref_title/ref_text」的契约自相矛盾，FAISS dict
            # 进来会渲染《daizhige》这类语料 slug。改用契约。
            # 契约的兜底值 "古籍" 表示「无出处信息」→ 回落到调用方给定的分类标题
            # （如「紫微 · 古籍参考」），保持原有语义不劣化。
            src = ref_title(ref)
            text = ref_text(ref)[:400]
            if not text:
                continue
            if src == "古籍":
                src = ""
            items.append(make_citation(
                i, "book", text,
                title=(f"《{src}》" if src and "《" not in src else src) or title,
                source=source,
            ))
        if items:
            self._append_citations(user_id, items)

    def _polish_with_engine_draft(self, msg: str, user_id: str, draft: str,
                                  stream_cb: Optional[Callable] = None,
                                  extra_hint: str = "",
                                  search_hint: str = "",
                                  session_id: Optional[str] = None) -> str:
        """方案 B·引擎结果注入：把 _handle_* 的引擎分析结果作为「引擎草稿」
        注入 system → LLM 以豆包式语气生成最终回复（AI 原生架构统一）。

        - 草稿的系统尾巴（反馈提示/版本页脚）先剥离、润色后原样回接
        - 命盘图片链接（📊…http…）若被 LLM 丢弃则自动补回
        - 本轮已注册引用（engine/book）随提示注入，LLM 需要时在陈述后标 [n]
        - extra_hint: P2 话题提示（重复话题/演化链），注入 system 引导
        - search_hint: P3 联网激活——needs_search 时作为「第 4 条要求」置顶，
          让搜索成为硬性指令（旧逻辑只在不置顶的附加提示里，LLM 常忽略）
        - LLM 仍可输出 <tool_call>（补检索古籍/网络）→ 由 _run_tool_loop 执行
        - LLM 失败/空结果 → 原样返回草稿（静默降级，行为不劣于现状）
        """
        if not draft:
            return draft
        # v2026-08-17（思考过程元宝式）：润色阶段（4~5s LLM 调用）此前无任何
        # 思考事件，前端思考胶囊长时间静止（PM：步骤不轮换）——补一步
        self._emit_stream_event(stream_cb, "thinking", "正在整理思绪…")
        api_key = getattr(self.llm, 'api_key', '') if self.llm else ''
        # Mock 兼容：Mock 对象不是 str（单元测试用 Mock llm 时不发起真实网络调用）
        if not isinstance(api_key, str) or not api_key:
            return draft
        model = getattr(self.llm, 'model', '') or "deepseek-flash"
        if not isinstance(model, str):
            model = "deepseek-flash"

        # 1) 剥离系统尾巴（版本页脚/反馈提示），润色后原样回接
        body = draft
        tail = ""
        # D2 修复：页脚含实时时间戳（get_version_footer 每次调用取当前秒），
        # 起草与润色跨秒时逐字 endswith 失配 → 页脚被当正文丢进 LLM、
        # tail='' → 页脚永久丢失。改按格式正则剥离，跨秒/格式微差仍可回接。
        # （B3-2-C 后页脚已不再生成，本剥离保持为防御性 no-op：存量草稿/消息
        # 含页脚时仍可正确剥离回接，不产生行为变化）
        m = re.search(
            r'\n?---\n解读版本: v[\d.]+ \| 生成时间: [^\n]+'
            r'(?:\n同一八字同一问题，结果始终一致)?$', body)
        if m:
            body = body[:m.start()].rstrip()
            tail = "\n\n" + m.group(0).strip("\n")
        # D2 修复：_FEEDBACK_PROMPT（「准」/「不准」）与 _add_feedback_prompt
        # （💬👍👎）文案曾不一致，引擎草稿实际以 emoji 版结尾 → 原 endswith
        # 只匹配纯文本版 → 反馈提示被当正文丢进 LLM、润色后丢失。按任一
        # 版本剥离，润色后统一回接纯文本版（_handle_feedback 按「准/不准」识别）。
        for fb in (_FEEDBACK_PROMPT, _FEEDBACK_PROMPT_EMOJI):
            if body.endswith(fb):
                body = body[: -len(fb)].rstrip()
                tail = f"\n\n{_FEEDBACK_PROMPT}" + tail
                break

        # 2) 命盘图片链接保底（LLM 润色可能丢弃链接）
        chart_url = ""
        m = re.search(r'📊[^\n]*(?:https?://\S+)', body)
        if m:
            chart_url = m.group(0).strip()

        # 3) 本轮已注册引用注入提示（LLM 需要时标注 [n]）
        citations = self._citations.get(user_id) or []
        def _cite_line(c):
            # 附内容摘录：仅标题（如多条"解梦 · 古籍参考"）时 LLM 无法判断
            # [n] 指向什么内容，标注引用无从下手
            excerpt = (c.get("text") or "").replace("\n", " ").strip()
            if excerpt:
                excerpt = excerpt[:50] + ("…" if len(excerpt) > 50 else "")
            base = f"[{c.get('index')}] {type_label(c.get('type'))}《{c.get('title', '')}》"
            return base + (f"｜{excerpt}" if excerpt else "")

        cite_hint = "；".join(_cite_line(c) for c in citations) or "（暂无）"

        system = (
            "你是易理明灯，一位懂命理的温暖朋友。说话像豆包：口语化、有温度、"
            "自然不端着，把专业术语讲成大白话。"
            "回答纪律：直接专业作答，禁止油滑/套近乎开场白（如「哈哈」「挺有意思」）；"
            "严格紧扣用户问题，用户没问的（名人相似、旁支话题）不得主动展开。\n"
            "下面是系统命理引擎刚算出的专业分析结果（真实数据，不是聊天内容）：\n"
            "【引擎分析结果】\n"
            f"{body}\n"
            "【引擎分析结果结束】\n\n"
            "请把以上结果润色成一段发给用户的自然回复。要求：\n"
            + (("0. 【硬性要求·实时信息】此题依赖实时信息（行业/公司/时事/最新数据）。"
                "你必须先输出一次 "
                "<tool_calls>[{\"tool\": \"web_search\", \"params\": "
                "{\"query\": \"具体关键词\"}}]</tool_calls> 让系统联网查证，"
                "收到搜索结果后再完成最终回复；不要凭记忆编造行业现状数据；"
                "若搜索不可用，则明确告知用户「实时信息暂不可用，以下按命理知识分析」。\n")
               if search_hint else "")
            + "1. 保留全部实质性数据（四柱/十神/卦象/日期/评分/宜忌条目等），"
            "可以调整表达结构，但不要删改、不要编造数据；"
            "【硬性要求】四柱干支（年月日时）必须与引擎结果逐字一致，"
            "禁止自行推算、改写或替换任何干支；\n"
            "2. 像朋友聊天一样组织语言，不要提及「引擎」「草稿」「检索」「系统」"
            "等技术词汇；\n"
            f"3. 引用规则：本轮可用来源：{cite_hint}。回答中用到来源里的具体数据、"
            "古籍记载或命盘信息时，必须在对应陈述后标注编号"
            "（如「古籍《X》载：…[1]」「你的命盘：庚午年…[1]」），"
            "且至少标注 1 个实际使用到的编号（全部内容均与来源无关时才可不标）；"
            "只标注与用户问题直接相关的内容，不相关不标注；\n"
            "4. 如果还缺少依据，可以输出一次 "
            "<tool_calls>[{\"tool\": \"web_search\", \"params\": "
            "{\"query\": \"需要联网查证的关键词\"}}]</tool_calls> 联网查证，"
            "系统会执行后把结果交回，你再继续完成回复；\n"
            "5. 若原结果本身已是清晰的列表/卡片格式（如宜忌、时辰表、排盘卡片），"
            "宜忌/时辰表等表格用 markdown 表格格式呈现、不要用代码块包裹，"
            "保持该结构完整，不要合并或删减条目，仅补充口语化的开头和结尾；\n"
            "6. 直接输出给用户的回复文本，不要解释过程；\n"
            "7. 【硬性要求】回复中禁止使用任何 emoji 表情符号"
            "（表情图标、颜文字、装饰符号都不用），只用文字与中文标点表达语气。"
            + (("\n\n" + extra_hint) if extra_hint else "")
            + "\n\n[可用工具清单]\n" + build_tool_description()
        )

        logger.info("引擎润色注入 user=%s search_hint=%s extra_hint=%s",
                    user_id, bool(search_hint), bool(extra_hint))
        messages = [{"role": "system", "content": system}]
        history = None
        if self.session_dao:
            try:
                # 会话历史末尾即当前用户消息（assistant 回复尚未保存）
                history = self.session_dao.get_context_for_llm(
                    user_id, history_limit=20, session_id=session_id)
            except Exception:
                history = None
        if history:
            messages.extend(history)
            if search_hint:
                messages.append({"role": "user", "content":
                    "（请按上面的硬性要求先输出 "
                    "<tool_calls>[{\"tool\": \"web_search\", \"params\": "
                    "{\"query\": \"具体关键词\"}}]</tool_calls> 联网查证后再继续）"})
        else:
            messages.append({"role": "user", "content": msg})
        _t0 = time.monotonic()
        try:
            from src.llm.client import deepseek_anthropic_completion
            # k11-E：polish 全程真流式且 search_hint 时被要求先输出 JSON 工单
            # （1960-1965）——转发前包 chunk 级过滤，开场 JSON 不落用户可见流
            _polish_cb = self._wrap_ctx_scrub_cb(stream_cb, user_id)
            polished = deepseek_anthropic_completion(
                api_key, messages, model=model,
                # k7d：polish 输入为引擎完整稿（实测最长 ~2600 字符），2000 token
                # 上限会截断润色输出（实测断于 1140 字符半句），用户实时流看到
                # 半句稿 → 4000（实测 2870-3382 字符长流完整）。
                max_tokens=4000, temperature=0.7, timeout=60.0,
                stream_cb=_polish_cb,
            )
            logger.info("[timing] stage=polish duration=%.1fs",
                        time.monotonic() - _t0)
        except Exception:
            # 计时覆盖异常路径：失败也要有 stage=polish 输出（宁多勿缺）
            logger.info("[timing] stage=polish duration=%.1fs",
                        time.monotonic() - _t0)
            return draft  # LLM 失败 → 静默降级返回草稿
        if not polished:
            return draft
        polished = polished.strip()
        if len(polished) < 10:
            return draft
        # k7b：LLM 从会话历史仿写卡尾（卡头行+假图行+[/card]+假页脚）的净化
        # （只净化 LLM 输出；净化后再补的 chart_url/tail 即唯一装饰）
        polished = strip_card_decor_for_llm(polished)
        if not polished or len(polished) < 10:
            return draft
        if chart_url and chart_url not in polished:
            polished = polished + "\n\n" + chart_url
        return polished + tail

    def _execute_tool_call(self, name: str, params: Union[str, dict], user_id: str,
                           user_question: str = "") -> ToolResult:
        """执行单个工具调用，返回可注入对话的结果文本。

        批次 1（spec 1.2）：注册表分派 + 参数校验 + 超时重试。
        - params 为 dict（结构化工单 params_obj）：先校验（非法不执行）→ 序列化回文本
        - params 为 str（文本标签兜底）：不校验直接执行（旧行为，兼容期）
        - 超时/异常按 retries 重试，耗尽 → 兜底文案
        """
        cap = CAPABILITY_BY_NAME.get(name)
        if cap is None or cap.executor is None:
            return ToolResult(name, False, f"未知工具「{name}」，请直接和用户正常聊天。")
        if isinstance(params, dict):
            err = validate_params(cap.cap_id, params)
            if err:
                return ToolResult(name, False, err)  # 参数不合法：不执行、不计重试
            params = serialize_params(params)
        return self._run_with_timeout(cap, params, user_id, user_question)

    def _run_with_timeout(self, cap, params: str, user_id: str,
                          user_question: str = "") -> ToolResult:
        """带超时重试执行注册表 executor（线程池包装，不阻塞事件循环）。

        Task 4 review I-2：超时/异常后 shutdown(wait=False, cancel_futures=True)，
        不等待被超时的线程结束（旧 with 块默认 wait=True，重试要等前一线程跑完
        才开始）；僵尸线程靠工具内部超时终会结束（LLM 60s / 网络 15s），
        进程长驻兜底。成功路径线程已结束，shutdown 即刻返回。
        B1-6：解释器 atexit 对非 daemon 线程的 join 阻塞上界 = 工具内部超时
        （有界不卡死），测试固化于 test_timeout_zombie_thread_self_terminates_bounded。
        """
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
        for attempt in range(max(1, cap.retries + 1)):
            ex = ThreadPoolExecutor(max_workers=1)
            try:
                fut = ex.submit(cap.executor, params, user_id=user_id,
                                user_question=user_question)
                return fut.result(timeout=cap.timeout_s)
            except FutTimeout:
                pass  # 超时 → 重试（最后一次循环走失败兜底）
            except Exception:  # noqa: BLE001 — 工具异常重试后兜底
                pass
            finally:
                ex.shutdown(wait=False, cancel_futures=True)
        return ToolResult(
            cap.name, False,
            f"「{cap.name}」执行超时/异常（已重试{cap.retries}次），"
            "请基于已有信息继续回答，或明确告知用户该能力暂不可用。")

    def _tool_bazi(self, params: str, user_id: str) -> ToolResult:
        """工具「排盘」：解析出生信息（文本描述）→ BaziEngine.calculate。"""
        if self.engine is None:
            return ToolResult("排盘", False, "「排盘」工具暂不可用，请直接与用户聊天。")
        # R2-5：档案兜底持久化原始值标记（lunar 转公历排盘后不把原始输入
        # 改写/抹标——persons 全量替换回写路径防自毁；文本解析路径恒 None）
        _arch_raw = None
        # k11c（r1 审查 F2 对齐）：真太阳时开关——文本解析路径同样先查档案：
        # 本人（档案年份与消息一致）→ 随档案 solar_time（0=关=北京时间直排），
        # 与 _handle_bazi parsed 直排同口径（同句自我生辰经 tool/直达两路由
        # 四柱一致）；第三方消息/无档案 → 引擎默认开（消息级无开关语义）。
        # 档案兜底路径（parsed None）在下方分支内另行随档案取值。
        _solar_tool = True
        parsed = self._extract_bazi_info(params)
        if parsed is not None:
            try:
                _sp = self._get_user_birth_profile(user_id)
                if _sp and _sp.get("year") and _sp["year"] == parsed[0]:
                    _solar_tool = (_sp.get("solar_time") not in (0, "0", False))
            except Exception:
                _solar_tool = True
        if parsed is None:
            # Task 1 排盘档案打通：解析失败先试档案（bazi_info + persons 兜底）填参，
            # 年/月/日至少齐才排盘；hour/minute 缺省 0（与 _extract_bazi_info 缺时辰一致）
            profile = self._get_user_birth_profile(user_id)
            _st_raw = (profile or {}).get("solar_time")
            _solar_tool = (_st_raw not in (0, "0", False)) if profile else True
            if profile and profile.get("year") and profile.get("month") and profile.get("day"):
                _hour = (profile.get("hour") if profile.get("hour") is not None else 0)
                _minute = (profile.get("minute")
                           if profile.get("minute") is not None else 0)
                _city = profile.get("city") or ""
                _gender = profile.get("gender") or "unknown"
                # R2-5：档案 calendar=='lunar' → 单点转换公历再进引擎（引擎契约
                # =公历输入，用户阴历 1999-03-28 被当公历直排起运差 5 年 P0）。
                # 转换失败（非法农历日等）→ 安全回落原值排盘 + warning 不阻塞。
                _sol = None
                if str(profile.get("calendar") or "solar") == "lunar":
                    _sol = to_solar_date(profile)
                    if _sol is None:
                        logger.warning(
                            "R2-5 排盘档案兜底：lunar 档案转公历失败，按原始值排盘 "
                            "user=%s birth=%s-%s-%s", user_id,
                            profile.get("year"), profile.get("month"),
                            profile.get("day"))
                if _sol:
                    parsed = (_sol[0], _sol[1], _sol[2], _hour, _minute,
                              _city, _gender)
                    _arch_raw = (profile.get("year"), profile.get("month"),
                                 profile.get("day"))
                else:
                    parsed = (profile.get("year"), profile.get("month"),
                              profile.get("day"), _hour, _minute, _city,
                              _gender)
            else:
                return ToolResult(
                    "排盘", False,
                    "缺少出生信息，无法排盘。请向用户自然询问：出生年月日时、出生地点、性别"
                    "（性别影响大运走向，尽量问到）。用户若不知道准确时辰，"
                    "可以说明会按午时（中午11-13点）排盘参考。",
                    needs_info=True,
                )
        year, month, day, hour, minute, city, gender = parsed
        try:
            result = self.engine.calculate(
                year, month, day, hour, minute, city, gender,
                solar_time=_solar_tool)
        except Exception as e:
            return ToolResult("排盘", False, f"排盘引擎执行失败：{str(e)[:100]}")
        # 持久化：与 _do_bazi_analysis 保持一致的记忆/画像逻辑
        # 阶段 5（方案 v5）：subject=other（帮他人排盘）不写入本人档案（数据库层同步保护）
        # R2-5：lunar 档案兜底（引擎已按转换公历算）持久化保留原始值 + calendar
        # 标记——存储层=原始输入事实源，消费点单点转公历；不改写原值不抹标记
        # （_sync_person_profile → person_dao 全量替换 birth_enc，缺 calendar
        # 键会默认 'solar' 抹掉 lunar 标记 → 自毁式修复）。
        _persist = {
            "year": _arch_raw[0] if _arch_raw is not None else year,
            "month": _arch_raw[1] if _arch_raw is not None else month,
            "day": _arch_raw[2] if _arch_raw is not None else day,
            "hour": hour, "minute": minute,
            "city": city, "gender": gender,
        }
        if _arch_raw is not None:
            _persist["calendar"] = "lunar"
        try:
            _subject = (self._analysis_facts.get(user_id) or {}).get("subject", "self")
            _facts_this = self._analysis_facts.get(user_id) or {}
            if _subject != "other":
                _bazi_save = dict(_persist)
                _bazi_save["bazi"] = result.bazi
                self.dao.save_user_bazi(user_id, _bazi_save)
            # P2 多人档案：对话建档（subject=self 年份不同→新建命主N；other 按关系/姓名）
            self._sync_person_profile(user_id, _persist, subject=_subject,
                                      facts=_facts_this, birth_ctx=params)
            self.dao.save_consultation(user_id, params, result)
            # 排盘结果落库 chart_records（与 _save_bazi_records 同口径）
            self._persist_chart_result(user_id, result, _persist, _subject)
        except Exception as exc:  # noqa: BLE001 — 落库失败不阻断排盘主链（原语义）
            # k37 S4：静默 pass 补日志（此前落库失败无痕）。只记「事件 + 位置」：
            # 不带 user_id/生辰/参数，异常只留类型名（防 DB 错误串把数据带进日志）。
            logger.warning(
                "排盘工具结果落库失败: 事件=工具结果持久化 位置=handler._tool_bazi type=%s",
                type(exc).__name__)
        # 阶段 5（方案 v5）：subject=other（帮他人排盘）不写入本人画像；
        # gender 冲突不覆盖，冲突提示拼入工具结果由 LLM 综合时向用户确认
        _gender_conflict_hint = ""
        if self.memory_system:
            _subject = (self._analysis_facts.get(user_id) or {}).get("subject", "self")
            if _subject != "other":
                try:
                    conflict = self.memory_system.save_bazi_info(user_id, {
                        "year": year, "month": month, "day": day,
                        "hour": hour, "minute": minute,
                        "city": city, "gender": gender,
                        "bazi": result.bazi,
                        "day_master": getattr(result, "day_master", ""),
                    }, subject=_subject)
                except Exception:
                    conflict = {}
                if conflict:
                    _gender_conflict_hint = (
                        "（注意：本次提供的性别与你之前保存的不一致，"
                        "请向用户确认以哪一次为准，确认前不要按新性别改结论）"
                    )
                # L3（方案 §5.5 来源②）：八字 → profile 关键事实条目
                self._persist_l3_bazi(user_id, {
                    "year": year, "month": month, "day": day,
                    "hour": hour, "minute": minute,
                    "city": city, "gender": gender,
                    "bazi": result.bazi,
                    "day_master": getattr(result, "day_master", ""),
                })
        try:
            from src.engines.bazi_formatter import format_compact_card
            chart = format_compact_card(result, {
                "year": year, "month": month, "day": day,
                "hour": hour, "minute": minute, "city": city, "gender": gender,
            })
        except Exception:
            chart = (
                f"八字：{' '.join(result.bazi)}  日主：{result.day_master}  "
                f"格局：{getattr(result, 'geju', '')}  用神：{getattr(result, 'yongshen', '')}"
            )
        # 阶段 5·来源体系（方案 §3.0 ②）：排盘工具结果标引擎来源"你的命盘"
        idx = self._alloc_citations(user_id, 1)
        self._append_citations(user_id, [make_citation(
            idx, "engine",
            f"你的命盘：八字 {' '.join(result.bazi)}，日主 {result.day_master}"
            f"（{year}年{month}月{day}日{gender}）",
            title="你的命盘", source="排盘引擎",
        )])
        _hint = f"\n\n{_gender_conflict_hint}" if _gender_conflict_hint else ""
        return ToolResult(
            "排盘", True,
            chart + _hint
            + f"\n\n（引用标注：以上命盘为本轮分析依据，引用时在陈述后标注 [{idx}]）"
        )

    def _tool_search(self, params: str, user_id: str = "",
                     user_question: str = "") -> ToolResult:
        """工具「检索」（阶段 5·检索升级）：多路召回 + Rerank 精排古籍 top5。

        流程（方案 §3.2）：查询扩展（LLM+术语表）→ 多路 FAISS 召回 → 候选池
        Top50 → 用【用户原问题】rerank 精排（bge-reranker-v2-m3，防偏题）→
        低分丢弃 → Top5。

        注入格式（防偏题②）：【用户问】→【相关资料】（带编号 [n] + 来源标注）
        - 古籍来源 type="book"，记忆来源 type="memory"（L3 按需召回）
        - 引用规则注入（防偏题③）：只引用直接相关的

        策略：FAISS 可用 → 注入向量检索结果；FAISS 不可用/无结果 →
        返回 needs_info 标记（带"继续自然对话"提示），_run_tool_loop 将其注入
        system 消息，LLM 像朋友聊天一样继续对话（可追问细节/共情/换角度聊），
        不注入空结果、不降级关键词检索。
        """
        # 检索不可用/未命中：明说降级（B4 回答纪律 + C6 同规则），
        # 不再静默（原提示"不要提及搜索功能/不要生硬地说没查到"导致用户无感知）
        _CHAT_FALLBACK = RETRIEVAL_UNAVAILABLE_HINT
        query = (params or "").strip()
        if not query:
            return ToolResult(
                "检索", False,
                "请像朋友聊天一样自然地向用户询问想查哪方面的古籍内容。",
                needs_info=True,
            )
        try:
            faiss_retriever = get_faiss_retriever()
            if not faiss_retriever.ensure_ready():
                logger.warning("FAISS 检索器不可用: %s", faiss_retriever.load_error)
                return ToolResult("检索", False, _CHAT_FALLBACK, needs_info=True)
            # 防偏题核心：rerank 用【用户原问题】打分（扩展查询只管召回）
            refs = faiss_retriever.search(
                query, top_k=5,
                original_query=(user_question or query),
                expand=True, rerank=True,
            )
        except Exception as e:
            logger.error("FAISS 检索异常: %s", e, exc_info=True)
            return ToolResult("检索", False, _CHAT_FALLBACK, needs_info=True)
        if not refs:
            return ToolResult("检索", False, _CHAT_FALLBACK, needs_info=True)

        # 记忆来源（方案 §3.0 ③）：L3 按需召回，与查询相关的历史记忆一并标注
        mem_refs = []
        if user_id and self.memory_system:
            try:
                mem_refs = self.memory_system.get_relevant_memories(
                    user_id, query, top_k=2) or []
            except Exception:
                mem_refs = []

        # 统一编号（本轮引用编号连续，供回答 [n] 标注与前端抽屉）
        start = self._alloc_citations(user_id, len(refs) + len(mem_refs))
        lines = [f"【用户问】{user_question or query}", "【相关资料】"]
        items = []
        # 精排分数域随内容类型变化（梦境类 0.9+、古籍类 0.01~0.28），
        # 展示用相对相关度（池内最佳=1.00），原始分数保留在引用数据里
        top1 = max((r.get("score") or 0.0) for r in refs) if refs else 1.0
        for i, ref in enumerate(refs, start=start):
            # k24 补丁：改用统一读取契约（title→source）。原 source→title 在 FAISS
            # 路径上会把语料 slug（如 daizhige）当书名展示给用户。
            src = ref_title(ref)
            # k26：正文同样走读取契约（悬空冒号在入口剥离，此处不再直读 ref["text"]）
            body = ref_text(ref)
            rel = (ref.get("score") or 0.0) / top1 if top1 > 0 else 1.0
            items.append(make_citation(
                i, "book", body[:400],
                title=f"《{src}》" if "《" not in src else src,
                source=str(ref.get("title") or src),
            ))
            lines.append(
                f"[{i}] 【古籍】《{src}》\"{body[:200]}\""
                f"（相关度 {rel:.2f}）"
            )
        for j, m in enumerate(mem_refs, start=start + len(refs)):
            content = (m.get("content") or "")[:200]
            items.append(make_citation(
                j, "memory", content,
                title="你之前说过的…", source="对话记忆",
            ))
            lines.append(f"[{j}] 【记忆】{content}")
        self._append_citations(user_id, items)
        lines.append(
            "（引用规则：只引用与用户问题直接相关的内容；"
            "引用时在相关陈述后标注编号，如「古籍《X》载：…[1]」；"
            "不相关的内容忽略，不要引用。）"
        )
        return ToolResult("检索", True, "\n".join(lines))

    def _tool_web_search(self, params: str, user_id: str = "",
                         _rate_count: bool = True) -> ToolResult:
        """工具「搜索」（阶段 5·网络检索）：Bing 免费搜索 → Top 3-5。

        结果带来源 URL（type="web"）注入；服务不可用 → 标 unavailable
        （prompt 不宣传，执行时自然降级，不阻断对话）。
        k11b：① 本轮引擎意图域已自动联网检索同 query → 直接复用首查结果文本
        （防 LLM 工单二次真实检索与引用编号分裂）；② 护栏频控（_rate_count=False
        表示调用方已计数——引擎域自动检索路径自带计数，不双计）。
        """
        query = (params or "").strip()
        # k11b-①：同 query 复用（自动检索文本已在首次注册 citations/编号）
        _gr = getattr(self, "_turn_grounded", {}) or {}
        gr = _gr.get(user_id)
        if (gr and gr.get("ok") and gr.get("query")
                and self._same_search_query(gr.get("query", ""), query)):
            return ToolResult("搜索", True, gr.get("text") or query)
        if not query:
            return ToolResult(
                "搜索", False,
                "请像朋友聊天一样自然地向用户询问想搜索哪方面的内容。",
                needs_info=True,
            )
        if not web_search_available():
            # C6：搜索不可用 → 明说降级（明确告知用户实时信息受限），不静默
            return ToolResult(
                "搜索", False, SEARCH_UNAVAILABLE_HINT,
                needs_info=True,
            )
        # k11b-②：护栏频控（只在真实发起检索前计数；超限按受限降级，不静默）
        if _rate_count and not self._search_rate_ok(user_id):
            return ToolResult(
                "搜索", False,
                "联网检索过于频繁（60 秒内已多次），本次未执行新的联网查询。"
                "请如实告知用户稍后再查或直接简要作答，不要编造外部实时信息。",
                needs_info=True,
            )
        results = search_web(query, limit=5)
        if not results:
            # C6：搜索无结果 → 明说降级，不静默
            return ToolResult(
                "搜索", False, SEARCH_UNAVAILABLE_HINT,
                needs_info=True,
            )
        start = self._alloc_citations(user_id, len(results))
        lines = [f"【网络搜索】关键词：{query}"]
        items = []
        for i, r in enumerate(results, start=start):
            items.append(make_citation(
                i, "web", r["text"], title=r["title"],
                source="网络", url=r["url"],
            ))
            lines.append(f"[{i}] {r['title']}（{r['url']}）")
            if r.get("text"):
                lines.append(f"    {r['text'][:120]}")
        self._append_citations(user_id, items)
        lines.append(
            "（引用规则：只引用与用户问题直接相关的内容；"
            "引用网络信息时在陈述后标注编号 [n]；不相关的内容忽略。）"
        )
        return ToolResult("搜索", True, "\n".join(lines))

    # ============================================================
    # k11b 联网搜索语义触发（引擎意图域·实体 QA 自动检索；plan
    # docs/superpowers/plans/2026-09-07-k11b-search-trigger.md）
    # ============================================================

    _SEARCH_RATE_LIMIT = 3      # 护栏：联网检索频控（次/60s/用户）
    _SEARCH_RATE_WINDOW_S = 60.0
    _GROUND_BLOCK_MAX_CHARS = 2400  # 护栏：检索结果注入块长度钳制

    def _search_rate_ok(self, user_id: str) -> bool:
        """护栏·频控：真实发起联网检索前调用（判定不计数，执行才计数）。

        超过 3 次/60s/用户 → False（调用方按"检索受限"降级话术处理，
        不静默不阻断对话）。object.__new__ 装配的测试实例无 __init__ 状态 →
        setdefault 惰性初始化。
        k11b-r1（P3 记录）：失败检索（无结果/通道不可用）同样消耗频控次数——
        意图=限制 Bing 抓取成本总量，失败也产生了网络尝试；护栏只管频度，
        质量降级由降级话术兜底（取舍记 plan）。
        """
        import collections
        import time as _time
        ticks = self.__dict__.setdefault("_search_ticks", {})
        q = ticks.setdefault(user_id or "", collections.deque())
        now = _time.monotonic()
        while q and now - q[0] >= self._SEARCH_RATE_WINDOW_S:
            q.popleft()
        if len(q) >= self._SEARCH_RATE_LIMIT:
            return False
        q.append(now)
        return True

    def _same_search_query(self, a: str, b: str) -> bool:
        """query 归一后比较（去空白/句读/语气词）——同 query 复用去重判据。"""
        import re as _re
        _norm = lambda s: _re.sub(r"[\s，。！？、.,!?]|吗$|呢$|啊$|呀$", "", s or "").lower()
        return bool(a and b and _norm(a) == _norm(b))

    def _parse_result_domains(self, text: str, limit: int = 5) -> list:
        """护栏·域名：从检索结果文本（executor 行内 URL）抽站点域名（剥 www.）。"""
        import re as _re
        out = []
        for m in _re.finditer(r"https?://([a-zA-Z0-9][a-zA-Z0-9.-]*)", text or ""):
            host = (m.group(1) or "").lower()
            if host.startswith("www."):
                host = host[4:]
            if host and host not in out and not host.endswith("bing.com"):
                out.append(host)
            if len(out) >= limit:
                break
        return out

    def _ground_search_for_turn(self, msg: str, user_id: str,
                                analysis: Optional[MessageAnalysis]) -> Optional[dict]:
        """k41（T104 收尾）：本轮「先检索」接缝——**唯一调用点**，各回复路径共用。

        检索判定与「走哪条回复路径」解耦：`decide_search` 判该搜（尤其
        `reason=entity`）时，在**路由之前**检索一次，结果由 advisor/bazi/
        free_chat/… 各路径共用（`_ground_hint_parts` 注入上下文，
        `_apply_ground_source_trace` 出口补来源）。

        改前实锤（E6 二轮 T104）：调用只挂在 `_will_polish` 块内（条件含
        `intent not in ("xuetang","advisor")`）→ LLM 判 advisor 时整块跳过，
        检索根本没发生、回复无实体名无来源。

        判定语义**零放宽**（红线）：触发仍由 `_engine_domain_ground_search` →
        `decide_search` 单点判定（层 1-4 + 硬锚否决逐字未变）；降级链路
        （免费额度耗尽）维持既有成本门不检索。
        返回 None = 不触发/异常 → 调用方按「无检索」处理。
        """
        if self._downgraded.get(user_id, False):
            return None
        try:
            g = self._engine_domain_ground_search(msg, user_id, analysis)
        except Exception as e:
            logger.warning("k11b 引擎域自动检索异常 user=%s err=%s",
                           user_id, str(e)[:120])
            return None
        return None if (not g or g.get("skip")) else g

    @staticmethod
    def _ground_hint_parts(ground: Optional[dict]) -> list:
        """k41：检索结果的上下文注入段（单一实现，各回复路径共用）。

        有结果块 → 块本身 + （检索成功时）使用要求段；无检索/无块 → 空。
        """
        block = (ground or {}).get("block") or ""
        if not block:
            return []
        parts = [block]
        if ground.get("ok"):
            parts.append(_GROUND_USE_REQUIREMENT)
        return parts

    def _has_engine_citations(self, user_id: str) -> bool:
        """k5 润色门判据（k41）：本轮是否已有**引擎/古籍**引用（非 web）。

        自动联网检索注册的 web 引用**不算**——k5 门的语义是「handler 产出了
        可润色的引擎内容」（原注释：信息收集/错误回复不注册引用 → 不润色，
        保持原样）。改前该门在自动检索**之前**求值（检索调用挂在门内），故这里
        排除 web 引用后语义与改前逐字等价；不排除则「只有检索结果」的信息收集
        轮会被润色（F2 渐进建档文案被 LLM 重写 = 行为漂移）。
        """
        return any((c or {}).get("type") != "web"
                   for c in (self._citations.get(user_id) or []))

    @staticmethod
    def _apply_ground_source_trace(reply: str, ground: Optional[dict]) -> str:
        """k41：自动检索的来源尾注（单一实现，所有回复路径出口共用）。

        与 k11b `_will_polish` 分支同款口径：只在「检索成功 + 回复已含实体名」
        时补确定性尾注（`append_source_trace`，纯文本、禁裸 JSON/泄漏）。
        """
        if not reply or not ground or not ground.get("ok") or not ground.get("entity"):
            return reply
        try:
            return append_source_trace(
                reply, entity=ground.get("entity", ""),
                domains=ground.get("domains") or [])
        except Exception:
            return reply

    def _apply_gender_confirm(self, reply: str, user_id: str) -> str:
        """k41（②g 兜底出口）：歧义类（`_gender_ambiguous_word`）的询问确认重挂。

        与 `_gender_acks`（T008 回执重挂）/ `_apply_ground_source_trace` 同一范式：
        出口幂等重挂（已含则不加），保证润色/工具循环/早退分支都不会把它吃掉。
        """
        _ask = (getattr(self, "_gender_confirms", None) or {}).pop(user_id, "")
        if _ask and _ask not in reply:
            return (reply.rstrip() + "\n\n" + _ask) if reply else _ask
        return reply

    def _engine_domain_ground_search(self, msg: str, user_id: str,
                                     analysis: Optional[MessageAnalysis]) -> dict:
        """k11b：引擎意图域·外部实体/时效触发 → 执行一次联网检索并准备注入块。

        - 触发判定：decide_search(msg, llm_needs_search=analysis.needs_search)
          （实体层/时效层/研究白名单 正信号 − 金融排除 − 命理本地锚；LLM 信号=
          分析器同一调用产出的 needs_search，OR 叠加，无新增二判 LLM——取舍见 plan）
        - 不触发 → {"skip": True}（调用方维持旧 analysis_hint 通道，零漂移）
        - 触发 → 复用 search executor _tool_web_search（真实检索 + citations
          type=web 注册单一编号源；禁新建 HTTP 通道），注入块 ≤2400 字符；
          同 query 由 executor 复用表去重（本轮 LLM 再发工单不再真实检索）
        - 频控超限/检索空/通道不可用 → 对应降级注（graceful degrade 非甩锅）
        - 返回: {skip, block, entity, domains, ok, query}
        """
        dec = _decide_search(msg, llm_needs_search=bool(
            getattr(analysis, "needs_search", False)))
        if not dec.should_search:
            return {"skip": True}
        query = (dec.query or "").strip()[:70]
        if not query:
            return {"skip": True}
        # 护栏·频控（执行前计数；超限 → 降级注，不发起检索）
        if not self._search_rate_ok(user_id):
            return {"block": _GROUND_RATE_NOTE, "entity": dec.entity or "",
                    "domains": [], "ok": False, "query": query, "skip": False}
        tr = self._tool_web_search(query, user_id, _rate_count=False)
        if not tr.ok or not tr.text:
            return {"block": _GROUND_EMPTY_NOTE, "entity": dec.entity or "",
                    "domains": [], "ok": False, "query": query, "skip": False}
        block = "【网络检索结果】\n" + tr.text
        if len(block) > self._GROUND_BLOCK_MAX_CHARS:  # 护栏·长度钳制
            block = block[: self._GROUND_BLOCK_MAX_CHARS].rsplit("\n", 1)[0] + "…"
        domains = self._parse_result_domains(tr.text)
        # 复用表：LLM 后续再发同 query 搜索工单 → executor 直取（防二次真实检索）
        self.__dict__.setdefault("_turn_grounded", {})[user_id] = {
            "entity": dec.entity or "", "query": query, "domains": domains,
            "ok": True, "text": tr.text,
        }
        return {"block": block, "entity": dec.entity or "", "domains": domains,
                "ok": True, "query": query, "skip": False}

    def _get_dream_retriever(self):
        """解梦检索器：276 万 FAISS 生产主路径优先（设计文档阶段 0），
        FAISS 不可用时降级本地 Retriever（行为同修复前）。"""
        try:
            fr = get_faiss_retriever()
            if fr.ensure_ready():
                return _ChunkSearchAdapter(fr)
        except Exception:
            pass
        return self.retriever

    def _tool_dream(self, params: str, user_id: str) -> ToolResult:
        """工具「解梦」：DreamEngine 象征分析 + 古籍匹配。"""
        if self.dream_engine is None:
            return ToolResult("解梦", False, "「解梦」工具暂不可用，请直接与用户聊天。")
        api_key = getattr(self.llm, 'api_key', '') if self.llm else ''
        try:
            result = self.dream_engine.analyze(
                params, self._get_dream_retriever(), api_key)
        except Exception as e:
            return ToolResult("解梦", False, f"解梦引擎执行失败：{str(e)[:100]}")
        lines = [f"梦境：{params}"]
        if getattr(result, "symbols", None):
            lines.append(f"核心象征：{'、'.join(result.symbols)}")
        if getattr(result, "emotions", None):
            lines.append(f"情绪基调：{result.emotions}")
        # 阶段 5·来源体系：解梦的古籍匹配结果标 book 来源（带编号 [n]）
        interps = (getattr(result, "interpretations", None) or [])[:5]
        if interps:
            lines.append("古籍参考：")
            start = self._alloc_citations(user_id, len(interps))
            items = []
            for i, interp in enumerate(interps, start=start):
                items.append(make_citation(
                    i, "book", interp[:400],
                    title="解梦 · 古籍参考", source="解梦引擎",
                ))
                lines.append(f"  [{i}] {interp[:200]}")
            self._append_citations(user_id, items)
        try:
            self.dao.save_consultation(user_id, params, result, intent="dream")
        except Exception:
            pass
        return ToolResult("解梦", True, "\n".join(lines))

    def _tool_fengshui(self, params: str) -> ToolResult:
        """工具「风水」：坐向解析 → FengshuiEngine.analyze。"""
        if self.fengshui_engine is None:
            return ToolResult("风水", False, "「风水」工具暂不可用，请直接与用户聊天。")
        direction = self._extract_direction(params)
        if direction is None:
            return ToolResult(
                "风水", False,
                "缺少房屋坐向信息。请向用户自然询问房子的坐向（如：坐北朝南 / 子山午向），"
                "有户型描述或照片描述也可以。",
                needs_info=True,
            )
        try:
            result = self.fengshui_engine.analyze(
                direction=direction, year_built=None, birth_year=None, gender=None,
            )
        except Exception as e:
            return ToolResult("风水", False, f"风水引擎执行失败：{str(e)[:100]}")
        lines = [f"坐向：{params.strip()}", f"宅卦：{result.house_gua}  当前运：{result.period}运"]
        if getattr(result, "person_gua", None):
            lines.append(f"命卦：{result.person_gua}")
        auspicious = {k: v for k, v in result.eight_mansions.items()
                      if k in {"生气", "天医", "延年", "伏位"}}
        inauspicious = {k: v for k, v in result.eight_mansions.items()
                        if k in {"绝命", "五鬼", "六煞", "祸害"}}
        if auspicious:
            lines.append(f"四吉方：{' '.join(f'{k}-{v}' for k, v in auspicious.items())}")
        if inauspicious:
            lines.append(f"四凶方：{' '.join(f'{k}-{v}' for k, v in inauspicious.items())}")
        return ToolResult("风水", True, "\n".join(lines))

    def _tool_zeri(self, params, user_id: str) -> ToolResult:
        """工具「择日」（Task 2 增强）：场景+意图双条件判定 → 时间窗口 → 多日 Top3 吉日。

        params 支持两种形式:
        - 自然语言字符串（LLM 标签路径）, 可内嵌 "exclude_dates: 2026-09-03,2026-09-06"
        - dict: {"text": "自然语言描述", "exclude_dates": ["2026-09-03", ...]}

        流程：
        ① 场景判定（6 场景同义词表）——缺场景 → 澄清 ToolResult, 不调引擎、不扣额度
        ② 意图判定（选日子/择日/哪天/吉日/挑个时间/好日子/换一批…）——场景在但无意图 →
           澄清反问, 不调引擎、不扣额度
        ③ 额度检查（_check_quota, 澄清不扣）——引擎调用成功后才扣减（异常不占额度）
        ④ user_bazi（k9/k18 起经 _get_user_birth_profile 统一档案链 →
           _map_user_bazi_for_zeri shengxiao/day_gan/month_zhi 映射，
           persons 建档用户不漏读；不再裸读 users.bazi_info）
        ⑤ select_lucky_days(scene, start, end, user_bazi, exclude_dates) → 结构化卡片文本
        ⑥ 末尾选择问句 + /pages/zeri/zeri 深链（前端 navFor 渲染跳转按钮）
        """
        if self.zeri_engine is None:
            return ToolResult("择日", False, "「择日」工具暂不可用，请直接与用户聊天。")
        exclude_dates = self._extract_zeri_exclude_dates(params)
        if isinstance(params, dict):
            params = params.get("text") or params.get("params") or ""
        scene = self._extract_zeri_scene(params)
        if scene is None:
            return ToolResult(
                "择日", False, ZERI_SCENE_QUESTION,
                needs_info=True,
            )
        if not (self._has_zeri_intent(params) or self._has_zeri_date_anchor(params)):
            return ToolResult(
                "择日", False,
                f"您是想给「{scene}」选日子吗？告诉我大概时间（如“下个月”“下周”“8月20日”），"
                f"我帮您挑几个好日子。",
                needs_info=True,
            )
        # 真实调用：额度门（免费额度用完 → 引导会员, 不调引擎）
        remaining, is_limited = self._check_quota(user_id)
        if is_limited and remaining <= 0:
            return ToolResult(
                "择日", False,
                "你今天的免费额度已用完。成为会员即可无限畅聊，"
                "基础版仅需 19.9 元/月。回复「会员」了解更多升级方案。",
            )

        start, end = self._extract_window(params)
        user_bazi = self._map_user_bazi_for_zeri(user_id)
        try:
            res = self.zeri_engine.select_lucky_days(
                scene=scene, start_date=start, end_date=end,
                user_bazi=user_bazi, prefer_weekend=True,
                exclude_dates=exclude_dates or None,
            )
            # 结果形状校验先于扣额度（fix-later）: 引擎返回非 dict → 不扣额度, 走异常兜底。
            # 仅引擎调用成功且结果形状合法后才扣额度（引擎异常/结果异常都不占额度, 澄清路径不扣）
            if not isinstance(res, dict):
                raise ValueError("引擎返回结果形状异常")
            self._consume_quota(user_id)
            cards = res.get("cards") or []
            scanned = res.get("scanned", 0)
            lines = [
                f"【择日】场景：{scene}｜时间范围：{start} ~ {end}",
                f"共扫描 {scanned} 天，为您挑出 {len(cards)} 个吉日：",
                "",
            ]
            marks = "①②③"
            for i, c in enumerate(cards[:3], start=0):
                lines.append(f"{marks[i]} {c.date} {c.lunar_text}")
                # k26：宜/忌各自为空即整行不渲染（空忌日不得输出悬空「忌：」）
                lines.extend(_yi_ji_render_lines(c.yi, c.ji))
                lines.append(f"吉时：{c.jishi}｜喜神：{c.xi_fangwei}｜财神：{c.cai_fangwei}")
                lines.append(f"理由：{c.reason_source}（总分{c.total}）")
                lines.append("")
            if res.get("suggest_wider"):
                reason = res.get("reason") or "窗口内合格吉日不足"
                lines.append(f"注：本窗口内合格吉日不足3天（仅{len(cards)}天合格），{reason}。"
                             "您可以回复「换一批」，我会避开已展示的日期重新挑选；"
                             "或告诉我新的时间范围。")
            if cards:
                lines.append("您选哪一个？选好后我帮您生成办事清单。")
            else:
                lines.append("本窗口内没有选出合格吉日，建议扩大日期范围后再试。")
            lines.append("/pages/zeri/zeri")
        except Exception as e:
            return ToolResult("择日", False, f"择日引擎执行失败：{str(e)[:100]}")
        return ToolResult("择日", True, "\n".join(lines))

    def _tool_query_records(self, params: str, user_id: str) -> ToolResult:
        """存量数据工具（Task 7）：RecordQuery 直读，无记录返回'未查到'（区别于'没查'）。"""
        if not getattr(self, "record_query", None):
            # record_query 未装配（无 db_path / 初始化失败）→ 与排盘引擎缺省同口径降级
            return ToolResult("records", False, "「查记录」工具暂不可用，请直接与用户聊天。")
        try:
            out = self.record_query.direct_query(user_id, params)
            if not out:
                return ToolResult("records", False, "未查到相关记录，可建议用户先建档/使用功能")
            return ToolResult("records", True, out)
        except Exception as e:
            logger.warning("查记录工具失败 %s", e)
            return ToolResult("records", False, "查询失败，稍后再试")

    def _tool_hehun(self, params, user_id: str) -> ToolResult:
        """工具「合婚」（批次 2 E1）：双方出生信息 → 双排盘 → 合婚评分卡片。

        params 支持两种形式（与 _tool_zeri 同型）：
        - dict（原生 tool_use / JSON 工单已序列化为字符串；防御性兼容 dict）
        - 自然语言字符串：
          ① 结构化键 "birth_a: X\nbirth_b: Y"（JSON 工单 serialize_params 产物）
          ② 文本标签兜底 "男X，女Y"（split_birth_pair 分隔符拆两段）

        流程（复用既有引擎，不新起）：
        ① split_birth_pair 拆双方 → 缺/拆不开 → 澄清追问（不调引擎）
        ② _extract_bazi_info 逐方解析 → 任一方失败 → 指明哪方需补
        ③ self.engine.calculate ×2 取双方四柱（与 _tool_bazi 同口径）
        ④ self.hehun_engine.match → format_hehun_card 紧凑卡片
        """
        if self.engine is None or self.hehun_engine is None:
            return ToolResult("合婚", False, "「合婚」工具暂不可用，请直接与用户聊天。")
        text = params.get("text") if isinstance(params, dict) else params
        text = (text or "").strip()
        pair = split_birth_pair(text)
        if pair is not None and _HEHUN_EMPTY_KEY_RE.search(text):
            # k39 S3 单档补全：结构化工单里**一方留空**（"birth_b:" 无值）=
            # 单方已就位，不是「没看懂」。若不归一，`split_birth_pair` 的
            # 分隔符形态会把参数键本身当成第二人，走下面的「第二方（birth_b）
            # 出生信息没看懂：「birth_b:」」分支——把参数键当用户出生信息回显
            # （半成品回复）。归一为 None → 走缺方澄清（下两分支）。
            pair = None
        if pair is None:
            if self._extract_bazi_info(text):
                return ToolResult(
                    "合婚", False,
                    "已收到一方的出生信息，还需要另一方的出生年月日时、出生地点、性别。",
                    needs_info=True,
                )
            return ToolResult(
                "合婚", False,
                "请提供双方出生信息（各含出生年月日时、地点、性别），我就为你们做合婚分析。"
                "如：birth_a=1990年5月20日 午时 北京 男、birth_b=1992年8月15日 巳时 上海 女",
                needs_info=True,
            )
        info_a = self._extract_bazi_info(pair[0])
        if info_a is None:
            return ToolResult(
                "合婚", False,
                f"第一方（birth_a）出生信息没看懂：「{pair[0][:50]}」。"
                "请提供完整的出生年月日时、出生地点、性别。",
                needs_info=True,
            )
        info_b = self._extract_bazi_info(pair[1])
        if info_b is None:
            return ToolResult(
                "合婚", False,
                f"第二方（birth_b）出生信息没看懂：「{pair[1][:50]}」。"
                "请提供完整的出生年月日时、出生地点、性别。",
                needs_info=True,
            )
        try:
            year_a, month_a, day_a, hour_a, minute_a, city_a, gender_a = info_a
            year_b, month_b, day_b, hour_b, minute_b, city_b, gender_b = info_b
            result_a = self.engine.calculate(
                year_a, month_a, day_a, hour_a, minute_a, city_a, gender_a)
            result_b = self.engine.calculate(
                year_b, month_b, day_b, hour_b, minute_b, city_b, gender_b)
            hehun_result = self.hehun_engine.match(result_a, result_b)
        except Exception as e:
            return ToolResult("合婚", False, f"合婚引擎执行失败：{str(e)[:100]}")
        return ToolResult("合婚", True, format_hehun_card(result_a, result_b, hehun_result))

    def _tool_naming(self, params, user_id: str) -> ToolResult:
        """工具「起名」（批次 2 E2）：姓氏+性别+出生信息（可选）→ 补益五行 + 候选名卡片。

        params 支持两种形式（与 _tool_hehun 同型）：
        - dict（原生 tool_use / JSON 工单已序列化为字符串；防御性兼容 dict）
        - 自然语言字符串：
          ① 结构化键 "surname: 张\ngender: 男\nbirth: 2019年3月15日 午时 北京"
             （JSON 工单 serialize_params 产物；birth 键可选）
          ② 文本标签兜底 "姓张，男孩，2019年3月15日 午时出生"（split_naming_params）

        流程（复用既有引擎，不新起）：
        ① split_naming_params 解析 → 缺姓氏/性别 → needs_info 点名缺项
        ② 出生信息（可选）：_extract_bazi_info 解析 → 失败 → needs_info 点名 birth；
           成功 → BaziEngine.calculate 取五行计数/用神 → target_elements 定补益集合
        ③ 无出生信息 → 降级：仅按五格数理均衡推荐（elements=None）
        ④ generate_candidates（确定性，字库=ming.CHAR_LIB，五格=xingming 引擎）
           → format_naming_card 紧凑卡片
        """
        if self.engine is None:
            return ToolResult("起名", False, "「起名」工具暂不可用，请直接与用户聊天。")
        text = params.get("text") if isinstance(params, dict) else params
        info = split_naming_params(text or "")
        missing = []
        if not info or not info.get("surname"):
            missing.append("姓氏（surname，如：张）")
        if not info or not info.get("gender"):
            missing.append("性别（gender，男/女）")
        if missing:
            return ToolResult(
                "起名", False,
                f"请提供{'、'.join(missing)}，我就为你推荐候选名。"
                "出生信息（birth）可选，提供后可按八字五行补益推荐用字；"
                "如：surname: 张、gender: 男、birth: 2019年3月15日 午时 北京",
                needs_info=True,
            )
        surname = info["surname"].strip()
        gender = info["gender"].strip()
        if not surname or len(surname) > 2:
            return ToolResult(
                "起名", False, "姓氏请用 1-2 个汉字（如：张、欧阳）。", needs_info=True)
        # 姓氏笔画未知 → 五格数理不可算（数据缺口，非参数缺失）
        if not all(get_stroke_count(c) > 0 for c in surname):
            return ToolResult(
                "起名", False,
                f"姓氏「{surname}」的笔画暂不在常用字库中，无法计算五格数理。"
                "请换常见姓氏，或直接告诉我姓氏笔画数。")
        birth = (info.get("birth") or "").strip()
        elements = None
        elements_desc = ""
        if birth:
            parsed = self._extract_bazi_info(birth)
            if parsed is None:
                return ToolResult(
                    "起名", False,
                    f"出生信息没看懂：「{birth[:50]}」。请提供完整的出生年月日时、出生地点"
                    "（如 birth: 2019年3月15日 午时 北京）。",
                    needs_info=True,
                )
            try:
                year, month, day, hour, minute, city, b_gender = parsed
                result = self.engine.calculate(
                    year, month, day, hour, minute, city, b_gender)
            except Exception as e:
                return ToolResult("起名", False, f"排盘引擎执行失败：{str(e)[:100]}")
            elements, elements_desc = target_elements(result.wuxing, result.yongshen)
        candidates = generate_candidates(surname, gender, elements, limit=5)
        if not candidates:
            return ToolResult(
                "起名", False,
                "暂无可推荐的候选名（当前条件下的可用字不足），请放宽条件"
                "（如去掉出生信息、或换其他性别）。")
        return ToolResult(
            "起名", True,
            format_naming_card(surname, gender, elements_desc, candidates))

    def _tool_fortune_cycle(self, params, user_id: str) -> ToolResult:
        """工具「流月流年」（批次 2 E3）：出生信息 + 目标年份/月份/关注维度 →
        流年流月干支 + 十神解读 + 吉凶月提示卡片。

        params 支持两种形式（与 _tool_hehun/_tool_naming 同型）：
        - dict（原生 tool_use / JSON 工单已序列化为字符串；防御性兼容 dict）
        - 自然语言字符串：
          ① 结构化键 "birth: X\nyear: 2027\nmonth: 6\nfocus: 财运"
             （JSON 工单 serialize_params 产物；year/month/focus 可选）
          ② 文本标签兜底 "1990年5月20日 午时 北京 男 2027年 看财运"
             （parse_cycle_params 日期感知拆分）

        流程（复用既有引擎，不新起）：
        ① parse_cycle_params 解析 → 缺出生信息 → 澄清追问（不调引擎）
        ② _extract_bazi_info 解析出生 → 失败 → 点名 birth
        ③ 目标年份/月份：给但非法 → 点名 year/month；缺省 → 今年/本月
           （标准库 datetime，与规则层 default_targets 同口径）
        ④ self.engine.calculate → format_cycle_card 紧凑卡片
           （流年/流月干支 + 十神 + 关注维度要点 + 吉凶月提示）
        """
        if self.engine is None:
            return ToolResult("流月流年", False, "「流月流年」工具暂不可用，请直接与用户聊天。")
        text = params.get("text") if isinstance(params, dict) else params
        info = parse_cycle_params(text or "")
        if not info or not info.get("birth"):
            return ToolResult(
                "流月流年", False,
                "请提供出生信息（出生年月日时、地点、性别），我就为你推演流年流月运势。"
                "可选指定目标年份（year）、月份（month）与关注维度（focus：事业/财运/感情）。"
                "如：birth: 1990年5月20日 午时 北京 男、year: 2027、focus: 财运",
                needs_info=True,
            )
        birth = info["birth"].strip()
        parsed = self._extract_bazi_info(birth)
        if parsed is None:
            return ToolResult(
                "流月流年", False,
                f"出生信息没看懂：「{birth[:50]}」。请提供完整的出生年月日时、出生地点、性别。",
                needs_info=True,
            )
        # 目标年份/月份：给但非法 → 点名（不默认静默纠正）；缺省 → 今年/本月
        year = month = None
        if info.get("year"):
            year = parse_target_year(info["year"])
            if year is None:
                return ToolResult(
                    "流月流年", False,
                    f"目标年份没看懂：「{info['year'][:20]}」。请用 1900-2300 的四位年份"
                    "（如 year: 2027），不填则默认今年。",
                    needs_info=True,
                )
        if info.get("month"):
            month = parse_target_month(info["month"])
            if month is None:
                return ToolResult(
                    "流月流年", False,
                    f"目标月份没看懂：「{info['month'][:20]}」。请用 1-12 的月份"
                    "（如 month: 6），不填则默认本月。",
                    needs_info=True,
                )
        from src.tools.fortune_cycle import (default_targets, parse_focus,
                                             parse_view)
        # k40（T027）：年视图（view=year，服务端内部键）→ month 保持 None →
        # `format_cycle_card` 走 12 月一览分支（流年问法的正确视图）；缺省/
        # 显式 month → 单月视图（改前行为）。两个视图的回复必然不同，故
        # 「流年」轮与「流月」轮不再逐字节同串（L2 multi_turn distinct 实锤）。
        _view_year = parse_view(info.get("view"))
        d_year, d_month = default_targets()
        if year is None:
            year = d_year
        if month is None and not _view_year:
            month = d_month
        focus_list = parse_focus(info.get("focus"))
        try:
            y, m, d, h, mi, city, b_gender = parsed
            result = self.engine.calculate(y, m, d, h, mi, city, b_gender)
        except Exception as e:
            return ToolResult("流月流年", False, f"排盘引擎执行失败：{str(e)[:100]}")
        return ToolResult(
            "流月流年", True,
            format_cycle_card(result, year, month, focus_list))

    def _tool_career_dir(self, params, user_id: str) -> ToolResult:
        """工具「择业」（批次 2 E4）：出生信息 + 当前考虑行业（可选）→
        喜用神 + 适合行业（五行分类）+ 吉利方位 + 禁忌行业卡片。

        params 支持两种形式（与 _tool_fortune_cycle 同型）：
        - dict（原生 tool_use / JSON 工单已序列化为字符串；防御性兼容 dict）
        - 自然语言字符串：
          ① 结构化键 "birth: 1990年5月20日 午时 北京 男\nindustry: 金融"
             （JSON 工单 serialize_params 产物；industry 键可选）
          ② 文本标签兜底 "我考虑做金融，1990年5月20日 午时 北京 男"
             （parse_career_params：行业关键词命中 + 出生年起始截取）

        流程（复用既有引擎，不新起）：
        ① parse_career_params 解析 → 缺出生信息 → 澄清追问（不调引擎）
        ② _extract_bazi_info 解析出生 → 失败 → 点名 birth
        ③ self.engine.calculate → 喜用神（引擎 yongshen 口径）→ 适合行业
           （喜用五行行业清单）+ 吉利方位（喜用五行方位）+ 禁忌行业
           （忌神五行行业，日主强弱判定）→ format_career_card 紧凑卡片
        """
        if self.engine is None:
            return ToolResult("择业", False, "「择业」工具暂不可用，请直接与用户聊天。")
        text = params.get("text") if isinstance(params, dict) else params
        info = parse_career_params(text or "")
        if not info or not info.get("birth"):
            return ToolResult(
                "择业", False,
                "请提供出生信息（出生年月日时、地点、性别），我就为你匹配适合行业与吉利方位。"
                "可选提供当前考虑行业（industry，如：金融），没有则按喜用神五行全量推荐。"
                "如：birth: 1990年5月20日 午时 北京 男、industry: 金融",
                needs_info=True,
            )
        birth = info["birth"].strip()
        parsed = self._extract_bazi_info(birth)
        if parsed is None:
            return ToolResult(
                "择业", False,
                f"出生信息没看懂：「{birth[:50]}」。请提供完整的出生年月日时、出生地点、性别。",
                needs_info=True,
            )
        try:
            y, m, d, h, mi, city, b_gender = parsed
            result = self.engine.calculate(y, m, d, h, mi, city, b_gender)
        except Exception as e:
            return ToolResult("择业", False, f"排盘引擎执行失败：{str(e)[:100]}")
        industry = (info.get("industry") or "").strip() or None
        return ToolResult("择业", True, format_career_card(result, industry))

    def _tool_num_omen(self, params, user_id: str) -> ToolResult:
        """工具「数字吉凶」（批次 2 E5）：数字串（手机号/车牌/楼层/门牌）→
        尾号/整体 81 数理吉凶 + 数理含义 + 改善建议卡片。最轻量（纯查表，无引擎调用）。

        params 支持两种形式（与 _tool_career_dir 同型）：
        - dict（原生 tool_use / JSON 工单已序列化为字符串；防御性兼容 dict）
        - 自然语言字符串：
          ① 结构化键 "number: 13812345678\ncontext: 手机号"（JSON 工单
             serialize_params 产物；context 键可选，不填按位数自动识别）
          ② 文本标签兜底 "帮我看看 138-1234-5678 这个手机号吉不吉"
             （parse_num_params：非数字字符剔除 + 场景关键词/位数识别）

        流程（纯查表，不调引擎）：
        ① parse_num_params 解析 → 无数字 → 澄清追问（点名 number 键示例）
        ② 位数 > 20 → 提示过长（非实际号码）
        ③ analyze_number 查 NUMEROLOGY_81（与起名工具同源同表）→ format_num_card
        """
        text = params.get("text") if isinstance(params, dict) else params
        info = parse_num_params(text or "")
        if not info or not info.get("number"):
            return ToolResult(
                "数字吉凶", False,
                "请提供要看的数字（手机号/车牌/楼层/门牌等），我就为你查 81 数理吉凶。"
                "如：number: 13812345678；可选提供使用场景（context：手机号/车牌/楼层/"
                "门牌），不填则按位数自动识别。",
                needs_info=True,
            )
        number = info["number"]
        if len(number) > 20:
            return ToolResult(
                "数字吉凶", False,
                f"数字串过长（{len(number)} 位），请提供手机号/车牌/楼层/门牌等实际号码。",
                needs_info=True,
            )
        context = (info.get("context") or "").strip()
        return ToolResult(
            "数字吉凶", True,
            format_num_card(analyze_number(number, context), number, context))

    # T018（k38，唯一真回归）相对年份折算：LLM 发「流月流年」工单时常只带 birth
    # 键（「帮我看看明年的流年运势」→ {"birth": …}，**year 键整个丢失**），工具
    # 按缺省渲染「今年流年」→ 用户问明年却答 2026（L1 partial=键集契约，缺键即
    # FAIL）。这里在**执行前**把用户原话里的相对年份（明年/后年/去年/今年…）
    # 折算成四位年份补进调用参数——与 `src.tools.fortune_cycle.relative_year_from_text`
    # 同一事实源（口径一致，不另立词表）：① 修用户可见回复；② 让 L1 记录到真实
    # 传入的 year 键；③ 生产真机（GLM 文本标签路径）同样受益。
    # 边界：只作用于流月流年工具（其余工具无 year 语义）；用户原话无相对年词
    # → 原样返回（不猜年份，保持 LLM 传值）。
    # k40（T018 第二轮·唯一真回归的收尾）：**用户原话含相对年词时无条件覆盖**
    # LLM 传值——原话是唯一事实源。k38 的口径是「LLM 给了 year 就尊重」，但
    # k38 同时改了工具描述（capability_registry「明年=今年+1，请先折算为四位
    # 年份」）诱导模型自己折算，而模型的「今年」是训练期幻觉（以为是 2023）→
    # 传 year=2024，确定性折算被自己挡住（本轮 L1 实录 `year='2024'`、回复
    # 「明年是2024年，属于甲辰年」）。相对词命中即覆盖，模型算错/算对都不影响；
    # 原话只有绝对年份（"2028年"）→ `relative_year_from_text` 返回 None →
    # 保持 LLM 传值（绝对年份不被改写，见 tests/test_k40_e6_r3.py 双向用例）。
    # k40 返工（Important-2）：无条件覆盖只作用于**相对年份表达**——
    # 「今年不太顺，帮我看看2028年的流年运势」这类混合句里，用户明确给出的四位
    # 绝对年份（`relative_year_from_text` 之前）优先：原话是唯一事实源，绝对
    # 年份比顺带提到的「今年/明年」更具体；模型若已按原话解析出该绝对年（或
    # 解析成别的年份）都以原话的绝对年为准，只有原话没有绝对年时才用相对词
    # 折算。纯绝对年（无相对词）走 `_ry` 为 None 的原短路，行为不变。
    # k40 第四轮（审查 Critical-2·目标年 vs 出生年分离）：绝对年候选先剔除
    # **出生语境**的年份——出生年只作输入，不得当目标年。「1990年5月20日
    # 15:30 北京 男，帮我看看明年的流年运势」实跑 year=1990、回复「1990年
    # 流年」（应 2027，371c551 与基线都正确）——用户粘出生串问流年是本产品
    # 最常见的形态。出生语境 = ① 年+月(+日) 日期串；② 年+出生/生(的)；
    # ③ 年+的 且位于子句边界（「我1990年的，…」自述）；④ 出生/生于/生辰/
    # 生日 等引导的年份。剔除后仍有绝对年 → 取最后一个（用户明确的目标年）；
    # 一个都不剩（全是出生年）→ 用相对词折算。
    _ABS_YEAR_RE = re.compile(r'([12]\d{3})\s*年')
    _ABS_BIRTH_CTX_RE = re.compile(
        r'\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*[日号]'          # ① 完整日期串
        r'|\d{4}\s*年\s*\d{1,2}\s*月\s*(?:出\s*生'               # ① 年+月+出生
        r'|生(?=[的了，,。.！!？?；;、\s]|$))'
        r'|\d{4}\s*年\s*出\s*生'                                  # ② 年出生
        r'|\d{4}\s*年\s*生(?=[的了，,。.！!？?；;、\s]|$)'          # ② 年生的
        r'|\d{4}\s*年\s*的\s*(?=[，,。.！!？?；;、\n]|$)'           # ③ 我1990年的，
        r'|(?:出生|生于|出生于|出生在|生在)'                      # ④ 出生词引导
        r'[^，,。.！!？?；;\n]{0,6}?\d{4}\s*年')

    def _with_relative_cycle_year(self, name: str, params, user_question: str):
        if name not in ("流月流年", "fortune_cycle"):
            return params
        if not isinstance(params, dict):
            return params
        try:
            from src.tools.fortune_cycle import relative_year_from_text
            _ry = relative_year_from_text(user_question)
        except Exception:
            return params
        if not _ry:
            return params
        _out = dict(params)
        _ug = str(user_question or "")
        _birth_spans = [m.span() for m in self._ABS_BIRTH_CTX_RE.finditer(_ug)]
        _abs_years = [
            int(m.group(1)) for m in self._ABS_YEAR_RE.finditer(_ug)
            if not any(a <= m.start() and m.end() <= b
                       for a, b in _birth_spans)]
        if _abs_years:
            _cur = str(_out.get("year", "")).strip()
            # 模型已按原话解析出其中一个绝对年 → 尊重其消歧；否则取原话里
            # 最后出现的绝对年（问句的目标年通常在句末，同「最内层/最后一条」
            # 既有口径）
            _out["year"] = (_cur if _cur in [str(y) for y in _abs_years]
                            else str(_abs_years[-1]))
        else:
            _out["year"] = str(_ry)
        return _out

    # ── R1-2 工具场景确定性兜底（评测 T094/T095/T039 修复）──────────────
    # 场景兜底统一改为「确定性执行对应工具」（0 LLM，经 _execute_tool_call
    # 出口 → L1 拦截器记录真实工具调用；回复为结构化卡片，含契约关键词）。
    # 任一环节失败 → 返回 None（调用方 fail-open 保留 _free_chat 原文）。
    def _scene_num_omen_fallback(self, msg: str, user_id: str, stream_cb=None,
                                 session_id=None) -> Optional[str]:
        """数字吉凶场景兜底：消息中数字 → 数字吉凶工具（纯查表，0 LLM）。

        原兜底（无）→ free_chat LLM 把工具调用 JSON 当回复文本输出
        （"num_omen {\\"number\\": …}"，T094 实锤，工具从未执行）。
        """
        try:
            from src.tools.num_omen import parse_num_params
            info = parse_num_params(msg or "")
            if info and info.get("number"):
                params = {"number": info["number"]}
                if info.get("context"):
                    params["context"] = info["context"]
                r = self._execute_tool_call("数字吉凶", params, user_id,
                                            user_question=msg)
                if r.ok and r.text:
                    return r.text
        except Exception:
            pass
        return None

    def _scene_career_dir_fallback(self, msg: str, user_id: str, stream_cb=None,
                                   session_id=None) -> Optional[str]:
        """择业场景兜底：档案出生信息 → 择业工具（引擎 yongshen 口径，0 LLM）。

        原兜底 _handle_career（=_handle_bazi 重排）→ 回复是命盘卡片而非择业
        分析，career_dir 工具从未调用（T095 实锤：L1 期望 1 次实际 0 次）。
        无档案（未建档）→ None（保留原兜底行为，走 _free_chat 原文）。
        """
        try:
            profile = self._get_user_birth_profile(user_id)
            if profile and profile.get("year"):
                birth = self._fmt_birth_text(profile)
                r = self._execute_tool_call("择业", {"birth": birth}, user_id,
                                            user_question=msg)
                if r.ok and r.text:
                    return r.text
        except Exception:
            pass
        return None

    def _scene_fortune_cycle_fallback(self, msg: str, user_id: str,
                                      stream_cb=None,
                                      session_id=None) -> Optional[str]:
        """流年流月场景兜底：档案出生信息 → 流月流年工具（0 LLM，与
        _scene_career_dir_fallback 同型）。

        原兜底 _handle_advisor → LLM「AI 行动建议」散文（L1 期望
        fortune_cycle 工具实际零调用，T026 3/3 实锤：链路轮3「明年运势
        怎么样」LLM 未输出任何工单）→ 改确定性执行工具：L1 记录
        fortune_cycle 调用（{"birth": 档案出生串} 与契约 partial 键匹配）
        + 回复为结构化流年卡片。无档案（未建档）→ None（保留原兜底行为）。
        """
        try:
            profile = self._get_user_birth_profile(user_id)
            if profile and profile.get("year"):
                birth = self._fmt_birth_text(profile)
                # k40（T027）：按用户原话区分视图——「流月」→ 月视图（缺省 =
                # 本月，改前行为）；「流年/明年」→ 年视图（view=year，12 月
                # 一览）。改前两次调用都只有 birth 键 → 两次都渲染「N月单月」
                # 分支 → 流年轮与流月轮逐字节同串（L2 multi_turn distinct
                # 实锤）。视图键为服务端内部键（不进 LLM schema），参数仍含
                # birth 键（L1 partial 键集契约不受影响）。
                _params = {"birth": birth}
                if "流月" not in (msg or ""):
                    _params["view"] = "year"
                # T018：档案兜底路径同样折算相对年份（本路径 L1 实测形态：
                # {"birth": …} 无 year 键 → 回复恒答当年）——同一事实源
                r = self._execute_tool_call(
                    "流月流年",
                    self._with_relative_cycle_year(
                        "流月流年", _params, msg),
                    user_id, user_question=msg)
                if r.ok and r.text:
                    return r.text
        except Exception:
            pass
        return None

    def _scene_naming_fallback(self, msg: str, user_id: str, stream_cb=None,
                               session_id=None) -> Optional[str]:
        """起名场景兜底：消息确定性拆 姓氏/性别/出生 → 起名工具（0 LLM）。

        原兜底 _handle_xingming → LLM 排盘/散文（L1 期望 naming 工具实际
        零调用，T045 3/3 实锤：回复是 bazi 卡片或空壳）→ 改确定性执行
        工具：split_naming_params 拆出 {surname, gender, birth} → dict 传
        执行层（serialize_params 多键 → "surname: 张\ngender: 男\nbirth: …"
        结构化形态，naming 工具解析器原生支持）→ L1 记录 naming 调用
        （三键与契约 partial 匹配）+ 回复为五行补益候选名卡片。
        拆不出姓氏/性别（"帮我起个名"无细节）→ None（fail-open 保留
        _free_chat 原文，LLM 自然追问）。
        """
        try:
            from src.tools.naming import split_naming_params
            info = split_naming_params(msg or "")
            if info and info.get("surname") and info.get("gender"):
                r = self._execute_tool_call("起名", info, user_id,
                                            user_question=msg)
                if r.ok and r.text:
                    return r.text
        except Exception:
            pass
        return None

    # ============================================================
    # k39 S3：合盘「单档补全」——一方缺信息时用默认命主档案补全本人
    # ============================================================

    # 来源标识（**服务端唯一文案**；小程序合盘页同串「本人（来自档案）」，
    # 满足 brief ③「服务端/前端口径一致，不要两套」）
    HEHUN_SELF_FROM_ARCHIVE_LABEL = "本人（来自档案）"

    # 「对方」语境标记：出生信息紧邻这些词 → 那一方是对方而非本人
    _HEHUN_OTHER_MARKERS = (
        "一个", "对方", "他", "她", "TA", "女朋友", "男朋友", "对象", "伴侣",
        "女孩", "男孩", "女孩子", "男孩子", "老婆", "老公", "妻子", "丈夫",
        "姑娘", "相亲", "介绍",
    )
    # 出生日期头（年+月）：定位消息里出生信息的位置
    _HEHUN_BIRTH_HEAD_RE = re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月")

    def _hehun_message_side(self, msg: str) -> str:
        """消息里那**一方**出生信息属于「本人」还是「对方」（k39 S3）。

        判据（可判定、零 LLM）：取该出生信息**前 8 字 + 后 6 字**窗口，
        命中 `_HEHUN_OTHER_MARKERS` → "other"，否则 "self"。
        例：「我和一个1992年…出生的女孩子合不合」→ 前窗含「一个」→ other；
        「我1990年5月20日 北京 男，和 TA 合不合」→ 前窗只有「我」、
        后窗「20日 北京 男」无标记 → self。
        """
        m = self._HEHUN_BIRTH_HEAD_RE.search(msg or "")
        if not m:
            return "self"
        window = (msg[max(0, m.start() - 8):m.start()]
                  + msg[m.end():m.end() + 6])
        return "other" if any(k in window for k in self._HEHUN_OTHER_MARKERS) \
            else "self"

    def _gender_ref_is_third_party(self, analysis, msg: str) -> bool:
        """k40 返工（Critical-1）：消息里的性别是否**指代第三人**（非本人语境）。

        判据 = 主语感知单一事实源（`_gender_word_subject` / `_GENDER_THIRD_BIRTH_MSG_RE`，
        见模块级注释）：
          ① 消息里的口语性别词**全部**被判为第三人主体（妹妹/姐姐/闺蜜/太太/
             嫂子/表妹/表姐/对象的女儿/对象是/相亲对象是/一个…女孩子…）→ True；
          ② 消息出生信息由第三人领属语引出（「我妹妹1990年出生的女孩子」、
             「我朋友1990年5月20日出生的女生」）→ True；
          ③ 消息里存在**本人自述**的性别词（我是女孩/我是一个女生…）→ False。
        判据与场景无关（改前限 hehun 场景 + 称谓白名单，白名单补不完 → 同类
        措辞一字之差即漏，见审查 Critical-1）。

        用途：G1 性别纠正守卫的前置条件——此类消息**不得**把消息性别当作
        用户改自己性别（T041 实证：守卫抢走 intent → hehun 链路从未被调用，
        全链 0 LLM；年份与档案一致时更会走 force_gender 覆写本人档案性别，
        即报告点名的 P0 档案污染风险）。守卫与提取层（`_has_self_gender_word`）
        同源但**独立生效**：声明式性别（独立「男/女」/「性别男/女」）不经
        口语词路径，只有本守卫能拦住「我妹妹1990年出生的，性别女」这类。

        边界：T008「我是女孩儿，不是男孩」形态 → 存在本人自述 → False
        （守卫行为不变）；判据异常 → False（保持改前行为，不劣化）。
        `analysis` 形参保留（调用方签名稳定，守卫不再依赖场景判定）。
        """
        if not msg:
            return False
        try:
            if _GENDER_THIRD_BIRTH_MSG_RE.search(msg):
                return True
            _seen_oral = False
            for _pos, _w in _gender_oral_words_in(msg):
                _seen_oral = True
                if _gender_word_subject(msg, _pos, _w) == "self":
                    return False  # 有本人自述 → 不是「全指第三人」
            return _seen_oral
        except Exception:
            return False  # 判据异常 → 不拦截（保持改前行为，不劣化）

    def _hehun_single_fill(self, msg: str, user_id: str) -> dict:
        """k39 S3 合盘「单档补全」：一方缺信息时用默认命主档案补全**本人**。

        返回 {"birth_a", "birth_b", "source_a"}（缺项为 ""；
        source_a ∈ {"message","archive",""}）。规则（服务端唯一口径）：

          1. 消息里双方齐全 → 一律以消息为准（**手填覆盖档案**，不补）；
          2. 消息里只有一方 → 按 `_hehun_message_side` 判归属：
             - "self"（本人，手填）→ birth_a = 消息（覆盖档案），birth_b 留空；
             - "other"（对方）→ birth_b = 消息，birth_a 取档案；
          3. 消息里没有出生信息 → birth_a 取档案（birth_b 留空，随后补问）。

        无档案且消息无信息 → 全空 → 调用方保持改前引导文案（**未建档行为不变**）。
        档案读取用 `_get_user_birth_profile`（persons 默认档优先，单一事实源）
        + `_fmt_birth_text`（既有档案→出生串口径，含农历→公历换算）。
        """
        if not msg:
            return {"birth_a": "", "birth_b": "", "source_a": ""}
        # 双方齐全（男…女…）→ 手填优先，原样返回（与 _split_hehun_pair 同口径）
        pair = self._split_hehun_pair(msg)
        if pair and pair[0] and pair[1]:
            return {"birth_a": pair[0], "birth_b": pair[1],
                    "source_a": "message"}
        # 双人语境但拆不出规范串（男/女 都在却解析失败）→ 不猜，交由原引导
        if re.search(r'男[^女]*', msg) and re.search(r'女.*', msg):
            return {"birth_a": "", "birth_b": "", "source_a": ""}
        info = self._extract_bazi_info(msg)
        side = self._hehun_message_side(msg) if info else ""
        birth_a = birth_b = ""
        source_a = ""
        if info:
            text = self._fmt_birth_text({
                "year": info[0], "month": info[1], "day": info[2],
                "hour": info[3], "minute": info[4], "city": info[5],
                "gender": info[6], "calendar": "solar",
            })
            if side == "other":
                birth_b = text
            else:
                birth_a, source_a = text, "message"
        if not birth_a:
            profile = None
            try:
                profile = self._get_user_birth_profile(user_id)
            except Exception:  # noqa: BLE001 — 档案读取失败按无档案处理
                profile = None
            if profile:
                birth_a = self._fmt_birth_text(profile)
                source_a = "archive"
        return {"birth_a": birth_a, "birth_b": birth_b, "source_a": source_a}

    def _hehun_tool_reply(self, fill: dict, user_id: str,
                          msg: str) -> Optional[str]:
        """单档补全的确定性出口：调合婚工具 + 显式标注来源（**两入口共用**）。

        - 工具可达（L1 记 hehun）：一方缺信息也照调（`birth_b=""`），
          由工具既有 `needs_info` 分支产出补问句 —— 不静默降级、不新造文案。
        - 来源标注（brief ①）：本人来自档案 → 「本人（来自档案）」；
          手填本人 → 「本人」（不谎称来源）。
        - 返回 None = 工具无可用输出 → 调用方走自己的兜底（行为不变）。
        """
        r = self._execute_tool_call(
            "合婚", {"birth_a": fill.get("birth_a", ""),
                     "birth_b": fill.get("birth_b", "")},
            user_id, user_question=msg)
        if not r.text:
            return None
        prefix = ""
        if fill.get("birth_a"):
            label = (self.HEHUN_SELF_FROM_ARCHIVE_LABEL
                     if fill.get("source_a") == "archive" else "本人")
            prefix = f"📌 合婚 · {label}：{fill['birth_a']}\n"
        if r.ok:
            return prefix + r.text
        if r.needs_info and fill.get("birth_a") and not fill.get("birth_b"):
            return prefix + r.text   # 本人已就位、只差对方 → 标注 + 补问
        return None

    def _scene_hehun_fallback(self, msg: str, user_id: str, stream_cb=None,
                              session_id=None) -> Optional[str]:
        """合婚场景兜底：消息确定性拆双方出生 → 合婚工具（引擎匹配，0 LLM）。

        原兜底 _handle_hehun 走 LLM analyze 输出散文（缺「男方」回显且 L1
        零调用，T039 flaky 实锤：LLM 自编合婚内容 1/3）。工具卡片按实际
        性别标注 男方/女方 四柱（format_hehun_card），契约关键词齐全。

        k39 S3 单档补全：一方缺信息时用默认命主档案补全本人（显式标注来源、
        手填优先），本人就位即调工具；只差对方 → 工具既有补问（不静默降级）。
        """
        try:
            fill = self._hehun_single_fill(msg, user_id)
            if fill["birth_a"] or fill["birth_b"]:
                out = self._hehun_tool_reply(fill, user_id, msg)
                if out:
                    return out
            # 无双方生辰（"我们合不合" 且无档案）→ 回退原引擎处理器（引导追问
            # 文案，与旧版 SCENE_DEFAULT_ENGINE 直调 _handle_hehun 行为一致——
            # 已有 T039 工具卡片正例，不破坏产品侧引导体验）。
            return self._handle_hehun(msg, user_id, stream_cb=stream_cb)
        except Exception:
            pass
        return None

    def _fmt_birth_text(self, profile: dict) -> str:
        """档案字典 → 出生信息自然语言串（择业工具 params.birth 用）。

        性别归一中文（persons 英文契约 male/female → 男/女），与引擎口径
        一致；hour/minute 缺省时省略（引擎按 0 时处理，行为与建档一致）。
        R2-5：lunar 档案 → 日期部分拼转换后公历（文本=公历口径，下游
        _extract_bazi_info 按公历解析正确）；solar/无标记 → 原值零回退。
        """
        y, m, d = profile.get("year"), profile.get("month"), profile.get("day")
        if str(profile.get("calendar") or "solar") == "lunar":
            _sol = to_solar_date(profile)
            if _sol:
                y, m, d = _sol
            else:
                logger.warning(
                    "R2-5 _fmt_birth_text：lunar 档案转公历失败，文本按原始值 "
                    "birth=%s-%s-%s", y, m, d)
        parts = [f"{y}年{m}月{d}日"]
        h, mi = profile.get("hour"), profile.get("minute")
        if h is not None:
            parts.append(f"{h}时" + (f"{mi}分" if mi else ""))
        if profile.get("city"):
            parts.append(str(profile["city"]))
        g = _GENDER_CN.get(str(profile.get("gender") or "").strip().lower(),
                           "unknown")
        parts.append(g)
        return " ".join(parts)

    # R1-3（评测 T009 修复·性别回显重挂）：润色/工具循环的 LLM 把排盘回复
    # 写成无性别散文（女档案契约 contains「女」1/3 实锤：纯散文只字未提
    # 女命）→ 确定性重挂性别声明（0 LLM，幂等：已有性别标记不再重复挂）：
    # - bazi 意图 + 引擎原稿（排盘真实发生）
    # - 回复含排盘标记（日主|大运|四柱|命盘|格局 之一，排除闲聊场景）
    # - 回复缺性别标记（女命/男命）
    # - subject=self（帮他人排盘不挂本人性别）
    # → 前置「（女命：本盘按女命排盘）」。只声明排盘口径不写大运方向
    # （起运顺逆由引擎盘面实际展示，阴年女顺排场景不被硬编码断言写错）。
    _CHART_MARKER_RE = re.compile(r"日主|大运|四柱|命盘|格局")
    _GENDER_MARK_RE = re.compile(r"女命|男命")

    def _build_chart_inject(self, result) -> str:
        """Task 10 快路径配套：已存排盘结果 → LLM 注入提示（防矛盾）。

        R1-3（T019 内容修复·流年错）：校准实锤回复流年干支写成出生年份
        的干支（2019 己亥）——引擎 liunian_rel 恒为当前流年（立春为界，
        {year, ganzhi}，2026 年 = 丙午）。把当前流年钉进注入提示：涉及
        流年一律以它为准，杜绝润色 LLM 自造/错位干支。字段缺失不注入
        （getattr 兜底，不编造）；任何异常忽略返回空串。
        """
        try:
            _cd = getattr(self, "chart_dao", None)
            if not _cd:
                return ""
            _parts = []
            if getattr(result, "day_master", None):
                _parts.append(f"{result.day_master}日主")
            if getattr(result, "geju", None):
                _parts.append(f"格局{result.geju}")
            if getattr(result, "yongshen", None):
                _parts.append(f"用神{result.yongshen}")
            # isinstance 守卫：Mock/测试桩的 liunian_rel 是 Mock 对象
            # （getattr 默认值对 Mock 不生效），_ln['year'] 会 TypeError
            # → 整段注入静默丢弃；真实 BaziResult.liunian_rel 恒为 dict。
            _ln = getattr(result, "liunian_rel", None)
            if isinstance(_ln, dict) and _ln.get("year") and _ln.get("ganzhi"):
                _parts.append(
                    f"当前流年 {_ln['year']} 年为 {_ln['ganzhi']} 年"
                    f"（流年干支 {_ln['ganzhi']}）")
            if not _parts:
                return ""
            _ln_note = ""
            if isinstance(_ln, dict) and _ln.get("ganzhi"):
                _ln_note = ("；涉及流年干支一律以当前流年 "
                            f"（{_ln.get('year', '今年')} {_ln['ganzhi']}）为准，"
                            "不得引用出生年份或其他年份的干支")
            return ("【用户已存排盘结果】" + "，".join(_parts)
                    + "。回答须与此一致，不矛盾。" + _ln_note + "。")
        except Exception as e:
            logger.warning("fastpath 已存结果注入失败（忽略）: %s", e)
            return ""

    def _rehang_gender_echo(self, reply: str, analysis, engine_draft,
                            user_id: str) -> str:
        if (not analysis or getattr(analysis, "intent", None) != "bazi"
                or not engine_draft or not reply
                or not self._CHART_MARKER_RE.search(reply)
                or self._GENDER_MARK_RE.search(reply)
                or (getattr(self, "_analysis_facts", None) or {}).get(
                    user_id, {}).get("subject", "self") == "other"):
            return reply
        try:
            _prof = self._get_user_birth_profile(user_id)
            _g = _GENDER_CN.get(
                str((_prof or {}).get("gender") or "").strip().lower(), "")
            if _g == "女" and "女" not in reply:
                return "（女命：本盘按女命排盘）\n\n" + reply
            if _g == "男" and "男" not in reply:
                return "（男命：本盘按男命排盘）\n\n" + reply
        except Exception:
            pass  # 档案读取异常 → 不重挂（宁缺勿错）
        return reply

    def _split_hehun_pair(self, msg: str) -> Optional[tuple]:
        """合婚场景兜底：确定性拆「男方…女方…」消息为 (birth_a 文本,
        birth_b 文本)。与 _handle_hehun 的 男[^女]* / 女.* 拆分同口径，
        输出为带性别标注的规范出生串（工具侧再走 _extract_bazi_info）。
        拆不出双方 → None。
        """
        if not msg:
            return None
        m_male = re.search(r'男[^女]*', msg)
        m_female = re.search(r'女.*', msg)
        if not (m_male and m_female):
            return None
        info_a = self._extract_bazi_info(m_male.group())
        info_b = self._extract_bazi_info(m_female.group())
        if not info_a or not info_b:
            return None
        return (self._fmt_birth_text({
            "year": info_a[0], "month": info_a[1], "day": info_a[2],
            "hour": info_a[3], "minute": info_a[4], "city": info_a[5],
            "gender": "男",
        }), self._fmt_birth_text({
            "year": info_b[0], "month": info_b[1], "day": info_b[2],
            "hour": info_b[3], "minute": info_b[4], "city": info_b[5],
            "gender": "女",
        }))

    def _looks_like_tool_echo(self, text: str) -> bool:
        """R1-2（T007/T094 类缺陷）：工具回显/参数 JSON 泄漏检测。

        命中条件（任一）：① 工具说明书模板行（中文名（cap_id）：…，与
        build_tool_description 逐字形态一致）；② 参数 JSON 泄漏（{ 后跟
        引号或中文，覆盖 {"birth": …} 与 {url} 模板形态）。回复层任何
        情况不得出现工具 schema/参数 JSON——回显即半成品回复。
        """
        if not text:
            return False
        return bool(_TOOL_DESC_ECHO_RE.search(text)
                    or _JSON_PARAM_LEAK_RE.search(text))

    # ============================================================
    # k11 事实纪律：本轮事实上下文 + 输出后称谓/神煞校验（纯规则）
    # ============================================================

    def _set_fact_ctx(self, user_id: str, gender, shensha) -> None:
        """记录本轮命理事实上下文（gender + 本盘神煞全集 allow），供流式出口
        chunk scrub 与整段 scrub 使用（chat_stream 出口逐 chunk 消费）。

        gender: 引擎归一结果（男/女/unknown）；shensha: 引擎算出全集（白名单）。
        无上下文的轮次（自由对话/非命理意图）不 scrub，防误伤。
        """
        try:
            self.__dict__.setdefault("_fact_ctx", {})[user_id] = {
                "gender": str(gender or ""),
                "shensha": [str(s) for s in (shensha or ())],
            }
        except Exception:
            pass

    def _pop_fact_ctx(self, user_id: str) -> None:
        """每轮 process 入口清空（上下文只属于触发它的那一轮排盘）。"""
        try:
            self.__dict__.get("_fact_ctx", {}).pop(user_id, None)
        except Exception:
            pass

    def _scrub_turn_text(self, text: str, user_id: str) -> str:
        """整段文本称谓/神煞 scrub（B/C 输出后校验器；纯规则，无上下文不 scrub）。"""
        if not text:
            return text
        try:
            ctx = (self.__dict__.get("_fact_ctx", {}) or {}).get(user_id)
            if not ctx:
                return text
            from src.utils.fact_guard import scrub_turn
            return scrub_turn(text, ctx.get("gender"), ctx.get("shensha"))
        except Exception:
            return text  # scrub 是增强：异常原样放行

    def _wrap_ctx_scrub_cb(self, stream_cb, user_id: str):
        """给 stream_cb 包一层"工具 JSON 过滤 + 本轮称谓/神煞 scrub"（chunk 级）。

        k11-E：LLM 流式先把 <tool_calls> JSON 工单/裸 JSON 吐出来时逐块拦截，
        任何 {tool…} JSON 不落用户可见流（与 chat_stream 出口同一过滤器语义，
        此处贴近生产 LLM 调用点再加一道，双层幂等）。
        """
        if stream_cb is None:
            return None
        try:
            from src.bot.stream_guard import wrap_chunk_filter as _wrap_json
            cb1 = _wrap_json(stream_cb)

            def _wrapped(evt_type: str, payload: dict) -> None:
                if evt_type == "chunk" and isinstance(payload, dict):
                    raw = (payload.get("text") if "text" in payload
                           else payload.get("content", ""))
                    if isinstance(raw, str) and raw:
                        cleaned = self._scrub_turn_text(raw, user_id)
                        if cleaned != raw:
                            # text/content 双键历史形态：同步覆盖防收尾比对错位
                            p2 = dict(payload)
                            for _k in ("text", "content"):
                                if _k in p2:
                                    p2[_k] = cleaned
                            cb1(evt_type, p2)
                            return
                cb1(evt_type, payload)

            return _wrapped
        except Exception:
            return stream_cb

    def _extract_zeri_exclude_dates(self, params) -> Optional[list]:
        """解析「换一批」去重日期 → select_lucky_days 的 exclude_dates 参数。

        - params 为 dict: 取 exclude_dates 键（list of "YYYY-MM-DD", 或逗号/空格分隔字符串）
        - params 为字符串: 识别内嵌 "exclude_dates: 2026-09-03,2026-09-06"
          （: / ＝ / = / ： 均可, fix-later 补全角冒号）
        无/非法 → None（不排除任何日期, 与调用方 exclude_dates or None 语义一致）。
        """
        if isinstance(params, dict):
            raw = params.get("exclude_dates")
        else:
            m = re.search(r"exclude_dates\s*[:＝=：]\s*([^<\n]+)", params or "")
            raw = m.group(1) if m else None
        if raw is None:
            return None
        if isinstance(raw, str):
            raw = re.findall(r"\d{4}-\d{2}-\d{2}", raw)
        elif not isinstance(raw, (list, tuple)):
            return None
        dates = list(dict.fromkeys(
            d for d in raw if isinstance(d, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)
        ))
        return dates or None

    def _extract_zeri_scene(self, text: str) -> Optional[str]:
        """择日场景判定：7 场景同义词表命中 → 规范场景名（嫁娶/搬家/开业/晋升/出行/提车/签约）。"""
        if not text:
            return None
        for scene, words in ZERI_SCENE_SYNONYMS.items():
            if any(w in text for w in words):
                return scene
        return None

    def _has_zeri_intent(self, text: str) -> bool:
        """择日意图判定：是否含 选日子/选个日子/择日/哪天/吉日/挑个时间/好日子。"""
        return any(w in text for w in ZERI_INTENT_WORDS)

    def _has_zeri_date_anchor(self, text: str) -> bool:
        """择日意图判定（日期锚点）：是否含具体日期（"8月20日"/"8月20号"/
        "2026-08-20"/"2026年8月20日"）。

        按任务简报测试用例「8月20日开业 → 窗口=8/20±7天」: 场景词+具体日期
        视为明确的选日请求（直接进引擎）; 仅场景词无日期无意图词（如"我想搬家"）
        仍走澄清反问。
        """
        if not text:
            return False
        if self._extract_date(text):
            return True
        return re.search(r'\d{1,2}\s*月\s*\d{1,2}\s*[日号]', text) is not None

    def _extract_window(self, text: str) -> Tuple[str, str]:
        """解析择日时间窗口, 返回 (start, end) ISO YYYY-MM-DD。

        - "下个月"/"下月" → 下个自然月
        - "下周"/"下星期" → 下个完整周（周一~周日）
        - 具体日期（"8月20日"/"8月20号"/"2026-08-20"/"2026年8月20日"）→ 该日 ±7 天
          （无年份且今年已过 → 顺延下一年）
        - 无任何信息 → 今天..未来30天
        防御: end 不超过 start+60 天
        """
        today = date.today()
        text = text or ""
        # 默认: 今天..未来30天
        start, end = today, today + timedelta(days=30)

        # 下个月 → 下个自然月
        if "下个月" in text or "下月" in text:
            y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
            start = date(y, m, 1)
            end = date(y + 1, 1, 1) - timedelta(days=1) if m == 12 \
                else date(y, m + 1, 1) - timedelta(days=1)
        # 下周 → 下个完整周（周一~周日）
        elif "下周" in text or "下星期" in text:
            start = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
            end = start + timedelta(days=6)
        else:
            # 具体日期: 完整年份形式复用 _extract_date
            target = None
            date_info = self._extract_date(text)
            if date_info:
                target = date(date_info[0], date_info[1], date_info[2])
            else:
                # 无年份: "8月20日"/"8月20号", 今年已过 → 顺延下一年
                m = re.search(r'(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]', text)
                if m:
                    try:
                        t = date(today.year, int(m.group(1)), int(m.group(2)))
                    except ValueError:
                        t = None
                    if t is not None and t < today:
                        try:
                            t = date(today.year + 1, int(m.group(1)), int(m.group(2)))
                        except ValueError:
                            t = None
                    target = t
            if target is not None:
                start, end = target - timedelta(days=7), target + timedelta(days=7)

        # 防御钳制: end 不超过 start+60 天（且不小于 start）
        if end > start + timedelta(days=60):
            end = start + timedelta(days=60)
        if end < start:
            end = start
        return start.isoformat(), end.isoformat()

    def _map_user_bazi_for_zeri(self, user_id: str, saved: Optional[dict] = None) -> Optional[dict]:
        """已保存/表单选填八字 → select_lucky_days 的 user_bazi 字典。

        saved 缺省 → 读存储档案(现状); 传入解析字段 dict(表单选填八字路径, 形如
        {year,month,day,hour,minute,city,gender}, 无四柱) → 先经本机排盘引擎
        calculate 补全四柱与五行(同 Fix4 口径), 再走同一映射, 替代存储档案。

        映射（zeri.py _build_lucky_card/_personal_score 实际消费的键）:
        - shengxiao: 年支 → 生肖（冲生肖排除）; 取不到则跳过（引擎不误伤）
        - day_gan/month_zhi: 日柱天干/月柱地支（喜用神计算）
        - wuxing/yongshen: 四柱齐备时用天干/地支五行表推导五行计数(或已存则透传),
          供引擎 _personal_score 计算喜用神 —— 修复前只传 shengxiao/day_gan/month_zhi,
          引擎无 wuxing 恒返 24 分(喜用神个人适配恒失效, 终审 Fix4)
        无八字 → None, 引擎走无八字兜底; 推导失败保持现状(引擎兜底 24)。
        """
        if saved is None:
            # k11c：统一档案读取（G3c 同源）——persons 建档用户 solar_time
            # 开关生效；原直读 users.bazi_info 拿不到 persons 权威值
            try:
                saved = self._get_user_birth_profile(user_id)
            except Exception:
                saved = None
        if not saved:
            return None
        # R2-6（同类残余直喂点）：存储档案可能是 lunar 原始 y/m/d（R2-5 后
        # 带 calendar 标记）→ 本机排盘补全前单点转公历（引擎契约=公历输入）
        saved = self._solarize_birth(saved)
        bazi = saved.get("bazi") or []
        # 表单选填八字: 解析字段 dict 无四柱 → 排盘补全(引擎异常 → 放弃, None 兜底不误伤)
        if not bazi and saved.get("year"):
            try:
                # k11c：档案 solar_time 随重排补全（0=关=北京时间直排；缺省开）
                result = self.engine.calculate(
                    int(saved["year"]), int(saved["month"]), int(saved["day"]),
                    int(saved.get("hour") or 0), int(saved.get("minute") or 0),
                    str(saved.get("city") or "北京"), str(saved.get("gender") or "unknown"),
                    solar_time=(saved.get("solar_time") not in (0, "0", False)))
                saved = dict(saved)
                saved["bazi"] = result.bazi
                saved["wuxing"] = result.wuxing   # 引擎五行表口径, 与 Fix4 推导一致
                bazi = result.bazi
            except Exception:
                return None
        if isinstance(bazi, str):
            bazi = bazi.split()   # 兼容 "甲子 乙丑 丙寅 丁卯" 字符串形式
        user_bazi = {}
        # 已显式保存的 wuxing/yongshen 优先透传(旧数据兼容)
        for k in ("wuxing", "yongshen"):
            v = saved.get(k)
            if v:
                user_bazi[k] = v
        if bazi and len(bazi[0]) > 1 and bazi[0][1] in ZODIAC_MAP:
            user_bazi["shengxiao"] = ZODIAC_MAP[bazi[0][1]]  # 年支 → 生肖
        if len(bazi) > 2:
            if bazi[2]:
                user_bazi["day_gan"] = bazi[2][0]     # 日柱天干
            if len(bazi[1]) > 1:
                user_bazi["month_zhi"] = bazi[1][1]   # 月柱地支
        # Fix4: 四柱齐备 → 天干/地支五行表推导 wuxing 计数(与 bazi.py BaziEngine.calculate
        # 同口径: 天干按 WUXING_TG、地支按 WUXING_DZ 各计一次), 引擎据此算喜用神
        if "wuxing" not in user_bazi and len(bazi) >= 4 \
                and all(isinstance(p, str) and len(p) == 2 for p in bazi[:4]):
            try:
                from src.engines.bazi import WUXING_TG, WUXING_DZ
                wx = {"金": 0, "木": 0, "水": 0, "火": 0, "土": 0}
                for pillar in bazi[:4]:
                    gan, zhi = pillar[0], pillar[1]
                    if gan in WUXING_TG:
                        wx[WUXING_TG[gan]] += 1
                    if zhi in WUXING_DZ:
                        wx[WUXING_DZ[zhi]] += 1
                if any(wx.values()):
                    user_bazi["wuxing"] = wx
            except Exception:
                pass  # 推导失败 → 保持现状(引擎兜底 24, 不误伤)
        return user_bazi or None

    # ============================================================
    # AI 原生（Phase 2）— 长期记忆主动提起（方案 2.2/5.5）
    # ============================================================

    def _get_welcome_back(self, user_id: str) -> str:
        """会话开始（非首次）时，基于用户画像生成"欢迎回来"式开场。

        - 由 LLM 基于画像自然生成（不硬编码模板）
        - 仅当：用户有持久记忆 且 本次会话还没有任何消息
        - 进程内缓存，单次请求只生成一次；LLM 失败时兜底用记忆模板
        """
        try:
            # A3-3（批次 2 审查结论）：开场白是快通道前置装饰——原实现仅逐段
            # try/except，has_memory（记忆层 DB）等未覆盖调用异常会冒泡，导致
            # 3 个调用点（流式首块/两条非流式拼接）整条回复丢失；外层兜底保证
            # 任何一步失败都只放弃开场白，主回复照常生成。
            if not self.session_dao or not self.memory_system:
                return ""
            if not self.memory_system.has_memory(user_id):
                return ""
            try:
                # 当前用户消息在 process 中先于本方法保存：只有 1 条（仅当前消息）才算会话开始
                history = self.session_dao.get_history(user_id, limit=3)
                if len(history) > 1:
                    return ""  # 会话已有历史消息，不算会话开始
            except Exception:
                return ""
            cache = getattr(self, "_welcome_cache", None)
            if cache is None:
                cache = {}
                self._welcome_cache = cache
            if user_id in cache:
                return cache[user_id]
            try:
                profile = self.memory_system.get_profile_summary(user_id)
            except Exception:
                profile = ""
            welcome = ""
            if profile:
                prompt = (
                    f"用户是「易理明灯」的回头客。用户画像：\n{profile}\n"
                    "请生成一句自然、温暖的欢迎回来开场白（30字以内，直接返回文本，"
                    "不要引号、不要JSON）。可以自然提及上次聊过的话题或最近的关心点，"
                    "像朋友打招呼一样。如果画像信息很少，就简单打个招呼。"
                )
                welcome = self._quick_flash(prompt, max_tokens=80, temperature=0.9)
            if not welcome:
                try:
                    welcome = self.memory_system.get_greeting(user_id)  # 兜底模板
                except Exception:
                    welcome = ""
            cache[user_id] = welcome
            return welcome
        except Exception as e:
            # A3 Minor ①（B1-11）：fail-open 兜底留观测日志——开场白任何失败 →
            # 放弃，不阻塞主回复（行为不变，仅加可观测性）
            logger.warning("欢迎回来开场白生成失败，已放弃: user=%s err=%s",
                           user_id, str(e)[:200])
            return ""

    def _compress_history(self, user_id: str, history: list, max_rounds: int = 7) -> list:
        """上下文压缩（方案 2.3）：会话超长时保留最近 3 轮完整 + 八字 + 关键事实摘要。

        丢弃中间的具体分析文本（只保留结论性记忆），避免 token 溢出。
        """
        if not history or len(history) <= max_rounds * 2:
            return history
        recent = history[-6:]  # 最近 3 轮（含当前消息）
        facts = []
        saved = None
        if self.dao:
            try:
                saved = self._get_user_birth_profile(user_id)
            except Exception:
                saved = None
        if saved and saved.get("bazi"):
            bazi_str = " ".join(str(p) for p in saved["bazi"][:4])
            facts.append(f"用户八字：{bazi_str}（已保存，别再问出生信息）")
        # Task 1 排盘档案打通：有原始出生字段 → 补出生行（LLM 工具路径可直接填参）
        birth_line = format_birth_line(saved)
        if birth_line:
            facts.append(birth_line)
        if self.memory_system:
            try:
                profile = self.memory_system.get_profile_summary(user_id)
                if profile:
                    facts.append(profile)
            except Exception:
                pass
        if self.memory:
            try:
                ctx = self.memory.get_context(user_id)
                if ctx:
                    facts.append(ctx)
            except Exception:
                pass
        if not facts:
            return recent
        summary = "\n".join(f"[记忆] {f}" for f in facts)
        return [{"role": "system", "content": summary}] + recent

    # ============================================================
    # L2 会话增量摘要（方案 §5.4）— 触发式滚动压缩（<summary>+<memories>）
    # ============================================================

    def _maybe_compact(self, user_id: str) -> str:
        """对话开始前检查触发 L2 压缩（上下文估算超阈值才压缩）。

        - 触发：历史输入 token > 窗口×70% → 拆分新旧 → 分块滚动摘要
        - 新摘要替换旧摘要（存 session_summaries 表，加密落库）
        - `<memories>` 提取的持久事实转入 L3（add_entry）
        - 失败降级：compactor 内部保留最近 3 轮+截断，不影响对话

        Returns:
            最新摘要文本（无触发/失败返回空字符串）
        """
        if not self.session_dao or not self.compactor:
            return ""
        # 廉价预检：按库中内容字节数估算 token 上限（密文 base64 ≈ 明文×1.33，
        # 保守系数 1.2 保证两倍安全），不触发就不解密历史（每轮零额外开销）
        # 隐私红线（终审 #1）：预检与压缩都排除 temp 倾诉消息（AND temp=0），
        # 夜间倾诉绝不进入 L2 摘要/session_summaries（24h 硬清理后应完全消失）
        try:
            conn = self.session_dao._connect()
            row = conn.execute(
                "SELECT COUNT(*), SUM(LENGTH(content)) FROM sessions"
                " WHERE user_id=? AND temp=0",
                (user_id,),
            ).fetchone()
            conn.close()
            msg_count = row[0] or 0
            byte_sum = row[1] or 0
        except Exception as e:
            logger.warning("L2 预检失败 user=%s: %s", user_id, e)
            return ""
        threshold = int(self.compactor.window_limit * self.compactor.trigger_ratio * 1.2)
        if msg_count < 20 or byte_sum < threshold:
            return ""
        try:
            history = self.session_dao.get_history(user_id, limit=2000, temp=False)
        except Exception as e:
            logger.warning("L2 读取历史失败 user=%s: %s", user_id, e)
            return ""
        messages = [{"role": h["role"], "content": h["content"]} for h in history]
        if not self.compactor.should_compact(messages):
            return ""
        prev = None
        try:
            prev = self.session_dao.get_summary(user_id)
        except Exception:
            prev = None
        prev_summary = (prev or {}).get("summary", "") or ""
        prev_memories = [(m or {}).get("content", "")
                         for m in (prev or {}).get("memories", []) if m] or None
        try:
            result = self.compactor.compact(messages, prev_summary=prev_summary,
                                            prev_memories=prev_memories)
        except Exception as e:
            logger.warning("L2 压缩异常 user=%s: %s", user_id, e)
            return ""
        if not result.summary_text:
            return ""
        try:
            self.session_dao.save_summary(
                user_id, result.summary_text, result.memories,
                model=getattr(self.llm, 'model', '') or '',
                message_count=len(messages), token_count=result.total_tokens,
            )
        except Exception as e:
            logger.warning("L2 摘要落库失败 user=%s: %s", user_id, e)
        # <memories> 持久事实 → L3 长期记忆
        for m in result.memories:
            self._apply_memory_entry(user_id, m)
        logger.info("L2 压缩完成 user=%s 旧消息%d 压缩率%.1f%% degraded=%s",
                    user_id, result.old_count, result.compression_pct * 100,
                    result.degraded)
        return result.summary_text

    def _apply_memory_entry(self, user_id: str, m: dict) -> None:
        """把 L2 <memories> 提取的持久事实写入 L3（方案 §5.5 来源①）。"""
        if not self.memory_system:
            return
        etype = (m or {}).get("type")
        content = (m or {}).get("content")
        if etype not in UserMemory.ENTRY_TYPES or not content:
            return
        try:
            self.memory_system.add_entry(
                user_id, etype, content,
                subject=m.get("subject", "") or content[:20],
                confidence=float(m.get("confidence", 0.8) or 0.8),
                ttl_days=m.get("ttl_days"),
                is_key=(etype in ("profile",)),
                source="l2_memories",
            )
        except Exception as e:
            logger.warning("L3 记忆写入失败 user=%s: %s", user_id, e)

    # ============================================================
    # L3 长期记忆（方案 §5.5）— 来源接入 / 关键事实保底
    # ============================================================

    def _collect_key_facts(self, user_id: str) -> list:
        """收集 L1 关键事实保底内容（方案 §5.3）：八字 + L3 is_key 条目原文。

        无论多旧都保留原文，不进压缩。
        """
        facts = []
        if self.dao:
            try:
                saved = self._get_user_birth_profile(user_id)
            except Exception:
                saved = None
            if saved and saved.get("bazi"):
                facts.append(f"用户八字：{' '.join(str(p) for p in saved['bazi'][:4])}"
                             "（已保存，别再问出生信息）")
            # Task 1 排盘档案打通：有原始出生字段 → 补出生行（LLM 工具路径可直接填参）
            birth_line = format_birth_line(saved)
            if birth_line:
                facts.append(birth_line)
        if self.memory_system:
            try:
                facts.extend(self.memory_system.get_key_facts(user_id))
            except Exception:
                pass
        # 去重保序
        seen = set()
        out = []
        for f in facts:
            if f not in seen:
                seen.add(f)
                out.append(f)
        return out

    def _persist_l3_bazi(self, user_id: str, bazi_info: dict) -> None:
        """八字排盘成功后写入 L3 profile 条目（方案 §5.5 来源②，关键事实）。"""
        if not self.memory_system:
            return
        try:
            b = bazi_info or {}
            bazi_str = " ".join(str(x) for x in (b.get("bazi") or [])[:4])
            if not bazi_str:
                return
            content = (f"用户八字已排盘：{bazi_str}"
                       f"（{b.get('year', '')}年{b.get('month', '')}月{b.get('day', '')}日"
                       f"{b.get('gender', '')}，日主 {b.get('day_master', '?')}）")
            self.memory_system.add_entry(
                user_id, "profile", content, subject="bazi",
                confidence=1.0, ttl_days=None, is_key=True,
                source="bazi_analysis",
            )
        except Exception as e:
            logger.warning("L3 八字记忆写入失败 user=%s: %s", user_id, e)

    def _sync_person_profile(self, user_id: str, birth: dict,
                             subject: str = "self",
                             facts: Optional[dict] = None,
                             birth_ctx: str = "") -> None:
        """P2 多人档案：排盘后同步建档（对话建档，方案 v5）。

        - subject=self：默认命主为唯一事实源——出生信息以最新排盘为准
          （年份不同也更新默认命主，不新建"命主N"）；
          无档案 → 建档 name="我" relation="自己"（含旧单档案自动迁移）
        - subject=other：按 facts 关系/姓名建 person；已存在同生日 person 则复用。
        先有先用保护照旧（gender 冲突不覆盖逻辑仍在 save_bazi_info / users.bazi_info 层）。
        - birth_ctx（k19 ④-4）：用户消息上下文片段 → person_dao 年份守卫
          （明示纠正句式豁免告警）；调用方传本轮消息/工具参原文。
        """
        if not self.dao:
            return
        try:
            from src.storage.person_dao import PersonDAO
            pdao = PersonDAO(self.dao.db_path)
            birth = birth or {}
            if not birth.get("year"):
                return
            b = {
                "gender": birth.get("gender", "unknown"),
                "birth_year": birth.get("year"),
                "birth_month": birth.get("month"),
                "birth_day": birth.get("day"),
                "birth_hour": birth.get("hour"),
                "birth_minute": birth.get("minute"),
                "calendar": birth.get("calendar", "solar"),
                "city": birth.get("city", ""),
            }
            if subject != "other":
                default = pdao.get_default_person(user_id)
                if default:
                    # 单一事实源：默认命主唯一，出生信息以最新排盘为准（年份不同也更新）
                    pdao.update_person(user_id, default["id"], birth=b,
                                       birth_ctx=birth_ctx)
                else:
                    pdao.create_person(user_id, name="我", relation="自己",
                                       is_default=True, birth=b,
                                       birth_ctx=birth_ctx)
            else:
                # subject=other：同生日 person 复用；否则按 facts 关系/姓名建档
                f = facts or {}
                existing = pdao.find_person_by_birth(user_id, b)
                if existing:
                    pdao.update_person(user_id, existing["id"], birth=b)
                    return
                name = str(f.get("name") or f.get("关系") or "").strip()[:32]
                relation = str(f.get("relation") or f.get("关系") or "其他").strip()
                from src.storage.person_dao import RELATION_VALUES
                if relation not in RELATION_VALUES:
                    relation = "其他"
                pdao.create_person(
                    user_id,
                    name=name or f"命主{pdao.count_persons(user_id) + 1}",
                    relation=relation, birth=b)
        except Exception as e:
            logger.warning("多人档案同步失败 user=%s: %s", user_id, e)

    def _persist_facts_entries(self, user_id: str, msg: str, facts: dict) -> None:
        """P2 System1 每轮提取钩子（阶段 5）：facts → L2 事实条目（去重入库）。

        - 性别/关系/公司/职位/城市等条目化（type: profile / preference / fact）
        - 出生信息走 persons 表，不入 fact_entries（避免重复）
        - 去重：同 type+content 不重复加，updated_at 刷新（add_fact_entry 内置）
        """
        if not self.memory_system or not facts:
            return
        skip_keys = {"subject", "birth", "birthday", "birth_date", "birth_info",
                     "birth_year", "birth_month", "birth_day", "birth_hour",
                     "birth_minute", "生日", "出生", "生辰"}
        type_map = {
            "gender": "profile", "relation": "profile", "relationship": "profile",
            "city": "profile", "hometown": "profile",
            "employer": "preference", "company": "preference",
            "position": "preference", "job": "preference",
            "industry": "preference",
            "公司": "preference", "职位": "preference", "职业": "preference",
            "行业": "preference",
        }
        subject = "other" if str(facts.get("subject", "self")) == "other" else "self"
        for k, v in facts.items():
            ks = str(k).strip().lower()
            if v is None or str(v).strip() == "" or ks in skip_keys:
                continue
            etype = type_map.get(ks, "fact")
            try:
                self.memory_system.add_fact_entry(
                    user_id, etype, f"{k}：{v}", subject=subject,
                    source_msg=(msg or "")[:200],
                )
            except Exception as e:
                logger.warning("L2 事实条目写入失败 user=%s key=%s: %s",
                               user_id, k, e)

    def _record_evolution(self, user_id: str, topic: str, reply: str) -> None:
        """P2 演化链：把本轮咨询的结论摘要 append 到 topic 时间线（cap 5 条）。"""
        if not topic or not reply or not self.memory_system:
            return
        try:
            quote = reply.replace("\n", " ").strip()
            if len(quote) > 160:
                quote = quote[:160] + "…"
            self.memory_system.add_evolution(user_id, topic, stance="", quote=quote)
        except Exception as e:
            logger.warning("演化链记录失败 user=%s topic=%s: %s", user_id, topic, e)

    def _capture_key_event(self, user_id: str, msg: str) -> None:
        """从用户消息中捕获明确关键陈述（工作/感情状态等）→ L3 event 条目。

        TTL 默认 90 天（如"正在找工作"3 个月失效）；同主题重复出现覆盖更新。
        """
        if not self.memory_system or not msg:
            return
        for pattern, etype, template, ttl_days, is_key in _KEY_EVENT_RULES:
            if re.search(pattern, msg):
                try:
                    self.memory_system.add_entry(
                        user_id, etype, template, subject=template.split("：")[0][:12],
                        confidence=0.9, ttl_days=ttl_days, is_key=is_key,
                        source="event_capture",
                    )
                except Exception as e:
                    logger.warning("L3 事件记忆写入失败 user=%s: %s", user_id, e)
                break  # 一条消息只记一个主要事件

    def _safety_flag(self, msg: str) -> Optional[str]:
        """安全事件标记（方案 §7.2 合规审计）：自伤/自杀信号 → 转介事件留痕。"""
        if msg and _SELF_HARM_RE.search(msg):
            return "self_harm_referral"
        return None

    def _pop_tool_log(self, user_id: str) -> Optional[dict]:
        """取走并清除该用户本轮的工具调用日志（落库用）。"""
        return self._tool_logs.pop(user_id, None)

    def _mark_card_turn(self, user_id: str, **marks) -> None:
        """E2-1 卡片化：记录本轮卡片判定上下文（引擎/直读路径调用）。

        记录仅用于 process() 出口的卡片包装；object.__new__ 装配的测试实例
        无 _card_turn 属性时静默跳过（不影响任何回复内容）。
        """
        turn = getattr(self, "_card_turn", None)
        if turn is None:
            return
        ctx = turn.setdefault(user_id, {})
        for k, v in marks.items():
            ctx[k] = v

    def _maybe_wrap_card(self, reply: str, user_id: str, *,
                         tool_calls: Optional[list] = None) -> str:
        """E2-1 对话消息卡片化：process() 出口统一包卡片标记。

        判定见 src.bot.card_mark.detect_card_type（纯函数，输入=回复文本+本轮
        上下文）。只作用于最终回复字符串（流式输出不受影响）；未命中/包装
        异常 → 原样返回（宁可漏包不可误包，绝不破坏回复）。
        """
        if not reply:
            return reply
        ctx = (getattr(self, "_card_turn", None) or {}).get(user_id) or {}
        try:
            card_type = detect_card_type(
                reply,
                tool_calls=tool_calls,
                scenario=(ctx.get("scenario") or "").strip() or None,
                direct_read=bool(ctx.get("data_read")),
                ran_paipan=bool(ctx.get("paipan")),
                ran_zeri=bool(ctx.get("zeri")),
                # 批次 2 P1（Task C1）：6 类引擎直跑标记
                ran_ziwei=bool(ctx.get("ziwei")),
                ran_liuyao=bool(ctx.get("liuyao")),
                ran_fengshui=bool(ctx.get("fengshui")),
                ran_mianxiang=bool(ctx.get("mianxiang")),
                ran_qimen=bool(ctx.get("qimen")),
                ran_dream=bool(ctx.get("dream")),
            )
            if not card_type:
                return reply
            return wrap_card(reply, card_type)
        except Exception as e:  # noqa: BLE001 — 判定/包装异常 → 保持原文
            logger.warning("卡片化失败（保持原文）user=%s: %s", user_id, e)
            return reply

    def _persist_user_turn(self, user_id: str, msg: str, analysis,
                           deep: bool, session_id: Optional[str],
                           regen: bool = False) -> None:
        """k13：用户消息落库（重试去重 + regen 同轮标记）。

        走 SessionDAO.add_user_message_dedup（原子判定+插入，plan §B2）：
        - regen=True（前端重试/重新生成）且同会话存在规范化同文 user 行 →
          不新插（生成链照常对既有轮次补 assistant）；
        - 无标记但同会话同文距最近行 ≤ 窗口（默认 20s）→ 不新插（双击/旧客户端）；
        - 其余照插。
        去重判定或落库异常（锁超时等）→ 退回原 add_message 直插
        （sessions 全量留存铁律：宁重勿丢，绝不让用户消息因护栏丢失）。
        """
        if not self.session_dao:
            return
        try:
            self.session_dao.add_user_message_dedup(
                user_id, msg,
                intent=analysis.intent if analysis else None,
                emotion=(analysis.emotion_label if analysis else None),
                model=getattr(self.llm, 'model', '') or '',
                safety_flag=self._safety_flag(msg),
                temp=deep, session_id=session_id,
                regen=bool(regen),
            )
        except Exception as e:
            logger.warning(
                "chat user-msg dedup guard failed, fallback plain insert: "
                "user=%s err=%s", user_id, e)
            try:
                self.session_dao.add_message(
                    user_id, "user", msg,
                    intent=analysis.intent if analysis else None,
                    emotion=(analysis.emotion_label if analysis else None),
                    model=getattr(self.llm, 'model', '') or '',
                    safety_flag=self._safety_flag(msg),
                    temp=deep, session_id=session_id,
                )
            except Exception:
                logger.exception("chat user-msg plain insert failed: user=%s",
                                 user_id)

    def process(self, message: str, user_id: str,
                stream_cb: Optional[Callable] = None, deep_night: bool = False,
                session_id: Optional[str] = None,
                downgraded: bool = False,
                regen: bool = False) -> str:
        """处理用户消息，返回回复。

        stream_cb（v8 流式阶段 3）：提供时把生成过程实时回调出去——
          ("chunk", {"text": ...}) 正文增量 / ("tool", {"text": ...}) 工具调用 /
          ("thinking", {"text": ...}) 思考步骤；
        不提供时行为与旧版完全一致（/api/chat 兼容）。

        deep_night（Task 5 倾诉临时通道）：跳过 L2 事实/事件捕捉/演化链等记忆管线，
        本轮回合消息全部以 temp 标记落库（24h 硬清理兜底），并注入深夜语气层。

        session_id（会话隔离）：前端新开对话时生成新会话标识，AI 上下文只取本会话
        消息（不带上个对话内容）；None = 旧行为（按用户全量取上下文）。

        regen（k13 重试去重，2026-09-09）：前端重试/重新生成标记 = 对既有轮次的
        同轮 regenerate——用户消息不新插行（同会话存在规范化同文 user 行时，
        由正常生成链对既有轮次补 assistant 行）；无匹配行照插（审计完整）。
        普通新提问不带该标记。
        """
        msg = message.strip()
        self._deep_night[user_id] = bool(deep_night)
        deep = self._deep_night.get(user_id, False)
        # L5-2（I-1）：降级链路标记——本回合各前置 LLM 调用（意图分析/秒回安抚/
        # L2 压缩/建议卡）按此跳过或降为规则判定，只保留核心回复生成走精简链路
        self._downgraded[user_id] = bool(downgraded)
        # 清理上一轮残留的工具日志（xuetang/advisor/confidant 等早退分支不消费）
        self._pop_tool_log(user_id)
        # k11-B/C：每轮清空事实上下文——本轮排盘重新 _set_fact_ctx 后才对输出
        # 生效（称谓/神煞 scrub 只作用于命理轮，自由对话无上下文不 scrub）
        self._pop_fact_ctx(user_id)
        # E2-1 卡片化：重置本轮卡片判定上下文（引擎/直读路径会写入记录；
        # object.__new__ 装配的测试实例无该属性时跳过——卡片化纯增量，不阻断主流程）
        _turn = getattr(self, "_card_turn", None)
        if _turn is not None:
            _turn[user_id] = {
                "scenario": "", "paipan": False, "zeri": False, "data_read": False,
                # 批次 2 P1（Task C1）：6 类引擎直跑标记
                "ziwei": False, "liuyao": False, "fengshui": False,
                "mianxiang": False, "qimen": False, "dream": False}
        # 阶段 5：清理上一轮残留的引用来源（早退分支不注册，防泄漏）
        self._citations.pop(user_id, None)
        # k11b：清理上轮自动联网检索事实（同 query 复用表只属于触发它的那轮；
        # object.__new__ 装配的测试实例可能无该属性——getattr 兜底）
        try:
            self._turn_grounded.pop(user_id, None)
        except (AttributeError, KeyError):
            pass

        # Step -2: Cache check (D2 speed optimization)
        # 终审：deepNight 请求跳过缓存读写——夜里语气/陪伴类回复不可命中白天缓存，
        # 缓存键也隐含用户+消息，避免倾诉缓存串味（应修 #缓存命中 temp/语气）
        # 会话隔离：缓存键掺入 session_id——新会话不命中旧会话同文回复的缓存
        # （否则新开对话问同一句仍会拿到旧会话缓存答案，白做隔离）
        # R1-2（评测 T089 修复·改城市立即重算）：缓存键掺入档案指纹
        # （G3b H-7 同口径，src/storage/birth_profile.profile_fingerprint）——
        # 改城市/改八字 → 指纹变化 → 当日缓存键变化 → 立即重算不命中旧缓存；
        # 无档案 "none" 与建档后指纹天然分键（与 api/calendar.py 同语义）。
        if is_cacheable(msg) and not deep:
            _cache_fp = self._profile_cache_fingerprint(user_id)
            cached = self.cache.get(f"{msg}::fp:{_cache_fp}", user_id,
                                    session_id or "")
            if cached:
                # 兜底：缓存回复可能存于旧格式（TOOL 标签残留）——出口强制 strip
                return strip_tool_calls(cached) or cached

        # Step -1: 反馈检测 (👍/👎) — learn from user feedback
        if msg in ("👍", "👎", "好评", "差评", "准", "不准", "good", "bad") or msg.startswith("👍") or msg.startswith("👎"):
            return self._handle_feedback(msg, user_id, session_id=session_id)

        # T5 重看盘直读（0 引擎 0 LLM）：问"我的盘/我的八字"等重看表述且
        # chart_records 已有排盘结果 → 直接读库秒回。置于额度/意图分析/
        # 引擎预生成（_start_pregen_instant）之前——命中即短路，不消耗额度、
        # 引擎绝不计算、LLM 绝不调用。
        reuse_text = self._try_reuse_chart(user_id, msg)
        if reuse_text:
            return self._maybe_wrap_card(reuse_text, user_id)

        # Step 0.6: 额度检查（L5-2 I-2 新旧额度协调）——聊天消息不再走旧硬断：
        # 免费用户对话由 chat_quota（15 条/日）治理，超限降级续聊（downgraded=True），
        # 绝不 429 硬断（「免费用户永远能聊」）。旧额度（memberships.queries_used/
        # queries_limit）只对非聊天功能生效——如择日工具 _tool_zeri 的引擎调用门
        # （_check_quota 仍在 1533 行保持原样）。

        # Handle "会员" keyword — show upgrade info（L5-2：档位与 SUBSCRIBE_PLANS 对齐）
        # R1-1（评测 T087/T088 修复·支付守卫优先级）：原守卫仅整句等值命中，
        # "开会员多少钱"/"帮我充一下会员" 等自然支付句式绕过 → LLM 意图漂移 →
        # scene_hint 兜底建档引导"需要先了解你的命盘哦～"，支付入口被吞且
        # memberships 3→4 攻击写入（T087/T088 实锤）。扩展为：
        #   等值命中 _MEMBER_GUARD_EXACT（原行为不变）
        #   或（含"会员" 且 命中 _MEMBER_PAY_WORDS 任一支付词）→ 支付守卫
        # 守卫命中即短路返回会员计划文案，绝不落入建档引导（优先级最高：
        # 本分支位于 record_query 会员直读/意图分析/建档引导之前）。
        # 纯账务查询（"我的会员额度还剩多少"）不含支付词 → 不受影响，
        # 仍走下方 record_query 直读（守卫不劫持查询，复用 record_query 同表）。
        # Q2 反例（既有产品契约）：含「续费」的句子不命中子串守卫——「续费
        # 会员多少钱」「我不想续费了」等续费语境走全流程/LLM（与 record_query
        # 会员直读的支付词守卫同口径：续费语境不短路）；「续费」整句仍等值
        # 命中 _MEMBER_GUARD_EXACT（Q2 正例，行为不变）。
        if (msg.strip() in _MEMBER_GUARD_EXACT
                or ("会员" in msg
                    and "续费" not in msg
                    and any(w in msg for w in _MEMBER_PAY_WORDS))):
            upgrade_msg = (
                "🌟 **易理明灯会员计划**\n\n"
                "📌 **基础会员**（完整分析 · 每日运势 · 畅聊不设限）\n"
                "  - 月卡 19.9 元/月\n"
                "  - 季卡 49.9 元/季\n"
                "  - 年卡 168 元/年（折合 14 元/月）\n\n"
                "📌 **高级会员** 39.9 元/月\n"
                "  - 含论财/论事业/论健康等专项论断\n"
                "  - 专属深度报告\n\n"
                "💡 免费用户每天可畅聊 15 条，超限自动切换精简回复；"
                "开通会员解锁完整版。\n"
                "回复「开通会员」或在「我的」页选择套餐即可升级！"
            )
            # 批次 2 P2（Task C2）拍板：会员升级/续费回复与其他付费动作一致
            # 包卡片。类型判据：会员域回复归 data 卡（E2-1 契约规则 3——
            # RecordQuery 会员直读同为 data）；升级信息为服务端确定性文案
            # （无 ⚠️/暂不可用/引擎执行失败 错误签名），直接判定可包。
            # 出口统一走 _maybe_wrap_card：错误签名拒包/防重复包装等防御
            # 双闭环对确定性文案天然生效；包装后落库，历史消息可回渲染。
            self._mark_card_turn(user_id, data_read=True)
            upgrade_msg = self._maybe_wrap_card(upgrade_msg, user_id)
            if self.session_dao:
                self.session_dao.add_message(user_id, "assistant", upgrade_msg,
                                             temp=deep, session_id=session_id)
            return upgrade_msg

        # Task 6 存量数据直读（0 引擎 0 LLM）：问"我的档案/解梦/历史/签/灯语/
        # 择吉/会员额度"等 → 直接读库秒回。置于意图分析/预生成之前——
        # 命中即短路（不消耗额度、LLM 绝不调用）；未命中返回 None 走全流程。
        if hasattr(self, "record_query") and self.record_query:
            try:
                direct = self.record_query.direct_query(user_id, msg)
            except Exception:
                direct = None  # DB 异常 fail-open，不阻塞 process 入口
            if direct:
                # E2-1 卡片化：存量直读 → data 卡片
                self._mark_card_turn(user_id, data_read=True)
                return self._maybe_wrap_card(direct, user_id)

        # T056（k38，K5 回归）签文释义直读（0 LLM 0 编造）：问「关帝灵签第三签
        # 是什么意思」类签文知识问题 → 从签库单一事实源确定性作答（见
        # _answer_qian_meaning）。修前无此路由 → LLM 判 advisor → 建档引导
        # 死胡同（回复连「签」字都没有）。置于直读之后、简单意图/AI 分析之前：
        # 命中即短路（不消耗额度、LLM 绝不调用，与存量直读同族）；签种/签号
        # 不明确 → None 落全流程（不猜签种给错签诗）。
        qian_reply = self._answer_qian_meaning(msg)
        if qian_reply:
            self._mark_card_turn(user_id, data_read=True)
            return self._maybe_wrap_card(qian_reply, user_id)

        # Step 0.4: A4 意图分级路由——简单意图直通快通道（0 LLM，预算感知）。
        # 问候/感谢/再见/日期时间等简单意图不再经过 AI 慢推理（意图分析 +
        # 回复生成 LLM 均省）；命中即短路：不扣额度、不写会话历史（与 T10
        # 快路径裁决一致）；未命中/异常 → None 走主链（fail-open）。
        # 普通/复杂档走现状主链不变（模型矩阵见 _score_intent_complexity）。
        simple_reply = self._route_simple_intent(msg, user_id)
        if simple_reply is not None:
            return simple_reply

        # Step 0.5: AI 分析 — 情绪 + 意图 in ONE call (no keywords, no two calls)
        # Task 2 等待时长优化：消息含完整出生信息时，把「排盘 + 秒回安抚」提交到
        # 后台线程与意图分析并行发出（二者无依赖，可重叠）；bazi/career 路由由
        # _do_bazi_analysis 消费，其他路由静默丢弃（flash 成本低、无墙钟代价）。
        # L5-2（I-1）：降级链路禁用付费前置调用——不提交「排盘+秒回安抚」预生成
        # （worker 内 _gen_instant_reply 为 LLM 调用）；意图分析改规则快判。
        if not downgraded:
            self._pregen_instant[user_id] = self._start_pregen_instant(msg, user_id)
        else:
            self._pregen_instant.pop(user_id, None)  # 清掉残留 Future，防泄漏
        # v2026-08-17（思考过程元宝式）：意图分析（~1s）此前无思考事件，
        # 用户发送后胶囊迟迟不出现——首条事件在开工时即发出
        self._emit_stream_event(stream_cb, "thinking", "正在领会你的意思…")
        _t0 = time.monotonic()
        if downgraded:
            analysis = self._rule_analyze(msg)
        else:
            analysis = self._analyze_message(msg, user_id, session_id=session_id)
        logger.info("[timing] stage=intent duration=%.1fs",
                    time.monotonic() - _t0)
        # R1-1（评测 T096 修复·跨用户污染）：本人运势类问句 → 确定性强制
        # bazi 意图。原链：LLM 意图分类器对"我的运势怎么样"漂移（free_chat/
        # advisor 均出现过）→ 回复生成 LLM 用会话上下文里的他人出生信息
        # 冒充本人（B3-1 在线实锤"根据你1976年5月13日出生的信息"，T096
        # 轮2 实锤 丙辰 盘）。强制 bazi 后走 _handle_bazi：
        #   - B3-1 规则1（档案优先）：默认命主（当前会话用户）完整出生信息
        #     直接使用 → 庚午（本人 1990 档案）必现、丙辰（朋友盘）必不现；
        #   - facts.subject 强制 "self"：排盘/落库只认本人（他人信息只作
        #     排盘展示，不替换当前用户档案——接口契约零破坏，前端字段不变）；
        #   - 无档案 → F2 渐进式建档引导（与 bazi 意图一致，不回归建档行为）。
        if (analysis.intent != "bazi"
                and _SELF_FORTUNE_RE.search(msg)
                and not self._is_third_party_birth_request(msg)):
            analysis.intent = "bazi"
            _facts = analysis.facts or {}
            if _facts.get("subject", "self") != "self":
                _facts = dict(_facts)
                _facts["subject"] = "self"
                analysis.facts = _facts
        # T076/k38-I4 单人婚姻询问不得落 hehun/advisor 死胡同（确定性兜底，
        # 双保险）：LLM 意图把「帮我看看我的婚姻状况」判成 hehun（prompt 旧规则
        # 「婚姻匹配 → hehun」未区分单/双人）→ `_handle_hehun` 无双方生辰直接吐
        # 「给我双方生辰即可直接测算」死胡同（T076 实锤）；analyzer 单人婚姻
        # 强路由判 advisor 时，若消息自带生辰则回「请提供你的出生信息」自相矛盾
        # （k38-I4 审查实测）→ 有生辰一律改判 bazi（落档路径）。命中四条件（全部
        # 同时成立才改判）：① 意图 = hehun/advisor ② 消息含 婚姻/姻缘 ③ 无双人
        # 语境（他/她/我们/双方/对象…）④ 无运势时间锚（运势/运程/流年/今年/明年）。
        # 例：无生辰 → advisor 建档引导「出生年月日时/出生地/性别」（T076 断言）；
        # 带完整/部分生辰 → bazi（排盘建档 / F2 渐进累积回显缺什么要什么）。
        # 双人合盘（含第二人）与场景词确定性命中（TOOL_SCENE_WORDS["hehun"]）
        # 一律不动——T039/T040/T044 现状回归保护；带运势锚的单人问句（T022
        # 「帮我看看我的婚姻运势」当前绿）同样不动。
        self._redirect_single_marriage(analysis, msg)
        # R1-2（评测 T008 修复·纠正不重排）：口语性别词纠正（"我是女孩儿，
        # 不是男孩"）→ 确定性强制 bazi 意图。原链：意图分类把此类归 free_chat，
        # G1 纠正逻辑在 _handle_bazi 内部永不执行——只确认不重排（chart_records
        # 零新增，T008 3/3 实锤）。门控：消息提取到已确认性别 + 档案性别已确认
        # + 不一致（male/female 归一中文后判定，persons 档案 gender 存英文）→
        # 强制 bazi → _handle_bazi 走 G1 纠正路径（重排 + 双写档案 + 固定回执）。
        # k40（T041·最高优先，L1+L2 同根因）：前置条件补「**非本人语境**」——
        # 消息里的出生信息指代对方时，其中的「女孩子/男孩子」是描述对方，不是
        # 用户在改自己的性别（见 `_gender_ref_is_third_party`）。改前实锤：
        # 「我和一个1992年10月1日 上海出生的女孩子合不合」→ 抽到 gender=女
        # （来源「一个…女孩子」）→ 档案 male → 判定纠正 → intent 被改写成 bazi
        # → `_handle_bazi` 见 1992≠1990 → 档案冲突确认 → **hehun 链路从未被
        # 调用**（全链 0 LLM，attempt elapsed 0.061s，L1 actual_calls=[]）。
        if (analysis.intent != "bazi"
                and not self._is_third_party_birth_request(msg)
                and not self._gender_ref_is_third_party(analysis, msg)):
            try:
                _cur_g = (self._extract_partial_birth(msg) or {}).get("gender")
                if _cur_g in ("男", "女"):
                    _saved_g = ((self._get_user_birth_profile(user_id) or {})
                                .get("gender"))
                    if self._is_gender_correction(_cur_g, _saved_g):
                        analysis.intent = "bazi"
            except Exception:
                pass  # 门控失败 → 保持原意图（不阻断主链）
        # k41（②g 兜底出口）：结构不可辨的性别声明（子句句首口语性别词 + 裸出生
        # 元组 + 消息还有其他内容）→ 不静默改写（`_gender_ref_is_third_party` 已
        # 按非本人拦下 G1 纠正/画像写入），也不静默丢弃：出口回一句询问确认
        # （确定性文案，零 LLM；用户按「我是{词}」复述即走 ① 组自述规则 → 纠正
        # 生效，无需跨轮状态）。见 `_gender_ambiguous_word` 注释与报告。
        _amb_word = _gender_ambiguous_word(msg)
        if _amb_word:
            self._gender_confirms = getattr(self, "_gender_confirms", None) or {}
            self._gender_confirms[user_id] = _gen_gender_confirm_ask(_amb_word)
        # 阶段 5（方案 v5）：记录本轮理解出的关键事实（subject=other 时排盘不写本人画像）
        self._analysis_facts[user_id] = analysis.facts or {}
        # P2 System1 每轮提取钩子（阶段 5）：facts → L2 事实条目（去重入库）
        # Task 5 deepNight：倾诉临时通道不落 L2 事实（24h 后整条会话消失）
        if not deep:
            self._persist_facts_entries(user_id, msg, analysis.facts or {})
        # P3 联网激活修复：needs_search 引导必须在【首次】 LLM 调用前注入。
        # 旧逻辑只在 _run_tool_loop 的后续迭代注入（LLM 已输出 <tool_call> 才发生），
        # 导致 LLM 从未看到搜索引导。这里预生成 hint，free_chat/润色两条路径都注入。
        try:
            analysis_hint = self._tool_loop_analysis_hint(analysis, msg)
        except Exception:
            analysis_hint = ""
        if analysis_hint:
            logger.info("P3 联网引导注入 user=%s needs_search=%s hint=%s",
                        user_id, getattr(analysis, "needs_search", False),
                        analysis_hint[:60].replace("\n", " "))

        # k41（T104 收尾）：**先检索**——检索判定与回复路径解耦（单一接缝）。
        # `decide_search` 判该搜时，无论下面走 xuetang/advisor 关键词分支、
        # confidant、free_chat 还是 handler_map（bazi/hehun/…）哪条路径，都在
        # 路由前检索一次；结果块注入该路径上下文（advisor 见 `_handle_advisor`
        # 的 ground_hint；free_chat/润色见 extra_hint），出口统一补来源尾注。
        # 改前该调用只在 `_will_polish` 块内 → advisor 路径整块跳过（T104 实证：
        # 检索不发生、回复无实体无来源）。判定语义零放宽（见接缝方法注释）。
        _k11b_ground = self._ground_search_for_turn(msg, user_id, analysis)
        _ground_hint = "\n".join(self._ground_hint_parts(_k11b_ground))

        # Task 3（思考步骤渐进展示）：不再在此集中预发全部思考步骤——
        # 各 _do_*/_handle_* 在真实工作里程碑处按进度发出（首条开工即发、
        # 每完成一步真实工作推进一步）；简单聊天无意图不发（保持不变）。

        # Phase 3: Track mood in user memory（Task 5 deepNight：倾诉情绪不入长期记忆）
        if self.memory_system and analysis.emotion_label and not deep:
            self.memory_system.add_mood_record(user_id, analysis.emotion_label)

        # AI 原生（Phase 2）：长期记忆 — 记录本消息主题次数（画像自动积累，方案 6.1）
        topic = ""
        if self.preference_dao:
            try:
                topic = self.preference_dao.detect_topic(msg) or ""
            except Exception:
                topic = ""
        if topic and self.memory_system and not deep:
            try:
                self.memory_system.record_topic(user_id, topic)
            except Exception:
                pass
        # 多次重复话题检测（同一主题 ≥3 次 → 提示换个角度，方案 5.5）
        topic_hint = ""
        if topic and self.memory_system and not deep:
            try:
                count = self.memory_system.get_topic_count(user_id, topic)
                _topic_cn = {"wealth": "财运", "love": "感情", "career": "事业",
                             "health": "健康", "growth": "个人成长"}
                if count >= 3:
                    topic_hint = (
                        f"【提示】用户已经是第 {count} 次聊到「{_topic_cn.get(topic, topic)}」"
                        "这个话题了。可以自然地提一句“这个话题我们聊过几次了，"
                        "要不要换个角度看看？”，但不要说教、不要生硬。"
                    )
                # P2 演化链：时间线 ≥2 条 → 注入"过往咨询演变"提示（方案 §三）
                evo_hint = self.memory_system.format_evolution_hint(user_id, topic)
                if evo_hint:
                    topic_hint = (topic_hint + "\n" if topic_hint else "") + evo_hint
            except Exception:
                topic_hint = ""

        # L3 来源②：对话中的明确关键陈述（工作/感情状态等）→ event 条目
        # Task 5 deepNight：倾诉内容不入 L3 记忆（临时通道 24h 硬清理）
        if not deep:
            self._capture_key_event(user_id, msg)

        # Save user message to session history（阶段 2：emotion/model/安全标记落库；
        # Task 5 deepNight：用户消息带 temp 标记）
        # k13 重试去重（2026-09-09，用户实锤点「重试」同一句消息存 4 份 user 行）：
        # 用户消息落库改走 _persist_user_turn —— regen 标记/同会话同文窗口去重，
        # 判定+插入原子（BEGIN IMMEDIATE），异常退回原 add_message（宁重勿丢）
        self._persist_user_turn(user_id, msg, analysis, deep, session_id,
                                regen=bool(regen))

        # 流式模式（v8 阶段 3）："欢迎回来"开场提前生成并作为首个正文块流出，
        # 让回头客在正文生成前就有内容可读（秒回感知）；后续不再重复拼接。
        if stream_cb is not None:
            welcome = self._get_welcome_back(user_id)
            if welcome:
                try:
                    stream_cb("chunk", {"text": welcome + "\n\n"})
                except Exception:
                    pass

        # Priority: xuetang keywords trump everything
        # L5-2 修复（降级成本）：降级时 xuetang/advisor/confidant 关键词分支
        # 门控 → 走 lite 精简回复（这些分支内部为 RAG/LLM 全量调用，降级不调）。
        if any(kw in msg for kw in ["学堂", "学习教程", "命理入门"]):
            self._consume_quota(user_id)
            if downgraded:
                reply = self._free_chat(msg, user_id, downgraded=True)
            else:
                reply = self._handle_xuetang(msg, user_id)
            # k41：早退分支同样过检索来源尾注（该搜的已搜到 → 回复体现来源）
            reply = self._apply_ground_source_trace(reply, _k11b_ground)
            # k41（②g）：歧义性别声明的询问确认同点重挂（早退分支不吞）
            reply = self._apply_gender_confirm(reply, user_id)
            if self.session_dao:
                self.session_dao.add_message(user_id, "assistant", reply, intent="xuetang",
                                             temp=deep, session_id=session_id)
            return reply

        # Task 5: advisor keyword fallback — catch "建议"/"怎么办" even if AI misses it
        if any(kw in msg for kw in ["建议", "怎么办", "有什么建议", "帮我分析", "我该怎么做"]):
            self._consume_quota(user_id)
            if downgraded:
                reply = self._free_chat(msg, user_id, downgraded=True)
            else:
                # k41：advisor 路径注入检索块（与 handler_map 入口同一实现）
                reply = self._handle_advisor(msg, user_id,
                                             ground_hint=_ground_hint)
            reply = self._apply_ground_source_trace(reply, _k11b_ground)
            # k41（②g）：歧义性别声明的询问确认同点重挂（早退分支不吞）
            reply = self._apply_gender_confirm(reply, user_id)
            if self.session_dao:
                self.session_dao.add_message(user_id, "assistant", reply, intent="advisor",
                                             temp=deep, session_id=session_id)
            return reply

        # H1: 心事树洞 — user sharing a story (overrides fortune intent when no birth info)
        # L5-2 修复（降级成本）：降级时 is_sharing 门控——不走 confidant 全量
        # LLM 分支，落回 intent=None → _free_chat 走 lite 精简回复。
        if analysis.is_sharing and not downgraded:
            # Only skip confidant if user explicitly provides birth date info
            has_birth_info = bool(re.search(r'\d{4}\s*[年/-]', msg))
            if not has_birth_info:
                self._consume_quota(user_id)
                reply = self._handle_confidant(msg, user_id, analysis,
                                               session_id=session_id)
                # B2 reviewer Important：confidant 早退不经 _run_tool_loop——
                # 兜底剥离工单残留（_handle_confidant 内部已剥，双剥幂等）
                reply = strip_tool_calls(reply) or reply
                # k41：早退分支同样过检索来源尾注（该搜的已搜到 → 回复体现来源）
                reply = self._apply_ground_source_trace(reply, _k11b_ground)
                # k41（②g）：歧义性别声明的询问确认同点重挂（早退分支不吞）
                reply = self._apply_gender_confirm(reply, user_id)
                if self.session_dao:
                    self.session_dao.add_message(user_id, "assistant", reply,
                                                 temp=deep, session_id=session_id)
                return reply

        if analysis.intent is None:
            self._consume_quota(user_id)
            # k41：已自动检索 → 检索块进 extra_hint（并压制旧「先发 web_search
            # 工单」引导——结果已在上下文；与润色路径同款口径）
            hints = [h for h in (topic_hint,
                                 ("" if _k11b_ground else analysis_hint)) if h]
            hints.extend(self._ground_hint_parts(_k11b_ground))
            if deep:
                from src.bot.night_persona import NIGHT_TONE_HINT
                hints.insert(0, NIGHT_TONE_HINT)
            reply = self._free_chat(msg, user_id, emotion_label=analysis.emotion_label,
                                    extra_hint="\n".join(hints),
                                    stream_cb=stream_cb, session_id=session_id,
                                    downgraded=downgraded,
                                    scene_hint=getattr(analysis, "scene_hint", None))
            # AI 原生（Phase 1）：<tool_call> 工具调用循环
            reply = self._run_tool_loop(msg, user_id, reply, stream_cb=stream_cb,
                                        analysis=analysis, session_id=session_id)
            # 阶段 2：本轮工具调用日志 → 落库字段
            tool_log = self._pop_tool_log(user_id)
            # 批次 2 E6（工具 AI 决策通道）：场景兜底——LLM 未输出任何工单
            # （tool_log 无 calls）且 scene_hint 命中映射表 → 回退默认引擎
            # （hehun/career_dir/num_omen → _scene_*_fallback 确定性执行对应
            # 工具（R1-2，0 LLM）：L1 记录真实工具调用 + 回复为结构化卡片；
            # naming→_handle_xingming / fortune_cycle→_handle_advisor）。
            # 降级链路不做引擎兜底（_rule_analyze 不产出 scene_hint，双保险
            # 再挡一次）。兜底失败/返回 None → 保留 _free_chat 原文（fail-open；
            # R1-2：_scene_*_fallback 无档案/无双方生辰时返回 None——旧版
            # _handle_* 恒返回字符串，直接赋值 None 曾让 reply 变 None）。
            if (not (tool_log or {}).get("calls")
                    and not downgraded
                    and getattr(analysis, "scene_hint", None)
                    in SCENE_DEFAULT_ENGINE):
                try:
                    _scene_handler = getattr(
                        self, SCENE_DEFAULT_ENGINE[analysis.scene_hint])
                    if analysis.scene_hint == "career_dir":
                        _scene_reply = _scene_handler(
                            msg, user_id, stream_cb=stream_cb,
                            session_id=session_id)
                    else:
                        _scene_reply = _scene_handler(
                            msg, user_id, stream_cb=stream_cb)
                    if _scene_reply:
                        reply = _scene_reply
                except Exception:
                    pass  # 兜底异常 → 保留 _free_chat 原文
            # R1-2（T039 flaky 收口）：合婚场景 LLM 自行执行了合婚工具
            # （tool_log 有 calls，L1 已记录真实调用）但把卡片缩写成散文、
            # 丢失「男方」回显（"这两位先生的八字…生肖都是属马"实锤，2/3）
            # → 用确定性合婚卡片直接替换回复（消息拆对 → 引擎双排盘，0
            # LLM）。不走 _execute_tool_call（L1 严格序列匹配，二次执行即
            # FAIL）——直调工具执行器 _tool_hehun（未包装、不重复记录，
            # 与场景兜底同口径；无落库副作用，persons/chart_records 零写）。
            if (getattr(analysis, "scene_hint", None) == "hehun"
                    and not downgraded and reply and "男方" not in reply):
                try:
                    _pair = self._split_hehun_pair(msg)
                    if _pair and _pair[0] and _pair[1]:
                        _r = self._tool_hehun(
                            {"text": "birth_a: %s\nbirth_b: %s"
                             % (_pair[0], _pair[1])}, user_id)
                        if _r.ok and _r.text:
                            reply = _r.text
                except Exception:
                    pass  # 兜底异常 → 保留原文（不劣于现状）
            # R1-2（T007/T094 类回显防漏）：兜底后回复仍含工具说明书模板/
            # 参数 JSON（LLM 把工具调用当回复文本输出）→ 最后一次替换为
            # 确定性兜底话术，保证回复层零工具 schema/JSON 泄漏。
            if self._looks_like_tool_echo(reply):
                reply = ("抱歉，我还在学习中，这次没能准确理解你的意思。"
                         "你可以换一种说法，或直接提供出生信息让我为你排盘～")
            # E2-1 卡片化：统一出口包装（只作用于最终回复字符串；开场白/引导语
            # 留在卡片外；未命中 → 原样返回）
            reply = self._maybe_wrap_card(reply, user_id,
                                          tool_calls=(tool_log or {}).get("calls"))
            # AI 原生（Phase 2）：长期记忆 — 会话开始（非首次）注入"欢迎回来"式开场
            # （流式模式下开场已提前流出，不再拼接，避免重复）
            if stream_cb is None:
                welcome = self._get_welcome_back(user_id)
                if welcome and reply:
                    reply = f"{welcome}\n\n{reply}"
            if self.session_dao:
                self.session_dao.add_message(
                    user_id, "assistant", reply,
                    intent=analysis.intent,
                    emotion=analysis.emotion_label,
                    tool_calls=json.dumps(tool_log["calls"], ensure_ascii=False)
                    if tool_log and tool_log["calls"] else None,
                    retrieval_hit=tool_log["retrieval_hit"] if tool_log else "unused",
                    model=getattr(self.llm, 'model', '') or '',
                    safety_flag=self._safety_flag(msg),
                    temp=deep,
                    session_id=session_id,
                )
            if analysis.needs_soothe and analysis.soothe_text:
                reply = analysis.soothe_text + "\n\n" + reply
            # k41：自由聊路径同样过检索来源尾注（该搜的已搜到 → 回复体现来源）
            reply = self._apply_ground_source_trace(reply, _k11b_ground)
            # k41（②g）：歧义性别声明的询问确认同点重挂（自由聊分支不吞）
            reply = self._apply_gender_confirm(reply, user_id)
            # P2 演化链：结论摘要 append 到 topic 时间线（cap 5）
            # Task 5 deepNight：倾诉临时通道不记演化链
            if not deep:
                self._record_evolution(user_id, topic, reply)
            return reply

        # Step 2: 路由到对应处理器（批次 1：从能力注册表投影，单一事实源）
        handler_map = {c.cap_id: c.executor for c in CAPABILITIES
                       if c.cap_type == "intent" and c.executor}

        # k5 单稿流门控（2026-09-04，用户实锤一条回复显示两遍）：引擎 handler
        # 内部的草稿正文 chunk（LLM 实时流）先拦截暂存不发出——正文唯一来源 =
        # 下方润色定稿流（对齐豆包/元宝：正文只生成/流出一遍）；thinking/tool
        # 事件原样透传（引擎分析阶段前端只显示思考状态）。handler 返回后按
        # _will_polish 决定：丢弃暂存（本稿将被润色重写）或按原序 flush（本稿
        # 即最终稿：信息收集/无引用/⚠️/降级等，用户不失内容）。仅流式模式
        # （stream_cb 非 None）启用包装；非流式 /api/chat 原样直传（handler
        # 内部不产出 chunk，零影响）。
        _draft_chunks: Optional[list] = None
        handler = handler_map.get(analysis.intent)
        if handler:
            try:
                self._consume_quota(user_id)
                if stream_cb is not None:
                    _draft_chunks = []
                    _real_cb = stream_cb

                    def _gated_cb(evt_type: str, payload: dict) -> None:
                        if evt_type == "chunk":
                            # k11-B/C：草稿正文暂存前 scrub（本轮事实上下文已由
                            # _do_bazi_analysis 在排盘后设置）——后续 flush（草稿
                            # 即最终稿分支）与 engine_draft（润色输入）都基于净文本
                            _p = payload
                            if isinstance(payload, dict):
                                raw = (payload.get("text") if "text" in payload
                                       else payload.get("content", ""))
                                if isinstance(raw, str) and raw:
                                    _clean = self._scrub_turn_text(raw, user_id)
                                    if _clean != raw:
                                        _p = dict(payload)
                                        for _k in ("text", "content"):
                                            if _k in _p:
                                                _p[_k] = _clean
                            _draft_chunks.append(_p)  # 草稿正文：暂存
                            return
                        _real_cb(evt_type, payload)  # thinking/tool：透传
                else:
                    _gated_cb = stream_cb
                # 会话隔离：解梦需读会话历史（P0-1 已有梦境免重复描述），
                # 仅 dream 处理器感知 session_id；其余引擎处理器不读历史
                if analysis.intent in ("dream", "bazi", "career"):
                    reply = handler(msg, user_id, stream_cb=_gated_cb,
                                    session_id=session_id)
                elif analysis.intent == "advisor":
                    # k41：advisor 入口注入已检索结果（与关键词早退分支同一实现；
                    # 改前 advisor 被 `_will_polish` 排除 → 检索块从不到达 LLM）
                    reply = handler(msg, user_id, stream_cb=_gated_cb,
                                    ground_hint=_ground_hint)
                else:
                    reply = handler(msg, user_id, stream_cb=_gated_cb)
            except Exception as e:
                reply = f"⚠️ 服务暂时不可用：{str(e)[:100]}\n\n请稍后再试或换一种命理方式。"
        else:
            reply = f"🔧 {analysis.intent} 模块暂未开放，试试：\n• 八字命理\n• 紫微斗数\n• 易经占卜\n• 风水分析"

        # 方案 B·引擎结果注入（AI 原生统一）：有意图的问题也走 LLM 自主二次生成。
        # _handle_* 完成真实分析（注册了 engine/book 引用）时，把引擎结果注入
        # system → LLM 以豆包式语气生成最终回复（可再输出 <tool_call> 补检索）；
        # 信息收集/错误回复不注册引用 → 不润色，保持原样。
        # xuetang/advisor/confidant 为独立对话模式（早退分支），保持现状。
        # L5-2 修复（降级成本）：降级时禁用润色 LLM 调用（_do_bazi_lite 的
        # 精简文案已自成一体，无需二次生成）。
        # D2 修复（数据正确性）：engine_draft 记录润色前的引擎原稿
        # （四柱与已存盘一致，作为四柱终审冲突时的兜底回退稿）。
        # R1-1（T096 跨用户污染）：本人运势问句 + 会话历史含第三方排盘 →
        # 跳过润色，保留确定性引擎原稿。润色 LLM 读历史时会把朋友的出生
        # 信息当成当前用户的（在线复现实锤：轮2「我的运势怎么样」被润色成
        # 「根据你1976年5月13日在上海出生的命盘…」；D2 因对手文案干支声明
        # 不足 4 个早退、且 chart_records 最新恰为朋友盘，无法兜底）。
        engine_draft = None
        _r1_self_fortune_turn = (
            not self._is_third_party_birth_request(msg)
            and bool(_SELF_FORTUNE_RE.search(msg)))
        # k5 单稿流：will_polish 在 handler 返回后即刻原样求值（条件与短路
        # 顺序同下方原块，只求一次、不重复求值）——True → 本稿将走润色
        # 重写，暂存的草稿 chunk 丢弃（正文以定稿流为唯一来源）；False →
        # 本稿即最终稿，按原序 flush 暂存 chunk 给真 stream_cb（用户看到
        # 的正文与落库一致）。
        _will_polish = (analysis.intent not in ("xuetang", "advisor")
                        and not downgraded
                        and reply and not reply.startswith("⚠️")
                        # k41：判据排除自动检索的 web 引用（与改前在检索前求值
                        # 逐字等价；见 `_has_engine_citations`）
                        and self._has_engine_citations(user_id)
                        and not (_r1_self_fortune_turn
                                 and self._history_has_third_party_birth(
                                     user_id, session_id)))
        if _draft_chunks:
            if _will_polish:
                _draft_chunks.clear()  # 草稿丢弃：正文只流一遍（润色定稿）
            else:
                for _payload in _draft_chunks:  # 按原序 flush（草稿即最终稿）
                    stream_cb("chunk", _payload)
        if _will_polish:
            engine_draft = reply
            # k11b（search-trigger）：命理意图域主链遇到外部实体/时效事实问题
            # （「易宝支付这家公司怎么样」）→ 在引擎草稿之外确定性自动联网检索
            # 一次，结果块注入润色上下文（引用已注册本轮 citations，LLM 标 [n]）；
            # 触发判定=实体层/时效层/白名单 − 金融排除 − 命理本地锚（decide_search，
            # 复用分析器 needs_search LLM 信号为 OR，无新增二判 LLM——取舍见 plan）。
            # 已自动检索 → 压制旧 search_hint 的「先输出 web_search 工单」硬性
            # 要求（结果已在上下文；LLM 若再发同 query 工单由 _tool_web_search
            # 复用表去重，不二次真实检索）。
            # k41：检索本身已上移到 process 的**路由前接缝**（`_ground_search_for_turn`）
            # ——本块只消费结果（`_k11b_ground`），不再自带调用点（advisor 路径
            # 曾被本块的条件排除 → 检索没发生，T104 实锤）。
            _extra_parts = [h for h in (topic_hint,
                                        ("" if _k11b_ground else analysis_hint))]
            _extra_parts.extend(self._ground_hint_parts(_k11b_ground))
            try:
                reply = self._polish_with_engine_draft(
                    msg, user_id, reply, stream_cb,
                    extra_hint="\n".join(_extra_parts),
                    search_hint=("" if _k11b_ground else analysis_hint),
                    session_id=session_id)
            except Exception:
                pass  # 润色异常 → 保留引擎原稿（静默降级，行为不劣于现状）

        # AI 原生（Phase 1）：<tool_call> 工具调用循环
        # R1-2（T092）：bazi 路由链路本轮已排盘（engine_draft 非空）→ 传
        # chart_draft 供 _run_tool_loop 丢弃冗余「排盘」工单（no_tool 契约
        # 下重复执行被 L1 判 FAIL）；其余意图（dream/zeri 等）无排盘产物，
        # 不传（LLM 合法发起的排盘工单照常执行）。
        reply = self._run_tool_loop(
            msg, user_id, reply, stream_cb=stream_cb, analysis=analysis,
            session_id=session_id,
            chart_draft=engine_draft if analysis.intent == "bazi" else None)

        # D2 修复（数据正确性·四柱终审）：润色/工具循环的 LLM 可能改写干支
        # （QA 实测：已存盘 庚午 辛巳 乙酉 甲申 被答成 庚午 甲申 乙丑 丙子），
        # 终审比对已存盘，冲突即回退引擎原稿；无原稿可回退时不写缓存，
        # 防止错误结果被 0.78s 缓存固化。
        reply, pillar_conflict = self._enforce_pillar_integrity(
            reply, engine_draft, user_id)
        # R1-2（评测 T007 修复·工具回显泄漏）：润色/工具循环的 LLM 把工具
        # 说明书模板或参数 JSON 原样回显为回复文本（"排盘（bazi_chart）\n
        # {\"birth\": …}"，T007 3/3 实锤）→ 回退确定性引擎原稿（engine_draft
        # 为引擎产物：完整四柱/大运分析，无模板/JSON；四柱终审已保证干支正确）。
        if self._looks_like_tool_echo(reply) and engine_draft:
            reply = engine_draft
        # R1-2（评测 T092 回归修复·零干支散文兜底）：润色 LLM 把引擎原稿
        # 缩写成零干支/残缺干支散文（"哦。如果你有任何其他问题或需要进一步
        # 的分析，请随时告诉我。" 与「全回复仅一处相邻干支（甲申）」实锤——
        # _enforce_pillar_integrity 的 ≥4 干支断言早退，不触发兜底）→ bazi
        # 意图 + 有引擎原稿 + 回复声明不足四柱（<4 个相邻干支对：表格式
        # 干支拆行（庚/午分列）时大运/流年行偶见 1-3 对，仍属残缺）→ 回退
        # 确定性引擎原稿（四柱/大运齐全，与 D2 同一条"正确性优先于润色
        # 语气"原则；其余意图的合法零干支回复不受影响）。
        if (analysis.intent == "bazi" and engine_draft
                and len(self._GANZHI_RE.findall(reply)) < 4):
            reply = engine_draft
        # R1-2（评测 T008 修复·纠正回执防丢）：性别纠正固定回执（"已按您
        # 本次说的「女」重新排盘（大运方向已随之调整）"）必须存活于最终
        # 回复——润色 LLM 会把回执缩写成无性别词散文（T008 2/3 实锤）→
        # 润色后幂等重挂：回执不在回复中则前置（确定性文案，零 LLM）。
        _ack = (getattr(self, "_gender_acks", None) or {}).pop(user_id, "")
        if _ack and _ack not in reply:
            reply = _ack + "\n\n" + reply
        # k40（T034 修复·确定性宜忌行防丢）：择日意图路径的宜忌行由
        # `_do_zeri_analysis` 落进草稿（与工具路径同一渲染实现），但润色/工具
        # 循环的 LLM 仍可能整行吃掉（"可行的哦"零宜忌字面实锤）→ 逐行幂等
        # 重挂：缺哪行补哪行（已逐字保留的行不重复，不产生重复条目）。
        _zj = (getattr(self, "_zeri_yi_ji_acks", None) or {}).pop(user_id, None)
        if _zj and analysis.intent == "zeri":
            _missing = [ln for ln in _zj if ln and ln not in reply]
            if _missing:
                reply = reply.rstrip() + "\n\n" + "\n".join(_missing)
        # R1-3（评测 T009 修复·性别回显重挂）：润色/工具循环的 LLM 把排盘
        # 回复写成无性别散文（女档案契约 contains「女」1/3 实锤：纯散文
        # 只字未提女命）→ 确定性重挂性别声明（0 LLM，见 _rehang_gender_echo）。
        reply = self._rehang_gender_echo(reply, analysis, engine_draft, user_id)
        # R1-2（T007 终局防漏·回复层兜底脱括号）：bazi 链路存在三个 LLM
        # 环节（analyze / 润色 / 工具循环二次生成），任一个把五行等数据
        # 重述成 dict 字面量（{'金': 3, …} 在线实锤）都会直接污染最终回复
        # ——即便 _format_chart 已不再向 LLM 展示 dict 形态。回复层确定性
        # 兜底：dict 字面量跨段（{...}）一律脱括号去引号（{'金': 3} → 金: 3），
        # 零 LLM、只作用于 dict 形态文本，不伤正文；评测 neg "{" 契约
        # 3/3 确定性满足。
        if analysis.intent == "bazi" and "{" in reply:
            reply = re.sub(
                r"\{[^{}]*\}",
                lambda m: m.group(0).replace("{", "").replace("}", "")
                .replace("'", "").replace('"', ""),
                reply)
        # k11-B/C（输出后校验器·引擎意图出口）：润色/工具循环把引擎稿重写后，
        # 称谓与神煞白名单可能被再次破坏——整段再 scrub 一道（纯规则，无上下文
        # 的意图不 scrub；已含本轮回执"女命/男命"标注不受影响——非女性称谓词）。
        if analysis.intent in ("bazi", "career"):
            try:
                _s = self._scrub_turn_text(reply, user_id)
                if _s:
                    reply = _s
            except Exception:
                pass
        # k11b：实体 QA 已自动联网检索（有结果）→ 回复必须带来源痕迹（禁裸
        # JSON 禁泄漏——来源尾注为确定性纯文本，已走 k11 scrub/stream-guard
        # 出口）；LLM 漏写来源且回复未含检索站点痕迹时确定性补尾注（回复须含
        # 实体名才补，防无关尾注；反馈提示「准/不准」行保留在尾注之后不被挤开）。
        # k41：实现收敛到 `_apply_ground_source_trace`（所有回复路径出口共用同一份）。
        reply = self._apply_ground_source_trace(reply, _k11b_ground)
        # k41（②g）：歧义性别声明的询问确认（主链出口重挂）
        reply = self._apply_gender_confirm(reply, user_id)
        # 阶段 2：本轮工具调用日志 → 落库字段
        tool_log = self._pop_tool_log(user_id)
        # E2-1 卡片化：统一出口包装（落库/缓存/返回值一致携带卡片标记；
        # 开场白/引导语留在卡片外；未命中 → 原样返回）
        reply = self._maybe_wrap_card(reply, user_id,
                                      tool_calls=(tool_log or {}).get("calls"))
        # AI 原生（Phase 2）：长期记忆 — 会话开始（非首次）注入"欢迎回来"式开场
        # （流式模式下开场已提前流出，不再拼接，避免重复）
        if stream_cb is None:
            welcome = self._get_welcome_back(user_id)
            if welcome and reply:
                reply = f"{welcome}\n\n{reply}"

        if self.session_dao:
            self.session_dao.add_message(
                user_id, "assistant", reply, intent=analysis.intent,
                emotion=analysis.emotion_label,
                tool_calls=json.dumps(tool_log["calls"], ensure_ascii=False)
                if tool_log and tool_log["calls"] else None,
                retrieval_hit=tool_log["retrieval_hit"] if tool_log else "unused",
                model=getattr(self.llm, 'model', '') or '',
                safety_flag=self._safety_flag(msg),
                temp=deep,
                session_id=session_id,
            )

        if analysis.needs_soothe and analysis.soothe_text:
            reply = analysis.soothe_text + "\n\n" + reply

        # P2 演化链：结论摘要 append 到 topic 时间线（cap 5）
        # Task 5 deepNight：倾诉临时通道不记演化链
        if not deep:
            self._record_evolution(user_id, topic, reply)

        # D2: Cache the response for high-frequency queries
        # 终审：deepNight 不写缓存（见 Step -2 注释，夜里回复只属于当晚）
        # 会话隔离：缓存键掺入 session_id，与 Step -2 读取同口径
        # D2 修复：四柱终审冲突且无回退稿的错误结果禁止入缓存
        # （0.78s 缓存固化错误盘面 → 用户长期看到错误盘，红线级）。
        # R1-2（T089）：缓存键掺入档案指纹，与 Step -2 读取同口径
        # （_cache_fp 已在 Step -2 的 is_cacheable 分支内计算，此条件严格
        # 是其子集——is_cacheable(msg) and not deep 相同，恒已定义）。
        if is_cacheable(msg) and not deep and not pillar_conflict:
            try:
                self.cache.set(f"{msg}::fp:{_cache_fp}", reply, user_id,
                               scope=session_id or "")
            except Exception:
                # A3-2（批次 2 审查结论）：写缓存是旁路优化——失败仅放弃本次
                # 缓存（下次仍走慢通道重算），已算好的回复照常返回；
                # 原实现异常冒泡会让用户拿不到已算好的回复。
                logger.warning("出口缓存写入失败（放弃缓存，回复照常返回）user=%s",
                               user_id, exc_info=True)

        return reply

    # ============================================================
    # Voice input support
    # ============================================================

    def _handle_voice(self, voice_text: str = "", downgraded: bool = False,
                      deep_night: bool = False) -> str:
        """处理语音输入。

        如果 CoW（Claude on WeChat）提供了语音→文字转写，
        则直接通过正常意图检测流程处理。
        如果没有转写文本，说明需要 CoW 语音插件支持。
        downgraded（L5-1）：对话额度用尽 → 降级链路（精简回复）。

        deep_night（k39 审查 C2 同类修复）：语音轮是"转写文本轮"，请求里的
        深夜标记必须与文本轮「同源同判」地传给 `process()` ——改前该标记同样
        被丢弃（端点没传、本方法也没透传）→ 深夜语音轮以 temp=0 落库并成为
        L2 压缩输入，与图片轮同一个缺陷类。**user_id 保持既有 `""` 形态不动**
        （语音轮归属/上下文口径属既有独立缺口，改它会连带改变语音轮的
        档案上下文与记忆语义，超出本次红线修复范围，已在报告列为待拍板项）。
        """
        if voice_text:
            return self.process(voice_text, "", deep_night=deep_night,
                                downgraded=downgraded)

        return "🎤 语音处理需要 CoW 语音插件支持。如果您正在使用微信，" \
               "请确保已安装 CoW 语音转文字插件。"

    # ============================================================
    # Image input support
    # ============================================================

    def _persist_image_turn(self, user_id: str, image_url: str,
                            user_text: str, reply: str,
                            deep_night: bool = False) -> None:
        """k39 S4：图片轮次落库（user 轮含图片 URL + assistant 轮）。

        为什么必须落库（改前 → 改后）：
        - 改前：图片轮**完全不落库**（`/api/chat` 的 image 分支绕开
          `process()`，而 `process()` 是全产品唯一的落库入口）→ `sessions`
          里没有任何上传 URL → `_referenced_upload_names`（k33 引用反查）
          永远查不到引用 → 72h TTL 一到，**每个**上传都算孤儿被删 →
          客户端本地历史里的图裂。
        - 改后：user 轮 content 里带原样上传 URL（清理反查按
          `/api/chat/uploads/<name>` 匹配），assistant 轮照常落库 →
          被引用的图超 TTL 不删；历史（本地 `ylm_chat_messages`/服务端
          sessions）都能按 URL 渲染。

        隐私口径**不放宽**（与文本链路同规则，逐项对齐 `process()`）：
        - 倾诉/深夜（deep-night）→ `temp=1`（24h 硬清理，同 `cleanup_temp`）；
          **判据是本轮请求的 `deep_night` 入参，不是进程内 `_deep_night` 字典**
          （k39 审查 C2：该字典只在 `process()` 文本轮里被赋值，图片轮直接读它
          必然取到**上一文本轮**的陈旧值——深夜「首条即发图」/直调 API 的图片轮
          会以 `temp=0` 落库并进入 L2 压缩输入，打破「夜间倾诉不进 L2」红线）；
        - **本轮不写记忆，但落下去的 `temp=0` 行会成为 L2 压缩的输入**
          （`_maybe_compact` 预检 `AND temp=0` + `get_history(temp=False)`）→
          所以"深夜图片轮不进 L2"完全依赖上面那行 `temp` 标记；
          `temp=1` 才真正把它挡在 L2 之外。
          本方法自身仍只写 `sessions`，绝不碰 UserMemory/compactor/演化链；
        - `user_id` 为空（无身份）→ 不落库（不建匿名共享身份）；
        - 落库失败只告警，绝不影响回复（优雅降级）。
        """
        if not user_id or not image_url:
            return
        dao = getattr(self, "session_dao", None)
        if dao is None:
            return
        try:
            deep = bool(deep_night)
            # 占位词「（图片）」与客户端 streamHost.sendImage 的 content 同口径；
            # URL 原样拼接（清理反查要求完整 /api/chat/uploads/<name>）
            content = f"（图片）{image_url}"
            if user_text:
                content += f"\n{user_text}"
            dao.add_message(user_id, "user", content, intent="image", temp=deep)
            if reply:
                dao.add_message(user_id, "assistant", reply, intent="image",
                                temp=deep)
        except Exception:  # noqa: BLE001 — 落库失败不影响回复
            logger.warning("图片轮次落库失败（不影响回复）user=%s", user_id,
                           exc_info=True)

    def _handle_image(self, image_url: str = "", user_text: str = "",
                      downgraded: bool = False, user_id: str = "",
                      deep_night: bool = False) -> str:
        """处理图片输入 — 支持面相分析 + 风水 + 通用（k39 S4：轮次落库）。

        `user_id`（k39 S4 新增，末位带默认值 → 既有 3 参调用不受影响）：
        图片轮次按 `_persist_image_turn` 落库（历史可渲染 + 清理能识别引用）；
        空 user_id（无身份调用方）保持改前行为——不落任何用户数据。

        `deep_night`（k39 审查 C2 新增）：本轮的深夜/倾诉标记，**由请求透传**
        （`req.deep_night`，与文本轮 `process(deep_night=...)` 同源同判），
        只用于落库时的 `temp` 判定 → 深夜图片轮 temp=1（24h 硬清理）且不进 L2。
        **刻意不写进 `self._deep_night` 字典**：那个字典是进程内跨轮状态，
        图片轮写它只会把上一轮的值留给下一轮（正是 C2 的成因）；本路径全部
        走显式入参，不读也不写该字典。
        """
        reply = self._handle_image_inner(image_url, user_text, downgraded)
        self._persist_image_turn(user_id, image_url, user_text, reply,
                                 deep_night=deep_night)
        return reply

    def _handle_image_inner(self, image_url: str = "", user_text: str = "",
                            downgraded: bool = False) -> str:
        """图片输入的处理本体（零落库；落库统一由 `_handle_image` 收口）。

        优先尝试 CV 面相分析（如果人脸检测成功），否则根据关键词路由。
        downgraded（L5-2 I-3）：降级时 CV 本地测量照跑，但跳过付费 DeepSeek
        报告生成（api_key 置空走本地规则文案），并附精简提示。
        """
        if not image_url:
            return "📷 请提供图片链接以便进行分析。"

        # k33/A23：SSRF 白名单（本服务自有域名 + /api/chat/uploads/ 路径，或显式
        # 配置的自有 CDN/预览主机）——非白名单 URL 一律不下载、不回显，直接引导
        # 重新上传。收口点在下载之前（下载点另有两道同源判定 + safe_urlretrieve
        # 重定向复检，防未来新调用方绕过）。
        from src.bot.image_url_guard import is_allowed_image_url, reject_reason
        if not is_allowed_image_url(image_url):
            logger.warning("图片 URL 不在白名单（拒绝下载）: %s", reject_reason(image_url))
            return "📷 这张图片的地址已失效，请在聊天框重新上传一次图片。"

        # Try face reading first
        face_result = self._try_face_reading(image_url, user_text,
                                             downgraded=downgraded)
        if face_result:
            return face_result

        # Try palm reading
        palm_result = self._try_palm_reading(image_url, user_text,
                                             downgraded=downgraded)
        if palm_result:
            return palm_result

        # Fall back to keyword-based routing
        if any(kw in user_text for kw in ["户型", "风水", "家居", "布局", "房间"]):
            return self._handle_image_fengshui(image_url, user_text)
        elif any(kw in user_text for kw in ["手相", "看相", "手掌"]):
            return self._handle_image_mianxiang(image_url, user_text)
        else:
            return self._handle_image_generic(image_url, user_text)

    def _try_palm_reading(self, image_url: str, user_text: str = "",
                          downgraded: bool = False) -> str:
        """Try CV palm analysis on an image. Returns result or None.

        downgraded（L5-2 I-3）：跳过付费 DeepSeek 报告（api_key 置空 → 本地
        规则文案），附精简提示。
        """
        try:
            import tempfile, os
            # k33/A23：下载点白名单判定（与 _handle_image 同源，防新调用方绕过）；
            # 下载走 safe_urlretrieve（重定向目标复检，k33 审查 I2）
            from src.bot.image_url_guard import is_allowed_image_url, safe_urlretrieve
            if not is_allowed_image_url(image_url):
                return None
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                safe_urlretrieve(image_url, tmp.name)
                tmp_path = tmp.name
            from src.engines.palm_reader import PalmReader, generate_palm_report
            reader = PalmReader()
            metrics = reader.analyze(tmp_path)
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            if metrics is None:
                return None
            api_key = "" if downgraded else (getattr(self.llm, 'api_key', '') if self.llm else '')
            report = generate_palm_report(metrics, retriever=None if downgraded else self.retriever,
                                          api_key=api_key)
            if downgraded:
                report = f"{report}\n\n💡 今日额度已用尽，已为你精简回复；开通会员解锁完整解读。"
            return report
        except Exception:
            return None

    def _try_face_reading(self, image_url: str, user_text: str = "",
                          downgraded: bool = False) -> str:
        """Try CV face analysis on an image. Returns result or None if no face.

        downgraded（L5-2 I-3）：CV 本地测量照跑（零成本），跳过付费 DeepSeek
        报告（api_key 置空 → generate_report 走本地测量/优势关注文案），附精简提示。
        """
        try:
            import tempfile, os
            # k33/A23：下载点白名单判定（与 _handle_image 同源，防新调用方绕过）；
            # 下载走 safe_urlretrieve（重定向目标复检，k33 审查 I2）
            from src.bot.image_url_guard import is_allowed_image_url, safe_urlretrieve
            if not is_allowed_image_url(image_url):
                return None
            # Download image to temp file
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                safe_urlretrieve(image_url, tmp.name)
                tmp_path = tmp.name

            from src.engines.face_reader import FaceReader, generate_report
            reader = FaceReader()
            metrics = reader.analyze(tmp_path)

            try:
                os.unlink(tmp_path)
            except Exception:
                pass

            if metrics is None:
                return None  # No face detected, let other handlers try

            # Generate report
            api_key = "" if downgraded else (getattr(self.llm, 'api_key', '') if self.llm else '')
            report = generate_report(
                metrics,
                retriever=None if downgraded else (self.retriever if hasattr(self, 'retriever') else None),
                api_key=api_key,
            )
            if downgraded:
                report = f"{report}\n\n💡 今日额度已用尽，已为你精简回复；开通会员解锁完整解读。"
            return report
        except Exception:
            return None  # Face analysis failed, fall back gracefully

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
            ("嫁娶", ["结婚", "婚礼", "嫁娶", "订婚", "婚"]),
            ("开业", ["开业", "开张", "开市", "开工"]),
            ("搬家", ["搬家", "入宅", "乔迁", "迁居"]),
            ("出行", ["出行", "旅游", "旅行", "出差"]),
            ("提车", ["提车", "买车", "购车"]),
            ("签约", ["签约", "签合同", "过户"]),
            ("晋升", ["晋升", "升职", "加薪", "升迁", "竞聘", "述职", "入职", "求职", "面试", "谈薪", "升官"]),
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

    def _handle_career(self, msg: str, user_id: str,
                       stream_cb: Optional[Callable] = None,
                       session_id: Optional[str] = None) -> str:
        """处理事业适配请求（career 意图）：排盘 + LLM 自主分析十神/五行与行业适配。

        与 _handle_bazi 同一分析管线；消息命中 career 场景关键词时，
        _route_by_scenario 自动注入【场景聚焦：事业分析】提示
        （行业五行适配/跳槽转型节点/职场贵人运）。
        不注入名人相似对照（名人库停用方案见 similarity 移除清单，未确认前不落笔）。
        """
        return self._handle_bazi(msg, user_id, stream_cb=stream_cb,
                                 session_id=session_id)

    def _get_user_birth_profile(self, user_id: str,
                                allow_chart_fallback: bool = True) -> Optional[dict]:
        """获取用户出生信息档案（G1 P0-B 修复：persons 默认档案 = 单一事实源）。

        G3c：委托 src.storage.birth_profile.get_user_birth_profile ——
        calendar 今日运势（api/calendar.py）与对话路径共用同一实现，
        读取顺序零漂移（数据一致性铁律），缓存指纹与读取源同源。

        allow_chart_fallback（k18 透传，默认 True）：档案「存在性」类判断
        （_should_fastpath RAG 快路径门控）传 False——只认 persons/bazi_info
        真实档案，chart_records 兜底可能只是他人/择时盘（k9 契约）。
        """
        return get_user_birth_profile(
            self.dao, user_id, chart_dao=getattr(self, "chart_dao", None),
            allow_chart_fallback=allow_chart_fallback)

    def _profile_cache_fingerprint(self, user_id: str) -> str:
        """今日运势类聊天缓存键的档案指纹（G3b H-7 同口径，R1-2 T089）。

        建档/改八字/改城市 → 指纹变化 → 缓存键变化 → 当日立即重算，不命中
        旧缓存；与 src/api/calendar.py 的 calendar:today 缓存键指纹同源
        （birth_profile.profile_fingerprint 单一实现，数据一致性铁律）。
        读取异常 → "none"（与无档案同键，行为退化为旧版不掺指纹）。
        """
        try:
            return profile_fingerprint(self._get_user_birth_profile(user_id))
        except Exception:
            return "none"

    # ── T5 重看盘直读（0 引擎 0 LLM）─────────────────────────────
    # 用户问"我的盘/我的八字"等重看表述、且 chart_records 已有排盘结果时，
    # 直接读库秒回——引擎绝不计算、LLM 绝不调用；未命中返回 None 走全流程。
    REUSE_KEYWORDS = ("我的盘", "我的八字", "上次的盘", "我的命盘", "重新看")

    def _try_reuse_chart(self, user_id: str, msg: str) -> str | None:
        """重看盘直读：问'我的盘/我的八字'等且有已存结果 → 0 引擎 0 LLM 秒回。"""
        if not msg or not any(kw in msg for kw in self.REUSE_KEYWORDS):
            return None
        if self._route_by_scenario(msg, user_id):
            return None  # 场景问句（事业/财运/合婚等）走全流程，不被直读劫持
        if ("排" in msg or "算" in msg) and ("重新" in msg or "再" in msg or "一次" in msg):
            return None  # 明确要重新排/算 → 走全流程
        # D5 修复（意图识别过宽，QA EXT-005）：去掉重看关键词后剩余文本必须
        # 为空或纯语气词，否则是"带着盘问具体问题"（如"结合我的八字，看看我
        # 今年秋天的运势要点"），不得秒回盘面复述模板，交全流程分析。
        rest = msg
        for kw in self.REUSE_KEYWORDS:
            rest = rest.replace(kw, "")
        if re.sub(r'[\s，。？！!?、,.；;:：的了啊吧呢吗嘛这啥什么帮我看看是一下重新]',
                  '', rest):
            return None
        try:
            chart = getattr(self, "chart_dao", None) and self.chart_dao.get_latest_chart(user_id)
            if not chart:
                return None
            r = chart["bazi_json"]
            bazi = r.get("bazi") or []
            lines = [f"这是你最近排过的盘（{chart['created_at']}）："]
            if bazi:
                stems = ["年柱", "月柱", "日柱", "时柱"]
                lines += [f"{stems[i]}：{g}" for i, g in enumerate(bazi[:4])]
            lines.append(f"日主：{r.get('day_master','')} · 格局：{r.get('geju','') or '—'}")
            if r.get("dayun"):
                lines.append("大运：" + " → ".join(f"{a}岁{ganzhi}" for a, ganzhi in r["dayun"][:6]))
            if r.get("liunian"):
                lines.append("流年：" + "、".join(f"{y}年{g}" for y, g in list(r["liunian"].items())[:5]))
            if r.get("shensha"):
                lines.append("神煞：" + "、".join(r["shensha"][:8]))
            lines.append("（直接看的已存结果；要重新详细分析就说'重新帮我分析'）")
            # E2-1 卡片化：重看盘直读 → 存量档案直读（data 卡片判定依据）
            self._mark_card_turn(user_id, data_read=True)
            return "\n".join(lines)
        except Exception as e:
            # A3-1（批次 2 审查结论）：T5 直读 fail-open 语义覆盖整函数——
            # 原实现只保护 get_latest_chart 调用，字段解析/组装（缺键/脏数据）
            # 异常会冒泡丢整条回复；现统一回落 None 走全流程，不阻塞 process 入口。
            # A3 Minor ①（B1-11）：fail-open 兜底留观测日志（行为不变，仅加可观测性）
            logger.warning("重看盘直读异常，回落全流程: user=%s err=%s",
                           user_id, str(e)[:200])
            return None

    # ── D2 四柱终审（数据正确性防线，QA AC-CHAT-009）──────────────
    # 润色/工具循环的 LLM 曾把已存盘 庚午 辛巳 乙酉 甲申 改写为
    # 庚午 甲申 乙丑 丙子 并被 0.78s 缓存固化。终审在 process() 收尾处
    # 比对回复中按序断言的四柱与已存盘，冲突即回退引擎原稿/禁入缓存。
    _GANZHI_RE = re.compile(r'[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]')

    def _latest_bazi(self, user_id: str) -> list:
        """已存盘四柱（供 D2 终审比对）；无盘/异常返回空列表。"""
        try:
            chart = getattr(self, "chart_dao", None) and self.chart_dao.get_latest_chart(user_id)
            if chart:
                return (chart.get("bazi_json") or {}).get("bazi") or []
        except Exception:
            pass
        return []

    def _pillar_claims_conflict(self, reply: str, chart_bazi: list) -> bool:
        """回复中断言了完整四柱、且与已存盘不一致 → 冲突。

        - 回复不足 4 个干支（非排盘类回复）无从断言四柱 → 不判冲突；
        - k7d（2026-09-05，D2 误判实证）：原实现取「文本序前 4 个干支」
          假设「正文先讲四柱、大运/流年在后」——polish 稿结构把
          「大运：3岁戊辰,13岁丁卯…」摘要行放在【分析解读】四柱声明行
          （己卯年、己巳月、乙丑日、辛巳时）之前 → 前 4 = 大运四连
          （[戊辰,丁卯,丙寅,乙丑]）≠ 存档 → 系统性误判冲突 → 回退引擎稿
          → 已流的 polish 稿与整卡异文 = 18:47 双稿形态。改为：回复全文
          存在「与存档同序同值的完整四柱四连组」（任意位置）→ 声明正确
          不冲突；断言 ≥4 个干支却无存档序四连 → LLM 写了别家的四柱，
          仍判冲突（原 D2 防护语义不变）。
        """
        if not reply or not chart_bazi or len(chart_bazi) < 4:
            return False
        found = self._GANZHI_RE.findall(reply)
        if len(found) < 4:
            return False
        want = list(chart_bazi[:4])
        for i in range(len(found) - 3):
            if list(found[i:i + 4]) == want:
                return False  # 已断言与存档同序同值四柱 → 声明正确
        return True

    def _liunian_claims_conflict(self, reply: str, user_id: str) -> bool:
        """回复中断言了带明确年份（今年/YYYY年）的流年干支且与已存盘
        不一致 → 冲突。

        D2 延伸（QA 复测观察）：润色 LLM 曾把 2026 年说成「丙子年」（已存盘
        2026=丙午）——四柱之外的流年数据同样不得被改写。只校验带明确年份
        的干支断言；无断言/年份不在已存流年表/无法比对的 → 一律放行
        （fail-open，绝不误伤）。
        """
        if not reply or not re.search(r'(?:今年|\d{4}\s*年)', reply):
            return False
        try:
            chart = getattr(self, "chart_dao", None) and self.chart_dao.get_latest_chart(user_id)
            if not chart:
                return False
            liunian = (chart.get("bazi_json") or {}).get("liunian") or {}
        except Exception:
            return False
        if not liunian:
            return False
        import datetime
        year_now = datetime.datetime.now().year
        for m in re.finditer(
                r'(?:今年|(\d{4})\s*年)[^，。！？!?；;]{0,6}?'
                r'([甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥])年',
                reply):
            year = int(m.group(1)) if m.group(1) else year_now
            stored = liunian.get(str(year))
            if stored and stored != m.group(2):
                return True
        return False

    def _enforce_pillar_integrity(self, reply: str, engine_draft: str | None,
                                  user_id: str) -> tuple:
        """D2 数据一致性终审执行器：返回 (最终回复, 是否禁入缓存)。

        - 回复断言了 ≥4 个干支且与已存盘四柱顺序不一致 → 数据错误；
        - 回复断言了年份流年干支且与已存盘不一致（如 2026=丙子 实为 丙午）
          → 数据错误；
        - 有引擎原稿（数据与已存盘一致）→ 回退原稿（正确性优先于润色语气），
          回退后内容一致 → 可正常入缓存（顺带把错误缓存路径换成正确值）；
        - 无原稿可回退 → 保留回复但标记禁入缓存（不固化错误）。
        """
        if not reply or len(self._GANZHI_RE.findall(reply)) < 4:
            return reply, False
        bazi_stored = self._latest_bazi(user_id)
        pillar_conflict = bool(bazi_stored) and self._pillar_claims_conflict(
            reply, bazi_stored)
        liunian_conflict = self._liunian_claims_conflict(reply, user_id)
        if not (pillar_conflict or liunian_conflict):
            return reply, False
        if engine_draft:
            logger.warning("D2 数据冲突回退引擎原稿 user=%s "
                           "四柱冲突=%s 流年冲突=%s 存档=%s",
                           user_id, pillar_conflict, liunian_conflict, bazi_stored)
            return engine_draft, False
        logger.warning("D2 数据冲突且无引擎原稿可回退 user=%s "
                       "四柱冲突=%s 流年冲突=%s 本次不写缓存",
                       user_id, pillar_conflict, liunian_conflict)
        return reply, True

    def _handle_bazi(self, msg: str, user_id: str,
                     stream_cb: Optional[Callable] = None,
                     session_id: Optional[str] = None) -> str:
        """处理八字请求"""
        # T5 重看盘直读兜底：任何直达 bazi 处理器的路径（含旧版直调入口）
        # 同样命中即短路，绝不重跑引擎/LLM
        reuse_text = self._try_reuse_chart(user_id, msg)
        if reuse_text:
            return reuse_text
        # R1-1（评测 T096 修复·跨用户污染·持久化半环闭环）：subject 由消息
        # 确定性判定（B3-1 规则2 消息级检测），不依赖 LLM analyzer 的 facts
        # ——真实 analyzer 对「帮我朋友排个盘…」返回 facts={} → 旧逻辑 subject
        # 默认 self → _sync_person_profile 把默认命主覆盖成朋友盘（1990→1976
        # 实锤，跨用户污染在线根因）；轮2「我的运势怎么样」analyzer 又可能回
        # facts.subject=other（旧信息冒充本人）。统一在入口按消息强制判定：
        # 他人信息只作排盘展示，绝不写入当前用户档案（产品裁决）。
        # k40 返工（Critical-1）：归属判定补「主语感知」证据——出生信息由
        # 第三人领属语引出（「我朋友1990年5月20日出生的女生」）时同样按他人
        # 处理（规则2 同款后果：只作排盘展示，不写本人档案/persons 性别）。
        # 改前此类消息 subject=self → `_sync_person_profile` 把本人档案性别
        # 写成消息里的性别（P0 同类）。
        _f = dict(self._analysis_facts.get(user_id) or {})
        _f["subject"] = ("other"
                         if (self._is_third_party_birth_request(msg)
                             or self._gender_ref_is_third_party(None, msg))
                         else "self")
        # R1-1（T096 终局）：subject 强制 self 时顺带清除 analyzer 从会话
        # 上下文捎带来的他人出生身份字段（在线复现实锤：轮2「我的运势怎么
        # 样」analyzer 回 facts.subject=other + birth_date='1976年5月13日'
        # + birth_time + birth_place + gender='女'）。他人信息只作排盘展示，
        # 不进入当前用户的任何数据路径（排盘/落库/建档读取均用档案或消息
        # 解析值，这些残留键是纯污染源——无代码读 facts.gender，清除零影响）。
        if _f["subject"] == "self":
            for _k in ("birth_date", "birth_time", "birth_place",
                       "birth_year", "birth_month", "birth_day",
                       "birth_hour", "birth_minute", "gender", "age"):
                _f.pop(_k, None)
        self._analysis_facts[user_id] = _f
        parsed = self._extract_bazi_info(msg)
        # k38-RI4b（复审实测·档案污染）：时间谓词（与 analyzer 路由、F2 提取
        # 同一事实源）——消息里的完整日期全在未来（婚期/预产期/行程等非生辰
        # 语境）时，`_extract_bazi_info` 靠补默认时辰/城市（hour=0、city=北京）
        # 也能凑出完整盘 → 婚期被当生辰排盘/建档。此处作废该提取结果，落回
        # 下方"无完整信息"分支（有档案→复用档案；无档案→建档引导/通用知识），
        # 不排盘不建档。修前实测：「2026年10月1日结婚，帮我看看我的婚姻」→
        # `_do_bazi_analysis(2026,10,1,0,0,'北京',…)` 落库。
        if parsed is not None and MessageAnalyzer.birth_dates_all_future(msg):
            parsed = None
        # L5-2 修复（降级成本）：降级用户的前置文案（复用档案确认/信息收集
        # 引导）不调 LLM，全部走固定文案（_do_bazi_analysis 内部同口径门控）。
        _dg = self._downgraded.get(user_id, False)

        if parsed is None:
            cur = self._extract_partial_birth(msg)
            if cur:
                # B3-1（2026-08-29）对话排盘归属判定（用户拍板规则）：
                # ① 规则2：当前消息明确第三方（他/她/朋友…）+ 出生信息 →
                #    排第三方（信息=当前消息+同会话对方分步累积，不被本人
                #    档案覆盖）；
                # ② 规则1：否则档案优先——默认命主有完整出生信息时直接用它，
                #    不被会话历史/渐进收集覆盖（历史里帮别人排的盘不再污染
                #    当前用户）；当前消息非身份键（时辰/城市/性别等）叠加；
                # ③ 规则3：当前消息出生年份与档案命主不一致且无法判定归属
                #    → 问一句确认（_gen_birth_conflict_ask），绝不静默排错；
                # ④ 无完整档案 → F2 渐进式累积（2026-08-26 体验保留：
                #    年龄→年份推算、部分回显、缺什么要什么、齐全自动排盘；
                #    历史累积跳过第三方标记消息，见 _collect_partial_birth）。
                if self._is_third_party_birth_request(msg):
                    known, missing = self._collect_partial_birth(
                        user_id, session_id, msg, third_party=True)
                    if known.get("year") and known.get("month") and known.get("day"):
                        # R2-6：known 可能含农历原始月日（分步口述含「农历」/中文
                        # 数字月日，_extract_partial_birth 带 _md_lunar 语义标记）→
                        # _feed_birth 单点转公历喂引擎 + arch_raw 持久化原值
                        _src = {k: v for k, v in known.items()
                                if k in ("year", "month", "day", "hour",
                                         "minute", "city", "gender")}
                        if known.get("_md_lunar"):
                            _src["calendar"] = "lunar"
                        return self._feed_birth(
                            _src, msg, user_id, stream_cb=stream_cb)
                    return self._gen_info_collection_prompt(
                        msg, lite=_dg, known=known, missing=missing)
                saved = self._get_user_birth_profile(user_id)
                if saved and saved.get("year") and saved.get("month") and saved.get("day"):
                    if (cur.get("year") and cur["year"] != saved["year"]):
                        return self._gen_birth_conflict_ask(msg, cur, saved)
                    # 档案为基线 + 当前消息补充（年份已判定一致或未提）
                    merged = dict(saved)
                    for _k in ("year", "month", "day", "hour", "minute",
                               "city", "gender"):
                        if _k in cur:
                            merged[_k] = cur[_k]
                    # R2-6：当前消息覆写月日时，日历语义随覆写值走——
                    # _md_lunar（显式「农历/阴历」或中文数字月日）= 农历口径；
                    # 阿拉伯数字无关键字 = 公历口径（D7 既有约定）。不覆写月日
                    # → 沿用档案继承的 calendar（B1 残余直喂点：merged 可能
                    # 仍是 lunar 原始值，_feed_birth 统一转公历后喂引擎）。
                    if "month" in cur and "day" in cur:
                        merged["calendar"] = (
                            "lunar" if cur.get("_md_lunar") else "solar")
                    # G1（2026-08-29 P0-C）：性别纠正——cur 提取到已确认性别
                    # （男/女）且档案性别也已确认、两者不一致 → 视为用户纠正
                    # 档案：以新性别重排 + 固定回执（零 LLM）；_do_bazi_analysis
                    # → _save_bazi_records 以新 gender 双写 bazi_info+persons，
                    # 档案自动跟随，永不背离。一致/档案 unknown → 原 merged
                    # 逻辑（cur 性别补充，行为不变）。
                    _saved_g = saved.get("gender")
                    _cur_g = merged.get("gender")
                    # C2b（2026-08-29）：判定逻辑抽为 _is_gender_correction，
                    # partial 分支与 parsed 直排路径共用同一判定（不重复实现）。
                    # k40 返工（Critical-1）：同 parsed 路径——性别按主语归属
                    # （第三人领属语引出的出生信息/性别词不得强制覆写本人档案）。
                    _is_correction = (
                        self._is_gender_correction(_cur_g, _saved_g)
                        and not self._gender_ref_is_third_party(None, msg))
                    if _is_correction:
                        # C2（2026-08-29）：纠正路径强制覆写记忆画像层性别——
                        # _do_bazi_analysis → _save_bazi_records → save_bazi_info
                        # (force_gender=True)。权威档案（users.bazi_info+persons）
                        # 本无条件双写新性别；画像层 save_bazi_info 默认拒绝
                        # 男↔女 冲突覆写，不强制则画像层 gender 永久滞后旧值
                        # （后续 LLM 上下文仍按旧性别引用，用户再遇言行错位）。
                        ack = self._gen_gender_correction_ack(_saved_g, _cur_g)
                        # R1-2（T008）：回执暂存，润色后幂等重挂（process 出口）
                        # getattr 兜底：__new__ 轻量装配（测试）无 __init__ 态
                        self._gender_acks = getattr(
                            self, "_gender_acks", None) or {}
                        self._gender_acks[user_id] = ack
                    else:
                        ack = self._gen_reuse_acknowledgment(msg, saved,
                                                             lite=_dg)
                    # R2-6（B1 残余直喂点）：merged 可能为 lunar 原始 y/m/d
                    # → _feed_birth 转公历后喂引擎，arch_raw 持久化原值+标记
                    result = self._feed_birth(
                        merged, msg, user_id, stream_cb=stream_cb,
                        force_gender=_is_correction)
                    return ack + "\n\n" + result if ack else result
                # ④ F2 渐进式累积（2026-08-26）：年/月/日齐全 → 直接
                # _do_bazi_analysis（hour/minute 缺省 0、gender 缺省
                # unknown、city 缺省空串，沿用 _do_bazi_analysis 参数约定
                # 与 1384 语义；排盘后 P2 对话建档自动落库，不新增）；
                # 不全 → 回显已确认项 + 缺什么要什么（_gen_info_collection_prompt）。
                known, missing = self._collect_partial_birth(
                    user_id, session_id, msg)
                if known.get("year") and known.get("month") and known.get("day"):
                    # R2-6（F2 渐进累积直喂点）：known 月日可能为农历原始值
                    # （分步口述含「农历」/中文数字月日，_md_lunar 标记）→
                    # _feed_birth 单点转公历喂引擎 + arch_raw 持久化原值
                    _src = {k: v for k, v in known.items()
                            if k in ("year", "month", "day", "hour",
                                     "minute", "city", "gender")}
                    if known.get("_md_lunar"):
                        _src["calendar"] = "lunar"
                    return self._feed_birth(
                        _src, msg, user_id, stream_cb=stream_cb)
                return self._gen_info_collection_prompt(
                    msg, lite=_dg, known=known, missing=missing)

            # 检查是否有已保存的信息 — 自动复用（bazi_info + persons +
            # chart_records 排盘档案兜底，D8 修复）
            saved = self._get_user_birth_profile(user_id)
            if saved and saved.get("year") and saved.get("month") and saved.get("day"):
                # AI generates a brief acknowledgment that we're using saved info
                ack = self._gen_reuse_acknowledgment(msg, saved, lite=_dg)
                # R2-6（B2 残余直喂点）：lunar 档案原始 y/m/d → _feed_birth
                # 转公历后喂引擎（R2-5 只修了 _tool_bazi 的档案兜底，本分支
                # 同款直喂——省前缀城市 + 阴历档案直排曾整链错误）
                result = self._feed_birth(
                    saved, msg, user_id, stream_cb=stream_cb)
                return ack + "\n\n" + result if ack else result

            # D9（2026-08-24 生产实测）：无档案但问事意图明确 → 先答通用
            # 命理知识（古籍知识可查向量库），末尾附建档引导——先答问题，
            # 再要档案；纯排盘引导请求（"帮我看看八字"）保持原引导文案。
            if self._has_question_topic(msg):
                return (self._answer_general_knowledge(msg) + "\n\n"
                        + self._gen_info_collection_prompt(msg, lite=_dg))

            # AI generates contextual info-collection prompt
            return self._gen_info_collection_prompt(msg, lite=_dg)

        year, month, day, hour, minute, city, gender = parsed
        # B3-1-fix（2026-08-29）规则3边界补全（parsed 完整信息路径）：
        # 当前消息含完整生辰（无第三方指代）且与档案命主年份冲突时，同样
        # 问一句确认——复用 _gen_birth_conflict_ask（与部分信息冲突路径
        # 同一套文案），绝不静默排盘/落库；信息与档案一致（或档案无此
        # 字段）时仍正常排盘（规则1不受影响）。有第三方指代 → 规则2排
        # 第三方，不询问（与既有 parsed 直排语义一致）。
        _is_correction = False
        _ack = ""
        # k11c：parsed 直排路径的档案真太阳时开关——本人（非第三方）且档案
        # 年份与消息一致（冲突时已在上方拦截）→ 随档案开关排；第三方/无档案
        # → 引擎默认开（与既有消息直排语义零变化）。
        _solar_self = True
        if not self._is_third_party_birth_request(msg):
            saved = self._get_user_birth_profile(user_id)
            _st_raw = (saved or {}).get("solar_time")
            _solar_self = (_st_raw not in (0, "0", False)) if saved else True
            if (saved and saved.get("year") and saved.get("month")
                    and saved.get("day") and saved["year"] != year):
                cur = {"year": year, "month": month, "day": day}
                if hour or minute:
                    cur["hour"] = hour
                    cur["minute"] = minute
                if city and city != "北京":
                    cur["city"] = city
                if gender and gender != "unknown":
                    cur["gender"] = gender
                return self._gen_birth_conflict_ask(msg, cur, saved)
            # C2b（2026-08-29）：parsed 直排路径同款性别纠正判定——消息提取
            # 到已确认性别（男/女）且档案性别也已确认、两者不一致 → 视为
            # 用户明示纠正：force_gender=True 穿透（_do_bazi_analysis →
            # _save_bazi_records → save_bazi_info 强制覆写画像层，权威档案
            # users.bazi_info+persons 随重排双写新性别）+ 固定回执（零 LLM）。
            # 一致/档案 unknown/第三方指代 → 行为保持现状（不强制、无回执）。
            # k40 返工（Critical-1）：parsed 路径的性别同样按**主语**归属——
            # `_extract_bazi_info` 的性别是裸子串规则（消息含「女」即取），
            # 「我朋友1990年5月20日出生的女生，帮我看看」这类由第三人领属语
            # 引出的性别词会走这里 force_gender 覆写本人档案（P0 同类，改前
            # 实测 male 档案 → 女 + 落盘）。`_gender_ref_is_third_party` 为
            # 同一主语感知事实源（声明式/口语词统一判）：判为第三人 →
            # 不判纠正、不强制覆写（仍按消息信息排盘，不改档案）。
            _saved_g = (saved or {}).get("gender")
            _is_correction = (self._is_gender_correction(gender, _saved_g)
                              and not self._gender_ref_is_third_party(
                                  None, msg))
            if _is_correction:
                _ack = self._gen_gender_correction_ack(_saved_g, gender)
                # R1-2（T008）：回执暂存，润色后幂等重挂（process 出口）
                # getattr 兜底：__new__ 轻量装配（测试）无 __init__ 态
                self._gender_acks = getattr(
                    self, "_gender_acks", None) or {}
                self._gender_acks[user_id] = _ack
        result = self._do_bazi_analysis(
            year, month, day, hour, minute, city, gender, msg, user_id,
            stream_cb=stream_cb, force_gender=_is_correction,
            solar_time=_solar_self,
        )
        return _ack + "\n\n" + result if _ack else result

    # ── R2-6 残余农历直喂点：档案/累积出生 dict 统一单点转换 ────────
    # R2-5 只修了 _tool_bazi 档案兜底；本批把同口径铺满 _handle_bazi 的
    # 档案+消息合并（B1）/档案直接兜底（B2）/F2 渐进累积（含第三方）/
    # _handle_calendar·_handle_hourly 引擎补齐，凡 lunar 原始 y/m/d 进引擎
    # 前必先转公历。存储层（persons/bazi_info/chart_records）原始值不动。

    def _solarize_birth(self, profile: dict) -> dict:
        """农历出生档案 → 公历 y/m/d 的浅拷贝（消费前单点转换）。

        R2-6（数据一致性铁律：存储=原始输入事实源，消费点单点转公历）：
        - calendar=='lunar' 且 to_solar_date 成功 → 返回**浅拷贝**：y/m/d 换
          公历，calendar 保留 'lunar' 原值（拷贝不落存储层，仅供消费；保留
          原值便于调用方判别"发生过转换"以决定 arch_raw 落库口径）。绝不
          mutate 入参 dict（persons/bazi_info/chart_records 原始值铁律）；
        - 非 lunar（含旧档案无标记，按 solar 语义）→ 原 dict 原样返回
          （正常流，不告警）；
        - lunar 但转换失败（非法农历日/越界/lunar-python 异常）→ 原 dict
          返回 + logger.warning 一行（不抛异常不阻塞，安全回落原值）。
        """
        if str(profile.get("calendar") or "solar") != "lunar":
            return profile
        _sol = to_solar_date(profile)
        if _sol:
            _p = dict(profile)
            _p["year"], _p["month"], _p["day"] = _sol
            return _p
        logger.warning(
            "R2-6 _solarize_birth：lunar 档案转公历失败，按原始值排盘 "
            "birth=%s-%s-%s", profile.get("year"), profile.get("month"),
            profile.get("day"))
        return profile

    def _feed_birth(self, src: dict, question: str, user_id: str,
                    stream_cb: Optional[Callable] = None,
                    force_gender: bool = False) -> str:
        """档案/累积出生 dict → 排盘主链路（R2-6 单点转换入口）。

        src 语义 = get_user_birth_profile / _collect_partial_birth 产出：
        y/m/d 为**原始输入**（农历时带 calendar='lunar' 语义）——
        lunar → _solarize_birth 转公历后喂引擎（引擎契约=公历输入）；
        且把原始 y/m/d + calendar='lunar' 经 arch_raw 透传 _do_bazi_analysis，
        持久化保留原始值 + 标记（不改写原值不抹标记，与 R2-5 _tool_bazi
        同口径）。solar/无标记 → 零行为变化（原值直喂、arch_raw=None）。
        转换失败 → 安全回落原值 + warning（_solarize_birth 内），不阻塞。
        solar_time（k11c）：src 携带档案开关（get_user_birth_profile 出参
        0/1；F2 渐进累积/第三方部分信息无键 → 引擎默认开现行为不变）。
        """
        _p = self._solarize_birth(src)
        _arch = None
        if str(src.get("calendar") or "solar") == "lunar":
            _arch = {"year": src.get("year"), "month": src.get("month"),
                     "day": src.get("day"), "calendar": "lunar"}
        _st = (src or {}).get("solar_time")
        return self._do_bazi_analysis(
            _p.get("year"), _p.get("month"), _p.get("day"),
            _p.get("hour") if _p.get("hour") is not None else 0,
            _p.get("minute") if _p.get("minute") is not None else 0,
            _p.get("city") or "", _p.get("gender") or "unknown",
            question, user_id, stream_cb=stream_cb,
            force_gender=force_gender, arch_raw=_arch,
            solar_time=(_st not in (0, "0", False)))

    # ── D9 无档案问事：先答通用知识，再要档案 ──────────────────────
    # 问事句式兜底词（知识关键词匹配优先；这里是句式级兜底）：
    # "我该佩戴/有没有…办法/能不能…" 等无主题词也可识别的问事表述
    _QUESTION_TOPIC_PATTERNS = ("怎么办", "如何", "能不能", "可以", "适合",
                                "建议", "该", "怎么", "吗", "呢", "有没有")

    def _has_question_topic(self, msg: str) -> bool:
        """D9：无档案时是否先答通用知识——消息含具体问事话题或问事句式。

        纯排盘引导请求（"帮我看看八字"、报生辰陈述）→ False（保持原引导）。
        """
        if not msg:
            return False
        for entry in _BAZI_GENERAL_KNOWLEDGE:
            if any(kw in msg for kw in entry["keywords"]):
                return True
        return any(p in msg for p in self._QUESTION_TOPIC_PATTERNS)

    def _answer_general_knowledge(self, msg: str, user_id: str = "") -> str:
        """D9：无档案问事的通用命理知识回答（先答问题，再要档案）。

        策略：静态知识兜底必回（确定性，0 LLM）；retriever 可用时先查古籍
        向量库，命中且 LLM 可用 → 以古籍佐证合成更贴合的回答；任何异常
        一律回退静态知识——绝不因检索/生成失败退回纯引导。
        """
        base = self._static_general_knowledge(msg)
        refs = []
        try:
            r = self.retriever.search(msg, category="bazi", top_k=5)
            if isinstance(r, (list, tuple)):
                # k24：旧判据 isinstance(x, dict) 只认 dict，而 retriever 返回的是
                # ChunkResult / _FaissChunk 对象 —— 检索再准也被整体丢弃，本分支
                # 恒不执行（同类断链）。改为兼容两种形态并保留有正文的条目。
                refs = [
                    x for x in (_ref_title_text(item) for item in r) if x[1]
                ]
        except Exception:
            refs = []
        if refs:
            try:
                ref_text = "\n".join(
                    f"- {t}：{text[:200]}" for t, text in refs[:3])
                composed = self._quick_flash(
                    f"用户问：「{msg}」，但还没有提供出生信息。\n"
                    f"以下是古籍检索到的相关资料：\n{ref_text}\n\n"
                    "请用现代中文给出100-150字的通用命理知识回答：直接回答用户"
                    "的问题本身（佩戴/财运等通用知识），不要索要出生信息，"
                    "不要编造用户的八字命局。用现代中文，不要用「小友」「老夫」。"
                    "直接返回文本，不要引号不要JSON。",
                    max_tokens=400)
                if composed:
                    return composed
            except Exception:
                pass
        return base

    def _static_general_knowledge(self, msg: str) -> str:
        """D9：静态通用命理知识（确定性兜底，无 LLM/无向量库也必回实质内容）。"""
        for entry in _BAZI_GENERAL_KNOWLEDGE:
            if any(kw in msg for kw in entry["keywords"]):
                return entry["content"]
        return _BAZI_GENERAL_KNOWLEDGE_FALLBACK

    COMMON_CITIES = {"北京", "上海", "广州", "深圳", "天津", "重庆", "杭州", "南京",
                     "成都", "武汉", "西安", "苏州", "长沙", "郑州", "青岛", "大连",
                     "厦门", "宁波", "福州", "合肥", "沈阳", "哈尔滨", "昆明", "贵阳",
                     "南宁", "海口", "兰州", "银川", "西宁", "拉萨", "乌鲁木齐",
                     "呼和浩特", "石家庄", "太原", "济南", "南昌", "榆树"}

    def _extract_user_context(self, msg: str) -> str:
        """从用户消息中提取处境/关注点。

        去掉日期、时间、城市、性别等排盘信息后，
        剩余文本即为用户关心的处境描述。
        """
        if not msg:
            return ""
        # 去掉日期时间模式
        cleaned = re.sub(r'\d{4}\s*[年/-]\s*\d{1,2}\s*[月/-]\s*\d{1,2}.*?(?=[一-鿿]|$)', '', msg)
        cleaned = re.sub(r'\d{1,2}\s*[点时:：]\s*\d{0,2}', '', cleaned)
        # 去掉城市和性别
        for city in self.COMMON_CITIES:
            cleaned = cleaned.replace(city, "")
        cleaned = cleaned.replace("男", "").replace("女", "")
        # 去掉排盘关键词
        for kw in ["八字", "排盘", "命理", "帮我", "看命", "算命", "看看"]:
            cleaned = cleaned.replace(kw, "")
        cleaned = cleaned.strip()
        # 如果去掉后为空，返回空字符串
        if not cleaned or len(cleaned) < 2:
            return ""
        return cleaned

    def _extract_bazi_info(self, msg: str) -> Optional[Tuple]:
        """从消息中提取八字信息 — 支持农历中文数字、时间描述、多种格式.

        D7（2026-08-24 生产实测 8767 真实链路）修复三项口语长句短板：
        ① 阴历/农历 + 中文数字月 + 阿拉伯数字日（"阴历三月28"）——
           _parse_cn_month_day 支持后，本函数标记 is_lunar 并经 lunar-python
           转阳历（引擎口径为阳历输入）；
        ② 模糊时辰（"接近11点"）→ 按边界取（"接近/将近/临近/快到/快" →
           anchor-1:55，即 10:55 优先；"大约/大概/约" → anchor:00），
           宁可先按边界排盘也不放弃解析（用户可在回复后确认）；
        ③ 嵌套城市（"吉林省长春市榆树市"）→ 取最内层"XX市"（榆树市），
           不再误取省名。
        """
        # Step 1: Extract year
        # D9（2026-08-26 回归修复）：旧版 BAZI_EXTRACT_PATTERNS 支持 dash/slash
        # 年份（"1990-05-20 15:00 深圳 女"），AI 原生重写（2da3396）时丢失。
        # 追加 `(\d{4})\s*[-/]\s*\d{1,2}` 替代项（group 5）恢复该格式，
        # 不触碰现有 年/公历/阳历/公元 各格式。
        year = None
        ym = re.search(r'(\d{4})\s*年|公历\s*(\d{4})|阳历\s*(\d{4})|公元\s*(\d{4})|(\d{4})\s*[-/]\s*\d{1,2}', msg)
        if ym:
            year = int(ym.group(1) or ym.group(2) or ym.group(3) or ym.group(4) or ym.group(5))

        if not year or year < 1900 or year > 2100:
            return None

        # Step 2: Extract month and day — try Chinese lunar first
        month = day = None
        is_lunar = False
        cn_md = _parse_cn_month_day(msg)
        if cn_md:
            month, day = cn_md
            is_lunar = True  # 中文数字月日（三月初三/三月28）→ 农历口径
        else:
            # Try numeric date: 8月15日, 8-15, 10月10日, 11.20
            # (?<!\d) 防止 dash 年份被误拆（"1990-05-20" 不能匹配成 "90-05"）
            md = re.search(r'(?<!\d)(\d{1,2})\s*[月\-/.]\s*(\d{1,2})\s*[日号]?', msg)
            if md:
                month = int(md.group(1))
                day = int(md.group(2))
                # 阿拉伯数字 + 显式农历/阴历前缀（"农历1999年3月28"）→ 农历
                if re.search(r'农历|阴历', msg):
                    is_lunar = True

        if not month or not day or abs(month) > 12 or day < 1 or day > 31:
            return None  # month=0/None 或超界 → 放弃本提取（不误传引擎）

        # 农历 → 阳历（lunar-python，闰月用负月）。转换失败不放弃：
        # ① 闰月在该年不存在（如"闰三月"实无闰三月）→ 按平月近似；
        # ② 小月无效日（如 2024 农历三月三十——三月仅 29 天）→ 库抛异常
        #    → 下方 except 回退分支按近似阳历继续（abs(month)）；
        # ③ 仍失败 → 按原值（近似阳历）继续——宁可多步确认也不放弃解析。
        # 口径固化（批次 2 B3-21，2026-08-27 reviewer 实测修正，库行为复核一致）：
        #   - 30 天月是合法日（如 2024 农历二月实有 30 天：二月初一=solar 3-10、
        #     二月三十=solar 4-8、三月初一=4-9），Lunar.fromYmd(2024,2,30) 返回
        #     2024-04-08 是正确换算而非"顺延"；库对真无效日抛异常（"only 29 days
        #     in lunar year 2024 month 3"）→ 本块 except 按近似阳历回退——
        #     "库口径顺延"说不存在；
        #   - 与 record_query._lunar_birth_text（阳历→农历展示，反向）各自独立
        #     fail-open，互不影响。
        # 测试见 tests/test_partial_birth.py::test_extract_lunar_*（四类场景固化）。
        if is_lunar:
            try:
                from lunar_python import Lunar
                _solar = Lunar.fromYmd(year, month, day).getSolar()
                year, month, day = (_solar.getYear(),
                                    _solar.getMonth(), _solar.getDay())
            except Exception:
                if month < 0:
                    try:
                        from lunar_python import Lunar
                        _solar = Lunar.fromYmd(year, abs(month), day).getSolar()
                        year, month, day = (_solar.getYear(),
                                            _solar.getMonth(), _solar.getDay())
                    except Exception:
                        month = abs(month)  # 平月近似，绝不传负月给引擎
                else:
                    month = abs(month)

        # Step 3: Extract time
        hour = 0
        minute = 0

        # Time descriptions: 凌晨3点→3, 下午3点→15, 晚上8点→20
        tm_desc = re.search(
            r'(凌晨|早上|早晨|上午|中午|正午|下午|傍晚|黄昏|晚上|夜里|夜间|半夜)'
            r'\s*(\d{1,2})?\s*[点时]?\s*(\d{0,2})?\s*[分]?',
            msg
        )
        if tm_desc:
            desc = tm_desc.group(1)
            h_val = int(tm_desc.group(2) or 0)
            m_val = int(tm_desc.group(3) or 0)
            adj = _TIME_ADJUST.get(desc, 0)
            # Don't add adjustment if hour is already in 24h format (>= 13)
            if h_val >= 13:
                hour = h_val
            else:
                hour = h_val + adj
            minute = m_val

        # Also try 时辰
        if hour == 0:
            shichen = re.search(r'([子丑寅卯辰巳午未申酉戌亥])时', msg)
            if shichen:
                hour = CHINESE_HOUR_MAP.get(shichen.group(1), 0)

        # D7 模糊时辰："接近11点"→10:55（先边界排盘，回复后由用户确认）；
        # "大约/大概/约11点"→11:00
        if hour == 0:
            fuzzy = re.search(
                r'(接近|将近|临近|快到|快|大约|大概|约)\s*(\d{1,2})\s*点', msg)
            if fuzzy:
                anchor = int(fuzzy.group(2))
                if fuzzy.group(1) in ("接近", "将近", "临近", "快到", "快"):
                    # 接近整点 → 前一小时 55 分（"接近11点"→10:55 优先）
                    hour = anchor - 1 if anchor >= 1 else 23
                    minute = 55
                else:
                    hour = anchor
                    minute = 0

        # Also try numeric time: 15:30, 15点30, 23:00
        if hour == 0:
            tm_num = re.search(r'(\d{1,2})\s*[点时:：]\s*(\d{0,2})', msg)
            if tm_num:
                hour = int(tm_num.group(1))
                minute = int(tm_num.group(2) or 0)
                # Check if PM adjustment needed
                if re.search(r'(下午|晚上|傍晚|夜间|夜里)', msg):
                    if 1 <= hour <= 12:
                        hour += 12

        # Step 4: Extract gender — P1-3: default to "unknown" instead of "男"
        gender = "unknown"
        if "女" in msg:
            gender = "女"
        elif "男" in msg:
            gender = "男"

        # Step 5: Extract city — D7 嵌套城市取最内层"XX市"
        # （"吉林省长春市榆树市"→ 榆树市，不再误取省名）
        city = "北京"
        city_matches = re.findall(r'([一-鿿]{2,5}?市)', msg)
        if city_matches:
            city = city_matches[-1]
        else:
            city_match = re.search(r'({})'.format('|'.join(self.COMMON_CITIES)), msg)
            if city_match:
                city = city_match.group(1)

        return (year, month, day, hour, minute, city, gender)

    # ── F2 渐进式出生信息累积（2026-08-26）────────────────────────────
    # B3-1（2026-08-29）：第三方排盘指代判定。用途：① _handle_bazi 当前
    # 消息归属判定（规则2）；② _collect_partial_birth 跳过含他人信息的
    # 历史消息（本人渐进收集不被污染）。仅在消息已含出生信息时判定才有
    # 意义；无出生信息时命中无害（不提取任何键）。
    # B3-1-fix（2026-08-29）：单字裸子串匹配（他/她/妈/爸）误伤本人陈述
    # ——实测「其他我记不清了」（"其他"含"他"）、「我妈说我是1976年生的」
    # （"妈"是信息出处不是排盘对象）被误判第三方，走 third_party 分支合并
    # 全部历史 → 历史含他人完整生辰时静默排出错盘并落库。收紧为两类证据：
    # ① 结构模式：帮/给/为/替 + 目标（他/她/朋友/亲属称谓…）+ 排/算/看
    #    （盘|八字|命|卦）——明确「给 X 排盘」；
    # ② 指代词 他/她 直接后接出生信息（年份/年龄）——「他1976年生」。
    # 亲缘称谓（妈/爸/朋友…）不再裸词触发，只在结构模式内有效。
    _THIRD_PARTY_MARKERS = (
        "他", "她", "朋友", "同事", "同学", "儿子", "女儿", "孩子", "小孩",
        "老公", "老婆", "妻子", "丈夫", "爸爸", "妈妈", "父亲", "母亲",
        "爷爷", "奶奶", "外公", "外婆", "姥爷", "姥姥", "哥哥", "弟弟",
        "姐姐", "妹妹", "孙子", "孙女", "侄子", "侄女", "外甥", "外甥女",
        "对象", "恋人", "男朋友", "女朋友", "客户", "老板", "邻居", "亲戚",
        "家属", "爸", "妈",
    )
    _THIRD_PARTY_STRUCT_RE = re.compile(
        r'(?:帮|给|为|替)[^，。！？!?；;、\n]{0,12}?'
        r'(?:' + '|'.join(_THIRD_PARTY_MARKERS) + r')'
        r'[^，。！？!?；;、\n]{0,4}?'
        r'(?:排|算|看|看看)(?:个|一)?(?:盘|八字|命|卦)?'
    )
    _THIRD_PARTY_PRONOUN_BIRTH_RE = re.compile(
        r'[他她][^，。！？!?；;、\n\d〇零一二三四五六七八九]{0,2}'
        r'(?:(?:\d{4}|[〇零一二三四五六七八九]{2,4})\s*年|\d{1,3}\s*岁)'
    )

    def _history_has_third_party_birth(self, user_id: str,
                                       session_id: Optional[str] = None) -> bool:
        """R1-1（T096 跨用户污染）：会话历史里是否存在第三方排盘消息。

        润色 LLM（_polish_with_engine_draft）会把会话历史注入提示——若历史里
        有「帮我朋友排个盘，他1976年…」这类消息，润色可能把朋友的出生信息
        当成当前用户的（在线复现实锤：T096 轮2「我的运势怎么样」被润色成
        「根据你1976年5月13日在上海出生的命盘…」，D2 因干支声明不足 4 个
        早退无法兜底）。返回 True → 调用方跳过润色，保留确定性引擎原稿。
        """
        if not self.session_dao:
            return False
        try:
            history = self.session_dao.get_context_for_llm(
                user_id, history_limit=20, session_id=session_id)
        except Exception:
            return False
        for m in history or []:
            if m.get("role") != "user":
                continue
            if self._is_third_party_birth_request(str(m.get("content") or "")):
                return True
        return False

    def _is_third_party_birth_request(self, msg: str) -> bool:
        """B3-1（规则2）：当前消息是否明确指代第三方排盘。

        用户拍板：只有明确给第三方（如「帮我朋友排，他X年X月X日X时生」）
        才排第三方；含糊时默认本人（档案命主）。
        B3-1-fix：结构模式（帮/给/为/替 + 目标 + 排盘）或指代词后接出生
        信息才算第三方；亲缘词裸出现不算——「其他我记不清了」「我妈说
        我是1976年生的」不再误判第三方。
        """
        if not msg:
            return False
        if self._THIRD_PARTY_STRUCT_RE.search(msg):
            return True
        return bool(self._THIRD_PARTY_PRONOUN_BIRTH_RE.search(msg))

    def _extract_partial_birth(self, msg: str,
                               current_year: Optional[int] = None) -> dict:
        """F2：从单条消息中提取"部分出生信息"——任意命中的键即可，不要求齐全。

        返回 dict 只含命中的键：year/month/day/hour/minute/city/gender 的
        任意非空子集；无任何命中 → {}。**不改 _extract_bazi_info 既有行为**
        （可抽共用正则，返回值语义不动）。

        - year：阿拉伯 4 位年（1900-2100，与 _extract_bazi_info 同口径）→
          中文数字年（"一九七六"→1976；两位"七六"→1976、"零六"→2006，
          27-99→19xx，00-26→20xx）→ 年龄推算（"50岁"→current_year-50，
          含"虚岁"→+1，仅阿拉伯数字，1-130 超界不取，多条取最后一条）。
          年份来自年龄推算时带 _age_used（回显"按X岁推算"说明用）
        - month/day：中文农历月日（_parse_cn_month_day）或阿拉伯数字月日——
          不做农历→阳历转换、不做月日齐全校验，保持原文口径
        - hour/minute：时间段词/时辰/模糊点/数字时间——只记数值不做校验
        - city/gender：最内层"XX市"；独立出现的男/女（防"渣男/美女"误取）
        """
        if not msg:
            return {}
        out: dict = {}
        cy = current_year if current_year is not None else date.today().year

        # k38-RI4b（复审实测·档案污染）：时间谓词——消息里的完整日期**全在未来**
        # （婚期/预产期/行程等非生辰语境）时不得当生辰提取。判定复用 analyzer 侧
        # 同一事实源 `MessageAnalyzer.birth_dates_all_future`（单一谓词覆盖
        # analyzer 路由与本提取两条路径，不在此重复实现时间判定）；命中任一可作
        # 生辰的日期即放行（「我1990年5月20日出生，2026年10月1日结婚」仍提取
        # 1990 那条）。修前实测：「2026年10月1日结婚，帮我看看我的婚姻」→ 婚期
        # year/month/day 被当生辰 → 排盘/建档（F2 累积、_redirect_single_marriage
        # 同洞）。
        if MessageAnalyzer.birth_dates_all_future(msg, cy):
            return {}

        # ── year（优先级：阿拉伯 4 位 > 中文 4 位 > 中文 2 位 > 年龄推算）──
        year = None
        age_used = None
        # k33/A16：「公历/阳历/公元」前缀式年份（"公历1976"无「年」字）与
        # _extract_bazi_info:5516 同口径收口——此前 F2 只认 `\d{4}年`，
        # "我公历1976生的"在渐进累积通道里年份永远缺失（分步补全死循环问年份）。
        ym = re.search(
            r'(\d{4})\s*年|公历\s*(\d{4})|阳历\s*(\d{4})|公元\s*(\d{4})', msg)
        if ym:
            y = int(next(g for g in ym.groups() if g))
            if 1900 <= y <= 2100:
                year = y
        if year is None:
            ym_cn4 = re.search(r'([〇零一二三四五六七八九]{4})年', msg)
            if ym_cn4:
                y = _cn_year_to_int(ym_cn4.group(1))
                if y is not None and 1900 <= y <= 2100:
                    year = y
        if year is None:
            ym_cn2 = re.search(r'([〇零一二三四五六七八九]{2})年', msg)
            if ym_cn2:
                y = _cn_year_to_int(ym_cn2.group(1))
                if y is not None and y <= 99:
                    year = 2000 + y if y < 27 else 1900 + y
        if year is None:
            age = _extract_age(msg)
            if age is not None:
                _y = cy - age + (1 if "虚岁" in msg else 0)
                if 1900 <= _y <= 2100:
                    year = _y
                    age_used = age
        if year is not None:
            out["year"] = year
            if age_used is not None:
                out["_age_used"] = age_used

        # ── month/day（农历口径在齐全时由 _extract_bazi_info/_do_bazi_analysis
        #    现有链路处理；部分信息阶段保持原文即可）──
        month = day = None
        cn_md = _parse_cn_month_day(msg)
        if cn_md:
            month, day = cn_md
        else:
            md = re.search(r'(\d{1,2})\s*[月\-/.]\s*(\d{1,2})\s*[日号]?', msg)
            if md:
                month = int(md.group(1))
                day = int(md.group(2))
        if month and day and abs(month) <= 12 and 1 <= day <= 31:
            out["month"] = month
            out["day"] = day
            # R2-6：月日口径语义标记——中文数字月日（_parse_cn_month_day）或
            # 显式「农历/阴历」关键字 → 农历口径；阿拉伯数字无关键字 → 公历
            # 口径（D7 既有约定）。marker 随 known 累积最新者胜（下游
            # _collect_partial_birth），齐全自动排盘时驱动 _feed_birth 单点
            # 转公历——残余直喂点不因分步口述的农历日期再漏网。
            out["_md_lunar"] = bool(cn_md or re.search(r'农历|阴历', msg))

        # ── hour/minute（复用 _extract_bazi_info 三段口径，只记数值不做校验）──
        hour = minute = None
        # 时间段词（凌晨/上午…）：必须带数字才取（"晚上出生"无具体时间不取）
        tm_desc = re.search(
            r'(凌晨|早上|早晨|上午|中午|正午|下午|傍晚|黄昏|晚上|夜里|夜间|半夜)'
            r'\s*(\d{1,2})?\s*[点时]?\s*(\d{0,2})?\s*[分]?',
            msg)
        if tm_desc and (tm_desc.group(2) or tm_desc.group(3)):
            desc = tm_desc.group(1)
            h_val = int(tm_desc.group(2) or 0)
            m_val = int(tm_desc.group(3) or 0)
            adj = _TIME_ADJUST.get(desc, 0)
            hour = h_val if h_val >= 13 else h_val + adj
            minute = m_val
        # 时辰字
        if hour is None:
            shichen = re.search(r'([子丑寅卯辰巳午未申酉戌亥])时', msg)
            if shichen:
                hour = CHINESE_HOUR_MAP.get(shichen.group(1), 0)
                minute = 0
        # 模糊点（"接近11点"→10:55；"大约11点"→11:00）
        if hour is None:
            fuzzy = re.search(
                r'(接近|将近|临近|快到|快|大约|大概|约)\s*(\d{1,2})\s*点', msg)
            if fuzzy:
                anchor = int(fuzzy.group(2))
                if fuzzy.group(1) in ("接近", "将近", "临近", "快到", "快"):
                    hour = anchor - 1 if anchor >= 1 else 23
                    minute = 55
                else:
                    hour = anchor
                    minute = 0
        # 数字时间（"15:30"/"10点"/"10点以后"→10:00）
        if hour is None:
            tm_num = re.search(r'(\d{1,2})\s*[点时:：]\s*(\d{0,2})', msg)
            if tm_num:
                hour = int(tm_num.group(1))
                minute = int(tm_num.group(2) or 0)
                if re.search(r'(下午|晚上|傍晚|夜间|夜里)', msg):
                    if 1 <= hour <= 12:
                        hour += 12
        if hour is not None:
            out["hour"] = hour
            out["minute"] = minute

        # ── city/gender ──
        city_matches = re.findall(r'([一-鿿]{2,5}?市)', msg)
        if city_matches:
            out["city"] = city_matches[-1]  # 最内层（"吉林省长春市榆树市"→榆树市）
        else:
            city_match = re.search(r'({})'.format('|'.join(self.COMMON_CITIES)),
                                   msg)
            if city_match:
                out["city"] = city_match.group(1)
        # G1（2026-08-29 P0-C）：性别口语词扩展（保持防"渣男/美女"误伤）——
        # 女系：女孩/女生/姑娘/丫头/女的/小姑娘/闺女/性别女 + 原独立「女」规则
        # 男系：男孩/男生/男的/小伙子/性别男 + 原独立「男」规则
        # 优先级：女系先查（与既有 女→男 顺序一致）；独立规则前字粘连
        # （渣/美）不命中；「女儿/儿子」等第三方称谓不在词表（归属判定归
        # 第三方链路，见 B3-1 _is_third_party_birth_request）
        # k40 返工（Critical-1 + Important-1）：性别词按**主语**归属（见模块级
        # 注释）——口语词按 `_gender_word_subject` 逐处判「本人自述 / 第三人」，
        # 消息出生信息由第三人领属语引出（「我妹妹1990年出生的女孩子」）时
        # **全部**性别来源（含独立「男/女」「性别男/女」声明式）一律不取：
        # 声明式同样带主语（「我妹妹1990年出生的，性别女」里的 性别女 是妹妹
        # 的），改前它绕过修饰排除 → 仍然覆写本人档案（P0 同类）。T008
        # 「我是女孩儿，不是男孩」判为本人自述 → 仍取 女（双向用例锁）。
        if _GENDER_THIRD_BIRTH_MSG_RE.search(msg):
            return out          # 出生信息属第三人 → 本条消息不取性别
        if (re.search(r'(?:^|[^\w])女(?:$|[^\w])|性别女', msg)
                or self._has_self_gender_word(msg, _ORAL_FEMALE_WORDS)):
            out["gender"] = "女"
        elif (re.search(r'(?:^|[^\w])男(?:$|[^\w])|性别男', msg)
                or self._has_self_gender_word(msg, _ORAL_MALE_WORDS)):
            out["gender"] = "男"
        return out

    # k40 返工（Critical-1 + Important-1）：口语性别词是否**本人自述**。
    # 判据 = 主语感知（`_gender_word_subject`，见模块级注释）：同一位置被判定
    # 指代第三人则跳过该处；本条消息的出生信息由第三人领属语引出
    # （「我妹妹1990年出生的女孩子」）→ 整条消息的口语性别词一律不取。
    # 修 Important-1：「我(是)一个女孩」的「一个」不再被当第三人修饰——量词
    # 前若为自述系动词（我是一个）→ 仍判本人自述。
    # k41（Critical-1 词形覆盖）：遍历口径与守卫同源——`_gender_oral_words_in`
    # 的**最大词形**（女孩子/男孩子/女孩儿/男孩儿/小姑娘…），否则「女孩子1990年
    # 出生的…」在提取层用裸词「女孩」判 self（偏移落在「子」上）→ 守卫修好了、
    # 提取层仍取到 女（数据面分裂：同一判定两套 token 口径）。
    @staticmethod
    def _has_self_gender_word(msg: str, words) -> bool:
        if _GENDER_THIRD_BIRTH_MSG_RE.search(msg):
            return False
        _words = tuple(words)
        for _pos, _w in _gender_oral_words_in(msg):
            if not any(_w.startswith(_x) for _x in _words):
                continue          # 男/女系各查各的（词表单一事实源 + 词法后缀）
            if _gender_word_subject(msg, _pos, _w) == "self":
                return True
        return False

    def _collect_partial_birth(self, user_id: str,
                               session_id: Optional[str] = None,
                               msg: str = "",
                               history: Optional[list] = None,
                               third_party: bool = False
                               ) -> Tuple[dict, list]:
        """F2：累积合并"会话历史 user 消息 + 当前 msg"中的部分出生信息。

        - history=None 时：session_dao.get_context_for_llm(user_id,
          history_limit=8, session_id=session_id)（session_id 可 None=用户级历史，
          现有行为）；只取 role=="user" 的消息
        - 合并顺序：历史（旧→新）→ 当前 msg，最新者胜（后写覆盖）
        - B3-1（2026-08-29）归属收窄：third_party=False（默认=本人渐进收集）
          时跳过含第三方指代（他/她/朋友/亲属…，_THIRD_PARTY_MARKERS）的
          历史消息——历史里帮别人排的盘不再污染当前用户；third_party=True
          （当前消息明确第三方排盘）时合并全部历史（对方分步给信息也累积）。
        - 不读档案基线（档案复用走 _handle_bazi 既有 saved 分支）；不落库（无状态）

        返回 (known, missing)：
        - known：合并后的 dict（只含至少命中一次的键，含 _age_used 推算说明）
        - missing：缺失项固定顺序 ["出生年份"(缺时首位), "出生月日", "出生时辰",
          "出生城市", "性别"]；hour/minute 任一缺 → "出生时辰" 进 missing
          （时辰缺失不阻塞排盘，沿用 hour/minute 缺省 0 语义，但引导仍要问）；
          known 为空 → missing 为全量列表
        """
        merged: dict = {}
        if history is None and self.session_dao is not None:
            try:
                history = self.session_dao.get_context_for_llm(
                    user_id, history_limit=8, session_id=session_id)
            except Exception:
                history = None
        messages: list = []
        if history:
            for _h in history:
                if _h.get("role") != "user" or not _h.get("content"):
                    continue
                _c = _h["content"]
                if not third_party and self._is_third_party_birth_request(_c):
                    continue  # B3-1：他人信息不入本人累积
                messages.append(_c)
        if msg:
            messages.append(msg)
        for m in messages:
            part = self._extract_partial_birth(m)
            for k, v in part.items():
                merged[k] = v
            # 最新一条直接报出年份 → 清除此前年龄推算的"按X岁推算"说明
            if "year" in part and "_age_used" not in part:
                merged.pop("_age_used", None)
            # R2-6：月日口径最新者胜（镜像 _age_used 清除语义）——本条月日为
            # 公历口径（阿拉伯数字无关键字）→ 清除此前「农历」标记；本条带
            # 农历标记已由上方合并写入。无月日条目不触碰既有标记。
            if "month" in part and "day" in part and not part.get("_md_lunar"):
                merged["_md_lunar"] = False
        if not merged:
            return {}, ["出生年份", "出生月日", "出生时辰", "出生城市", "性别"]
        missing: list = []
        if "year" not in merged:
            missing.append("出生年份")
        if not (merged.get("month") and merged.get("day")):
            missing.append("出生月日")
        if "hour" not in merged or "minute" not in merged:
            missing.append("出生时辰")
        if "city" not in merged:
            missing.append("出生城市")
        if "gender" not in merged:
            missing.append("性别")
        return merged, missing

    def _gen_birth_conflict_ask(self, msg: str, cur: dict,
                                saved: dict) -> str:
        """B3-1（2026-08-29）规则3：当前消息出生信息与档案命主不一致且
        无法判定归属 → 问一句确认（固定文案零 LLM，绝不静默排错）。

        - 回显当前消息已确认信息（_format_partial_echo，年龄推算带说明）
        - 回显档案信息；给出路：回复「按档案」用档案信息排；或直接把完整
          出生时间发过来（帮别人排则带对方信息）。
        """
        cur_echo = _format_partial_echo(cur, msg)
        saved_echo = (f"{saved.get('year')}年{saved.get('month')}月"
                      f"{saved.get('day')}日")
        return (
            f"您刚说的出生信息（{cur_echo}）和档案里您的信息"
            f"（{saved_echo}）不太一致，我先跟您确认一下，怕排错盘：\n"
            f"1. 按档案信息（{saved_echo}）给您排——回复「按档案」；\n"
            "2. 按刚说的信息排——请把完整出生时间直接发给我"
            "（年月日时分+城市+性别）；\n"
            "3. 如果是帮别人排的，请告诉我对方的出生时间～"
        )

    @staticmethod
    def _emit_stream_event(stream_cb, evt_type: str, text: str) -> None:
        """安全地发出流式进度事件（流式模式；回调异常静默忽略）。"""
        if stream_cb is None:
            return
        try:
            stream_cb(evt_type, {"text": text})
        except Exception:
            pass

    def _should_fastpath(self, user_id: str, birth: dict) -> bool:
        """快路径门控：有已存排盘结果（同生辰）或档案时跳过 RAG 预检索。

        契约（Task 10）：
        - 仅在主分析路径内生效（降级路径在其之前 return，不经过本方法）
        - fail-open：任何异常回落 False，走原检索路径，绝不因门控错误
          让用户拿到空引用
        - chart 比对 year/month/day（get_latest_chart 已解密 birth 键；
          语义与 _persist_chart_result 落库的 birth 一致）
        """
        try:
            chart_dao = getattr(self, "chart_dao", None)
            if chart_dao:
                chart = chart_dao.get_latest_chart(user_id)
                if chart and chart.get("birth"):
                    b = chart["birth"]
                    # R2-6：chart 行日期=原始输入（lunar 档案排盘落库原始
                    # y/m/d+标记，R2-5 口径）→ 比对前单点转公历——消费方统一
                    # 经 to_solar_date（_feed_birth 转出的引擎参数为公历，chart
                    # 原始值不转则同生辰 lunar 用户每次重排都漏过快路径白跑 RAG）
                    _cb = self._solarize_birth(b)
                    if (_cb.get("year") == birth.get("year")
                            and _cb.get("month") == birth.get("month")
                            and _cb.get("day") == birth.get("day")):
                        return True
            # k18：「档案」存在性判断改走统一档案链（persons 优先，与文档
            # 语义「或档案时跳过 RAG 预检索」一致）——原裸读 bazi_info 把
            # persons-only 建档用户漏成无档案（T089 同类的 fastpath 面）。
            # allow_chart_fallback=False：③ chart_records 兜底不算档案——
            # ③ 行可能只是他人/择时盘，且 chart 同生辰比对（上方第一条件）
            # 已覆盖「已存同盘」场景（k9 契约：chart 不匹配 + 无档案 → False）。
            if self._get_user_birth_profile(user_id,
                                            allow_chart_fallback=False):
                return True
        except Exception:
            pass
        return False

    def _do_bazi_analysis(
        self, year, month, day, hour, minute, city, gender, question, user_id,
        stream_cb: Optional[Callable] = None,
        force_gender: bool = False,
        arch_raw: Optional[dict] = None,
        solar_time: bool = True,
    ) -> str:
        """执行八字分析

        force_gender（G1-C2）：仅用户明示性别纠正分支传 True，穿透到
        _save_bazi_records → save_bazi_info 强制覆写记忆画像层冲突性别。
        arch_raw（R2-6）：lunar 原始出生 y/m/d（含 calendar='lunar' 标记）——
        仅当档案/累积来源为农历且引擎已按转公历算盘时由 _feed_birth 透传：
        落库（bazi_info/persons/chart_records）保留原始 y/m/d + 标记
        （存储=原始输入事实源，不改写原值不抹标记——B1/B2/F2 残余直喂点与
        R2-5 _tool_bazi 同口径）。None = 普通公历路径，行为零变化。
        solar_time（k11c）：档案级真太阳时开关 → 引擎（True=按出生地经度+
        均时差修正=默认开现行为；False=用户档案关闭=北京时间直排）。
        默认 True：消息直排/无档案等无开关语义路径零行为变化。
        """
        # 1. 排盘（流式模式先发进度事件，避免引擎阶段长沉默触发看门狗）
        self._emit_stream_event(stream_cb, "thinking", "正在排盘…")
        result = self.engine.calculate(
            year, month, day, hour, minute, city, gender,
            solar_time=solar_time)
        # k11-B/C：本轮事实上下文（称谓 scrub 用 gender + 神煞 scrub 用全集 allow）
        # ——process 入口已清空；仅命理轮在此重建，供流式出口/整段 scrub 消费。
        self._set_fact_ctx(user_id, getattr(result, "gender", ""),
                           getattr(result, "shensha", None))
        # R2-6：落库 birth 字典——arch_raw 提供时保留原始 y/m/d + calendar
        # 标记；否则引擎参数即事实。hour/minute/city/gender 恒取引擎参数
        # （农历转换只作用于 y/m/d，时间/地点/性别不受影响原样持久化）。
        _persist_birth = {"year": year, "month": month, "day": day,
                          "hour": hour, "minute": minute,
                          "city": city, "gender": gender}
        if arch_raw is not None:
            _persist_birth = {"year": arch_raw.get("year", year),
                              "month": arch_raw.get("month", month),
                              "day": arch_raw.get("day", day),
                              "hour": hour, "minute": minute,
                              "city": city, "gender": gender,
                              "calendar": "lunar"}
        # E2-1 卡片化：完成引擎排盘（paipan 卡片判定依据，含降级精简路径）
        self._mark_card_turn(user_id, paipan=True)

        # R1-1（评测 T096 修复·跨用户污染·只作排盘展示）：subject=other（帮
        # 朋友排盘）→ 确定性卡片回复：跳过 LLM 深度分析/秒回安抚/行动建议/
        # 润色。他人命盘只作展示——分析 LLM 曾把朋友盘干支重印成
        # 「年柱：丙辰（火土）」（且改写为错柱乙卯/甲辰），混入用户运势上下文
        # 造成跨用户污染表述（T096 轮1 实锤）；确定性卡片（天干/地支分行）永
        # 不含连续干支文本，朋友盘绝不进入用户会话的运势表述。
        _subject_now = (self._analysis_facts.get(user_id) or {}).get("subject", "self")
        if _subject_now == "other":
            self._pregen_instant.pop(user_id, None)  # 他人盘不消费秒回预生成
            self._save_bazi_records(result, _persist_birth,
                                    question=question, user_id=user_id,
                                    force_gender=force_gender)
            try:
                from src.engines.bazi_formatter import format_compact_card
                chart = format_compact_card(result, {
                    "year": year, "month": month, "day": day,
                    "hour": hour, "minute": minute,
                    "city": city, "gender": gender})
            except Exception:
                chart = ""
            reply = chart + "\n\n已为你朋友排出命盘，供展示参考；如需分析你自己的运势，随时告诉我。"
            return self._add_feedback_prompt(reply)

        # L5-2 修复（降级成本漏洞）：降级时 bazi 走「引擎排盘 + 精简文案」——
        # 排盘为确定性 0 成本（BaziEngine 本地计算）；RAG 检索 / AdaptiveAdvisor
        # 并行 LLM / 主分析 LLM / 秒回安抚 / 下文引导等最贵路径全部跳过。
        if self._downgraded.get(user_id, False):
            # R2-6：arch_raw 透传——lite 内部 _save_bazi_records 落库保留
            # 原始 y/m/d + calendar 标记；卡片展示仍用引擎参数（公历口径，
            # 与主路径展示一致）
            return self._do_bazi_lite(result, {
                "year": year, "month": month, "day": day,
                "hour": hour, "minute": minute,
                "city": city, "gender": gender,
            }, question=question, user_id=user_id, stream_cb=stream_cb,
                force_gender=force_gender, arch_raw=arch_raw)

        # 2. 秒回安抚（在LLM分析前生成，最终拼接到回复开头）
        # Task 2：优先取并行预生成结果（意图分析期间已完成），未就绪则同步兜底
        # L5-2（I-1）：降级链路禁用——预生成未提交、同步兜底也是 LLM 调用，
        # 均跳过（downgraded 时精简回复已足够，不再产生额外 Flash 调用）
        instant_reply = self._consume_pregen_instant(user_id)
        if not instant_reply and not self._downgraded.get(user_id, False):
            instant_reply = self._gen_instant_reply(result)

        # 3. 保存用户数据（dao 档案 / persons 多人档案 / 咨询记录 / 记忆画像）
        self._save_bazi_records(result, _persist_birth,
                                question=question, user_id=user_id,
                                force_gender=force_gender)

        # 4. P1-3: If gender is unknown, add instruction for gender-neutral language
        gender_note = ""
        if gender == "unknown":
            gender_note = "\n\n【注意：用户未提供性别，分析时请使用中性表述，如「命主」而非「他/她」，不要默认任何性别倾向】"
            question_with_gender = question + gender_note
        else:
            question_with_gender = question

        # 4. 检索古籍（Task 10 快路径：已建档/已存盘 → 跳过 RAG 预检索，
        #    LLM 需要古籍时经 tool_loop 的 search 工具兜底；引擎重排毫秒级保证新鲜）
        self._emit_stream_event(stream_cb, "thinking", "正在查阅古籍…")
        search_query = f"{result.day_master} {question}"
        if self._should_fastpath(user_id, {"year": year, "month": month, "day": day,
                                           "hour": hour, "minute": minute,
                                           "city": city, "gender": gender}):
            refs = []
            logger.info("fastpath: 用户 %s 有存量数据，跳过预检索", user_id)
        else:
            refs = self.retriever.search(search_query, category="bazi", top_k=15)

        # 4.5 并行启动「行动建议」生成（不依赖 analyze 输出，与深度分析并行，
        #     大幅降低整条请求延迟；LLM 调用在 worker 线程中执行，线程安全）
        _advice_future = None
        _advice_pool = None
        if HAS_ADVISOR_V2:
            try:
                import concurrent.futures
                _advice_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                _adv_key = getattr(self.llm, 'api_key', '') if self.llm else ''
                _advice_future = _advice_pool.submit(
                    lambda: AdaptiveAdvisor().generate(
                        result,
                        user_context=self._extract_user_context(question),
                        api_key=_adv_key,
                    )
                )
            except Exception:
                _advice_future = None
                if _advice_pool:
                    _advice_pool.shutdown(wait=False)
                    _advice_pool = None

        # 5. P3: Build personalized preference context for LLM injection
        pref_extra = self._get_personalized_context(user_id)

        # Task 10 快路径配套：注入用户已存排盘结果（防矛盾）。
        # 本步刚经 _save_bazi_records 落库，latest chart 即本次排盘；且
        # _persist_chart_result 写入的正是 result.day_master/geju/yongshen——
        # 故 T10 M-4（批次 2 B3）合并：不再二次 get_latest_chart，直接取
        # result 属性（消除一次 DB 读；门控 _should_fastpath 的读取保留，
        # 其语义是"排盘前"比对，与落库后的注入不共享状态）。
        # 字段缺失不注入该字段（getattr 兜底，不编造）；任何异常忽略不阻塞主流程。
        _chart_inject = self._build_chart_inject(result)

        # Phase 2: Scenario-aware structured report
        scenario_info = self._route_by_scenario(question_with_gender, user_id)
        # E2-1 卡片化：记录本轮实际使用的分析场景（yunshi 卡片判定依据——
        # 依赖"实际路由记录"而非关键词扫描，含"财运"的闲聊无记录不误判）
        if scenario_info:
            self._mark_card_turn(
                user_id, scenario=scenario_info.get("category", ""))
        # Task 2 计时覆盖异常路径：analyze 抛异常也要有 stage=main_analysis 输出
        # （宁多勿缺）——try/finally 保证成败都记录耗时
        _t0 = time.monotonic()
        try:
            if scenario_info:
                from src.llm.report_prompts import STRUCTURED_REPORT_PROMPT, SCENARIO_FOCUS_PROMPTS
                extra_prompt = STRUCTURED_REPORT_PROMPT
                if pref_extra:
                    extra_prompt += "\n\n" + pref_extra
                cat = scenario_info.get("category", "")
                if cat in SCENARIO_FOCUS_PROMPTS:
                    extra_prompt += "\n\n" + SCENARIO_FOCUS_PROMPTS[cat]
                if _chart_inject:
                    extra_prompt += "\n\n" + _chart_inject
                # Inject scenario prompt template into the question
                enhanced_question = (
                    scenario_info["prompt_template"]
                    + "\n\n用户的原始问题：\n"
                    + question
                )
                self._emit_stream_event(stream_cb, "thinking", "正在推演五行流年…")
                analysis = self.llm.analyze(
                    result, refs, enhanced_question,
                    extra_system_prompt=extra_prompt,
                )
            else:
                self._emit_stream_event(stream_cb, "thinking", "正在推演五行流年…")
                _extra = pref_extra if pref_extra else ""
                if _chart_inject:
                    _extra = (_extra + "\n\n" + _chart_inject) if _extra else _chart_inject
                analysis = self.llm.analyze(
                    result, refs, question_with_gender,
                    extra_system_prompt=_extra if _extra else None,
                )
        finally:
            logger.info("[timing] stage=main_analysis duration=%.1fs",
                        time.monotonic() - _t0)

        # k11-B/C（输出后校验器第二道，正文层）：主分析 LLM 正文称谓/神煞 scrub
        # ——prompt 有事实包与白名单条款（第一道），此处兜底（真幻觉 文昌贵人 案：
        # 纯规则去词，零 LLM）。advice 字段已在 AdaptiveAdvisor.generate 内 scrub。
        try:
            from src.utils.fact_guard import scrub_turn as _scrub
            _gen_g = getattr(result, "gender", "") or ""
            _gen_allow = list(getattr(result, "shensha", None) or [])
            _resp = getattr(analysis, "response", "")
            if _resp:
                analysis.response = _scrub(_resp, _gen_g, _gen_allow)
        except Exception:
            pass

        # 6. 生成命盘图片
        chart_url = ""
        try:
            from src.images.bazi_chart_html import BaziChartHTML
            gen = BaziChartHTML()
            # AI-generated personalized title
            personal_title = self._gen_chart_title(result)
            chart_path = gen.generate(result, title=personal_title)
            filename = os.path.basename(chart_path)
            chart_url = f"http://124.221.233.214/charts/{filename}"
        except Exception as e:
            import traceback
            import logging
            logging.getLogger(__name__).warning(f"Chart generation failed: {e}\n{traceback.format_exc()}")

        # 7. 行动建议 (V2: AI自适应) — 优先取并行生成结果
        self._emit_stream_event(stream_cb, "thinking", "正在整理行动建议…")
        advice_section = ""
        try:
            if _advice_future is not None:
                advice_data = _advice_future.result(timeout=60)
            elif HAS_ADVISOR_V2:
                # 并行启动失败时的兜底：同步生成
                advisor = AdaptiveAdvisor()
                # 从用户问题中提取处境信息（去掉日期时间部分后的剩余文本）
                user_context = self._extract_user_context(question)
                api_key = getattr(self.llm, 'api_key', '') if self.llm else ''
                advice_data = advisor.generate(
                    result, user_context=user_context,
                    api_key=api_key,
                )
            else:
                advice_data = None

            if advice_data and advice_data.get("actions"):
                actions = advice_data.get("actions", [])
                if actions:
                    lines = ["\n\n📌 AI 行动建议（基于命局趋势 + 当前处境生成）"]
                    icons = {"事业": "💼", "财运": "💰", "感情": "❤️", "健康": "🏥", "个人成长": "🌱"}
                    for a in actions:
                        cat = a.get("category", "")
                        advice = a.get("advice", "")
                        timing = a.get("timing", "")
                        confidence = a.get("confidence", "medium")
                        cf_icons = {"high": "✅", "medium": "📌", "low": "💡"}
                        cf = cf_icons.get(confidence, "📌")
                        icon = icons.get(cat, "")
                        timing_str = f"【{timing}】" if timing else ""
                        lines.append(f"{cf} {icon} {cat}：{advice} {timing_str}")
                    advice_section = "\n".join(lines)

                    # 名人对照已移除（2026-08-09 方案 v5 选 A：未问"像谁"不输出）

                    # 加每日小贴士
                    daily_tip = advice_data.get("daily_tip", "")
                    style_note = advice_data.get("style_notes", "")
                    if daily_tip:
                        advice_section += f"\n\n💡 今日贴士：{daily_tip}"
                    if style_note:
                        advice_section += f"\n✨ {style_note}"
            elif not HAS_ADVISOR_V2:
                # V1 fallback
                advisor = AdvisorEngine()
                advice = advisor.generate_advice(result)
                has_advice = any(items for items in advice.values())
                if has_advice:
                    lines = ["\n\n📌 行动建议（基于命局趋势推测，实际结果因人而异）"]
                    icons = {"事业": "💼", "财运": "💰", "感情": "❤️", "健康": "🏥", "个人成长": "🌱"}
                    for cat in ["事业", "财运", "感情", "健康", "个人成长"]:
                        items = advice.get(cat, [])
                        if items:
                            joined = "；".join(items[:2])
                            lines.append(f"{icons.get(cat, '')} {cat}（约70%参考概率）：{joined}")
                    advice_section = "\n".join(lines)
        except Exception:
            pass
        finally:
            if _advice_pool:
                _advice_pool.shutdown(wait=False)

        # 8. 详细排盘（替代旧的纯文字回复）
        try:
            from src.engines.bazi_formatter import format_compact_card
            birth_info = {"year": year, "month": month, "day": day, "hour": hour,
                          "minute": minute, "city": city, "gender": gender}
            chart = format_compact_card(result, birth_info)
        except Exception:
            chart = ""
        # R1-1（评测 T096 修复·确定性四柱展示）：本人排盘卡片附标准四柱行——
        # 卡片天干/地支分行展示（如 庚/午 分列）不产生连续干支文本，补一行
        # 「四柱：庚午 辛巳 乙酉 甲申」（与已存盘逐字一致，D2 终审同口径），
        # 使本人年柱以标准连续形式出现在回复中；第三方盘走上方确定性分支
        # 不含此行（朋友干支绝不入会话表述）。
        if getattr(result, "bazi", None):
            chart += f"\n📜 四柱：{' '.join(result.bazi)}"

        # 阶段 5·来源体系（方案 §3.0 ②）：意图路径排盘也标引擎来源"你的命盘"
        try:
            idx = self._alloc_citations(user_id, 1)
            self._append_citations(user_id, [make_citation(
                idx, "engine",
                f"你的命盘：八字 {' '.join(result.bazi)}，日主 {result.day_master}"
                f"（{year}年{month}月{day}日{gender}）",
                title="你的命盘", source="排盘引擎",
            )])
        except Exception:
            pass

        reply = chart + "\n\n" + analysis.response
        if advice_section:
            reply += advice_section
        if chart_url:
            reply += f"\n\n📊 命盘图片：{chart_url}"

        # 命例相似度分析已移除（2026-08-09 方案 v5 选 A：SimilarityEngine 停用，
        # 未问"像谁"不再输出命例对照）

        # k11-B/C：秒回安抚/下文引导同为 LLM 产物，拼接前 scrub（纯规则）
        try:
            from src.utils.fact_guard import scrub_turn as _scrub2
            _g2 = getattr(result, "gender", "") or ""
            _a2 = list(getattr(result, "shensha", None) or [])
            if instant_reply:
                instant_reply = _scrub2(instant_reply, _g2, _a2)
        except Exception:
            pass

        if instant_reply:
            reply = instant_reply + "\n\n---\n\n" + reply

        # 9. AI 生成下文引导（替代硬编码的「还想了解什么？」）
        followup = self._gen_followup_questions(result, question)
        # k11-B/C：引导语 scrub（须在生成之后）
        try:
            if followup:
                from src.utils.fact_guard import scrub_turn as _scrub3
                followup = _scrub3(followup, getattr(result, "gender", "") or "",
                                   list(getattr(result, "shensha", None) or []))
        except Exception:
            pass
        if followup:
            reply += "\n\n" + followup

        # 10. 反馈提示 (F3)
        # 版本页脚已移除（B3-2-C：用户反馈底部「解读版本/同一八字」技术性文字
        # 突兀且无意义；前端顶部静态免责行「内容由 AI 生成 · 仅供娱乐参考」保留）
        reply = self._add_feedback_prompt(reply)

        # 11. 记录对话记忆
        if self.memory:
            self.memory.add_interaction(user_id, question, reply, intent="bazi",
                                        key_data={"day_master": result.day_master,
                                                  "geju": getattr(result, 'geju', '')})

        return reply

    def _persist_chart_result(self, user_id: str, result, birth: dict,
                              subject: str = "self") -> None:
        """排盘结果落库 chart_records（重看 0 重跑，对话/工具排盘共用）。

        - subject=self：默认命主（已建档）挂 person_id，供重看盘按命主取档
        - subject=other：帮他人排盘也落库，但归属本人名下（person_id=None）
        落库失败仅告警，不阻塞排盘主流程。
        """
        chart_dao = getattr(self, "chart_dao", None)
        if not chart_dao:
            return
        try:
            person = None
            if subject != "other":
                try:
                    from src.storage.person_dao import PersonDAO
                    person = PersonDAO(self.dao.db_path).get_default_person(user_id)
                except Exception:
                    person = None
            self.chart_dao.save_chart(
                user_id, (person or {}).get("id") if person else None,
                {"year": birth["year"], "month": birth["month"],
                 "day": birth["day"], "hour": birth["hour"],
                 "minute": birth["minute"], "city": birth["city"],
                 "gender": birth["gender"],
                 # R2-5：calendar 由 birth 透传（默认 solar=零行为回退）——
                 # lunar 档案兜底排盘的行需带 lunar 标记（chart_records 是
                 # get_user_birth_profile ③ 级兜底源，行内日期是原始农历值，
                 # 消费方凭标记单点转公历）
                 "calendar": birth.get("calendar", "solar")},
                # R1-3（T064）：bazi 路径行为不变（result.bazi 恒有）；
                # getattr 兜底让 ZiweiResult（无 bazi 字段）落库不再
                # AttributeError——紫微路径落库行存在即满足重看 0 重跑。
                {"bazi": getattr(result, "bazi", []),
                 "day_master": getattr(result, "day_master", ""),
                 "wuxing": getattr(result, "wuxing", {}),
                 "shishen": getattr(result, "shishen", []),
                 "dayun": getattr(result, "dayun", []),
                 "liunian": getattr(result, "liunian", {}),
                 "liunian_full": getattr(result, "liunian_full", []),
                 "shensha": getattr(result, "shensha", []),
                 "geju": getattr(result, "geju", ""),
                 "yongshen": getattr(result, "yongshen", ""),
                 "nayin": getattr(result, "nayin", []),
                 "taiyuan": getattr(result, "taiyuan", ""),
                 "qiyun_detail": getattr(result, "qiyun_detail", None)})
        except Exception as e:
            logger.warning("chart_records 落库失败 user=%s: %s", user_id, e)

    def _save_bazi_records(self, result, birth: dict, question: str,
                           user_id: str, force_gender: bool = False) -> None:
        """八字结果落库（主路径与降级路径共用）：dao 档案 / persons 多人档案 /
        咨询记录 / 记忆画像。subject=other（帮他人排盘）不写本人档案与画像。

        force_gender（G1-C2）：用户明示性别纠正分支传 True，穿透到
        save_bazi_info 强制覆写记忆画像层冲突性别（权威档案本无条件双写，
        画像层 save_bazi_info 默认拒绝 男↔女 覆写——不穿透则画像层永久滞后）。
        """
        year, month, day = birth["year"], birth["month"], birth["day"]
        hour, minute, city, gender = (birth["hour"], birth["minute"],
                                      birth["city"], birth["gender"])
        _subject = (self._analysis_facts.get(user_id) or {}).get("subject", "self")
        _facts_this = self._analysis_facts.get(user_id) or {}
        if _subject != "other":
            _bazi_save = {
                "year": year, "month": month, "day": day,
                "hour": hour, "minute": minute,
                "city": city, "gender": gender,
                "bazi": result.bazi,
            }
            # R2-6：calendar 标记透传落库（仅 lunar 写显式键；solar/无标记
            # 不写 → 读取缺省 solar，与 R2-5 _tool_bazi 同口径）——lunar 来源
            # 的档案经 B1/B2/F2 排盘后 bazi_info 不被抹标（自毁式修复防范）
            if birth.get("calendar") == "lunar":
                _bazi_save["calendar"] = "lunar"
            self.dao.save_user_bazi(user_id, _bazi_save)
        # P2 多人档案：对话建档（subject=self 年份不同→新建命主N；other 按关系/姓名）
        # R2-6：calendar 标记透传（仅 lunar 写显式键，与 _bazi_save 同口径）——
        # _sync_person_profile → person_dao 全量替换 birth_enc，缺 calendar 键
        # 默认 'solar' 会把 lunar 标记抹掉（自毁式修复，R2-5 同款注释）。
        _person_sync = {
            "year": year, "month": month, "day": day,
            "hour": hour, "minute": minute,
            "city": city, "gender": gender,
        }
        if birth.get("calendar") == "lunar":
            _person_sync["calendar"] = "lunar"
        self._sync_person_profile(user_id, _person_sync,
                                  subject=_subject, facts=_facts_this,
                                  birth_ctx=question)
        self.dao.save_consultation(user_id, question, result)
        # 排盘结果落库 chart_records（重看 0 重跑；subject=other 也落库但归属本人名下）
        self._persist_chart_result(user_id, result, birth, _subject)

        # Phase 3: Save to user memory system
        # 阶段 5（方案 v5）：subject=other（帮他人排盘）不写入本人画像
        _subject = (self._analysis_facts.get(user_id) or {}).get("subject", "self")
        if self.memory_system and _subject != "other":
            # R2-6 Fix（画像/引擎口径统一）：本函数上方 dao/persons/chart 落
            # 库保留 lunar 原始 y/m/d + calendar 标记（存储=原始输入事实源）；
            # 画像层/L3 则是 LLM 上下文消费方（get_profile_summary →
            # format_birth_line 渲染「出生:…」行），口径必须与引擎实际排盘
            # 一致 = 公历——arch_raw（B1/B2/F2）流下 birth 载原始农历 y/m/d
            # + 'lunar' 标记，写画像层前单点 to_solar_date 转公历（与 R2-5
            # _tool_bazi 画像层写引擎实收公历值同口径；不 mutate birth）。
            # 转换失败 → 回落原始值 + warning（此时引擎本就按原始值排盘，
            # 画像层与引擎仍一致，语义同 _solarize_birth）。
            _prof_year, _prof_month, _prof_day = year, month, day
            if birth.get("calendar") == "lunar":
                _sol = to_solar_date(birth)
                if _sol is not None:
                    _prof_year, _prof_month, _prof_day = _sol
                else:
                    logger.warning(
                        "R2-6 画像层 lunar 转公历失败，画像层按原始值写入 "
                        "user=%s birth=%s-%s-%s", user_id, year, month, day)
            self.memory_system.save_bazi_info(user_id, {
                "year": _prof_year, "month": _prof_month, "day": _prof_day,
                "hour": hour, "minute": minute,
                "city": city, "gender": gender,
                "bazi": result.bazi,
                "day_master": getattr(result, "day_master", ""),
            }, subject=_subject, force_gender=force_gender)
            # L3（方案 §5.5 来源②）：八字 → profile 关键事实条目（同画像层公历口径）
            self._persist_l3_bazi(user_id, {
                "year": _prof_year, "month": _prof_month, "day": _prof_day,
                "hour": hour, "minute": minute,
                "city": city, "gender": gender,
                "bazi": result.bazi,
                "day_master": getattr(result, "day_master", ""),
            })
            # Extract topic from question
            user_context = self._extract_user_context(question)
            if user_context:
                self.memory_system.add_concern(user_id, user_context)
                self.memory_system.remember(user_id, "last_topic", user_context)

    def _do_bazi_lite(self, result, birth: dict, question: str, user_id: str,
                      stream_cb: Optional[Callable] = None,
                      force_gender: bool = False,
                      arch_raw: Optional[dict] = None) -> str:
        """降级链路八字：引擎排盘 + 精简文案（确定性 0 成本，不调任何 LLM）。

        L5-2 修复（降级成本漏洞）：原 _do_bazi_analysis 在降级时仍走
        RAG（retriever.search）+ AdaptiveAdvisor 并行 LLM + 主分析 LLM
        的最贵路径；本方法保留：排盘（确定性 0 成本）+ 命盘卡片 +
        规则要点文案 + 数据落库（不丢档案），跳过：RAG / advisor /
        主分析 LLM / 秒回安抚 / 下文引导（全部 LLM 调用）。
        arch_raw（R2-6）：透传给落库（与 _do_bazi_analysis 同口径，保留
        lunar 原始 y/m/d + 标记）；birth 仍为引擎参数（公历），仅用于展示。
        """
        # 数据落库与主路径同口径（subject=other 保护）；arch_raw → 原始值+标记
        _persist = birth
        if arch_raw is not None:
            _persist = dict(birth)
            _persist.update({"year": arch_raw.get("year", birth["year"]),
                             "month": arch_raw.get("month", birth["month"]),
                             "day": arch_raw.get("day", birth["day"]),
                             "calendar": "lunar"})
        self._save_bazi_records(result, _persist, question, user_id,
                                force_gender=force_gender)
        # 命盘卡片（确定性 0 成本）
        try:
            from src.engines.bazi_formatter import format_compact_card
            chart = format_compact_card(result, birth)
        except Exception:
            chart = ""
        # 规则要点文案（确定性 0 成本）
        reply = chart + "\n\n" + self._bazi_lite_summary(result)
        # 阶段 5·来源体系：排盘也标引擎来源（引用列表纯内存，无成本）
        try:
            idx = self._alloc_citations(user_id, 1)
            self._append_citations(user_id, [make_citation(
                idx, "engine",
                f"你的命盘：八字 {' '.join(result.bazi)}，日主 {result.day_master}"
                f"（{birth['year']}年{birth['month']}月{birth['day']}日{birth['gender']}）",
                title="你的命盘", source="排盘引擎",
            )])
        except Exception:
            pass
        # 反馈提示（静态文本；版本页脚已移除，B3-2-C）
        reply = self._add_feedback_prompt(reply)
        # 记录对话记忆（本地存储）
        if self.memory:
            self.memory.add_interaction(user_id, question, reply, intent="bazi",
                                        key_data={"day_master": result.day_master,
                                                  "geju": getattr(result, 'geju', '')})
        return reply

    def _bazi_lite_summary(self, result) -> str:
        """降级链路八字精简文案：纯规则要点（确定性 0 成本，不调 LLM）。

        与 format_compact_card 命盘卡片互补：补一段规则生成的日主强弱 /
        格局 / 用神 / 神煞文字要点，让降级用户也有可读的结论而非只有表格。
        """
        lines = ["🔍 命局要点"]
        dm = str(getattr(result, "day_master", "") or "")
        wx = getattr(result, "wuxing", {}) or {}
        if dm and wx:
            elem = _DM_WUXING.get(dm[0], "")
            own = wx.get(elem, 0)
            total = sum(wx.values()) or 1
            top = max(wx, key=wx.get)
            low = min(wx, key=wx.get)
            if elem:
                trend = "偏旺" if own * 5 >= total * 2 else "偏弱"
                lines.append(f"· 日主{dm}属{elem}，命局{elem}气{own}分（{trend}），"
                             f"五行为「{top}」最旺、「{low}」最弱")
        geju = str(getattr(result, "geju", "") or "")
        if geju:
            lines.append(f"· 格局：{geju}")
        yongshen = str(getattr(result, "yongshen", "") or "")
        if yongshen:
            lines.append(f"· 用神：{yongshen}（补益方向）")
        ss = getattr(result, "shensha", []) or []
        if ss:
            lines.append(f"· 神煞：{'、'.join(str(s) for s in ss[:5])}")
        lines.append("")
        lines.append("💡 今日额度已用尽，回复已精简；开通会员可解锁完整深度分析"
                     "（含流年、大运、行动建议）。")
        return "\n".join(lines)

    def _gen_chart_title(self, result) -> str:
        """Generate a short personalized title for the chart based on user's chart data."""
        try:
            dm = getattr(result, "day_master", "?")
            geju = getattr(result, "geju", "")
            yongshen = getattr(result, "yongshen", "")

            parts = [f"{dm}日主"]
            if geju:
                parts.append(geju)
            if yongshen:
                parts.append(f"用{yongshen}")

            # Find the most interesting aspect
            wuxing = getattr(result, "wuxing", {})
            if wuxing:
                max_wx = max(wuxing, key=wuxing.get)
                min_wx = min(wuxing, key=wuxing.get)
                if wuxing.get(min_wx, 0) == 0:
                    parts.append(f"补{min_wx}为先")

            shensha = getattr(result, "shensha", [])
            if shensha and "天乙贵人" in shensha:
                parts.append("贵人格")

            return " · ".join(parts[-4:])  # max 4 parts
        except Exception:
            return ""

    # ------------------------------------------------------------
    # AI 动态生成辅助方法
    # ------------------------------------------------------------

    def _quick_flash(self, prompt: str, max_tokens: int = 150, temperature: float = 0.7) -> str:
        """Helper: single Flash call with error handling.

        Bugfix: 改用 Anthropic 兼容端点 + thinking disabled（同 client.py），
        避免推理模型占满 max_tokens 导致空回复。
        """
        api_key = getattr(self.llm, 'api_key', '') if self.llm else ''
        if not api_key:
            return ""
        try:
            from src.llm.client import deepseek_anthropic_completion
            return deepseek_anthropic_completion(
                api_key,
                [{"role": "user", "content": prompt}],
                model="deepseek-flash",
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=20.0,
            )
        except Exception:
            return ""

    def gen_suggestions(self, user_id: str, question: str, reply: str) -> list:
        """v1.2 建议卡片：为刚完成的回复生成 2-3 个用户最可能追问的问题。

        - 轻量实现：单独一次 LLM 调用（max_tokens 小、timeout 8s，复用主模型）
        - 只在用户最后一条消息是提问时生成（is_question 宽松启发式）
        - 失败/超时/格式不合 → 返回 []（不影响主回复，前端无建议卡即降级）
        """
        q = (question or "").strip()
        if not q or not is_question(q):
            return []
        # L5-2（I-1）：降级链路禁用建议卡生成（独立 LLM 调用，降级不调）
        if self._downgraded.get(user_id, False):
            return []
        api_key = getattr(self.llm, 'api_key', '') if self.llm else ''
        if not isinstance(api_key, str) or not api_key:
            return []
        try:
            snippet = (reply or "").replace("\n", " ").strip()
            if len(snippet) > 240:
                snippet = snippet[:240] + "…"
            prompt = (
                "你是「易理明灯」的建议生成器，一款温暖的中文命理陪伴应用。\n"
                f"用户刚刚问了：「{q}」\n"
                f"你对用户的回复是：「{snippet}」\n\n"
                "请生成 2-3 个用户接下来最可能追问的问题。要求：\n"
                "1. 像用户自己会问出口的话，口语化、简短、自然（20 字以内）；\n"
                "2. 紧贴刚才的回复内容延伸（可以是命理话题，也可以是轻松的闲聊延伸）；\n"
                "3. 不要编号、不要引号、不要多余解释。\n"
                "直接输出，每行一个问题。"
            )
            from src.llm.client import deepseek_anthropic_completion
            raw = deepseek_anthropic_completion(
                api_key,
                [{"role": "user", "content": prompt}],
                model=getattr(self.llm, 'model', '') or "deepseek-flash",
                max_tokens=160, temperature=0.8, timeout=8.0,
            ) or ""
            out: list = []
            for line in raw.splitlines():
                s = line.strip()
                # 去掉可能出现的编号/符号前缀
                s = re.sub(r"^[-*\d.、\s]+", "", s).strip()
                s = s.strip('"\'“”「」')
                if not s or len(s) > 40:
                    continue
                if s not in out:
                    out.append(s)
                if len(out) >= 3:
                    break
            return out if len(out) >= 2 else []
        except Exception:
            return []  # 生成失败 → 无建议（前端不渲染建议卡，主回复不受影响）

    def _is_gender_correction(self, new_g: Optional[str],
                              old_g: Optional[str]) -> bool:
        """G1-C2/C2b（2026-08-29）：用户明示性别纠正判定——新提取性别与
        档案性别均为已确认值（男/女）且不一致 → True（视为用户明示纠正，
        允许 force_gender 强制覆写画像层）；任一 unknown/None 或一致 →
        False（静默数据冲突/tool 直排路径不扩大强制覆写面）。

        partial 分支（_handle_bazi 部分信息累积）与 parsed 直排路径
        （_handle_bazi 完整生辰消息）共用同一判定，不重复实现。
        R1-2（评测 T008 修复）：persons 档案 gender 存英文契约（male/
        female，与前端旧 genderCode 同源）→ 判定前归一中文（_GENDER_CN），
        否则「我是女孩儿」对 male 档案永不判定为纠正（只确认不重排）。
        """
        _n = _GENDER_CN.get(new_g, new_g)
        _o = _GENDER_CN.get(old_g, old_g)
        return (_n in ("男", "女") and _o in ("男", "女")
                and _n != _o)

    def _gen_gender_correction_ack(self, old_g: str, new_g: str) -> str:
        """G1（2026-08-29 P0-C）：性别纠正回执（固定文案，零 LLM——
        正确性路径确定性优先，不依赖降级/额度状态）。
        R1-2：old_g 可能为 persons 英文契约（male/female）→ 归一中文再拼装。"""
        _o = _GENDER_CN.get(old_g, old_g)
        _n = _GENDER_CN.get(new_g, new_g)
        return (f"我注意到档案里记录的是{_o}，已按您本次说的「{_n}」"
                f"重新排盘（大运方向已随之调整）。")

    def _gen_reuse_acknowledgment(self, msg: str, saved: dict,
                                  lite: bool = False) -> str:
        """Generate a brief acknowledgment when reusing saved bazi info.

        lite（L5-2 修复·降级成本）：降级链路不调 LLM，返回固定文案。
        """
        bazi_str = " ".join(saved.get("bazi", ["?"])[:4]) if saved.get("bazi") else ""
        if not bazi_str:
            return ""
        if lite:
            return "📌 已按你之前保存的出生信息排盘（今日额度用尽，回复已精简）。"
        prompt = (
            f"用户之前已提供过八字信息（{bazi_str}），现在用户问：「{msg}」。\n"
            "请用15字以内的现代中文，自然地告诉用户「基于你之前的八字信息来看...」。\n"
            "不要用「小友」「老夫」。直接返回一句话，不要引号不要JSON。"
        )
        return self._quick_flash(prompt, max_tokens=60)

    def _gen_info_collection_prompt(self, msg: str, lite: bool = False,
                                    known: Optional[dict] = None,
                                    missing: Optional[list] = None) -> str:
        """AI generates contextual info-collection prompt based on what user said.

        F2（2026-08-26）：known 非空 → 固定模板（零 LLM 调用）——先回显已
        确认项，再只问缺失项（回显是机械拼接，不需要创造性；降级路径同构）。
        known 为空/None → 原 prompt 与 lite 文案一字不改（LLM 引导同现有口径）。
        lite（L5-2 修复·降级成本）：降级链路不调 LLM，直接返回固定引导文案。
        """
        if known:
            _missing = missing or ["出生年份", "出生月日", "出生时辰",
                                   "出生城市", "性别"]
            return (f"好的，已记下：{_format_partial_echo(known, msg)}。"
                    f"再告诉我{'、'.join(_missing)}，就能给你排盘。")
        if lite:
            return ("好的，想帮你看看八字～请告诉我：\n"
                    "📅 出生年月日（阳历/阴历）\n⏰ 几点几分\n"
                    "📍 出生城市\n👤 性别（男/女，这个很重要，影响大运方向）\n\n"
                    "💡 示例：1990年5月20日 下午3点 北京 男")
        # P1-3: explicitly prompt for gender, don't default to male
        prompt = (
            f"用户说：「{msg}」，想了解八字命理但还没提供出生信息。\n"
            "请生成一段友善的引导，请用户提供：出生年月日时、出生地、性别。\n"
            "提醒用户性别很重要（影响大运走向），尽量明确告知。\n"
            "风格：像朋友一样自然，不要死板。给出一个具体示例。\n"
            "用现代中文，不要用「小友」「老夫」。50-80字。\n"
            "直接返回文本，不要引号不要JSON。"
        )
        result = self._quick_flash(prompt, max_tokens=120)
        return result or ("好的，想帮你看看八字～请告诉我：\n"
                          "📅 出生年月日（阳历/阴历）\n⏰ 几点几分\n"
                          "📍 出生城市\n👤 性别（男/女，这个很重要，影响大运方向）\n\n"
                          "💡 示例：1990年5月20日 下午3点 北京 男")

    def _gen_followup_questions(self, result, question: str) -> str:
        """AI generates 1-2 natural follow-up questions after analysis."""
        bazi = getattr(result, "bazi", None)
        dm = getattr(result, "day_master", "")
        geju = getattr(result, "geju", "")
        bazi_str = " ".join(bazi[:4]) if isinstance(bazi, (list, tuple)) and len(bazi) >= 4 else ""

        if not bazi_str:
            return ""

        prompt = (
            f"刚给用户做了八字分析：{bazi_str}，日主{dm}，格局{geju}。\n"
            f"用户关心：「{question}」。\n"
            "请生成1-2个自然的下文引导问题，帮用户继续深入探索。\n"
            "风格：像朋友聊天一样自然，不要机械。例如：\n"
            "- 「要不要帮你看看下个月的整体运势？」\n"
            "- 「想了解一下你的桃花运在哪个大运最旺吗？」\n"
            "用现代中文，不要用「小友」「老夫」。15-30字。\n"
            "直接返回文本，不要引号不要JSON。格式：💬 还想了解：..."
        )
        return self._quick_flash(prompt, max_tokens=120, temperature=0.9)

    # ------------------------------------------------------------
    # 秒回安抚 (Instant Emotional Reply) — AI-generated
    # ------------------------------------------------------------

    def _gen_instant_reply(self, result) -> str:
        """Generate personalized instant reply via AI — no hardcoded templates.

        Uses a quick Flash call to find the most interesting aspect of the
        user's chart and craft a warm, unique opener. Runs in parallel with
        the deep Pro model analysis, so latency is hidden.

        Task 2：无论同步生成还是预生成线程路径都记录本阶段实际耗时。
        """
        _t0 = time.monotonic()
        try:
            bazi = getattr(result, "bazi", None)
            if not isinstance(bazi, (list, tuple)) or len(bazi) < 4:
                return ""
            bazi_str = " ".join(str(p) for p in bazi[:4])
            dm = getattr(result, "day_master", "")
            if not isinstance(dm, str) or not dm:
                return ""

            # Collect available chart data for the AI
            chart_info_parts = [f"八字: {bazi_str}", f"日主: {dm}"]
            shensha = getattr(result, "shensha", None)
            if isinstance(shensha, (list, tuple)) and shensha:
                chart_info_parts.append(f"神煞: {', '.join(str(s) for s in shensha[:5])}")
            geju = getattr(result, "geju", "")
            if isinstance(geju, str) and geju:
                chart_info_parts.append(f"格局: {geju}")
            wuxing = getattr(result, "wuxing", None)
            if isinstance(wuxing, dict) and wuxing:
                wuxing_str = " ".join(f"{k}{v}" for k, v in wuxing.items())
                chart_info_parts.append(f"五行: {wuxing_str}")
            chart_info = "，".join(chart_info_parts)
            # k11-B：秒回安抚同为 LLM 产物（报告 §五#3 低危同类）——补性别与
            # 称谓规则；输出后另有 _do_bazi_analysis 拼装前 scrub 兜底
            _ig = str(getattr(result, "gender", "") or "").strip()
            _gender_note = ""
            if _ig in ("男", "女", "male", "female"):
                _gender_note = f"用户性别：{'男' if _ig in ('男', 'male') else '女'}；"
                if _ig in ("男", "male"):
                    _gender_note += ("使用中性称谓（你/朋友），"
                                     "禁止女性称谓与闺蜜口吻（姐妹/亲爱的等）。")

            prompt = (
                f"用户的命盘排出来了：{chart_info}。\n"
                "请生成一段30-50字的个性化开场白：\n"
                "1. 先展示八字和日主\n"
                "2. 从命盘中找一个最亮眼的亮点（神煞、格局、五行特色等），用温暖现代的语气说出来\n"
                "3. 风格：像朋友发来的消息，不要用'小友''老夫'等老气称呼\n"
                + (_gender_note + "\n" if _gender_note else "")
                + "4. 结尾用🌟\n"
                "直接返回开场白文本，不要引号不要JSON。"
            )

            text = self._quick_flash(prompt, max_tokens=150, temperature=0.8)
            if text:
                logger.info("[timing] stage=instant duration=%.1fs",
                            time.monotonic() - _t0)
                return text
        except Exception:
            pass

        # Fallback: minimal template (only when AI unavailable)
        try:
            bazi = getattr(result, "bazi", None)
            dm = getattr(result, "day_master", "")
            if bazi and dm:
                bazi_str = " ".join(str(p) for p in bazi[:4])
                logger.info("[timing] stage=instant duration=%.1fs",
                            time.monotonic() - _t0)
                return f"命盘已排出【{bazi_str}】，日主{dm}。正在深度推演中~ 🌟"
        except Exception:
            pass
        logger.info("[timing] stage=instant duration=%.1fs",
                    time.monotonic() - _t0)
        return ""

    # ============================================================
    # 紫微斗数 (Ziwei)
    # ============================================================

    def _handle_ziwei(self, msg: str, user_id: str, stream_cb: Optional[Callable] = None) -> str:
        """处理紫微斗数请求 - 与八字相同的信息收集"""
        parsed = self._extract_bazi_info(msg)

        if parsed is None:
            # R1-3（T064 同源修复）：紫微重看盘直读统一走 persons 档案优先
            # 读取（_get_user_birth_profile，与 bazi/calendar/hourly 同源——
            # 原 get_user_bazi 只读 users.bazi_info，persons 建档用户漏读
            # → 重复反问出生信息）。档内 gender 为英文契约时引擎侧按
            # 中文口径归一（_GENDER_CN，与 bazi 路径一致）。
            saved = self._get_user_birth_profile(user_id)
            if saved:
                _g = _GENDER_CN.get(
                    str(saved.get("gender") or "").strip().lower(), "unknown")
                return self._do_ziwei_analysis(
                    saved["year"], saved["month"], saved["day"],
                    saved["hour"], saved["minute"], saved["city"],
                    _g, msg, user_id, stream_cb=stream_cb,
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
            stream_cb=stream_cb,
        )

    def _do_ziwei_analysis(
        self, year, month, day, hour, minute, city, gender, question, user_id,
        stream_cb: Optional[Callable] = None,
    ) -> str:
        """执行紫微斗数分析"""
        try:
            # Task 3：思考步骤按真实工作里程碑渐进发出（首条开工即发）
            self._emit_stream_event(stream_cb, "thinking", "我在排紫微斗数盘…")
            result = self.ziwei_engine.calculate(year, month, day, hour, minute, city, gender)
            # E2-1 卡片化（批次 2 P1）：紫微引擎完成排盘（ziwei 卡片判定依据；
            # 引擎异常走 except 不落标记 → 宁漏勿误）
            self._mark_card_turn(user_id, ziwei=True)
            self.dao.save_consultation(user_id, question, result, intent="ziwei")
            # R1-3（T064 修复·紫微落库补全）：紫微路径此前只 save_consultation，
            # persons/chart_records 零写——R1-3 意图强路由把紫微请求从 bazi
            # 路径接管后，L4 契约（persons_created + chart_records_created）
            # 必须由本路径自己满足（与 bazi 路径同源：
            # _sync_person_profile 建档 + _persist_chart_result 落盘）。
            # ZiweiResult 无 bazi/dayun 等八字字段 → _persist_chart_result
            # 的 getattr 兜底存空（chart_records 行存在即满足重看 0 重跑）。
            try:
                _z_birth = {"year": year, "month": month, "day": day,
                            "hour": hour, "minute": minute,
                            "city": city or "", "gender": gender}
                self._sync_person_profile(user_id, _z_birth, birth_ctx=question)
                self._persist_chart_result(user_id, result, _z_birth)
            except Exception as e:
                logger.warning("紫微排盘落库失败（不阻塞主流程）: %s", e)
            search_query = f"紫微斗数 {result.ming_gong} {question}"
            self._emit_stream_event(stream_cb, "thinking", "正在查阅古籍…")
            refs = self.retriever.search(search_query, category="ziwei", top_k=15)
            if not refs:
                refs = self.retriever.search(search_query, top_k=15)  # fallback: any category
            chart_str = self._format_ziwei_chart(result)
            # 阶段 5·来源体系（方案 §3.0）：紫微命盘 → 引擎来源；检索古籍 → book 来源
            try:
                self._register_engine_citation(
                    user_id,
                    f"紫微斗数命盘：命宫{result.ming_gong} 身宫{result.shen_gong} "
                    f"五行局{result.wuxing_ju}，四化："
                    f"{' '.join(f'{k}:{v}' for k, v in result.sihua.items() if v) or '无'}",
                    title="你的紫微命盘", source="紫微排盘引擎",
                )
                self._register_book_citations(user_id, refs, title="紫微 · 古籍参考")
            except Exception:
                pass
            self._emit_stream_event(stream_cb, "thinking", "逐宫推演十二宫…")
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

    def _handle_liuyao(self, msg: str, user_id: str, stream_cb: Optional[Callable] = None) -> str:
        """处理六爻占卜请求"""
        # 提取所问之事
        question = self._get_question_after_keywords(msg, [
            "六爻", "占卜", "起卦", "起一卦", "卜卦", "算卦", "看看", "帮我",
        ])
        if not question:
            question = "一般运势"
        return self._do_liuyao_analysis(question, msg, user_id, stream_cb=stream_cb)

    def _do_liuyao_analysis(self, question: str, original_msg: str, user_id: str,
                            stream_cb: Optional[Callable] = None) -> str:
        """执行六爻占卜"""
        try:
            # Task 3：思考步骤按真实工作里程碑渐进发出（首条开工即发）
            self._emit_stream_event(stream_cb, "thinking", "我在起卦…")
            result = self.liuyao_engine.cast(method="random", question=question)
            # E2-1 卡片化（批次 2 P1）：六爻引擎完成起卦（liuyao 卡片判定依据）
            self._mark_card_turn(user_id, liuyao=True)
            self.dao.save_consultation(user_id, original_msg, result, intent="liuyao")
            self._emit_stream_event(stream_cb, "thinking", "正在查阅古籍…")
            refs = self.retriever.search(
                f"六爻 {result.original_hexagram} {question}", category="yijing", top_k=15)
            if not refs:
                refs = self.retriever.search(f"六爻 {question}", top_k=15)
            chart_str = self._format_liuyao_chart(result)
            # 阶段 5·来源体系（方案 §3.0）：卦象 → 引擎来源；检索古籍 → book 来源
            try:
                var = ""
                if getattr(result, "changed_hexagram", "") and result.changed_hexagram != result.original_hexagram:
                    var = f"；变卦{result.changed_hexagram}"
                self._register_engine_citation(
                    user_id,
                    f"六爻卦象：所问之事「{result.question or '一般运势'}」，"
                    f"本卦{result.original_hexagram}（{result.palace}宫/{result.palace_wuxing}）{var}",
                    title="六爻卦象", source="六爻起卦引擎",
                )
                self._register_book_citations(user_id, refs, title="易经 · 古籍参考")
            except Exception:
                pass
            self._emit_stream_event(stream_cb, "thinking", "推演卦象变化…")
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

    def _handle_fengshui(self, msg: str, user_id: str, stream_cb: Optional[Callable] = None) -> str:
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

        return self._do_fengshui_analysis(direction, birth_year, gender, msg, user_id,
                                          stream_cb=stream_cb)

    def _do_fengshui_analysis(
        self, direction, birth_year, gender, question, user_id,
        stream_cb: Optional[Callable] = None,
    ) -> str:
        """执行风水分析"""
        # Task 3：思考步骤按真实工作里程碑渐进发出（首条开工即发）
        # 1. 分析
        self._emit_stream_event(stream_cb, "thinking", "我在勘察风水格局…")
        result = self.fengshui_engine.analyze(
            direction=direction,
            year_built=birth_year,
            birth_year=birth_year,
            gender=gender,
        )
        # E2-1 卡片化（批次 2 P1）：风水引擎完成分析（fengshui 卡片判定依据；
        # 引擎异常向上冒泡 → process 出口 ⚠️ 降级文案，不落标记 → 宁漏勿误）
        self._mark_card_turn(user_id, fengshui=True)

        # 2. 保存
        self.dao.save_consultation(user_id, question, result, intent="fengshui")

        # 3. 检索古籍
        search_query = f"风水 {result.house_gua} {question}"
        self._emit_stream_event(stream_cb, "thinking", "正在查阅古籍…")
        refs = self.retriever.search(search_query, category="fengshui", top_k=15)

        # 阶段 5·来源体系（方案 §3.0）：风水分析 → 引擎来源；检索古籍 → book 来源
        try:
            self._register_engine_citation(
                user_id,
                f"风水分析：宅卦{result.house_gua}，当前{result.period}运"
                + (f"，命卦{result.person_gua}" if result.person_gua else ""),
                title="风水分析结果", source="风水引擎",
            )
            self._register_book_citations(user_id, refs, title="风水 · 古籍参考")
        except Exception:
            pass

        # 4. LLM分析（带错误处理）
        try:
            chart_str = self._format_fengshui_chart(result)
            self._emit_stream_event(stream_cb, "thinking", "结合五行方位分析…")
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

    def _handle_mianxiang(self, msg: str, user_id: str, stream_cb: Optional[Callable] = None) -> str:
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

        return self._do_mianxiang_analysis(description, msg, user_id, stream_cb=stream_cb)

    def _do_mianxiang_analysis(self, description: str, original_msg: str, user_id: str,
                               stream_cb: Optional[Callable] = None) -> str:
        """执行面相分析"""
        # Task 3：思考步骤按真实工作里程碑渐进发出（首条开工即发）
        # 1. 面相分析
        self._emit_stream_event(stream_cb, "thinking", "我在端详你的面相…")
        result = self.mianxiang_engine.analyze(description=description)
        # E2-1 卡片化（批次 2 P1）：面相引擎完成分析（mianxiang 卡片判定依据；
        # 引擎异常向上冒泡 → ⚠️ 降级文案，不落标记 → 宁漏勿误）
        self._mark_card_turn(user_id, mianxiang=True)

        # 2. 保存
        self.dao.save_consultation(user_id, original_msg, result, intent="mianxiang")

        # 3. 检索古籍
        self._emit_stream_event(stream_cb, "thinking", "正在查阅古籍…")
        refs = self.retriever.search(
            f"面相 {result.face_type} {description}",
            category="mianxiang", top_k=15,
        )

        # 阶段 5·来源体系（方案 §3.0）：面相分析 → 引擎来源；检索古籍 → book 来源
        try:
            self._register_engine_citation(
                user_id,
                f"面相分析：{result.face_type}，三停："
                f"{' '.join(f'{k}{v}' for k, v in result.three_zones.items())}",
                title="面相分析结果", source="面相引擎",
            )
            self._register_book_citations(user_id, refs, title="相学 · 古籍参考")
        except Exception:
            pass

        # 4. LLM分析
        chart_str = self._format_mianxiang_chart(result)
        self._emit_stream_event(stream_cb, "thinking", "结合五宫五行分析…")
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
    # 灵签签文释义（T056，k38）——签库单一事实源直读（0 LLM，0 编造）
    # ============================================================
    # 问「关帝灵签第三签是什么意思」类**签文知识**问题：确定性从签库
    # （`src/api/qian.QIAN_KINDS`，与 /api/qian 同一事实源、同一份 json）取签卡
    # 作答。修前无此路由：请求被 LLM 判成 advisor → `_handle_advisor` 无档案
    # 分支「想为你生成专属建议，需要先了解你的命盘哦～」死胡同（K5 回归实锤：
    # 回复连「签」字都没有）。
    #
    # 命中三条件缺一不可（宁可落自由问答，也**不猜**签种/签号给错签诗）：
    #   ① 签文语境词 + ② 明确签种词（关帝/观音/玄武山；未写签种但写「灵签」
    #      → 原版 8 支签库「灵签原版」）+ ③ 可解析且在该签种库内的签号
    #     （阿拉伯或中文数字：「第3签」「第三签」）。
    # 签种不明（裸「第三签是什么意思」）→ None（不路由）——猜错签种等于给错
    # 签诗，比不回答更糟。
    _QIAN_CONTEXT_RE = re.compile(
        r"灵签|关帝签|关帝灵签|观音签|玄武山|签文|解签|签诗|签意|签号")
    _QIAN_NO_RE = re.compile(
        r"第\s*([0-9]{1,3}|[一二三四五六七八九十百零〇]{1,4})\s*[签籤]")
    _QIAN_KIND_WORDS = (("关帝", "guandi"), ("观音", "guanyin"),
                        ("玄武山", "xuanwushan"))
    _QIAN_DISCLAIMER = "（签文以签库原文为准，只作心意，不作断言）"

    def _answer_qian_meaning(self, msg: str) -> Optional[str]:
        """签文释义直读（0 LLM 0 编造）：签卡取自签库单一事实源 QIAN_KINDS。

        命中条件见上方区块注释；任一不满足 → None（调用方走全流程自由问答）。
        输出只包含签库原文字段（签号/等第/签诗/解曰/所求）+ 一行来源口径声明，
        绝不由模型补写签诗或等第（K5 红线：「不得编造签诗/等第」）。
        """
        try:
            if not msg or not self._QIAN_CONTEXT_RE.search(msg):
                return None
            m = self._QIAN_NO_RE.search(msg)
            if not m:
                return None
            no = _parse_cn_num(m.group(1))
            if no is None or no <= 0:
                return None
            kind = "original"
            for word, k in self._QIAN_KIND_WORDS:
                if word in msg:
                    kind = k
                    break
            from src.api.qian import KIND_NAMES, QIAN_KINDS
            cards = QIAN_KINDS.get(kind) or []
            card = next((c for c in cards if c.get("no") == no), None)
            if card is None:
                return None  # 该签种无此签号 → 不猜，落全流程
            head = f"{KIND_NAMES.get(kind, '灵签')}第{no}签"
            if card.get("jx"):
                head += f"（{card['jx']}）"
            lines = [head]
            poem = card.get("poem") or []
            if poem:
                lines.append("签诗：" + " ".join(poem))
            if card.get("jie"):
                lines.append("解曰：" + str(card["jie"]))
            if card.get("suo"):
                lines.append("所求：" + str(card["suo"]))
            lines.append(self._QIAN_DISCLAIMER)
            return "\n".join(lines)
        except Exception:
            return None  # 签库不可用/结构变更 → 不路由（fail-open 走全流程）

    # ============================================================
    # 择日 (Zeri)
    # ============================================================

    def _handle_zeri(self, msg: str, user_id: str, stream_cb: Optional[Callable] = None) -> str:
        """处理择日请求"""
        # R1-3（评测 T100 修复·择日额度门）：意图引擎路径此前不受额度门控
        # （D4 记录边界：额度门只在工具执行器路径）→ 免费额度用尽的用户
        # 走意图路径仍可无限调用择日引擎。现在补门（与 _tool_zeri 同文案，
        # 只读不写 → memberships 零写入红线延续）：额度用尽 → 引导会员，
        # 不调引擎、不扣额度。plan=free 的额度门触发前 _consume_quota 会把
        # used 重置为 1（use_quota 既有语义）→ 本门放行走完整择日分析
        # （契约「引导会员或正常产出」双分支均合规）。
        remaining, is_limited = self._check_quota(user_id)
        if is_limited and remaining <= 0:
            return ("你今天的免费额度已用完。成为会员即可无限畅聊，"
                    "基础版仅需 19.9 元/月。回复「会员」了解更多升级方案。")

        date_info = self._extract_date(msg)
        purpose = self._extract_purpose(msg)

        if not date_info:
            # k38（T035）月范围多日推荐：场景 + **明确年月锚**（"2026年10月搬家，
            # 帮我挑几个好日子"）→ 引擎 Top3 多日吉日卡（0 LLM，见下方方法）。
            # 与 D5 负例契约分工：只有相对窗口词（下个月/下周）而无明确年月的
            # 请求仍走下方确定性「日期引导文案」（T033 回归保护，不得硬跑引擎）；
            # 明确到年月 = 用户已给定时间范围 → 直接挑日（工具路径 `_tool_zeri`
            # 早有此能力，意图路径此前缺失 → T035 实测落到建档引导）。
            _ym = self._YEAR_MONTH_ANCHOR_RE.search(msg)
            _scene = self._extract_zeri_scene(msg)
            if _ym and _scene:
                _ym_reply = self._do_zeri_range_analysis(
                    int(_ym.group(1)), int(_ym.group(2)), _scene, msg, user_id)
                if _ym_reply:
                    return _ym_reply
            return """请告诉我您想查询的日期和用途：

📅 日期：哪一年哪一天？
🎯 用途：用于什么？

💡 示例1：2026年8月15日适合结婚吗？
💡 示例2：我要在2026年10月1日搬家，这天好吗？"""

        return self._do_zeri_analysis(date_info, purpose, msg, user_id, stream_cb=stream_cb)

    # 「YYYY年M月」年月锚（后不接日/号 = 月范围请求；接了日的完整日期由
    # `_extract_date` 先走单日分析）
    _YEAR_MONTH_ANCHOR_RE = re.compile(
        r'(\d{4})\s*年\s*(\d{1,2})\s*月(?!\s*\d{1,2}\s*[日号])')

    def _do_zeri_range_analysis(self, year: int, month: int, scene: str,
                                msg: str, user_id: str) -> Optional[str]:
        """月范围多日择日（0 LLM）：既有引擎 `select_lucky_days` Top3 → 中文日期卡。

        - 复用工具路径同款引擎调用（逐日扫描 + 冲煞排除 + 三层评分 + 周末偏好），
          不新起引擎、不新起文案口径；
        - **不走 `_execute_tool_call`**（与 R1-2 直调 `_tool_hehun` 同口径）：本方法
          是意图引擎路径的确定性产物，L1 契约「引擎域零工具调用」不得被破；
          额度门由 `_handle_zeri` 入口统一把关（不重复扣减、不重复写 memberships）；
        - 任何失败（引擎异常/窗口无合格吉日/形状异常）→ None，调用方回落
          确定性日期引导文案（fail-open，不劣于现状）。
        """
        try:
            if self.zeri_engine is None:
                return None
            import calendar as _calendar
            from datetime import date as _date
            start = _date(year, month, 1)
            end = _date(year, month, _calendar.monthrange(year, month)[1])
            user_bazi = self._map_user_bazi_for_zeri(user_id)
            res = self.zeri_engine.select_lucky_days(
                scene=scene, start_date=start.isoformat(),
                end_date=end.isoformat(), user_bazi=user_bazi,
                prefer_weekend=True)
            if not isinstance(res, dict):
                return None
            cards = res.get("cards") or []
            if not cards:
                return None
            self._mark_card_turn(user_id, zeri=True)
            lines = [f"【择日】场景：{scene}｜时间范围：{year}年{month}月",
                     f"共扫描 {res.get('scanned', 0)} 天，"
                     f"为您挑出 {len(cards)} 个吉日：", ""]
            marks = "①②③"
            for i, c in enumerate(cards[:3]):
                raw = str(getattr(c, "date", "") or "")
                try:
                    _cn = f"{int(raw[5:7])}月{int(raw[8:10])}日"
                except Exception:
                    _cn = raw
                lines.append(f"{marks[i]} {raw}（{_cn}）"
                             f"{getattr(c, 'lunar_text', '') or ''}")
                # k26：宜/忌各自为空即整行不渲染（空忌日不得输出悬空「忌：」）
                lines.extend(_yi_ji_render_lines(getattr(c, "yi", None) or [],
                                                 getattr(c, "ji", None) or []))
                lines.append(f"吉时：{getattr(c, 'jishi', '') or '—'}｜"
                             f"喜神：{getattr(c, 'xi_fangwei', '') or '—'}｜"
                             f"财神：{getattr(c, 'cai_fangwei', '') or '—'}")
                lines.append(f"理由：{getattr(c, 'reason_source', '') or '—'}"
                             f"（总分{getattr(c, 'total', 0)}）")
                lines.append("")
            if res.get("suggest_wider"):
                lines.append("注：本时间范围内合格吉日不足 3 天，"
                             "可告诉我更宽的时间范围，我再帮您挑选。")
            lines.append("您选哪一个？选好后我帮您生成办事清单。")
            return "\n".join(lines)
        except Exception:
            return None

    def _do_zeri_analysis(self, date_info, purpose, question, user_id,
                          stream_cb: Optional[Callable] = None) -> str:
        """执行择日分析"""
        year, month, day = date_info

        # Task 3：思考步骤按真实工作里程碑渐进发出（首条开工即发）
        # 1. 择日
        self._emit_stream_event(stream_cb, "thinking", "我在翻黄历择吉…")
        result = self.zeri_engine.select(year, month, day, purpose=purpose)
        # E2-1 卡片化：完成择日引擎分析（zeri 卡片判定依据）
        self._mark_card_turn(user_id, zeri=True)

        # 2. 保存
        self.dao.save_consultation(user_id, question, result, intent="zeri")

        # 3. 检索古籍
        search_query = f"择日 {result.jianchu} {purpose or '吉日'}"
        self._emit_stream_event(stream_cb, "thinking", "正在查阅古籍…")
        refs = self.retriever.search(search_query, category="zeri", top_k=15)

        # 阶段 5·来源体系（方案 §3.0）：择日结果 → 引擎来源；检索古籍 → book 来源
        try:
            _yi_ji = _yi_ji_render_lines(result.yi, result.ji)
            self._register_engine_citation(
                user_id,
                f"择日分析：{year}年{month}月{day}日，建除十二神{result.jianchu}，"
                f"二十八宿{result.ershibaxiu}（{result.xiu_jixiong}）"
                + (f"，{'；'.join(_yi_ji)}" if _yi_ji else ""),
                title="择日分析结果", source="择日引擎",
            )
            self._register_book_citations(user_id, refs, title="择日 · 古籍参考")
        except Exception:
            pass

        # 4. LLM分析
        chart_str = self._format_zeri_chart(result, year, month, day)
        self._emit_stream_event(stream_cb, "thinking", "比对吉凶宜忌…")
        analysis = self.llm.analyze(chart_str, refs, question)

        # k40（T034）：意图路径与工具路径**同一份引擎数据、同一渲染实现**
        # （`_yi_ji_render_lines`，与 `_tool_zeri`/`_format_zeri_chart` 同源）
        # ——改前意图路径只把引擎结果喂给 LLM 散文，宜忌条目由 LLM 自行取舍，
        # 实测「…挺不错的日子来搬家…可行的哦」零宜忌字面（撞数据一致性铁律：
        # 同一引擎数据的两个通道渲染不一致）。这里把确定性宜忌行拼进草稿
        # （润色提示词同样要求保留宜忌条目），并在 process 出口幂等重挂
        # （润色/工具循环把整行吃掉时补回，见 `_zeri_yi_ji_acks` 消费点）——
        # 与 `_gender_acks`（T008 回执重挂）同一范式。
        # k40 返工（Minor-2）：草稿拼接**逐行判存在**——LLM 若已逐字复述某行
        # （「宜：入宅、祭祀…」），不再追加造成重复行（出口重挂本就逐行幂等，
        # 这里补齐草稿侧的同一口径）。
        _yi_ji_lines = _yi_ji_render_lines(result.yi, result.ji)
        if _yi_ji_lines:
            try:
                self._zeri_yi_ji_acks = getattr(
                    self, "_zeri_yi_ji_acks", None) or {}
                self._zeri_yi_ji_acks[user_id] = _yi_ji_lines
            except Exception:
                pass  # 暂存失败 → 不影响主链（草稿仍含宜忌行）
        _resp = analysis.response or ""
        _missing_lines = [ln for ln in _yi_ji_lines if ln not in _resp]
        if _missing_lines:
            _resp = _resp.rstrip() + "\n\n" + "\n".join(_missing_lines)
        return _resp

    def _format_zeri_chart(self, r: ZeriResult, year: int, month: int, day: int) -> str:
        """格式化择日结果为文本"""
        lines = ["择日分析结果："]
        lines.append(f"日期：{year}年{month}月{day}日")
        lines.append(f"建除十二神：{r.jianchu}")
        lines.append(f"二十八宿：{r.ershibaxiu}（{r.xiu_jixiong}）")
        lines.append(f"冲：{r.chong}")
        # k26：空宜/空忌整行不渲染（2026-02-10 忌=[] 实测：旧代码输出「忌：」）
        lines.extend(_yi_ji_render_lines(r.yi, r.ji))
        lines.append(f"综合判定：{r.overall}")
        return '\n'.join(lines)

    # ============================================================
    # 奇门遁甲 (Qimen) - 引擎排盘 + RAG + LLM
    # ============================================================

    def _handle_qimen(self, msg: str, user_id: str, stream_cb: Optional[Callable] = None) -> str:
        """处理奇门遁甲咨询 - 使用QimenEngine完整排盘 + LLM用神解读"""
        # 提取用户所问之事
        question = self._get_question_after_keywords(msg, [
            "奇门", "遁甲", "奇门遁甲", "看看", "帮我",
        ])
        if not question:
            question = "奇门遁甲运筹"

        # 1. 提取日期时间
        from datetime import datetime
        date_info = self._extract_date(msg)
        if date_info:
            year, month, day = date_info
        else:
            # 无日期信息 — 用当前时间
            now = datetime.now()
            year, month, day = now.year, now.month, now.day

        # 提取时辰
        hour = 12  # 默认午时
        hour_match = re.search(r'(\d{1,2})\s*[点时:：]', msg)
        if hour_match:
            raw_hour = int(hour_match.group(1))
            # 如果用户写"下午3点"之类，做偏移
            if re.search(r'(下午|晚上|傍晚|夜间|夜里)', msg) and 1 <= raw_hour <= 12:
                hour = raw_hour + 12
            else:
                hour = raw_hour
        else:
            shichen_match = re.search(r'([子丑寅卯辰巳午未申酉戌亥])时', msg)
            if shichen_match:
                hour = CHINESE_HOUR_MAP.get(shichen_match.group(1), 12)

        # Null guard — return early if engine is not injected
        if self.qimen_engine is None:
            return "⚠️ 奇门遁甲排盘功能暂时不可用，请稍后再试。"

        # Task 3：思考步骤按真实工作里程碑渐进发出（首条开工即发）
        # 2. 排盘
        self._emit_stream_event(stream_cb, "thinking", "我在起奇门局…")
        result = self.qimen_engine.calculate(year, month, day, hour)
        # E2-1 卡片化（批次 2 P1）：奇门引擎完成排盘（qimen 卡片判定依据；
        # 引擎异常向上冒泡 → ⚠️ 降级文案，不落标记 → 宁漏勿误）
        self._mark_card_turn(user_id, qimen=True)

        # 3. 格式化命盘
        chart_str = self.qimen_engine.print_chart(result)

        # 4. 检索古籍
        self._emit_stream_event(stream_cb, "thinking", "正在查阅古籍…")
        refs = self.retriever.search(f"奇门遁甲 {question}", category="qimen", top_k=15)
        if not refs:
            refs = self.retriever.search(f"奇门遁甲 {question}", top_k=15)  # fallback

        # 阶段 5·来源体系（方案 §3.0）：奇门局 → 引擎来源；检索古籍 → book 来源
        try:
            self._register_engine_citation(
                user_id,
                f"奇门遁甲局：{year}年{month}月{day}日（{hour}时），"
                f"所问之事「{question}」",
                title="奇门遁甲局", source="奇门排盘引擎",
            )
            self._register_book_citations(user_id, refs, title="奇门 · 古籍参考")
        except Exception:
            pass

        # 5. LLM 用神分析
        self._emit_stream_event(stream_cb, "thinking", "推演九宫格局…")
        analysis = self.llm.analyze(chart_str, refs, question)

        # 6. 组合回复
        reply = chart_str + "\n\n" + analysis.response

        # 7. 反馈提示
        reply = self._add_feedback_prompt(reply)

        return reply

    # ============================================================
    # 姓名学 (Xingming) - RAG + LLM only, no dedicated engine
    # ============================================================

    def _handle_xingming(self, msg: str, user_id: str, stream_cb: Optional[Callable] = None) -> str:
        """处理姓名学咨询 — 提取姓名 → XingmingEngine 计算五格 → LLM 叙事"""
        # Null guard
        if self.xingming_engine is None:
            return "⚠️ 姓名学引擎暂不可用，请稍后再试。"

        question = self._get_question_after_keywords(msg, [
            "起名", "改名", "名字", "姓名", "看看", "帮我",
        ])

        if not question or len(question) < 2:
            return """请告诉我您想分析的名字或起名需求：

💡 示例1：分析"张伟"这个名字好不好
💡 示例2：给2026年出生的龙宝宝起名，姓王
💡 示例3：想改名字，有什么建议"""

        # 1. 提取姓名 — 优先从引号内提取，或从连续中文字符识别
        name_match = re.search(r'[""「『]([一-鿿]{2,4})[""」』]', msg)
        if not name_match:
            name_match = re.search(r'(?:分析?|叫|给|为)([一-鿿]{2,4})', msg)
        if not name_match:
            name_match = re.search(r'([一-鿿]{2,4})(?:这|的)', msg)
        if not name_match:
            name_match = re.search(r'^.*?([一-鿿]{2,4})', msg)
        name = name_match.group(1) if name_match else ""

        if not name or len(name) < 2:
            return "请提供完整的姓名（至少两个字），例如「张伟」「李小明」。"

        # 拆分姓氏和名字（默认姓1字，名=剩余）
        surname = name[0]
        given_name = name[1:]
        # 尝试识别复姓
        compound_surnames = {"欧阳", "上官", "司马", "司徒", "诸葛", "夏侯", "慕容", "皇甫",
                             "令狐", "长孙", "宇文", "鲜于", "钟离", "独孤", "达奚", "万俟"}
        if len(name) >= 3 and name[:2] in compound_surnames:
            surname = name[:2]
            given_name = name[2:]

        # 性别推断
        gender = "男"
        if any(w in msg for w in ["女", "女性", "姑娘", "女士"]):
            gender = "女"

        # Task 3：思考步骤按真实工作里程碑渐进发出（首条开工即发）
        # 2. 引擎计算五格三才
        self._emit_stream_event(stream_cb, "thinking", "我在拆解姓名笔画五行…")
        result = self.xingming_engine.analyze(surname, given_name, gender)

        # 3. 格式化为结构化字盘
        wuge_str = "  ".join(f"{k}={v}" for k, v in result.wuge.items())
        sancai_str = f"{result.sancai} ({result.sancai_ji})"
        analysis_lines = []
        for cell, info in result.analysis.items():
            analysis_lines.append(
                f"【{cell}】{info.get('数字','?')}数 — {info.get('吉凶','?')} — "
                f"{info.get('运势','')}。{info.get('详解','')}"
            )
        chart_lines = [
            f"姓名：{surname} {given_name}",
            f"性别：{gender}",
            f"笔画：姓={result.stroke_counts.get(surname,'?')}  "
            f"名1={result.stroke_counts.get(given_name[0],'?') if given_name else '?'}  "
            f"名2={result.stroke_counts.get(given_name[1],'?') if len(given_name) > 1 else '?'}",
            f"五格：{wuge_str}",
            f"三才：{sancai_str}",
            # R1-2（T007 同类）：五行计数一律无括号渲染（{'天格': …} dict
            # 字面量泄漏=半成品回复，评测 neg "{" 契约同一红线）
            f"五行：{'、'.join(f'{k}{v}' for k, v in (result.wuxing or {}).items()) or '无'}",
            "---",
            "各格详解：",
            *analysis_lines,
            "---",
            f"综合：{result.overall}",
        ]
        chart_str = "\n".join(chart_lines)

        # 4. 检索古籍
        self._emit_stream_event(stream_cb, "thinking", "正在查阅古籍…")
        refs = self.retriever.search(f"姓名学 {name} {question}", category="xingming", top_k=15)

        # 阶段 5·来源体系（方案 §3.0）：姓名五格 → 引擎来源；检索古籍 → book 来源
        try:
            self._register_engine_citation(
                user_id,
                f"姓名「{surname} {given_name}」五格：{wuge_str}；"
                f"三才：{result.sancai}（{result.sancai_ji}）；"
                f"五行：{'、'.join(f'{k}{v}' for k, v in (result.wuxing or {}).items()) or '无'}",
                title="姓名五格分析", source="姓名学引擎",
            )
            self._register_book_citations(user_id, refs, title="姓名学 · 古籍参考")
        except Exception:
            pass

        # 5. LLM 生成叙事分析
        self._emit_stream_event(stream_cb, "thinking", "推演三才配置…")
        analysis = self.llm.analyze(chart_str, refs, question)

        return analysis.response

    # ============================================================
    # 合婚配对 (Hehun) - 引擎计算五行/生肖/日柱 + LLM叙事
    # ============================================================

    def _handle_hehun(self, msg: str, user_id: str, stream_cb: Optional[Callable] = None) -> str:
        """合婚配对 - 提取双方信息，引擎计算匹配度，LLM生成叙事分析

        k39 S3：入口先走「单档补全」（`_hehun_single_fill`，与场景兜底
        `_scene_hehun_fallback` **同一实现、同一文案**，不两套）——
        本人缺失时用默认命主档案补全并显式标注「本人（来自档案）」；
        只缺对方时确定性补问，不再要求用户重复提供本人信息。
        双方都由消息给出（手填）时行为与改前完全一致。
        """
        fill = self._hehun_single_fill(msg, user_id)
        if fill["source_a"] == "archive" or (fill["birth_a"]
                                             and not fill["birth_b"]):
            # 档案补全 / 只缺对方 → 同一确定性出口（工具卡或标注补问）
            out = self._hehun_tool_reply(fill, user_id, msg)
            if out:
                return out
        # Extract both parties' birth info
        parts = re.split(r'[，。,\.\s]+女|女方|对方|对象|伴侣', msg)
        info_a = self._extract_bazi_info(msg)

        # Try to extract second person
        info_b = None
        if info_a and len(parts) > 1:
            info_b = self._extract_bazi_info(parts[1] if len(parts) > 1 else '')
        if not info_b:
            # Try alternative split: "男...女..."
            male_match = re.search(r'男[^女]*', msg)
            female_match = re.search(r'女.*', msg)
            if male_match and female_match:
                info_a = self._extract_bazi_info(male_match.group())
                info_b = self._extract_bazi_info(female_match.group())

        if not info_a or not info_b:
            return """想看看你们合不合？给我双方生辰即可直接测算；
或进入「双人合盘」页，从档案一键选人，还能生成墨韵缘笺：
/pages/hehun/hehun"""

        # Compute both charts
        year_a, month_a, day_a, hour_a, minute_a, city_a, gender_a = info_a
        year_b, month_b, day_b, hour_b, minute_b, city_b, gender_b = info_b

        # Null guard — return early if engine is not injected
        if self.hehun_engine is None:
            return "⚠️ 合婚配对功能暂时不可用，请稍后再试。"

        # Task 3：思考步骤按真实工作里程碑渐进发出（首条开工即发）
        self._emit_stream_event(stream_cb, "thinking", "我在比对两人命盘…")
        result_a = self.engine.calculate(year_a, month_a, day_a, hour_a, minute_a, city_a, gender_a or "男")
        result_b = self.engine.calculate(year_b, month_b, day_b, hour_b, minute_b, city_b, gender_b or "女")

        # Engine matching
        hehun_result = self.hehun_engine.match(result_a, result_b)
        shengxiao_a = hehun_result.shengxiao_detail.get("shengxiao1", "")
        shengxiao_b = hehun_result.shengxiao_detail.get("shengxiao2", "")

        # Build structured chart string
        chart_str = f"""男方八字：{' '.join(result_a.bazi)}  日主{result_a.day_master}  属{shengxiao_a}
女方八字：{' '.join(result_b.bazi)}  日主{result_b.day_master}  属{shengxiao_b}

五行互补得分：{hehun_result.wuxing_score}/100 — {hehun_result.bazi_match.get('complement_desc', '')}
生肖配对：{hehun_result.shengxiao}
日柱关系得分：{hehun_result.rizhu_score}/100 — {hehun_result.rizhu}
综合评分：{hehun_result.score}/100"""

        self._emit_stream_event(stream_cb, "thinking", "正在查阅古籍…")
        refs = self.retriever.search(f"合婚 婚姻匹配 {shengxiao_a} {shengxiao_b}", category="hehun", top_k=15)

        # 阶段 5·来源体系（方案 §3.0）：合婚评分 → 引擎来源；检索古籍 → book 来源
        try:
            self._register_engine_citation(
                user_id,
                f"合婚配对：{shengxiao_a}×{shengxiao_b}；五行互补"
                f"{hehun_result.wuxing_score}/100；日柱关系{hehun_result.rizhu_score}/100；"
                f"综合评分{hehun_result.score}/100",
                title="合婚配对结果", source="合婚引擎",
            )
            self._register_book_citations(user_id, refs, title="合婚 · 古籍参考")
        except Exception:
            pass

        self._emit_stream_event(stream_cb, "thinking", "推演五行互补…")
        analysis = self.llm.analyze(chart_str, refs, f"分析这对男女的婚姻匹配度，给出3条化解建议")

        return analysis.response

    # ============================================================
    # 解梦 (Dream) - engine + RAG + LLM
    # ============================================================

    def _handle_dream(self, msg: str, user_id: str,
                      stream_cb: Optional[Callable] = None,
                      session_id: Optional[str] = None) -> str:
        """处理解梦请求 - 提取完整梦境 + 处境

        P0-1 修复: 如果用户在本会话中已经分享过梦境内容，
        则直接使用已有梦境内容进行分析，不再要求重新描述。
        """
        # 去掉触发词，保留完整描述
        for kw in ["帮我解梦", "解梦", "做梦"]:
            msg = msg.replace(kw, "", 1)
        dream_text = msg.strip()

        if not dream_text or len(dream_text) < 2:
            # P0-1: 检查会话历史中是否有已分享的梦境内容
            if self.session_dao:
                history = self.session_dao.get_context_for_llm(
                    user_id, history_limit=20, session_id=session_id)
                for m in reversed(history):
                    if m["role"] == "user" and any(kw in m["content"] for kw in ["梦见", "梦到", "做梦", "梦见了"]):
                        dream_text = m["content"]
                        break
            if not dream_text or len(dream_text) < 2:
                # v2026-08-17：去 emoji（PM：回复 emoji 过多显 low）
                return """请描述您的梦境，我来为您解梦：

您可以详细说说：
• 梦里发生了什么？
• 梦里有什么情绪和感觉？
• 最近有什么特别担心或关注的事情吗？

例如：「梦见一条大蟒蛇在追我，我很害怕，最近工作压力大，老板总刁难我」"""

        return self._do_dream_analysis(dream_text, user_id, stream_cb=stream_cb)

    def _do_dream_analysis(self, dream_text: str, user_id: str,
                           stream_cb: Optional[Callable] = None) -> str:
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

        # 2. 获取用户八字（日主个性化上下文）
        bazi_info = None
        try:
            # k18：日主是盘面键 → 改经 get_user_birth_profile_full（k8 语义：
            # 四柱只取「出生档案匹配的 chart_records」）——原裸读 bazi_info
            # 旧行 bazi 键可能为历史他人盘污染（21:44 事故族），且 persons-
            # only 建档用户（bazi_info 无行）永远拿不到个性化；显示格式保持
            # 原样（bazi[2] 日柱串，如「戊午」+ 当前大运占位）。
            from src.storage.birth_profile import get_user_birth_profile_full
            saved = get_user_birth_profile_full(
                self.dao, user_id,
                chart_dao=getattr(self, "chart_dao", None))
            if saved:
                bazi_info = {"day_master": f"{saved.get('bazi', ['?'])[2]}", "current_dayun": "当前"}
        except Exception:
            pass

        # 3. 引擎分析 + RAG检索（276万 FAISS 生产主路径优先，降级本地 27k）
        # 流式模式先发进度事件，避免 FAISS 检索（CPU 向量化，可长达数十秒）
        # 的沉默期触发看门狗超时
        self._emit_stream_event(stream_cb, "thinking", "正在分析梦境象征…")
        api_key = getattr(self.llm, 'api_key', '') if self.llm else ''
        if self.dream_engine:
            result = self.dream_engine.analyze(
                dream_text, self._get_dream_retriever(), api_key,
                user_context, bazi_info)
            # E2-1 卡片化（批次 2 P1）：解梦引擎完成分析（dream 卡片判定依据；
            # 引擎未注入走空壳兜底 → 不落标记 → 宁漏勿误）
            self._mark_card_turn(user_id, dream=True)
        else:
            result = DreamResult()

        # 阶段 5·来源体系（方案 §3.0 ①）：解梦引擎的古籍匹配结果 → book 来源
        interps = (getattr(result, "interpretations", None) or [])[:5]
        if interps:
            try:
                start = self._alloc_citations(user_id, len(interps))
                items = []
                for i, interp in enumerate(interps, start=start):
                    items.append(make_citation(
                        i, "book", str(interp)[:400],
                        title="解梦 · 古籍参考", source="解梦引擎",
                    ))
                self._append_citations(user_id, items)
            except Exception:
                pass

        self.dao.save_consultation(user_id, dream_text, result, intent="dream")

        # 4. 构建完整 Prompt 并调用 LLM
        self._emit_stream_event(stream_cb, "thinking", "正在整理解梦要点…")
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
        """格式化最终回复 — AI 动态情绪开场 + 古籍解读"""
        # AI generates emotional opener (already done by LLM in analysis.response)
        # The opener is natural language understanding, not keyword matching
        reply = "🌙 周公解梦\n\n"
        reply += f"梦境：{dream_text}\n"

        if result.interpretations:
            reply += "\n📖 古籍记载：\n"
            for i, interp in enumerate(result.interpretations[:3], 1):
                reply += f"  {i}. {interp[:200]}\n"

        reply += f"\n🔮 AI解读：\n{llm_analysis}"
        return reply

    # ============================================================
    # 八字学堂
    # ============================================================

    def _handle_xuetang(self, msg: str, user_id: str, stream_cb: Optional[Callable] = None) -> str:
        """Handle learning/tutorial requests with optional personalization.

        If the user has saved bazi data, the lesson will include
        personalized examples based on their chart.
        """
        from src.engines.xuetang import list_topics, get_lesson, personalized_lesson

        # Remove trigger words
        for kw in ["学堂", "学习", "教程"]:
            msg = msg.replace(kw, "", 1)
        topic = msg.strip()

        if not topic or topic in ("", "列表", "目录", "帮助"):
            return list_topics()

        # Check if user has saved bazi for personalization
        retriever = self.retriever if hasattr(self, 'retriever') else None
        bazi_data = None
        try:
            # k18（REST /api/xuetang 同款，k8 语义）：四柱/盘面键只取
            # get_user_birth_profile_full 的「出生档案匹配 chart_records 盘」
            # ——原裸读 users.bazi_info.bazi（旧行 bazi 键可能为历史他人盘
            # 污染，21:44 事故源；无匹配盘 → 通用课程，不把他人盘四柱用于
            # 个性化）；persons-only 建档用户 bazi_info 无行也可经 persons
            # → chart_records 读取链获得个性化。
            if self.dao:
                from src.storage.birth_profile import get_user_birth_profile_full
                saved = get_user_birth_profile_full(
                    self.dao, user_id,
                    chart_dao=getattr(self, "chart_dao", None))
                if saved and saved.get("bazi"):
                    bazi_data = {
                        "day_master": saved.get("day_master", ""),
                        "bazi": saved.get("bazi", []),
                        "geju": saved.get("geju", ""),
                        "yongshen": saved.get("yongshen", ""),
                    }
        except Exception:
            # If reading bazi fails, fall back to standard lesson
            pass

        if bazi_data:
            try:
                return personalized_lesson(topic, bazi_data=bazi_data, retriever=retriever)
            except Exception:
                # If personalization fails, fall back to standard lesson
                pass

        return get_lesson(topic, retriever=retriever)

    # ============================================================
    # AI 建议 (Advisor V2)
    # ============================================================

    ADVISOR_KEYWORDS = ["建议", "怎么办", "有什么建议", "帮我分析"]

    def _handle_advisor(self, msg: str, user_id: str,
                        stream_cb: Optional[Callable] = None,
                        ground_hint: str = "") -> str:
        """处理 AI 建议请求 — 基于八字 + 用户处境生成个性化建议.

        ground_hint（k41）：本轮自动联网检索的结果块 + 使用要求（
        `_ground_hint_parts` 产出）。**只增不改**——空串 = 改前行为逐字节不变；
        非空时并入 user_context 交给建议 LLM（外部实体 QA 的事实依据 + 来源）。
        """
        # 1. 检查用户八字是否已保存
        # k11c：残余 bazi_info 直读 → 统一档案读取（G3c 同源，同 _handle_calendar
        # /_handle_hourly）——persons 建档用户与档案 solar_time 开关同源生效，
        # 不再只认 users.bazi_info（persons 新档案无 bazi_info 行 → 旧路径误
        # 引导建档，T089 同款问题的 advisor 面）。
        saved = self._get_user_birth_profile(user_id)
        if not saved:
            return ("💡 想为你生成专属建议，需要先了解你的命盘哦～\n"
                    "请提供你的出生信息：出生年月日时、出生地、性别\n\n"
                    "例如：1990年5月20日 下午3点 北京 男")
        # k11c：档案真太阳时开关（1=开=引擎默认；0=关=北京时间直排）
        _solar_adv = (saved.get("solar_time") not in (0, "0", False))
        # R2-6（同类残余直喂点）：存储档案可能为 lunar 原始 y/m/d → 重排盘前
        # 单点转公历（引擎契约=公历输入；阴历当公历算错日主/四柱，建议全链错）
        saved = self._solarize_birth(saved)

        # 2. 提取用户处境（去掉排盘信息后的剩余文本）
        user_context = self._extract_user_context(msg)
        if not user_context:
            # 从消息中提取关键词，如果没有具体语境，使用默认描述
            user_context = "一般运势咨询"
        # k41（T104）：本轮自动检索结果并入处境——LLM 以检索事实作答并标注来源
        # （改前 advisor 路径拿不到检索块：回复无实体名、无来源痕迹）。
        if ground_hint:
            user_context = user_context + "\n\n" + ground_hint

        # 3. 重新排盘
        try:
            # k9（R2-6 遗留 minors 实测暴露）：引擎实参归一化与 _feed_birth/
            # _map_user_bazi_for_zeri 等消费方同口径——minute 缺省 0、city/gender
            # 缺省显式化（bazi_info 行可能无 minute：时辰建档 → None 直喂引擎
            # TypeError → 用户拿「重新计算失败」而非建议）。
            result = self.engine.calculate(
                int(saved["year"]), int(saved["month"]), int(saved["day"]),
                int(saved.get("hour") or 0), int(saved.get("minute") or 0),
                str(saved.get("city") or ""),
                str(saved.get("gender") or "unknown"),
                solar_time=_solar_adv,
            )
        except Exception as e:
            return f"⚠️ 命盘重新计算失败：{str(e)[:100]}"

        # k11-B/C：advisor 直答面同样登记本轮事实上下文（称谓/神煞出口校验用）
        self._set_fact_ctx(user_id, getattr(result, "gender", ""),
                           getattr(result, "shensha", None))

        # 4. 调用 AdaptiveAdvisor 生成建议
        api_key = getattr(self.llm, 'api_key', '') if self.llm else ''
        try:
            advisor = AdaptiveAdvisor()
            advice_data = advisor.generate(
                result, user_context=user_context, api_key=api_key,
            )
        except Exception as e:
            return f"⚠️ AI 建议生成失败：{str(e)[:100]}\n\n请稍后再试或换个问题～"

        # 5. 格式化回复
        lines = []

        # 5a. 名人匹配已移除（2026-08-09 方案 v5 选 A：未问"像谁"不输出名人对照）

        # 5b. 领域建议
        actions = advice_data.get("actions", [])
        if actions:
            lines.append("📌 **AI 行动建议**（基于命局趋势 + 当前处境生成）\n")
            icons = {"事业": "💼", "财运": "💰", "感情": "❤️", "健康": "🏥", "个人成长": "🌱"}
            for a in actions:
                cat = a.get("category", "")
                advice = a.get("advice", "")
                timing = a.get("timing", "")
                confidence = a.get("confidence", "medium")
                concrete_steps = a.get("concrete_steps", "")
                success_metric = a.get("success_metric", "")

                cf_icons = {"high": "✅", "medium": "📌", "low": "💡"}
                cf = cf_icons.get(confidence, "📌")
                icon = icons.get(cat, "")
                timing_str = f"⏰ {timing}" if timing else ""

                lines.append(f"{cf} {icon} **{cat}**")
                lines.append(f"   {advice}")
                if timing_str:
                    lines.append(f"   {timing_str}")
                if concrete_steps:
                    lines.append(f"   📋 具体步骤：{concrete_steps}")
                if success_metric:
                    lines.append(f"   🎯 衡量标准：{success_metric}")
                lines.append("")

        # 5c. 随机发现
        serendipity = advice_data.get("serendipity", "")
        if serendipity:
            lines.append(f"💫 {serendipity}\n")

        # 5d. 每日小贴士
        daily_tip = advice_data.get("daily_tip", "")
        if daily_tip:
            lines.append(f"💡 **今日贴士**：{daily_tip}")

        # 5e. 风格备注
        style_note = advice_data.get("style_notes", "")
        if style_note:
            lines.append(f"✨ {style_note}")

        reply = "\n".join(lines)
        if not reply:
            reply = "💡 根据你的命盘分析，建议保持平稳心态，审时度势。具体建议需要结合你的实际问题来分析，不妨详细说说你的情况？"

        # 6. 反馈提示
        reply = self._add_feedback_prompt(reply)

        # k11-B/C（输出后校验器·advisor 直答面）：建议文案已由 generate 内 scrub，
        # 此处对拼装后的整段再做一道称谓/神煞校验（纯规则；无 result → 原样）。
        try:
            _sr = self._scrub_turn_text(reply, user_id)
            if _sr:
                reply = _sr
        except Exception:
            pass

        # 7. 保存咨询记录
        self.dao.save_consultation(user_id, msg, result, intent="advisor")

        return reply

    # ============================================================
    # AI 幸运日历
    # ============================================================

    def _handle_calendar(self, msg: str, user_id: str, stream_cb: Optional[Callable] = None) -> str:
        """Handle calendar/today-fortune requests."""
        # R1-2（评测 T089 修复·已建档仍引导）：统一档案读取（persons 默认
        # 档案优先 → bazi_info → chart_records，G3c 同源）——原直读
        # users.bazi_info：persons 建档用户 bazi_info 为空 → 误判未建档 →
        # 引导建档（T089 轮1 实锤）；改城市后当日重算（轮3 distinct）由
        # process() 缓存键档案指纹保障。
        saved = self._get_user_birth_profile(user_id)
        if not saved:
            return ("📅 想生成你的专属每日运势日历，需要先设置八字哦～\n"
                    "告诉我你的出生日期，例如：1990年5月20日 下午3点 北京 男")
        # R1-2（T089）：persons-only 建档（档案缺四柱扩展键 bazi/day_master/
        # wuxing/dayun）→ 确定性即时排盘补齐（引擎计算，0 LLM；不要求预先
        # 有 chart_records——「已建档即应直接算运势」）。排盘失败 → 降级用
        # 基础档案（LuckyCalendar 出通用版，不引导）。
        if not saved.get("bazi") and self.engine is not None:
            try:
                # R2-6（同类残余直喂点）：lunar 档案原始 y/m/d → 单点转公历
                # 再补齐四柱（引擎契约=公历输入；R2-5 只修了 /api/calendar.py
                # 端点，对话入口 _handle_calendar 同款直喂遗漏——阴历当公历
                # 算错日主，daily/hourly 展示全链跟着错）。转换失败 → 回落
                # 原值 + warning（_solarize_birth 内），不阻塞主流程。
                saved = self._solarize_birth(saved)
                # k11c：真太阳时开关随档案（0=关=北京时间直排；缺省/旧行=开）
                bz = self.engine.calculate(
                    saved["year"], saved["month"], saved["day"],
                    saved.get("hour") or 0, saved.get("minute") or 0,
                    saved.get("city") or "",
                    _GENDER_CN.get(str(saved.get("gender") or "").lower(), "男"),
                    solar_time=(saved.get("solar_time") not in (0, "0", False)))
                saved = dict(saved)
                saved["bazi"] = list(bz.bazi)
                saved["day_master"] = bz.day_master
                saved["wuxing"] = dict(bz.wuxing)
                saved["dayun"] = [f"{s}岁{gz}" for s, gz in bz.dayun]
            except Exception:
                pass

        api_key = getattr(self.llm, 'api_key', '') if self.llm else ''
        if not api_key:
            return "📅 日历服务暂时不可用，请稍后再试～"

        try:
            # Task 3：思考步骤按真实工作里程碑渐进发出（首条开工即发）
            # Daily calendar
            self._emit_stream_event(stream_cb, "thinking", "我在查今日星象…")
            from src.engines.calendar import LuckyCalendar
            cal = LuckyCalendar(api_key)
            preferences = self._get_preference_hint(user_id)
            day = cal.daily(saved, None, preferences=preferences)

            # 阶段 5·来源体系（方案 §3.0 ②）：今日运势 → 引擎来源
            try:
                self._register_engine_citation(
                    user_id,
                    f"今日专属运势：{day.date}，总体"
                    f"{getattr(day, 'overall_mood', '') or '平稳'}",
                    title="今日运势", source="幸运日历引擎",
                )
            except Exception:
                pass

            reply = self._format_calendar(day)

            # Hourly fortune (deterministic, no API call)
            try:
                from src.engines.hourly_fortune import format_hourly_card
                bazi_list = saved.get("bazi", ["?"])
                day_master = bazi_list[2] if len(bazi_list) >= 3 else "?"
                day_bz = bazi_list[2] if len(bazi_list) >= 3 else "?"
                # Get day branch from the day pillar
                if len(bazi_list) >= 3 and len(bazi_list[2]) >= 2:
                    day_branch = bazi_list[2][1]  # Second char of day pillar = branch
                else:
                    day_branch = "子"

                # Map day master element
                wx_map = {"甲":"木","乙":"木","丙":"火","丁":"火","戊":"土","己":"土",
                          "庚":"金","辛":"金","壬":"水","癸":"水"}
                dm_element = wx_map.get(day_master, "土") + day_master if len(day_master) >= 1 else "未知"

                hourly = format_hourly_card(dm_element, day_branch)
                reply += "\n\n" + hourly
            except Exception:
                pass  # Hourly is best-effort

            return reply
        except Exception as e:
            return f"📅 日历生成失败：{str(e)[:100]}"

    # ============================================================
    # 时辰运势 (Hourly Fortune)
    # ============================================================

    def _handle_hourly(self, msg: str, user_id: str, stream_cb: Optional[Callable] = None) -> str:
        """Handle hourly fortune requests — 十二时辰逐时分析."""
        # R1-2（T089 同源修复）：统一档案读取 + 即时排盘补齐（与 _handle_calendar
        # 同口径）——persons 建档用户不再误引导，时辰运势基于真实日主生成。
        saved = self._get_user_birth_profile(user_id)
        if not saved:
            return ("⏰ 想查看今日十二时辰运势，需要先设置八字哦～\n"
                    "告诉我你的出生日期，例如：1990年5月20日 下午3点 北京 男")
        if not saved.get("bazi") and self.engine is not None:
            try:
                # R2-6（同类残余直喂点，与 _handle_calendar 同口径）：lunar
                # 档案原始 y/m/d → 单点转公历再补齐四柱（见 _handle_calendar
                # 注释；时辰运势的日主/时辰干支依赖正确四柱）
                saved = self._solarize_birth(saved)
                # k11c：真太阳时开关随档案（0=关=北京时间直排；缺省/旧行=开）
                bz = self.engine.calculate(
                    saved["year"], saved["month"], saved["day"],
                    saved.get("hour") or 0, saved.get("minute") or 0,
                    saved.get("city") or "",
                    _GENDER_CN.get(str(saved.get("gender") or "").lower(), "男"),
                    solar_time=(saved.get("solar_time") not in (0, "0", False)))
                saved = dict(saved)
                saved["bazi"] = list(bz.bazi)
                saved["day_master"] = bz.day_master
            except Exception:
                pass

        try:
            # Task 3：思考步骤按真实工作里程碑渐进发出（首条开工即发）
            self._emit_stream_event(stream_cb, "thinking", "我在推演时辰运势…")
            # Compute today's stem & branch
            from datetime import datetime, timezone, timedelta
            now = datetime.now(timezone(timedelta(hours=8)))
            date_str = now.strftime("%Y-%m-%d")

            stems = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
            branches = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
            ref = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8)))
            dt = datetime(now.year, now.month, now.day, tzinfo=timezone(timedelta(hours=8)))
            days_diff = (dt - ref).days
            day_stem = stems[(1 + days_diff) % 10]
            day_branch = branches[(5 + days_diff) % 12]

            # Extract user's day master
            bazi_list = saved.get("bazi", ["?"])
            day_master = bazi_list[2] if len(bazi_list) >= 3 else "?"
            wx_map = {"甲": "木", "乙": "木", "丙": "火", "丁": "火",
                      "戊": "土", "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水"}
            user_wx = wx_map.get(day_master, "土")
            user_day_master = user_wx + day_master

            # Generate hourly fortune
            from src.engines.hourly_fortune import get_hourly_fortune, format_hourly_card
            hourly = get_hourly_fortune(user_day_master, day_branch)
            reply = format_hourly_card(user_day_master, day_branch)

            # 阶段 5·来源体系（方案 §3.0 ②）：时辰运势 → 引擎来源
            try:
                self._register_engine_citation(
                    user_id,
                    f"今日时辰运势：用户日主{user_day_master}，今日日支{day_branch}"
                    f"（{day_stem}{day_branch}日），十二时辰逐时分析",
                    title="今日时辰运势", source="时辰运势引擎",
                )
            except Exception:
                pass

            # Optional: LLM summary for the best hours
            api_key = getattr(self.llm, 'api_key', '') if self.llm else ''
            if api_key:
                try:
                    best = [h for h in hourly if h["rating"] in ("excellent", "good")][:3]
                    if best:
                        best_names = "、".join(h["name"] for h in best)
                        activities = "；".join(
                            "、".join(h["activities"]) for h in best if h["activities"]
                        )
                        prompt = (
                            f"用户日主{user_day_master}，今日日支{day_branch}（{day_stem}{day_branch}日）。"
                            f"今日最佳时段：{best_names}，适宜活动：{activities}。"
                            "请用一句话给出今日时辰运势的总结建议（20字以内），语气温暖实用。直接返回文本。"
                        )
                        from src.llm.client import deepseek_anthropic_completion
                        summary = deepseek_anthropic_completion(
                            api_key,
                            [{"role": "user", "content": prompt}],
                            model="deepseek-flash",
                            max_tokens=200,
                            temperature=0.7,
                            timeout=20.0,
                        )
                        if summary:
                            reply += f"\n\n💬 {summary}"
                except Exception:
                    pass  # Summary is best-effort

            return reply
        except Exception as e:
            return f"⏰ 时辰运势生成失败：{str(e)[:100]}"

    def _format_calendar(self, day) -> str:
        """Format a CalendarDay into a WeChat-friendly message."""
        lines = [f"📅 {day.date} 专属运势"]

        if day.overall_mood:
            lines.append(f"\n✨ {day.overall_mood}")

        if day.is_special and day.special_note:
            lines.append(f"\n⚠️ {day.special_note}")

        if day.yi:
            lines.append("\n✅ 宜：")
            for item in day.yi[:4]:
                action = item.get("action", "")
                time_ = item.get("time", "")
                reason = item.get("reason", "")
                time_str = f"（{time_}）" if time_ else ""
                reason_str = f" — {reason}" if reason else ""
                lines.append(f"  • {action}{time_str}{reason_str}")

        if day.ji:
            lines.append("\n❌ 忌：")
            for item in day.ji[:4]:
                action = item.get("action", "")
                time_ = item.get("time", "")
                reason = item.get("reason", "")
                time_str = f"（{time_}）" if time_ else ""
                reason_str = f" — {reason}" if reason else ""
                lines.append(f"  • {action}{time_str}{reason_str}")

        lucky = []
        if day.lucky_color:
            lucky.append(f"🎨 {day.lucky_color}")
        if day.lucky_direction:
            lucky.append(f"🧭 {day.lucky_direction}")
        if day.lucky_number:
            lucky.append(f"🔢 {day.lucky_number}")
        if lucky:
            lines.append(f"\n🍀 幸运：{'  '.join(lucky)}")

        lines.append(f"\n📅 回复「7天运势」查看一周预览")

        return "\n".join(lines)

    # ============================================================
    # 心事树洞 — Deep Listening Mode (H1-H3)
    # ============================================================

    def _handle_confidant(self, msg: str, user_id: str, analysis,
                          session_id: Optional[str] = None) -> str:
        """Deep listening mode: engage with user's story before offering fortune reading.

        Uses a dedicated system prompt (CONFIDANT_PROMPT) that prioritizes
        understanding and empathy over analysis. The transition to fortune
        telling happens naturally after 2-3 listening turns.
        """
        try:
            from src.llm.prompts import CONFIDANT_PROMPT

            # Task 5 deepNight：倾诉临时通道注入深夜语气层（红灯规则优先级最高）
            if self._deep_night.get(user_id, False):
                from src.bot.night_persona import NIGHT_TONE_HINT
                msg = msg + "\n\n" + NIGHT_TONE_HINT

            # Count how many turns of listening this user has had
            listening_turns = self._get_listening_turns(user_id)
            self._set_listening_turns(user_id, listening_turns + 1)

            # After 2-3 turns, gently offer transition
            transition_hint = ""
            if listening_turns >= 2:
                transition_hint = (
                    "\n\n[用户已经分享了{0}轮了。如果感觉用户情绪已经得到一定释放，"
                    "可以在回复末尾自然地提议：'要不要我从命理角度帮你看看？' "
                    "但不要强制，给用户选择权。]".format(listening_turns)
                )

            api_key = getattr(self.llm, 'api_key', '') if self.llm else ''
            if not api_key:
                # B2 reviewer Important：回退 _free_chat 结果不经 _run_tool_loop，
                # 剥离 B2-11 工单教学诱导的 JSON 工单残留（无会话存储单消息分支）
                _fallback = self._free_chat(msg, user_id, emotion_label="sadness",
                                            session_id=session_id)
                return strip_tool_calls(_fallback) or _fallback

            import httpx
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            messages = [
                {"role": "system", "content": CONFIDANT_PROMPT},
                {"role": "user", "content": msg + transition_hint},
            ]

            # Include recent conversation context
            if self.session_dao:
                history = self.session_dao.get_context_for_llm(
                    user_id, history_limit=5, session_id=session_id)
                if len(history) > 1:
                    messages = [{"role": "system", "content": CONFIDANT_PROMPT}]
                    messages.extend(history[-4:])  # last 4 messages
                    if transition_hint:
                        messages[-1] = {
                            "role": "user",
                            "content": messages[-1]["content"] + transition_hint,
                        }

            from src.llm.client import deepseek_anthropic_completion
            reply = deepseek_anthropic_completion(
                api_key,
                messages,
                model="deepseek-flash",
                max_tokens=2000,
                temperature=0.8,
                timeout=30.0,
            )

            # If soothing was detected, prepend it
            if analysis.needs_soothe and analysis.soothe_text:
                reply = analysis.soothe_text + "\n\n" + reply

            # D3: Record confidant session for future follow-up
            if self.memory:
                self.memory.add_interaction(
                    user_id, msg, reply, intent="confidant",
                    key_data={"needs_followup": True, "topic": analysis.emotion_label or "倾诉"}
                )

            # B2 reviewer Important：confidant 主路径直出同样不经
            # _run_tool_loop——剥离任何工具调用残留（出口纪律一致，防御性）
            return strip_tool_calls(reply) or reply
        except Exception:
            # B2 reviewer Important：异常回退 _free_chat 结果同样不经
            # _run_tool_loop——剥离工单残留后再交给用户
            _fallback = self._free_chat(msg, user_id, emotion_label="sadness",
                                        session_id=session_id)
            return strip_tool_calls(_fallback) or _fallback

    def _get_listening_turns(self, user_id: str) -> int:
        """Track how many consecutive listening turns a user has had."""
        if not hasattr(self, '_listening_turns'):
            self._listening_turns = {}
        return self._listening_turns.get(user_id, 0)

    def _set_listening_turns(self, user_id: str, count: int):
        """Update listening turn counter. Reset when > 5 or user switches topic."""
        if not hasattr(self, '_listening_turns'):
            self._listening_turns = {}
        if count > 5:
            count = 0  # Reset after extended listening
        self._listening_turns[user_id] = count

    # ============================================================
    # 帮助信息
    # ============================================================

    def _conversational_chat(self, history: list, user_id: str,
                             session_id: Optional[str] = None) -> str:
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
            if re.search(r'\d{4}', current_msg) or re.search(r'(?:^|[^\w])[男女](?:$|[^\w])', current_msg):
                return '看起来您可能在提供出生信息。请按格式告诉我：\n📅 出生年月日\n⏰ 几点几分\n📍 出生城市\n👤 性别\n\n例如：1990年5月20日 下午3点 北京 男'

        # 构建传给 LLM 的历史消息
        llm_history = list(history)

        # 如果显式历史只有一条消息，尝试从会话存储中补充更早的上下文
        if len(history) <= 1 and self.session_dao:
            session_ctx = self.session_dao.get_context_for_llm(
                user_id, history_limit=15, session_id=session_id)
            if len(session_ctx) > len(history):
                # 用会话历史替换，但确保当前用户消息在最后
                llm_history = [m for m in session_ctx if m["role"] != "user" or m["content"] != current_msg]
                llm_history.append({"role": "user", "content": current_msg})

        # Save user message to session
        if self.session_dao and current_msg:
            self.session_dao.add_message(user_id, "user", current_msg,
                                         session_id=session_id)

        # 多轮对话：把历史消息传给 LLM
        try:
            reply = self.llm.chat_conversation(llm_history)
        except Exception:
            reply = '我在这里，有什么困惑尽管说。'

        # Save assistant reply to session
        if self.session_dao:
            self.session_dao.add_message(user_id, "assistant", reply,
                                         session_id=session_id)

        return reply

    def _free_chat(self, msg: str, user_id: str, emotion_label: str = None,
                   extra_hint: str = "",
                   stream_cb: Optional[Callable] = None,
                   session_id: Optional[str] = None,
                   downgraded: bool = False,
                   scene_hint: str = None) -> str:
        """自由对话：没有命中任何命理意图时，直接用 LLM 自然聊天。

        当检测到情绪信号时，将情绪上下文注入提示词，
        确保 LLM 优先提供情感支持而非索要信息。

        P0-1 修复: 总是加载完整会话历史传给 LLM (history_limit=20)，
        确保多轮对话上下文不丢失。不再因为只有 1 条消息就走单消息模式。

        AI 原生（Phase 2）:
        - extra_hint: 多次重复话题提示（方案 5.5）
        - 会话超长时执行上下文压缩（方案 2.3）

        stream_cb（v8 阶段 3）：提供时主 LLM 调用走真实流式，增量实时回调。
        """
        if msg.strip() in ('',' ','?','？'):
            # Phase 3: Mood-aware greeting for returning users
            # A4：抽取为 _greeting_reply（简单意图快通道复用，行为不变）
            return self._greeting_reply(user_id)

        # 如果消息含数字或年份且用户已有八字，直接路由到八字分析
        # 注意: "男"/"女" 必须是独立出现(性别标记)，不能是 "渣男"/"美女" 等词的一部分
        # 批次 2 E6（工具 AI 决策通道）：scene_hint 命中的消息（如
        # 「这个手机号 13800138000 好不好」含 4 位数字串）跳过出生信息
        # 引导门——否则工具链入口被掐断，LLM 永远看不到工具清单。
        has_year = bool(re.search(r'\d{4}', msg))
        has_gender = bool(re.search(r'(?:^|[^\w])[男女](?:$|[^\w])', msg))
        # k18：「用户是否已有档案」改走统一档案链（persons 默认档案 →
        # bazi_info → chart_records 兜底，G3c 同源）——原裸读 bazi_info 把
        # persons-only 建档用户误判为「无八字」→ 重复引导建档/注入渐进引导
        # hint（T089 同类自由对话面）。fail-open：链内异常回落 None 走原
        # 无档案分支，绝不因档案读取错误阻塞自由对话。
        try:
            saved_bazi = (self._get_user_birth_profile(user_id)
                          if self.dao else None)
        except Exception:
            saved_bazi = None
        if (has_year or has_gender) and not saved_bazi and not scene_hint:
            # F2（2026-08-26）：先看是否已在会话中累积部分出生信息——有则
            # 渐进引导（回显已确认项 + 只问缺失项，不要求完整格式）；无则
            # 保持原固定格式引导（行为不变）。
            try:
                _pknown, _pmissing = self._collect_partial_birth(
                    user_id, session_id, msg)
            except Exception:
                _pknown = None
            if _pknown:
                return self._gen_info_collection_prompt(
                    msg, lite=downgraded, known=_pknown, missing=_pmissing)
            return '看起来您可能在提供出生信息。请按格式告诉我：\n📅 出生年月日（阳历/阴历）\n⏰ 几点几分\n📍 出生城市\n👤 性别\n\n例如：1990年5月20日 下午3点 北京 男'
        if saved_bazi and (has_year or has_gender) and not scene_hint:
            # 用户已有八字，但提供了新的出生信息，可能想更新或已有信息
            # L5-2（I-1）：降级链路意图判定改规则快判（零 LLM 调用）
            if self._downgraded.get(user_id, False):
                intent_result = self._rule_analyze(msg)
            else:
                intent_result = self._analyze_message(msg, user_id,
                                                      session_id=session_id)
            if intent_result.intent == "bazi":
                return self._handle_bazi(msg, user_id, session_id=session_id)

        # F2（2026-08-26）：无档案 + 非情绪消息 + 会话已累积部分出生信息 →
        # 注入渐进引导 hint（LLM 先复述确认已给信息，再只问缺失项，
        # 不要要求完整格式，不要问今年是哪一年）。仅影响 LLM 提示词，
        # 无额外 LLM 调用；历史获取为 DAO 读，降级路径同样注入。
        # 批次 2 E6（Important-1 修复）：scene_hint 命中的工具场景消息跳过——
        # 含生日片段（如「1990年5月20日 想给孩子起名」）会同时命中 partial
        # 累积与场景门控，注入该 hint 会与工具链调度指令并存、LLM 行为不可预测。
        partial_hint = ""
        if not saved_bazi and not emotion_label and not scene_hint:
            try:
                _pknown, _pmissing = self._collect_partial_birth(
                    user_id, session_id, msg)
                if _pknown:
                    partial_hint = (
                        f"【重要】用户正在分步提供出生信息，目前已确认："
                        f"{_format_partial_echo(_pknown, msg)}。"
                        f"请先复述确认这些信息，然后只询问缺失项："
                        f"{'、'.join(_pmissing)}。"
                        "不要要求完整格式，不要问今年是哪一年。")
            except Exception:
                partial_hint = ""

        # 所有其他消息 → 用 LLM 自然对话（总是带完整会话历史）
        try:
            # Build preference hint for LLM (P3: personalized RLHF context)
            pref_hint = self._get_personalized_context(user_id)

            # Build emotional context hint for the LLM
            emotion_hint = ""
            if emotion_label:
                emotion_hints = {
                    "heartbreak": "【重要】用户正在经历感情创伤，请优先给予共情和情感支持。先安抚情绪，再谈其他。不要一上来就索要出生信息。",
                    "sadness": "【重要】用户情绪低落，请先给予温暖的理解和陪伴。不要急于索要信息。",
                    "anxiety": "用户感到焦虑不安，请给出踏实、具体的建议帮助缓解。先共情，再给方案。",
                    "confusion": "用户在艰难选择中，请直接给建议和方法帮他们理清思路。绝对不要索要出生信息或八字。",
                    "anger": "用户感到愤怒不平，请先认可他们的感受，再引导理性看待。",
                }
                emotion_hint = emotion_hints.get(emotion_label, "")

            # Combine all hints: memory + preferences + emotion + repeated-topic
            memory_ctx = self.memory.get_context(user_id) if self.memory else ""

            combined_hint = ""
            if memory_ctx:
                combined_hint = memory_ctx
            if pref_hint:
                combined_hint = combined_hint + "\n" + pref_hint if combined_hint else pref_hint
            if emotion_hint:
                combined_hint = combined_hint + "\n" + emotion_hint if combined_hint else emotion_hint
            if extra_hint:
                combined_hint = combined_hint + "\n" + extra_hint if combined_hint else extra_hint
            if partial_hint:
                combined_hint = combined_hint + "\n" + partial_hint if combined_hint else partial_hint

            # L1 滚动窗口（方案 §5.3）+ L2 增量摘要（方案 §5.4）：
            # 1) L2 触发检查（超阈值 → 分块滚动摘要，摘要存 session_summaries）
            # 2) L1 按 token 预算动态保留轮数 + 关键事实保底（八字/L3 is_key）
            # Task 5 deepNight：深夜不压缩 → 内容不入 L2 摘要（临时通道 24h 硬清理）
            # L5-2（I-1）：降级链路跳过 L2 压缩（compactor 为 LLM 调用，降级不调）
            if self.session_dao and not self._deep_night.get(user_id, False) \
                    and not self._downgraded.get(user_id, False):
                summary = self._maybe_compact(user_id)
                profile = ""
                if self.memory_system:
                    try:
                        profile = self.memory_system.get_profile_summary(user_id) or ""
                        # L3 按需召回（方案 §5.2/§5.5）：只注入与当前问题相关的条目
                        rels = self.memory_system.get_relevant_memories(
                            user_id, msg, top_k=3)
                        if rels:
                            rel_lines = "\n".join(f"- {r['content']}" for r in rels)
                            profile = (profile + "\n" if profile else "") + rel_lines
                    except Exception:
                        profile = ""
                key_facts = self._collect_key_facts(user_id)
                # 终审：白天 LLM 上下文排除 temp 倾诉消息（天亮就忘——
                # 24h 窗口内的夜间倾诉不流入白天对话，只删不用的兜底在 cleanup_temp）
                # 会话隔离：session_id 传入 → 上下文只取本会话消息（新开对话全新上下文）
                history = self.session_dao.get_context_for_llm(
                    user_id, history_limit=200, session_id=session_id, temp=False)
                messages = _assemble_context(
                    history, profile=profile, summary=summary,
                    current=msg, key_facts=tuple(key_facts),
                )
                if messages:
                    # P1 #2（批次 1 部署后暴露）：主链首轮 SYSTEM 必须带真实工具
                    # 清单，否则模型第一轮不知道有工具可调 → 工具链触发率 0%
                    # （CHAT_PROMPT 只有教学示例，清单在 _run_tool_loop 第二轮
                    # 才注入，形成"先有鸡还是先有蛋"死锁）
                    messages.insert(0, {"role": "system",
                                        "content": "[可用工具清单]\n"
                                        + build_tool_description()})
                    if combined_hint:
                        messages[-1] = {
                            "role": messages[-1]["role"],
                            "content": messages[-1]["content"] + f"\n\n{combined_hint}"
                        }
                    return self.llm.chat_conversation(
                        messages, stream_cb=stream_cb, lite=downgraded)
            # 无会话存储时，用单消息模式
            chat_msg = msg
            if combined_hint:
                chat_msg = msg + f"\n\n{combined_hint}"
            # P1 #2（B2-11）：单消息分支与主链首轮同口径——CHAT_PROMPT
            # （JSON 工单教学）+ [可用工具清单]，否则无会话存储配置下模型
            # 首轮同样不知道有真实工具可调（工具链触发率 0% 根因复现）。
            # lite 降级链（_chat_lite/CHAT_PROMPT_LITE 不调工具）忽略该参数。
            result = self.llm.chat(
                chat_msg, lite=downgraded,
                system_prompt=("[可用工具清单]\n" + build_tool_description()
                               + "\n\n" + CHAT_PROMPT))
            return result.response
        except Exception:
            return '我在这里。有什么想问的尽管说。若要看八字，请告知您的出生年月日时。'

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
