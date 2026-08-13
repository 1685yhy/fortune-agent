"""择吉日 Task 2 — 对话工具增强（_tool_zeri + 意图判定 + _extract_window）测试。

覆盖（hermetic·离线, 无 LLM/无网络, mock select_lucky_days）:
1. "下个月搬家,帮我选个日子" → 调引擎: scene=搬家, 窗口=下月自然月; 结果含 3 卡结构化信息
   （日期/农历/宜/忌/吉时/喜神/财神/理由/总分）+ 选择问句 + /pages/zeri/zeri 深链
2. "帮我看看哪天适合结婚" → scene=嫁娶, 窗口=未来30天
3. "今天适合干嘛"(无场景词) → 澄清 ToolResult, 不调引擎, 不扣额度
4. "我想搬家"(场景词无意图词) → 澄清反问, 不调引擎, 不扣额度
5. "8月20日开业" → 窗口=8/20±7 天
6. mock 引擎断言被调参数: scene/window/user_bazi(shengxiao=马, day_gan, month_zhi)
7. 额度扣减断言: 真实调用扣 1 次; 额度用完 → 不调引擎 + 额度提示
8. suggest_wider=True → 结果含扩窗提示
9. _execute_tool_call 分发路径（handler 工具链入口）
10. _extract_window 纯函数: 下个月/下周/具体日期/无信息/8月20号
11. 回归: _extract_purpose 词表扩充(提车/签约) + _handle_zeri 旧路径不受影响

用法:
    cd /mnt/e/fortune-agent && .venv/bin/python3 scripts/test_zeri_tool.py
退出码: 0 = 全部通过; 1 = 有失败项
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.engines.zeri import LuckyDayCard
from src.bot.handler import MessageHandler

_PASS = 0
_FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {name}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {name} {detail}")


# ---------------------------------------------------------------------------
# 桩件
# ---------------------------------------------------------------------------

class FakeZeriEngine:
    """记录 select_lucky_days 调用参数的桩引擎（不跑真实历法计算）。"""

    def __init__(self):
        self.calls = []
        self.select_calls = []
        self.result = None

    def select_lucky_days(self, scene=None, start_date=None, end_date=None,
                          user_bazi=None, exclude_dates=None, prefer_weekend=False):
        self.calls.append(dict(
            scene=scene, start_date=start_date, end_date=end_date,
            user_bazi=user_bazi, exclude_dates=exclude_dates,
            prefer_weekend=prefer_weekend))
        return self.result

    def select(self, year, month, day, purpose=""):
        """旧单日接口桩（_handle_zeri 旧路径回归用）。"""
        from types import SimpleNamespace
        self.select_calls.append((year, month, day, purpose))
        return SimpleNamespace(
            jianchu="成", ershibaxiu="张", xiu_jixiong="吉",
            yi=["入宅"], ji=["开市"], chong="冲马(午)", overall="吉")


class FakeDAO:
    def __init__(self, bazi=None):
        self.bazi = bazi
        self.calls = 0

    def get_user_bazi(self, user_id):
        self.calls += 1
        return self.bazi

    def save_consultation(self, *args, **kwargs):
        return None


class FakeMemberDAO:
    def __init__(self, limit=500, used=0):
        self.limit = limit
        self.used = used
        self.use_count = 0

    def get_membership(self, user_id):
        return {"queries_limit": self.limit, "queries_used": self.used}

    def use_quota(self, user_id):
        self.use_count += 1
        self.used += 1


def make_card(d="2026-09-06", lunar="农历七月廿五 癸未日 星期日",
              yi=("入宅", "移徙", "安床"), ji=("开市", "安葬"),
              jishi="巳时(9-11点)", xi="正南", cai="西南",
              scene=40, personal=24, practical=10, total=74, reason="成日值日"):
    return LuckyDayCard(
        date=d, lunar_text=lunar, yi=list(yi), ji=list(ji), jishi=jishi,
        xi_fangwei=xi, cai_fangwei=cai, scene_score=scene,
        personal_score=personal, practical_score=practical,
        total=total, reason_source=reason)


def make_result(n_cards=3, suggest_wider=False):
    cards = [
        make_card(d="2026-09-06", lunar="农历七月廿五 癸未日 星期日",
                  reason="成日值日", total=74),
        make_card(d="2026-09-13", lunar="农历八月初二 庚寅日 星期日",
                  reason="周末宜搬家", total=70),
        make_card(d="2026-09-02", lunar="农历七月廿一 己卯日 星期三",
                  reason="宜入宅", total=66),
    ][:n_cards]
    return {
        "cards": cards,
        "scanned": 30,
        "suggest_wider": suggest_wider,
        "reason": "合格吉日不足3天（本窗口30天），建议扩大日期范围或调整偏好"
                  if suggest_wider else None,
    }


def build_handler(engine=None, bazi=None, member=None):
    """用 object.__new__ 构造轻量 handler, 只挂 _tool_zeri 需要的属性（离线）。"""
    h = object.__new__(MessageHandler)
    h.zeri_engine = engine or FakeZeriEngine()
    h.dao = FakeDAO(bazi=bazi)
    h.member_dao = member or FakeMemberDAO()
    return h


# 期望窗口计算（与 spec 一致, 测试内独立实现）

def next_month_window():
    t = date.today()
    y, m = (t.year + 1, 1) if t.month == 12 else (t.year, t.month + 1)
    start = date(y, m, 1)
    end = date(y + 1, 1, 1) - timedelta(days=1) if m == 12 \
        else date(y, m + 1, 1) - timedelta(days=1)
    return start.isoformat(), end.isoformat()


def next_week_window():
    t = date.today()
    start = t + timedelta(days=(7 - t.weekday()) % 7 or 7)
    end = start + timedelta(days=6)
    return start.isoformat(), end.isoformat()


def month_day_window(mon, day):
    t = date(date.today().year, mon, day)
    if t < date.today():
        t = date(t.year + 1, mon, day)
    return (t - timedelta(days=7)).isoformat(), (t + timedelta(days=7)).isoformat()


# ---------------------------------------------------------------------------
# 1. 下个月搬家,帮我选个日子 → 调引擎 + 窗口=下月自然月 + 3卡信息
# ---------------------------------------------------------------------------
print("== 1. '下个月搬家,帮我选个日子' ==")
eng = FakeZeriEngine()
eng.result = make_result()
h = build_handler(engine=eng)
r = h._tool_zeri("下个月搬家,帮我选个日子", "u1")
check("调引擎 1 次", len(eng.calls) == 1, f"calls={len(eng.calls)}")
if eng.calls:
    call = eng.calls[0]
    exp_s, exp_e = next_month_window()
    check("scene=搬家", call["scene"] == "搬家", f"got {call['scene']}")
    check("窗口=下月自然月", call["start_date"] == exp_s and call["end_date"] == exp_e,
          f"got {call['start_date']}~{call['end_date']} want {exp_s}~{exp_e}")
    check("exclude_dates=None", call["exclude_dates"] is None)
    check("prefer_weekend=True(周末加分)", call["prefer_weekend"] is True)
check("ok=True", r.ok is True, f"ok={r.ok}")
for kw in ["搬家", "2026-09-06", "农历七月廿五 癸未日 星期日", "宜：入宅、移徙、安床",
           "忌：开市、安葬", "吉时：巳时(9-11点)", "喜神：正南", "财神：西南",
           "理由：成日值日（总分74）", "您选哪一个", "/pages/zeri/zeri"]:
    check(f"结果含「{kw}」", kw in r.text, f"text={r.text[:400]}")
check("3 卡都在结果中", all(c.date in r.text for c in eng.result["cards"]),
      f"text={r.text[:400]}")

# ---------------------------------------------------------------------------
# 2. 帮我看看哪天适合结婚 → 嫁娶 + 未来30天
# ---------------------------------------------------------------------------
print("== 2. '帮我看看哪天适合结婚' ==")
eng = FakeZeriEngine()
eng.result = make_result()
h = build_handler(engine=eng)
r = h._tool_zeri("帮我看看哪天适合结婚", "u2")
check("调引擎 1 次", len(eng.calls) == 1, f"calls={len(eng.calls)}")
if eng.calls:
    call = eng.calls[0]
    today = date.today()
    exp_s, exp_e = today.isoformat(), (today + timedelta(days=30)).isoformat()
    check("scene=嫁娶", call["scene"] == "嫁娶", f"got {call['scene']}")
    check("窗口=未来30天", call["start_date"] == exp_s and call["end_date"] == exp_e,
          f"got {call['start_date']}~{call['end_date']} want {exp_s}~{exp_e}")
check("ok=True", r.ok is True, f"ok={r.ok}")

# ---------------------------------------------------------------------------
# 3. 无场景词 → 澄清, 不调引擎, 不扣额度
# ---------------------------------------------------------------------------
print("== 3. '今天适合干嘛'(无场景词) ==")
eng = FakeZeriEngine()
eng.result = make_result()
member = FakeMemberDAO(limit=3, used=1)
h = build_handler(engine=eng, member=member)
r = h._tool_zeri("今天适合干嘛", "u3")
check("不调引擎", len(eng.calls) == 0, f"calls={len(eng.calls)}")
check("不扣额度", member.use_count == 0, f"use_count={member.use_count}")
check("澄清文案", "哪件事" in r.text and "搬家/嫁娶/开业/出行/提车/签约" in r.text,
      f"text={r.text}")
check("ok=False", r.ok is False, f"ok={r.ok}")

# ---------------------------------------------------------------------------
# 4. 场景词无意图词 → 澄清反问, 不调引擎, 不扣额度
# ---------------------------------------------------------------------------
print("== 4. '我想搬家'(无意图词) ==")
eng = FakeZeriEngine()
eng.result = make_result()
member = FakeMemberDAO(limit=3, used=1)
h = build_handler(engine=eng, member=member)
r = h._tool_zeri("我想搬家", "u4")
check("不调引擎", len(eng.calls) == 0, f"calls={len(eng.calls)}")
check("不扣额度", member.use_count == 0, f"use_count={member.use_count}")
check("澄清反问含场景", "搬家" in r.text and "选日子" in r.text, f"text={r.text}")
check("ok=False", r.ok is False, f"ok={r.ok}")

# 非择日闲聊: 场景+意图都不全 → 澄清而非报错
eng2 = FakeZeriEngine()
eng2.result = make_result()
r2 = build_handler(engine=eng2)._tool_zeri("今天天气怎么样", "u4b")
check("纯闲聊不调引擎", len(eng2.calls) == 0 and r2.ok is False)

# ---------------------------------------------------------------------------
# 5. 具体日期 "8月20日开业" → 窗口=8/20±7
# ---------------------------------------------------------------------------
print("== 5. '8月20日开业' ==")
eng = FakeZeriEngine()
eng.result = make_result()
h = build_handler(engine=eng)
r = h._tool_zeri("8月20日开业", "u5")
check("调引擎 1 次", len(eng.calls) == 1, f"calls={len(eng.calls)}")
if eng.calls:
    call = eng.calls[0]
    exp_s, exp_e = month_day_window(8, 20)
    check("scene=开业", call["scene"] == "开业", f"got {call['scene']}")
    check("窗口=8/20±7天", call["start_date"] == exp_s and call["end_date"] == exp_e,
          f"got {call['start_date']}~{call['end_date']} want {exp_s}~{exp_e}")

# 带年份全日期
eng = FakeZeriEngine()
eng.result = make_result()
h = build_handler(engine=eng)
r = h._tool_zeri("2026年10月1日 签合同 挑个时间", "u5b")
check("带年全日期调引擎", len(eng.calls) == 1, f"calls={len(eng.calls)}")
if eng.calls:
    check("scene=签约", eng.calls[0]["scene"] == "签约", f"got {eng.calls[0]['scene']}")
    check("窗口=2026-10-01±7", eng.calls[0]["start_date"] == "2026-09-24"
          and eng.calls[0]["end_date"] == "2026-10-08",
          f"got {eng.calls[0]['start_date']}~{eng.calls[0]['end_date']}")

# ---------------------------------------------------------------------------
# 6. user_bazi 映射: bazi=["庚午","辛巳","乙酉","甲申"] → 马/乙/巳
# ---------------------------------------------------------------------------
print("== 6. user_bazi 映射 ==")
eng = FakeZeriEngine()
eng.result = make_result()
h = build_handler(engine=eng, bazi={"year": 1990, "bazi": ["庚午", "辛巳", "乙酉", "甲申"]})
h._tool_zeri("下个月搬家,帮我选个日子", "u6")
check("调引擎 1 次", len(eng.calls) == 1, f"calls={len(eng.calls)}")
if eng.calls:
    ub = eng.calls[0]["user_bazi"]
    check("shengxiao=马(年支午)", ub and ub.get("shengxiao") == "马", f"got {ub}")
    check("day_gan=乙(日柱天干)", ub and ub.get("day_gan") == "乙", f"got {ub}")
    check("month_zhi=巳(月柱地支)", ub and ub.get("month_zhi") == "巳", f"got {ub}")

# 无八字 → user_bazi=None（引擎走无八字兜底, 不误伤）
eng = FakeZeriEngine()
eng.result = make_result()
h = build_handler(engine=eng, bazi=None)
h._tool_zeri("下个月搬家,帮我选个日子", "u6b")
check("无八字 → user_bazi=None", eng.calls and eng.calls[0]["user_bazi"] is None,
      f"got {eng.calls[0]['user_bazi'] if eng.calls else None}")

# ---------------------------------------------------------------------------
# 7. 额度: 真实调用扣 1 次; 用完 → 不调引擎 + 额度提示
#    (项目 .env 开了 EXPERIENCE_MODE=true 会跳过额度——测试临时关闭)
# ---------------------------------------------------------------------------
print("== 7. 额度扣减 ==")
_orig_exp = os.environ.pop("EXPERIENCE_MODE", None)
try:
    eng = FakeZeriEngine()
    eng.result = make_result()
    member = FakeMemberDAO(limit=3, used=2)
    h = build_handler(engine=eng, member=member)
    r = h._tool_zeri("下个月搬家,帮我选个日子", "u7")
    check("扣 1 次额度", member.use_count == 1 and member.used == 3,
          f"use_count={member.use_count} used={member.used}")

    eng = FakeZeriEngine()
    eng.result = make_result()
    member = FakeMemberDAO(limit=3, used=3)
    h = build_handler(engine=eng, member=member)
    r = h._tool_zeri("下个月搬家,帮我选个日子", "u7b")
    check("额度用完不调引擎", len(eng.calls) == 0, f"calls={len(eng.calls)}")
    check("额度用完不扣", member.use_count == 0, f"use_count={member.use_count}")
    check("额度提示文案", "额度" in r.text and "会员" in r.text, f"text={r.text}")
finally:
    if _orig_exp is not None:
        os.environ["EXPERIENCE_MODE"] = _orig_exp

# ---------------------------------------------------------------------------
# 8. suggest_wider=True → 扩窗提示注入结果
# ---------------------------------------------------------------------------
print("== 8. suggest_wider ==")
eng = FakeZeriEngine()
eng.result = make_result(n_cards=2, suggest_wider=True)
h = build_handler(engine=eng)
r = h._tool_zeri("8月20日开业", "u8")
check("扩窗提示含 reason", "不足3天" in r.text and "扩大" in r.text, f"text={r.text}")

# 0 卡 → 无合格吉日提示 + 不出现选择问句
eng = FakeZeriEngine()
eng.result = make_result(n_cards=0, suggest_wider=True)
h = build_handler(engine=eng)
r = h._tool_zeri("8月20日开业", "u8b")
check("0 卡 → 无合格吉日提示", "没有选出合格吉日" in r.text, f"text={r.text}")
check("0 卡 → 不出现选择问句", "您选哪一个" not in r.text, f"text={r.text}")

# ---------------------------------------------------------------------------
# 9. _execute_tool_call 分发路径
# ---------------------------------------------------------------------------
print("== 9. _execute_tool_call 分发 ==")
eng = FakeZeriEngine()
eng.result = make_result()
h = build_handler(engine=eng)
r = h._execute_tool_call("择日", "下个月搬家,帮我选个日子", "u9", user_question="")
check("分发后调引擎", len(eng.calls) == 1, f"calls={len(eng.calls)}")
check("分发结果 ok", r.ok is True and "2026-09-06" in r.text, f"ok={r.ok}")

# ---------------------------------------------------------------------------
# 10. _extract_window 纯函数
# ---------------------------------------------------------------------------
print("== 10. _extract_window ==")
h = build_handler()
exp_s, exp_e = next_month_window()
got = h._extract_window("下个月搬家")
check("下个月 → 自然月", got == (exp_s, exp_e), f"got {got} want {exp_s},{exp_e}")
exp_s, exp_e = next_week_window()
got = h._extract_window("下周 搬家 选日子")
check("下周 → 周一~周日", got == (exp_s, exp_e), f"got {got} want {exp_s},{exp_e}")
got = h._extract_window("2026-10-01 开业")
check("2026-10-01 → ±7", got == ("2026-09-24", "2026-10-08"), f"got {got}")
exp_s, exp_e = month_day_window(8, 20)
got = h._extract_window("8月20号 开业")
check("8月20号 → ±7", got == (exp_s, exp_e), f"got {got}")
today = date.today()
got = h._extract_window("随便选个")
check("无信息 → 今天~+30天", got == (today.isoformat(),
      (today + timedelta(days=30)).isoformat()), f"got {got}")

# ---------------------------------------------------------------------------
# 11. 回归: _extract_purpose 词表扩充 + _handle_zeri 旧路径
# ---------------------------------------------------------------------------
print("== 11. 回归 ==")
h = build_handler()
check("_extract_purpose 搬家", h._extract_purpose("2026年8月15日 搬家") == "搬家")
check("_extract_purpose 新增提车", h._extract_purpose("我想买车提车") == "提车",
      f"got {h._extract_purpose('我想买车提车')!r}")
check("_extract_purpose 新增签约", h._extract_purpose("签合同 过户") == "签约",
      f"got {h._extract_purpose('签合同 过户')!r}")
check("_extract_date 回归", h._extract_date("2026年8月15日 搬家") == (2026, 8, 15))
msg = h._handle_zeri("下个月搬家", "u11")
check("_handle_zeri 无日期 → 旧询问文案", "请告诉我您想查询的日期" in msg, f"msg={msg[:60]}")
eng = FakeZeriEngine()
eng.result = make_result()
h = build_handler(engine=eng)
try:
    h._handle_zeri("2026年8月15日 搬家", "u11b")
except AttributeError:
    pass  # 桩件缺 retriever/llm, 旧管线在 select 之后中断 —— 非本测试目标
check("_handle_zeri 有日期 → 仍走旧单日 select 引擎(未走新 select_lucky_days)",
      eng.select_calls == [(2026, 8, 15, "搬家")] and eng.calls == [],
      f"select_calls={eng.select_calls} new_calls={eng.calls}")

print()
print(f"结果: {_PASS} passed, {_FAIL} failed")
sys.exit(0 if _FAIL == 0 else 1)
