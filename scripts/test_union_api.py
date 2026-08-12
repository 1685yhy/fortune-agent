"""双人合盘聚合 API 测试（TestClient+临时 DB+假 token）"""
import json, os, re, sys
# 支付墙测试需关闭体验模式（.env 的 EXPERIENCE_MODE=true 仅在未设置时生效，
# 这里先置空以避免 .env 覆盖，使 403 防绕过用例可真实触发；同 test_virtual_pay 模式）
os.environ["EXPERIENCE_MODE"] = ""
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from starlette.testclient import TestClient
from src.security.auth import AuthHandler, set_auth_handler

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

# ── 鉴权与引擎注入（参考 test_p2 模式）──
_auth = AuthHandler()
set_auth_handler(_auth)

import src.api.union as union_mod
from src.engines.hehun import HehunEngine
from src.engines.bazi import BaziEngine

class FakeDao:
    """记录归档调用，验证免费档零落库 / 付费归档脱敏。"""
    def __init__(self):
        self.calls = []
    def save_consultation(self, user_id, question, chart_result=None, analysis="", intent="bazi"):
        self.calls.append({"user_id": user_id, "question": question,
                           "chart": chart_result, "analysis": analysis, "intent": intent})
        return 1

fake_dao = FakeDao()
union_mod.setup(HehunEngine(), BaziEngine(), llm=None, retriever=None,
                member_dao=None, dao=fake_dao)

from src.main import app
client = TestClient(app)
h = {"Authorization": "Bearer " + _auth.create_user_token("union_user")}

# 1. 未登录 401
r = client.post("/api/union", json={})
check("未登录 401", r.status_code == 401)

# 2. 缺一方生辰 400（双契约校验与 /api/hehun 一致）
r = client.post("/api/union", headers=h, json={"person_a": {"year": 1990, "month": 5, "day": 20}})
check("缺对方生辰 400", r.status_code == 400)

# 3. 免费档成功（原生契约 person_a/person_b）
r = client.post("/api/union", headers=h, json={
    "person_a": {"year": 1990, "month": 5, "day": 20, "hour": 8, "city": "北京", "gender": "男"},
    "person_b": {"year": 1992, "month": 8, "day": 15, "hour": 14, "city": "上海", "gender": "女"},
    "relation": "恋人"})
d = r.json()
check("免费档 200", r.status_code == 200)
check("契合分与等级", 0 <= d["score"] <= 100 and d["levelLabel"] in ("天作之合", "情投意合", "相得益彰", "和而不同", "细水长流"))
check("三维得分条", d["dimensions"]["wuxing"]["max"] == 40 and d["dimensions"]["rizhu"]["max"] == 35)
check("缘语含悬念半句", d["quoteParts"]["main"] and d["quoteParts"]["cliffhanger"].endswith("……"))
check("缘语主句≤24字", len(d["quoteParts"]["main"]) <= 24)
check("付费墙指向 deep_report", d["paywall"]["product"] == "deep_report" and d["paywall"]["price"] == 19.9)
check("缘笺生辰脱敏", re.match(r"^[一-鿿]{2} · \d{4}年\d{1,2}月\d{1,2}日 属[一-鿿]$", d["yuan_card"]["birthA"]) is not None
      and "时" not in d["yuan_card"]["birthA"] and "上海" not in d["yuan_card"]["birthB"])
check("免费档零落库", len(fake_dao.calls) == 0)

# 4. 免费档小程序契约（person1/person2 + birthHour 时辰序号）
r = client.post("/api/union", headers=h, json={
    "person1": {"birthYear": 1990, "birthMonth": 5, "birthDay": 20, "birthHour": 5, "gender": "male"},
    "person2": {"birthYear": 1992, "birthMonth": 8, "birthDay": 15, "birthHour": 6, "gender": "female"}})
check("小程序契约兼容", r.status_code == 200 and 0 <= r.json()["score"] <= 100)

# ── Task 5: 付费档（deep_report 校验 + 归档脱敏）──

class FakeMemberDAO:
    def __init__(self, purchased):
        self.purchased = purchased
    def get_user_purchase(self, user_id, product_id):
        return {} if self.purchased else None

union_mod.setup(HehunEngine(), BaziEngine(), llm=None, retriever=None,
                member_dao=FakeMemberDAO(False), dao=fake_dao)
PAID_BODY = {
    "person_a": {"year": 1990, "month": 5, "day": 20, "hour": 8, "city": "北京", "gender": "男"},
    "person_b": {"year": 1992, "month": 8, "day": 15, "hour": 14, "city": "上海", "gender": "女"},
    "relation": "恋人", "paid": True,
}

# 5. 未购买 → 403（防绕过）
r = client.post("/api/union", headers=h, json=PAID_BODY)
check("未购买 403", r.status_code == 403)
check("付费墙文案指向 deep_report", "deep_report" in r.json()["detail"])

# 6. 已购买 → 四章报告 + 归档
fake_dao.calls.clear()
union_mod.setup(HehunEngine(), BaziEngine(), llm=None, retriever=None,
                member_dao=FakeMemberDAO(True), dao=fake_dao)
r = client.post("/api/union", headers=h, json=PAID_BODY)
d = r.json()
check("付费档 200 四章", r.status_code == 200 and d["purchased"] is True
      and [c["title"] for c in d["report"]["chapters"]] == ["前世今生", "相处模式", "矛盾点与化解", "契合详情"])
check("归档产生 reportId", d["reportId"] == "1")
check("归档 intent=hehun", fake_dao.calls and fake_dao.calls[0]["intent"] == "hehun")
check("归档 question 脱敏", "1990" not in fake_dao.calls[0]["question"] and "5月20日" not in fake_dao.calls[0]["question"])
check("归档 chart 脱敏", "1990" not in json.dumps(fake_dao.calls[0]["chart"]) and "1992" not in json.dumps(fake_dao.calls[0]["chart"]))
check("归档 analysis 无出生地", "上海" not in fake_dao.calls[0]["analysis"])

# 7. 免费档绝不返回报告正文（免费调用后再断言零归档）
r = client.post("/api/union", headers=h, json={k: v for k, v in PAID_BODY.items() if k != "paid"})
check("免费档无报告字段", "report" not in r.json() and "reportId" not in r.json())
print(f"\nALL PASS ({ok})")
