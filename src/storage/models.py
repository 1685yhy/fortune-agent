"""SQLite 数据模型."""
import sqlite3
import json
from pathlib import Path
from datetime import datetime

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    bazi_info TEXT,          -- JSON: {year, month, day, hour, minute, city, gender, bazi[]}
    ziwei_info TEXT,          -- JSON: full ziwei chart
    push_enabled INTEGER DEFAULT 1,
    push_time TEXT DEFAULT '08:00',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    consultation_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS consultations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    question TEXT,
    intent TEXT,              -- bazi, ziwei, etc.
    chart_data TEXT,          -- JSON: full chart
    analysis TEXT,            -- LLM response
    feedback TEXT,            -- user feedback
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS push_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    push_date TEXT NOT NULL,
    message TEXT,
    success INTEGER DEFAULT 1,
    error TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS memberships (
    user_id TEXT PRIMARY KEY,
    plan TEXT NOT NULL,                -- 'free', 'basic', 'pro', 'annual'
    started_at TEXT,
    expires_at TEXT,
    queries_used INTEGER DEFAULT 0,
    queries_limit INTEGER,            -- NULL = unlimited
    auto_renew INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    amount REAL NOT NULL,
    plan TEXT NOT NULL,
    status TEXT DEFAULT 'pending',    -- pending/paid/cancelled
    payment_method TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_consultations_user ON consultations(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_push_log_user_date ON push_log(user_id, push_date);
CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id, created_at);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id TEXT PRIMARY KEY,
    style_sassy REAL DEFAULT 0.33,       -- 毒舌权重 (EMA)
    style_analyst REAL DEFAULT 0.33,     -- 分析权重 (EMA)
    style_gentle REAL DEFAULT 0.34,      -- 温柔权重 (EMA)
    topic_wealth REAL DEFAULT 0.2,       -- 财运话题偏好
    topic_love REAL DEFAULT 0.2,         -- 感情话题偏好
    topic_career REAL DEFAULT 0.2,       -- 事业话题偏好
    topic_health REAL DEFAULT 0.2,       -- 健康话题偏好
    topic_growth REAL DEFAULT 0.2,       -- 成长话题偏好
    prefer_short INTEGER DEFAULT 0,      -- 偏好简短回复
    feedback_count INTEGER DEFAULT 0,    -- 收到的反馈总数
    positive_count INTEGER DEFAULT 0,    -- 好评数
    last_style TEXT DEFAULT '',          -- 最后使用的人格模式
    last_topic TEXT DEFAULT '',          -- 最后关注的话题
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,           -- 'user' or 'assistant'
    content TEXT NOT NULL,
    intent TEXT,                  -- bazi/ziwei/etc, NULL for free chat
    emotion TEXT,                 -- LLM 分析出的情绪标签（方案 §7.2）
    tool_calls TEXT,              -- JSON: 本轮 <tool_call> 记录 [{type, params, hit}]
    retrieval_hit TEXT DEFAULT 'unused',  -- hit / miss / unused
    model TEXT,                   -- 生成模型/版本（训练溯源）
    safety_flag TEXT,             -- 安全事件标记（如 self_harm_referral 自伤转介）
    session_id TEXT,              -- 会话标识（会话隔离：新开对话 → 新 session_id）
    created_at TEXT DEFAULT (datetime('now')),
    id INTEGER PRIMARY KEY AUTOINCREMENT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, created_at);
-- 注意：idx_sessions_session 不在 SCHEMA 建（老库 executescript 时列尚不存在），
-- 在 _migrate_db 中与 session_id ALTER 同批创建

-- L2 会话摘要（增量摘要持久化；summary/memories 加密落库，同 sessions.content）
CREATE TABLE IF NOT EXISTS session_summaries (
    user_id TEXT PRIMARY KEY,
    summary TEXT,                 -- 最新摘要（加密）
    memories TEXT,                -- JSON 数组（加密）：持久事实 → 转 L3
    model TEXT,
    message_count INTEGER DEFAULT 0,
    token_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- 多人档案（P2）：一个用户下多个命主（自己/家人/朋友）。
-- name/relation 明文（列表展示用）；出生信息 birth_enc 整行 JSON AES 加密
-- （沿用 users.bazi_info 的加密模式，敏感字段不落明文）。
-- is_default 唯一性由应用层保证（set_default 事务内清旧置新）。
CREATE TABLE IF NOT EXISTS persons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    relation TEXT DEFAULT '其他',
    is_default INTEGER DEFAULT 0,
    birth_enc TEXT,               -- AES 加密 JSON: {gender, birth_year, birth_month,
                                  --   birth_day, birth_hour, birth_minute, calendar, city}
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_persons_user ON persons(user_id, is_default);
"""

def connect(db_path: str, timeout: float = 10.0) -> sqlite3.Connection:
    """打开 SQLite 连接：启用 WAL 模式 + busy_timeout。

    - journal_mode=WAL：单写多读并发安全，读写互不阻塞；
      WAL 是数据库文件的持久属性，首次设置后所有连接自动生效，
      每次连接再执行一次为无害的幂等操作。
    - busy_timeout=10000ms：并发写冲突时等待而非立即抛 locked。
    """
    conn = sqlite3.connect(db_path, timeout=timeout)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
    except sqlite3.Error:
        pass  # 只读文件系统等极端场景不致命，保持默认行为
    return conn


def _get_columns(conn, table: str) -> set:
    """获取表中现有列名"""
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def _migrate_db(conn):
    """执行数据库迁移（新增列等）"""
    changes = False
    existing_cols = _get_columns(conn, "users")

    if "push_enabled" not in existing_cols:
        conn.execute("ALTER TABLE users ADD COLUMN push_enabled INTEGER DEFAULT 1")
        changes = True
    if "push_time" not in existing_cols:
        conn.execute("ALTER TABLE users ADD COLUMN push_time TEXT DEFAULT '08:00'")
        changes = True

    # P2 账号注销（软删+90 天归档）：status active/cancelled + cancelled_at
    if "status" not in existing_cols:
        conn.execute("ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'active'")
        changes = True
    if "cancelled_at" not in existing_cols:
        conn.execute("ALTER TABLE users ADD COLUMN cancelled_at TEXT")
        changes = True

    # 阶段 2（方案 §7.2）：sessions 表补齐数据资产字段（安全加列，幂等）
    # 会话隔离：sessions 加 session_id 维度（老库 ALTER 兼容；新库已含于 SCHEMA）
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "sessions" in tables:
        session_cols = _get_columns(conn, "sessions")
        for col, ddl in (
            ("emotion", "TEXT"),
            ("tool_calls", "TEXT"),
            ("retrieval_hit", "TEXT DEFAULT 'unused'"),
            ("model", "TEXT"),
            ("safety_flag", "TEXT"),
            ("session_id", "TEXT"),
        ):
            if col not in session_cols:
                conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {ddl}")
                changes = True
        # 会话级查询索引（老库可能只缺索引不缺列：无条件执行并提交，保证落盘）
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_session ON sessions(session_id)")
        changes = True

    if changes:
        conn.commit()


def init_db(db_path: str):
    """初始化数据库"""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    conn.executescript(SCHEMA_SQL)
    _migrate_db(conn)
    conn.commit()
    return conn
