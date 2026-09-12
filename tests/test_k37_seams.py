"""k37 接缝收口：S1 名笺额度超管豁免 + S4 工具路径静默 pass 补日志。

S1：`src/api/ming.py::_check_quota` 此前是超管额度豁免的漏网点——ADMIN_IDS 白名单
    用户免费「名笺生成」3 次/日仍 429。判据复用 `src/security/admin.py::is_admin_user`
    （单一事实源，不另写一套）；豁免语义与 k36 在 handler 一致：**只豁免额度门**
    （不 429），`consume_quota` 仍照常计数（照 k36
    test_admin_consume_path_unchanged 口径）。
S4：`src/bot/handler.py::_tool_bazi` 持久化段 `except Exception: pass` → 补日志，
    只记事件与位置：不得把用户隐私内容（user_id/生辰/参数）打进日志。

运行：
  OMP_NUM_THREADS=1 /home/a/fortune-run/.venv/bin/python3 -m pytest tests/test_k37_seams.py -q
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from src.api import ming as ming_mod  # noqa: E402
from src.security import admin as admin_mod  # noqa: E402

ADMIN_USER = "su_admin_u"
OTHER_USER = "plain_u"


class _FakeMingDAO:
    """consume_quota 桩：记录每次调用；remaining<0 表示库内已到上限（返回 -1）。"""

    def __init__(self, remaining=-1):
        self.remaining = remaining
        self.calls = []

    def consume_quota(self, user_id, day, limit):
        self.calls.append((user_id, day, limit))
        if self.remaining < 0:
            return -1
        self.remaining -= 1
        return self.remaining


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """每个用例从「无白名单/无会员注入/非体验模式」出发（防 .env 与模块全局串味）。"""
    monkeypatch.delenv("ADMIN_IDS", raising=False)
    monkeypatch.setattr(ming_mod, "is_experience_mode", lambda: False)
    monkeypatch.setattr(ming_mod, "_member_dao", None)
    yield


def _wire(monkeypatch, remaining=-1):
    dao = _FakeMingDAO(remaining)
    monkeypatch.setattr(ming_mod, "_mdao", lambda: dao)
    return dao


# ───────────────────────── S1：名笺额度超管豁免 ─────────────────────────

class TestMingQuotaAdminExemption:
    """免费名笺生成额度门：白名单内免额度、白名单外/未配置一律原行为。"""

    def test_judge_is_single_source(self):
        """判据唯一：ming 直接用 src/security/admin.is_admin_user（不另写一套）。"""
        assert ming_mod.is_admin_user is admin_mod.is_admin_user

    def test_non_admin_over_limit_429(self, monkeypatch):
        """白名单外用户超限 → 429（既有行为，豁免不得外溢）。"""
        _wire(monkeypatch)
        with pytest.raises(HTTPException) as ei:
            ming_mod._check_quota(OTHER_USER)
        assert ei.value.status_code == 429

    def test_admin_over_limit_no_429_and_still_counted(self, monkeypatch):
        """超管超限 → 放行（不 429）；consume_quota 仍照常计数（与 handler 口径一致）。"""
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        dao = _wire(monkeypatch)
        ming_mod._check_quota(ADMIN_USER)  # 不抛 = 放行
        assert [c[0] for c in dao.calls] == [ADMIN_USER], "豁免只加在检查口，计数照常"

    def test_admin_under_limit_unchanged(self, monkeypatch):
        """白名单内未超限：与普通用户同为放行，计数照常（无额外分支差异）。"""
        monkeypatch.setenv("ADMIN_IDS", ADMIN_USER)
        dao = _wire(monkeypatch, remaining=2)
        ming_mod._check_quota(ADMIN_USER)
        assert len(dao.calls) == 1

    def test_empty_whitelist_still_429(self, monkeypatch):
        """fail-closed：ADMIN_IDS 未配置 → 零超管，仍 429。"""
        _wire(monkeypatch)
        with pytest.raises(HTTPException) as ei:
            ming_mod._check_quota(ADMIN_USER)
        assert ei.value.status_code == 429

    def test_other_user_in_whitelist_still_429(self, monkeypatch):
        """白名单是精确匹配：白名单里是别人 → 本用户仍 429。"""
        monkeypatch.setenv("ADMIN_IDS", "someone_else")
        _wire(monkeypatch)
        with pytest.raises(HTTPException) as ei:
            ming_mod._check_quota(ADMIN_USER)
        assert ei.value.status_code == 429

    def test_experience_mode_unchanged(self, monkeypatch):
        """体验模式既有豁免通道不变：不查库、不计数。"""
        monkeypatch.setattr(ming_mod, "is_experience_mode", lambda: True)
        dao = _wire(monkeypatch)
        ming_mod._check_quota(OTHER_USER)
        assert dao.calls == []


# ───────────────────── S4：工具排盘静默 pass 补日志 ─────────────────────

def _handler_with_failing_persist(tmp_path):
    """object.__new__ 手工装配的 MessageHandler：工具排盘持久化首步即抛。"""
    from unittest.mock import Mock

    from src.bot.handler import MessageHandler
    from src.storage.chart_dao import ChartDAO

    h = object.__new__(MessageHandler)
    dao = Mock()
    dao.db_path = str(tmp_path / "p.db")
    dao.save_user_bazi = Mock(side_effect=RuntimeError("db down"))  # 持久化段第一步
    h.dao = dao
    h.memory_system = None
    h._analysis_facts = {}
    h._citations = {}
    h.chart_dao = ChartDAO(str(tmp_path / "c.db"))
    return h


def test_tool_bazi_persist_failure_logged_without_privacy(tmp_path, caplog):
    """落库失败：主链照常返回（不阻断），但必须留事件+位置日志，且不含隐私内容。"""
    import src.bot.handler as handler_mod
    from src.engines.bazi import BaziEngine

    h = _handler_with_failing_persist(tmp_path)
    h.engine = BaziEngine()
    with caplog.at_level(logging.WARNING, logger=handler_mod.__name__):
        tr = h._tool_bazi("1990年5月20日 15点 北京 男", "u_priv_7")
    assert tr.ok is True, "持久化失败不得阻断排盘主链（原语义不变）"
    hits = [r.getMessage() for r in caplog.records if "失败" in r.getMessage()]
    assert hits, "持久化失败必须留日志（此前 except: pass 静默无痕）"
    msg = "\n".join(hits)
    assert "_tool_bazi" in msg, "日志必须含位置（便于定位）"
    for leak in ("u_priv_7", "1990", "5月20日", "15点", "北京"):
        assert leak not in msg, f"日志不得含用户隐私内容：{leak}"
