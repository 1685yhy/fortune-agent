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
    -- ⚠️ 已废弃（k62 移除代码路径，未 DROP COLUMN）：下面 3 列是早期「3 模式
    -- 人设」残留的三个风格权重。三者只更新**当时被选中的那一个**（handler 传
    -- style=preferred_style，即当时 argmax），随后归一化 → 权重会分化（实测
    -- 30×全👍终值 0.0476/0.0476/0.9049，**不是**恒等 ≈1/3）。
    -- preferred_style 就是当时的 argmax：默认起步落在 'gentle'（建表默认 0.34），
    -- 但反馈符号序列能把它推走（全差评时确定性 3-循环 sassy→analyst→gentle：
    -- 10 次 → 'sassy'、12 次 → 'gentle'）⇒ 是「把自身输出当输入」的自强化回路，
    -- 会经 to_prompt_hint() 以中文名给用户注入从未表达过的风格偏好。
    -- 代码侧已整体移除（PreferenceDAO 不再读写）。删除依据不靠上面这些数字，
    -- 而在于：**它不编码用户特异的风格偏好，只是反馈符号序列的确定性函数**
    -- ——准确机制与旧措辞更正见 preference_dao.py 类 docstring（2026-09-20
    -- 已按一手实测更正，原先它指向的那几句是被证伪的旧说法）。
    -- **列保留**：生产库有真实数据，破坏性迁移需先报批。
    -- 新写入行取本处默认值。
    style_sassy REAL DEFAULT 0.33,       -- 【已废弃·k62】毒舌权重 (EMA)
    style_analyst REAL DEFAULT 0.33,     -- 【已废弃·k62】分析权重 (EMA)
    style_gentle REAL DEFAULT 0.34,      -- 【已废弃·k62】温柔权重 (EMA)
    topic_wealth REAL DEFAULT 0.2,       -- 财运话题偏好
    topic_love REAL DEFAULT 0.2,         -- 感情话题偏好
    topic_career REAL DEFAULT 0.2,       -- 事业话题偏好
    topic_health REAL DEFAULT 0.2,       -- 健康话题偏好
    topic_growth REAL DEFAULT 0.2,       -- 成长话题偏好
    prefer_short INTEGER DEFAULT 0,      -- 偏好简短回复
    feedback_count INTEGER DEFAULT 0,    -- 收到的反馈总数
    positive_count INTEGER DEFAULT 0,    -- 好评数
    last_style TEXT DEFAULT '',          -- 【已废弃·k62】最后使用的人格模式（随三权重移除）
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


# ══════════════════════════════════════════════════════════════════════
# k76：账号注销/删除的**用户数据落点清单**（单一事实源）
# ══════════════════════════════════════════════════════════════════════
# 背景（k72 独立核查 A3）：注销删除覆盖不全 —— `privacy.delete_user_data()`
# 只删 consultations/push_log/user_preferences/users/payments；`dao.cleanup_
# cancelled_accounts()` 的 10 张表清单**也不含** zeri_plans / night_lamp /
# night_prefs / night_remember / jian_prefs / ming_saves / ming_quota /
# qian_saves / chat_quota / share_entries。两条链路各写各的清单 ⇒ 必然漂移。
# 本批把清单**收敛到此处一份**，两条链路都读它（数据一致性铁律）。
#
# 清单来源 = `SCHEMA_SQL` 建表（本文件）+ 各 DAO 自建表（`CREATE TABLE` 点）：
#   favorites(favorite_dao) chart_records(chart_dao) jian_prefs/jian_cards(jian_dao)
#   ming_saves/ming_quota(ming_dao) qian_saves(qian_dao) zeri_*(zeri_dao)
#   night_prefs/night_remember(night_dao) night_lamp(lamp_dao)
#   chat_quota(chat_quota_dao) share_entries(share_dao) midas_orders(pay_midas)
#
# 三类判定：
#   【应删】本人可识别/可关联到本人的数据，注销后不再保留（PIPL 第 47 条删除权）。
#   【应删·按伪名】share_entries 不存 user_id（匿名分享红线），按 HMAC 伪名
#       `owner_tag` 定位删除；查不到归属（当年未登录创建）的分享行删不掉，
#       但受 30 天有效期约束（见 src/config.share_ttl_days）。
#   【依法留存】payments / midas_orders = 支付流水，**不删**，依据《电子商务法》
#       第 31 条（商品和服务信息、交易信息应当保存**不少于三年**）+
#       《网络交易监督管理办法》第 15 条。留存的是"交易事实"，注销不消灭它。
# ⚠️ 唯一性：下列元组 (表名, 归属列) 是**删除行为的唯一事实源**；新增用户表
#    必须在 `ACCOUNT_PURGE_TABLES` 里登记，`tests/test_k76_*` 会核对建表清单。
ACCOUNT_PURGE_TABLES = (
    # 主表（users 行最后由调用方删，保证其它表先清干净）
    ("consultations", "user_id"),
    ("push_log", "user_id"),
    ("memberships", "user_id"),
    ("user_preferences", "user_id"),
    ("sessions", "user_id"),
    ("session_summaries", "user_id"),
    ("persons", "user_id"),
    ("chart_records", "user_id"),
    ("favorites", "user_id"),
    ("jian_prefs", "user_id"),
    ("jian_cards", "user_id"),
    ("ming_saves", "user_id"),
    ("ming_quota", "user_id"),
    ("qian_saves", "user_id"),
    ("zeri_plans", "user_id"),
    ("zeri_prefs", "user_id"),
    ("zeri_quota", "user_id"),
    ("night_prefs", "user_id"),
    ("night_remember", "user_id"),
    ("night_lamp", "user_id"),
    ("chat_quota", "user_id"),
    ("user_tone_feedback", "user_id"),   # 遗留表（docs/DATABASE.md §2.9）：代码零引用，
                                         # 但生产库**确实存在**且有 user_id 列 —— 有行
                                         # 就必须能删干净，否则"期满彻底删除"名不副实
    ("share_entries", "owner_tag"),      # 匿名分享：按 HMAC 伪名归属，见上注
)

#: 注销时**依法留存**的表（(表名, 归属列, 依据)）——明确"不删"，并说明为什么。
ACCOUNT_RETAIN_TABLES = (
    ("payments", "user_id",
     "支付流水：《电子商务法》第 31 条（交易信息保存不少于三年）"),
    ("midas_orders", "user_id",
     "支付流水（米大师订单）：同《电子商务法》第 31 条"),
)

#: 用户**文件类**落点（(标识, 依据)）——由 `dao.purge_account_files()` 实现删除。
ACCOUNT_PURGE_FILE_KINDS = (
    ("avatar", "data/avatars/{user_id}.jpg 头像"),
    ("uploads", "data/uploads/chat/* —— 仅删该用户会话/收藏/分享里被引用的那些"),
    ("memory", "data/memory/{user_id}.json L3 画像"),
    ("reports", "data/reports/*.json —— 归属为该用户的报告（owner_enc 命中）"),
    ("share_cards", "分享图 PNG —— 仅当对应报告被删时一并删"),
)


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
