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

logger = logging.getLogger(__name__)

# 对话历史是核心资产（方案 §7.1：原始对话全量留存，一条不丢）；
# 自动清理上限仅为防失控的软保护（2000 条 ≈ 1000 轮），
# L2 增量摘要负责运行时压缩，存储层不丢弃原文。
MAX_MESSAGES_PER_USER = 2000

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
        conn = self._connect()
        try:
            cols = [d[1] for d in conn.execute("PRAGMA table_info(sessions)")]
            if "temp" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN temp INTEGER DEFAULT 0")
            if "temp_expire_at" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN temp_expire_at TEXT DEFAULT ''")
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
                    retrieval_hit, model, safety_flag, temp, temp_expire_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, role, content_enc, intent, emotion, tool_calls,
                 retrieval_hit, model, safety_flag, 1 if temp else 0, temp_expire_at),
            )
            conn.commit()
        finally:
            conn.close()
        self._cleanup(user_id)

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
                    temp: Optional[bool] = None) -> List[Dict]:
        """获取指定用户的最近 N 条消息（content 自动解密；旧明文读取时懒迁移）。

        Args:
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
                            temp: Optional[bool] = None) -> List[Dict]:
        """获取可用于 LLM API 的历史消息列表（自动解密）。

        Args:
            user_id: 用户标识
            history_limit: 最多返回多少条消息（默认 15，控制 token 用量）
            temp: 透传 get_history 的 temp 过滤（None=全部；False=白天不读
                  夜间倾诉；True=仅 temp），None 保持原行为

        Returns:
            list of dicts: [{"role": "user"/"assistant", "content": "..."}, ...]
        """
        history = self.get_history(user_id, limit=history_limit, temp=temp)
        return [{"role": h["role"], "content": h["content"]} for h in history]

    def clear_history(self, user_id: str):
        """清除指定用户的所有会话消息。"""
        conn = self._connect()
        try:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.commit()
        finally:
            conn.close()
