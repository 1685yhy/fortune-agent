"""数据访问对象."""
import json
import sqlite3
from typing import Optional, Dict
from datetime import datetime

from .models import init_db

class UserDAO:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._last_consultation_id: int = 0
        init_db(db_path)

    def _connect(self):
        return sqlite3.connect(self.db_path)

    @property
    def last_consultation_id(self) -> int:
        return self._last_consultation_id

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

        cursor = conn.execute(
            "INSERT INTO consultations (user_id, question, intent, chart_data, analysis) VALUES (?,?,?,?,?)",
            (user_id, question, intent, chart_json, analysis),
        )
        consultation_id = cursor.lastrowid
        self._last_consultation_id = consultation_id
        conn.commit()
        conn.close()
        return consultation_id

    def get_user_stats(self) -> dict:
        """获取用户统计"""
        conn = self._connect()
        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_cons = conn.execute("SELECT COUNT(*) FROM consultations").fetchone()[0]
        conn.close()
        return {"total_users": total, "total_consultations": total_cons}

    def get_all_users_with_bazi(self) -> list:
        """查询所有保存了八字信息的用户"""
        conn = self._connect()
        rows = conn.execute(
            "SELECT user_id, bazi_info, push_enabled, push_time FROM users WHERE bazi_info IS NOT NULL"
        ).fetchall()
        conn.close()
        users = []
        for row in rows:
            users.append({
                "user_id": row[0],
                "bazi_info": json.loads(row[1]) if row[1] else None,
                "push_enabled": bool(row[2]),
                "push_time": row[3] or "08:00",
            })
        return users

    def get_user_push_settings(self, user_id: str) -> dict:
        """查询用户推送设置"""
        conn = self._connect()
        row = conn.execute(
            "SELECT push_enabled, push_time FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        conn.close()
        if row:
            return {"push_enabled": bool(row[0]), "push_time": row[1] or "08:00"}
        return {"push_enabled": False, "push_time": "08:00"}

    def set_push_enabled(self, user_id: str, enabled: bool):
        """设置用户是否开启推送"""
        conn = self._connect()
        conn.execute(
            "UPDATE users SET push_enabled=?, updated_at=? WHERE user_id=?",
            (1 if enabled else 0, datetime.now().isoformat(), user_id),
        )
        conn.commit()
        conn.close()

    def set_push_time(self, user_id: str, push_time: str):
        """设置用户推送时间"""
        conn = self._connect()
        conn.execute(
            "UPDATE users SET push_time=?, updated_at=? WHERE user_id=?",
            (push_time, datetime.now().isoformat(), user_id),
        )
        conn.commit()
        conn.close()

    def get_user_consultations(self, user_id: str, limit: int = 20) -> list:
        """获取用户最近咨询历史"""
        conn = self._connect()
        rows = conn.execute(
            """SELECT id, question, intent, analysis, feedback, created_at
               FROM consultations
               WHERE user_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (user_id, limit),
        ).fetchall()
        conn.close()
        return [
            {
                "id": r[0],
                "question": r[1],
                "intent": r[2],
                "analysis_preview": (r[3] or "")[:100],
                "feedback": r[4],
                "created_at": r[5],
            }
            for r in rows
        ]

    def get_user_accuracy(self, user_id: str) -> dict:
        """计算用户历史预测准确率"""
        conn = self._connect()
        rows = conn.execute(
            """SELECT id, feedback FROM consultations
               WHERE user_id = ? AND feedback IS NOT NULL AND feedback != ''""",
            (user_id,),
        ).fetchall()
        conn.close()

        total = len(rows)
        positive = sum(1 for r in rows if r[1] == "positive")
        negative = sum(1 for r in rows if r[1] == "negative")

        accuracy_pct = round((positive / total) * 100, 1) if total > 0 else None

        return {
            "user_id": user_id,
            "total_feedback": total,
            "positive": positive,
            "negative": negative,
            "accuracy_pct": accuracy_pct,
        }

    def save_feedback(self, consultation_id: int, feedback: str) -> bool:
        """保存用户反馈（positive/negative）"""
        if feedback not in ("positive", "negative"):
            return False
        conn = self._connect()
        conn.execute(
            "UPDATE consultations SET feedback = ? WHERE id = ?",
            (feedback, consultation_id),
        )
        conn.commit()
        conn.close()
        return True

    def get_total_predictions(self) -> dict:
        """获取全局预测统计"""
        conn = self._connect()
        total = conn.execute("SELECT COUNT(*) FROM consultations").fetchone()[0]
        with_feedback = conn.execute(
            "SELECT COUNT(*) FROM consultations WHERE feedback IS NOT NULL AND feedback != ''"
        ).fetchone()[0]
        conn.close()

        verified_pct = round((with_feedback / total) * 100, 1) if total > 0 else 0
        return {
            "total_predictions": total,
            "verified_count": with_feedback,
            "verified_pct": verified_pct,
        }

    def log_push(self, user_id: str, push_date: str, message: str, success: bool = True, error: str = ""):
        """记录推送日志"""
        conn = self._connect()
        conn.execute(
            "INSERT INTO push_log (user_id, push_date, message, success, error) VALUES (?,?,?,?,?)",
            (user_id, push_date, message, 1 if success else 0, error),
        )
        conn.commit()
        conn.close()

    def get_push_log(self, user_id: str, push_date: str) -> list:
        """获取指定日期的推送记录"""
        conn = self._connect()
        rows = conn.execute(
            "SELECT id, message, success, error, created_at FROM push_log WHERE user_id=? AND push_date=?",
            (user_id, push_date),
        ).fetchall()
        conn.close()
        return [
            {"id": r[0], "message": r[1], "success": bool(r[2]), "error": r[3], "created_at": r[4]}
            for r in rows
        ]
