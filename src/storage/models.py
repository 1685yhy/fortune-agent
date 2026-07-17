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

CREATE TABLE IF NOT EXISTS sessions (
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,           -- 'user' or 'assistant'
    content TEXT NOT NULL,
    intent TEXT,                  -- bazi/ziwei/etc, NULL for free chat
    created_at TEXT DEFAULT (datetime('now')),
    id INTEGER PRIMARY KEY AUTOINCREMENT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, created_at);
"""

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

    if changes:
        conn.commit()


def init_db(db_path: str):
    """初始化数据库"""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    _migrate_db(conn)
    conn.commit()
    return conn
