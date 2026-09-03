"""R2-6（Gap B）：_handle_bazi 残余农历直喂点修复（B1 合并 / B2 档案兜底 /
F2 渐进累积）+ 同批同类直喂点（_handle_calendar 补齐四柱）+ _solarize_birth 单测。

R2-5 只修了 _tool_bazi 档案兜底（tests/test_lunar_birth_solar_convert.py，
19 条）；对话主链路 _handle_bazi 内三处仍把 lunar 原始 y/m/d 直喂引擎
（用户 2026-09-04 实锤：档案 calendar=lunar, 1999-03-28, hour=9,
city='吉林省长春市', gender='男'）：
- B1 档案+消息合并分支（消息补时辰 → merged 继承 lunar 原始值）
- B2 档案直接兜底分支
- F2 渐进式累积（分步口述「农历1999年3月28日…上午11点」）
- 同批勘察发现的同类点：_handle_calendar/_handle_hourly 缺四柱时的引擎补齐

修复 = 单点转换：_feed_birth/_solarize_birth（calendar=='lunar' → to_solar_date
转公历浅拷贝喂引擎；原始 y/m/d + calendar 标记经 arch_raw 原样落库，不改写
原值不抹标记——与 R2-5 存储=事实源铁律同口径）。引擎契约不变（公历入参，
BaziEngine.calculate 签名零改动）。

北极星断言（与问真逐字，主会话已实证）：
calculate(1999,5,13,11,0,'长春','男',solar_time=True).qiyun_desc
== "出生后2年4月22天0时起运"；jiaoyun 白露后27天（辛、丙年）。

运行：/home/a/fortune-agent/.venv/bin/python -m pytest tests/test_bazi_residual_paths.py -q
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
from unittest.mock import Mock  # noqa: E402

from src.storage.birth_profile import to_solar_date  # noqa: E402

# 生产档案字面量（persons 表真实值，勿自造顺手值）
ARCHIVE_LUNAR = {"year": 1999, "month": 3, "day": 28, "hour": 9,
                 "minute": None, "city": "吉林省长春市", "gender": "男",
                 "calendar": "lunar"}
NORTH_STAR = "出生后2年4月22天0时起运"  # 问真逐字（hour=11, 真太阳时修正后）


# ================================================================
# 装配：object.__new__(MessageHandler) + 真实 DAO/引擎（R2-5 模式）
# ================================================================

class _RecordingEngine:
    """记录 calculate 实参并透传真实 BaziEngine 结果。"""

    def __init__(self):
        self.real = None
        self.calls = []
        self.results = []

    def calculate(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        r = self.real.calculate(*args, **kwargs)
        self.results.append(r)
        return r


def _make_handler(tmp_path, uid, engine=None, downgraded=True):
    """object.__new__ 装配：真实 DAO + 真实引擎（_RecordingEngine 包裹）。

    downgraded=True：_do_bazi_analysis 走 _do_bazi_lite（确定性 0 LLM），
    引擎实收/落库与主路径同口径，测试不依赖任何 LLM/网络。
    """
    from src.bot.handler import MessageHandler
    from src.storage.dao import UserDAO
    from src.storage.chart_dao import ChartDAO
    h = object.__new__(MessageHandler)
    h.dao = UserDAO(str(tmp_path / "p.db"))
    h.chart_dao = ChartDAO(str(tmp_path / "c.db"))
    h.memory_system = None
    h.memory = None
    h._analysis_facts = {}
    h._citations = {}
    h._downgraded = {uid: True} if downgraded else {}
    h._deep_night = {}
    h._gender_acks = {}
    h.session_dao = None
    h.llm = None
    h.engine = engine
    return h


def _create_lunar_person(pdao, user_id):
    pdao.create_person(
        user_id, name="我", relation="自己", is_default=True,
        birth={"gender": "男", "birth_year": 1999, "birth_month": 3,
               "birth_day": 28, "birth_hour": 9, "birth_minute": None,
               "calendar": "lunar", "city": "吉林省长春市"})


def _real_dbs(tmp_path):
    from src.storage.dao import UserDAO
    from src.storage.person_dao import PersonDAO
    return UserDAO(str(tmp_path / "p.db")), PersonDAO(str(tmp_path / "p.db"))


# ================================================================
# 1) _solarize_birth 单测（浅拷贝 / 不 mutate 入参 / 失败回落 + warning）
# ================================================================

class TestSolarizeBirth:
    def _h(self):
        from src.bot.handler import MessageHandler
        return object.__new__(MessageHandler)

    def test_lunar_returns_converted_copy_original_untouched(self):
        """lunar → 返回浅拷贝（y/m/d=公历 5/13）；入参 dict 不被 mutate
        （y/m/d 仍 3/28 + calendar 'lunar'）；copy 非原对象。"""
        h = self._h()
        prof = dict(ARCHIVE_LUNAR)
        out = h._solarize_birth(prof)
        assert out is not prof, "必须返回拷贝（转换发生过）"
        assert (out["year"], out["month"], out["day"]) == (1999, 5, 13)
        assert out["calendar"] == "lunar"  # 拷贝保留原标记（消费方据此透传落库）
        assert out["hour"] == 9 and out["city"] == "吉林省长春市"
        # 入参原值铁律：绝不被改写（persons/bazi_info/chart_records 事实源）
        assert (prof["year"], prof["month"], prof["day"]) == (1999, 3, 28)
        assert prof["calendar"] == "lunar"

    def test_solar_returns_same_object_no_warning(self, caplog):
        """solar 档案 → 原 dict 原样返回（零转换零告警）。"""
        h = self._h()
        prof = {"year": 1990, "month": 5, "day": 20, "hour": 15,
                "calendar": "solar", "city": "北京", "gender": "男"}
        with caplog.at_level(logging.WARNING, logger="src.bot.handler"):
            assert h._solarize_birth(prof) is prof
        assert not caplog.records

    def test_legacy_no_calendar_returns_same_object_no_warning(self, caplog):
        """旧档案无 calendar 键 → 缺省 solar 语义 → 原 dict 返回、不告警。"""
        h = self._h()
        prof = {"year": 1990, "month": 5, "day": 20, "hour": 15,
                "city": "北京", "gender": "男"}
        with caplog.at_level(logging.WARNING, logger="src.bot.handler"):
            assert h._solarize_birth(prof) is prof
        assert not caplog.records

    def test_lunar_conversion_failure_returns_original_with_warning(self, caplog):
        """lunar 但转换失败（1999-03-30 不存在）→ 原 dict 回落 + warning
        （不抛异常不阻塞）；原 dict 未被 mutate。"""
        h = self._h()
        prof = {"year": 1999, "month": 3, "day": 30, "hour": 9,
                "calendar": "lunar", "city": "吉林省长春市", "gender": "男"}
        with caplog.at_level(logging.WARNING, logger="src.bot.handler"):
            assert h._solarize_birth(prof) is prof
        assert (prof["year"], prof["month"], prof["day"]) == (1999, 3, 30)
        assert any("失败" in r.message for r in caplog.records), \
            f"转换失败必须 warning: {[r.message for r in caplog.records]}"

    def test_to_solar_contract_same_as_storage_module(self):
        """与 R2-5 单点转换函数结果一致（同口径复核）。"""
        assert to_solar_date(ARCHIVE_LUNAR) == (1999, 5, 13)


# ================================================================
# 2) B1：lunar 档案 + 消息补 hour=11 → 引擎实收公历 (1999,5,13,11,0)
# ================================================================

class TestB1MergeBranch:
    def test_lunar_archive_plus_hour_message_engine_receives_solar(self, tmp_path):
        """B1 残余直喂点：档案 lunar 1999-03-28(hour=9) + 消息补「上午11点」→
        引擎实收 (1999,5,13,11,0)，qiyun == 北极星逐字（修复前实收
        (1999,3,28,11,0) → 断言必失败）。"""
        from src.engines.bazi import BaziEngine
        from src.storage.person_dao import PersonDAO
        rec = _RecordingEngine()
        rec.real = BaziEngine()
        h = _make_handler(tmp_path, "u-b1", engine=rec)
        dao, pdao = h.dao, PersonDAO(h.dao.db_path)
        _create_lunar_person(pdao, "u-b1")

        out = h._handle_bazi("我的出生时辰是上午11点", "u-b1")
        assert rec.calls, "引擎必须被调用"
        args = rec.calls[0][0]
        assert args[:5] == (1999, 5, 13, 11, 0), \
            f"引擎应收公历 (1999,5,13,11,0)，实收 {args[:5]}"
        assert args[5] == "吉林省长春市" and args[6] == "男"
        assert rec.results[0].qiyun_desc == NORTH_STAR, rec.results[0].qiyun_desc
        assert "排盘" in out or "命局" in out

    def test_b1_persist_keeps_raw_lunar_and_marker(self, tmp_path):
        """B1 落库三处（bazi_info/persons/chart_records）保留原始 3/28 +
        calendar='lunar'——不改写原值不抹标记（自毁式修复防范）。"""
        from src.engines.bazi import BaziEngine
        from src.storage.person_dao import PersonDAO
        rec = _RecordingEngine()
        rec.real = BaziEngine()
        h = _make_handler(tmp_path, "u-b1p", engine=rec)
        dao, pdao = h.dao, PersonDAO(h.dao.db_path)
        _create_lunar_person(pdao, "u-b1p")

        h._handle_bazi("我的出生时辰是上午11点", "u-b1p")
        bazi = dao.get_user_bazi("u-b1p")
        assert (bazi["year"], bazi["month"], bazi["day"]) == (1999, 3, 28)
        assert bazi.get("calendar") == "lunar"
        p = pdao.get_default_person("u-b1p")
        assert (p["birth_year"], p["birth_month"], p["birth_day"]) == (1999, 3, 28)
        assert p["calendar"] == "lunar"
        chart = h.chart_dao.get_latest_chart("u-b1p")
        assert chart is not None
        assert (chart["birth"]["year"], chart["birth"]["month"],
                chart["birth"]["day"]) == (1999, 3, 28)
        assert chart["birth"]["calendar"] == "lunar"
        # 四柱 = 公历盘（5/13 → 乙丑日主，11 时 → 壬午时柱），不是 3/28 盘
        # （己卯日主）——hour=11 午时，时柱 壬午（R2-5 的辛巳是 hour=9 巳时）
        assert chart["bazi_json"]["bazi"] == ["己卯", "己巳", "乙丑", "壬午"]

    def test_b1_solar_archive_zero_behavior_change(self, tmp_path):
        """B1 对照：solar 档案 + 消息补时辰 → 引擎实收原值 (1990,5,20,15,0)，
        落库无 lunar 标记（零行为回退）。"""
        from src.engines.bazi import BaziEngine
        from src.storage.person_dao import PersonDAO
        rec = _RecordingEngine()
        rec.real = BaziEngine()
        h = _make_handler(tmp_path, "u-b1s", engine=rec)
        dao, pdao = h.dao, PersonDAO(h.dao.db_path)
        pdao.create_person(
            "u-b1s", name="我", relation="自己", is_default=True,
            birth={"gender": "男", "birth_year": 1990, "birth_month": 5,
                   "birth_day": 20, "birth_hour": 9, "birth_minute": 0,
                   "calendar": "solar", "city": "北京"})

        h._handle_bazi("我的出生时辰是下午3点", "u-b1s")
        args = rec.calls[0][0]
        assert args[:5] == (1990, 5, 20, 15, 0)
        bazi = dao.get_user_bazi("u-b1s")
        assert (bazi["year"], bazi["month"], bazi["day"]) == (1990, 5, 20)
        assert bazi.get("calendar") != "lunar"  # solar 流不写 lunar 标记


# ================================================================
# 3) B2：lunar 档案直接兜底 → 引擎实收 (1999,5,13,9,0)
# ================================================================

class TestB2ArchiveFallback:
    def test_lunar_archive_direct_feed_engine_receives_solar(self, tmp_path):
        """B2 残余直喂点：「帮我排个盘」→ saved 直接兜底——引擎实收
        (1999,5,13,9,0, 吉林省长春市, 男)（修复前实收 1999-3-28 农历 → 断言
        必失败）；qiyun == 同参 city='长春' 直算（真太阳时修正未因省前缀被跳过）。"""
        from src.engines.bazi import BaziEngine
        from src.storage.person_dao import PersonDAO
        rec = _RecordingEngine()
        rec.real = BaziEngine()
        h = _make_handler(tmp_path, "u-b2", engine=rec)
        pdao = PersonDAO(h.dao.db_path)
        _create_lunar_person(pdao, "u-b2")

        out = h._handle_bazi("帮我排个盘", "u-b2")
        assert rec.calls
        args = rec.calls[0][0]
        assert args[:5] == (1999, 5, 13, 9, 0), \
            f"引擎应收公历 (1999,5,13,9,0)，实收 {args[:5]}"
        assert args[5] == "吉林省长春市" and args[6] == "男"
        ref = BaziEngine().calculate(1999, 5, 13, 9, 0, "长春", "男")
        assert rec.results[0].qiyun_desc == ref.qiyun_desc
        assert "2年4月" in rec.results[0].qiyun_desc
        assert "排盘" in out or "命局" in out


# ================================================================
# 4) F2：分步口述农历（历史「农历1999年3月28日」+ 当前消息补全）→ 公历
# ================================================================

def _session_dao_with(*contents):
    sd = Mock()
    sd.get_context_for_llm.return_value = [
        {"role": "user", "content": c} for c in contents]
    return sd


class TestF2ProgressiveLunar:
    def test_split_lunar_statement_feeds_converted_solar(self, tmp_path):
        """F2 残余直喂点：无档案用户分步口述——历史「农历1999年3月28日出生」
        + 当前「上午9点 吉林省长春市 男」→ known 月日=农历原始值（_md_lunar
        标记）→ 引擎实收 (1999,5,13,9,0)（修复前实收 (1999,3,28,9,0) → 断言
        必失败）。"""
        from src.engines.bazi import BaziEngine
        rec = _RecordingEngine()
        rec.real = BaziEngine()
        h = _make_handler(tmp_path, "u-f2", engine=rec)
        h.session_dao = _session_dao_with("农历1999年3月28日出生")

        out = h._handle_bazi("上午9点 吉林省长春市 男，帮我排个盘", "u-f2")
        assert rec.calls, "F2 齐全必须自动排盘（不引导）"
        args = rec.calls[0][0]
        assert args[:5] == (1999, 5, 13, 9, 0), \
            f"引擎应收公历 (1999,5,13,9,0)，实收 {args[:5]}"
        assert "2年4月" in rec.results[0].qiyun_desc
        assert "排盘" in out or "命局" in out
        # 落库保留原始农历 + 标记（无档案用户建档路径同铁律）
        bazi = h.dao.get_user_bazi("u-f2")
        assert (bazi["year"], bazi["month"], bazi["day"]) == (1999, 3, 28)
        assert bazi.get("calendar") == "lunar"

    def test_split_arabic_solar_md_stays_solar(self, tmp_path):
        """F2 对照：分步口述阿拉伯数字月日（无农历关键字，D7=公历口径）→
        引擎实收原值 (1990,5,20,9,0)，不做多余转换。"""
        from src.engines.bazi import BaziEngine
        rec = _RecordingEngine()
        rec.real = BaziEngine()
        h = _make_handler(tmp_path, "u-f2s", engine=rec)
        h.session_dao = _session_dao_with("1990年5月20日出生")

        h._handle_bazi("上午9点 北京 男，帮我排个盘", "u-f2s")
        args = rec.calls[0][0]
        assert args[:5] == (1990, 5, 20, 9, 0)
        bazi = h.dao.get_user_bazi("u-f2s")
        assert (bazi["year"], bazi["month"], bazi["day"]) == (1990, 5, 20)
        assert bazi.get("calendar") != "lunar"

    def test_md_lunar_marker_latest_wins(self):
        """_collect_partial_birth：月日口径最新者胜——先农历后公历（无关键字）
        → marker 清除；先公历后农历 → marker 置位。"""
        from src.bot.handler import MessageHandler
        h = object.__new__(MessageHandler)
        h.session_dao = None
        # 最新一条公历（阿拉伯数字无关键字）→ 清除先前农历标记
        sd = _session_dao_with("农历1999年3月28日")
        h.session_dao = sd
        known, _ = h._collect_partial_birth("u-x", history=[{"role": "user", "content": "农历1999年3月28日"}, {"role": "user", "content": "其实是5月13日"}])
        assert (known["month"], known["day"]) == (5, 13)
        assert known.get("_md_lunar") is False
        # 最新一条农历（显式关键字）→ 置位
        known2, _ = h._collect_partial_birth("u-x", history=[{"role": "user", "content": "5月13日"}, {"role": "user", "content": "记错了是农历3月28日"}])
        assert (known2["month"], known2["day"]) == (3, 28)
        assert known2.get("_md_lunar") is True


# ================================================================
# 5) B3：parsed 直排（_extract_bazi_info 解析层已转公历）行为断言
# ================================================================

class TestB3ParsedPath:
    def test_parsed_lunar_full_message_feeds_solar(self, tmp_path):
        """B3：消息带「农历1999年3月28日」→ 解析层已转公历（勘察结论：
        _extract_bazi_info 返回恒为公历，无 calendar 键，无需二转）→ 引擎
        实收 (1999,5,13,9,0)；直接喂公历绝不重复转换。"""
        from src.engines.bazi import BaziEngine
        rec = _RecordingEngine()
        rec.real = BaziEngine()
        h = _make_handler(tmp_path, "u-b3", engine=rec)

        out = h._handle_bazi(
            "农历1999年3月28日上午9点 吉林省长春市 男，帮我排个盘", "u-b3")
        assert rec.calls
        args = rec.calls[0][0]
        assert args[:5] == (1999, 5, 13, 9, 0), \
            f"引擎应收公历 (1999,5,13,9,0)，实收 {args[:5]}"
        assert args[5] == "吉林省长春市"
        assert rec.results[0].qiyun_desc.startswith("出生后2年4月")
        assert "排盘" in out or "命局" in out


# ================================================================
# 6) 同批同类点：_handle_calendar 缺四柱引擎补齐（lunar → 公历）
# ================================================================

class TestCalendarEngineFillSolarized:
    def test_calendar_fill_engine_receives_solar(self, tmp_path):
        """lunar 档案（persons-only，无 bazi）→ _handle_calendar 引擎补齐
        四柱：实收 (1999,5,13,9,0)（修复前实收 1999-3-28 → 断言必失败）；
        补齐后 bazi 为公历盘四柱。"""
        from src.engines.bazi import BaziEngine
        from src.storage.person_dao import PersonDAO
        rec = _RecordingEngine()
        rec.real = BaziEngine()
        h = _make_handler(tmp_path, "u-c1", engine=rec)
        pdao = PersonDAO(h.dao.db_path)
        _create_lunar_person(pdao, "u-c1")

        out = h._handle_calendar("看看今天的运势", "u-c1")
        assert rec.calls, "缺四柱必须触发引擎补齐"
        args = rec.calls[0][0]
        assert args[:5] == (1999, 5, 13, 9, 0), \
            f"引擎应收公历 (1999,5,13,9,0)，实收 {args[:5]}"
        # 无 api_key（h.llm=None）→ 固定兜底文案，补齐已完成可观测
        assert "日历" in out or "可用" in out


# ================================================================
# 7) Fix Round 1（评审 Important）：arch_raw 流画像层/L3 口径 = 引擎实收
#    公历日期（_save_bazi_records 画像层曾写 lunar 原始值无标记 → 分裂）
# ================================================================

class TestProfileLayerSolarAlignment:
    """评审修复（画像/引擎分裂）：B1 等 arch_raw（lunar 档案+消息补时）流下
    dao/persons/chart 保留 lunar 原始值+标记（存储铁律不动），但画像层
    bazi_info 与 L3 profile 事实是 LLM 上下文消费方（get_profile_summary →
    format_birth_line 渲染「出生:…」行），必须收到引擎实际排盘口径的**公历**
    日期（1999-5-13）——与 R2-5 _tool_bazi 画像层口径统一；修复前写 lunar
    原始 3/28 无标记 → 画像「出生:1999年3月28日…」与同上下文 5/13 公历盘
    （乙丑日）四柱自相矛盾。真实 UserMemory 装配（tmp 目录文件落盘，真实
    消费路径 format_birth_line/get_profile_summary/list_entries 全链路），
    非 mock——堵住 _make_handler memory_system=None 的测试盲区。"""

    def _attach_memory(self, h, tmp_path, uid):
        from src.memory.user_memory import UserMemory
        mem = UserMemory(base_dir=str(tmp_path / "mem"))
        h.memory_system = mem
        return mem

    def test_b1_lunar_archive_profile_layer_gets_solar(self, tmp_path):
        """B1（lunar 档案 3/28 + 消息补 hour=11 → arch_raw 流）：画像层
        bazi_info/L3 必须 = 公历 (1999,5,13)（修复前画像层写原始 3/28 无
        标记 → 断言必失败）；档案三处原值 + lunar 标记不动（复核）。"""
        from src.engines.bazi import BaziEngine
        from src.storage.person_dao import PersonDAO
        rec = _RecordingEngine()
        rec.real = BaziEngine()
        h = _make_handler(tmp_path, "u-pf1", engine=rec)
        mem = self._attach_memory(h, tmp_path, "u-pf1")
        pdao = PersonDAO(h.dao.db_path)
        _create_lunar_person(pdao, "u-pf1")

        h._handle_bazi("我的出生时辰是上午11点", "u-pf1")
        # 引擎确收公历 + 北极星 qiyun（基线复核，与既有 B1 测试同口径）
        assert rec.calls and rec.results[0].qiyun_desc == NORTH_STAR
        # 画像层 bazi_info：公历 5/13，无 lunar 标记（solar 口径值）
        data = mem._load("u-pf1")
        bi = data.get("bazi_info") or {}
        assert (bi.get("year"), bi.get("month"), bi.get("day")) == (1999, 5, 13), \
            f"画像层应收公历 (1999,5,13)，实收 " \
            f"{(bi.get('year'), bi.get('month'), bi.get('day'))}"
        assert bi.get("calendar") != "lunar"
        assert bi.get("hour") == 11 and bi.get("city") == "吉林省长春市"
        # 消费方渲染：summary 出生行 = 公历日期，不得再出现农历 3/28
        summary = mem.get_profile_summary("u-pf1")
        assert "1999年5月13日" in summary, summary
        assert "3月28日" not in summary, summary
        # L3 profile 事实条目：日期 = 公历（修复前写 3/28 与公历四柱自相矛盾）
        entries = mem.list_entries("u-pf1", entry_type="profile")
        assert entries and any("1999年5月13日" in (e.get("content") or "")
                               for e in entries), entries
        assert not any("1999年3月28日" in (e.get("content") or "")
                       for e in entries), entries
        # 档案三处原值不动（存储=原始输入事实源铁律复核）
        bazi = h.dao.get_user_bazi("u-pf1")
        assert (bazi["year"], bazi["month"], bazi["day"]) == (1999, 3, 28)
        assert bazi.get("calendar") == "lunar"
        p = pdao.get_default_person("u-pf1")
        assert (p["birth_year"], p["birth_month"], p["birth_day"]) == (1999, 3, 28)
        assert p["calendar"] == "lunar"

    def test_b1_solar_archive_profile_layer_unchanged(self, tmp_path):
        """回归：solar 档案流（无 arch_raw）画像层值不变——bazi_info 仍
        1990-5-20 原值、无 lunar 标记，summary 渲染 1990年5月20日。"""
        from src.engines.bazi import BaziEngine
        from src.storage.person_dao import PersonDAO
        rec = _RecordingEngine()
        rec.real = BaziEngine()
        h = _make_handler(tmp_path, "u-pf2", engine=rec)
        mem = self._attach_memory(h, tmp_path, "u-pf2")
        pdao = PersonDAO(h.dao.db_path)
        pdao.create_person(
            "u-pf2", name="我", relation="自己", is_default=True,
            birth={"gender": "男", "birth_year": 1990, "birth_month": 5,
                   "birth_day": 20, "birth_hour": 9, "birth_minute": 0,
                   "calendar": "solar", "city": "北京"})

        h._handle_bazi("我的出生时辰是下午3点", "u-pf2")
        assert rec.calls
        assert rec.calls[0][0][:5] == (1990, 5, 20, 15, 0)
        data = mem._load("u-pf2")
        bi = data.get("bazi_info") or {}
        assert (bi.get("year"), bi.get("month"), bi.get("day")) == (1990, 5, 20), bi
        assert bi.get("calendar") != "lunar"
        summary = mem.get_profile_summary("u-pf2")
        assert "1990年5月20日" in summary, summary
        assert "3月28日" not in summary
