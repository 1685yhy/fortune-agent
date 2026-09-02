"""R2-2 额度限流修复测试：use_quota 满额重置删除 → 择日额度门真触发。

根因链（本批修复）：MemberDAO.use_quota 对 free 用户满额（used>=limit）时
执行 `UPDATE memberships SET queries_used=1`（重置为 1）→ 聊天路径
_consume_quota 在择日门（_check_quota）之前执行 → 重置后门检查
remaining=limit-1>0 → 门永不触发，免费用户可无限调引擎。
修复：满额不 UPDATE、保持 used=limit（额度门据此真触发）。

本测试全部使用真实 MemberDAO + 临时库（种子行直接 SQL 落库），
修复前每个断言都会失败（可判别非恒过）：
- 测试 1：修复前 use_quota 返回 True 且 used 变 1（现返回 False、used 保持 5）
- 测试 3：修复前 _consume_quota 先重置 → 门放行 → 调引擎产出正常文案
  （无「会员/额度」）；修复后门触发返回会员引导文案
- 测试 4：修复前 _consume_quota 把 used 5→1；修复后 used 保持 5
"""
import os
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from src.bot.handler import MessageHandler  # noqa: E402
from src.storage.dao import UserDAO  # noqa: E402
from src.storage.member_dao import MemberDAO  # noqa: E402

FREE_LIMIT = 5


def _seed_free(db_path: str, user_id: str, limit: int, used: int) -> MemberDAO:
    """建库并种子 free 行（显式 create_membership 落行 → SQL 直接定 used）。"""
    dao = MemberDAO(db_path)
    dao.create_membership(user_id, "free", queries_limit=limit)
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "UPDATE memberships SET queries_used=? WHERE user_id=? AND plan='free'",
            (used, user_id),
        )
        con.commit()
    finally:
        con.close()
    return dao


def _membership_count(db_path: str) -> int:
    con = sqlite3.connect(db_path)
    try:
        return con.execute("SELECT COUNT(*) FROM memberships").fetchone()[0]
    finally:
        con.close()


def _make_handler(db_path: str):
    """裸 handler 实例（不经 __init__ 网络绑定；member_dao 用真实临时库）。"""
    h = object.__new__(MessageHandler)
    h.dao = UserDAO(db_path)
    h.chart_dao = None
    h.llm = Mock()
    h.llm.analyze.return_value = SimpleNamespace(response="2026年9月15日宜搬家。")
    h.retriever = Mock()
    h.retriever.search.return_value = []
    h.zeri_engine = Mock()
    h.zeri_engine.select.return_value = SimpleNamespace(
        jianchu="成", ershibaxiu="亢金龙", xiu_jixiong="吉", chong="冲狗",
        yi=["搬家", "入宅"], ji=["动土"], overall="吉日可用",
    )
    h.memory = None
    h.memory_system = None
    h.session_dao = None
    h.member_dao = MemberDAO(db_path)
    h.preference_dao = None
    h.cache = Mock()
    h.cache.get.return_value = None
    h._deep_night = {}
    h._downgraded = {}
    h._tool_logs = {}
    h._citations = {}
    h._analysis_facts = {}
    h._pregen_instant = {}
    h._pregen_pool = Mock()
    h._gender_acks = {}
    return h


# ============================================================
# 1) use_quota 满额不重置（此前会重置为 1）
# ============================================================

def test_use_quota_at_limit_does_not_reset_free(tmp_path):
    """free 5/5 满额 → use_quota 不 UPDATE 不重置：返回 False、used 保持 5、
    行数不变（修复前：返回 True 且 used 被重置为 1）。"""
    db = str(tmp_path / "t.db")
    dao = _seed_free(db, "u_full", FREE_LIMIT, FREE_LIMIT)
    assert dao.use_quota("u_full") is False  # 未扣成
    mem = dao.get_membership("u_full")
    assert mem["queries_used"] == FREE_LIMIT  # 修复前此处变 1
    assert mem["queries_remaining"] == 0
    assert _membership_count(db) == 1  # 零写入延续（不新增行）


# ============================================================
# 2) 未满扣减保持（正常记账不受影响）
# ============================================================

def test_use_quota_under_limit_increments(tmp_path):
    """free 2/5 未满 → use_quota 照常 +1：返回 True、used=3。"""
    db = str(tmp_path / "t.db")
    dao = _seed_free(db, "u_ok", FREE_LIMIT, 2)
    assert dao.use_quota("u_ok") is True
    assert dao.get_membership("u_ok")["queries_used"] == 3


# ============================================================
# 3) 门触发端到端：满额 + 记账先行（聊天路径真实顺序）→ 择日门真触发
# ============================================================

def test_zeri_gate_fires_after_consume_at_limit(tmp_path, monkeypatch):
    """种子 5/5 + EXPERIENCE_MODE=0：先 _consume_quota（聊天路径在路由前
    记账）再 _handle_zeri → 额度门触发返回会员引导文案，不调引擎；
    used 保持 5（修复前：记账把 used 重置为 1 → 门放行 → 引擎被调、
    回复无「会员/额度」→ 本测试失败）。"""
    import src.bot.handler as handler_mod  # handler 模块级导入的函数引用须打此处
    monkeypatch.setattr(handler_mod, "is_experience_mode", lambda: False)
    db = str(tmp_path / "t.db")
    h = _make_handler(db)
    _seed_free(db, "u1", FREE_LIMIT, FREE_LIMIT)
    h.member_dao = MemberDAO(db)  # 与种子同一真实库
    # 聊天路径真实顺序：路由到意图处理器前先 _consume_quota（记账）
    h._consume_quota("u1")
    reply = h._handle_zeri("2026年9月15日搬家 帮我选个日子", "u1")
    assert "会员" in reply and "额度" in reply
    assert "19.9" in reply
    # 门触发 → 不调引擎、不写库（used 保持满额）
    h.zeri_engine.select.assert_not_called()
    assert h.member_dao.get_membership("u1")["queries_used"] == FREE_LIMIT
    assert _membership_count(db) == 1


# ============================================================
# 4) 聊天不挡：满额时 _consume_quota 不抛异常、不写行
# ============================================================

def test_consume_quota_at_limit_chat_not_blocked(tmp_path, monkeypatch):
    """used=5/limit=5 时 _consume_quota 不抛异常（聊天不 429 不硬断），
    记账不再增长：used 保持 5、行数不变（修复前：used 被重置为 1）。"""
    import src.bot.handler as handler_mod
    monkeypatch.setattr(handler_mod, "is_experience_mode", lambda: False)
    db = str(tmp_path / "t.db")
    h = _make_handler(db)
    _seed_free(db, "u_chat", FREE_LIMIT, FREE_LIMIT)
    h.member_dao = MemberDAO(db)
    h._consume_quota("u_chat")  # 不抛异常即过
    mem = h.member_dao.get_membership("u_chat")
    assert mem["queries_used"] == FREE_LIMIT  # 修复前此处变 1
    assert mem["queries_remaining"] == 0
    assert _membership_count(db) == 1  # 不写行


# ============================================================
# 5) R1-1 护栏：无行用户 use_quota 空操作成功（零写入不变）
# ============================================================

def test_use_quota_no_row_no_write_noop_kept(tmp_path):
    """R1-1 语义延续：无行用户 use_quota 返回 True 且 memberships 零写入
    （本批删除满额重置不得误伤无行分支）。"""
    db = str(tmp_path / "t.db")
    dao = MemberDAO(db)
    assert dao.use_quota("u_norow") is True
    assert dao.get_membership("u_norow")["queries_used"] == 0
    assert _membership_count(db) == 0
