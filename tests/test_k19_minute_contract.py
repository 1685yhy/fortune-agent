# -*- coding: utf-8 -*-
"""k19：表单分钟级精度 — REST 生辰契约测试（2026-09-10）。

后端 birth_enc/users.bazi_info/chart_records 的 minute 早已全链路支持；
缺口只在 REST 边界 normalize_hour：0-11 恒被当「时辰序号」→ 钟表时间
10:55（birthHour=10 + minute=55）会被错映射成 戌时 21 点。k19 扩展：
minute>0 或显式 birthClock 声明 = 钟表时间信号 → 0-23 时钟小时直通引擎
（真太阳时校准需要真实时钟分钟）。旧调用方（不带 minute/birthClock）→
行为零变化。隔离：纯函数 + BaziInput 模型，零网络零 LLM。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

from src.api.birth_contract import normalize_hour  # noqa: E402


# ── normalize_hour 纯函数 ──

def test_normalize_hour_legacy_semantics_unchanged():
    """旧调用方（不带 minute/birthClock）：0-11 时辰序号 → 代表整点不变。"""
    assert normalize_hour(None) == 12            # 缺省午时中点
    assert normalize_hour(6) == 12               # 午时序号 → 12（校准锚点口径）
    assert normalize_hour(0) == 23               # 子时 → 23 晚子时
    assert normalize_hour(10) == 19              # 序号10(戌) → 19
    assert normalize_hour(13) == 13              # 12-23 时钟原样
    assert normalize_hour(5, 0) == 9             # minute=0 非信号 → 序号语义
    assert normalize_hour(5, None) == 9


def test_normalize_hour_clock_minute_passthrough():
    """minute>0 = 钟表时间信号 → 0-11 时钟小时直通（10:55 场景）。"""
    assert normalize_hour(10, 55) == 10          # 10:55 → 时钟 10（不再戌时）
    assert normalize_hour(5, 30) == 5            # 05:30 → 时钟 5
    assert normalize_hour(0, 30) == 0            # 00:30 → 时钟 0（子时）
    assert normalize_hour(11, 1) == 11           # 11:01 → 午时边界直通
    assert normalize_hour(23, 30) == 23
    assert normalize_hour(17, 0, True) == 17


def test_normalize_hour_birthclock_passthrough_even_zero_minute():
    """显式 birthClock=True（10 点整场景）→ minute=0 也按时钟直通。"""
    assert normalize_hour(10, 0, True) == 10
    assert normalize_hour(0, 0, True) == 0
    assert normalize_hour(6, 0, True) == 6       # 序号6=午 → 时钟 6（声明后）
    assert normalize_hour(None, 0, True) == 12   # 无 hour 仍缺省午时
    assert normalize_hour(99, 0, True) == 23     # 越界仍夹紧


# ── _resolve_person（paipan/hehun 共用契约解析） ──

def test_resolve_person_clock_row(tmp_path):
    """BaziInput(birthHour=10+minute=55) → 时钟小时 10 直通引擎入参。"""
    from src.api.hehun import BaziInput, _resolve_person
    p = _resolve_person(BaziInput(birthYear=1999, birthMonth=3, birthDay=28,
                                  birthHour=10, minute=55, city="上海",
                                  gender="女"))
    assert p.hour == 10 and p.minute == 55
    assert p.year == 1999


def test_resolve_person_birthclock_declared(tmp_path):
    """birthClock=True + birthHour=10 + minute=0（10 点整）→ 时钟 10。"""
    from src.api.hehun import BaziInput, _resolve_person
    p = _resolve_person(BaziInput(birthYear=1999, birthMonth=3, birthDay=28,
                                  birthHour=10, minute=0, birthClock=True,
                                  gender="女"))
    assert p.hour == 10 and p.minute == 0


def test_resolve_person_legacy_index_unchanged(tmp_path):
    """旧调用方（无 birthClock/minute）：0-11 仍为时辰序号语义。"""
    from src.api.hehun import BaziInput, _resolve_person
    p = _resolve_person(BaziInput(birthYear=1999, birthMonth=3, birthDay=28,
                                  birthHour=10, gender="女"))
    assert p.hour == 19                        # 序号 10 = 戌时代表 19 点
    p2 = _resolve_person(BaziInput(birthYear=1999, birthMonth=3, birthDay=28,
                                   birthHour=6, gender="女"))
    assert p2.hour == 12                       # 午时序号 → 12（校准口径不变）
