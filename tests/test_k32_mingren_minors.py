# -*- coding: utf-8 -*-
"""k32（A15）名人命盘三项 Minor：

1. 空亡改读引擎字段 `kongwang_day`（旧代码在 api 层手推旬头，与引擎 XUN_KONG
   表重复实现——本文件既锁「输出取自引擎字段」，也锁两实现 60 甲子逐一等价）；
2. `_DEFAULT_HOUR` 死常量守卫 + 时辰解析失败不静默呈现假时辰（旧代码脏
   shichen + 无 hour → 静默按午时呈现且无 hour_note）；
3. timeline 事件 `idx` 组内唯一（前端 wx:key 由 year 改 idx 的稳定唯一键；
   同一年多条事件真实存在，miniprogram 侧接线留 k34）。

隔离：`_serialize_chart` 直调 + 真实引擎（纯计算，无网络/无落库）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402

from src.api import mingren as mingren_api  # noqa: E402
from src.engines.bazi import DIZHI, TIANGAN, XUN_KONG, BaziEngine  # noqa: E402

ZUOZONGTANG = (1812, 11, 10, {"shichen": "寅时", "gender": "男"})


@pytest.fixture(autouse=True)
def _engine():
    mingren_api.setup(member_dao=None, data_path=str(mingren_api._DATA_FILE),
                      engine=BaziEngine())
    yield


# ─────────────────────────── 1. 空亡 ───────────────────────────

def test_a15_kong_equals_engine_kongwang_day():
    """各柱 kong = 地支 ∈ 引擎 kongwang_day（日柱旬空亡）——输出与引擎字段一致。"""
    chart = mingren_api._serialize_chart(*ZUOZONGTANG)
    r = BaziEngine().calculate(1812, 11, 10, 4, 20, "北京", "男")
    assert len(r.kongwang_day) == 2, "引擎 kongwang_day 恒为两支"
    kong = set(r.kongwang_day)
    assert [p["kong"] for p in chart["pillars"]] == \
        [gz[1] if gz[1] in kong else "" for gz in r.bazi]
    assert [p["kong"] for p in chart["pillars"]] == ["", "", "", "寅"]


def test_a15_legacy_formula_equivalent_all_60():
    """行为等价性证据：旧手推公式（旬头 idx=(zhi-gan)%12，取前两支）与引擎
    XUN_KONG 表在 60 甲子上逐一相等 → 改读引擎字段对存量 33 张命盘零变化。"""
    for gz, kong in XUN_KONG.items():
        dg = (DIZHI.index(gz[1]) - TIANGAN.index(gz[0])) % 12
        assert {DIZHI[(dg - 2) % 12], DIZHI[(dg - 1) % 12]} == set(kong), gz


def test_a15_kong_follows_engine_field_not_hand_formula(monkeypatch):
    """单一事实源：输出必须取自引擎字段——引擎值异于手推公式时也须跟随。

    手推公式下日柱自身地支永不在本旬空亡 → 恒空串；本用例把 kongwang_day
    改成「日柱地支 + 另一支」，输出日柱 kong 必须随之非空（旧代码必失败）。
    """
    engine = mingren_api._bazi_engine
    real = engine.calculate
    holder = {}

    def _spy(*a, **kw):
        r = real(*a, **kw)
        holder["day_zhi"] = r.bazi[2][1]
        r.kongwang_day = r.bazi[2][1] + "子"
        return r

    monkeypatch.setattr(engine, "calculate", _spy)
    chart = mingren_api._serialize_chart(*ZUOZONGTANG)
    assert chart["pillars"][2]["kong"] == holder["day_zhi"], \
        "日柱 kong 必须跟随引擎 kongwang_day（旧手推实现恒为 ''）"


def test_a15_all_library_charts_kong_from_engine():
    """存量 33 张命盘逐一：kong = 地支 ∈ 引擎 XUN_KONG[日柱]（引擎表权威口径）。"""
    birth = mingren_api._load_birth()
    assert len(birth) >= 20
    for name in birth:
        chart = mingren_api._chart_of(name)
        assert chart, name
        kong = set(XUN_KONG[chart["bazi"][2]])
        assert [p["kong"] for p in chart["pillars"]] == \
            [gz[1] if gz[1] in kong else "" for gz in chart["bazi"]], name


# ──────────────────────── 2. 死常量守卫 + 假时辰 ────────────────────────

def test_a15_default_hour_constant_alive_and_consistent():
    """_DEFAULT_HOUR 不再是死常量，且与 birth_contract.normalize_hour(None) 同口径。"""
    from src.api.birth_contract import normalize_hour
    assert mingren_api._DEFAULT_HOUR == 12
    assert normalize_hour(None) == mingren_api._DEFAULT_HOUR
    assert mingren_api.SHICHEN_NAME[mingren_api._DEFAULT_HOUR] == "午时"


def test_a15_missing_shichen_default_noon_with_note():
    """时辰缺失 → 默认午时 + hour_note（既有口径不回退）。"""
    chart = mingren_api._serialize_chart(1700, 1, 1, {"gender": "男"})
    assert chart["hour_note"] == "时辰无考，以午时推演"
    assert chart["meta"]["shichen"] == "午时"


def test_a15_unparseable_shichen_not_silently_noon():
    """脏 shichen（解析失败）且无 hour → 不得静默呈现假时辰（必须标注）。

    旧代码：`shichen is None and hour is None` 才标注 → "寅时初" 这类脏值
    直接按午时呈现且无 hour_note（真机=假时辰）。
    """
    for dirty in ("寅时初", "午后", "子時", "上午"):
        chart = mingren_api._serialize_chart(1700, 1, 1,
                                             {"shichen": dirty, "gender": "男"})
        assert chart["hour_note"] == "时辰无考，以午时推演", dirty
        assert chart["meta"]["shichen"] == "午时", dirty


def test_a15_valid_shichen_no_note():
    """可解析时辰（含单字「子」形态）→ 无 hour_note，按该时辰排（不回退）。"""
    chart = mingren_api._serialize_chart(*ZUOZONGTANG)
    assert "hour_note" not in chart
    assert chart["meta"]["shichen"] == "寅时"


# ──────────────────────── 3. timeline 唯一键 ────────────────────────

def test_a15_timeline_events_unique_idx():
    """每个大运组内事件 idx 唯一（前端 wx:key 可由 year 改 idx）。"""
    birth = mingren_api._load_birth()
    checked = 0
    for name in birth:
        chart = mingren_api._chart_of(name)
        item = mingren_api._load_data().get(name) or {}
        timeline = mingren_api._build_timeline(item, chart)
        for g in timeline:
            idxs = [e["idx"] for e in g["events"]]
            assert len(idxs) == len(set(idxs)), (name, g["label"])
            assert all(isinstance(i, int) for i in idxs), name
            assert all("year" in e and "event" in e for e in g["events"]), name
            checked += len(idxs)
    assert checked > 0, "测试库应有事件可校验"


def test_a15_timeline_duplicate_year_real_case():
    """真实重复键案例（旧 wx:key='year' 会告警）：忽必烈同年两条事件 →
    year 重复，但同组内 idx 唯一。"""
    chart = mingren_api._chart_of("忽必烈")
    assert chart
    item = mingren_api._load_data().get("忽必烈")
    timeline = mingren_api._build_timeline(item, chart)
    events = [e for g in timeline for e in g["events"]]
    assert len(events) > len({e["year"] for e in events}), \
        "库内确有同年多事件（重复 year）"
    for g in timeline:
        idxs = [e["idx"] for e in g["events"]]
        assert len(idxs) == len(set(idxs)), g["label"]
