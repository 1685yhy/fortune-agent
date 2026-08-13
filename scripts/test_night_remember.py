"""'要我记得吗'单轮落库测试:分类级脱敏 + 当晚一次 + API
运行: .venv/bin/python3 scripts/test_night_remember.py
退出码: 0=全部通过; 1=有失败
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from starlette.testclient import TestClient
from src.engines.night_classify import classify_night_topic

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

# 1. 分类规则(纯逻辑)
check("事业分类", classify_night_topic("你记得我下周要面试吗") == "事业")
check("感情分类", classify_night_topic("最近总想起他") == "感情")
check("健康分类", classify_night_topic("失眠了好几天") == "健康")
check("未命中其他", classify_night_topic("今天天气不错") == "其他")
check("空串其他", classify_night_topic("") == "其他")

# 2. API:分类级落库(原文不入记忆)+ 当晚仅一次
fd, path = tempfile.mkstemp(suffix='.db'); os.close(fd)
memdir = tempfile.mkdtemp(prefix="night_mem_")
os.environ["USER_MEMORY_DIR"] = memdir
import src.storage.dao as dao_mod
dao_mod._DB_PATH = path
import src.api.night as night_mod
from src.main import app
_test_dao = night_mod.NightPrefDAO(dao_mod.get_conn())
night_mod._pref_dao = _test_dao
app.state.night_pref_dao = _test_dao

client = TestClient(app)

# 假 token 机制(同 test_night_api):require_user 走模块级共享 get_auth_handler(),
# 必须由同一 AuthHandler 实例签发的真实 JWT 才可通过校验,非裸字符串。
# 注意:API 侧 uid 取 JWT sub 声明,即 create_user_token 的入参(记忆文件按 uid 命名,
# 不能用整个 JWT 当 uid——文件超长限制);因此断言一律用 UID 读记忆。
from src.security.auth import AuthHandler, set_auth_handler
_auth = AuthHandler()
set_auth_handler(_auth)
UID = "dev-token-test-user-night"
TOKEN = _auth.create_user_token(UID)
h = {"Authorization": f"Bearer {TOKEN}"}

r = client.post("/api/night/remember", headers=h,
                json={"message": "你记得我下周要面试吗"})
check("单轮落库", r.status_code == 200 and r.json()["remembered"] is True
      and r.json()["category"] == "事业")

from src.memory.user_memory import UserMemory
entries = UserMemory(base_dir=memdir).list_entries(UID)
check("分类级入库", len(entries) == 1 and entries[0]["type"] == "topic"
      and entries[0]["subject"] == "事业")
check("原文不落库", all("面试" not in e["content"] for e in entries))
check("当晚仅一次", client.post("/api/night/remember", headers=h,
                             json={"message": "面试"}).json()["remembered"] is False)

print(f"\nALL PASS ({ok})")
