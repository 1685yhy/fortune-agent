"""会话持久化 - SQLite 存储聊天历史.

敏感字段加密（AES-256-GCM）：sessions.content 加密落库，
读取时自动解密；旧明文读取时懒迁移为密文（与 dao.py 八字加密同款写法）。
阶段 2：intent/emotion/tool_calls/retrieval_hit/model/safety_flag 为
非敏感数据资产字段，明文存储（方案 §7.2）。
"""
import json
import logging
import re
import sqlite3
from typing import Optional, List, Dict

from .models import init_db, connect as db_connect
# k7b：LLM 上下文卡装饰剥离。src.bot.card_mark 为纯函数模块（只 import re/
# typing），无循环依赖；storage 层引用其纯函数属轻微层序倒置，但卡规则
# 与 wrap_card 同源同文件、清洗语义归属卡模块，可接受（契约函数名不变）。
from src.bot.card_mark import strip_card_decor_for_llm

logger = logging.getLogger(__name__)

# 对话历史是核心资产（方案 §7.1：原始对话全量留存，一条不丢）；
# 自动清理上限仅为防失控的软保护（2000 条 ≈ 1000 轮），
# L2 增量摘要负责运行时压缩，存储层不丢弃原文。
MAX_MESSAGES_PER_USER = 2000

# k13 重试去重窗口（秒）：同一 (user_id, session_id) 下、同 role=user、
# 规范化内容相同的消息，距最近一条同会话同文 user 行 ≤ 本窗口 → 判定重试/
# 重复提交 → 不新插 user 行（该轮 assistant 由正常生成链补上）。
# 依据（plan §B3）：双击/连点/双端同发为秒级；用户阅读+再发 ≥ 数十秒——
# 20s 误伤面极小。实测重复节奏（2-5 分钟/次）由前端 regen 标记覆盖
# （重试入口全部带标记），本窗口仅兜底无标记重复（旧客户端/竞态），
# 故不放大窗口（放大即吞掉超窗真重复提问的审计行——全量留存红线）。
RETRY_DEDUP_WINDOW_SECONDS = 20

# 去重判定向前回看的同会话 user 行数上限（content 加密落库，须取回解密后
# Python 侧比对；上限防全表解密，2000 条/用户软上限下 10 条足够覆盖窗口）
_DEDUP_LOOKBACK = 10

# 密文格式：v1:base64 / dev:base64（AES-256-GCM，见 dao.py 同款写法）
# 用严格正则匹配整串，避免把含 ":" 的普通明文（如 "12:30 见"）误判为密文去解密。
_CIPHER_RE = re.compile(r"^(v\d+|dev):[A-Za-z0-9+/=]+$")

_encryptor = None


def _get_encryptor():
    """惰性初始化 DataEncryptor（读取 ENCRYPTION_KEY；未配置时自动降级 dev 密钥并告警）。"""
    global _encryptor
    if _encryptor is None:
        from src.security.encryption import DataEncryptor
        _encryptor = DataEncryptor()
    return _encryptor


def _is_ciphertext(text: str) -> bool:
    """判断是否已是密文（严格格式：version:base64 整串匹配）。"""
    return bool(text) and bool(_CIPHER_RE.match(text))


def _encrypt_text(text: Optional[str]) -> Optional[str]:
    """加密文本；空值原样返回。"""
    if not text:
        return text
    return _get_encryptor().encrypt(text)


def _decrypt_or_plain(text: Optional[str]) -> Optional[str]:
    """尝试解密；非密文（旧明文）或解密失败按原样返回（兼容旧数据）。"""
    if not text or not _is_ciphertext(text):
        return text
    try:
        decrypted = _get_encryptor().decrypt(text)
        if decrypted is not None:
            return decrypted
    except Exception:
        pass
    return text  # 解密失败 → 按明文兼容处理


class SessionDAO:
    """会话数据访问层，负责聊天消息的持久化与查询。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        init_db(db_path)
        # Task 5 迁移: temp 倾诉消息标记 + 24h 过期时间(老库 ALTER 兼容)
        # 会话隔离迁移: sessions 表加 session_id 维度（老库 ALTER 兼容；幂等——
        # 重复初始化时列已存在跳过；旧行 session_id 为 NULL）
        conn = self._connect()
        try:
            cols = [d[1] for d in conn.execute("PRAGMA table_info(sessions)")]
            if "temp" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN temp INTEGER DEFAULT 0")
            if "temp_expire_at" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN temp_expire_at TEXT DEFAULT ''")
            if "session_id" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN session_id TEXT")
            # 断点续传迁移: offline_completed=1 表示"客户端在生成完成前断开,回复由
            # 服务端后台完成并落库"(下次进入经 pending 接口补全); consumed=1 表示
            # 前端已消费该补全(不再返回)。老库 ALTER 兼容,幂等。
            if "offline_completed" not in cols:
                conn.execute(
                    "ALTER TABLE sessions ADD COLUMN offline_completed INTEGER DEFAULT 0")
            if "consumed" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN consumed INTEGER DEFAULT 0")
            # 会话级查询走该索引（session_id 维度；CREATE IF NOT EXISTS 天然幂等）
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_session ON sessions(session_id)")
            # 断点续传补全查询索引（user+session+未消费离线回复）
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_offline ON "
                "sessions(user_id, session_id, offline_completed, consumed)")
            conn.commit()
        finally:
            conn.close()

    def _connect(self):
        return db_connect(self.db_path, timeout=10)

    def add_message(
        self,
        user_id: str,
        role: str,
        content: str,
        intent: Optional[str] = None,
        emotion: Optional[str] = None,
        tool_calls: Optional[str] = None,
        retrieval_hit: Optional[str] = None,
        model: Optional[str] = None,
        safety_flag: Optional[str] = None,
        temp: bool = False,
        session_id: Optional[str] = None,
    ):
        """保存一条聊天消息（content 加密落库）。

        阶段 2 数据资产字段（方案 §7.2）：intent/emotion/tool_calls/retrieval_hit/
        model/safety_flag 为非敏感字段，明文存储。

        Args:
            user_id: 用户标识
            role: 'user' 或 'assistant'
            content: 消息正文（加密后存储）
            intent: 命理意图（bazi/ziwei/liuyao 等），自由对话时为 None
            emotion: LLM 分析出的情绪标签
            tool_calls: 本轮 <tool_call> 记录 JSON（[{type, params, hit}]）
            retrieval_hit: hit / miss / unused
            model: 生成模型版本
            safety_flag: 安全事件标记（self_harm_referral 等）
            temp: 倾诉临时消息（深夜默认模式）——带 24h 过期时间，
                  由 cleanup_temp 硬清理兜底（方案§4:服务端 24h 硬清理）。
            session_id: 会话标识（会话隔离：新开对话 → 新 session_id →
                  上下文只取本会话消息；None = 旧行为，按用户全量存取）。
        """
        content_enc = _encrypt_text(content)
        temp_expire_at = ""
        if temp:
            from datetime import datetime, timedelta
            temp_expire_at = (datetime.utcnow() + timedelta(hours=24)).isoformat()
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO sessions
                   (user_id, role, content, intent, emotion, tool_calls,
                    retrieval_hit, model, safety_flag, temp, temp_expire_at,
                    session_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, role, content_enc, intent, emotion, tool_calls,
                 retrieval_hit, model, safety_flag, 1 if temp else 0,
                 temp_expire_at, session_id),
            )
            conn.commit()
        finally:
            conn.close()
        self._cleanup(user_id)

    # ------------------------------------------------------------
    # k13 重试去重（2026-09-09，用户实锤：点「重试」同一句用户消息存 4 份 user 行）
    # ------------------------------------------------------------

    @staticmethod
    def normalize_dedup_text(text: Optional[str]) -> str:
        """重试去重比较用的规范化（纯函数）。

        规则：按 Unicode isspace 剥首尾空白——覆盖半角空格/全角空格（　）/
        换行（\r\n）；**内部空白不折叠**（不过度归并，防异文同判：
        "帮我 看看" ≠ "帮我看看"）。与 handler 落库前 message.strip() 同口径，
        兼容历史行首尾带空白（解密后比对）。
        """
        return (text or "").strip()

    def add_user_message_dedup(
        self,
        user_id: str,
        content: str,
        *,
        intent: Optional[str] = None,
        emotion: Optional[str] = None,
        tool_calls: Optional[str] = None,
        retrieval_hit: Optional[str] = None,
        model: Optional[str] = None,
        safety_flag: Optional[str] = None,
        temp: bool = False,
        session_id: Optional[str] = None,
        regen: bool = False,
        window_seconds: float = RETRY_DEDUP_WINDOW_SECONDS,
    ) -> dict:
        """原子写入一条 role=user 消息（k13 重试去重护栏）。

        语义（判定 + 插入在同一个 BEGIN IMMEDIATE 事务内——并发下检查与
        写入原子，WAL 单写者 + busy_timeout 排队；多进程/多线程安全，
        窗口基于 DB created_at 计算，无内存态）：
        - regen=True（前端重试/重新生成标记，plan §B2-1）：同会话存在规范化
          同文 user 行 → 不新插（该轮 assistant 由调用方正常生成链补上）；
          无匹配行（缓存命中轮等从未落库）→ 照插（审计完整）。
        - regen=False：同会话最近规范化同文 user 行距其 created_at
          ≤ window_seconds → 判定重试/双击重复 → 不新插；跨会话/异文/
          超窗同文（用户真重复提问）→ 正常插入。
        - 异常（如锁超时）向上抛，由调用方退回 add_message（全量留存铁律，
          宁重勿丢）。

        content 加密落库（同 add_message）。返回
        {"inserted": bool, "matched_id": Optional[int]}。
        """
        norm = self.normalize_dedup_text(content)
        if norm == "":
            # 空内容防御：与 add_message 同语义直接落（不做去重判定）
            self.add_message(user_id, "user", content, intent=intent,
                             emotion=emotion, tool_calls=tool_calls,
                             retrieval_hit=retrieval_hit, model=model,
                             safety_flag=safety_flag, temp=temp,
                             session_id=session_id)
            return {"inserted": True, "matched_id": None}
        content_enc = _encrypt_text(content)
        temp_expire_at = ""
        if temp:
            from datetime import datetime, timedelta
            temp_expire_at = (datetime.utcnow() + timedelta(hours=24)).isoformat()
        conn = self._connect()
        try:
            # BEGIN IMMEDIATE：进入前即取写锁——并发提交者在此排队，等锁释放后
            # 必然看到先提交者刚插入的行 → 判定不插（查插原子）
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """SELECT id, content,
                          (julianday('now') - julianday(created_at)) * 86400.0
                   FROM sessions
                   WHERE user_id = ?
                     AND role = 'user'
                     AND (? IS NULL OR session_id = ?)
                   ORDER BY id DESC LIMIT ?""",
                (user_id, session_id, session_id, _DEDUP_LOOKBACK),
            ).fetchall()
            matched_id = None
            within = False
            for rid, rcontent, age_sec in rows:
                if self.normalize_dedup_text(
                        _decrypt_or_plain(rcontent) or "") == norm:
                    matched_id = rid
                    # created_at 异常（NULL）→ 不按窗口去重（宁插勿吞，审计完整）
                    within = (age_sec is not None
                              and age_sec <= window_seconds)
                    break
            if matched_id is not None and (regen or within):
                conn.commit()  # 只读判定（判定窗口不插行），提交释放锁
                logger.info(
                    "chat user-msg dedup: user=%s session=%s regen=%s "
                    "matched_id=%s content=%.30s",
                    user_id, session_id, bool(regen), matched_id, norm)
                return {"inserted": False, "matched_id": matched_id}
            conn.execute(
                """INSERT INTO sessions
                   (user_id, role, content, intent, emotion, tool_calls,
                    retrieval_hit, model, safety_flag, temp, temp_expire_at,
                    session_id)
                   VALUES (?, 'user', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, content_enc, intent, emotion, tool_calls,
                 retrieval_hit, model, safety_flag, 1 if temp else 0,
                 temp_expire_at, session_id),
            )
            conn.commit()
            self._cleanup(user_id)  # 与 add_message 同口径：超上限软清理
            return {"inserted": True, "matched_id": matched_id}
        finally:
            conn.close()

    # ------------------------------------------------------------
    # 断点续传（生成断点续传）：客户端断开后服务端继续生成并落库，
    # 落库消息打 offline_completed 标记 → pending 接口补全 → consume 消费
    # ------------------------------------------------------------

    def get_max_message_id(self, user_id: str,
                           session_id: Optional[str] = None) -> int:
        """断点续传：生成开始前该会话最后一条消息 id（离线标记的 id 下界）。

        语义：watcher 只标记本轮生成期间新增的 assistant 消息（id > 该值），
        缓存命中/反馈等不落库分支不会误标记上一轮未标记的回复。
        """
        conn = self._connect()
        try:
            sql = "SELECT MAX(id) FROM sessions WHERE user_id = ?"
            params = [user_id]
            if session_id is not None:
                sql += " AND session_id = ?"
                params.append(session_id)
            row = conn.execute(sql, params).fetchone()
            return row[0] if row and row[0] is not None else 0
        finally:
            conn.close()

    def mark_offline_completed(self, user_id: str,
                               session_id: Optional[str] = None,
                               min_id: Optional[int] = None) -> int:
        """把指定会话中最近一条未标记的 assistant 消息标记为离线完成。

        断点续传语义：客户端在生成完成前断开（SSE 流中断），生成在服务端
        后台跑完并由 handler 正常落库——本条只补标记，不重复写入。

        Args:
            user_id: 用户标识
            session_id: 会话标识（None = 旧行为按用户维度找最近一条）
            min_id: 只标记 id > min_id 的 assistant 消息（本轮生成开始前
                    的最后一条消息 id——缓存命中/反馈等不落库分支不会误标记
                    上一轮未标记的回复；None = 不限，按最近一条）

        Returns:
            受影响行数（0 = 无符合条件且未标记的 assistant 消息）。
        """
        conn = self._connect()
        try:
            # 先取目标 id 再 UPDATE：SQLite 的 UPDATE...IN(子查询) 会随扫描行
            # 重求值子查询（本次更新已改的 flag 立即可见），同一会话多次标记会
            # 级联打到更旧的消息——两步式保证每次只打一条（幂等）。
            sql = ("SELECT id FROM sessions"
                   " WHERE user_id = ? AND role = 'assistant'"
                   " AND offline_completed = 0"
                   " AND (? IS NULL OR session_id = ?)")
            params = [user_id, session_id, session_id]
            if min_id is not None:
                sql += " AND id > ?"
                params.append(min_id)
            sql += " ORDER BY id DESC LIMIT 1"
            row = conn.execute(sql, params).fetchone()
            if row is None:
                return 0
            cur = conn.execute(
                "UPDATE sessions SET offline_completed=1 WHERE id = ?",
                (row[0],),
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def get_pending_offline_messages(
        self, user_id: str, session_id: str, limit: int = 5
    ) -> List[Dict]:
        """断点续传补全：指定会话中未消费的后台完成回复（最新在前）。

        Args:
            user_id: 用户标识（鉴权层保证 = JWT sub，防越权）
            session_id: 会话标识（必填，接口层已归一化）
            limit: 最多返回条数

        Returns:
            list of dicts: [{id, content(已解密), created_at}, ...]
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT id, content, created_at FROM sessions
                   WHERE user_id = ?
                     AND session_id = ?
                     AND role = 'assistant'
                     AND offline_completed = 1
                     AND consumed = 0
                   ORDER BY id DESC LIMIT ?""",
                (user_id, session_id, limit),
            ).fetchall()
            return [{
                "id": r[0],
                "content": _decrypt_or_plain(r[1]),
                "created_at": r[2],
            } for r in rows]
        finally:
            conn.close()

    def consume_pending_offline(self, user_id: str, session_id: str,
                                up_to_time: str = "") -> int:
        """消费断点续传补全：把该会话中 created_at <= up_to_time 的
        未消费离线回复标记为已消费（前端补全展示后调用，幂等）。

        Args:
            user_id: 用户标识
            session_id: 会话标识
            up_to_time: 消费截止时间（created_at 字符串比较，ISO 排序兼容；
                        空 = 消费全部）

        Returns:
            受影响行数。
        """
        conn = self._connect()
        try:
            sql = ("UPDATE sessions SET consumed=1"
                   " WHERE user_id = ? AND session_id = ?"
                   " AND role = 'assistant' AND offline_completed = 1"
                   " AND consumed = 0")
            params = [user_id, session_id]
            if up_to_time:
                sql += " AND created_at <= ?"
                params.append(up_to_time)
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def cleanup_temp(self, now_iso: str = "") -> int:
        """删除过期的临时倾诉消息(24h 硬清理兜底),返回删除条数。"""
        from datetime import datetime
        now_iso = now_iso or datetime.utcnow().isoformat()
        conn = self._connect()
        try:
            cur = conn.execute(
                "DELETE FROM sessions WHERE temp=1 AND temp_expire_at != '' AND temp_expire_at < ?",
                (now_iso,))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def _cleanup(self, user_id: str):
        """删除超出保留上限的旧消息，每个用户最多保留 MAX_MESSAGES_PER_USER 条。"""
        conn = self._connect()
        try:
            # 删除超出上限的最旧记录
            conn.execute(
                """DELETE FROM sessions
                   WHERE user_id = ?
                     AND id NOT IN (
                         SELECT id FROM sessions
                         WHERE user_id = ?
                         ORDER BY created_at DESC, id DESC
                         LIMIT ?
                     )""",
                (user_id, user_id, MAX_MESSAGES_PER_USER),
            )
            conn.commit()
        finally:
            conn.close()

    def _migrate_content_encrypted(self, message_id: int, encrypted_content: str):
        """把明文消息原地迁移为密文（懒迁移，仅在读取时触发）。"""
        try:
            conn = self._connect()
            conn.execute(
                "UPDATE sessions SET content=? WHERE id=?",
                (encrypted_content, message_id),
            )
            conn.commit()
            conn.close()
            logger.info("已迁移会话消息 %s 为密文存储", message_id)
        except Exception as e:
            logger.warning("会话消息迁移加密失败 id=%s: %s", message_id, e)

    def get_history(self, user_id: str, limit: int = 20,
                    session_id: Optional[str] = None,
                    temp: Optional[bool] = None) -> List[Dict]:
        """获取指定用户的最近 N 条消息（content 自动解密；旧明文读取时懒迁移）。

        会话隔离：session_id 传了则只取该会话的消息（旧行 session_id 为 NULL
        不会带入——新会话上下文绝不混入无会话标记的存量消息）；
        未传则保持旧行为（按用户取最近消息，跨会话混合）。

        Args:
            session_id: 会话标识（None=旧行为：按用户全量取）。
            temp: None=全部消息（含 temp 倾诉）；False=排除 temp 倾诉消息
                  （压缩/L2 摘要路径，隐私红线：夜间倾诉不进 L2）；True=仅 temp。

        Returns:
            list of dicts: [{id, user_id, role, content, intent, emotion,
                             tool_calls, retrieval_hit, model, safety_flag,
                             temp, temp_expire_at, created_at}, ...]
        """
        conn = self._connect()
        try:
            sql = ("SELECT id, user_id, role, content, intent, emotion,"
                   " tool_calls, retrieval_hit, model, safety_flag,"
                   " temp, temp_expire_at, created_at"
                   " FROM sessions WHERE user_id = ?")
            params = [user_id]
            if session_id is not None:
                sql += " AND session_id = ?"
                params.append(session_id)
            if temp is not None:
                sql += " AND temp = ?"
                params.append(1 if temp else 0)
            sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            # 按时间正序返回（旧→新）
            rows.reverse()
            items = []
            for r in rows:
                content = r[3]
                if content and not _is_ciphertext(content):
                    # 旧数据明文：读取时懒迁移为密文（与 dao.py 八字迁移同策略）
                    self._migrate_content_encrypted(r[0], _encrypt_text(content))
                items.append({
                    "id": r[0],
                    "user_id": r[1],
                    "role": r[2],
                    "content": _decrypt_or_plain(content),
                    "intent": r[4],
                    "emotion": r[5],
                    "tool_calls": r[6],
                    "retrieval_hit": r[7],
                    "model": r[8],
                    "safety_flag": r[9],
                    "temp": r[10],
                    "temp_expire_at": r[11],
                    "created_at": r[12],
                })
            return items
        finally:
            conn.close()

    # ------------------------------------------------------------
    # L2 会话摘要（方案 §5.4：增量摘要持久化，加密落库）
    # ------------------------------------------------------------

    def save_summary(self, user_id: str, summary: str, memories: Optional[list] = None,
                     model: str = "", message_count: int = 0, token_count: int = 0) -> None:
        """保存/覆盖该用户的 L2 会话摘要（加密落库，新摘要替换旧摘要）。"""
        conn = self._connect()
        try:
            mem_json = json.dumps(memories, ensure_ascii=False) if memories else ""
            conn.execute(
                """INSERT INTO session_summaries
                   (user_id, summary, memories, model, message_count, token_count, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(user_id) DO UPDATE SET
                     summary=excluded.summary,
                     memories=excluded.memories,
                     model=excluded.model,
                     message_count=excluded.message_count,
                     token_count=excluded.token_count,
                     updated_at=datetime('now')""",
                (user_id, _encrypt_text(summary), _encrypt_text(mem_json),
                 model, message_count, token_count),
            )
            conn.commit()
        finally:
            conn.close()

    def get_summary(self, user_id: str) -> Optional[dict]:
        """获取用户最新的 L2 会话摘要。

        Returns:
            dict: {summary, memories, model, message_count, token_count, created_at}
            无摘要时返回 None。
        """
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT summary, memories, model, message_count, token_count, created_at
                   FROM session_summaries WHERE user_id = ?""",
                (user_id,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        memories = []
        if row[1]:
            try:
                memories = json.loads(_decrypt_or_plain(row[1]) or "[]")
            except (ValueError, TypeError):
                memories = []
        return {
            "summary": _decrypt_or_plain(row[0]) or "",
            "memories": memories,
            "model": row[2] or "",
            "message_count": row[3] or 0,
            "token_count": row[4] or 0,
            "created_at": row[5],
        }

    def clear_summary(self, user_id: str) -> None:
        """删除用户的 L2 会话摘要（隐私删除）。"""
        conn = self._connect()
        try:
            conn.execute("DELETE FROM session_summaries WHERE user_id = ?", (user_id,))
            conn.commit()
        finally:
            conn.close()

    def get_context_for_llm(self, user_id: str, history_limit: int = 15,
                            session_id: Optional[str] = None,
                            temp: Optional[bool] = None) -> List[Dict]:
        """获取可用于 LLM API 的历史消息列表（自动解密）。

        会话隔离：session_id 传了则只取该会话消息（AI 上下文 = 当前会话，
        新开对话不带上个对话内容）；未传保持旧行为（按用户全量取）。

        Args:
            user_id: 用户标识
            history_limit: 最多返回多少条消息（默认 15，控制 token 用量）
            session_id: 会话标识（None=旧行为）
            temp: 透传 get_history 的 temp 过滤（None=全部；False=白天不读
                  夜间倾诉；True=仅 temp），None 保持原行为

        Returns:
            list of dicts: [{"role": "user"/"assistant", "content": "..."}, ...]

        k7b：返回内容将直接进 LLM 上下文——assistant 消息先经
        strip_card_decor_for_llm 剥卡装饰（卡/图/页脚只由装配层注入，LLM
        不该看到卡样例，否则会仿写卡尾自增强）。只读清洗，绝不回写库
        （库内定稿保持原样——前端历史渲染仍读原文含卡）。
        """
        history = self.get_history(user_id, limit=history_limit,
                                   session_id=session_id, temp=temp)
        out = []
        for h in history:
            content = h["content"]
            if h["role"] == "assistant" and content:
                content = strip_card_decor_for_llm(content)
            out.append({"role": h["role"], "content": content})
        return out

    def clear_history(self, user_id: str):
        """清除指定用户的所有会话消息。"""
        conn = self._connect()
        try:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.commit()
        finally:
            conn.close()
