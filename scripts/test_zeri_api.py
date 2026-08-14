"""择吉日 DAO+API 测试(TestClient+临时 DB+假 token)
```
运行：.venv/bin/python3 scripts/test_zeri_api.py
退出码：0=全部通过；1=有失败
```
覆盖：选日落库(免费档 plan_type 兜底)/详情/非本人 403/清单勾选备注/reminder 开关/
免费历史仅 3 条 vs 会员全量/换一批免费每日 3 次第 4 次 429 vs 会员不限/prefs 读写(绑定态来自 jian_prefs)/
空场景 select 400/status 过滤/竞态 bump 拒绝 429/次日额度重置/set_remind_sent 非法值拒绝。
"""
import os, sys, tempfile
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from starlette.testclient import TestClient

# .env 默认 EXPERIENCE_MODE=true（体验模式免扣额度）——测试固定为关，走免费额度路径；
# 末尾单独用例显式开启验证「体验模式不限」。
os.environ["EXPERIENCE_MODE"] = ""

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

# 独立临时 DB 注入（get_conn() 读取 dao_mod._DB_PATH）
fd, path = tempfile.mkstemp(suffix='.db'); os.close(fd)
import src.storage.dao as dao_mod
dao_mod._DB_PATH = path
from src.storage.zeri_dao import ZeriDAO
import src.api.zeri as zeri_mod
from src.storage.jian_dao import JianPrefDAO
from src.main import app

_test_dao = ZeriDAO(dao_mod.get_conn())
_test_jian_dao = JianPrefDAO(dao_mod.get_conn())
zeri_mod._dao = _test_dao

# ── 会员判定 stub（生产注入 MemberDAO）──
class _FakeMemberDAO:
    def __init__(self):
        self.members = {}
    def set_member(self, uid, plan="basic"):
        self.members[uid] = plan
    def get_membership(self, uid):
        plan = self.members.get(uid, "free")
        return {"plan": plan}

# ── 引擎 stub（生产注入 handler.zeri_engine；未知场景抛 ValueError 与真实引擎一致）──
from src.engines.zeri import LuckyDayCard
class _FakeZeriEngine:
    _SCENES = {"嫁娶", "搬家", "开业", "出行", "提车", "签约"}
    def __init__(self):
        self.last_exclude = None   # Fix2: 记录引擎实际收到的 exclude_dates
    def select_lucky_days(self, scene="", start_date="", end_date="", user_bazi=None,
                          exclude_dates=None, prefer_weekend=False):
        self.last_exclude = exclude_dates
        if not (scene and start_date and end_date):
            raise ValueError("参数缺失")
        if scene not in self._SCENES:
            raise ValueError(f"未知场景: {scene!r}")
        cards = [LuckyDayCard(date=d, lunar_text="农历七月初八 丁卯日 星期四",
                              yi=["出行"], ji=["开市"], jishi="巳时(9-11点)",
                              xi_fangwei="正南", cai_fangwei="西南", scene_score=42,
                              personal_score=24, practical_score=10, total=76,
                              reason_source="成日值日")
                 for d in ("2026-08-20", "2026-08-22", "2026-08-24")]
        return {"cards": cards, "scanned": 30, "suggest_wider": False, "reason": None}
class _FakeHandler:
    zeri_engine = _FakeZeriEngine()
    def _map_user_bazi_for_zeri(self, uid):
        return None

_member_dao = _FakeMemberDAO()
zeri_mod._member_dao = _member_dao
zeri_mod._handler = _FakeHandler()
app.state.zeri_dao = _test_dao

client = TestClient(app)

# 假 token 机制（仿 test_jian_api.py）：require_user 走模块级共享 get_auth_handler()，
# token 必须由同一 AuthHandler 实例签发才可通过校验。
from src.security.auth import AuthHandler, set_auth_handler
_auth = AuthHandler()
set_auth_handler(_auth)
UID = "zeri-test-user"
UID2 = "zeri-test-user-b"
UIDM = "zeri-test-member"
UID3 = "zeri-test-reset"     # 跨日额度重置用例(免费)
UID4 = "zeri-test-race"      # 竞态 bump 拒绝用例(免费)
UID5 = "zeri-test-options"   # options 不扣额度用例(免费)
TOKEN = _auth.create_user_token(UID)
TOKEN2 = _auth.create_user_token(UID2)
TOKENM = _auth.create_user_token(UIDM)
TOKEN3 = _auth.create_user_token(UID3)
TOKEN4 = _auth.create_user_token(UID4)
TOKEN5 = _auth.create_user_token(UID5)
h = {"Authorization": f"Bearer {TOKEN}"}
h2 = {"Authorization": f"Bearer {TOKEN2}"}
hm = {"Authorization": f"Bearer {TOKENM}"}
h3 = {"Authorization": f"Bearer {TOKEN3}"}
h4 = {"Authorization": f"Bearer {TOKEN4}"}
h5 = {"Authorization": f"Bearer {TOKEN5}"}
_member_dao.set_member(UIDM, "basic")


def _bj_day(offset_days=0):
    """北京时区 N 天前的日期串(与 API 层 _bj_today 同口径)。"""
    return (datetime.now(timezone(timedelta(hours=8)))
            + timedelta(days=offset_days)).strftime("%Y-%m-%d")

CARD = {"date": "2026-08-20", "lunar_text": "农历七月初八 丁卯日 星期四", "yi": ["嫁娶"],
        "ji": ["开市"], "jishi": "巳时(9-11点)", "xi_fangwei": "正南", "cai_fangwei": "西南",
        "scene_score": 45, "personal_score": 24, "practical_score": 10, "total": 79,
        "reason_source": "成日值日"}
ITEMS = [
    {"stage": "提前3天", "text": "发请柬并统计宾客名单", "core": True},
    {"stage": "提前3天", "text": "与酒店核对桌数菜单", "core": True},
    {"stage": "提前1天", "text": "彩排走场对词", "core": True},
    {"stage": "当天", "text": "吉时9-11点 婚车出发接亲", "core": True},
    {"stage": "当天", "text": "宴席开席,敬酒答谢", "core": True},
]

# 1. 未登录 401
r = client.get("/api/zeri/plans")
check("未登录 plans 401", r.status_code == 401)
r = client.post("/api/zeri/select", json={"scene": "嫁娶", "lucky_date": "2026-08-20",
                                          "card": CARD, "items": ITEMS})
check("未登录 select 401", r.status_code == 401)
r = client.get("/api/zeri/prefs")
check("未登录 prefs 401", r.status_code == 401)

# 2. 选日落库（免费档 plan_type 兜底：客户端传 member 被服务端降级为 free）
r = client.post("/api/zeri/select", headers=h, json={"scene": "嫁娶", "lucky_date": "2026-08-20",
                                                     "card": CARD, "items": ITEMS, "plan_type": "member"})
assert r.status_code == 200, r.text
pid = r.json()["plan_id"]
check("select 返回 plan_id", isinstance(pid, int))
check("免费档 plan_type 兜底为 free", r.json()["plan_type"] == "free")

# 2b. 空场景(含纯空白)select 400,不落库
r = client.post("/api/zeri/select", headers=h, json={"scene": "   ", "lucky_date": "2026-08-20",
                                                     "card": CARD, "items": []})
check("空场景 select 400", r.status_code == 400)

# 3. 详情（完整 card/items/plan_type）
r = client.get(f"/api/zeri/plans/{pid}", headers=h)
d = r.json()["plan"]
check("详情卡数据完整", d["card"]["date"] == "2026-08-20" and d["card"]["total"] == 79)
check("详情清单完整", len(d["items"]) == len(ITEMS) and d["items"][0]["text"] == ITEMS[0]["text"])
check("详情 plan_type free", d["plan_type"] == "free")
check("详情默认未订阅提醒", d["reminder_enabled"] == 0)

# 4. 非本人 403（归属校验红线）
r = client.get(f"/api/zeri/plans/{pid}", headers=h2)
check("他人详情 403", r.status_code == 403)
r = client.put(f"/api/zeri/plans/{pid}/item", headers=h2, json={"idx": 0, "done": True})
check("他人 item 403", r.status_code == 403)
r = client.put(f"/api/zeri/plans/{pid}/reminder", headers=h2, json={"enabled": True})
check("他人 reminder 403", r.status_code == 403)

# 5. 不存在的 plan 404
r = client.get("/api/zeri/plans/99999", headers=h)
check("不存在计划 404", r.status_code == 404)

# 6. 清单项勾选/备注持久化
r = client.put(f"/api/zeri/plans/{pid}/item", headers=h, json={"idx": 0, "done": True, "note": "已统计 12 桌"})
assert r.status_code == 200, r.text
check("item 返回更新后清单", r.json()["items"][0]["done"] == 1 and r.json()["items"][0]["note"] == "已统计 12 桌")
r = client.get(f"/api/zeri/plans/{pid}", headers=h)
items = r.json()["plan"]["items"]
check("item 勾选持久化", items[0]["done"] == 1 and items[0]["note"] == "已统计 12 桌")
check("其余项未被误改", items[1].get("done") is None)
r = client.put(f"/api/zeri/plans/{pid}/item", headers=h, json={"idx": 99, "done": True})
check("越界 idx 400", r.status_code == 400)
r = client.put(f"/api/zeri/plans/{pid}/item", headers=h, json={"idx": 0})
check("空 patch 400", r.status_code == 400)

# 7. reminder 开关落库（主动同意制：enabled=true 即用户同意）
r = client.put(f"/api/zeri/plans/{pid}/reminder", headers=h, json={"enabled": True})
check("reminder 开启", r.status_code == 200 and r.json()["enabled"] is True)
r = client.get(f"/api/zeri/plans/{pid}", headers=h)
check("reminder 落库", r.json()["plan"]["reminder_enabled"] == 1)
r = client.put(f"/api/zeri/plans/{pid}/reminder", headers=h, json={"enabled": False})
check("reminder 关闭", r.status_code == 200 and r.json()["enabled"] is False)

# 8. 历史：免费仅 3 条 + more_requires_member=true
for dt in ["2026-09-01", "2026-09-05", "2026-09-09", "2026-09-13"]:
    rr = client.post("/api/zeri/select", headers=h, json={"scene": "搬家", "lucky_date": dt,
                                                          "card": {**CARD, "date": dt}, "items": []})
    assert rr.status_code == 200, rr.text
r = client.get("/api/zeri/plans", headers=h)
plans = r.json()["plans"]
check("免费历史最多 3 条", len(plans) == 3)
check("免费 more_requires_member=true", r.json()["more_requires_member"] is True)
check("历史按最新在前", plans[0]["lucky_date"] == "2026-09-13")

# 8b. status 过滤: cancelled 计划不出现在历史/计数(未来 cancel 端点引入后历史不混入)
_active_before = _test_dao.count_plans(UID)   # 5: pid(嫁娶) + 4 条搬家
_test_dao.conn.execute(
    "UPDATE zeri_plans SET status='cancelled' WHERE user_id=? AND lucky_date='2026-09-13'", (UID,))
_test_dao.conn.commit()
r = client.get("/api/zeri/plans", headers=h)
check("cancelled 计划不出现在历史", all(p["lucky_date"] != "2026-09-13" for p in r.json()["plans"]))
check("count_plans 剔除 cancelled(-1)", _test_dao.count_plans(UID) == _active_before - 1)
check("DAO list_plans 全量同样剔除 cancelled",
      all(p["lucky_date"] != "2026-09-13" for p in _test_dao.list_plans(UID, limit=None)))

# 9. 会员全量历史（mock 会员）
for dt in ["2026-08-01", "2026-08-05", "2026-08-09", "2026-08-13", "2026-08-17"]:
    rr = client.post("/api/zeri/select", headers=hm, json={"scene": "开业", "lucky_date": dt,
                                                           "card": {**CARD, "date": dt}, "items": []})
    assert rr.status_code == 200, rr.text
r = client.get("/api/zeri/plans", headers=hm)
check("会员全量历史 5 条", len(r.json()["plans"]) == 5)
check("会员 more_requires_member=false", r.json()["more_requires_member"] is False)

# 9b. Fix3(终审): 服务端清单重建 —— 忽略客户端 items, 免费/会员边界由服务端 enforce
from src.engines.zeri_checklist import CHECKLIST_TEMPLATES
_full_marry = [dict(it) for it in CHECKLIST_TEMPLATES["嫁娶"]]   # 10 项(会员全量式)
r = client.post("/api/zeri/select", headers=h2, json={"scene": "嫁娶", "lucky_date": "2026-10-01",
                                                      "card": {**CARD, "date": "2026-10-01"},
                                                      "items": _full_marry, "plan_type": "member"})
assert r.status_code == 200, r.text
free_pid = r.json()["plan_id"]
check("Fix3: 免费用户 POST 会员式 items → plan_type 仍 free", r.json()["plan_type"] == "free")
_free_items = client.get(f"/api/zeri/plans/{free_pid}", headers=h2).json()["plan"]["items"]
check("Fix3: 免费 POST 全量 items → 落库仅 core 5 项", len(_free_items) == 5)
check("Fix3: 重建 items 全为 core 且按阶段排序",
      all(it.get("core") for it in _free_items)
      and [it["stage"] for it in _free_items] == ["提前3天", "提前3天", "提前1天", "当天", "当天"])
check("Fix3: 重建 items 文案来自模板(非客户端)",
      _free_items[0]["text"] == "发请柬并统计宾客名单"
      and all(it["text"] not in {"黑客注入项"} for it in _free_items))
r = client.post("/api/zeri/select", headers=hm, json={"scene": "嫁娶", "lucky_date": "2026-10-05",
                                                      "card": {**CARD, "date": "2026-10-05"},
                                                      "items": [{"stage": "当天", "text": "黑客注入项"}],
                                                      "plan_type": "free"})
assert r.status_code == 200, r.text
_m_pid = r.json()["plan_id"]
_m_items = client.get(f"/api/zeri/plans/{_m_pid}", headers=hm).json()["plan"]["items"]
check("Fix3: 会员 POST 任意 → 落库全量模板 10 项", len(_m_items) == 10)
check("Fix3: 会员重建 items 不含客户端项", all(it["text"] != "黑客注入项" for it in _m_items))

# 10. 换一批：免费每日 3 次，第 4 次 429；会员不限
for i in range(3):
    r = client.post("/api/zeri/refresh", headers=h, json={"scene": "搬家", "start": "2026-09-01",
                                                          "end": "2026-09-30"})
    assert r.status_code == 200, r.text
    check(f"换一批第{i+1}次返回 3 卡", len(r.json()["cards"]) == 3)
    check(f"换一批第{i+1}次剩余额度提示", r.json()["refresh_remaining"] == 2 - i)
r = client.post("/api/zeri/refresh", headers=h, json={"scene": "搬家", "start": "2026-09-01",
                                                      "end": "2026-09-30"})
check("免费第 4 次换一批 429", r.status_code == 429)
for i in range(4):
    r = client.post("/api/zeri/refresh", headers=hm, json={"scene": "开业", "start": "2026-08-01",
                                                           "end": "2026-08-31"})
    assert r.status_code == 200, r.text
check("会员换一批不限(4次全过)", True)
r = client.post("/api/zeri/refresh", headers=hm, json={"scene": "不存在的场景", "start": "2026-08-01",
                                                       "end": "2026-08-31"})
check("非法场景 400", r.status_code == 400)

# 10b. 竞态路径: 预检通过(count=2)但 bump 被拒(count 已到 3)→ 权威 allowed 结果 429,
#      不因预检侥幸通过而放行(修复前此处错误返回 200)
today = _bj_day()
for _ in range(3):
    assert _test_dao.bump_refresh(UID4, today, limit=3)[0]  # 今日额度实际已用满 3 次
_orig_count = _test_dao.get_refresh_count          # 模拟并发预检读到旧值 2
_test_dao.get_refresh_count = lambda uid, day: 2
try:
    r = client.post("/api/zeri/refresh", headers=h4, json={"scene": "搬家", "start": "2026-09-01",
                                                           "end": "2026-09-30"})
finally:
    _test_dao.get_refresh_count = _orig_count
check("竞态下 bump 拒绝 → 429", r.status_code == 429)
check("竞态 429 后额度未再增加", _test_dao.get_refresh_count(UID4, today) == 3)

# 10c. 次日额度重置: 昨日已用满 3 次,今日 refresh 应允许并从 0 重新计数
yesterday = _bj_day(-1)
for _ in range(3):
    assert _test_dao.bump_refresh(UID3, yesterday, limit=3)[0]
check("昨日额度已用满 3", _test_dao.get_refresh_count(UID3, yesterday) == 3)
r = client.post("/api/zeri/refresh", headers=h3, json={"scene": "搬家", "start": "2026-09-01",
                                                       "end": "2026-09-30"})
check("昨日用满不影响今日: 今日首次 refresh 200", r.status_code == 200)
check("今日首次 refresh 剩余额度为 2", r.json()["refresh_remaining"] == 2)
check("今日计数为 1", _test_dao.get_refresh_count(UID3, today) == 1)
check("昨日计数保持 3 不变", _test_dao.get_refresh_count(UID3, yesterday) == 3)

# 10d. options 端点（初始加载取卡）：200/字段结构与 refresh 同构/不扣额度（连调 3 次 quota 不变）/
#      refresh 额度独立保留/未登录 401/空场景 400/exclude_dates 可选传
r = client.get("/api/zeri/options")
check("未登录 options 401", r.status_code == 401)
r = client.get("/api/zeri/options", headers=h5,
               params={"scene": "嫁娶", "start": "2026-09-01", "end": "2026-09-30"})
assert r.status_code == 200, r.text
body = r.json()
check("options 返回 3 卡", len(body["cards"]) == 3)
check("options 字段结构同构 refresh", "scanned" in body and "suggest_wider" in body
      and "reason" in body and "refresh_remaining" in body)
check("options 卡字段完整", body["cards"][0]["date"] == "2026-08-20"
      and body["cards"][0]["total"] == 76)
_count_before = _test_dao.get_refresh_count(UID5, today)
for _ in range(3):
    r = client.get("/api/zeri/options", headers=h5,
                   params={"scene": "嫁娶", "start": "2026-09-01", "end": "2026-09-30"})
    assert r.status_code == 200, r.text
check("options 连续调 3 次不扣额度", _test_dao.get_refresh_count(UID5, today) == _count_before)
r = client.post("/api/zeri/refresh", headers=h5, json={"scene": "嫁娶", "start": "2026-09-01",
                                                       "end": "2026-09-30"})
check("options 后首次 refresh 仍可用(剩余 2)", r.status_code == 200
      and r.json()["refresh_remaining"] == 2)
r = client.get("/api/zeri/options", headers=h5,
               params={"scene": "嫁娶", "start": "2026-09-01", "end": "2026-09-30",
                       "exclude_dates": "2026-08-20,2026-08-22"})
check("options exclude_dates 可选传 200", r.status_code == 200)
r = client.get("/api/zeri/options", headers=h5,
               params={"scene": "   ", "start": "2026-09-01", "end": "2026-09-30"})
check("空场景 options 400", r.status_code == 400)

# 10e. Fix2(终审): options 静默忽略 exclude_dates(不传引擎) —— 防免费用户改 exclude 无限换一批
r = client.get("/api/zeri/options", headers=h5,
               params={"scene": "嫁娶", "start": "2026-09-01", "end": "2026-09-30",
                       "exclude_dates": "2026-08-20,2026-08-22"})
assert r.status_code == 200, r.text
check("Fix2: options 丢弃 exclude_dates, 引擎收到 None",
      zeri_mod._handler.zeri_engine.last_exclude is None)
r = client.post("/api/zeri/refresh", headers=h5, json={"scene": "嫁娶", "start": "2026-09-01",
                                                       "end": "2026-09-30",
                                                       "exclude_dates": ["2026-08-20"]})
assert r.status_code == 200, r.text
check("Fix2: refresh 的 exclude_dates 正常传引擎",
      zeri_mod._handler.zeri_engine.last_exclude == ["2026-08-20"])

# 11. prefs 读写；绑定态复用 jian_prefs（同一服务号，不重复存 openid）
r = client.get("/api/zeri/prefs", headers=h)
p = r.json()["prefs"]
check("默认 prefs", p["reminder_enabled"] == 0 and p["bound_status"] == "unbound")
r = client.put("/api/zeri/prefs", headers=h, json={"reminder_enabled": True})
check("保存 reminder_enabled", r.status_code == 200 and r.json()["prefs"]["reminder_enabled"] == 1)
_test_jian_dao.upsert_pref(UID, {"bound_status": "bound", "mp_openid": "oXXXX"})
r = client.get("/api/zeri/prefs", headers=h)
check("绑定态来自 jian_prefs", r.json()["prefs"]["bound_status"] == "bound")
check("zeri prefs 不出参 openid", "mp_openid" not in r.json()["prefs"])
r = client.put("/api/zeri/prefs", headers=h, json={})
check("空 prefs 400", r.status_code == 400)

# 12. 提醒已发标记（调度侧落库，供排期去重）
plan = _test_dao.get_plan(UID, pid)
check("初始 remind_sent 均为 0", plan["remind_sent_d1"] == 0 and plan["remind_sent_d0"] == 0)
_test_dao.set_remind_sent(UID, pid, "d1")
plan = _test_dao.get_plan(UID, pid)
check("remind_sent_d1 落库", plan["remind_sent_d1"] == 1)
_test_dao.set_remind_sent(UID, pid, "d0")
plan = _test_dao.get_plan(UID, pid)
check("remind_sent_d0 落库", plan["remind_sent_d0"] == 1)

# 12b. 非法 d1_or_d0 值拒绝,不落任何列(修复前任意非 d1 值都误写 d0)
check("非法值 set_remind_sent 返回 False", _test_dao.set_remind_sent(UID, pid, "garbage") is False)
plan = _test_dao.get_plan(UID, pid)
check("非法值未改动 remind_sent 列", plan["remind_sent_d1"] == 1 and plan["remind_sent_d0"] == 1)

# 12c. Fix5(终审): 日期窗口钳制(两端点共用校验) —— 93 天 400 / 92 天 200 / 非法格式 400 / end<start 400
#      (2026 非闰年: 01-01→04-03 = 92 天, 01-01→04-04 = 93 天)
r = client.post("/api/zeri/refresh", headers=h, json={"scene": "搬家", "start": "2026-01-01",
                                                      "end": "2026-04-04"})
check("Fix5: refresh 93 天窗口 400", r.status_code == 400 and "92 天" in r.json()["detail"])
r = client.get("/api/zeri/options", headers=h5, params={"scene": "嫁娶", "start": "2026-01-01",
                                                        "end": "2026-04-04"})
check("Fix5: options 93 天窗口 400", r.status_code == 400)
r = client.post("/api/zeri/refresh", headers=h3, json={"scene": "搬家", "start": "2026-01-01",
                                                       "end": "2026-04-03"})
check("Fix5: refresh 92 天窗口 200", r.status_code == 200)
r = client.get("/api/zeri/options", headers=h5, params={"scene": "嫁娶", "start": "2026-01-01",
                                                        "end": "2026-04-03"})
check("Fix5: options 92 天窗口 200", r.status_code == 200)
r = client.get("/api/zeri/options", headers=h5, params={"scene": "嫁娶", "start": "2026-02-30",
                                                        "end": "2026-04-03"})
check("Fix5: 非法日期格式 400", r.status_code == 400)
r = client.post("/api/zeri/refresh", headers=h, json={"scene": "搬家", "start": "2026-09-30",
                                                      "end": "2026-09-01"})
check("Fix5: end 早于 start 400", r.status_code == 400)

# 13. 体验模式换一批不限（临时开启体验模式）
os.environ["EXPERIENCE_MODE"] = "true"
try:
    for i in range(4):
        r = client.post("/api/zeri/refresh", headers=h2, json={"scene": "签约",
                                                               "start": "2026-09-01",
                                                               "end": "2026-09-30"})
        assert r.status_code == 200, r.text
    check("体验模式换一批不限(4次全过)", True)
finally:
    os.environ["EXPERIENCE_MODE"] = ""

print(f"\nALL PASS ({ok})")
