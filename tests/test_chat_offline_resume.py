"""生成断点续传测试：退出/切走后生成不中断，下次进入自动补全。

覆盖（对应设计方案）：
- 迁移：offline_completed/consumed 列（新库/老库/幂等）；
- 落库标记：mark_offline_completed 只打最近一条 assistant 消息、
  按会话隔离、不重复标记；
- pending 查询：只返回 未消费 + 离线完成 的 assistant 回复（最新在前、解密）；
- consume 消费：按 created_at 截止时间消费，消费后不再返回、幂等；
- 接口契约：build_pending_response / consume_pending（main.py 端点薄调用）：
  GET 契约 {items:[{role,content,time,offline}]}，非法 session_id → 空；
- 流式断开：模拟客户端断开（生成器 aclose）→ 生成继续完成 → 落库 +
  offline_completed=1；在线完成 → offline_completed=0；会话隔离
  （A 会话断开不影响 B 会话的标记）。
"""
import asyncio
import os
import sqlite3
import tempfile
import time

import pytest

from src.storage.session_dao import SessionDAO
from src.storage.models import connect
from src.api.chat_stream import (
    ChatStreamer,
    build_pending_response,
    consume_pending,
)

USER = "u_offline_test"
SID_A = "s_offline_a1"
SID_B = "s_offline_b2"


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


def _cols(db_path):
    conn = connect(db_path)
    try:
        return [d[1] for d in conn.execute("PRAGMA table_info(sessions)")]
    finally:
        conn.close()


def _seed(dao, sid, text, role="assistant", offline=False):
    """写入一条消息（可选离线完成标记）。"""
    dao.add_message(USER, role, text, session_id=sid)
    if offline:
        dao.mark_offline_completed(USER, sid)


# ── 迁移 ─────────────────────────────────────────────────────────────

def test_migration_adds_offline_columns(db_path):
    """新库：sessions 表含 offline_completed/consumed 列 + 补全索引。"""
    dao = SessionDAO(db_path)  # 内部 init_db + ALTER 尝试
    cols = _cols(db_path)
    assert "offline_completed" in cols
    assert "consumed" in cols
    conn = connect(db_path)
    try:
        idx = {r[1] for r in conn.execute(
            "PRAGMA index_list(sessions)").fetchall()}
    finally:
        conn.close()
    assert "idx_sessions_offline" in idx


def test_migration_offline_columns_idempotent(db_path):
    """重复初始化（进程重启/多实例）不报错、不加重复列。"""
    SessionDAO(db_path)
    SessionDAO(db_path)
    SessionDAO(db_path)
    cols = _cols(db_path)
    assert cols.count("offline_completed") == 1
    assert cols.count("consumed") == 1


def test_migration_offline_legacy_db(db_path):
    """老库（无断点续传列）：init 后列补齐，旧行数据不丢且默认 0。"""
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
            session_id TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            id INTEGER PRIMARY KEY AUTOINCREMENT
        );
        INSERT INTO sessions (user_id, role, content) VALUES ('old_user', 'assistant', '旧回复');
        """
    )
    conn.commit()
    conn.close()
    SessionDAO(db_path)   # 触发迁移
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT content, offline_completed, consumed FROM sessions WHERE user_id='old_user'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and row[0] == "旧回复"
    assert row[1] == 0 and row[2] == 0  # 旧行默认未标记


# ── 落库标记 ─────────────────────────────────────────────────────────

def test_mark_offline_completed_marks_this_turn_only(db_path):
    """标记只打本轮生成期间新增的 assistant 消息（min_id 下界），user 不打。"""
    dao = SessionDAO(db_path)
    dao.add_message(USER, "user", "问题1", session_id=SID_A)
    dao.add_message(USER, "assistant", "回复1", session_id=SID_A)  # 上一轮（未标记）
    before = dao.get_max_message_id(USER, SID_A)                   # 本轮起点
    dao.add_message(USER, "user", "问题2", session_id=SID_A)
    dao.add_message(USER, "assistant", "回复2", session_id=SID_A)
    assert dao.mark_offline_completed(USER, SID_A, min_id=before) == 1
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT role, offline_completed FROM sessions"
            " WHERE user_id=? AND session_id=? ORDER BY id",
            (USER, SID_A)).fetchall()
    finally:
        conn.close()
    marks = [r[1] for r in rows]
    assert marks == [0, 0, 0, 1]      # 仅本轮（新增）的 assistant 被打标记
    hist = dao.get_history(USER, limit=20, session_id=SID_A)   # 解密后内容
    assert hist[-1]["content"] == "回复2"
    # 幂等：同一下界重复打 → 0（已标记的不再打，更旧的也不打）
    assert dao.mark_offline_completed(USER, SID_A, min_id=before) == 0
    # 上一轮的回复不被误标记（min_id=最新 id → 无本轮新增消息 → 0 行，
    # 即缓存命中/反馈等不落库分支不会误标上一轮未标记的回复）
    assert dao.mark_offline_completed(
        USER, SID_A, min_id=dao.get_max_message_id(USER, SID_A)) == 0


def test_mark_offline_completed_legacy_without_min_id(db_path):
    """min_id 缺省（None）→ 旧行为：按会话打最近一条未标记 assistant。"""
    dao = SessionDAO(db_path)
    _seed(dao, SID_A, "回复1")
    _seed(dao, SID_A, "回复2")
    assert dao.mark_offline_completed(USER, SID_A) == 1   # 打最近一条（回复2）
    assert dao.mark_offline_completed(USER, SID_A) == 1   # 再打下一条（回复1）
    conn = connect(db_path)
    try:
        marked = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id=? AND session_id=?"
            " AND offline_completed=1", (USER, SID_A)).fetchone()[0]
    finally:
        conn.close()
    assert marked == 2


def test_mark_offline_completed_session_isolation(db_path):
    """A 会话断开标记不影响 B 会话。"""
    dao = SessionDAO(db_path)
    _seed(dao, SID_A, "A回复")
    _seed(dao, SID_B, "B回复")
    assert dao.mark_offline_completed(USER, SID_A) == 1
    conn = connect(db_path)
    try:
        a = conn.execute(
            "SELECT offline_completed FROM sessions WHERE session_id=?",
            (SID_A,)).fetchone()[0]
        b = conn.execute(
            "SELECT offline_completed FROM sessions WHERE session_id=?",
            (SID_B,)).fetchone()[0]
    finally:
        conn.close()
    assert a == 1 and b == 0


def test_mark_offline_completed_no_match_noop(db_path):
    """会话中无 assistant 消息（缓存命中/反馈等不落库分支）→ 标记为 no-op。"""
    dao = SessionDAO(db_path)
    dao.add_message(USER, "user", "问题", session_id=SID_A)
    assert dao.mark_offline_completed(USER, SID_A) == 0


# ── pending 查询 ─────────────────────────────────────────────────────

def test_pending_returns_offline_unconsumed_newest_first(db_path):
    """pending 只返回 未消费+离线完成 的 assistant 回复，最新在前、内容解密。"""
    dao = SessionDAO(db_path)
    dao.add_message(USER, "user", "问题1", session_id=SID_A)
    dao.add_message(USER, "assistant", "在线回复", session_id=SID_A)     # 在线：不打标记
    _seed(dao, SID_A, "离线回复1", offline=True)
    _seed(dao, SID_A, "离线回复2", offline=True)   # 最新
    pending = dao.get_pending_offline_messages(USER, SID_A)
    assert [p["content"] for p in pending] == ["离线回复2", "离线回复1"]
    assert all(p["created_at"] for p in pending)
    assert pending[0]["id"] > pending[1]["id"]


def test_pending_excludes_consumed_and_online(db_path):
    """已消费的不再返回；未打离线标记的在线消息不返回。"""
    dao = SessionDAO(db_path)
    _seed(dao, SID_A, "离线待消费", offline=True)
    _seed(dao, SID_A, "离线已消费", offline=True)
    dao.consume_pending_offline(USER, SID_A, "")   # 全部消费
    assert dao.get_pending_offline_messages(USER, SID_A) == []
    _seed(dao, SID_A, "在线回复")                    # 在线完成不打标记
    pending = dao.get_pending_offline_messages(USER, SID_A)
    assert [p["content"] for p in pending] == []


def test_pending_session_isolation(db_path):
    """A 会话的 pending 不影响 B 会话。"""
    dao = SessionDAO(db_path)
    _seed(dao, SID_A, "A的离线回复", offline=True)
    _seed(dao, SID_B, "B的在线回复")
    assert [p["content"] for p in dao.get_pending_offline_messages(USER, SID_A)] == ["A的离线回复"]
    assert dao.get_pending_offline_messages(USER, SID_B) == []


def test_pending_other_user_invisible(db_path):
    """其他用户的离线回复对当前用户不可见（鉴权层 + 查询层双保险）。"""
    dao = SessionDAO(db_path)
    _seed(dao, SID_A, "别人家的回复", offline=True)   # 用同一 USER 简化，见下
    # 换一个 user_id 查询（越权形状）→ 空
    assert dao.get_pending_offline_messages("u_other", SID_A) == []


# ── consume ──────────────────────────────────────────────────────────

def test_consume_by_time_cutoff(db_path):
    """按 created_at 截止时间消费；截止后不再返回；幂等。

    created_at 为 SQLite datetime('now') 秒级精度——两条消息构造出不同的
    时间戳（早/晚），验证截止消费只消费截止时间之前（含）的离线回复。
    """
    dao = SessionDAO(db_path)
    dao.add_message(USER, "assistant", "早的离线", session_id=SID_A)
    dao.mark_offline_completed(USER, SID_A)
    dao.add_message(USER, "assistant", "晚的离线", session_id=SID_A)
    dao.mark_offline_completed(USER, SID_A)
    # 显式拉开时间戳（created_at 秒级精度，同秒插入会撞时间）：
    # 最早一条 = 早的离线（10:00），另一条 = 晚的离线（10:05）
    conn = connect(db_path)
    try:
        first_id = conn.execute(
            "SELECT MIN(id) FROM sessions WHERE session_id=?", (SID_A,)
        ).fetchone()[0]
        conn.execute(
            "UPDATE sessions SET created_at='2026-08-19 10:00:00' WHERE id=?",
            (first_id,))
        conn.execute(
            "UPDATE sessions SET created_at='2026-08-19 10:05:00'"
            " WHERE session_id=? AND id!=?", (SID_A, first_id))
        conn.commit()
    finally:
        conn.close()
    pending = dao.get_pending_offline_messages(USER, SID_A)
    assert len(pending) == 2
    assert [p["content"] for p in pending] == ["晚的离线", "早的离线"]
    # 只消费到 10:01 → 早的离线被消费，晚的离线仍在
    assert dao.consume_pending_offline(USER, SID_A, "2026-08-19 10:01:00") == 1
    remaining = dao.get_pending_offline_messages(USER, SID_A)
    assert [p["content"] for p in remaining] == ["晚的离线"]
    # 幂等：重复消费不报错
    dao.consume_pending_offline(USER, SID_A, "2026-08-19 10:01:00")
    assert dao.consume_pending_offline(USER, SID_A, "") == 1  # 消费晚的
    assert dao.get_pending_offline_messages(USER, SID_A) == []


def test_consume_scoped_to_session_and_user(db_path):
    """消费只影响本用户本会话，不影响其他会话/其他用户。"""
    dao = SessionDAO(db_path)
    _seed(dao, SID_A, "A离线", offline=True)
    _seed(dao, SID_B, "B离线", offline=True)
    dao.consume_pending_offline(USER, SID_A, "")
    assert dao.get_pending_offline_messages(USER, SID_A) == []
    assert len(dao.get_pending_offline_messages(USER, SID_B)) == 1
    dao.consume_pending_offline("u_other", SID_A, "")   # 别人消费不了我的
    assert len(dao.get_pending_offline_messages(USER, SID_A)) == 0  # 本就被上面消费了
    dao.consume_pending_offline(USER, SID_B, "")
    assert dao.get_pending_offline_messages(USER, SID_B) == []


# ── 接口契约（main.py 端点薄调用同一函数） ──────────────────────────

def test_build_pending_response_contract(db_path):
    """GET /api/chat/pending 契约：{items:[{role,content,time,offline}]}。"""
    dao = SessionDAO(db_path)
    _seed(dao, SID_A, "离线补全", offline=True)
    res = build_pending_response(dao, USER, SID_A)
    assert len(res["items"]) == 1
    it = res["items"][0]
    assert it["role"] == "assistant"
    assert it["content"] == "离线补全"
    assert it["time"]
    assert it["offline"] is True


def test_build_pending_response_invalid_session(db_path):
    """非法/空 session_id → 空 items（不 4xx，不落入存储）。"""
    dao = SessionDAO(db_path)
    _seed(dao, SID_A, "离线补全", offline=True)
    assert build_pending_response(dao, USER, "") == {"items": []}
    assert build_pending_response(dao, USER, "bad id;DROP") == {"items": []}
    assert build_pending_response(dao, USER, "short") == {"items": []}
    # 合法 session 但另一个用户 → 空（鉴权层已保证 uid=JWT sub，此处双保险）
    assert build_pending_response(dao, "u_other", SID_A) == {"items": []}


def test_consume_pending_contract(db_path):
    """POST /api/chat/pending/consume 契约：消费后 pending 不再返回。"""
    dao = SessionDAO(db_path)
    _seed(dao, SID_A, "离线补全", offline=True)
    res = consume_pending(dao, USER, SID_A, "2099-01-01 00:00:00")
    assert res == {"ok": True}
    assert build_pending_response(dao, USER, SID_A) == {"items": []}
    # 非法 session → ok:false；无 dao（服务未就绪）→ ok:true 不报错
    assert consume_pending(dao, USER, "bad;DROP", "2099-01-01 00:00:00") == {"ok": False}
    assert consume_pending(None, USER, SID_A, "2099-01-01 00:00:00") == {"ok": True}


# ── 流式断开（核心场景：退出后继续生成 + 落库 + 打标记） ─────────────

class _MockMemberDAO:
    def check_quota(self, uid):
        return True

    def use_quota(self, uid):
        pass

    def get_membership(self, uid):
        return {"plan": "free"}


class _MockHandler:
    """模拟真实 handler.process：生成期间落库（用户+助手消息）后返回回复。"""

    def __init__(self, session_dao, sid):
        self.session_dao = session_dao
        self.sid = sid

    def pop_citations(self, uid):
        return []

    def gen_suggestions(self, uid, q, r):
        return []

    def process(self, message, user_id, stream_cb=None, deep_night=False,
                session_id=None, downgraded=False):
        time.sleep(0.6)  # 模拟慢生成（客户端在生成中退出）
        self.session_dao.add_message(user_id, "user", message,
                                     session_id=session_id)
        self.session_dao.add_message(user_id, "assistant", "后台完成的回复",
                                     session_id=session_id)
        return "后台完成的回复"


class _Req:
    def __init__(self, sid):
        self.message = "你好"
        self.message_type = "text"
        self.voice_text = ""
        self.image_url = ""
        self.deep_night = False
        self.session_id = sid


async def _run_stream(st, req, auth, disconnect_after_start: bool):
    """驱动 events() 生成器；disconnect_after_start=True 时在生成中 aclose。"""
    gen = st.events(req, None, auth)
    it = gen.__aiter__()
    evts = [await it.__anext__()]  # start
    if disconnect_after_start:
        # 继续推进生成器：executor 任务提交 + 进入事件循环（首个 ping 出现），
        # 此刻生成已在后台线程跑 → 客户端断开（StreamingResponse 对生成器 aclose）
        evts.append(await asyncio.wait_for(it.__anext__(), timeout=5))
        await gen.aclose()
        # 事件循环继续运行：断点续传 watcher 等后台生成完成后补打离线标记
        # （生产环境循环常驻；此处显式续跑让 watcher 完成）
        await asyncio.sleep(1.5)
        return evts
    while True:
        try:
            evts.append(await asyncio.wait_for(it.__anext__(), timeout=10))
        except StopAsyncIteration:
            break
    return evts


def _make_streamer(db_path, sid):
    dao = SessionDAO(db_path)
    handler = _MockHandler(dao, sid)
    st = ChatStreamer(handler=handler, member_dao=_MockMemberDAO(), dao=None,
                      ping_interval=0.3, simulation_delay=0.01)
    return st, dao


def test_disconnect_mid_generation_completes_and_marks_offline(db_path):
    """核心：客户端在生成中退出 → 生成继续完成 → 落库且 offline_completed=1。"""
    st, dao = _make_streamer(db_path, SID_A)
    evts = asyncio.run(_run_stream(st, _Req(SID_A),
                                   {"method": "jwt", "user_id": USER}, True))
    assert evts[0]["type"] == "start"           # 断开前只收到 start（生成中）
    time.sleep(1.2)                              # 等 executor 线程跑完
    hist = dao.get_history(USER, limit=20, session_id=SID_A)
    assert len(hist) == 2                        # user + assistant 均已落库
    assert hist[-1]["role"] == "assistant"
    conn = connect(db_path)
    try:
        marks = conn.execute(
            "SELECT role, offline_completed FROM sessions"
            " WHERE user_id=? AND session_id=? ORDER BY id",
            (USER, SID_A)).fetchall()
    finally:
        conn.close()
    assert [(r[0], r[1]) for r in marks] == [("user", 0), ("assistant", 1)]
    # 补全接口可见 → 消费后不再返回
    assert build_pending_response(dao, USER, SID_A)["items"][0]["content"] == "后台完成的回复"
    assert consume_pending(dao, USER, SID_A,
                           build_pending_response(dao, USER, SID_A)["items"][0]["time"]) == {"ok": True}
    assert build_pending_response(dao, USER, SID_A) == {"items": []}


def test_online_completion_not_marked_offline(db_path):
    """在线完成：offline_completed=0，pending 不返回（普通路径不受影响）。"""
    st, dao = _make_streamer(db_path, SID_A)
    evts = asyncio.run(_run_stream(st, _Req(SID_A),
                                   {"method": "jwt", "user_id": USER}, False))
    types = [e["type"] for e in evts]
    assert "done" in types                        # 完整走到 done
    conn = connect(db_path)
    try:
        marks = conn.execute(
            "SELECT offline_completed FROM sessions"
            " WHERE user_id=? AND session_id=?",
            (USER, SID_A)).fetchall()
    finally:
        conn.close()
    assert marks == [(0,), (0,)]                  # user + assistant 均未标记
    assert build_pending_response(dao, USER, SID_A) == {"items": []}


def test_disconnect_session_isolation_via_stream(db_path):
    """A 会话断开发标记，B 会话（在线完成）不受影响。"""
    st_a, dao = _make_streamer(db_path, SID_A)
    st_b, dao_b = _make_streamer(db_path, SID_B)
    assert dao_b.db_path == dao.db_path  # 同一库（实例不同）
    asyncio.run(_run_stream(st_a, _Req(SID_A),
                            {"method": "jwt", "user_id": USER}, True))
    asyncio.run(_run_stream(st_b, _Req(SID_B),
                            {"method": "jwt", "user_id": USER}, False))
    time.sleep(1.2)
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT session_id, role, offline_completed FROM sessions"
            " WHERE user_id=? ORDER BY session_id, id",
            (USER,)).fetchall()
    finally:
        conn.close()
    marks = {sid: [r[2] for r in rows if r[0] == sid and r[1] == "assistant"]
             for sid in (SID_A, SID_B)}
    assert marks[SID_A] == [1]     # 断开会话：已标记离线完成
    assert marks[SID_B] == [0]     # 在线会话：未标记
    assert build_pending_response(dao, USER, SID_A)["items"]
    assert build_pending_response(dao, USER, SID_B) == {"items": []}
