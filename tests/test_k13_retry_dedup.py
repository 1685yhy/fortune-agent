"""k13 重试重复消息根治（2026-09-09，用户实锤：点「重试」同一句用户消息
存 4 份 user 行 #29/#31/#33/#35，节奏 2-5 分钟/次；#35 失败轮无 assistant）。

护栏（plan docs/superpowers/plans/2026-09-09-k13-retry-dedup.md §B2）：
- regen=True（前端重试/重新生成同轮标记）：同会话存在规范化同文 user 行 →
  不新插（assistant 由正常生成链补上）；无匹配行（缓存轮等从未落库）→ 照插。
- regen=False：同会话最近规范化同文 user 行距其 created_at ≤ 窗口（默认 20s）
  → 判定重试/双击重复 → 不新插；跨会话/异文/超窗同文（真重复提问）→ 照插。
- 规范化：strip 首尾空白（含全角空格/换行），内部空白不折叠。
- 判定+插入同一 BEGIN IMMEDIATE 事务（并发双提交只落一行）。

覆盖（对应任务书 D）：
① 同会话同内容 10s 内第二次 user 提交 → 不新增行 + 补答（生成链）后恰一轮 [u,a]
② 超窗（30s）/异文 → 正常新增
③ 跨会话同内容 → 不拦
④ 规范化（全角空格/尾随换行）判定同文；内部半角空格不归并
⑤ regen 标记：超窗匹配也拦；无匹配照插
⑥ 并发双提交（双线程真并发）只落一行
⑦ handler 级 _persist_user_turn：regen 语义透传 + 异常退回原 add_message（宁重勿丢）

运行：/home/a/fortune-agent/.venv/bin/python -m pytest tests/test_k13_retry_dedup.py -q
"""
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

from unittest.mock import Mock  # noqa: E402

import pytest  # noqa: E402

from src.storage.session_dao import RETRY_DEDUP_WINDOW_SECONDS  # noqa: E402
from src.storage.session_dao import SessionDAO  # noqa: E402
from src.storage.session_dao import _decrypt_or_plain  # noqa: E402
from src.storage.models import connect  # noqa: E402

USER = "u_k13"
SID = "s_k13_1"
SID_OTHER = "s_k13_2"
MSG = "帮我看看我的八字"


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


def _rows(dao, session_id=SID, role="user"):
    """按 id 正序列出某会话某 role 的行（含 created_at）。"""
    out = []
    conn = connect(dao.db_path)
    try:
        rows = conn.execute(
            """SELECT id, content, created_at FROM sessions
               WHERE user_id = ? AND role = ? AND (? IS NULL OR session_id = ?)
               ORDER BY id ASC""",
            (USER, role, session_id, session_id),
        ).fetchall()
    finally:
        conn.close()
    for r in rows:
        out.append({"id": r[0], "content": r[1], "created_at": r[2]})
    return out


def _user_row_ids(dao, session_id=SID):
    return [r["id"] for r in _rows(dao, session_id)]


def _backdate_row(dao, row_id, seconds):
    """把行的 created_at 回拨（窗口测试免 sleep：SQLite datetime('now') UTC 口径）。"""
    conn = connect(dao.db_path)
    try:
        conn.execute(
            "UPDATE sessions SET created_at = datetime('now', ?) WHERE id = ?",
            (f"-{int(seconds)} seconds", row_id))
        conn.commit()
    finally:
        conn.close()


def _submit(dao, text, session_id=SID, regen=False):
    """模拟 handler 用户消息落库调用（process 内唯一 user 落库点的护栏方法）。"""
    return dao.add_user_message_dedup(USER, text, session_id=session_id,
                                      regen=regen)


# ── ① 窗口内同文第二次提交：不新增行；补答后恰一轮 ─────────────────────────

def test_within_window_second_submit_deduped_then_answer_completes_round(db_path):
    dao = SessionDAO(db_path)
    r1 = _submit(dao, MSG)
    assert r1 == {"inserted": True, "matched_id": None}
    # 10s 内第二次提交（真实重试节奏）→ 不新增行
    r2 = _submit(dao, MSG)
    assert r2["inserted"] is False
    assert r2["matched_id"] is not None
    assert len(_user_row_ids(dao)) == 1, "窗口内同文第二次提交不得新增 user 行"
    # 补答触发（正常生成链对既有轮次落 assistant）→ 恰一轮 [u, a]
    dao.add_message(USER, "assistant", "好的，我来帮你分析。", session_id=SID)
    assert len(_user_row_ids(dao)) == 1
    assert len(_rows(dao, SID, role="assistant")) == 1, "补答后该轮恰一条 assistant"


def test_within_window_matched_id_points_to_original_row(db_path):
    dao = SessionDAO(db_path)
    dao.add_message(USER, "user", "另一句不相干的内容", session_id=SID)
    _submit(dao, MSG)
    r2 = _submit(dao, MSG)
    assert r2["matched_id"] is not None
    # matched_id 应指向同文原行（id 为第 2 行，非第 1 行异文行）
    ids = _user_row_ids(dao)
    assert r2["matched_id"] == ids[1]
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT content FROM sessions WHERE id = ?", (r2["matched_id"],)
        ).fetchone()
    finally:
        conn.close()
    assert _decrypt_or_plain(row[0]) == MSG, "匹配行内容 == 同文（密文比对在解密后）"


# ── ② 超窗同文 / 异文 → 正常新增 ──────────────────────────────────────────

def test_over_window_same_content_is_real_repeat_not_blocked(db_path):
    dao = SessionDAO(db_path)
    _submit(dao, MSG)
    _backdate_row(dao, _user_row_ids(dao)[0], 30)   # 30s > 默认 20s 窗口
    r2 = _submit(dao, MSG)
    assert r2["inserted"] is True, "超窗同文 = 用户真重复提问，不拦"
    assert len(_user_row_ids(dao)) == 2


def test_different_content_always_inserts(db_path):
    dao = SessionDAO(db_path)
    _submit(dao, MSG)
    r2 = _submit(dao, MSG + "，换个角度")
    assert r2["inserted"] is True
    assert len(_user_row_ids(dao)) == 2


# ── ③ 跨会话同内容 → 不拦 ─────────────────────────────────────────────────

def test_cross_session_same_content_not_blocked(db_path):
    dao = SessionDAO(db_path)
    _submit(dao, MSG, session_id=SID)
    r2 = _submit(dao, MSG, session_id=SID_OTHER)
    assert r2["inserted"] is True, "跨会话同文（新会话重问）不拦"
    assert len(_user_row_ids(dao, SID_OTHER)) == 1


def test_none_session_scope_independent_from_named_session(db_path):
    """session_id=None（旧行为作用域）与具名会话互不拦截。"""
    dao = SessionDAO(db_path)
    _submit(dao, MSG, session_id=None)
    r2 = _submit(dao, MSG, session_id=SID)
    assert r2["inserted"] is True


# ── ④ 规范化：全角空格/尾随换行/首尾空白 判同文；内部空白不归并 ──────────

def test_normalize_edges_fullwidth_space_and_newline(db_path):
    dao = SessionDAO(db_path)
    _submit(dao, MSG)
    for variant in [MSG + "\n", "  " + MSG, MSG + "　", "　" + MSG,
                    MSG + "\r\n"]:
        r = _submit(dao, variant)
        assert r["inserted"] is False, f"变体 {variant!r} 应与 {MSG!r} 判同文（重试）"
    assert len(_user_row_ids(dao)) == 1


def test_normalize_inner_space_not_collapsed(db_path):
    dao = SessionDAO(db_path)
    _submit(dao, MSG)
    r = _submit(dao, MSG.replace("我的", "我的 ", 1))
    assert r["inserted"] is True, "内部插入空格为异文，不得归并误拦"


def test_normalize_pure_function():
    assert SessionDAO.normalize_dedup_text("  a　b\n") == "a　b"
    assert SessionDAO.normalize_dedup_text("a b") == "a b"
    assert SessionDAO.normalize_dedup_text("") == ""
    assert SessionDAO.normalize_dedup_text(None) == ""


# ── ⑤ regen 标记：超窗匹配也拦；无匹配照插 ────────────────────────────────

def test_regen_blocks_over_window_match(db_path):
    """前端重试（带 regen）节奏可远超 20s（实测 2-5 分钟/次）→ 标记路径无窗口限制。"""
    dao = SessionDAO(db_path)
    _submit(dao, MSG)
    _backdate_row(dao, _user_row_ids(dao)[0], 300)   # 5 分钟前
    r = _submit(dao, MSG, regen=True)
    assert r["inserted"] is False, "regen 标记 + 同会话同文（无论多旧）→ 不新插 user 行"
    assert len(_user_row_ids(dao)) == 1


def test_regen_without_match_inserts(db_path):
    """缓存命中轮从未落库（无 user 行）→ regen 照插，保审计完整。"""
    dao = SessionDAO(db_path)
    dao.add_message(USER, "user", "其他内容", session_id=SID)
    r = _submit(dao, MSG, regen=True)
    assert r["inserted"] is True
    assert len(_user_row_ids(dao)) == 2


def test_regen_match_beyond_lookback_depth_inserts(db_path):
    """review Minor-1 限定口径锁定：regen 只回看最近 _DEDUP_LOOKBACK(10) 条
    同作用域 user 行——10 行之前更早的失败轮重试查不到匹配 → 照插（不误伤
    超深历史，审计完整）。"""
    dao = SessionDAO(db_path)
    _submit(dao, MSG)                    # 最旧同文轮（第 11 行）
    for i in range(10):                  # 其后 10 行其他内容
        _submit(dao, f"其他问题第 {i} 条")
    assert len(_user_row_ids(dao)) == 11
    # 回看深度 = 10 → MSG 行（第 11 行）不在最近 10 条内 → regen 照插
    r = _submit(dao, MSG, regen=True)
    assert r["inserted"] is True, "超回看深度的更早轮次 regen 查不到匹配 → 照插"
    assert len(_user_row_ids(dao)) == 12


def test_regen_and_dedup_path_encrypt_content(db_path):
    dao = SessionDAO(db_path)
    _submit(dao, MSG)
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT content FROM sessions WHERE user_id=? AND role='user'",
            (USER,)).fetchone()
    finally:
        conn.close()
    assert _decrypt_or_plain(row[0]) == MSG, "去重插入路径加密落库不变"


# ── ⑥ 并发双提交只落一行（BEGIN IMMEDIATE 原子性真并发打点） ──────────────

def test_concurrent_double_submit_single_row(db_path):
    dao = SessionDAO(db_path)
    results = []
    barrier = threading.Barrier(2)

    def worker():
        dao_i = SessionDAO(db_path)  # 独立连接（模拟双请求/双进程并发）
        barrier.wait()
        results.append(dao_i.add_user_message_dedup(USER, MSG, session_id=SID))

    ts = [threading.Thread(target=worker) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in ts), "并发线程应已结束"
    inserted = [r for r in results if r["inserted"]]
    assert len(inserted) == 1, f"并发双提交应恰 1 行插入，实际 {len(inserted)}"
    assert len(_user_row_ids(dao)) == 1, "并发双提交后 user 行数必须为 1"


# ── ⑦ handler 级 _persist_user_turn：regen 透传 + 异常退回 add_message ────

def _stub_handler(dao):
    """object.__new__ 手工装配（test_chat_entry_fixes 同风格）。"""
    from src.bot.handler import MessageHandler
    h = object.__new__(MessageHandler)
    h.session_dao = dao
    h.llm = Mock(model="test-model")
    h._safety_flag = Mock(return_value=None)
    return h


def test_persist_user_turn_regen_dedup(db_path):
    dao = SessionDAO(db_path)
    h = _stub_handler(dao)
    analysis = Mock(intent="free_chat", emotion_label="neutral")
    h._persist_user_turn(USER, MSG, analysis, False, SID, regen=False)
    assert len(_user_row_ids(dao)) == 1
    # 同轮重试（regen=True）→ 不新插
    h._persist_user_turn(USER, MSG, analysis, False, SID, regen=True)
    assert len(_user_row_ids(dao)) == 1
    # 超窗后仍 regen → 不新插（标记路径无窗口限制）
    _backdate_row(dao, _user_row_ids(dao)[0], 120)
    h._persist_user_turn(USER, MSG, analysis, False, SID, regen=True)
    assert len(_user_row_ids(dao)) == 1


def test_persist_user_turn_plain_send_window_dedup(db_path):
    dao = SessionDAO(db_path)
    h = _stub_handler(dao)
    analysis = Mock(intent="free_chat", emotion_label="neutral")
    h._persist_user_turn(USER, MSG, analysis, False, SID, regen=False)
    h._persist_user_turn(USER, MSG + "\n", analysis, False, SID, regen=False)
    assert len(_user_row_ids(dao)) == 1, "无标记同文窗口内 → 判重试去重（旧客户端兜底）"


def test_persist_user_turn_fallback_plain_insert_on_guard_error(db_path):
    """护栏异常（锁超时等）→ 退回原 add_message 直插：宁重勿丢铁律。"""
    dao = SessionDAO(db_path)
    h = _stub_handler(dao)
    analysis = Mock(intent="free_chat", emotion_label="neutral")
    orig = dao.add_user_message_dedup
    try:
        dao.add_user_message_dedup = Mock(side_effect=RuntimeError("locked"))
        h._persist_user_turn(USER, MSG, analysis, False, SID, regen=False)
    finally:
        dao.add_user_message_dedup = orig
    assert len(_user_row_ids(dao)) == 1, "护栏异常 → 退回直插，用户消息绝不丢"


def test_window_constant_is_20s(db_path):
    """窗口默认值锁定（plan §B3 依据）。"""
    assert RETRY_DEDUP_WINDOW_SECONDS == 20
