# -*- coding: utf-8 -*-
"""k49 A（Important-1）：`_tool_bazi` 工具路径的档案守卫（第三写入口收口）。

现象（k48-r3 上线后的四维独立验收实测，证据 `/tmp/k48verify-main/toolpath.json`）：
`h._tool_bazi("1995年3月8日10点 北京 女", "u1")` —— 用户档案 1999-03-28 长春 →
**persons 被改成 1995-03-08 北京**（21:44 事故的 4 年跳变形态）；
`h._tool_bazi("我1999年生的，视力4.5", "u1")` → 改成 1999-04-05 北京。
该工具经 `capability_registry` 暴露给 LLM 工具循环（params 由模型填），
`_handle_bazi`/`_handle_ziwei` 的"先问后写"守卫**覆盖不到**它。

期望：工具路径与对话层同一守卫——冲突（年/月/日/城市）且来源消息非
「显式出生陈述」→ 不写入、回确认问句（承接后经对话层再写）；无冲突照旧直写。

隔离：tmp_path 真实 SQLite + 真实 BaziEngine；零网络零 LLM（降级档）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

from unittest.mock import Mock  # noqa: E402

from src.bot.handler import MessageHandler  # noqa: E402

ARCHIVE = dict(birth_year=1999, birth_month=3, birth_day=28, birth_hour=10,
               birth_minute=55, city="长春", gender="男")


def _mk_archive(db_path, user_id="u1", **over):
    from src.storage.person_dao import PersonDAO
    b = {"gender": "男", "birth_year": 1999, "birth_month": 3, "birth_day": 28,
         "birth_hour": 10, "birth_minute": 55, "calendar": "solar",
         "city": "长春"}
    b.update(over)
    return PersonDAO(db_path).create_person(
        user_id, name="我", relation="自己", is_default=True, birth=b)


def _h(tmp_path, users=("u1",), seed=ARCHIVE):
    """object.__new__ 装配（真实 engine/dao/persons；零 __init__ 副作用）。"""
    from src.engines.bazi import BaziEngine
    from src.storage.dao import UserDAO
    db = str(tmp_path / "p.db")
    h = object.__new__(MessageHandler)
    h.engine = BaziEngine()
    h.ziwei_engine = None
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
    h._downgraded = {u: True for u in users}     # 降级档：零 LLM
    h._deep_night = {}
    h._analysis_facts = {}
    h.tool_logs = {}
    h._citations = {}
    h._fact_ctx = {}
    h._gen_info_collection_prompt = Mock(return_value="渐进引导")
    h._gen_reuse_acknowledgment = Mock(return_value="")
    for u in users:
        if seed:
            _mk_archive(db, u, **seed)
    return h, db


def _snap(db, user_id="u1"):
    from src.storage.person_dao import PersonDAO
    p = PersonDAO(db).get_default_person(user_id)
    if not p:
        return None
    return (p["birth_year"], p["birth_month"], p["birth_day"], p["city"])


# ════════════════════════════════════════════════════════════════
# 1) 现象复现（改前必然失败）
# ════════════════════════════════════════════════════════════════
class TestToolPathConflictDoesNotWrite:
    def test_year_conflict_tool_write_blocked(self, tmp_path):
        """验收实测形态：工具参数 1995-03-08 北京 → 不得改写 1999-03-28 长春。"""
        h, db = _h(tmp_path)
        r = h._tool_bazi("1995年3月8日10点 北京 女", "u1")
        assert _snap(db) == (1999, 3, 28, "长春"), "工具路径静默改档未收口"
        assert "不太一致" in r.text, r.text
        assert not r.ok, "冲突时不得按新信息出盘"

    def test_residual_vision_45_tool_write_blocked(self, tmp_path):
        """残留①形态：`我1999年生的，视力4.5` 工具参数 → 不得改成 1999-04-05。"""
        h, db = _h(tmp_path)
        h._tool_bazi("我1999年生的，视力4.5", "u1")
        assert _snap(db) == (1999, 3, 28, "长春"), "裸小数被当生辰写入"

    def test_conflict_stashes_pending_for_dialogue_layer(self, tmp_path):
        """承接半环：冲突时暂存待更新值（键 (user, session)），不落库。"""
        h, db = _h(tmp_path)
        h._tool_bazi("1995年3月8日10点 北京 女", "u1", "", "s1")
        assert (("u1", "s1") in h._pending_birth), h._pending_birth
        assert _snap(db) == (1999, 3, 28, "长春")


# ════════════════════════════════════════════════════════════════
# 2) 双向：该直写的一律直写（"说一次就记住"体验不变）
# ════════════════════════════════════════════════════════════════
class TestToolPathStillWritesWhenNoConflict:
    def test_named_explicit_statement_writes_directly(self, tmp_path):
        """显式出生陈述（同轮用户原文带出生语境）→ 直接写 + 出盘。"""
        h, db = _h(tmp_path)
        r = h._tool_bazi(
            "1999年7月8日10点 北京 男", "u1",
            "我是1999年7月8日在北京出生的", "s1")
        assert r.ok, r.text
        assert _snap(db) == (1999, 7, 8, "北京")

    def test_no_conflict_writes_directly(self, tmp_path):
        """与档案同值（无冲突）→ 照旧直写、不出确认问句（"说一次就记住"）。
        城市取城市库可识别值（城市缺省"北京"覆盖档案城市是**既有**另一族
        问题，见报告诚实披露——本用例不覆盖它）。"""
        h, db = _h(tmp_path, seed=dict(ARCHIVE, city="沈阳"))
        r = h._tool_bazi("1999年3月28日10点55分 沈阳 男", "u1")
        assert r.ok, r.text
        assert "不太一致" not in r.text
        assert _snap(db) == (1999, 3, 28, "沈阳")

    def test_no_archive_writes_directly(self, tmp_path):
        """无档案（首次建档）→ 无冲突可比 → 直接写。"""
        h, db = _h(tmp_path, users=("u9",), seed=None)
        r = h._tool_bazi("1990年5月20日 15点 北京 男", "u9")
        assert r.ok, r.text
        assert _snap(db, "u9") == (1990, 5, 20, "北京")


# ════════════════════════════════════════════════════════════════
# 3) 承接 / 隔离
# ════════════════════════════════════════════════════════════════
class TestToolPathConfirmAndScope:
    def test_confirm_applies_pending(self, tmp_path):
        """工具抛出确认问句 → 用户回「确认」→ 对话层承接并排盘（写入新值）。"""
        h, db = _h(tmp_path)
        h._tool_bazi("1995年3月8日10点 北京 女", "u1", "", "s1")
        assert _snap(db) == (1999, 3, 28, "长春")
        h._handle_bazi("确认", "u1", None, "s1")
        assert _snap(db) == (1995, 3, 8, "北京")

    def test_other_session_confirm_does_not_apply(self, tmp_path):
        """跨会话隔离：另一会话回「确认」不得承接（k49 I-3 同族）。"""
        h, db = _h(tmp_path)
        h._tool_bazi("1995年3月8日10点 北京 女", "u1", "", "sA")
        h._handle_bazi("确认", "u1", None, "sB")
        assert _snap(db) == (1999, 3, 28, "长春")

    def test_cross_user_archive_untouched(self, tmp_path):
        """跨用户隔离：u1 的工具排盘不得动 u2 的档案。"""
        h, db = _h(tmp_path, users=("u1", "u2"))
        _mk_archive(db, "u2", birth_year=1968, birth_month=5, birth_day=13,
                    city="上海")
        h._tool_bazi("1995年3月8日10点 北京 女", "u1")
        assert _snap(db, "u1") == (1999, 3, 28, "长春")
        assert _snap(db, "u2") == (1968, 5, 13, "上海")


# ════════════════════════════════════════════════════════════════
# 4) 上下文注入（"工具执行上下文拿不到当轮用户原文"的正面证据）
# ════════════════════════════════════════════════════════════════
class TestToolExecContextThreading:
    def test_run_with_timeout_injects_question_and_session(self):
        """`_run_with_timeout` 把 user_question/session_id 注入执行器
        （守卫判"是否显式出生陈述"必需；改前 bazi_chart 适配层把两者丢弃）。"""
        from src.bot.capability_registry import Capability
        seen = {}

        def _exec(params, user_id="", user_question="", session_id=None):
            seen.update(params=params, user_id=user_id,
                        user_question=user_question, session_id=session_id)
            from src.bot.tool_calls import ToolResult
            return ToolResult("排盘", True, "ok")

        h = object.__new__(MessageHandler)
        cap = Capability("bazi_chart", "排盘", "", {}, executor=_exec,
                         timeout_s=8.0)
        h._run_with_timeout(cap, "1995年3月8日10点 北京 女", "u1",
                            "我是1995年3月8日出生的", "s1")
        assert seen["user_question"] == "我是1995年3月8日出生的"
        assert seen["session_id"] == "s1"

    def test_legacy_executor_signature_still_works(self):
        """**老签名绑定**（无 session_id 形参；既有测试/外部自定义绑定形态）
        不得因新增 kwarg 整条工具链失败（否则 TypeError 被当"异常重试"吞掉 →
        静默降级成"工具不可用"——本批实测过 5 条既有测试因此翻红）。"""
        from src.bot.capability_registry import Capability
        from src.bot.tool_calls import ToolResult
        calls = []

        def _legacy(params, user_id="", user_question=""):
            calls.append((params, user_id, user_question))
            return ToolResult("排盘", True, "legacy-ok")

        h = object.__new__(MessageHandler)
        cap = Capability("bazi_chart", "排盘", "", {}, executor=_legacy,
                         timeout_s=8.0)
        r = h._run_with_timeout(cap, "1999年3月28日10点 长春 男", "u1",
                                "我1999年3月28日10点出生在长春", "s1")
        assert r.ok and r.text == "legacy-ok", r
        assert calls and calls[0][2] == "我1999年3月28日10点出生在长春"
