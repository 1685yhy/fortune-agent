"""抽灵签 DAO+API 测试(TestClient+临时 DB+假 token)
```
运行：.venv/bin/python3 scripts/test_qian_api.py
退出码：0=全部通过；1=有失败
```
覆盖：签文库 8 支结构与原型原文逐字一致性/未登录 401/draw 返回 8 支之一+字段完整+
poem 4 行(确定性 stub 摇中指定签)/save 收藏/重复收藏 already/无效签号 400/
history 近 20 条倒序+字段完整+用户隔离。
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from starlette.testclient import TestClient

os.environ["EXPERIENCE_MODE"] = ""

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

# 独立临时 DB 注入(get_conn() 读取 dao_mod._DB_PATH)
fd, path = tempfile.mkstemp(suffix='.db'); os.close(fd)
import src.storage.dao as dao_mod
dao_mod._DB_PATH = path
from src.storage.qian_dao import QianDAO
import src.api.qian as qian_mod
from src.main import app

_test_dao = QianDAO(dao_mod.get_conn())
qian_mod._dao = _test_dao
app.state.qian_dao = _test_dao

client = TestClient(app)

# 假 token 机制(仿 test_jian_api.py/test_zeri_api.py)
from src.security.auth import AuthHandler, set_auth_handler
_auth = AuthHandler()
set_auth_handler(_auth)
UID = "qian-test-user"
UID2 = "qian-test-user-b"
TOKEN = _auth.create_user_token(UID)
TOKEN2 = _auth.create_user_token(UID2)
h = {"Authorization": f"Bearer {TOKEN}"}
h2 = {"Authorization": f"Bearer {TOKEN2}"}

# ─────────────────────────── 签文库原型原文独立副本(与 src/api/qian.py 逐字比对) ───────────────────────────
# 来源: Task 3 签文库表(原型原文 8 支)——逐字使用,不得增删改。
LIB_REF = [
    {"no": 7, "jx": "上上签", "cls": "up",
     "poem": ["枯木逢春再发花", "云开月出见归鸦", "向来求事皆如意", "何必迟迟问晚霞"],
     "jie": "所求之事如枯木逢春,正是转机萌动之时。旧事可翻篇,新事有贵人扶持;心里想的那件事,放心去做,时机已到。",
     "suo": "所问诸事 · 皆可顺遂"},
    {"no": 12, "jx": "上吉签", "cls": "up",
     "poem": ["顺水行舟稳且安", "前头自有渡人滩", "莫嫌浪小行来缓", "过了此弯天地宽"],
     "jie": "此签主顺。眼前进展虽缓,却是一步一个脚印的稳;再过一程,便是开阔水面。莫急,莫改向,按原路走下去。",
     "suo": "事业 · 缓中得进"},
    {"no": 5, "jx": "中吉签", "cls": "mid",
     "poem": ["桥边问柳柳低头", "半是春来半是愁", "待得东风吹过岸", "一江明月照归舟"],
     "jie": "心有所悬,情有所牵。此事不必急于开口,待三五日后风头转向,自然有台阶可下。今夜先安睡,答案在梦里。",
     "suo": "感情 · 缓一缓再谈"},
    {"no": 9, "jx": "中吉签", "cls": "mid",
     "poem": ["灯下拾珠未辨真", "且看潮退见沙痕", "莫将心事轻言尽", "留待花时对故人"],
     "jie": "眼前的选项有真有假,先别急着亮底牌。过段时日水落石出,再谈不迟。守住口,就是守住机会。",
     "suo": "抉择 · 宜守待明"},
    {"no": 3, "jx": "中平签", "cls": "mid",
     "poem": ["山径独行莫问程", "一程烟雨一程晴", "他年若到桃源口", "再看落花听水声"],
     "jie": "此签平平,不算好也不算坏。独行路上,把每一步走稳就是赢;所求之事,七分靠自己,三分看机缘。",
     "suo": "诸事 · 平顺务实"},
    {"no": 15, "jx": "中平签", "cls": "mid",
     "poem": ["檐下听雨夜迟迟", "心事如丝结万枝", "莫问归期何日至", "且留灯影照人时"],
     "jie": "近来多思多虑,是时候给自己放个假。想不清的事,搁两日再看,多半自己就清了。灯下养神,胜过灯下纠结。",
     "suo": "心境 · 宜歇不宜急"},
    {"no": 1, "jx": "下签", "cls": "low",
     "poem": ["晚渡无舟且系缆", "明朝潮涨自浮还", "莫因一步留行处", "误了东风水一湾"],
     "jie": "此签提醒:眼下时机未熟,强行出发容易搁浅。建议缓行三日,等潮涨风转;不是不成,是时辰未到。",
     "suo": "出行 · 宜缓不宜急"},
    {"no": 20, "jx": "上吉签", "cls": "up",
     "poem": ["高阁登临眼界宽", "四方山水入栏杆", "此时不作凌云想", "更待何年展羽翰"],
     "jie": "此签大吉,主升阶与远见。站高一步看问题,眼前纠结自消;若有升迁、求学、远行之事,此时正是好时机。",
     "suo": "前程 · 可上层楼"},
]

# ─────────────────────────── 1. 签文库一致性(与独立副本逐字比对,防手误) ───────────────────────────
check("签文库恰 8 支", len(qian_mod.QIAN_LIBRARY) == 8)
check("签文库与独立副本逐字一致(含 no/jx/cls/poem/jie/suo 全字段)",
      qian_mod.QIAN_LIBRARY == LIB_REF)
for c in qian_mod.QIAN_LIBRARY:
    check(f"第{c['no']}签 poem 恰 4 行", isinstance(c["poem"], list) and len(c["poem"]) == 4)
    check(f"第{c['no']}签 cls 合法", c["cls"] in ("up", "mid", "low"))
check("签号互异且 QIAN_BY_NO 索引完整",
      len({c["no"] for c in qian_mod.QIAN_LIBRARY}) == 8
      and len(qian_mod.QIAN_BY_NO) == 8)

# ─────────────────────────── 2. 未登录 401 ───────────────────────────
check("draw 未登录 401", client.post("/api/qian/draw").status_code == 401)
check("save 未登录 401", client.post("/api/qian/save", json={"no": 7}).status_code == 401)
check("history 未登录 401", client.get("/api/qian/history").status_code == 401)

# ─────────────────────────── 3. draw ───────────────────────────
r = client.post("/api/qian/draw", headers=h)
check("draw 200", r.status_code == 200)
card = r.json()["card"]
check("draw 返回 8 支之一", card["no"] in qian_mod.QIAN_BY_NO)
check("draw 字段完整(no/jx/cls/poem/jie/suo)",
      all(k in card for k in ("no", "jx", "cls", "poem", "jie", "suo")))
check("draw poem 4 行", isinstance(card["poem"], list) and len(card["poem"]) == 4)
check("draw 签卡与签文库对应条目一致", card == qian_mod.QIAN_BY_NO[card["no"]])

# 随机性冒烟: 连续 30 摇全部有效(不 flaky: 不断言覆盖全 8 支)
for _ in range(30):
    c = client.post("/api/qian/draw", headers=h).json()["card"]
    assert c["no"] in qian_mod.QIAN_BY_NO and len(c["poem"]) == 4
check("连续 30 摇全部返回合法签卡", True)

# 确定性: stub random.choice 摇中指定签(验证端到端透传)
import src.api.qian as qian_mod2
_orig_choice = qian_mod2.random.choice
qian_mod2.random.choice = lambda seq: seq[2]   # 第 3 支 = 签五(中吉)
c = client.post("/api/qian/draw", headers=h).json()["card"]
qian_mod2.random.choice = _orig_choice
check("确定性 stub 摇中签五(poem 首行=桥边问柳柳低头)", c["no"] == 5 and c["poem"][0] == "桥边问柳柳低头")

# ─────────────────────────── 4. save 收藏 ───────────────────────────
r = client.post("/api/qian/save", headers=h, json={"no": 7})
check("save 首次收藏 saved=true", r.status_code == 200 and r.json()["saved"] is True
      and r.json()["already"] is False)
r = client.post("/api/qian/save", headers=h, json={"no": 7})
check("save 重复收藏 already=true(不报错)",
      r.status_code == 200 and r.json()["saved"] is False and r.json()["already"] is True)
r = client.post("/api/qian/save", headers=h, json={"no": 999})
check("save 无效签号 400", r.status_code == 400)
r = client.post("/api/qian/save", headers=h, json={"no": "abc"})
check("save 非法类型 422", r.status_code == 422)

# ─────────────────────────── 5. history 历史 ───────────────────────────
for no in (5, 20, 1):
    client.post("/api/qian/save", headers=h, json={"no": no})
r = client.get("/api/qian/history", headers=h)
items = r.json()["items"]
check("history 200 且含 4 条(7/5/20/1)", r.status_code == 200 and len(items) == 4)
check("history 倒序(最新在前)", [i["no"] for i in items] == [1, 20, 5, 7])
for i in items:
    check(f"history 字段完整(no={i['no']})",
          all(k in i for k in ("no", "jx", "poem_first", "drawn_at")))
check("history 诗首行与签文库一致", items[0]["poem_first"] == qian_mod.QIAN_BY_NO[1]["poem"][0]
      and items[0]["jx"] == qian_mod.QIAN_BY_NO[1]["jx"])

# 用户隔离: B 收藏不进 A 的历史
client.post("/api/qian/save", headers=h2, json={"no": 12})
r = client.get("/api/qian/history", headers=h)
check("用户隔离(B 收藏不混入 A 历史)", len(r.json()["items"]) == 4)
r = client.get("/api/qian/history", headers=h2)
check("B 自己的历史恰 1 条", len(r.json()["items"]) == 1
      and r.json()["items"][0]["no"] == 12)

print(f"\n=== 全部通过: {ok} 项 ===")
