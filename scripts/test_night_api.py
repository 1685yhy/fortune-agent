"""深夜偏好/状态 API 测试(TestClient+临时 DB+假 token)
```
运行：.venv/bin/python3 scripts/test_night_api.py
退出码：0=全部通过；1=有失败
```
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from starlette.testclient import TestClient

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

# 独立临时 DB 注入
fd, path = tempfile.mkstemp(suffix='.db'); os.close(fd)
import src.storage.dao as dao_mod
dao_mod._DB_PATH = path
import src.api.night as night_mod
from src.main import app

_test_dao = night_mod.NightPrefDAO(dao_mod.get_conn())
night_mod._pref_dao = _test_dao
app.state.night_pref_dao = _test_dao

client = TestClient(app)

# 假 token 机制（同 test_jian_api）：require_user 走模块级共享 get_auth_handler()，
# token 必须由同一 AuthHandler 实例签发才可通过校验，因此这里用 AuthHandler
# 签发的真实 JWT（dev 测试用户），非裸字符串。
from src.security.auth import AuthHandler, set_auth_handler
_auth = AuthHandler()
set_auth_handler(_auth)
TOKEN = _auth.create_user_token("dev-token-test-user-night")

# 1. 未登录 401
check("未登录 401", client.get("/api/night/prefs").status_code == 401)

h = {"Authorization": f"Bearer {TOKEN}"}

# 2. 默认偏好
r = client.get("/api/night/prefs", headers=h)
check("默认档位标准", r.status_code == 200 and r.json()["prefs"]["preset"] == "standard")
check("默认灯语定时 15", r.json()["prefs"]["lamp_timer_min"] == 15)

# 3. 保存档位/定时/开关
r = client.put("/api/night/prefs", headers=h,
               json={"preset": "night", "lamp_timer_min": 30, "effect_enabled": False})
check("保存档位", r.status_code == 200 and r.json()["prefs"]["preset"] == "night")
check("保存定时", r.json()["prefs"]["lamp_timer_min"] == 30)
check("保存开关", r.json()["prefs"]["effect_enabled"] == 0)

# 4. 非法参数 400
check("非法档位 400", client.put("/api/night/prefs", headers=h, json={"preset": "dusk"}).status_code == 400)
check("非法定时 400", client.put("/api/night/prefs", headers=h, json={"lamp_timer_min": 7}).status_code == 400)

# 5. 状态接口(含时段窗;night_mode 与真实时钟相关,只断言存在)
r = client.get("/api/night/status", headers=h)
check("状态含时段窗", r.status_code == 200 and r.json()["window"] == "22:00-02:00" and "night_mode" in r.json())

print(f"\nALL PASS ({ok})")
