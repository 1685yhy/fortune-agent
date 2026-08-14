"""择吉日 DAO+API 测试(TestClient+临时 DB+假 token)
```
运行：.venv/bin/python3 scripts/test_zeri_api.py
退出码：0=全部通过；1=有失败
```
覆盖：选日落库(免费档 plan_type 兜底)/详情/非本人 403/清单勾选备注/reminder 开关/
免费历史仅 3 条 vs 会员全量/换一批免费每日 3 次第 4 次 429 vs 会员不限/prefs 读写(绑定态来自 jian_prefs)。
"""
import os, sys, tempfile
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
    def select_lucky_days(self, scene="", start_date="", end_date="", user_bazi=None,
                          exclude_dates=None, prefer_weekend=False):
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
TOKEN = _auth.create_user_token(UID)
TOKEN2 = _auth.create_user_token(UID2)
TOKENM = _auth.create_user_token(UIDM)
h = {"Authorization": f"Bearer {TOKEN}"}
h2 = {"Authorization": f"Bearer {TOKEN2}"}
hm = {"Authorization": f"Bearer {TOKENM}"}
_member_dao.set_member(UIDM, "basic")

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

# 9. 会员全量历史（mock 会员）
for dt in ["2026-08-01", "2026-08-05", "2026-08-09", "2026-08-13", "2026-08-17"]:
    rr = client.post("/api/zeri/select", headers=hm, json={"scene": "开业", "lucky_date": dt,
                                                           "card": {**CARD, "date": dt}, "items": []})
    assert rr.status_code == 200, rr.text
r = client.get("/api/zeri/plans", headers=hm)
check("会员全量历史 5 条", len(r.json()["plans"]) == 5)
check("会员 more_requires_member=false", r.json()["more_requires_member"] is False)

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
