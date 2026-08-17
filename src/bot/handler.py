"""消息处理 - 意图识别和信息收集."""
import json
import logging
import os
import re
from datetime import date, timedelta
import time
from typing import Optional, Tuple, Any, Callable

logger = logging.getLogger(__name__)

from src.engines.bazi import BaziEngine, BaziResult
from src.engines.ziwei import ZiweiEngine, ZiweiResult
from src.engines.liuyao import LiuyaoEngine, LiuyaoResult
from src.engines.fengshui import FengshuiEngine, FengshuiResult
from src.engines.mianxiang import MianxiangEngine, MianxiangResult
from src.engines.zeri import ZeriEngine, ZeriResult, ZODIAC_MAP
from src.engines.dream import DreamEngine, DreamResult
from src.engines.hehun import HehunEngine
from src.engines.qimen import QimenEngine
from src.engines.xingming import XingmingEngine
from src.engines.message_analyzer import MessageAnalyzer, MessageAnalysis
try:
    from src.engines.advisor_v2 import AdaptiveAdvisor
    HAS_ADVISOR_V2 = True
except ImportError:
    from src.engines.advisor import AdvisorEngine
    HAS_ADVISOR_V2 = False
from src.rag.retriever import Retriever, ChunkResult
from src.llm.client import FortuneLLM, AnalysisResult
from src.config import is_experience_mode

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
from src.utils.cache import ResponseCache, is_cacheable
from src.ml.quality_predictor import QualityPredictor
from src.memory.user_memory import UserMemory, format_birth_line
# 命例相似度引擎已停用（2026-08-09 方案 v5 选 A 彻底移除，见 src/engines/similarity.py 注释）
from .formatter import split_long_message, format_error, format_loading
from src.reading_version import get_version_footer
# 阶段 3（方案 v5）：<tool_call>搜索: 引导按搜索可用性注入（不可用时返回空串不宣传）
from src.llm.prompts import _web_tool_guide_line

# AI 原生对话系统（Phase 1）— <tool_call> 标签解析与工具执行
# 意图识别已完全由 LLM 承担（_analyze_message），不再有任何硬编码关键词表。
from .tool_calls import (
    parse_tool_calls,
    strip_tool_calls,
    ToolCall,
    ToolResult,
    TOOL_REGISTRY,
    MAX_TOOL_ITERATIONS,
    SEARCH_UNAVAILABLE_HINT,
    RETRIEVAL_UNAVAILABLE_HINT,
)

# v8 阶段 3（过程体验）：工具调用事件文案（思考路径逐步点亮）
_TOOL_EVENT_LABELS = {
    "排盘": "正在排盘…",
    "检索": "正在查阅古籍…",
    "解梦": "正在翻阅梦兆典籍…",
    "风水": "正在勘察风水…",
    "择日": "正在择吉日…",
}

# ============================================================
# 择吉日（Task 2）: 6 场景同义词表 + 意图词表（双条件判定）
# ============================================================
ZERI_SCENE_SYNONYMS = {
    "嫁娶": ["嫁娶", "结婚", "婚礼", "订婚"],
    "搬家": ["搬家", "入宅", "乔迁"],
    "开业": ["开业", "开张", "开店"],
    "出行": ["出行", "旅游", "出差"],
    "提车": ["提车", "买车", "购车"],
    "签约": ["签约", "签合同", "过户"],
}
ZERI_INTENT_WORDS = ["选日子", "选个日子", "择日", "哪天", "吉日", "挑个时间", "好日子",
                     "换一批", "重新选", "还有别的日子吗"]
ZERI_SCENE_QUESTION = "您是给哪件事选日子？搬家/嫁娶/开业/出行/提车/签约？"

# Task 3（思考步骤渐进展示）：思考步骤文案已迁移到各 _do_*/_handle_*
# 的真实工作里程碑处（见各处的 _emit_stream_event 调用）——首条在开工时
# 发出（用户有"开始处理"反馈），之后每完成一步真实工作推进一步。
# 不再有 INTENT_THINKING_STEPS 预置字典（旧实现：开工前一个事件循环全部打光）。
# FAISS 语义检索（生产主路径）：276 万条古籍向量库（bge-m3 1024 维，
# inner_product）。检索工具只走 FAISS；不可用/无结果时走 LLM 自然对话兜底，
# 不降级关键词检索（见 _tool_search）。
from src.rag.faiss_retriever import get_faiss_retriever

# 阶段 5·来源体系与引用校验（方案 §3.0/§3.2 ③）：四类来源统一角标 +
# 回答后校验（不相关引用剔除）；网络检索（智谱 Web Search，可用才宣传）
from src.rag.citation import make_citation, verify_citations, public_citation, type_label
from src.rag.web_search import search_web, web_search_available

# 方案 B·引擎结果注入（AI 原生统一）：_handle_* 分析回复的系统尾巴
# （反馈提示/版本页脚）——润色时先剥离、润色后原样回接，防 LLM 改写
# v2026-08-17：去 emoji（PM 反馈回复 emoji 过多显 low），反馈字词保留
# （_handle_feedback 仍识别「准/不准」文本）
_FEEDBACK_PROMPT = "———\n这个分析对你有帮助吗？可回复「准」或「不准」告诉我"


class _FaissChunk:
    """FaissRetriever 返回的 dict → ChunkResult 兼容对象（解梦引擎按 r.text 取用）。

    设计文档阶段 0：276 万 FAISS 为生产主路径检索器；本地 Retriever(27k)
    与 FaissRetriever 返回结构不同，解梦等内部检索经此适配统一。
    """

    __slots__ = ("text", "source", "score", "category")

    def __init__(self, d: dict):
        self.text = d.get("text") or ""
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
CHINESE_HOUR_MAP = {
    "子时": 23, "丑时": 1, "寅时": 3, "卯时": 5, "辰时": 7,
    "巳时": 9, "午时": 11, "未时": 13, "申时": 15, "酉时": 17,
    "戌时": 19, "亥时": 21,
    "子": 23, "丑": 1, "寅": 3, "卯": 5, "辰": 7,
    "巳": 9, "午": 11, "未": 13, "申": 15, "酉": 17, "戌": 19, "亥": 21,
}

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
    Returns (month, day) or None."""
    m = re.search(
        r'(正月|一月|二月|三月|四月|五月|六月|七月|八月|九月|十月|'
        r'冬月|十一月|腊月|十二月|'
        r'正|一|二|三|四|五|六|七|八|九|十|冬|腊)'
        r'\s*月\s*'
        r'(初[一二三四五六七八九十]|'
        r'[一二二两三三四四五五六六七七八八九九]?十[一二三四五六七八九]?|'
        r'二十|廿[一二三四五六七八九]?|三十|卅十?|'
        r'零[一二三四五六七八九]|'
        r'[一二三四五六七八九])'
        r'\s*[日号]?',
        text
    )
    if m:
        month_str = m.group(1)
        day_str = m.group(2)
        # Parse month
        month_map = {"正": 1, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                     "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
                     "冬": 11, "腊": 12}
        month = month_map.get(month_str[0])
        if month is None:
            month = month_map.get(month_str.replace("月",""))
        # Parse day
        if "初" in day_str:
            day = _parse_cn_num(day_str.replace("初", ""))
        else:
            day = _parse_cn_num(day_str)
        if month and day and 1 <= month <= 12 and 1 <= day <= 31:
            return month, day
    return None

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
        # Multi-turn memory
        api_key = getattr(llm, 'api_key', '') if llm else ''
        self.memory = ConversationMemory(api_key) if api_key else None
        # L2 会话增量摘要（方案 §5.4）— 上下文超阈值触发，分块滚动压缩
        self.compactor = MemoryCompactor(api_key, model=getattr(llm, 'model', 'deepseek-v4-flash')) if api_key else None
        # L1/L2/L3 运行时状态：工具调用日志（本轮 <tool_call> 记录，落库用）
        self._tool_logs: dict = {}
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

        # 命例相似度引擎已停用（2026-08-09 方案 v5 选 A 彻底移除）：
        # 不再初始化 SimilarityEngine（原 data/wenzhen_charts.db 44k 命例对照），
        # 未问"像谁"不再输出命例/名人对照

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
        return reply + "\n\n———\n💬 这个分析对你有帮助吗？👍 有帮助  👎 不太准"

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
        if (re.search(r'\d{4}\s*[年/-]\s*\d{1,2}\s*[月/-]\s*\d{1,2}', msg)
                and not MessageAnalyzer.INTENT_HINT_PATTERN.search(msg)):
            return MessageAnalysis(needs_soothe=False, soothe_text="",
                                   emotion_label=None, intent="bazi")
        return MessageAnalysis(needs_soothe=False, soothe_text="",
                               emotion_label=None, intent=None)

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

    def _start_pregen_instant(self, msg: str):
        """消息含完整出生信息且意图为排盘（bazi）→ 排盘 + 秒回安抚提交后台线程。

        返回 Future（_do_bazi_analysis 消费）；非排盘意图/无出生信息/启动失败 → None。
        约束：不改变消息顺序与内容语义——秒回 LLM 调用仍先于主分析发出，
        只是准备阶段与意图分析重叠执行（一轮省 20-30s）。

        Task 2 终审修复：提交前先用 _quick_intent（MessageAnalyzer fast path，
        无 LLM 调用）判定意图——仅排盘类（bazi）才预生成；含意图提示词的
        hehun/ziwei/自由聊天等消息直接跳过（不再产生被丢弃的调用）。
        """
        try:
            # 排盘意图门控：fast path 未判 bazi（含意图词/无出生日期）→ 跳过
            if self._quick_intent(msg) != "bazi":
                return None
            parsed = self._extract_bazi_info(msg)
            if not parsed:
                return None
            year, month, day, hour, minute, city, gender = parsed
            return self._pregen_pool.submit(
                self._pregen_instant_worker,
                year, month, day, hour, minute, city, gender,
            )
        except Exception:
            logger.warning("秒回预生成启动失败（回退同步生成）", exc_info=True)
            return None

    def _pregen_instant_worker(self, year, month, day, hour, minute,
                               city, gender) -> str:
        """后台线程：排盘 + 秒回安抚生成（与意图分析重叠执行）。

        线程安全：engine.calculate 为确定性本地计算（无共享可变状态）；
        _gen_instant_reply 仅做只读访问 + LLM 调用（httpx 线程安全）。
        """
        try:
            result = self.engine.calculate(year, month, day, hour, minute,
                                           city, gender)
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

    def _tool_loop_analysis_hint(self, analysis: Optional[MessageAnalysis]) -> str:
        """阶段 2/3 最小实现：把理解 JSON（附加需求/联网需求）转成注入下一轮 LLM 的提示。

        - secondary_needs：要求逐项覆盖（多需求不遗漏）
        - needs_search 且搜索工具可用：引导 LLM 按需输出 <tool_call>搜索: 标签查证
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
        if getattr(analysis, "needs_search", False):
            try:
                from src.rag.web_search import web_search_available
                if web_search_available():
                    hints.append(
                        "【实时信息】此问题依赖实时信息（公司/行业/时事/最新数据）。"
                        "请先输出一次 <tool_call>搜索: 具体关键词</tool_call> 获取实时信息，"
                        "再基于搜索结果继续回答，不要凭记忆编造行业现状数据；"
                        "若搜索不可用，则明确告知用户"
                        "「实时信息暂不可用，以下按命理知识分析」。"
                    )
            except Exception:
                pass
        return "\n".join(hints)

    def _run_tool_loop(self, msg: str, user_id: str, reply: str,
                       stream_cb: Optional[Callable] = None,
                       analysis: Optional[MessageAnalysis] = None,
                       session_id: Optional[str] = None) -> str:
        """检测回复中的 <tool_call> 标签 → 执行工具 → 结果以 system 注入 → 再次调 LLM。

        - 最多 MAX_TOOL_ITERATIONS（2）次迭代，防死循环
        - 解析失败/LLM 调用失败：静默降级，返回原文（去标签）
        - 工具执行失败：错误注入 system 提示，对话继续

        阶段 2/3 最小实现（方案 v5）：
        - analysis.secondary_needs → 注入"附加需求逐项覆盖"（多需求不遗漏）
        - analysis.needs_search 且搜索可用 → 注入联网搜索引导（LLM 决定是否 <tool_call>搜索:）

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
        retrieval_hit = "unused"
        # 阶段 5：本轮引用来源（user_id → list）由工具/处理器注册；
        # 不在本方法清空（处理器注册的引用要保留到本方法末尾统一校验）

        for _ in range(MAX_TOOL_ITERATIONS):
            calls = parse_tool_calls(reply)
            if not calls:
                break
            logger.info("工具调用执行 user=%s calls=%s",
                        user_id, [c.name for c in calls])
            visible = strip_tool_calls(reply)
            results = []
            for c in calls:
                # v8 阶段 3：工具调用前先发"思考路径"事件（前端点亮 ● → ✓）
                if stream_cb is not None:
                    try:
                        stream_cb("tool", {"text": _TOOL_EVENT_LABELS.get(
                            c.name, f"正在{c.name}…")})
                    except Exception:
                        pass
                r = self._execute_tool_call(c.name, c.params, user_id,
                                            user_question=msg)
                results.append(r)
                executed_calls.append({
                    "type": r.name,
                    "params": (c.params or "")[:200],
                    "hit": bool(r.ok),
                })
                if r.name in ("检索", "搜索"):
                    retrieval_hit = "hit" if r.ok else "miss"

            messages = [{"role": "system", "content":
                "你是易理明灯，请基于工具执行结果继续自然地完成你的回复。"
                "回答纪律：直接专业作答，禁止油滑/套近乎开场白；"
                "严格紧扣用户问题，用户没问的（名人相似、旁支话题）不得主动展开。"
                + self._tool_loop_analysis_hint(analysis)}]
            if history:
                messages.extend(history)
            else:
                messages.append({"role": "user", "content": msg})
            if visible:
                messages.append({"role": "assistant", "content": visible})
            results_text = "\n\n".join(
                f"【工具：{r.name}】\n{r.text}" for r in results
            )
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
                    "不要再次输出 <tool_call> 标签。"
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
                from src.llm.client import deepseek_anthropic_completion
                new_reply = deepseek_anthropic_completion(
                    api_key, messages, model=self.llm.model or "deepseek-v4-flash",
                    max_tokens=2000, temperature=0.7, timeout=60.0,
                    stream_cb=stream_cb,
                )
            except Exception:
                break  # LLM 调用失败 → 静默降级返回原文
            if not new_reply:
                break
            reply = new_reply

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

        refs: retriever.search 返回的 ChunkResult 列表（也兼容 dict）。
        """
        if not refs:
            return
        items = []
        start = self._alloc_citations(user_id, min(limit, len(refs)))
        for i, ref in enumerate(list(refs)[:limit], start=start):
            src = ref.get("source") if isinstance(ref, dict) else getattr(ref, "source", "")
            text = ref.get("text") if isinstance(ref, dict) else getattr(ref, "text", "")
            src = src or ""
            text = (text or "")[:400]
            if not text:
                continue
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
        model = getattr(self.llm, 'model', '') or "deepseek-v4-flash"
        if not isinstance(model, str):
            model = "deepseek-v4-flash"

        # 1) 剥离系统尾巴（版本页脚/反馈提示），润色后原样回接
        body = draft
        tail = ""
        footer = get_version_footer()
        footer_block = f"---\n{footer}"
        if footer and body.endswith(footer_block):
            body = body[: -len(footer_block)].rstrip()
            tail = f"\n\n{footer_block}"
        if body.endswith(_FEEDBACK_PROMPT):
            body = body[: -len(_FEEDBACK_PROMPT)].rstrip()
            tail = f"\n\n{_FEEDBACK_PROMPT}" + tail

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
                "你必须先输出一次 <tool_call>搜索: 具体关键词</tool_call> 让系统联网查证，"
                "收到搜索结果后再完成最终回复；不要凭记忆编造行业现状数据；"
                "若搜索不可用，则明确告知用户「实时信息暂不可用，以下按命理知识分析」。\n")
               if search_hint else "")
            + "1. 保留全部实质性数据（四柱/十神/卦象/日期/评分/宜忌条目等），"
            "可以调整表达结构，但不要删改、不要编造数据；\n"
            "2. 像朋友聊天一样组织语言，不要提及「引擎」「草稿」「检索」「系统」"
            "等技术词汇；\n"
            f"3. 引用规则：本轮可用来源：{cite_hint}。回答中用到来源里的具体数据、"
            "古籍记载或命盘信息时，必须在对应陈述后标注编号"
            "（如「古籍《X》载：…[1]」「你的命盘：庚午年…[1]」），"
            "且至少标注 1 个实际使用到的编号（全部内容均与来源无关时才可不标）；"
            "只标注与用户问题直接相关的内容，不相关不标注；\n"
            "4. 如果还缺少依据，可以输出一次 <tool_call>检索: 关键词</tool_call>"
            + _web_tool_guide_line() + "\n"
            "系统会执行后把结果交回，你再继续完成回复；\n"
            "5. 若原结果本身已是清晰的列表/卡片格式（如宜忌、时辰表、排盘卡片），"
            "宜忌/时辰表等表格用 markdown 表格格式呈现、不要用代码块包裹，"
            "保持该结构完整，不要合并或删减条目，仅补充口语化的开头和结尾；\n"
            "6. 直接输出给用户的回复文本，不要解释过程；\n"
            "7. 【硬性要求】回复中禁止使用任何 emoji 表情符号"
            "（表情图标、颜文字、装饰符号都不用），只用文字与中文标点表达语气。"
            + (("\n\n" + extra_hint) if extra_hint else "")
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
                    "（请按上面的硬性要求先输出 <tool_call>搜索: 关键词</tool_call>"
                    " 联网查证后再继续）"})
        else:
            messages.append({"role": "user", "content": msg})
        _t0 = time.monotonic()
        try:
            from src.llm.client import deepseek_anthropic_completion
            polished = deepseek_anthropic_completion(
                api_key, messages, model=model,
                max_tokens=2000, temperature=0.7, timeout=60.0,
                stream_cb=stream_cb,
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
        if chart_url and chart_url not in polished:
            polished = polished + "\n\n" + chart_url
        return polished + tail

    def _execute_tool_call(self, name: str, params: str, user_id: str,
                           user_question: str = "") -> ToolResult:
        """执行单个工具调用，返回可注入对话的结果文本。"""
        if name == "排盘":
            return self._tool_bazi(params, user_id)
        if name == "检索":
            return self._tool_search(params, user_id=user_id,
                                     user_question=user_question)
        if name == "搜索":
            return self._tool_web_search(params, user_id=user_id)
        if name == "解梦":
            return self._tool_dream(params, user_id)
        if name == "风水":
            return self._tool_fengshui(params)
        if name == "择日":
            return self._tool_zeri(params, user_id)
        return ToolResult(name, False, f"未知工具「{name}」，请直接和用户正常聊天。")

    def _tool_bazi(self, params: str, user_id: str) -> ToolResult:
        """工具「排盘」：解析出生信息（文本描述）→ BaziEngine.calculate。"""
        if self.engine is None:
            return ToolResult("排盘", False, "「排盘」工具暂不可用，请直接与用户聊天。")
        parsed = self._extract_bazi_info(params)
        if parsed is None:
            # Task 1 排盘档案打通：解析失败先试档案（bazi_info + persons 兜底）填参，
            # 年/月/日至少齐才排盘；hour/minute 缺省 0（与 _extract_bazi_info 缺时辰一致）
            profile = self._get_user_birth_profile(user_id)
            if profile and profile.get("year") and profile.get("month") and profile.get("day"):
                parsed = (
                    profile.get("year"), profile.get("month"), profile.get("day"),
                    profile.get("hour") if profile.get("hour") is not None else 0,
                    profile.get("minute") if profile.get("minute") is not None else 0,
                    profile.get("city") or "",
                    profile.get("gender") or "unknown",
                )
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
            result = self.engine.calculate(year, month, day, hour, minute, city, gender)
        except Exception as e:
            return ToolResult("排盘", False, f"排盘引擎执行失败：{str(e)[:100]}")
        # 持久化：与 _do_bazi_analysis 保持一致的记忆/画像逻辑
        # 阶段 5（方案 v5）：subject=other（帮他人排盘）不写入本人档案（数据库层同步保护）
        try:
            _subject = (self._analysis_facts.get(user_id) or {}).get("subject", "self")
            _facts_this = self._analysis_facts.get(user_id) or {}
            if _subject != "other":
                self.dao.save_user_bazi(user_id, {
                    "year": year, "month": month, "day": day,
                    "hour": hour, "minute": minute,
                    "city": city, "gender": gender,
                    "bazi": result.bazi,
                })
            # P2 多人档案：对话建档（subject=self 年份不同→新建命主N；other 按关系/姓名）
            self._sync_person_profile(user_id, {
                "year": year, "month": month, "day": day,
                "hour": hour, "minute": minute,
                "city": city, "gender": gender,
            }, subject=_subject, facts=_facts_this)
            self.dao.save_consultation(user_id, params, result)
        except Exception:
            pass
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
            src = ref.get("source") or ref.get("title") or "古籍"
            rel = (ref.get("score") or 0.0) / top1 if top1 > 0 else 1.0
            items.append(make_citation(
                i, "book", ref["text"][:400],
                title=f"《{src}》" if "《" not in src else src,
                source=str(ref.get("title") or src),
            ))
            lines.append(
                f"[{i}] 【古籍】《{src}》\"{ref['text'][:200]}\""
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

    def _tool_web_search(self, params: str, user_id: str = "") -> ToolResult:
        """工具「搜索」（阶段 5·网络检索）：智谱 Web Search API → Top 3-5。

        结果带来源 URL（type="web"）注入；服务不可用 → 标 unavailable
        （prompt 不宣传，执行时自然降级，不阻断对话）。
        """
        if not web_search_available():
            # C6：搜索不可用 → 明说降级（明确告知用户实时信息受限），不静默
            return ToolResult(
                "搜索", False, SEARCH_UNAVAILABLE_HINT,
                needs_info=True,
            )
        query = (params or "").strip()
        if not query:
            return ToolResult(
                "搜索", False,
                "请像朋友聊天一样自然地向用户询问想搜索哪方面的内容。",
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
        ④ user_bazi（dao.get_user_bazi → shengxiao/day_gan/month_zhi 映射）
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
                lines.append(f"宜：{'、'.join(c.yi)}")
                lines.append(f"忌：{'、'.join(c.ji)}")
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
        """择日场景判定：6 场景同义词表命中 → 规范场景名（嫁娶/搬家/开业/出行/提车/签约）。"""
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
            try:
                saved = self.dao.get_user_bazi(user_id)
            except Exception:
                saved = None
        if not saved:
            return None
        bazi = saved.get("bazi") or []
        # 表单选填八字: 解析字段 dict 无四柱 → 排盘补全(引擎异常 → 放弃, None 兜底不误伤)
        if not bazi and saved.get("year"):
            try:
                result = self.engine.calculate(
                    int(saved["year"]), int(saved["month"]), int(saved["day"]),
                    int(saved.get("hour") or 0), int(saved.get("minute") or 0),
                    str(saved.get("city") or "北京"), str(saved.get("gender") or "unknown"))
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
                             facts: Optional[dict] = None) -> None:
        """P2 多人档案：排盘后同步建档（对话建档，方案 v5）。

        - subject=self：默认 person 与本次排盘年份不一致（年份不同）→
          新建 person（name="命主N"）；一致 → 更新默认 person 出生信息；
          无档案 → 建档 name="我" relation="自己"（含旧单档案自动迁移）
        - subject=other：按 facts 关系/姓名建 person；已存在同生日 person 则复用。
        先有先用保护照旧（gender 冲突不覆盖逻辑仍在 save_bazi_info / users.bazi_info 层）。
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
                if default and default.get("birth_year") and \
                        default["birth_year"] != birth.get("year"):
                    # 默认命主与本次排盘年份不一致 → 新建"命主N"（默认不变）
                    pdao.create_person(
                        user_id, name=f"命主{pdao.count_persons(user_id) + 1}",
                        relation="其他", birth=b)
                elif default:
                    pdao.update_person(user_id, default["id"], birth=b)
                else:
                    pdao.create_person(user_id, name="我", relation="自己",
                                       is_default=True, birth=b)
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

    def process(self, message: str, user_id: str,
                stream_cb: Optional[Callable] = None, deep_night: bool = False,
                session_id: Optional[str] = None) -> str:
        """处理用户消息，返回回复。

        stream_cb（v8 流式阶段 3）：提供时把生成过程实时回调出去——
          ("chunk", {"text": ...}) 正文增量 / ("tool", {"text": ...}) 工具调用 /
          ("thinking", {"text": ...}) 思考步骤；
        不提供时行为与旧版完全一致（/api/chat 兼容）。

        deep_night（Task 5 倾诉临时通道）：跳过 L2 事实/事件捕捉/演化链等记忆管线，
        本轮回合消息全部以 temp 标记落库（24h 硬清理兜底），并注入深夜语气层。

        session_id（会话隔离）：前端新开对话时生成新会话标识，AI 上下文只取本会话
        消息（不带上个对话内容）；None = 旧行为（按用户全量取上下文）。
        """
        msg = message.strip()
        self._deep_night[user_id] = bool(deep_night)
        deep = self._deep_night.get(user_id, False)
        # 清理上一轮残留的工具日志（xuetang/advisor/confidant 等早退分支不消费）
        self._pop_tool_log(user_id)
        # 阶段 5：清理上一轮残留的引用来源（早退分支不注册，防泄漏）
        self._citations.pop(user_id, None)

        # Step -2: Cache check (D2 speed optimization)
        # 终审：deepNight 请求跳过缓存读写——夜里语气/陪伴类回复不可命中白天缓存，
        # 缓存键也隐含用户+消息，避免倾诉缓存串味（应修 #缓存命中 temp/语气）
        # 会话隔离：缓存键掺入 session_id——新会话不命中旧会话同文回复的缓存
        # （否则新开对话问同一句仍会拿到旧会话缓存答案，白做隔离）
        if is_cacheable(msg) and not deep:
            cached = self.cache.get(msg, user_id, session_id or "")
            if cached:
                # 兜底：缓存回复可能存于旧格式（TOOL 标签残留）——出口强制 strip
                return strip_tool_calls(cached) or cached

        # Step -1: 反馈检测 (👍/👎) — learn from user feedback
        if msg in ("👍", "👎", "好评", "差评", "准", "不准", "good", "bad") or msg.startswith("👍") or msg.startswith("👎"):
            return self._handle_feedback(msg, user_id, session_id=session_id)

        # Step 0.6: P1-2 额度检查 — 免费用户每日3次限制
        remaining, is_limited = self._check_quota(user_id)
        if is_limited:
            if remaining <= 0:
                msg_warning = "💡 你今天的免费额度已用完。成为会员即可无限畅聊，基础版仅需 19.9 元/月。\n\n回复「会员」了解更多升级方案。\n或回复「👍」告诉我之前的分析有用，帮助我改进～"
                if self.session_dao:
                    self.session_dao.add_message(user_id, "assistant", msg_warning,
                                                 temp=deep, session_id=session_id)
                return msg_warning
            elif remaining == 1:
                # 倒计时提醒：仅剩1次免费机会
                msg = (
                    "💡 你还有 1 次免费提问机会，之后可以升级会员继续使用。\n\n"
                    + msg
                )

        # Handle "会员" keyword — show upgrade info
        if msg.strip() in ("会员", "升级", "付费", "套餐", "价格", "多少钱"):
            upgrade_msg = (
                "🌟 **易理明灯会员计划**\n\n"
                "📌 **基础版** 19.9元/月\n"
                "  - 每月50次完整命理分析\n"
                "  - 含命盘图表\n"
                "  - 无广告\n\n"
                "📌 **专业版** 39.9元/月\n"
                "  - 每月150次分析\n"
                "  - 含PDF详细报告\n"
                "  - 优先回复\n\n"
                "📌 **年度版** 168元/年（省65%）\n"
                "  - 专业版全部功能\n"
                "  - 每周运势推送\n\n"
                "💡 免费用户每天可享3次基础分析。\n"
                "回复「开通基础版」即可升级！"
            )
            if self.session_dao:
                self.session_dao.add_message(user_id, "assistant", upgrade_msg,
                                             temp=deep, session_id=session_id)
            return upgrade_msg

        # Step 0.5: AI 分析 — 情绪 + 意图 in ONE call (no keywords, no two calls)
        # Task 2 等待时长优化：消息含完整出生信息时，把「排盘 + 秒回安抚」提交到
        # 后台线程与意图分析并行发出（二者无依赖，可重叠）；bazi/career 路由由
        # _do_bazi_analysis 消费，其他路由静默丢弃（flash 成本低、无墙钟代价）。
        self._pregen_instant[user_id] = self._start_pregen_instant(msg)
        # v2026-08-17（思考过程元宝式）：意图分析（~1s）此前无思考事件，
        # 用户发送后胶囊迟迟不出现——首条事件在开工时即发出
        self._emit_stream_event(stream_cb, "thinking", "正在领会你的意思…")
        _t0 = time.monotonic()
        analysis = self._analyze_message(msg, user_id, session_id=session_id)
        logger.info("[timing] stage=intent duration=%.1fs",
                    time.monotonic() - _t0)
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
            analysis_hint = self._tool_loop_analysis_hint(analysis)
        except Exception:
            analysis_hint = ""
        if analysis_hint:
            logger.info("P3 联网引导注入 user=%s needs_search=%s hint=%s",
                        user_id, getattr(analysis, "needs_search", False),
                        analysis_hint[:60].replace("\n", " "))

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
        if self.session_dao:
            self.session_dao.add_message(
                user_id, "user", msg, intent=analysis.intent,
                emotion=analysis.emotion_label,
                model=getattr(self.llm, 'model', '') or '',
                safety_flag=self._safety_flag(msg),
                temp=deep,
                session_id=session_id,
            )

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
        if any(kw in msg for kw in ["学堂", "学习教程", "命理入门"]):
            self._consume_quota(user_id)
            reply = self._handle_xuetang(msg, user_id)
            if self.session_dao:
                self.session_dao.add_message(user_id, "assistant", reply, intent="xuetang",
                                             temp=deep, session_id=session_id)
            return reply

        # Task 5: advisor keyword fallback — catch "建议"/"怎么办" even if AI misses it
        if any(kw in msg for kw in ["建议", "怎么办", "有什么建议", "帮我分析", "我该怎么做"]):
            self._consume_quota(user_id)
            reply = self._handle_advisor(msg, user_id)
            if self.session_dao:
                self.session_dao.add_message(user_id, "assistant", reply, intent="advisor",
                                             temp=deep, session_id=session_id)
            return reply

        # H1: 心事树洞 — user sharing a story (overrides fortune intent when no birth info)
        if analysis.is_sharing:
            # Only skip confidant if user explicitly provides birth date info
            has_birth_info = bool(re.search(r'\d{4}\s*[年/-]', msg))
            if not has_birth_info:
                self._consume_quota(user_id)
                reply = self._handle_confidant(msg, user_id, analysis,
                                               session_id=session_id)
                if self.session_dao:
                    self.session_dao.add_message(user_id, "assistant", reply,
                                                 temp=deep, session_id=session_id)
                return reply

        if analysis.intent is None:
            self._consume_quota(user_id)
            hints = [h for h in (topic_hint, analysis_hint) if h]
            if deep:
                from src.bot.night_persona import NIGHT_TONE_HINT
                hints.insert(0, NIGHT_TONE_HINT)
            reply = self._free_chat(msg, user_id, emotion_label=analysis.emotion_label,
                                    extra_hint="\n".join(hints),
                                    stream_cb=stream_cb, session_id=session_id)
            # AI 原生（Phase 1）：<tool_call> 工具调用循环
            reply = self._run_tool_loop(msg, user_id, reply, stream_cb=stream_cb,
                                        analysis=analysis, session_id=session_id)
            # 阶段 2：本轮工具调用日志 → 落库字段
            tool_log = self._pop_tool_log(user_id)
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
            # P2 演化链：结论摘要 append 到 topic 时间线（cap 5）
            # Task 5 deepNight：倾诉临时通道不记演化链
            if not deep:
                self._record_evolution(user_id, topic, reply)
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
            "calendar": self._handle_calendar,
            "hourly": self._handle_hourly,
            "xuetang": self._handle_xuetang,
            "advisor": self._handle_advisor,
            "career": self._handle_career,
        }

        handler = handler_map.get(analysis.intent)
        if handler:
            try:
                self._consume_quota(user_id)
                # 会话隔离：解梦需读会话历史（P0-1 已有梦境免重复描述），
                # 仅 dream 处理器感知 session_id；其余引擎处理器不读历史
                if analysis.intent == "dream":
                    reply = handler(msg, user_id, stream_cb=stream_cb,
                                    session_id=session_id)
                else:
                    reply = handler(msg, user_id, stream_cb=stream_cb)
            except Exception as e:
                reply = f"⚠️ 服务暂时不可用：{str(e)[:100]}\n\n请稍后再试或换一种命理方式。"
        else:
            reply = f"🔧 {analysis.intent} 模块暂未开放，试试：\n• 八字命理\n• 紫微斗数\n• 易经占卜\n• 风水分析"

        # 方案 B·引擎结果注入（AI 原生统一）：有意图的问题也走 LLM 自主二次生成。
        # _handle_* 完成真实分析（注册了 engine/book 引用）时，把引擎结果注入
        # system → LLM 以豆包式语气生成最终回复（可再输出 <tool_call> 补检索）；
        # 信息收集/错误回复不注册引用 → 不润色，保持原样。
        # xuetang/advisor/confidant 为独立对话模式（早退分支），保持现状。
        if (analysis.intent not in ("xuetang", "advisor")
                and reply and not reply.startswith("⚠️")
                and self._citations.get(user_id)):
            try:
                reply = self._polish_with_engine_draft(
                    msg, user_id, reply, stream_cb,
                    extra_hint="\n".join(h for h in (topic_hint, analysis_hint) if h),
                    search_hint=analysis_hint, session_id=session_id)
            except Exception:
                pass  # 润色异常 → 保留引擎原稿（静默降级，行为不劣于现状）

        # AI 原生（Phase 1）：<tool_call> 工具调用循环
        reply = self._run_tool_loop(msg, user_id, reply, stream_cb=stream_cb,
                                    analysis=analysis, session_id=session_id)
        # 阶段 2：本轮工具调用日志 → 落库字段
        tool_log = self._pop_tool_log(user_id)
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
        if is_cacheable(msg) and not deep:
            self.cache.set(msg, reply, user_id, scope=session_id or "")

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
        """处理图片输入 — 支持面相分析 + 风水 + 通用。

        优先尝试 CV 面相分析（如果人脸检测成功），否则根据关键词路由。
        """
        if not image_url:
            return "📷 请提供图片链接以便进行分析。"

        # Try face reading first
        face_result = self._try_face_reading(image_url, user_text)
        if face_result:
            return face_result

        # Try palm reading
        palm_result = self._try_palm_reading(image_url, user_text)
        if palm_result:
            return palm_result

        # Fall back to keyword-based routing
        if any(kw in user_text for kw in ["户型", "风水", "家居", "布局", "房间"]):
            return self._handle_image_fengshui(image_url, user_text)
        elif any(kw in user_text for kw in ["手相", "看相", "手掌"]):
            return self._handle_image_mianxiang(image_url, user_text)
        else:
            return self._handle_image_generic(image_url, user_text)

    def _try_palm_reading(self, image_url: str, user_text: str = "") -> str:
        """Try CV palm analysis on an image. Returns result or None."""
        try:
            import urllib.request, tempfile, os
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                urllib.request.urlretrieve(image_url, tmp.name)
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
            api_key = getattr(self.llm, 'api_key', '') if self.llm else ''
            return generate_palm_report(metrics, retriever=self.retriever, api_key=api_key)
        except Exception:
            return None

    def _try_face_reading(self, image_url: str, user_text: str = "") -> str:
        """Try CV face analysis on an image. Returns result or None if no face."""
        try:
            import urllib.request, tempfile, os
            # Download image to temp file
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                urllib.request.urlretrieve(image_url, tmp.name)
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
            api_key = getattr(self.llm, 'api_key', '') if self.llm else ''
            report = generate_report(
                metrics,
                retriever=self.retriever if hasattr(self, 'retriever') else None,
                api_key=api_key,
            )
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
                       stream_cb: Optional[Callable] = None) -> str:
        """处理事业适配请求（career 意图）：排盘 + LLM 自主分析十神/五行与行业适配。

        与 _handle_bazi 同一分析管线；消息命中 career 场景关键词时，
        _route_by_scenario 自动注入【场景聚焦：事业分析】提示
        （行业五行适配/跳槽转型节点/职场贵人运）。
        不注入名人相似对照（名人库停用方案见 similarity 移除清单，未确认前不落笔）。
        """
        return self._handle_bazi(msg, user_id, stream_cb=stream_cb)

    def _get_user_birth_profile(self, user_id: str) -> Optional[dict]:
        """获取用户出生信息档案（Task 1 排盘档案打通，单向只读）。

        ① users.bazi_info 非空且有 year 键 → 原样返回；
        ② 否则查 persons 表主档案：默认档案有出生数据 → 保持默认优先
           （默认通常即用户本人）；默认无出生数据 → 取最近更新
           （updated_at 降序）的有出生数据的档案（Brief 要求）。
           把 birth_year/birth_month/birth_day/birth_hour/birth_minute/gender/city
           映射为 {year, month, day, hour, minute, city, gender}；
        ③ 都没有 → None。

        不回写 persons、不新增写路径（单向打通）。
        """
        if not self.dao:
            return None
        try:
            bazi = self.dao.get_user_bazi(user_id)
        except Exception:
            bazi = None
        if bazi and bazi.get("year"):
            return bazi
        # ② persons 档案兜底（db_path 访问已置于 self.dao 守卫内）
        try:
            from src.storage.person_dao import PersonDAO
            pdao = PersonDAO(self.dao.db_path)
            persons = pdao.list_persons(user_id)
            default = next((p for p in persons if p.get("is_default")), None)
            if default and default.get("birth_year"):
                pick = default
            else:
                candidates = [p for p in persons if p.get("birth_year")]
                if not candidates:
                    return None
                # 选最近更新的有出生数据的档案（updated_at 降序取最大者）
                pick = max(candidates, key=lambda p: p.get("updated_at") or "")
            return {
                "year": pick.get("birth_year"),
                "month": pick.get("birth_month"),
                "day": pick.get("birth_day"),
                "hour": pick.get("birth_hour"),
                "minute": pick.get("birth_minute"),
                "city": pick.get("city") or "",
                "gender": pick.get("gender") or "unknown",
            }
        except Exception:
            pass
        return None

    def _handle_bazi(self, msg: str, user_id: str,
                     stream_cb: Optional[Callable] = None) -> str:
        """处理八字请求"""
        parsed = self._extract_bazi_info(msg)

        if parsed is None:
            # 检查是否有已保存的信息 — 自动复用（bazi_info + persons 档案兜底）
            saved = self._get_user_birth_profile(user_id)
            if saved and saved.get("year") and saved.get("month") and saved.get("day"):
                # AI generates a brief acknowledgment that we're using saved info
                ack = self._gen_reuse_acknowledgment(msg, saved)
                result = self._do_bazi_analysis(
                    saved.get("year"), saved.get("month"), saved.get("day"),
                    saved.get("hour") if saved.get("hour") is not None else 0,
                    saved.get("minute") if saved.get("minute") is not None else 0,
                    saved.get("city") or "",
                    saved.get("gender") or "unknown",
                    msg, user_id, stream_cb=stream_cb,
                )
                return ack + "\n\n" + result if ack else result

            # AI generates contextual info-collection prompt
            return self._gen_info_collection_prompt(msg)

        year, month, day, hour, minute, city, gender = parsed
        return self._do_bazi_analysis(
            year, month, day, hour, minute, city, gender, msg, user_id,
            stream_cb=stream_cb,
        )

    COMMON_CITIES = {"北京", "上海", "广州", "深圳", "天津", "重庆", "杭州", "南京",
                     "成都", "武汉", "西安", "苏州", "长沙", "郑州", "青岛", "大连",
                     "厦门", "宁波", "福州", "合肥", "沈阳", "哈尔滨", "昆明", "贵阳",
                     "南宁", "海口", "兰州", "银川", "西宁", "拉萨", "乌鲁木齐",
                     "呼和浩特", "石家庄", "太原", "济南", "南昌"}

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
        """从消息中提取八字信息 — 支持农历中文数字、时间描述、多种格式."""
        # Step 1: Extract year
        year = None
        ym = re.search(r'(\d{4})\s*年|公历\s*(\d{4})|阳历\s*(\d{4})|公元\s*(\d{4})', msg)
        if ym:
            year = int(ym.group(1) or ym.group(2) or ym.group(3) or ym.group(4))

        if not year or year < 1900 or year > 2100:
            return None

        # Step 2: Extract month and day — try Chinese lunar first
        month = day = None
        cn_md = _parse_cn_month_day(msg)
        if cn_md:
            month, day = cn_md
        else:
            # Try numeric date: 8月15日, 8-15, 10月10日, 11.20
            md = re.search(r'(\d{1,2})\s*[月\-/.]\s*(\d{1,2})\s*[日号]?', msg)
            if md:
                month = int(md.group(1))
                day = int(md.group(2))

        if not month or not day or month < 1 or month > 12 or day < 1 or day > 31:
            return None

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

        # Step 5: Extract city
        city = "北京"
        city_match = re.search(r'([一-鿿]{2,4}(?:市|省))', msg)
        if city_match:
            city = city_match.group(1)
        else:
            city_match = re.search(r'({})'.format('|'.join(self.COMMON_CITIES)), msg)
            if city_match:
                city = city_match.group(1)

        return (year, month, day, hour, minute, city, gender)

    @staticmethod
    def _emit_stream_event(stream_cb, evt_type: str, text: str) -> None:
        """安全地发出流式进度事件（流式模式；回调异常静默忽略）。"""
        if stream_cb is None:
            return
        try:
            stream_cb(evt_type, {"text": text})
        except Exception:
            pass

    def _do_bazi_analysis(
        self, year, month, day, hour, minute, city, gender, question, user_id,
        stream_cb: Optional[Callable] = None,
    ) -> str:
        """执行八字分析"""
        # 1. 排盘（流式模式先发进度事件，避免引擎阶段长沉默触发看门狗）
        self._emit_stream_event(stream_cb, "thinking", "正在排盘…")
        result = self.engine.calculate(year, month, day, hour, minute, city, gender)

        # 2. 秒回安抚（在LLM分析前生成，最终拼接到回复开头）
        # Task 2：优先取并行预生成结果（意图分析期间已完成），未就绪则同步兜底
        instant_reply = self._consume_pregen_instant(user_id)
        if not instant_reply:
            instant_reply = self._gen_instant_reply(result)

        # 3. 保存用户数据
        # 阶段 5（方案 v5）：subject=other（帮他人排盘）不写入本人档案（数据库层同步保护）
        _subject = (self._analysis_facts.get(user_id) or {}).get("subject", "self")
        _facts_this = self._analysis_facts.get(user_id) or {}
        if _subject != "other":
            self.dao.save_user_bazi(user_id, {
                "year": year, "month": month, "day": day,
                "hour": hour, "minute": minute,
                "city": city, "gender": gender,
                "bazi": result.bazi,
            })
        # P2 多人档案：对话建档（subject=self 年份不同→新建命主N；other 按关系/姓名）
        self._sync_person_profile(user_id, {
            "year": year, "month": month, "day": day,
            "hour": hour, "minute": minute,
            "city": city, "gender": gender,
        }, subject=_subject, facts=_facts_this)
        self.dao.save_consultation(user_id, question, result)

        # Phase 3: Save to user memory system
        # 阶段 5（方案 v5）：subject=other（帮他人排盘）不写入本人画像
        _subject = (self._analysis_facts.get(user_id) or {}).get("subject", "self")
        if self.memory_system and _subject != "other":
            self.memory_system.save_bazi_info(user_id, {
                "year": year, "month": month, "day": day,
                "hour": hour, "minute": minute,
                "city": city, "gender": gender,
                "bazi": result.bazi,
                "day_master": getattr(result, "day_master", ""),
            }, subject=_subject)
            # L3（方案 §5.5 来源②）：八字 → profile 关键事实条目
            self._persist_l3_bazi(user_id, {
                "year": year, "month": month, "day": day,
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

        # 4. P1-3: If gender is unknown, add instruction for gender-neutral language
        gender_note = ""
        if gender == "unknown":
            gender_note = "\n\n【注意：用户未提供性别，分析时请使用中性表述，如「命主」而非「他/她」，不要默认任何性别倾向】"
            question_with_gender = question + gender_note
        else:
            question_with_gender = question

        # 4. 检索古籍
        self._emit_stream_event(stream_cb, "thinking", "正在查阅古籍…")
        search_query = f"{result.day_master} {question}"
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

        # Phase 2: Scenario-aware structured report
        scenario_info = self._route_by_scenario(question_with_gender, user_id)
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
                analysis = self.llm.analyze(
                    result, refs, question_with_gender,
                    extra_system_prompt=pref_extra if pref_extra else None,
                )
        finally:
            logger.info("[timing] stage=main_analysis duration=%.1fs",
                        time.monotonic() - _t0)

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

        if instant_reply:
            reply = instant_reply + "\n\n---\n\n" + reply

        # 9. AI 生成下文引导（替代硬编码的「还想了解什么？」）
        followup = self._gen_followup_questions(result, question)
        if followup:
            reply += "\n\n" + followup

        # 10. 反馈提示 (F3)
        reply = self._add_feedback_prompt(reply)

        # Add reading version footer for traceability and reproducibility
        reply += f"\n\n---\n{get_version_footer()}"

        # 11. 记录对话记忆
        if self.memory:
            self.memory.add_interaction(user_id, question, reply, intent="bazi",
                                        key_data={"day_master": result.day_master,
                                                  "geju": getattr(result, 'geju', '')})

        return reply

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
                model="deepseek-v4-flash",
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
                model=getattr(self.llm, 'model', '') or "deepseek-v4-flash",
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

    def _gen_reuse_acknowledgment(self, msg: str, saved: dict) -> str:
        """Generate a brief acknowledgment when reusing saved bazi info."""
        bazi_str = " ".join(saved.get("bazi", ["?"])[:4]) if saved.get("bazi") else ""
        if not bazi_str:
            return ""
        prompt = (
            f"用户之前已提供过八字信息（{bazi_str}），现在用户问：「{msg}」。\n"
            "请用15字以内的现代中文，自然地告诉用户「基于你之前的八字信息来看...」。\n"
            "不要用「小友」「老夫」。直接返回一句话，不要引号不要JSON。"
        )
        return self._quick_flash(prompt, max_tokens=60)

    def _gen_info_collection_prompt(self, msg: str) -> str:
        """AI generates contextual info-collection prompt based on what user said."""
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

            prompt = (
                f"用户的命盘排出来了：{chart_info}。\n"
                "请生成一段30-50字的个性化开场白：\n"
                "1. 先展示八字和日主\n"
                "2. 从命盘中找一个最亮眼的亮点（神煞、格局、五行特色等），用温暖现代的语气说出来\n"
                "3. 风格：像朋友发来的消息，不要用'小友''老夫'等老气称呼\n"
                "4. 结尾用🌟\n"
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
            saved = self.dao.get_user_bazi(user_id)
            if saved:
                return self._do_ziwei_analysis(
                    saved["year"], saved["month"], saved["day"],
                    saved["hour"], saved["minute"], saved["city"],
                    saved["gender"], msg, user_id, stream_cb=stream_cb,
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
            self.dao.save_consultation(user_id, question, result, intent="ziwei")
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
    # 择日 (Zeri)
    # ============================================================

    def _handle_zeri(self, msg: str, user_id: str, stream_cb: Optional[Callable] = None) -> str:
        """处理择日请求"""
        date_info = self._extract_date(msg)
        purpose = self._extract_purpose(msg)

        if not date_info:
            return """请告诉我您想查询的日期和用途：

📅 日期：哪一年哪一天？
🎯 用途：用于什么？

💡 示例1：2026年8月15日适合结婚吗？
💡 示例2：我要在2026年10月1日搬家，这天好吗？"""

        return self._do_zeri_analysis(date_info, purpose, msg, user_id, stream_cb=stream_cb)

    def _do_zeri_analysis(self, date_info, purpose, question, user_id,
                          stream_cb: Optional[Callable] = None) -> str:
        """执行择日分析"""
        year, month, day = date_info

        # Task 3：思考步骤按真实工作里程碑渐进发出（首条开工即发）
        # 1. 择日
        self._emit_stream_event(stream_cb, "thinking", "我在翻黄历择吉…")
        result = self.zeri_engine.select(year, month, day, purpose=purpose)

        # 2. 保存
        self.dao.save_consultation(user_id, question, result, intent="zeri")

        # 3. 检索古籍
        search_query = f"择日 {result.jianchu} {purpose or '吉日'}"
        self._emit_stream_event(stream_cb, "thinking", "正在查阅古籍…")
        refs = self.retriever.search(search_query, category="zeri", top_k=15)

        # 阶段 5·来源体系（方案 §3.0）：择日结果 → 引擎来源；检索古籍 → book 来源
        try:
            self._register_engine_citation(
                user_id,
                f"择日分析：{year}年{month}月{day}日，建除十二神{result.jianchu}，"
                f"二十八宿{result.ershibaxiu}（{result.xiu_jixiong}），"
                f"宜：{'、'.join(result.yi)}；忌：{'、'.join(result.ji)}",
                title="择日分析结果", source="择日引擎",
            )
            self._register_book_citations(user_id, refs, title="择日 · 古籍参考")
        except Exception:
            pass

        # 4. LLM分析
        chart_str = self._format_zeri_chart(result, year, month, day)
        self._emit_stream_event(stream_cb, "thinking", "比对吉凶宜忌…")
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
            f"五行：{result.wuxing}",
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
                f"三才：{result.sancai}（{result.sancai_ji}）；五行：{result.wuxing}",
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
        """合婚配对 - 提取双方信息，引擎计算匹配度，LLM生成叙事分析"""
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

        # 2. 获取用户八字
        bazi_info = None
        try:
            saved = self.dao.get_user_bazi(user_id)
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
            if self.dao:
                saved = self.dao.get_user_bazi(user_id)
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

    def _handle_advisor(self, msg: str, user_id: str, stream_cb: Optional[Callable] = None) -> str:
        """处理 AI 建议请求 — 基于八字 + 用户处境生成个性化建议."""
        # 1. 检查用户八字是否已保存
        saved = self.dao.get_user_bazi(user_id)
        if not saved:
            return ("💡 想为你生成专属建议，需要先了解你的命盘哦～\n"
                    "请提供你的出生信息：出生年月日时、出生地、性别\n\n"
                    "例如：1990年5月20日 下午3点 北京 男")

        # 2. 提取用户处境（去掉排盘信息后的剩余文本）
        user_context = self._extract_user_context(msg)
        if not user_context:
            # 从消息中提取关键词，如果没有具体语境，使用默认描述
            user_context = "一般运势咨询"

        # 3. 重新排盘
        try:
            result = self.engine.calculate(
                saved["year"], saved["month"], saved["day"],
                saved["hour"], saved["minute"], saved["city"],
                saved["gender"],
            )
        except Exception as e:
            return f"⚠️ 命盘重新计算失败：{str(e)[:100]}"

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

        # 7. 保存咨询记录
        self.dao.save_consultation(user_id, msg, result, intent="advisor")

        return reply

    # ============================================================
    # AI 幸运日历
    # ============================================================

    def _handle_calendar(self, msg: str, user_id: str, stream_cb: Optional[Callable] = None) -> str:
        """Handle calendar/today-fortune requests."""
        saved = self.dao.get_user_bazi(user_id)
        if not saved:
            return ("📅 想生成你的专属每日运势日历，需要先设置八字哦～\n"
                    "告诉我你的出生日期，例如：1990年5月20日 下午3点 北京 男")

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
        saved = self.dao.get_user_bazi(user_id)
        if not saved:
            return ("⏰ 想查看今日十二时辰运势，需要先设置八字哦～\n"
                    "告诉我你的出生日期，例如：1990年5月20日 下午3点 北京 男")

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
                            model="deepseek-v4-flash",
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
                return self._free_chat(msg, user_id, emotion_label="sadness",
                                       session_id=session_id)

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
                model="deepseek-v4-flash",
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

            return reply
        except Exception:
            return self._free_chat(msg, user_id, emotion_label="sadness",
                                   session_id=session_id)

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
                   session_id: Optional[str] = None) -> str:
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
            if self.memory_system and self.memory_system.has_memory(user_id):
                greeting = self.memory_system.get_greeting(user_id)
                if greeting:
                    return f"欢迎回来！{greeting}"
            # 检查是否有已保存的八字 — 如果有，直接引导到八字分析而非要求重新提供
            saved = self.dao.get_user_bazi(user_id) if self.dao else None
            if saved:
                return '欢迎回来！您的八字信息已保存，有什么想了解的可以直接问～'
            return '您好！我是易理明灯AI命理顾问。直接告诉我您的出生日期，我帮您看八字。'

        # 如果消息含数字或年份且用户已有八字，直接路由到八字分析
        # 注意: "男"/"女" 必须是独立出现(性别标记)，不能是 "渣男"/"美女" 等词的一部分
        has_year = bool(re.search(r'\d{4}', msg))
        has_gender = bool(re.search(r'(?:^|[^\w])[男女](?:$|[^\w])', msg))
        saved_bazi = self.dao.get_user_bazi(user_id) if self.dao else None
        if (has_year or has_gender) and not saved_bazi:
            return '看起来您可能在提供出生信息。请按格式告诉我：\n📅 出生年月日（阳历/阴历）\n⏰ 几点几分\n📍 出生城市\n👤 性别\n\n例如：1990年5月20日 下午3点 北京 男'
        if saved_bazi and (has_year or has_gender):
            # 用户已有八字，但提供了新的出生信息，可能想更新或已有信息
            intent_result = self._analyze_message(msg, user_id,
                                                  session_id=session_id)
            if intent_result.intent == "bazi":
                return self._handle_bazi(msg, user_id)

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

            # L1 滚动窗口（方案 §5.3）+ L2 增量摘要（方案 §5.4）：
            # 1) L2 触发检查（超阈值 → 分块滚动摘要，摘要存 session_summaries）
            # 2) L1 按 token 预算动态保留轮数 + 关键事实保底（八字/L3 is_key）
            # Task 5 deepNight：深夜不压缩 → 内容不入 L2 摘要（临时通道 24h 硬清理）
            if self.session_dao and not self._deep_night.get(user_id, False):
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
                    if combined_hint:
                        messages[-1] = {
                            "role": messages[-1]["role"],
                            "content": messages[-1]["content"] + f"\n\n{combined_hint}"
                        }
                    return self.llm.chat_conversation(messages, stream_cb=stream_cb)
            # 无会话存储时，用单消息模式
            chat_msg = msg
            if combined_hint:
                chat_msg = msg + f"\n\n{combined_hint}"
            result = self.llm.chat(chat_msg)
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
