# -*- coding: utf-8 -*-
"""k49 E（既有缺陷，同类一并修）：城市提取吞语境前缀 + F：SyntaxWarning 噪音。

E 现象（四维验收实测，基线逐字相同 ⇒ 既有缺陷非回归）：
`我出生在长春市` → `city="出生在长春市"`（XX市 正则从任意起算位起步，把
"出生在"卷进候选），且脏值同时进 persons / users.bazi_info / chart_records
三源（数据一致性面）。
期望：剥掉语境前缀（出生在/生在/来自/是/在 等）→ "长春市"（保留后缀"市"，
与既有 "广州市" 口径一致，不另做归一）；修复后**三源同值**（走既有漏斗，
不新增写路径）；罕见地名不被误改（宁可不动不可改错）。

F 现象：`handler.py:988` `_valid_month_day` docstring 非 raw 却含 `` `(?<!\\d)` ``
→ `SyntaxWarning: invalid escape sequence '\\d'`（基线 0 警告）。改 raw 即可。

隔离：tmp_path 真实 SQLite + 真实 BaziEngine；零 LLM（降级档）。
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-32-bytes-long!!")

from unittest.mock import Mock  # noqa: E402

import pytest  # noqa: E402

from src.bot.handler import MessageHandler, _clean_city_name  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _h(tmp_path):
    from src.engines.bazi import BaziEngine
    from src.storage.chart_dao import ChartDAO
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
    h.chart_dao = ChartDAO(str(tmp_path / "c.db"))
    h.member_dao = None
    h._downgraded = {"u1": True}
    h._deep_night = {}
    h._analysis_facts = {}
    h.tool_logs = {}
    h._citations = {}
    h._fact_ctx = {}
    h._pregen_instant = {}
    h._consume_pregen_instant = Mock(return_value="")
    h._gen_info_collection_prompt = Mock(return_value="渐进引导")
    h._gen_reuse_acknowledgment = Mock(return_value="")
    return h, db


# ════════════════════════════════════════════════════════════════
# E-1 提取层：前缀剥离
# ════════════════════════════════════════════════════════════════
class TestCityPrefixStripped:
    @pytest.mark.parametrize("msg,expect", [
        ("我出生在长春市", "长春市"),
        ("我在长春市", "长春市"),
        ("我出生于长春市", "长春市"),
        ("我出生在吉林市", "吉林市"),
        ("我出生在长春市榆树市", "榆树市"),
    ])
    def test_partial_extractor_strips_prefix(self, msg, expect):
        h = object.__new__(MessageHandler)
        assert h._extract_partial_birth(msg).get("city") == expect

    def test_full_extractor_strips_prefix(self):
        h = object.__new__(MessageHandler)
        got = h._extract_bazi_info("我1999年3月28日10点出生在长春市 男")
        assert got[5] == "长春市", got

    @pytest.mark.parametrize("msg,expect", [
        ("我生在沈阳", "沈阳"),
        ("我是1990年5月20日10点 广州市 男", "广州市"),   # 既有 XX市 口径
        ("三月初三，吉林省长春市榆树市出生，男", "榆树市"),  # 既有最内层口径
    ])
    def test_existing_city_forms_unchanged(self, msg, expect):
        h = object.__new__(MessageHandler)
        got = h._extract_partial_birth(msg).get("city")
        if got is None:      # 全量形态（含年份）走全量提取器
            got = (h._extract_bazi_info(msg) or (None,) * 7)[5]
        assert got == expect

    def test_city_without_suffix_still_not_taken(self):
        """既有行为保持（本批**未**扩大城市识别范围）：`我来自吉林长春` 的
        长春既不在 COMMON_CITIES 也无"市"尾缀 → 仍不取城市（宁可不动不可改错：
        放开裸城市名会让"下个月去三亚"一类行程地名变成出生地）。"""
        h = object.__new__(MessageHandler)
        assert h._extract_partial_birth("我来自吉林长春").get("city") is None

    def test_rare_name_not_mangled(self):
        """宁可不动不可改错：剥完不是已知城市 → 原样返回。"""
        assert _clean_city_name("长春") == "长春"            # 无前缀
        assert _clean_city_name("是我的") == "是我的"         # 剥完 <2 字 → 不动
        assert _clean_city_name("") == ""
        assert _clean_city_name("在庄市") == "在庄市"          # 未知"城市"不剥
        assert _clean_city_name("我在北") == "我在北"          # 剥完不足 2 字


# ════════════════════════════════════════════════════════════════
# E-2 三源同值（persons / users.bazi_info / chart_records）
# ════════════════════════════════════════════════════════════════
class TestThreeStoreConsistency:
    def test_prefix_city_same_in_three_stores(self, tmp_path):
        """改前实测：三源同值但值是脏的 "出生在长春市"；改后三源同值 = "长春市"。"""
        from src.storage.dao import UserDAO
        h, db = _h(tmp_path)
        h._handle_bazi("我1999年3月28日10点出生在长春市 男", "u1")
        p = h.chart_dao and None
        from src.storage.person_dao import PersonDAO
        persons_city = PersonDAO(db).get_default_person("u1")["city"]
        bazi_city = (UserDAO(db).get_user_bazi("u1") or {}).get("city")
        chart_city = (h.chart_dao.get_latest_chart("u1") or {}).get("birth", {}
                      ).get("city")
        assert persons_city == bazi_city == chart_city == "长春市", (
            persons_city, bazi_city, chart_city)


# ════════════════════════════════════════════════════════════════
# F SyntaxWarning
# ════════════════════════════════════════════════════════════════
class TestNoSyntaxWarning:
    def test_handler_compiles_without_syntax_warning(self):
        """`-W error::SyntaxWarning` 下编译 handler.py 必须干净（基线 1 条
        `invalid escape sequence '\\d'`——`_valid_month_day` docstring 非 raw）。"""
        code = ("import py_compile, sys;"
                "py_compile.compile(sys.argv[1], cfile=sys.argv[2], doraise=True)")
        r = subprocess.run(
            [sys.executable, "-W", "error::SyntaxWarning", "-c", code,
             os.path.join(ROOT, "src", "bot", "handler.py"),
             os.path.join(str(self.tmp_cfile()), "h.pyc")],
            capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    @staticmethod
    def tmp_cfile():
        import tempfile
        return tempfile.mkdtemp()
