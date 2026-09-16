# -*- coding: utf-8 -*-
"""k49 C（Important-3）：紫微路径待确认值按 (user, session) 键控。

现象（四维验收实测，证据 `/tmp/k48verify-main/evidence/ziwei_scope.json`
`{"leaked": true, "persons": [1995, 3, 8, "长春"]}`）：
会话 A 走 `_handle_ziwei` 冲突 → 问句，pending 存于键 `('u1','')`
（`_handle_ziwei` 无 session_id 形参）；随后**另一会话**发「确认」→ 经
`_peek_pending_birth` 回落 `(user,'')` **承接生效**（档案被改成 1995-03-08
并出盘）。而 `_pending_birth_key` 注释自述"防跨用户/跨会话串味"——跨会话
这半没做到（对照：纯 bazi 路径跨会话**不**承接，已验 PASS）。

期望：紫微路径 pending 同样按 (user, session) 键控（对齐 k48-r2 给
bazi/career/dream 三意图线程化 session_id 的做法）；`_peek_pending_birth`
在有 session 时**不得**回落 `(user,'')`。同会话仍承接、跨用户仍不承接。

隔离：tmp_path 真实 SQLite + 真实 BaziEngine（紫微引擎以 stub 顶替）；零 LLM。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

from unittest.mock import Mock  # noqa: E402

from src.bot.handler import MessageHandler  # noqa: E402

ARCHIVE = dict(birth_year=1999, birth_month=3, birth_day=28, birth_hour=10,
               birth_minute=55, city="长春", gender="男")
# 紫微冲突问句的触发形态（验收脚本同源）
ZIWEI_CONFLICT_MSG = "帮我排紫微，1995年3月8日10点北京女"


def _mk_archive(db_path, user_id="u1", **over):
    from src.storage.person_dao import PersonDAO
    b = {"gender": "男", "birth_year": 1999, "birth_month": 3, "birth_day": 28,
         "birth_hour": 10, "birth_minute": 55, "calendar": "solar",
         "city": "长春"}
    b.update(over)
    return PersonDAO(db_path).create_person(
        user_id, name="我", relation="自己", is_default=True, birth=b)


def _h(tmp_path, users=("u1",)):
    from src.engines.bazi import BaziEngine
    from src.storage.dao import UserDAO
    db = str(tmp_path / "p.db")
    h = object.__new__(MessageHandler)
    h.engine = BaziEngine()
    h.ziwei_engine = Mock()
    h.llm = Mock()
    h.llm.api_key = ""
    h.dao = UserDAO(db)
    h.session_dao = None
    h.retriever = Mock()
    h.retriever.search.return_value = []
    h.memory = None
    h.memory_system = None
    h.chart_dao = None
    h.member_dao = None
    h._downgraded = {u: True for u in users}
    h._deep_night = {}
    h._analysis_facts = {}
    h.tool_logs = {}
    h._citations = {}
    h._fact_ctx = {}
    h._pregen_instant = {}
    h._consume_pregen_instant = Mock(return_value="")
    h._gen_info_collection_prompt = Mock(return_value="渐进引导")
    h._gen_reuse_acknowledgment = Mock(return_value="[复用档案]")
    h._do_ziwei_analysis = Mock(return_value="紫微分析")
    for u in users:
        _mk_archive(db, u, **ARCHIVE)
    return h, db


def _snap(db, user_id="u1"):
    from src.storage.person_dao import PersonDAO
    p = PersonDAO(db).get_default_person(user_id)
    return None if not p else (p["birth_year"], p["birth_month"],
                               p["birth_day"], p["city"])


class TestZiweiPendingScope:
    def test_cross_session_confirm_not_applied(self, tmp_path):
        """验收复现：A 会话紫微冲突问句 → B 会话回「确认」→ 不得承接。"""
        h, db = _h(tmp_path)
        o1 = h._handle_ziwei(ZIWEI_CONFLICT_MSG, "u1", session_id="sessA")
        assert "您刚说的出生信息" in o1, "紫微冲突未触发确认问句（前置条件）"
        h._handle_bazi("确认", "u1", None, "sessB")
        assert _snap(db) == (1999, 3, 28, "长春"), "跨会话承接生效（档案被改）"

    def test_cross_session_pending_not_readable(self, tmp_path):
        """直证：pending 存在 (u1, sessA)，sessB 读不到。"""
        h, db = _h(tmp_path)
        h._handle_ziwei(ZIWEI_CONFLICT_MSG, "u1", session_id="sessA")
        assert ("u1", "sessA") in h._pending_birth
        assert ("u1", "") not in h._pending_birth, "紫微仍落 (user,'') 通用键"
        assert h._peek_pending_birth("u1", "sessB") is None
        assert h._peek_pending_birth("u1", "sessA") is not None

    def test_same_session_confirm_applied(self, tmp_path):
        """同会话仍承接（体验不退）：A 会话内回「确认」→ 应用新值排盘。"""
        h, db = _h(tmp_path)
        h._handle_ziwei(ZIWEI_CONFLICT_MSG, "u1", session_id="sessA")
        h._handle_bazi("确认", "u1", None, "sessA")
        assert _snap(db) == (1995, 3, 8, "长春")

    def test_cross_user_not_applied(self, tmp_path):
        """跨用户隔离（既有行为回归）：u2 回「确认」不得承接 u1 的问句。"""
        h, db = _h(tmp_path, users=("u1", "u2"))
        h._handle_ziwei(ZIWEI_CONFLICT_MSG, "u1", session_id="sessA")
        h._handle_bazi("确认", "u2", None, "sessA")
        assert _snap(db, "u2") == (1999, 3, 28, "长春")

    def test_no_session_caller_still_works(self, tmp_path):
        """旧调用方（无 session_id 形参）语义不变：None → 键 (user,'')，
        同调用形态内仍可承接。"""
        h, db = _h(tmp_path)
        h._handle_ziwei(ZIWEI_CONFLICT_MSG, "u1")
        assert ("u1", "") in h._pending_birth
        h._handle_bazi("确认", "u1", None, None)
        assert _snap(db) == (1995, 3, 8, "长春")
