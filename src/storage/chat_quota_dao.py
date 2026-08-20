"""对话日额度（chat_quota）— 免费用户每日对话条数限制（降级链路，L5-1）。

参考 ming_quota 先例：PRIMARY KEY (user_id, day)，按自然日轮转，无定时任务。

规则（判定/消费逻辑在 src/services/chat_quota.py）：
- 免费用户每日 15 条对话（CHAT_DAILY_LIMIT）；前 15 条正常，第 16 条起走
  降级链路（GLM-4-Flash 精简回复，downgraded 标记），不 429 硬断。
- 会员（plan != free）/体验模式：不计数（API 层判定后跳过，本表无记录）。
- 消费点：/api/chat 与 /api/chat/stream 请求进入时（鉴权后、LLM 调用前）。

与 member_dao.queries_used/queries_limit（命理查询次数，免费 3 次/日）
是两个独立维度：对话额度按天自然轮转、独立计数，互不干扰。
"""
from src.storage.models import connect as db_connect


class ChatQuotaDAO:
    """对话日额度数据访问层。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        conn = db_connect(db_path)
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS chat_quota (
                user_id TEXT NOT NULL,
                day TEXT NOT NULL,
                cnt INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, day)
            )""")
            conn.commit()
        finally:
            conn.close()

    def _connect(self):
        return db_connect(self.db_path, timeout=10)

    def get_count(self, user_id: str, day: str) -> int:
        """当日已用对话条数（无记录 = 0）。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT cnt FROM chat_quota WHERE user_id=? AND day=?",
                (user_id, day)).fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def consume(self, user_id: str, day: str, limit: int) -> int:
        """消费一次对话额度。返回剩余次数（>=0 表示放行并已 +1；<0 表示超限，不计数）。

        语义与 ming_dao.consume_quota 一致。并发下两次请求同时看到 cnt=limit-1
        时可能双双 +1（cnt 略超 limit），可接受——超限请求降级而非拒绝。
        """
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT cnt FROM chat_quota WHERE user_id=? AND day=?",
                (user_id, day)).fetchone()
            cnt = row[0] if row else 0
            if cnt >= limit:
                return -1
            if row:
                conn.execute(
                    "UPDATE chat_quota SET cnt=cnt+1 WHERE user_id=? AND day=?",
                    (user_id, day))
            else:
                conn.execute(
                    "INSERT INTO chat_quota (user_id, day, cnt) VALUES (?,?,1)",
                    (user_id, day))
            conn.commit()
            return limit - cnt - 1
        finally:
            conn.close()
