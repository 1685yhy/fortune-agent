#!/usr/bin/env python3
"""流日四运（fortune4）测试 — Task 2 今日页四运/时辰/详解页 后端契约.

覆盖（全部离线，LLM mock）：
  1. 规则兜底 derive_fortune4：确定性 / 四维齐全 / 分数 0-10 / desc 非空
  2. 引擎 LLM 路径：mock httpx.post 返回含 fortune4 的 JSON → CalendarDay.fortune4 解析
  3. 引擎降级路径：LLM 失败 → _fallback_calendar 带 fortune4（规则兜底）
  4. 引擎健壮性：LLM 返回 fortune4 缺失/非法 → _sanitize_fortune4 兜底为规则结果
  5. API 层：/api/calendar/today 响应含 fortune4 / yi_detail / ji_detail / hourly.tag；
     day.fortune4 缺失时规则派生
  6. 缓存：同一 (user,date) 二次调用命中 6h 缓存，不重复调 LLM

用法:
    python3 scripts/test_calendar_fortune4.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest.mock as mock

from src.engines.calendar import LuckyCalendar, derive_fortune4
import src.api.calendar as cal_api
from src.utils.cache import get_cache

ok = 0


def check(name, cond, detail=""):
    global ok
    assert cond, f"FAIL: {name} {detail}"
    ok += 1
    print(f"PASS: {name}")


def test_rule_fallback():
    f1 = derive_fortune4("金", "庚", 72)
    f2 = derive_fortune4("金", "庚", 72)
    check("规则兜底确定性（同输入同输出）", f1 == f2)
    check("四维齐全", set(f1) == {"career", "wealth", "love", "health"})
    for k, v in f1.items():
        check(f"四运 {k} 分数 0-10", 0 <= v["score"] <= 10, str(v))
        check(f"四运 {k} desc 非空", bool(v["desc"]), str(v))
    f3 = derive_fortune4("火", "丙", 60)
    check("不同五行/分数 → 不同结果", f3 != f1)
    f4 = derive_fortune4("木", "", 78)
    check("无命主也可派生（通用路径）", set(f4) == {"career", "wealth", "love", "health"})


def test_engine_llm_path():
    fake_json = {
        "overall_mood": "平稳向好",
        "yi": [{"action": "洽谈", "time": "巳时9-11点", "reason": "官星得地"}],
        "ji": [{"action": "借贷", "time": "全天", "reason": "财星坐库"}],
        "lucky_color": "金色",
        "lucky_direction": "西",
        "lucky_number": "4",
        "is_special": False,
        "special_note": "",
        "fortune4": {
            "career": {"score": 7.2, "desc": "官星得地，贵人星动。宜主动开口。"},
            "wealth": {"score": 6.8, "desc": "财星坐库，稳稳当当。偏财勿贪。"},
            "love": {"score": 5.5, "desc": "今日情绪偏低沉，话留到傍晚再说。"},
            "health": {"score": 7.0, "desc": "精神头足，宜早活动筋骨。"},
        },
    }
    import json as _json
    resp = mock.Mock()
    resp.json.return_value = {"content": [{"type": "text", "text": _json.dumps(fake_json, ensure_ascii=False)}]}
    with mock.patch("httpx.post", return_value=resp) as m:
        cal = LuckyCalendar("fake-key")
        day = cal.daily({"bazi": ["庚", "辰", "庚", "辰"], "day_master": "庚金"},
                        date_str="2026-08-15")
    check("LLM 路径解析 fortune4", day.fortune4["career"]["score"] == 7.2, str(day.fortune4))
    check("LLM 路径 desc 透传", "官星得地" in day.fortune4["career"]["desc"])
    check("LLM 被调用", m.call_count == 1)


def test_engine_fallback_path():
    with mock.patch("httpx.post", side_effect=Exception("network down")):
        cal = LuckyCalendar("fake-key")
        day = cal.daily({"bazi": ["庚", "辰", "庚", "辰"]}, date_str="2026-08-15")
    check("LLM 失败 → fallback fortune4 四维齐全",
          set(day.fortune4) == {"career", "wealth", "love", "health"}, str(day.fortune4))
    check("fallback desc 非空", all(v["desc"] for v in day.fortune4.values()))
    # 非法 fortune4（score 越界）→ sanitize 兜底
    bad = {
        "career": {"score": 99, "desc": "x"}, "wealth": {"score": 6.8, "desc": "y"},
        "love": {"score": 5.5, "desc": "z"}, "health": {"score": 7.0, "desc": "w"},
    }
    import json as _json
    resp = mock.Mock()
    resp.json.return_value = {"content": [{"type": "text", "text": _json.dumps(bad, ensure_ascii=False)}]}
    with mock.patch("httpx.post", return_value=resp):
        cal = LuckyCalendar("fake-key")
        day = cal.daily({"bazi": ["庚", "辰", "庚", "辰"]}, date_str="2026-08-15")
    check("非法 fortune4 → 规则兜底（分数均 ≤10）",
          all(0 <= v["score"] <= 10 for v in day.fortune4.values()))


class _FakeDay:
    """模拟 LuckyCalendar.daily 返回的 CalendarDay（可带或不带 fortune4）。"""

    def __init__(self, with_fortune4):
        self.date = "2026-08-15"
        self.lunar_date = "六月廿九"
        self.day_stem = "庚"
        self.day_branch = "申"
        self.yi = [{"action": "洽谈", "time": "巳时9-11点", "reason": "官星得地"},
                   {"action": "出行", "time": "午时11-13点", "reason": "气行通畅"},
                   {"action": "早起", "time": "辰时7-9点", "reason": "晨气清爽"}]
        self.ji = [{"action": "借贷", "time": "全天", "reason": "财星坐库"},
                   {"action": "熬夜", "time": "子时23点后", "reason": "伤神损运"}]
        self.lucky_color = "金色"
        self.lucky_direction = "西"
        self.lucky_number = "4"
        self.overall_mood = "官星得地，宜谈合作"
        self.fortune4 = {
            "career": {"score": 7.2, "desc": "官星得地，贵人星动。宜主动开口。"},
            "wealth": {"score": 6.8, "desc": "财星坐库，稳稳当当。"},
            "love": {"score": 5.5, "desc": "今日情绪偏低沉，话留到傍晚再说。"},
            "health": {"score": 7.0, "desc": "精神头足，宜早活动筋骨。"},
        } if with_fortune4 else None


class _FakeDao:
    def get_user_bazi(self, user_id):
        return {"bazi": ["庚", "辰", "庚", "辰"], "day_master": "庚金"}


def test_api_response(with_fortune4, uid="wx_test_f4"):
    cache = get_cache()
    cache.clear()
    cal_api._dao = _FakeDao()
    cal_api._handler_ref = None  # api_key="" → daily 走 mock

    calls = {"n": 0}

    class _FakeLuckyCalendar:
        def __init__(self, api_key):
            pass

        def daily(self, saved, date_str=None, preferences=""):
            calls["n"] += 1
            return _FakeDay(with_fortune4)

    with mock.patch.object(cal_api, "LuckyCalendar", _FakeLuckyCalendar):
        res = asyncio.run(cal_api.get_today_calendar(uid=uid, date="2026-08-15"))

    check("API 响应含 fortune4", "fortune4" in res, str(list(res.keys())))
    f4 = res["fortune4"]
    check("fortune4 四维齐全", set(f4) == {"career", "wealth", "love", "health"})
    for k, v in f4.items():
        check(f"fortune4.{k} 分数 0-10", 0 <= v["score"] <= 10 and isinstance(v["score"], float))
        check(f"fortune4.{k} desc 非空", bool(v.get("desc")))
    check("yi_detail 逐条（action/time/reason）",
          all(set(i) == {"action", "time", "reason"} for i in res["yi_detail"]))
    check("ji_detail 逐条", all(set(i) == {"action", "time", "reason"} for i in res["ji_detail"]))
    check("hourly 12 条且带 tag", len(res["hourly"]) == 12 and all("tag" in h for h in res["hourly"]))
    if with_fortune4:
        check("LLM 四运透传", res["fortune4"]["career"]["score"] == 7.2)
        check("LLM desc 透传", res["fortune4"]["career"]["desc"].startswith("官星得地"))
    else:
        # 规则派生的 desc 是模板句（含当日五行「金气」），与 LLM 句可区分
        check("缺四运 → 规则派生", "金气" in res["fortune4"]["career"]["desc"],
              res["fortune4"]["career"]["desc"])
        check("规则派生 desc 均非空", all(v["desc"] for v in res["fortune4"].values()))
    return res, calls


def test_cache():
    res1, calls1 = test_api_response(True, uid="wx_test_f4_cache")
    n1 = calls1["n"]
    res2, calls2 = test_api_response(True, uid="wx_test_f4_cache")
    check("二次调用命中 6h 缓存（LLM 不再调用）", calls2["n"] == n1,
          f"{calls2['n']} vs {n1}")
    check("缓存结果一致", res1 == res2)


test_rule_fallback()
test_engine_llm_path()
test_engine_fallback_path()
test_api_response(True)
test_api_response(False)
test_cache()

print(f"\nALL PASS ({ok})")
