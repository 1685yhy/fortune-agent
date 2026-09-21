"""匿名对话分享存储。表 share_entries: 分享落地页数据。

- id: 8 位 base62 短随机串(主键),由调用方生成、不泄露用户身份;
- content: JSON 字符串 {"pairs": [{u, tag, content}...], "dateText": str};
- created_at: 创建时间(real epoch)。
- expires_at: **失效时刻**(real epoch; k76 新增)。控制方 2026-09-21 拍板：
  分享链接**设有效期**（默认 30 天,`FORTUNE_SHARE_TTL_DAYS` 可配,见
  `src/config.py::share_ttl_days` 单一事实源）,**到期后链接失效**——过期行
  读不出来(get/get_state 一律按过期处理),并由 purge_expired() 物理清理。
  存量行(建列时已存在)按 `created_at + TTL` **回算**(见 _migrate)。
- owner_tag: 归属标记(**不是** user_id)。取
  `DataEncryptor.encrypt_user_id()` 的确定性 HMAC 值——足够回答"这条分享
  属于哪个账号"(注销时据此删除),又不可反推出用户身份,维持本表
  "不落任何明文用户标识"的既有红线。未登录时的匿名分享为空串
  (无归属可删,仍受 TTL 约束)。
"""
import json
import sqlite3
import time


def owner_tag_for(user_id: str) -> str:
    """user_id → 分享归属标记（确定性 HMAC 伪名；**单一事实源**）。

    - 复用既有 `DataEncryptor.encrypt_user_id`（HMAC-SHA256 截断）——**不新增
      密钥、不新增算法**；同一 user_id 恒得同一标记，故注销时能按标记删干净，
      而标记本身不可反推用户身份（本表"不落明文用户标识"的红线不变）。
    - user_id 为空 / 加密不可用（未配置密钥等）→ `""`（= 不记归属）。宁可少删
      该账号的匿名分享（仍受 TTL 到期约束），也不把明文 user_id 落进本表。
    """
    uid = (user_id or "").strip()
    if not uid:
        return ""
    try:
        from src.security.encryption import DataEncryptor
        return DataEncryptor().encrypt_user_id(uid) or ""
    except Exception:
        return ""


class ShareDAO:
    def __init__(self, conn):
        self.conn = conn
        self.conn.execute("""CREATE TABLE IF NOT EXISTS share_entries (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            created_at REAL,
            expires_at REAL,
            owner_tag TEXT DEFAULT ''
        )""")
        self._migrate()
        self.conn.commit()

    # ── 迁移(老库:建表语句里没有 expires_at / owner_tag) ──────────────

    def _migrate(self):
        """给老库补列 + 存量行按创建时间回算 expires_at(**幂等**)。

        存量行的处置依据(控制方拍板原文)："到期后链接失效(**已有链接也会失效**)"
        —— 所以存量行按 `created_at + TTL` 回算,**不刷新**:
          * 已经存在超过 TTL 的分享 → 立刻失效(这正是"已有链接也会失效"的字面要求);
          * 未到期的分享 → 从创建时刻起算,保留剩余时间。
        回算只在列为 NULL 时执行一次(`expires_at IS NULL`),新写入行一律带值,
        因此本分支在正常运行的库上第二次启动即为空操作。
        """
        cols = {r[1] for r in self.conn.execute(
            "PRAGMA table_info(share_entries)").fetchall()}
        if "expires_at" not in cols:
            self.conn.execute("ALTER TABLE share_entries ADD COLUMN expires_at REAL")
        if "owner_tag" not in cols:
            self.conn.execute(
                "ALTER TABLE share_entries ADD COLUMN owner_tag TEXT DEFAULT ''")
        # 存量行回算:created_at 缺失(NULL)的行按"现在"起算(不因缺列而永久有效)
        from src.config import share_ttl_seconds
        ttl = share_ttl_seconds()
        self.conn.execute(
            "UPDATE share_entries SET expires_at = COALESCE(created_at, ?) + ? "
            "WHERE expires_at IS NULL",
            (time.time(), ttl))

    # ── 写入 ─────────────────────────────────────────────────────────

    def insert(self, share_id: str, content: dict, owner_tag: str = "",
               ttl_seconds: float = None) -> bool:
        """写入一条匿名分享。返回是否成功:
        - 主键冲突(小概率)或异常 → False,由调用方换新 id 重试。

        ttl_seconds 缺省取 `share_ttl_seconds()`(默认 30 天)。
        owner_tag 缺省空串 = 无归属(未登录的匿名分享)。
        """
        if ttl_seconds is None:
            from src.config import share_ttl_seconds
            ttl_seconds = share_ttl_seconds()
        now = time.time()
        try:
            self.conn.execute(
                "INSERT INTO share_entries (id, content, created_at, expires_at, owner_tag) "
                "VALUES (?,?,?,?,?)",
                (share_id, json.dumps(content, ensure_ascii=False), now,
                 now + float(ttl_seconds), owner_tag or ""))
            self.conn.commit()
            # 顺手清理已过期行(有界、幂等;失败不影响写入结果)
            try:
                self.purge_expired(now)
            except Exception:
                pass
            return True
        except sqlite3.IntegrityError:
            return False
        except Exception:
            return False

    # ── 读取 ─────────────────────────────────────────────────────────

    def get(self, share_id: str):
        """按 id 读取内容 dict;不存在、**已过期**或损坏返回 None。"""
        return self.get_state(share_id)[1]

    def get_state(self, share_id: str, now: float = None):
        """按 id 读取 → `(state, entry)`。

        state ∈ {"ok", "expired", "missing"}:
          - "ok"      → entry 为内容 dict,可展示;
          - "expired" → 行存在但已过 expires_at(**一律不再返回内容**,fail-closed);
          - "missing" → 无此行 / 内容损坏 / expires_at 缺失(老行未回算的兜底)。
        调用方据此区分状态码与文案(过期 ≠ 不存在)。
        """
        now = time.time() if now is None else now
        row = self.conn.execute(
            "SELECT content, expires_at FROM share_entries WHERE id=?",
            (share_id,)).fetchone()
        if not row:
            return "missing", None
        content, expires_at = row[0], row[1]
        if expires_at is None or float(expires_at) <= float(now):
            # expires_at 为 NULL = 无法判定有效期 → 按过期处理(绝不退回"永久有效")
            return "expired", None
        try:
            return "ok", json.loads(content)
        except (ValueError, TypeError):
            return "missing", None

    # ── 清理 / 注销删除 ──────────────────────────────────────────────

    def purge_expired(self, now: float = None) -> int:
        """物理删除已过期行,返回删除行数(存储卫生:过期内容不留库)。"""
        now = time.time() if now is None else now
        cur = self.conn.execute(
            "DELETE FROM share_entries WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (now,))
        self.conn.commit()
        return cur.rowcount or 0

    def delete_by_owner(self, owner_tag: str) -> int:
        """删除某账号的全部分享(注销用),返回删除行数。

        owner_tag 为空 → 返回 0(**不做全表删除**:空标记对不上任何账号,
        若误删全表会连带删掉别人的分享)。
        """
        if not owner_tag:
            return 0
        cur = self.conn.execute(
            "DELETE FROM share_entries WHERE owner_tag=?", (owner_tag,))
        self.conn.commit()
        return cur.rowcount or 0
