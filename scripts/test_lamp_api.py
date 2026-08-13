"""灯语库 API 测试:今日灯语(生成+缓存)/收藏/历史(免费近3夜/会员全部)+预生成 worker"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest.mock as mock
from starlette.testclient import TestClient

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

fd, path = tempfile.mkstemp(suffix='.db'); os.close(fd)
import src.storage.dao as dao_mod
dao_mod._DB_PATH = path
from src.storage.lamp_dao import LampDAO
import src.api.night as night_mod
from src.main import app

_test_ldao = LampDAO(dao_mod.get_conn())
night_mod._lamp_dao = _test_ldao
app.state.night_lamp_dao = _test_ldao

class FakeMember:
    def __init__(self, plan): self.plan = plan
    def get_membership(self, uid): return {"plan": self.plan}
night_mod._member_dao = FakeMember("free")

# 假 token 机制(同 test_night_api.py):require_user 走模块级共享 get_auth_handler(),
# token 必须由同一 AuthHandler 实例签发(裸字符串会被 401 拒绝),uid 取 JWT sub。
from src.security.auth import AuthHandler, set_auth_handler
_auth = AuthHandler()
set_auth_handler(_auth)
UID = "dev-token-test-user-night"
TOKEN = _auth.create_user_token(UID)

# 测试日期以北京时间今天为基准(计划编写日 2026-08-12 的硬编码改为动态,跨日不失效)
from datetime import datetime, timedelta, timezone
_BJ = timezone(timedelta(hours=8))
_TODAY = datetime.now(_BJ).strftime("%Y-%m-%d")
def _d(n): return (datetime.now(_BJ) - timedelta(days=n)).strftime("%Y-%m-%d")

client = TestClient(app)
h = {"Authorization": f"Bearer {TOKEN}"}
_ldao = night_mod._lamp_dao

# 1. 未登录 401
check("未登录 401", client.get("/api/night/lamp/today").status_code == 401)

# 2. 今日灯语(免费:生成+入库,无语音)
with mock.patch("src.engines.night_soliloquy.build_soliloquy",
                return_value={"date": _TODAY, "text": "灯还亮着。晚安。灯下的人",
                              "anchors": ["事业"], "fallback": False}), \
     mock.patch("src.storage.session_dao.SessionDAO", return_value=object()):
    r = client.get("/api/night/lamp/today", headers=h)
    d = r.json()["lamp"]
    check("今日灯语生成", r.status_code == 200 and d["text"].startswith("灯还亮着"))
    check("免费无语音", "audio_url" not in d)
check("灯语落库", _ldao.get_lamp(UID, _TODAY) is not None)

# 3. 收藏/取消收藏/404
r = client.post("/api/night/lamp/favorite", headers=h, json={"date": _TODAY})
check("收藏", r.status_code == 200 and r.json()["favorited"] is True)
check("库内 favorite=1", _ldao.get_lamp(UID, _TODAY)["favorite"] == 1)
check("取消收藏", client.post("/api/night/lamp/favorite", headers=h,
                           json={"date": _TODAY}).json()["favorited"] is False)
check("不存在 404", client.post("/api/night/lamp/favorite", headers=h,
                             json={"date": "2020-01-01"}).status_code == 404)

# 4. 历史:免费仅近 3 夜
for day, txt in ((_d(3), "灯语A"), (_d(2), "灯语B"),
                 (_d(1), "灯语C"), (_TODAY, "灯语D")):
    _ldao.upsert_lamp(UID, day, txt)
r = client.get("/api/night/lamp/history", headers=h)
lamps = r.json()["lamps"]
check("免费仅近3夜", len(lamps) == 3 and lamps[0]["date"] == _TODAY)
check("免费无语音字段", all("audio_url" not in l for l in lamps))

# 5. 会员:历史全部 + 今日含语音
night_mod._member_dao = FakeMember("pro")
_ldao.upsert_lamp(UID, _TODAY, "灯语D", "http://127.0.0.1:8768/audio/d.mp3")
r = client.get("/api/night/lamp/history", headers=h)
check("会员全量", len(r.json()["lamps"]) == 4)
check("会员今日灯语带语音",
      "audio_url" in client.get("/api/night/lamp/today", headers=h).json()["lamp"])

# 5b. 会员:on-demand 今日灯语(未缓存)触发语音合成分支
MEMBER_UID = "dev-token-test-member-night"
MEMBER_TOKEN = _auth.create_user_token(MEMBER_UID)
h_m = {"Authorization": f"Bearer {MEMBER_TOKEN}"}
night_mod._member_dao = FakeMember("pro")
with mock.patch("src.engines.night_soliloquy.build_soliloquy",
                return_value={"date": _TODAY, "text": "灯还亮着。晚安。灯下的人",
                              "anchors": ["事业"], "fallback": False}), \
     mock.patch("src.engines.night_soliloquy.synth_lamp_audio",
                return_value="http://127.0.0.1:8768/audio/member.mp3") as m_synth, \
     mock.patch("src.storage.session_dao.SessionDAO", return_value=object()):
    r = client.get("/api/night/lamp/today", headers=h_m)
    d = r.json()["lamp"]
    check("会员on-demand触发语音合成", m_synth.called is True)
    check("会员今日灯语带audio_url",
          d.get("audio_url") == "http://127.0.0.1:8768/audio/member.mp3")
check("会员on-demand灯语落库", _ldao.get_lamp(MEMBER_UID, _TODAY) is not None)

# 6. 预生成 worker:订阅用户生成(ok),已入库跳过(skipped)
c = dao_mod.get_conn()
from src.storage.jian_dao import JianPrefDAO
JianPrefDAO(c)  # 确保 jian_prefs 表存在(临时库首建)
c.execute("INSERT INTO jian_prefs (user_id, jian_enabled, night_enabled, bound_status, mp_openid) "
          "VALUES (?, 0, 1, 'bound', 'oX')", (UID,))
c.commit()
import src.main as main_mod
with mock.patch("src.engines.night_soliloquy.build_soliloquy",
                return_value={"text": "灯还亮着。晚安。灯下的人", "fallback": False}), \
     mock.patch("src.engines.night_soliloquy.synth_lamp_audio", return_value="http://x/1.mp3"), \
     mock.patch("src.storage.session_dao.SessionDAO", return_value=object()), \
     mock.patch("src.storage.member_dao.MemberDAO", return_value=FakeMember("pro")):
    stats1 = main_mod._prewarm_night_lamps("2099-01-01", limit=5)
    check("预生成 ok=1", stats1["ok"] == 1)
    stats2 = main_mod._prewarm_night_lamps("2099-01-01", limit=5)
    check("已入库跳过", stats2["skipped"] == 1)

# 7. 终审:prewarm 按日轮转(ORDER BY user_id LIMIT n OFFSET (doy%分片)*n)
#    总订阅 5 人(UID + z-rot-a..d),limit=2 → 分片 3 → 连续 3 天分批覆盖全部且不重复
#    doy: 2099-01-01=1(1%3=1→offset2) / 2099-01-02=2(2%3=2→offset4) / 2099-01-03=3(0→offset0)
ROT_DAYS = ("2099-01-01", "2099-01-02", "2099-01-03")
ROT_USERS = ("dev-token-test-user-night", "z-rot-a", "z-rot-b", "z-rot-c", "z-rot-d")
for ru in ROT_USERS:
    c.execute("INSERT OR IGNORE INTO jian_prefs "
              "(user_id, jian_enabled, night_enabled, bound_status, mp_openid) "
              "VALUES (?, 0, 1, 'bound', 'oX')", (ru,))
c.commit()
with mock.patch("src.engines.night_soliloquy.build_soliloquy",
                return_value={"text": "灯还亮着。晚安。灯下的人", "fallback": False}), \
     mock.patch("src.engines.night_soliloquy.synth_lamp_audio", return_value="http://x/1.mp3"), \
     mock.patch("src.storage.session_dao.SessionDAO", return_value=object()), \
     mock.patch("src.storage.member_dao.MemberDAO", return_value=FakeMember("pro")):
    stats = {ds: main_mod._prewarm_night_lamps(ds, limit=2) for ds in ROT_DAYS}
check("轮转每天只生成一批评次(2/1/2)",
      [stats[d]["total"] for d in ROT_DAYS] == [2, 1, 2])
owners = {}
for ru in ROT_USERS:
    owners[ru] = sum(1 for d in ROT_DAYS if _ldao.get_lamp(ru, d) is not None)
# UID 在 2099-01-01 已由第 6 节(limit=5 全量)生成过,轮转日再取到属跨日正常;
# 关键不变量:三天分批(2/1/2)互不重叠 → 全部订阅用户均被覆盖、轮转新用户各恰好一次
check("轮转三天覆盖全部订阅用户且分批不重叠",
      all(owners[ru] >= 1 for ru in ROT_USERS)
      and all(owners[ru] == 1 for ru in ("z-rot-a", "z-rot-b", "z-rot-c", "z-rot-d")))

print(f"\nALL PASS ({ok})")
