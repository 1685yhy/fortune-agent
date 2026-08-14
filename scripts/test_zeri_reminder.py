"""择吉日提醒调度测试(mock send_template/mp_ready/会员判定,仿 test_jian_scheduler.py)
```
运行：.venv/bin/python3 scripts/test_zeri_reminder.py
退出码：0=全部通过；1=有失败
```
覆盖：档1(前1天21:00)/档2(当天7:30,7:29 不发)/只发一次(remind_sent 去重)/
非会员不发+体验模式发/无 mp_openid 跳过/失败连3 → zeri_prefs.bound_status=invalid 停推/
mp_ready False 不调 send_template/未完成数 N 仅计 done=false/thing ≤20字。
"""
import os, sys, tempfile, sqlite3
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest.mock as mock
from src.storage.zeri_dao import ZeriDAO
from src.storage.jian_dao import JianPrefDAO
import src.main as main_mod

# 固定非体验模式(末尾单独用例显式开启验证「体验模式也发」)
os.environ["EXPERIENCE_MODE"] = ""
# 推送通道 env:send_template/mp_ready 均被 mock,tpl_id 走真实 _env 取值
os.environ["MP_ZERI_TEMPLATE_ID"] = "TPL_ZERI_TEST"

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

fd, path = tempfile.mkstemp(suffix='.db'); os.close(fd)
conn = sqlite3.connect(path)
zdao = ZeriDAO(conn)
jdao = JianPrefDAO(conn)

BJ = timezone(timedelta(hours=8))
def bj(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=BJ)

# 以"真实今天"为基准构造日期(worker 时间点可注入 now,与日期计算同源)
TODAY = datetime.now(timezone(timedelta(hours=8)))
TODAY_STR = TODAY.strftime("%Y-%m-%d")
TOMORROW_STR = (TODAY + timedelta(days=1)).strftime("%Y-%m-%d")

# ── 会员判定 stub(生产注入 MemberDAO;plan != free 即会员)──
class _FakeMemberDAO:
    def __init__(self):
        self.members = {}
    def set_member(self, uid, plan="basic"):
        self.members[uid] = plan
    def get_membership(self, uid):
        plan = self.members.get(uid, "free")
        return {"plan": plan}
member_dao = _FakeMemberDAO()

def make_plan(uid, lucky_date, scene="嫁娶", items=None, reminder_enabled=1, plan_type="member"):
    """落一条提醒开启的计划,返回 plan_id。"""
    return zdao.upsert_plan(uid, scene, lucky_date,
                            {"date": lucky_date, "jishi": "巳时(9-11点)", "total": 79},
                            items if items is not None else [
                                {"stage": "提前3天", "text": "发请柬并统计宾客名单", "core": True},
                                {"stage": "当天", "text": "吉时9-11点 婚车出发接亲", "core": True},
                            ],
                            plan_type, reminder_enabled=reminder_enabled)

def bind(uid, openid=None):
    """复用 jian_prefs 绑定(openid 不重复存)。"""
    jdao.upsert_pref(uid, {"bound_status": "bound", "mp_openid": openid or f"o_{uid}"})

def ok_env():
    """mock 通道就绪:send_template 记录调用,mp_ready=True。"""
    sent = []
    env = (mock.patch("src.services.wechat_mp.send_template",
                      side_effect=lambda oid, tpl, data, url="": sent.append((oid, tpl, data, url)) or {}),
           mock.patch("src.services.wechat_mp.mp_ready", return_value=True))
    return sent, env

def fail_env():
    """mock 通道故障:send_template 抛异常,mp_ready=True。"""
    return (mock.patch("src.services.wechat_mp.send_template",
                       side_effect=RuntimeError("模拟模板消息通道故障")),
            mock.patch("src.services.wechat_mp.mp_ready", return_value=True))

def run_batch(now):
    return main_mod._zeri_reminder_batch(zdao, now, member_dao)

# ── 1. 档1(前1天 21:00): 发 1 次且仅 1 次 ────────────────────────────
member_dao.set_member("u_d1", "basic")
pid1 = make_plan("u_d1", TOMORROW_STR, scene="嫁娶")
bind("u_d1")
sent1, env1 = ok_env()
with env1[0], env1[1]:
    stats = run_batch(bj(TODAY.year, TODAY.month, TODAY.day, 21, 0))
check("档1 21:00 发 1 条", stats["pushed"] == 1 and len(sent1) == 1)
check("档1 openid 取 jian_prefs.mp_openid", sent1[0][0] == "o_u_d1")
check("档1 模板 env MP_ZERI_TEMPLATE_ID", sent1[0][1] == "TPL_ZERI_TEST")
d1_thing1 = sent1[0][2]["thing1"]["value"]
check("档1 thing1 含场景/明天/日期", "嫁娶" in d1_thing1 and "明天" in d1_thing1 and TOMORROW_STR[5:] in d1_thing1)
check("档1 thing1 ≤20字", len(d1_thing1) <= 20)
d1_thing2 = sent1[0][2]["thing2"]["value"]
check("档1 thing2 含未完成数 N=2", "清单" in d1_thing2 and "2" in d1_thing2)
check("档1 thing2 ≤20字", len(d1_thing2) <= 20)
check("档1 URL 带 plan_id", f"id={pid1}" in sent1[0][3])
check("档1 发送后 remind_sent_d1=1", zdao.get_plan("u_d1", pid1)["remind_sent_d1"] == 1)
sent1.clear()
with env1[0], env1[1]:
    stats2 = run_batch(bj(TODAY.year, TODAY.month, TODAY.day, 21, 30))
check("档1 再次 run 不再发(去重)", stats2["pushed"] == 0 and len(sent1) == 0)

# ── 2. 档2(当天 7:30;7:29 不发) ────────────────────────────────────
member_dao.set_member("u_d0", "basic")
pid0 = make_plan("u_d0", TODAY_STR, scene="开业")
bind("u_d0")
sent2, env2 = ok_env()
with env2[0], env2[1]:
    stats_a = run_batch(bj(TODAY.year, TODAY.month, TODAY.day, 7, 29))
check("档2 7:29 不发", stats_a["pushed"] == 0 and len(sent2) == 0)
with env2[0], env2[1]:
    stats_b = run_batch(bj(TODAY.year, TODAY.month, TODAY.day, 7, 30))
check("档2 7:30 发 1 条", stats_b["pushed"] == 1 and len(sent2) == 1)
d0_thing1 = sent2[0][2]["thing1"]["value"]
check("档2 thing1 含今日吉时+jishi", "今日吉时" in d0_thing1 and "巳时" in d0_thing1)
check("档2 thing1 ≤20字", len(d0_thing1) <= 20)
check("档2 thing2 就绪文案", sent2[0][2]["thing2"]["value"] == "清单已就绪,祝诸事顺遂")
check("档2 发送后 remind_sent_d0=1", zdao.get_plan("u_d0", pid0)["remind_sent_d0"] == 1)

# ── 3. 非会员不发;体验模式发 ───────────────────────────────────────
pid_free = make_plan("u_free", TODAY_STR, scene="搬家", plan_type="free")
bind("u_free")
sent3, env3 = ok_env()
with env3[0], env3[1]:
    stats_f = run_batch(bj(TODAY.year, TODAY.month, TODAY.day, 7, 30))
check("非会员不发", stats_f["pushed"] == 0 and len(sent3) == 0)
os.environ["EXPERIENCE_MODE"] = "true"
try:
    with env3[0], env3[1]:
        stats_e = run_batch(bj(TODAY.year, TODAY.month, TODAY.day, 7, 30))
    check("体验模式也发(免费用户)", stats_e["pushed"] == 1 and len(sent3) == 1)
    check("体验模式发送的是 u_free", sent3[0][0] == "o_u_free")
finally:
    os.environ["EXPERIENCE_MODE"] = ""

# ── 4. 无 mp_openid 不发;失败连3 → bound_status=invalid 停推 ───────────
member_dao.set_member("u_nb", "basic")
make_plan("u_nb", TODAY_STR, scene="签约")   # 不 bind → 无 openid
sent4, env4 = ok_env()
with env4[0], env4[1]:
    stats_nb = run_batch(bj(TODAY.year, TODAY.month, TODAY.day, 7, 30))
check("无 openid 跳过(不发送)", stats_nb["pushed"] == 0 and len(sent4) == 0 and stats_nb["skipped"] >= 1)

member_dao.set_member("u_fail", "basic")
pid_f = make_plan("u_fail", TODAY_STR, scene="提车")
bind("u_fail")
for i in range(1, 4):
    fenv = fail_env()
    with fenv[0], fenv[1]:
        run_batch(bj(TODAY.year, TODAY.month, TODAY.day, 7, 30))
    pref = zdao.get_pref("u_fail") or {}
    check(f"失败{i}次 fail_count={i}", pref.get("fail_count") == i)
    if i < 3:
        check(f"失败{i}次未标记 invalid", pref.get("bound_status") != "invalid")
check("失败3次 bound_status=invalid", (zdao.get_pref("u_fail") or {}).get("bound_status") == "invalid")
# invalid 后停推:不再尝试发送(即使通道恢复)
sent5 = []
with mock.patch("src.services.wechat_mp.send_template",
                side_effect=lambda oid, tpl, data, url="": sent5.append(oid) or {}), \
     mock.patch("src.services.wechat_mp.mp_ready", return_value=True):
    stats_inv = run_batch(bj(TODAY.year, TODAY.month, TODAY.day, 8, 0))
check("invalid 后不再发送", stats_inv["pushed"] == 0 and len(sent5) == 0)
check("invalid 计划 sent 标记未被改写", zdao.get_plan("u_fail", pid_f)["remind_sent_d0"] == 0)

# ── 5. mp_ready False → 不调 send_template(休眠态,同晨笺) ──────────────
member_dao.set_member("u_mp", "basic")
make_plan("u_mp", TODAY_STR, scene="出行")
bind("u_mp")
sent6 = []
with mock.patch("src.services.wechat_mp.send_template",
                side_effect=lambda oid, tpl, data, url="": sent6.append(oid) or {}), \
     mock.patch("src.services.wechat_mp.mp_ready", return_value=False):
    stats_mp = run_batch(bj(TODAY.year, TODAY.month, TODAY.day, 7, 30))
check("mp_ready False 不调 send_template", len(sent6) == 0 and stats_mp["pushed"] == 0)

# ── 6. 未完成数 N 仅计 done=false(已勾选项不计入;N 在档1 文案中) ──────
member_dao.set_member("u_n", "basic")
pid_n = make_plan("u_n", TOMORROW_STR, scene="出行",
                  items=[{"text": "已完成的项", "done": 1},
                         {"text": "未完成的项甲", "done": 0},
                         {"text": "未完成的项乙", "core": True}])  # 无 done 键按未完成计
bind("u_n")
sent8, env8 = ok_env()
with env8[0], env8[1]:
    run_batch(bj(TODAY.year, TODAY.month, TODAY.day, 21, 0))
n_entry = next(e for e in sent8 if e[0] == "o_u_n")  # 按 openid 定位(与其它用户同档共发)
check("N 仅计未完成(done=1 不计)", "2" in n_entry[2]["thing2"]["value"])

# ── 7. Fix1(终审): 设置页开关生效 —— prefs reminder_enabled=0 整批跳过 ─────────
#      未建 prefs 行的用户不受影响(默认开启语义保持)
member_dao.set_member("u_off", "basic")
make_plan("u_off", TODAY_STR, scene="提车")      # 计划级 reminder_enabled=1(已订阅)
bind("u_off")
zdao.upsert_pref("u_off", {"reminder_enabled": 0})   # 设置页 PUT /api/zeri/prefs 写 0
sent_off = []
with mock.patch("src.services.wechat_mp.send_template",
                side_effect=lambda oid, tpl, data, url="": sent_off.append(oid) or {}), \
     mock.patch("src.services.wechat_mp.mp_ready", return_value=True):
    run_batch(bj(TODAY.year, TODAY.month, TODAY.day, 7, 30))
check("Fix1: prefs 显式关闭 → 整批跳过(计划级订阅不生效)", "o_u_off" not in sent_off)

member_dao.set_member("u_nopref", "basic")
make_plan("u_nopref", TODAY_STR, scene="出行")   # 无 zeri_prefs 行(未碰过设置页)
bind("u_nopref")
member_dao.set_member("u_on", "basic")
make_plan("u_on", TODAY_STR, scene="签约")
bind("u_on")
zdao.upsert_pref("u_on", {"reminder_enabled": 1})    # 设置页显式开启
sent_np = []
with mock.patch("src.services.wechat_mp.send_template",
                side_effect=lambda oid, tpl, data, url="": sent_np.append(oid) or {}), \
     mock.patch("src.services.wechat_mp.mp_ready", return_value=True):
    run_batch(bj(TODAY.year, TODAY.month, TODAY.day, 7, 30))
check("Fix1: 无 prefs 行 → 默认开启仍发送", "o_u_nopref" in sent_np)
check("Fix1: prefs 显式开启 → 正常发送", "o_u_on" in sent_np)
check("Fix1: 同批仍不发已关闭用户", "o_u_off" not in sent_np)

# 7b. 回归: 批次自身的成功计数 upsert 不得把用户误打为关闭态 —— 否则收到过一条
#      提醒后 prefs 行(默认 reminder_enabled=0)会被批次查询当"显式关闭"整批跳过
member_dao.set_member("u_keep", "basic")
pid_k = make_plan("u_keep", TODAY_STR, scene="开业")
bind("u_keep")
sent_k, env_k = ok_env()
with env_k[0], env_k[1]:
    run_batch(bj(TODAY.year, TODAY.month, TODAY.day, 7, 30))
check("Fix1: 成功发送后 prefs 行 reminder_enabled=1(不误打关闭)",
      (zdao.get_pref("u_keep") or {}).get("reminder_enabled") == 1)
make_plan("u_keep", TOMORROW_STR, scene="搬家")   # 同用户新计划(档1 候选)
sent_k2, env_k2 = ok_env()
with env_k2[0], env_k2[1]:
    run_batch(bj(TODAY.year, TODAY.month, TODAY.day, 21, 0))
check("Fix1: 收过提醒的用户新计划仍发送(未被 NOT IN 排除)", any(e[0] == "o_u_keep" for e in sent_k2))

print(f"\nALL PASS ({ok})")
