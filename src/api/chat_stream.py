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
from src.bot.handler import is_question
from src.bot.tool_calls import strip_tool_calls  # 兜底：流式出口清理 TOOL 标签残留

logger = logging.getLogger(__name__)

# 分句模拟流式（保底）：按标点切块；长句按硬边界二次切分
_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])")

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
    - covered 计算保持现有（最长后缀匹配）；covered==0 → 整段重发（保留现状）；
    - 剩余起点恰在句子边界（reply[covered-1] 是 。！？… 换行）→ 增量补发 reply[covered:]；
    - 否则（covered 截在句子中间）→ 回溯到 reply 中 covered 之前的最后一个
      句子结束符，从该句起点补发 reply[边界+1:]，保证句子完整；
    - covered 之前不存在任何句子结束符（整个前缀是同一句）→ 整段补发。
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
        start = boundary + 1  # 前缀内无边界 → boundary=-1 → start=0 → 整段补发
    return reply[start:]


def split_sentences(text: str) -> list:
    """把完整回复切成适合模拟打字机的文本块。"""
    parts = re.split(_SENT_SPLIT, text)
    out = []
    for i, p in enumerate(parts):
        if not p:
            continue
        if i == 0:
            # 保留首段前导空白（尾部补足时与正文分隔符对齐）
            p = p.rstrip()
        else:
            p = p.strip()
        if not p:
            continue
        while len(p) > 48:
            out.append(p[:48])
            p = p[48:]
        out.append(p)
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

        # ── 配额检查（与 /api/chat 一致；体验模式不限次）───────────
        try:
            quota_ok = True if is_experience_mode() else self.member_dao.check_quota(user_id)
        except Exception:
            quota_ok = True
        if not quota_ok:
            try:
                membership = self.member_dao.get_membership(user_id)
                plan = (membership or {}).get("plan", "free")
                yield {
                    "type": "chunk",
                    "content": (
                        f"⚠️ 今日查询次数已用尽。\n"
                        f"当前计划：{(membership or {}).get('plan_label', '免费版')}\n"
                        f"已用次数：{(membership or {}).get('queries_used', 0)}\n"
                        f"上限：{(membership or {}).get('queries_limit', 3)}\n\n"
                        f"💡 升级会员可获得更多查询次数："
                        f"基础版¥19.9/月(50次)，专业版¥39.9/月(150次)"
                    ),
                }
            except Exception:
                yield {"type": "chunk", "content": "⚠️ 今日查询次数已用尽，请明日再来或升级会员。"}
            yield {"type": "done", "consultation_id": None}
            return

        # ── 主流水线：executor 线程跑 handler，本协程边收边推 ─────
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        chunk_texts: list = []
        disconnected = {"flag": False}
        # 断点续传：会话标识（text 路径透传；voice/image 无会话维度 → None）
        session_id = (normalize_session_id(getattr(req, "session_id", "") or "")
                      if req.message_type == "text" else None)
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

        def stream_cb(evt_type: str, payload: dict):
            """worker 线程回调 → 投递到事件循环队列（线程安全）。"""
            try:
                loop.call_soon_threadsafe(queue.put_nowait, (evt_type, payload))
            except RuntimeError:
                disconnected["flag"] = True  # 事件循环已关（客户端断开）

        def _run() -> str:
            """executor 线程内跑核心逻辑 + 成功即扣配额（与 /api/chat 一致）。

            断点续传：生成不因客户端断开而中断——本线程照常跑完，handler 照常
            落库（回复 + session_id）。离线标记不在此线程做（跨线程读标志有
            竞态），由事件循环线程在生成器提前关闭时安排 watcher 任务补打
            （见下方 finally）。
            """
            try:
                if req.message_type == "voice":
                    reply = self.handler._handle_voice(req.voice_text)
                elif req.message_type == "image":
                    reply = self.handler._handle_image(req.image_url, req.message)
                else:
                    # 会话隔离：session_id 透传（新开对话 → 全新上下文；空/非法 → 旧行为）
                    reply = self.handler.process(
                        req.message, user_id, stream_cb=stream_cb,
                        deep_night=bool(getattr(req, "deep_night", False)),
                        session_id=session_id)
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
                            chunk_texts.append(payload.get("text", ""))
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
                        chunk_texts.append(payload.get("text", ""))
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
                logger.info("chat stream generator closed early: user=%s", user_id)

                async def _wait_and_mark():
                    try:
                        reply = await task
                    except Exception:
                        return  # 生成失败/取消：无回复可补全
                    if reply:
                        self._mark_offline_if_needed(
                            user_id, session_id, reply, min_id=before_id)

                try:
                    loop.create_task(_wait_and_mark())
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
        if question and is_question(question) and reply and not reply.startswith("⚠️"):
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
        }
