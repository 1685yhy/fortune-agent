"""双人合盘聚合 API 测试（TestClient+临时 DB+假 token）"""
import os, re, sys
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

print(f"\nALL PASS ({ok})")
