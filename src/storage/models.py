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

CREATE INDEX IF NOT EXISTS idx_consultations_user ON consultations(user_id, created_at);
"""

def init_db(db_path: str):
    """初始化数据库"""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn
