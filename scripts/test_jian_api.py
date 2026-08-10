"""晨笺偏好/绑定 API 测试(TestClient+临时 DB+假 token)
```
运行：.venv/bin/python3 scripts/test_jian_api.py
退出码：0=全部通过；1=有失败
```
"""
import os, sys, tempfile, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from starlette.testclient import TestClient

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

# 独立临时 DB 注入（get_conn() 读取 dao_mod._DB_PATH）
fd, path = tempfile.mkstemp(suffix='.db'); os.close(fd)
import src.storage.dao as dao_mod
dao_mod._DB_PATH = path
from src.storage.jian_dao import JianPrefDAO
import src.api.jian as jian_mod
from src.main import app

_test_dao = JianPrefDAO(dao_mod.get_conn())
jian_mod._pref_dao = _test_dao
app.state.jian_pref_dao = _test_dao

client = TestClient(app)

# 假 token 机制（参考 test_p2 / test_virtual_pay）：require_user 走模块级共享
# get_auth_handler()，token 必须由同一 AuthHandler 实例签发才可通过校验，
# 因此这里用 AuthHandler 签发的真实 JWT（dev 测试用户），非裸字符串。
from src.security.auth import AuthHandler, set_auth_handler
_auth = AuthHandler()
set_auth_handler(_auth)
TOKEN = _auth.create_user_token("dev-token-test-user-jian")

# 1. 未登录 401
r = client.get("/api/jian/prefs")
check("未登录 401", r.status_code == 401)

h = {"Authorization": f"Bearer {TOKEN}"}
# 2. 默认偏好
r = client.get("/api/jian/prefs", headers=h)
check("默认偏好存在", r.status_code == 200 and r.json()["prefs"]["jian_time"] == "07:30")

# 3. 保存时间自选
r = client.put("/api/jian/prefs", headers=h, json={"jian_time": "08:15", "jian_enabled": True})
check("保存时间", r.status_code == 200 and r.json()["prefs"]["jian_time"] == "08:15")
check("开关保存", r.json()["prefs"]["jian_enabled"] == 1)

# 4. 非法时间 400
r = client.put("/api/jian/prefs", headers=h, json={"jian_time": "25:99"})
check("非法时间 400", r.status_code == 400)

# 5. 绑定
r = client.put("/api/jian/bind", headers=h, json={"mp_openid": "oXXXX"})
check("绑定成功", r.status_code == 200 and r.json()["bound"] is True)

# 6. 绑定限流:每用户 5 次/分钟,前 5 次放行,第 6 次 429(用独立用户隔离)
TOKEN2 = _auth.create_user_token("dev-token-test-user-jian-rl")
h2 = {"Authorization": f"Bearer {TOKEN2}"}
for i in range(5):
    r = client.put("/api/jian/bind", headers=h2, json={"mp_openid": f"oRL{i}"})
    assert r.status_code == 200, f"第{i+1}次绑定应放行,实际 {r.status_code}"
check("限流内 5 次放行", True)
r = client.put("/api/jian/bind", headers=h2, json={"mp_openid": "oRL-6th"})
check("第 6 次绑定 429", r.status_code == 429)
check("429 带 Retry-After", r.headers.get("Retry-After", "") != "")

print(f"\nALL PASS ({ok})")
