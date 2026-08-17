"""会话隔离测试：sessions 表 session_id 维度 —— 新开对话不带上个对话内容。

PM 反馈：新开对话后 AI 回复还是接着上上个对话（记忆混淆）。
方案：前端新开对话生成新 session_id → 请求随附 → 后端上下文只取本会话消息。
本测试覆盖：迁移（新库/老库/幂等）、按会话隔离取回、无 session_id 兼容、
NULL 旧行不带入、API 层 session_id 归一化。
"""
import os
import re
import sqlite3
import tempfile

import pytest

from src.storage.session_dao import SessionDAO
from src.storage.models import init_db, connect
from src.api.chat_stream import normalize_session_id, SESSION_ID_RE

USER = "u_test_session"
SID_A = "s_abc12345"   # 合法：8~64 位 [A-Za-z0-9_-]
SID_B = "s_xyz67890"


@pytest.fixture
def db_path():
    """临时数据库文件（自动清理 .db/-wal/-shm）。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    yield path
    for suffix in ("", "-wal", "-shm"):
        p = path + suffix
        if os.path.exists(p):
            try:
                os.unlink(p)
            except OSError:
                pass


def _add(dao, sid, *texts):
    """按顺序写入 u/a 交替消息（同一会话）。"""
    for i, t in enumerate(texts):
        dao.add_message(USER, "user" if i % 2 == 0 else "assistant", t,
                        session_id=sid)


# ── 迁移 ─────────────────────────────────────────────────────────────

def test_migration_adds_session_id_column(db_path):
    """新库：sessions 表含 session_id 列 + 会话索引。"""
    dao = SessionDAO(db_path)  # 内部 init_db + ALTER 尝试
    conn = connect(db_path)
    try:
        cols = [d[1] for d in conn.execute("PRAGMA table_info(sessions)")]
        idx = {r[1] for r in conn.execute(
            "PRAGMA index_list(sessions)").fetchall()}
    finally:
        conn.close()
    assert "session_id" in cols
    assert "idx_sessions_session" in idx


def test_migration_idempotent(db_path):
    """重复初始化（进程重启/多实例）不报错、不加重复列。"""
    SessionDAO(db_path)
    SessionDAO(db_path)   # 第二次构造 → ALTER 已被列检查跳过
    SessionDAO(db_path)   # 第三次仍幂等
    conn = connect(db_path)
    try:
        cols = [d[1] for d in conn.execute("PRAGMA table_info(sessions)")]
    finally:
        conn.close()
    assert cols.count("session_id") == 1


def test_migration_legacy_db(db_path):
    """老库（无 session_id 列）：init 后列补齐，旧行保留且 session_id 为 NULL。"""
    # 手工构造旧版表结构（含 temp 列，模拟 Task 5 后的库）
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE sessions (
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            intent TEXT,
            emotion TEXT,
            tool_calls TEXT,
            retrieval_hit TEXT DEFAULT 'unused',
            model TEXT,
            safety_flag TEXT,
            temp INTEGER DEFAULT 0,
            temp_expire_at TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            id INTEGER PRIMARY KEY AUTOINCREMENT
        );
        INSERT INTO sessions (user_id, role, content) VALUES ('old_user', 'user', '旧对话');
        """
    )
    conn.commit()
    conn.close()
    dao = SessionDAO(db_path)   # 触发迁移
    conn = connect(db_path)
    try:
        cols = [d[1] for d in conn.execute("PRAGMA table_info(sessions)")]
        row = conn.execute("SELECT content, session_id FROM sessions WHERE user_id='old_user'").fetchone()
    finally:
        conn.close()
    assert "session_id" in cols
    assert row is not None and row[0] == "旧对话" and row[1] is None  # 旧行数据不丢


# ── 会话隔离读写 ──────────────────────────────────────────────────────

def test_session_isolation_by_context(db_path):
    """两个会话各自的消息按 session_id 隔离取回，互不串味。"""
    dao = SessionDAO(db_path)
    _add(dao, SID_A, "A1", "A回", "A2", "A回2")
    _add(dao, SID_B, "B1", "B回")
    ctx_a = dao.get_context_for_llm(USER, history_limit=20, session_id=SID_A)
    ctx_b = dao.get_context_for_llm(USER, history_limit=20, session_id=SID_B)
    assert [m["content"] for m in ctx_a] == ["A1", "A回", "A2", "A回2"]
    assert [m["content"] for m in ctx_b] == ["B1", "B回"]


def test_new_session_sees_nothing_of_old(db_path):
    """新开对话（新 session_id）查不到上个会话的任何消息。"""
    dao = SessionDAO(db_path)
    _add(dao, SID_A, "上个对话说了什么")
    fresh = dao.get_context_for_llm(USER, history_limit=20,
                                    session_id=SID_B)
    assert fresh == []


def test_legacy_null_rows_not_in_session_context(db_path):
    """旧行（session_id NULL）不带入新会话上下文（存量消息不污染新对话）。"""
    dao = SessionDAO(db_path)
    dao.add_message(USER, "user", "旧接口遗留消息")          # session_id 默认 None
    dao.add_message(USER, "user", "本会话消息", session_id=SID_A)
    ctx = dao.get_context_for_llm(USER, history_limit=20, session_id=SID_A)
    assert [m["content"] for m in ctx] == ["本会话消息"]


def test_no_session_id_keeps_old_behavior(db_path):
    """不传 session_id = 旧行为：按用户取最近消息（跨会话混合），存量调用不受影响。"""
    dao = SessionDAO(db_path)
    _add(dao, SID_A, "会话A的消息", "A回复")
    _add(dao, SID_B, "会话B的消息", "B回复")
    ctx = dao.get_context_for_llm(USER, history_limit=20)   # 无 session_id
    contents = [m["content"] for m in ctx]
    assert "会话A的消息" in contents and "会话B的消息" in contents
    assert len(ctx) == 4


def test_history_and_temp_filters_compose(db_path):
    """session_id 与 temp 过滤可组合（白天上下文排除倾诉的会话内过滤）。"""
    dao = SessionDAO(db_path)
    dao.add_message(USER, "user", "白天问题", session_id=SID_A)
    dao.add_message(USER, "user", "深夜倾诉", session_id=SID_A, temp=True)
    ctx = dao.get_context_for_llm(USER, history_limit=20,
                                  session_id=SID_A, temp=False)
    assert [m["content"] for m in ctx] == ["白天问题"]
    # 无 session_id 的旧路径 temp 语义不变
    ctx_all = dao.get_context_for_llm(USER, history_limit=20, temp=False)
    assert len(ctx_all) == 1


def test_add_message_stores_session_id(db_path):
    """写入的消息确实带 session_id 落库（content 加密，按 session_id 维度反查）。"""
    dao = SessionDAO(db_path)
    dao.add_message(USER, "user", "你好", session_id=SID_A)
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id=? AND session_id=?",
            (USER, SID_A)).fetchone()
        none_cnt = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id=? AND session_id IS NULL",
            (USER,)).fetchone()
    finally:
        conn.close()
    assert row[0] == 1 and none_cnt[0] == 0


# ── API 层归一化 ──────────────────────────────────────────────────────

def test_normalize_session_id_valid():
    assert SESSION_ID_RE.match("s_lxk2m3n4")           # 前端生成格式
    assert SESSION_ID_RE.match("s_" + "a" * 62)        # 64 位上限
    assert SESSION_ID_RE.match("ABC_123-def")          # 大小写/下划线/连字符
    assert normalize_session_id("  s_lxk2m3n4  ") == "s_lxk2m3n4"


def test_normalize_session_id_invalid_falls_back_to_none():
    assert normalize_session_id("") is None            # 空 = 旧行为
    assert normalize_session_id("   ") is None
    assert normalize_session_id(None) is None
    assert normalize_session_id("short") is None       # 不足 8 位
    assert normalize_session_id("s_" + "a" * 70) is None  # 超 64 位
    assert normalize_session_id("s_中文会话id") is None   # 非法字符
    assert normalize_session_id("s_id with space") is None
    assert normalize_session_id("s_id;DROP TABLE") is None  # 注入形状也拒绝
