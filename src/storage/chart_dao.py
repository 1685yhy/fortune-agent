"""排盘结果持久化（对话/表单排盘统一落库，重看 0 重跑 0 生成）。"""
import json, logging
from .models import connect as db_connect
from .dao import _encrypt_text, _decrypt_or_plain

logger = logging.getLogger(__name__)

SCHEMA_CHART = """
CREATE TABLE IF NOT EXISTS chart_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    person_id INTEGER,
    birth_enc TEXT,        -- AES: {year,month,day,hour,minute,city,gender,calendar}
    bazi_enc TEXT,         -- AES: BaziResult 全字段 JSON（四柱/大运/流年/神煞等）
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chart_user ON chart_records(user_id, created_at);
"""


def _connect(db_path: str):
    """打开指向 db_path 的新连接（每次独立，调用方负责 close）。"""
    return db_connect(db_path)


class ChartDAO:
    def __init__(self, db_path: str):
        self.db_path = db_path
        conn = _connect(db_path)
        try:
            conn.executescript(SCHEMA_CHART)
            conn.commit()
        finally:
            conn.close()

    def save_chart(self, user_id: str, person_id, birth: dict, result: dict) -> int:
        birth_enc = _encrypt_text(json.dumps(birth, ensure_ascii=False))
        bazi_enc = _encrypt_text(json.dumps(result, ensure_ascii=False, default=str))
        conn = _connect(self.db_path)
        try:
            cur = conn.execute(
                "INSERT INTO chart_records (user_id, person_id, birth_enc, bazi_enc) "
                "VALUES (?,?,?,?)", (user_id, person_id, birth_enc, bazi_enc))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def _row_to_dict(self, row):
        if not row:
            return None
        return {"id": row[0], "birth": json.loads(_decrypt_or_plain(row[3]) or "{}"),
                "bazi_json": json.loads(_decrypt_or_plain(row[4]) or "{}"),
                "created_at": row[5]}

    def get_latest_chart(self, user_id: str, person_id=None) -> dict | None:
        conn = _connect(self.db_path)
        try:
            if person_id is not None:
                row = conn.execute(
                    "SELECT id, user_id, person_id, birth_enc, bazi_enc, created_at "
                    "FROM chart_records WHERE user_id=? AND person_id=? "
                    "ORDER BY id DESC LIMIT 1", (user_id, person_id)).fetchone()
            else:
                row = conn.execute(
                    "SELECT id, user_id, person_id, birth_enc, bazi_enc, created_at "
                    "FROM chart_records WHERE user_id=? ORDER BY id DESC LIMIT 1",
                    (user_id,)).fetchone()
        finally:
            conn.close()
        return self._row_to_dict(row)

    def list_charts(self, user_id: str, limit: int = 10) -> list:
        conn = _connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT id, user_id, person_id, birth_enc, bazi_enc, created_at "
                "FROM chart_records WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit)).fetchall()
        finally:
            conn.close()
        return [self._row_to_dict(r) for r in rows]
