"""数据访问对象."""
import json
import sqlite3
from typing import Optional, Dict
from datetime import datetime

from .models import init_db

class UserDAO:
    def __init__(self, db_path: str):
        self.db_path = db_path
        init_db(db_path)

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def get_user_bazi(self, user_id: str) -> Optional[Dict]:
        """获取用户已保存的八字"""
        conn = self._connect()
        row = conn.execute(
            "SELECT bazi_info FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        conn.close()
        if row and row[0]:
            return json.loads(row[0])
        return None

    def save_user_bazi(self, user_id: str, bazi_info: dict):
        """保存或更新用户八字信息"""
        conn = self._connect()
        existing = conn.execute(
            "SELECT user_id FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()

        bazi_json = json.dumps(bazi_info, ensure_ascii=False)
        now = datetime.now().isoformat()

        if existing:
            conn.execute(
                "UPDATE users SET bazi_info=?, updated_at=?, consultation_count=consultation_count+1 WHERE user_id=?",
                (bazi_json, now, user_id),
            )
        else:
            conn.execute(
                "INSERT INTO users (user_id, bazi_info, created_at, updated_at, consultation_count) VALUES (?,?,?,?,1)",
                (user_id, bazi_json, now, now),
            )
        conn.commit()
        conn.close()

    def save_consultation(self, user_id: str, question: str, chart_result=None, analysis: str = "", intent: str = "bazi"):
        """保存咨询记录"""
        conn = self._connect()

        if chart_result is not None and hasattr(chart_result, 'bazi'):
            # BaziResult handling — preserve backward compatibility
            chart_json = json.dumps({
                "bazi": chart_result.bazi,
                "day_master": chart_result.day_master,
                "wuxing": chart_result.wuxing,
                "shishen": chart_result.shishen,
                "geju": chart_result.geju,
                "yongshen": chart_result.yongshen,
            }, ensure_ascii=False)
        elif isinstance(chart_result, dict):
            chart_json = json.dumps(chart_result, ensure_ascii=False)
        elif chart_result is not None:
            chart_json = json.dumps({"type": type(chart_result).__name__}, ensure_ascii=False)
        else:
            chart_json = ""

        conn.execute(
            "INSERT INTO consultations (user_id, question, intent, chart_data, analysis) VALUES (?,?,?,?,?)",
            (user_id, question, intent, chart_json, analysis),
        )
        conn.commit()
        conn.close()

    def get_user_stats(self) -> dict:
        """获取用户统计"""
        conn = self._connect()
        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_cons = conn.execute("SELECT COUNT(*) FROM consultations").fetchone()[0]
        conn.close()
        return {"total_users": total, "total_consultations": total_cons}
