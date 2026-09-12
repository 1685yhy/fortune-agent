"""v8 阶段 3：对话过程体验 — SSE 流式 + 思考路径（对标豆包/元宝）。

事件协议（SSE data 行，每行一个 JSON）：
  {"type":"start"}                          连接建立（200ms 内必达）
  {"type":"thinking","text":"…"}            思考路径步骤（逐步点亮）
  {"type":"tool","text":"…"}                工具调用事件（"正在排盘…"等）
  {"type":"chunk","content":"…"}            正文增量（真实流式/分句模拟）
  {"type":"done","consultation_id":…}       完成（consultation_id 可为 null）
  {"type":"error","message":"…"}            异常（含单 chunk 间隔超时看门狗）
  {"type":"ping"}                           心跳保活（前端忽略）

线程模型：
  - 本协程跑在事件循环；handler.process（含 LLM 调用）在 executor 线程执行，
    事件循环保持空闲，SSE 才能边生成边推送。
  - handler 侧 stream_cb（worker 线程）→ loop.call_soon_threadsafe →
    asyncio.Queue（本协程消费）。
  - 客户端断开（wx.request.abort / 断网）：StreamingResponse 会对生成器
    aclose()/取消 → 本协程记录日志后退出；executor 线程自然跑完（配额照扣、
    历史落库不受影响），其回调因无人消费而静默丢弃。
  - 看门狗：ping_interval 无事件 → 心跳 ping；chunk_gap_timeout 无正文 →
    error 事件（正文流尚未开始——引擎计算阶段——给 2× 宽限，防误杀）。
  - 收尾：真实流式未覆盖的正文（尾部/早退分支/缓存命中）以分句模拟流式
    补足（最长公共前缀对齐，不重复已流出的部分）。

断点续传（2026-08-19，PM：退出/切走后生成不中断）：
  - 生成在 executor 线程跑，客户端断开后继续完成（不 cancel），handler 照常
    落库（回复 + session_id 已由 handler.process 内部完成，无需重复写入）。
  - 生成器提前关闭（客户端断开/异常）时，finally 安排 watcher 任务等在后台
    生成完成，随后给该会话最近一条 assistant 消息打 offline_completed=1
    （SessionDAO.mark_offline_completed，只补标记；watcher 在事件循环线程
    执行，无跨线程竞态；await task 同时保住 future 引用，线程完成后回调不丢）。
  - 前端下次进入经 GET /api/chat/pending 补全 → POST /api/chat/pending/consume
    消费（契约见 build_pending_response / consume_pending）。
  - 在线路径不受影响：生成器正常跑完到 done → 不打标记；看门狗超时返回
    （error 事件）时任务仍在跑 → finally 同样安排 watcher → 视为离线完成
    （用户看到"超时"，实际回复后台完成了，下次进入自动补全）。
"""
import asyncio
import json
import logging
import re
import time
from typing import AsyncIterator, Optional

from src.config import is_experience_mode
from src.bot.tool_calls import strip_tool_calls  # 兜底：流式出口清理 TOOL 标签残留

logger = logging.getLogger(__name__)

# 断点续传 watcher 注册表（M2，2026-08-28）：生成器提前关闭（客户端断开）时
# 安排的后台 watcher 任务由这里强引用持有——未定属的任务在循环关闭而 executor
# 未结束时被 GC 会打出 "Task was destroyed but it is pending!" 告警；
# 定属后由 add_done_callback 在任务完成/取消时自动摘除（见 events() finally）。
_OFFLINE_WATCHERS: set = set()

# 分句模拟流式（保底）：按标点切块；长句按硬边界二次切分
_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])")
# k7 逐字保真分句：捕获组把句末分隔符留在所属句子块尾部（join 可还原原文）
_SENT_SPLIT_CAP = re.compile(r"([。！？!?；;])")

# 收尾对齐的句子结束符（。！？… 换行）
_SENT_END_CHARS = "。！？…\n"


def compute_stream_remaining(reply: str, streamed_text: str) -> str:
    """流式收尾对齐：计算需要补发的剩余文本（分句模拟流式前调用）。

    根因：同一轮多次 LLM 调用（草稿流/润色稿/工具续写）灌同一条流，收尾时
    `streamed_text.endswith(reply[:k])` 求出的最长前缀重叠 covered 可能截在
    句子中间——流出尾巴与 reply 开头对不上时，reply 开头没流出过的句子会被
    静默丢弃（用户看到「直接、」这类残缺片段）。

    规则（约束：reply 中未流出过的句子绝不丢弃；补发内容必须句子完整，
    截断句中已流出的前缀允许重叠一次）：
    - covered 计算保持现有（最长后缀匹配）；
    - 剩余起点恰在句子边界（reply[covered-1] 是 。！？… 换行）→ 增量补发 reply[covered:]；
    - 否则（covered 截在句子中间）→ 回溯到 reply 中 covered 之前的最后一个
      句子结束符，从该句起点补发 reply[边界+1:]，保证句子完整；
    - covered==0 / covered 前缀内无句子边界 → k5 尾部对齐补发：reply 最长前缀
      已在 streamed 中完整流出过（正文主体已流出，reply 尾部是流后追加的图片
      URL/页脚）→ 只补未流出的尾部增量，不再整段重发；正文从未流出 → 整段
      补发（降级/非流式兜底，保留现状语义）。
    - k5 前缀命中不了（reply 前缀是卡壳、正文在 reply 中段）→ k7 中段命中：
      reply = 卡壳 + 正文 + 卡尾（排盘卡），正文已作为实时流完整流出——镜像
      k5，找 streamed 最长前缀在 reply 中的连续子串对齐位置，覆盖 ≥90% 全长
      → 只补壳头 + 壳尾，不再整段重发（正文绝不重发）。
    - k33/A19：k5 尾部对齐的补发点落在空行边界（reply[best-1]==reply[best]=="\n"）
      → 回退一位，补发块以整段空行开头，前端拼接吞首换行时段落分隔仍在。
    """
    if not reply:
        return ""
    if not streamed_text:
        return reply
    limit = min(len(streamed_text), len(reply))
    covered = 0
    # 从最长重叠往下找（常见：完整覆盖/前缀+welcome/无重叠）
    for k in range(limit, 0, -1):
        if streamed_text.endswith(reply[:k]):
            covered = k
            break
    if covered >= len(reply):
        return ""
    start = covered
    if covered > 0 and reply[covered - 1] not in _SENT_END_CHARS:
        # 截断在句子中间 → 回溯到最近的完整句子边界（含换行）
        boundary = -1
        for idx in range(covered - 1, -1, -1):
            if reply[idx] in _SENT_END_CHARS:
                boundary = idx
                break
        start = boundary + 1  # 前缀内无边界 → boundary=-1 → start=0 → 落入下方 k5 尾部对齐
    if start == 0:
        # k5 单稿流（2026-09-04，用户实锤一条回复显示两遍）：streamed 尾部与
        # reply 前缀无重叠（covered==0：多稿灌流 / 回复经再处理后与流尾逐字
        # 失配），或 covered 前缀内无句子边界（正文尾部恰缺句末标点，旧逻辑
        # 整段重发 = 把已流出正文再整段重发一遍）→ 不再无条件整段重发：
        # 找 reply 最长前缀已在 streamed 中作为连续子串出现（正常：定稿正文
        # 已完整流过，reply = 定稿正文 + 流后追加的尾部如命盘图 URL/页脚
        # → 只补未流出的尾部增量）。复杂度：k 递减的 in 为 C 层快速子串
        # 查找，reply ≤3000 字符、每次请求仅一次，命中即 break，最坏
        # ~百 ms 级可接受。
        best = 0
        for k in range(len(reply), 0, -1):
            if reply[:k] in streamed_text:
                best = k
                break
        # 正文主体（≥20 字符）确已完整流出 → 只补尾部；或重叠前缀恰止于
        # 句子边界（reply[covered] 即换行等句末符，尾部以完整句/段开始）
        # → 同理只补尾部（覆盖 covered>0 但前缀内无边界、长度不足 20 的
        # 短正文尾部追加场景）。
        if best >= 20 or (best == covered and best > 0
                          and reply[best] in _SENT_END_CHARS):
            # k33/A19（k5 Minor-1「refill 吞换行」）：命中点恰在空行边界
            # （reply[best-1] == reply[best] == "\n"）时回退一位——补发块以完整
            # 空行开头（"\n\n"）。原实现补发块以单个 "\n" 开头，前端逐块拼接
            # 时吃掉首字符换行 → 段落分隔被并成一行（显示级缺陷）。回退一位
            # 只多补一个换行符：正文一字不重、不丢。
            if 0 < best < len(reply) and reply[best - 1] == "\n" \
                    and reply[best] == "\n":
                best -= 1
            return reply[best:]  # 正文主体已流出 → 只补尾部增量（不再整段重发）
        if best < 20 and len(streamed_text) >= 20:
            # k7c（2026-09-05，引用角标死区实证）：原条件 best == 0 漏掉
            # 0 < best < 20 的死区——reply 前缀与 streamed 存在 1-19 字符
            # 巧合子串（典型：排盘正文带引擎引用角标 [1]，reply[:1]="["
            # 与正文 "[" 命中 → best=1）→ k5 不触发、中段也被闸死 → 落兜底
            # 整段重发 = 18:47 双份形态回归。best < 20 即「k5 无有意义命中
            # （正文主体未作为 reply 前缀流出）」→ 允许尝试中段对齐；中段
            # 自身有 ≥90% 全长阈值封死误伤，不中自然落兜底，行为不劣化。
            # 原 k7 注释（2026-09-05，18:47 排盘双份实证）：reply 的正文主体
            # 被卡壳包裹（reply = 卡前缀 + 正文 + 卡尾），正文已作为实时流
            # 完整流出 → 只补壳头+壳尾，不再整段重发。镜像 k5 的前缀查找：
            # 找 streamed_text 最长前缀在 reply 中作连续子串的对齐位置
            # （reply.find 为 C 层快速查找，长度同 k5，最坏 ~百 ms 级可接受）。
            mid_start, mid_len = -1, 0
            for k in range(len(streamed_text), 0, -1):
                j = reply.find(streamed_text[:k])
                if j >= 0:
                    mid_start, mid_len = j, k
                    break
            # 正文主体确已流出（≥90% 全长才算，防短巧合误截；允许尾部失真
            # 重叠一次）→ 只补壳头 + 壳尾（命中起点即 reply 开头 → 全部已
            # 流出 → 返回 ""；失真 → 返回含正文尾少量重叠，绝不丢字）
            if mid_len >= max(20, int(len(streamed_text) * 0.9)):
                return reply[:mid_start] + reply[mid_start + mid_len:]
    # 正文从未流出（降级/非流式兜底）→ 整段模拟流式（保持现状语义）
    return reply[start:]


def split_sentences(text: str) -> list:
    """把完整回复切成适合模拟打字机的文本块（逐字保真，k7 2026-09-05）。

    契约：`"".join(split_sentences(text)) == text` 逐字相等——绝不吞任何字符
    （旧实现按无捕获 _SENT_SPLIT 切分后逐段 strip，句末标点后的段间空行/
    句末换行被吞，18:47 实证 981→969 丢 12 字符，前端收到的 body 与实时流
    prefix 逐字失配，卡片去重认不出重复）。

    实现：捕获组分句——句末分隔符（与 _SENT_SPLIT 同字符集）留在所属句子
    块尾部；单句 >48 字符按 48 硬切（只断块不丢字，join 后仍还原原文）；
    剔除的只有真空块（块为空串才剔除，不做 strip——含空白的块剔除会破坏
    join 还原）。块边界只在句末分隔符处或 48 硬切处，不跨句子。
    """
    parts = _SENT_SPLIT_CAP.split(text)
    out = []
    buf = ""
    for p in parts:
        buf += p
        if len(p) == 1 and p in "。！？!?；;":
            # 句末分隔符 → 句子完整，flush（超 48 先硬切）
            while len(buf) > 48:
                out.append(buf[:48])
                buf = buf[48:]
            if buf:
                out.append(buf)
            buf = ""
    if buf:
        # 文本不以句末标点收尾的残尾
        while len(buf) > 48:
            out.append(buf[:48])
            buf = buf[48:]
        if buf:
            out.append(buf)
    return out


def sse_format(event: dict) -> str:
    """单个 SSE data 行（含双换行分隔）。"""
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


# 会话标识格式：字母/数字/下划线/连字符，8~64 位（前端生成 s_+时间戳+随机4位）
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def normalize_session_id(raw: str = "") -> Optional[str]:
    """会话标识归一化：去空白；格式合法返回原值，空/非法返回 None（= 旧行为）。

    - 空请求 → None：不传 session_id 的存量调用完全按旧行为（按 user 取上下文）；
    - 非法格式 → None：防御非法输入（不 4xx 拒绝，避免误伤；也不落入存储）。
    """
    sid = (raw or "").strip()
    if not sid:
        return None
    if SESSION_ID_RE.match(sid):
        return sid
    logger.warning("session_id 格式非法已忽略: %r", sid[:40])
    return None


# ── 断点续传补全（pending 接口契约，main.py 端点薄调用）────────────
# GET  /api/chat/pending?session_id=xxx → {"items":[{role,content,time,offline}]}
#      未消费的后台完成回复，最新在前；非法/空 session_id → 空 items
# POST /api/chat/pending/consume {session_id, time} → {"ok": true}
#      消费截止 time（created_at <= time 的全部离线回复标记已消费，幂等）
def build_pending_response(dao, user_id: str, session_id: str = "") -> dict:
    """断点续传补全响应：该会话未消费的后台完成回复（最新在前）。"""
    sid = normalize_session_id(session_id or "")
    if sid is None:
        return {"items": []}
    if dao is None:
        return {"items": []}
    items = []
    try:
        pending = dao.get_pending_offline_messages(user_id, sid)
    except Exception:
        logger.exception("chat pending query failed: user=%s", user_id)
        return {"items": []}
    for m in pending or []:
        items.append({
            "role": "assistant",
            "content": m.get("content", ""),
            "time": m.get("created_at", ""),
            "offline": True,
        })
    return {"items": items}


def consume_pending(dao, user_id: str, session_id: str = "",
                    time: str = "") -> dict:
    """消费断点续传补全：标记该会话 created_at <= time 的离线回复为已消费。"""
    sid = normalize_session_id(session_id or "")
    if sid is None:
        return {"ok": False}
    if dao is None:
        return {"ok": True}
    try:
        dao.consume_pending_offline(user_id, sid, time or "")
    except Exception:
        logger.exception("chat pending consume failed: user=%s", user_id)
        return {"ok": False}
    return {"ok": True}


class ChatStreamer:
    """流式对话编排器：与 /api/chat 共用核心 handler 逻辑，但逐事件推送。"""

    def __init__(
        self,
        handler,
        member_dao,
        dao,
        sanitizer=None,
        auditor=None,
        validator=None,
        chat_quota_dao=None,
        ping_interval: float = 15.0,
        chunk_gap_timeout: float = 60.0,
        simulation_delay: float = 0.05,
    ):
        self.handler = handler
        self.member_dao = member_dao
        self.dao = dao
        self.sanitizer = sanitizer
        self.auditor = auditor
        self.validator = validator
        self.chat_quota_dao = chat_quota_dao  # L5-1：对话日额度（降级链路）
        self.ping_interval = ping_interval          # 心跳间隔（保活）
        self.chunk_gap_timeout = chunk_gap_timeout  # 无正文看门狗
        self.simulation_delay = simulation_delay    # 模拟流式块间延迟

    # ── 断点续传：离线完成标记 ───────────────────────────────────
    def _mark_offline_if_needed(self, user_id: str,
                                session_id: Optional[str], reply: str,
                                min_id: Optional[int] = None):
        """把本轮落库的 assistant 回复标记为离线完成（offline_completed=1）。

        断点续传：消息本身已由 handler.process 内部落库（回复 + session_id），
        这里只补标记——给该会话本轮生成期间新增的最近一条 assistant 消息打
        标记（min_id = 生成开始前最后一条消息 id）。
        无 session_dao / 落库失败 → 静默降级（标记失败不影响本轮流程，
        仅补全不可用）。
        """
        if not reply:
            return
        # handler 持有 session_dao（与 /api/chat 落库同一实例）
        session_dao = getattr(self.handler, "session_dao", None)
        if session_dao is None:
            return
        try:
            session_dao.mark_offline_completed(user_id, session_id,
                                               min_id=min_id)
        except Exception:
            logger.warning("chat stream offline mark failed: user=%s", user_id)

    async def _wait_and_mark(self, task, user_id: str, session_id: Optional[str],
                             before_id: Optional[int]):
        """断点续传 watcher：等后台生成完成后补打 offline_completed 标记。

        仅由 events() 的 finally 通过 _OFFLINE_WATCHERS 注册表创建并持有；
        任务完成/取消时由 add_done_callback 自动从注册表摘除。
        """
        try:
            reply = await task
        except Exception:
            return  # 生成失败/取消：无回复可补全
        if reply:
            self._mark_offline_if_needed(user_id, session_id, reply,
                                         min_id=before_id)

    # ── 事件转换 / 收集 ──────────────────────────────────────────
    @staticmethod
    def _to_event(evt_type: str, payload: dict) -> dict:
        if evt_type == "chunk":
            return {"type": "chunk", "content": payload.get("text", "")}
        if evt_type == "tool":
            return {"type": "tool", "text": payload.get("text", "")}
        if evt_type == "thinking":
            return {"type": "thinking", "text": payload.get("text", "")}
        return {"type": evt_type, **payload}

    @staticmethod
    def _client_ip(request) -> str:
        try:
            forwarded = request.headers.get("X-Forwarded-For", "")
            if forwarded:
                return forwarded.split(",")[0].strip()
            return request.client.host if request.client else ""
        except Exception:
            return ""

    # ── 主入口：异步事件生成器 ───────────────────────────────────
    async def events(self, req, request, auth: dict) -> AsyncIterator[dict]:
        """按协议产出事件字典序列（main.py 包装为 StreamingResponse）。"""
        # 身份权威来源与 /api/chat 完全一致
        if auth.get("method") == "api_key":
            user_id = (req.user_id or "").strip() or "api_user"
        else:
            user_id = auth.get("user_id", "")

        # 服务未就绪
        if self.handler is None or self.member_dao is None:
            yield {"type": "error", "message": "服务暂未就绪，请稍后重试"}
            return

        # start 事件：连接建立即发（200ms 内）
        yield {"type": "start"}

        # ── 输入安全检测（与 /api/chat 一致）─────────────────────
        if self.sanitizer and req.message:
            cleaned, is_attack, attack_type = self.sanitizer.clean_and_check(req.message)
            if is_attack:
                ip = self._client_ip(request)
                if self.auditor:
                    try:
                        self.auditor.attack_detected(attack_type, user_id, ip, req.message[:80])
                    except Exception:
                        pass
                yield {"type": "chunk", "content": "⚠️ 输入包含不安全内容，已拦截。请使用正常语言描述您的问题。"}
                yield {"type": "done", "consultation_id": None}
                return
            req.message = cleaned

        if self.sanitizer and req.voice_text:
            cleaned, is_attack, _ = self.sanitizer.clean_and_check(req.voice_text)
            if not is_attack:
                req.voice_text = cleaned

        # ── 对话额度（L5-1/L5-2 I-2）：请求进入时消费；超限 → 降级链路（不 429 硬断）──
        # L5-2（I-2 新旧额度协调）：移除旧额度（member_dao.check_quota）硬断——
        # 聊天消息由 chat_quota（15 条/日）治理，超限降级续聊，绝不硬断；
        # 旧额度仅对非聊天功能生效（如择日工具 _tool_zeri 的引擎调用门）。
        # 会员/体验模式不计数；免费用户第 16 条起 downgraded=True →
        # LLM 切 GLM-4-Flash + 精简 prompt（见 _run 与 done 事件）。
        downgraded = False
        try:
            from src.services.chat_quota import try_consume_chat_quota
            quota_ctx = try_consume_chat_quota(
                self.member_dao, self.chat_quota_dao, user_id)
            downgraded = bool(quota_ctx.get("downgraded"))
        except Exception:
            logger.warning("chat stream quota ctx failed: user=%s", user_id)

        # ── 主流水线：executor 线程跑 handler，本协程边收边推 ─────
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        chunk_texts: list = []
        disconnected = {"flag": False}
        # 断点续传：会话标识（text 路径透传；voice/image 无会话维度 → None）
        session_id = (normalize_session_id(getattr(req, "session_id", "") or "")
                      if req.message_type == "text" else None)
        # k13 重试去重：前端重试/重新生成同轮标记（text 路径透传 → process →
        # 同会话同文不新插 user 行，正常生成链补 assistant；voice/image 忽略）
        regen = bool(getattr(req, "regen", False))
        # 生成开始前该会话最后一条消息 id——离线标记只打本轮新增的消息
        # （缓存命中/反馈等不落库分支不会误标记上一轮未标记的回复）
        before_id = None
        try:
            _sdao = getattr(self.handler, "session_dao", None)
            if _sdao is not None:
                before_id = _sdao.get_max_message_id(user_id, session_id)
        except Exception:
            before_id = None
        out_box: dict = {}  # 阶段 5：worker 线程回传引用来源（done 事件携带）

        # k11-E（JSON 泄漏 chunk 级过滤 · 发送总闸）：对每个发送 chunk 先过
        # ToolJsonChunkFilter（polish 开场工单/工具循环 GLM 先吐 JSON 的竞态在
        # 任何路径都到不了用户），再做本轮称谓/神煞 scrub（B/C，handler 排盘轮
        # 才登记事实上下文 → 无上下文不 scrub，自由对话不受影响）。双层与
        # handler 内 polish/tool-loop 的调用点过滤幂等叠加。过滤后文本与
        # chunk_texts/剩余模拟流一致（否则收尾重发会带回已滤残渣）。
        try:
            from src.bot.stream_guard import ToolJsonChunkFilter
            _json_filter = ToolJsonChunkFilter()
        except Exception:
            _json_filter = None
        _scrub_h = getattr(self, "handler", None)

        def _send_guard(evt_type: str, payload: dict):
            """单请求级 cb 包装：工具 JSON 过滤 + 事实 scrub 后投递。"""
            try:
                if evt_type != "chunk" and _json_filter is not None:
                    # review r1-4（Minor）：thinking/tool 等事件 = LLM 子流边界
                    # （引擎阶段→润色/工具循环各成一段）。某子流被 max_tokens 截断
                    # 在未闭合 JSON 时，悬挂缓冲会吞掉下一子流头部——边界处 finish()
                    # 丢弃残块（宁漏不泄），下一子流从头计数
                    _json_filter.finish()
                if evt_type == "chunk" and isinstance(payload, dict):
                    raw = (payload.get("text") if "text" in payload
                           else payload.get("content", ""))
                    if isinstance(raw, str) and raw:
                        cleaned = raw
                        if _json_filter is not None:
                            cleaned = _json_filter.feed(raw)
                        if _scrub_h is not None and cleaned:
                            try:
                                cleaned = _scrub_h._scrub_turn_text(
                                    cleaned, user_id) or cleaned
                            except Exception:
                                pass
                        if not cleaned:
                            return  # 全过滤/缓冲中：本 chunk 不透传
                        if cleaned != raw:
                            # text/content 双键历史形态（k7 键名归一）：同步覆盖，
                            # 防 chunk_texts（content 优先）收脏而事件收净
                            p2 = dict(payload)
                            for _k in ("text", "content"):
                                if _k in p2:
                                    p2[_k] = cleaned
                            payload = p2
                loop.call_soon_threadsafe(queue.put_nowait, (evt_type, payload))
            except RuntimeError:
                disconnected["flag"] = True  # 事件循环已关（客户端断开）

        def stream_cb(evt_type: str, payload: dict):
            """worker 线程回调 → 投递到事件循环队列（线程安全）。"""
            _send_guard(evt_type, payload)

        def _run() -> str:
            """executor 线程内跑核心逻辑 + 成功即扣配额（与 /api/chat 一致）。

            断点续传：生成不因客户端断开而中断——本线程照常跑完，handler 照常
            落库（回复 + session_id）。离线标记不在此线程做（跨线程读标志有
            竞态），由事件循环线程在生成器提前关闭时安排 watcher 任务补打
            （见下方 finally）。
            """
            try:
                if req.message_type == "voice":
                    # k39 审查 C2 同类修复：deep_night 透传（改前语音轮同样丢该标记）
                    reply = self.handler._handle_voice(
                        req.voice_text, downgraded=downgraded,
                        deep_night=bool(getattr(req, "deep_night", False)))
                elif req.message_type == "image":
                    # L5-2（I-3）：降级标记透传 → CV 报告走本地精简文案（不调付费报告）
                    # k39 S4：user_id 透传 → 图片轮次落库（历史可渲染 + 清理识别引用）
                    # k39 审查 C2：deep_night 同样透传（与文本轮同源同判）→ 深夜
                    # 图片轮 temp=1 + 不进 L2；此前图片轮的深夜标记被丢弃。
                    reply = self.handler._handle_image(
                        req.image_url, req.message, downgraded=downgraded,
                        user_id=user_id,
                        deep_night=bool(getattr(req, "deep_night", False)))
                else:
                    # 会话隔离：session_id 透传（新开对话 → 全新上下文；空/非法 → 旧行为）
                    # L5-1 降级：对话额度用尽 → 精简 prompt + GLM 模型
                    # k13：regen 同轮标记透传（重试不新插 user 行，补答既有轮）
                    reply = self.handler.process(
                        req.message, user_id, stream_cb=stream_cb,
                        deep_night=bool(getattr(req, "deep_night", False)),
                        session_id=session_id, downgraded=downgraded,
                        regen=regen)
                # 兜底：回复出口强制清理 TOOL 标签残留（格式变体/未知工具名/
                # 未闭合标签——handler 工具循环已清，这里对最终 reply 再 strip 一次，
                # 后续「剩余文本模拟流式」与 done 内容都基于清理后的文本）
                reply = strip_tool_calls(reply) or reply
            except Exception:
                logger.exception("chat stream process failed: user=%s", user_id)
                raise
            # 阶段 5：本轮引用来源（校验后）→ done 事件携带（供前端角标抽屉）
            try:
                out_box["citations"] = self.handler.pop_citations(user_id) or []
            except Exception:
                out_box["citations"] = []
            # 成功响应后扣减配额（放在线程内：请求断开也照常扣，防刷；体验模式不扣）
            try:
                if not is_experience_mode():
                    self.member_dao.use_quota(user_id)
            except Exception:
                logger.warning("chat stream quota charge failed: user=%s", user_id)
            return reply or ""

        task = asyncio.ensure_future(loop.run_in_executor(None, _run))
        last_event = time.monotonic()
        try:
            while True:
                if task.done():
                    # 任务完成：先把剩余事件排空（回调先于 future 完成入队，FIFO 保证不漏）
                    while not queue.empty():
                        evt_type, payload = queue.get_nowait()
                        last_event = time.monotonic()
                        yield self._to_event(evt_type, payload)
                        if evt_type == "chunk":
                            # k7 键名归一（2026-09-05，18:47 排盘双份实证）：SSE
                            # 出口事件键为 content，内部 payload 键历史上有 text/
                            # content 两种写法 → content 优先、text 兜底，两种
                            # 都收得到（旧写法只认 text，漏收即 chunk_texts 恒空
                            # → streamed_text 恒空 → 收尾无条件整段补发 = 双份）
                            chunk_texts.append(
                                payload.get("content") or payload.get("text")
                                or "")
                    break

                waiter = asyncio.ensure_future(queue.get())
                try:
                    done, _pending = await asyncio.wait(
                        {task, waiter},
                        timeout=self.ping_interval,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except (asyncio.CancelledError, GeneratorExit):
                    waiter.cancel()
                    logger.info("SSE 流被客户端中断: user=%s msg=%s", user_id, (req.message or "")[:40])
                    raise
                except Exception:
                    waiter.cancel()
                    raise

                if waiter in done:
                    evt_type, payload = waiter.result()
                    last_event = time.monotonic()
                    if evt_type == "chunk":
                        # k7 键名归一：见 task.done() 排空分支注释
                        chunk_texts.append(
                            payload.get("content") or payload.get("text")
                            or "")
                    yield self._to_event(evt_type, payload)
                    continue

                waiter.cancel()  # 超时无事件
                # 看门狗：长时间无正文 → error；否则心跳保活。
                # 有意图路径的引擎阶段（_handle_* 排盘/检索/分析，真实流式
                # 尚未开始）是合法长沉默：正文流尚未产生（最多只有 welcome
                # 开场 1 块）时给 2× 宽限，防 60s 误杀（润色/工具循环的真实
                # 流式 chunk 在引擎完成后才会到达）；正文流开始后维持原间隔。
                idle = time.monotonic() - last_event
                gap = (self.chunk_gap_timeout * 2
                       if len(chunk_texts) <= 1 else self.chunk_gap_timeout)
                if idle >= gap:
                    logger.warning("chat stream chunk timeout: user=%s idle=%.0fs gap=%.0fs",
                                   user_id, idle, gap)
                    yield {"type": "error", "message": "回复生成超时，请重试"}
                    return
                yield {"type": "ping"}
        finally:
            if not task.done():
                # 生成器被关闭（客户端断开/异常）：后台线程自然跑完，仅记日志。
                # 断点续传：客户端已断开但生成未中断——安排 watcher 任务等生成
                # 完成后补打 offline_completed 标记（下次进入经 pending 补全）。
                # 标记在事件循环线程做（无跨线程竞态）；await task 同时保住了
                # future 引用，线程完成后回调不会丢。
                # M2：watcher 必须由模块级注册表强引用持有（防未完成即被 GC
                # 触发 "Task was destroyed"），完成后 add_done_callback 自动摘除。
                logger.info("chat stream generator closed early: user=%s", user_id)
                try:
                    watcher = loop.create_task(self._wait_and_mark(
                        task, user_id, session_id, before_id))
                    _OFFLINE_WATCHERS.add(watcher)
                    watcher.add_done_callback(_OFFLINE_WATCHERS.discard)
                except RuntimeError:
                    logger.warning(
                        "chat stream offline watcher create failed: user=%s",
                        user_id)

        # ── 收尾：校验 / 模拟流式 / done ─────────────────────────
        try:
            reply = task.result()
        except Exception:
            yield {"type": "error", "message": "回复生成失败，请稍后重试"}
            return

        # 准确率验证（与 /api/chat 一致：违规记日志；
        # 2026-08-17：不再追加免责尾巴——免责改为对话页顶部静态浅色小字）
        if self.validator and reply and len(reply) > 10:
            try:
                val_result = self.validator.validate(reply, engine_data_used=True)
                if not val_result["passed"]:
                    logger.warning("Accuracy issue in stream response: %s", val_result["violations"])
            except Exception:
                pass

        # 正文未在流式中完全流出（引擎路径非流式调用/早退分支/缓存命中）→
        # 分句模拟流式补足。真实流式已流出的文本是最终回复的前缀（润色尾部
        # /页脚/图片链接是流式结束后才追加的；"欢迎回来"开场是回复之外的前置
        # 内容），取「streamed 尾部与 reply 前缀的最长重叠」即已覆盖长度，
        # 只补剩余部分，避免重复输出。
        # 对齐策略见 compute_stream_remaining：重叠截在句子中间时回溯到最近的
        # 完整句子边界补发，reply 中未流出过的句子绝不丢弃（防「直接、」缺头）。
        streamed_text = "".join(chunk_texts).rstrip()
        remaining = compute_stream_remaining(reply, streamed_text) if reply else ""
        if remaining and remaining.strip():
            for piece in split_sentences(remaining):
                if self.simulation_delay > 0:
                    await asyncio.sleep(self.simulation_delay)
                yield {"type": "chunk", "content": piece}

        consultation_id = None
        if self.dao:
            try:
                consultation_id = self.dao.get_last_consultation_id(user_id)
            except Exception:
                consultation_id = None

        # ── v1.2 建议卡片：用户最后一条消息是提问时，轻量生成 2-3 个追问 ──
        # 独立 executor 调用 + 6s 硬超时：生成失败/超时 → 无建议（不影响主回复，
        # 前端不渲染建议卡即自然降级）。等待期间不产生事件，远小于前端看门狗 60s。
        suggestions: list = []
        question = (req.voice_text or "").strip() or (req.message or "").strip()
        from src.bot.handler import is_question  # 局部导入：避免启动时拉入 handler 全链（torch）
        # L5-2（I-1）：降级链路不生成建议卡（独立 LLM 调用，降级不调）
        if question and is_question(question) and reply and not reply.startswith("⚠️") \
                and not downgraded:
            def _gen_sugg() -> list:
                try:
                    return self.handler.gen_suggestions(user_id, question, reply) or []
                except Exception:
                    return []

            sugg_task = asyncio.ensure_future(loop.run_in_executor(None, _gen_sugg))
            try:
                suggestions = await asyncio.wait_for(sugg_task, timeout=6.0)
            except Exception:
                suggestions = []  # 超时/取消：丢弃本次建议，不阻塞 done

        yield {
            "type": "done",
            "consultation_id": consultation_id,
            "citations": out_box.get("citations", []) or [],
            "suggestions": suggestions,
            # L5-1 降级标记：true 时前端提示"今日额度已用尽，已为你精简回复"
            "downgraded": downgraded,
            # k5 单稿流（2026-09-04）：done 携带定稿全文（与落库同文）——前端
            # 本批不消费，字段先就位：未来前端「整泡兜底替换」用（显示 = 落库
            # 数据一致性铁律，回看与屏幕永远同稿）。
            "content": reply,
        }
