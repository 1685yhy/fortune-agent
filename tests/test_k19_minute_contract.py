# -*- coding: utf-8 -*-
"""k19：表单分钟级精度 — REST 生辰契约测试（2026-09-10，k19 review 修正版）。

后端 birth_enc/users.bazi_info/chart_records 的 minute 早已全链路支持；
REST 边界缺口：0-11 恒被当「时辰序号」，钟表时间 10:55（birthHour=10 +
birthClock 声明）需要直通时钟小时。

k19 review 修正（regression 铁证：时柱壬午→己卯）：**只认显式
clock_signal/birthClock 声明**；minute 不携带 hour 语义（旧 BaziInput 契约
birthHour 0-11=时辰序号 + minute=时辰内偏置，如 6+25=午时 25 分，必须保留）。
新前端钟表档必带 birthClock:true（k19 前端已实现）；非钟表调用方不带 →
行为零变化。隔离：纯函数 + BaziInput 模型，零网络零 LLM。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

from src.api.birth_contract import normalize_hour  # noqa: E402


# ── normalize_hour 纯函数 ──

def test_normalize_hour_legacy_semantics_unchanged():
    """旧调用方（不带 clock_signal）：0-11 时辰序号 → 代表整点不变。"""
    assert normalize_hour(None) == 12            # 缺省午时中点
    assert normalize_hour(6) == 12               # 午时序号 → 12（校准锚点口径）
    assert normalize_hour(0) == 23               # 子时 → 23 晚子时
    assert normalize_hour(10) == 19              # 序号10(戌) → 19
    assert normalize_hour(13) == 13              # 12-23 时钟原样
    assert normalize_hour(5) == 9                # 序号5(巳) → 9


def test_normalize_hour_minute_offset_keeps_idx_semantics():
    """review 回归防护：minute=时辰内偏置（旧契约）不影响 hour 判定——
    问真锚点 birthHour=6 + minute=25（午时 25 分）→ 仍按序号 6 → 12。
    签名只收 (hour, clock_signal)——minute 不再可能被误当信号（k19 review
    后 m>0 兜底已删），偏置随 BaziInput.minute 原样流经引擎。"""
    assert normalize_hour(6) == 12
    assert normalize_hour(5) == 9


def test_normalize_hour_clock_signal_passthrough():
    """显式 clock_signal=True = 钟表时间声明 → 0-11 时钟小时直通（10:55）。"""
    assert normalize_hour(10, True) == 10        # 10:55 → 时钟 10
    assert normalize_hour(5, True) == 5          # 05:30 → 时钟 5
    assert normalize_hour(0, True) == 0          # 00:30 → 时钟 0（子时）
    assert normalize_hour(11, True) == 11        # 11:01 → 午时边界直通
    assert normalize_hour(23, True) == 23
    assert normalize_hour(6, True) == 6          # 序号6=午 → 声明后时钟 6
    assert normalize_hour(10, True) == 10        # 声明后 minute 0 也直通
    assert normalize_hour(None, True) == 12      # 无 hour 仍缺省午时
    assert normalize_hour(99, True) == 23        # 越界仍夹紧
    # 同一数值只认声明：无声明=序号语义（午 12），有声明=时钟 6
    assert normalize_hour(6) != normalize_hour(6, True)


# ── _resolve_person（paipan/hehun 共用契约解析） ──

def test_resolve_person_clock_row():
    """BaziInput(birthHour=10 + birthClock=True + minute=55) → 时钟 10。"""
    from src.api.hehun import BaziInput, _resolve_person
    p = _resolve_person(BaziInput(birthYear=1999, birthMonth=3, birthDay=28,
                                  birthHour=10, minute=55, birthClock=True,
                                  city="上海", gender="女"))
    assert p.hour == 10 and p.minute == 55
    assert p.year == 1999


def test_resolve_person_birthclock_declared_zero_minute():
    """birthClock=True + birthHour=10 + minute=0（10 点整）→ 时钟 10。"""
    from src.api.hehun import BaziInput, _resolve_person
    p = _resolve_person(BaziInput(birthYear=1999, birthMonth=3, birthDay=28,
                                  birthHour=10, minute=0, birthClock=True,
                                  gender="女"))
    assert p.hour == 10 and p.minute == 0


def test_resolve_person_legacy_index_minute_offset_kept():
    """旧契约回归防护：birthHour=6 + minute=25（问真锚点 YAN 盘）无
    birthClock → 序号语义 hour=12、minute=25 原样随附（时柱壬午不再变己卯）。"""
    from src.api.hehun import BaziInput, _resolve_person
    p = _resolve_person(BaziInput(birthYear=1999, birthMonth=3, birthDay=28,
                                  birthHour=6, minute=25, gender="女"))
    assert p.hour == 12 and p.minute == 25
    p2 = _resolve_person(BaziInput(birthYear=1999, birthMonth=3, birthDay=28,
                                   birthHour=10, minute=25, gender="女"))
    assert p2.hour == 19                        # 序号 10 = 戌时代表 19 点
    assert p2.minute == 25


def test_resolve_person_legacy_plain():
    """旧调用方（无 birthClock/minute）行为零变化。"""
    from src.api.hehun import BaziInput, _resolve_person
    p = _resolve_person(BaziInput(birthYear=1999, birthMonth=3, birthDay=28,
                                  birthHour=6, gender="女"))
    assert p.hour == 12 and p.minute == 0
