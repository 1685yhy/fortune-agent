# 明灯晨笺(早晚双笺)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现"明灯晨笺":服务号模板消息早晚双笺(用户自选时间+主动同意制),古籍金句管线+轻私语管线,今日页晨笺卡+笺匣+订阅设置,三版小程序同步。

**Architecture:** 复用现有 `_daily_precompute_worker`(每小时预计算)与 `_daily_push_worker`(每分钟检查推送)架构:金句 0 点预生成;推送任务按用户偏好时间分组下发服务号模板消息;前端三版(墨韵为准)新增晨笺卡/引导页/订阅设置/笺匣分类。

**Tech Stack:** FastAPI + httpx + sqlite(现有 dao 模式)、微信服务号模板消息 API、小程序原生(三版)、测试用 scripts/test_*.py 脚本(TestClient+断言)。

## Global Constraints

- 所有后端代码遵循现有模式:DAO 在 `src/storage/*_dao.py`,API 在 `src/api/*.py`(挂 require_user 鉴权),引擎在 `src/engines/*.py`,env 经 `src/config.load_env_file(".env")`
- 测试文件放 `scripts/test_*.py`,运行 `.venv/bin/python3 scripts/test_xxx.py`,全部命令 `cd /mnt/e/fortune-agent` 后执行
- 前端三版同步:miniprogram(墨韵,正版)/ miniprogram_simple / miniprogram_fusion,结构一致仅皮肤
- 推送红线(spec):用户主动开启+选时+确认才推送;未开启绝不发;随时可关;私语只出分类级信息(不点名事件/人物/原话)
- 金句红线:真出自古籍(有书名)、宁缺毋滥(无合格金句当日不出金句,绝不编造)
- 服务号配置 env:MP_APP_ID / MP_APP_SECRET / MP_JIAN_TEMPLATE_ID / MP_NIGHT_TEMPLATE_ID(新增)
- 不 commit 敏感配置(.env 已 gitignore)

---

### Task 1: 晨笺订阅偏好存储(DAO)

**Files:**
- Create: `src/storage/jian_dao.py`
- Test: `scripts/test_jian_pref.py`

**Interfaces:**
- Consumes: 无(独立新表)
- Produces: `JianPrefDAO(conn)`;`get_pref(user_id) -> dict|None`(键:user_id/jian_enabled/jian_time/night_enabled/night_time/bound_status/updated_at);`upsert_pref(user_id, prefs: dict)`;`list_enabled_at(time_hm: str, kind: str) -> list[str]`(kind='jian'|'night',返回该时刻应推送的 user_id 列表);`count_enabled() -> int`

- [ ] **Step 1: 写失败测试**

创建 `scripts/test_jian_pref.py`:
```python
"""晨笺订阅偏好 DAO 测试"""
import os, sys, tempfile, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.storage.jian_dao import JianPrefDAO

def make_dao():
    fd, path = tempfile.mkstemp(suffix='.db'); os.close(fd)
    return JianPrefDAO(sqlite3.connect(path)), path

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

# 1. 建表+默认无数据
dao, path = make_dao()
check("初始无偏好", dao.get_pref("u1") is None)

# 2. upsert 插入
dao.upsert_pref("u1", {"jian_enabled": 1, "jian_time": "07:30",
                       "night_enabled": 1, "night_time": "23:00", "bound_status": "bound"})
p = dao.get_pref("u1")
check("插入成功", p and p["jian_time"] == "07:30" and p["jian_enabled"] == 1)

# 3. upsert 部分更新(只改时间不丢其他字段)
dao.upsert_pref("u1", {"jian_time": "08:00"})
p = dao.get_pref("u1")
check("部分更新保留", p["jian_time"] == "08:00" and p["night_time"] == "23:00")

# 4. list_enabled_at 按时刻筛选
dao.upsert_pref("u2", {"jian_enabled": 1, "jian_time": "07:30", "night_enabled": 0, "night_time": "", "bound_status": "bound"})
dao.upsert_pref("u3", {"jian_enabled": 0, "jian_time": "07:30", "night_enabled": 0, "night_time": "", "bound_status": "bound"})
r = dao.list_enabled_at("07:30", "jian")
check("7:30 晨笺组", "u1" in r and "u2" in r and "u3" not in r)
r2 = dao.list_enabled_at("23:00", "night")
check("23:00 晚安组", "u1" in r2)

# 5. 未绑定不可推送
dao.upsert_pref("u4", {"jian_enabled": 1, "jian_time": "07:30", "bound_status": "unbound"})
r3 = dao.list_enabled_at("07:30", "jian")
check("未绑定排除", "u4" not in r3)

print(f"\nALL PASS ({ok})")
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /mnt/e/fortune-agent && .venv/bin/python3 scripts/test_jian_pref.py`
Expected: `ModuleNotFoundError: No module named 'src.storage.jian_dao'`

- [ ] **Step 3: 实现 DAO**

创建 `src/storage/jian_dao.py`:
```python
"""晨笺订阅偏好存储。表 jian_prefs: user_id 主键; 开关/时间/绑定状态。"""
import json, time

class JianPrefDAO:
    def __init__(self, conn):
        self.conn = conn
        self.conn.execute("""CREATE TABLE IF NOT EXISTS jian_prefs (
            user_id TEXT PRIMARY KEY,
            jian_enabled INTEGER DEFAULT 0,
            jian_time TEXT DEFAULT '07:30',
            night_enabled INTEGER DEFAULT 0,
            night_time TEXT DEFAULT '23:00',
            bound_status TEXT DEFAULT 'unbound',
            mp_openid TEXT DEFAULT '',
            updated_at REAL
        )""")
        self.conn.commit()

    def get_pref(self, user_id: str):
        row = self.conn.execute("SELECT * FROM jian_prefs WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.execute("SELECT * FROM jian_prefs").description]
        return dict(zip(cols, row))

    def upsert_pref(self, user_id: str, prefs: dict):
        cur = self.conn.execute("SELECT * FROM jian_prefs WHERE user_id=?", (user_id,)).fetchone()
        cols = [d[0] for d in self.conn.execute("SELECT * FROM jian_prefs").description]
        data = dict(zip(cols, cur)) if cur else {}
        data.update(prefs)
        data["user_id"] = user_id
        data["updated_at"] = time.time()
        self.conn.execute("""INSERT INTO jian_prefs (user_id, jian_enabled, jian_time,
            night_enabled, night_time, bound_status, mp_openid, updated_at)
            VALUES (:user_id,:jian_enabled,:jian_time,:night_enabled,:night_time,:bound_status,:mp_openid,:updated_at)
            ON CONFLICT(user_id) DO UPDATE SET
            jian_enabled=excluded.jian_enabled, jian_time=excluded.jian_time,
            night_enabled=excluded.night_enabled, night_time=excluded.night_time,
            bound_status=excluded.bound_status, mp_openid=excluded.mp_openid,
            updated_at=excluded.updated_at""", data)
        self.conn.commit()

    def list_enabled_at(self, time_hm: str, kind: str) -> list:
        col, time_col = ("jian_enabled", "jian_time") if kind == "jian" else ("night_enabled", "night_time")
        rows = self.conn.execute(
            f"SELECT user_id FROM jian_prefs WHERE {col}=1 AND {time_col}=? AND bound_status='bound'",
            (time_hm,)).fetchall()
        return [r[0] for r in rows]

    def count_enabled(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM jian_prefs WHERE jian_enabled=1 OR night_enabled=1").fetchone()
        return row[0]
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python3 scripts/test_jian_pref.py`
Expected: `ALL PASS (5)`

- [ ] **Step 5: Commit**

```bash
git add src/storage/jian_dao.py scripts/test_jian_pref.py
git commit -m "feat(jian): 晨笺订阅偏好 DAO(开关/时间/绑定状态,含按时刻筛选)"
```

---

### Task 2: 服务号模板消息封装

**Files:**
- Create: `src/services/wechat_mp.py`
- Test: `scripts/test_wechat_mp.py`

**Interfaces:**
- Consumes: env `MP_APP_ID/MP_APP_SECRET/MP_JIAN_TEMPLATE_ID/MP_NIGHT_TEMPLATE_ID`
- Produces: `get_mp_access_token() -> str`(带 7000s 缓存);`send_template(openid: str, template_id: str, data: dict, url: str = "") -> dict`(返回微信响应 dict;网络/鉴权失败抛 `MpError`);`mp_ready() -> bool`(四 env 齐)

- [ ] **Step 1: 写失败测试**

创建 `scripts/test_wechat_mp.py`:
```python
"""服务号模板消息封装测试(mock httpx)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest.mock as mock
from src.services.wechat_mp import get_mp_access_token, send_template, mp_ready, _reset_cache

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

# 1. mp_ready 缺 env 判定
os.environ.pop("MP_APP_ID", None); os.environ.pop("MP_APP_SECRET", None)
os.environ.pop("MP_JIAN_TEMPLATE_ID", None); os.environ.pop("MP_NIGHT_TEMPLATE_ID", None)
check("未配置时 ready=False", not mp_ready())

os.environ["MP_APP_ID"] = "wx_test"; os.environ["MP_APP_SECRET"] = "sec"
os.environ["MP_JIAN_TEMPLATE_ID"] = "tpl_j"; os.environ["MP_NIGHT_TEMPLATE_ID"] = "tpl_n"
check("配置齐全时 ready=True", mp_ready())

# 2. access_token 获取+缓存
_reset_cache()
fake_resp = mock.Mock(); fake_resp.raise_for_status = lambda: None
fake_resp.json = lambda: {"access_token": "tok_1", "expires_in": 7200}
with mock.patch("httpx.get", return_value=fake_resp) as m:
    t1 = get_mp_access_token()
    check("token 获取", t1 == "tok_1")
    t2 = get_mp_access_token()
    check("token 缓存复用(只请求一次)", m.call_count == 1)

# 3. 发送模板消息
_reset_cache()
with mock.patch("httpx.get", return_value=fake_resp), \
     mock.patch("httpx.post", return_value=fake_resp) as mpost:
    r = send_template("openid1", "tpl_j", {"thing1": {"value": "晨笺"}})
    check("发送成功返回微信响应", r.get("access_token") == "tok_1")
    body = mpost.call_args.kwargs.get("json") or mpost.call_args.args[1]
    check("发送带 openid", body["touser"] == "openid1")
    check("发送带模板", body["template_id"] == "tpl_j")

print(f"\nALL PASS ({ok})")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python3 scripts/test_wechat_mp.py`
Expected: `ModuleNotFoundError: No module named 'src.services.wechat_mp'`

- [ ] **Step 3: 实现封装**

创建 `src/services/wechat_mp.py`:
```python
"""微信服务号模板消息封装(晨笺/晚安推送通道)。"""
import logging, os, time
import httpx

logger = logging.getLogger(__name__)
_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
_SEND_URL = "https://api.weixin.qq.com/cgi-bin/message/template/send"

_token_cache: dict = {"token": "", "expire_at": 0.0}

def _env(key: str) -> str:
    return os.getenv(key, "").strip()

def mp_ready() -> bool:
    return all(_env(k) for k in ("MP_APP_ID", "MP_APP_SECRET", "MP_JIAN_TEMPLATE_ID", "MP_NIGHT_TEMPLATE_ID"))

def _reset_cache() -> None:
    _token_cache.update({"token": "", "expire_at": 0.0})

class MpError(Exception):
    pass

def get_mp_access_token() -> str:
    if _token_cache["token"] and time.time() < _token_cache["expire_at"]:
        return _token_cache["token"]
    r = httpx.get(_TOKEN_URL, params={
        "grant_type": "client_credential",
        "appid": _env("MP_APP_ID"),
        "secret": _env("MP_APP_SECRET"),
    }, timeout=10)
    r.raise_for_status()
    data = r.json()
    if "access_token" not in data:
        raise MpError(f"access_token 失败: {data}")
    _token_cache["token"] = data["access_token"]
    _token_cache["expire_at"] = time.time() + max(60, int(data.get("expires_in", 7200)) - 200)
    return _token_cache["token"]

def send_template(openid: str, template_id: str, data: dict, url: str = "") -> dict:
    token = get_mp_access_token()
    body = {"touser": openid, "template_id": template_id, "data": data}
    if url:
        body["url"] = url
    r = httpx.post(_SEND_URL, params={"access_token": token}, json=body, timeout=10)
    r.raise_for_status()
    result = r.json()
    if result.get("errcode") not in (0, None):
        raise MpError(f"模板发送失败: {result}")
    return result
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python3 scripts/test_wechat_mp.py`
Expected: `ALL PASS (6)`

- [ ] **Step 5: Commit**

```bash
git add src/services/wechat_mp.py scripts/test_wechat_mp.py
git commit -m "feat(jian): 服务号模板消息封装(access_token 缓存+模板发送+ready 判定)"
```

---

### Task 3: 晨笺偏好 API(绑定状态+开关+时间自选)

**Files:**
- Create: `src/api/jian.py`
- Modify: `src/main.py`(挂 router,参考现有 include_router)
- Test: `scripts/test_jian_api.py`

**Interfaces:**
- Consumes: `JianPrefDAO`(Task 1)
- Produces: `GET /api/jian/prefs -> {prefs: {...}}`;`PUT /api/jian/prefs` body `{jian_enabled?, jian_time?, night_enabled?, night_time?}` -> `{prefs}`;`PUT /api/jian/bind` body `{mp_openid: str}` -> `{bound: true}`(校验时间格式 HH:MM,非法 400;未登录 401)

- [ ] **Step 1: 写失败测试**

创建 `scripts/test_jian_api.py`:
```python
"""晨笺偏好/绑定 API 测试(TestClient+临时 DB+假 token)"""
import os, sys, tempfile, sqlite3
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
from src.storage.jian_dao import JianPrefDAO
import src.api.jian as jian_mod
from src.main import app

_test_dao = JianPrefDAO(dao_mod.get_conn())
jian_mod._pref_dao = _test_dao
app.state.jian_pref_dao = _test_dao

client = TestClient(app)
TOKEN = "dev-token-test-user-jian"  # 参考 test_p2 假 token 机制(dev 模式)

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

print(f"\nALL PASS ({ok})")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python3 scripts/test_jian_api.py`
Expected: `ModuleNotFoundError: No module named 'src.api.jian'`(或 404)

- [ ] **Step 3: 实现 API**

创建 `src/api/jian.py`:
```python
"""晨笺订阅 API:偏好读写(开关+时间自选)+服务号绑定。全挂 require_user。"""
import logging, re
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from src.storage.jian_dao import JianPrefDAO
from src.security.auth import require_user  # 与现有 API 相同鉴权

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jian", tags=["jian"])

_pref_dao: Optional[JianPrefDAO] = None

def _dao() -> JianPrefDAO:
    global _pref_dao
    if _pref_dao is None:
        from src.storage.dao import get_conn
        _pref_dao = JianPrefDAO(get_conn())
    return _pref_dao

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

class PrefUpdate(BaseModel):
    jian_enabled: Optional[bool] = None
    jian_time: Optional[str] = None
    night_enabled: Optional[bool] = None
    night_time: Optional[str] = None

class BindBody(BaseModel):
    mp_openid: str

@router.get("/prefs")
def get_prefs(uid: str = Depends(require_user)):
    prefs = _dao().get_pref(uid) or {"user_id": uid, "jian_enabled": 0, "jian_time": "07:30",
                                     "night_enabled": 0, "night_time": "23:00", "bound_status": "unbound"}
    return {"prefs": prefs}

@router.put("/prefs")
def put_prefs(body: PrefUpdate, uid: str = Depends(require_user)):
    patch = {}
    for k in ("jian_time", "night_time"):
        v = getattr(body, k)
        if v is not None:
            if not _TIME_RE.match(v):
                raise HTTPException(status_code=400, detail=f"非法时间格式: {v}")
            patch[k] = v
    if body.jian_enabled is not None:
        patch["jian_enabled"] = 1 if body.jian_enabled else 0
    if body.night_enabled is not None:
        patch["night_enabled"] = 1 if body.night_enabled else 0
    _dao().upsert_pref(uid, patch)
    return {"prefs": _dao().get_pref(uid)}

@router.put("/bind")
def bind(body: BindBody, uid: str = Depends(require_user)):
    _dao().upsert_pref(uid, {"bound_status": "bound", "mp_openid": body.mp_openid})
    return {"bound": True}
```

在 `src/main.py` 挂载(与现有 router 并列):
```python
from src.api.jian import router as jian_router
app.include_router(jian_router)
```
注:`mp_openid` 列已在 Task 1 建表语句中包含(新增库直接创建;已有库用 `ALTER TABLE jian_prefs ADD COLUMN mp_openid TEXT DEFAULT ''` 兼容)。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python3 scripts/test_jian_api.py`
Expected: `ALL PASS (5)`

- [ ] **Step 5: Commit**

```bash
git add src/api/jian.py src/storage/jian_dao.py src/main.py scripts/test_jian_api.py
git commit -m "feat(jian): 晨笺偏好 API(时间自选/开关/绑定,全接口鉴权)"
```

---

### Task 4: 古籍金句管线

**Files:**
- Create: `src/engines/jian_quote.py`
- Test: `scripts/test_jian_quote.py`

**Interfaces:**
- Consumes: `src/engines/calendar.LuckyCalendar`(干支)、RAG 检索函数(参考 `src/rag/web_search.py` 的模块约定;检索入口 `src/rag/` 现有 `search` 能力)、env `DEEPSEEK_API_KEY`
- Produces: `generate_daily_quote(date_str: str, day_ganzhi: str) -> dict|None`;返回 `{quote, book, translation, matched_advice}` 或 None(无合格金句);`QUOTE_CACHE`(dict,按 date_str,模块级)

- [ ] **Step 1: 写失败测试**

创建 `scripts/test_jian_quote.py`:
```python
"""古籍金句管线测试(规则部分纯逻辑;RAG/LLM 部分 mock)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest.mock as mock
from src.engines.jian_quote import generate_daily_quote, _filter_candidates, _reset_cache

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

# 1. 筛选规则:长度/书名/无书名剔除
cands = [
    {"text": "申月金旺,逢土生扶,反为有用之才", "book": "穷通宝鉴"},
    {"text": "太短", "book": "滴天髓"},
    {"text": "x" * 60, "book": "三命通会"},  # 超长
    {"text": "无名句", "book": ""},            # 无书名
]
good = _filter_candidates(cands)
check("长度与书名筛选", len(good) == 1 and good[0]["book"] == "穷通宝鉴")

# 2. 30 天不重复
_reset_cache()
fake_r = {"text": "申月金旺,逢土生扶,反为有用之才", "book": "穷通宝鉴"}
with mock.patch("src.engines.jian_quote._retrieve_candidates", return_value=[fake_r, fake_r]):
    r1 = generate_daily_quote("2026-08-10", "庚申")
    r2 = generate_daily_quote("2026-08-11", "辛酉")
    check("首日有金句", r1 is not None and r1["book"] == "穷通宝鉴")
    check("次日不重复", r2 is not None and r2["quote"] != r1["quote"] or r2 is None)

# 3. 无合格候选 → None(宁缺毋滥)
with mock.patch("src.engines.jian_quote._retrieve_candidates", return_value=[]):
    r3 = generate_daily_quote("2026-08-12", "壬戌")
    check("空候选返回 None", r3 is None)

print(f"\nALL PASS ({ok})")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python3 scripts/test_jian_quote.py`
Expected: `ModuleNotFoundError: No module named 'src.engines.jian_quote'`

- [ ] **Step 3: 实现管线**

创建 `src/engines/jian_quote.py`:
```python
"""古籍金句管线:干支→RAG 候选→规则筛选(书名/长度/30天不重复)→LLM 核对→None 兜底。"""
import logging, os, re
import httpx

logger = logging.getLogger(__name__)
_QUOTE_RECENT: list = []          # 最近 30 天已用金句
_RECENT_MAX = 30
QUOTE_CACHE: dict = {}

def _reset_cache():
    _QUOTE_RECENT.clear(); QUOTE_CACHE.clear()

def _retrieve_candidates(day_ganzhi: str) -> list:
    """RAG 检索:按干支+宜忌主题检索古籍块。返回 [{text, book}]。
    实现时接入现有 RAG(retriever/faiss 检索),候选 20 条。"""
    from src.rag import search_rag  # 实际按项目 RAG 入口调整
    hits = search_rag(f"{day_ganzhi} 命理 宜忌 古籍", top_k=20)
    return [{"text": h.get("text", ""), "book": h.get("book", h.get("source", ""))} for h in hits]

def _filter_candidates(cands: list) -> list:
    out = []
    for c in cands:
        t = (c.get("text") or "").strip().replace("\n", "")
        book = (c.get("book") or "").strip()
        if not book or not 8 <= len(t) <= 45:
            continue
        if any(p in t for p in ("http", "www.", "{{", "}}")):
            continue
        out.append({"text": t, "book": book})
    return out

def _llm_verify(quote: str, book: str, day_ganzhi: str) -> str:
    """LLM 核对金句与当日干支是否呼应;不呼应返回空串。
    失败时宽容通过(仅日志),不阻断当日金句。"""
    key = os.getenv("DEEPSEEK_API_KEY", "")
    if not key:
        return quote
    try:
        r = httpx.post("https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "deepseek-chat",
                  "messages": [{"role": "user", "content":
                      f"今日干支:{day_ganzhi}。金句:「{quote}」出自《{book}》。"
                      "这句话与今日干支/宜忌是否呼应?只回答:是 或 否"}],
                  "max_tokens": 10, "temperature": 0},
            timeout=15)
        ans = r.json()["choices"][0]["message"]["content"].strip()
        return quote if "否" not in ans else ""
    except Exception as e:
        logger.warning("金句核对失败(宽容通过): %s", e)
        return quote

def generate_daily_quote(date_str: str, day_ganzhi: str) -> dict | None:
    if date_str in QUOTE_CACHE:
        return QUOTE_CACHE[date_str]
    cands = _filter_candidates(_retrieve_candidates(day_ganzhi))
    for c in cands:
        if c["text"] in _QUOTE_RECENT:
            continue
        if not _llm_verify(c["text"], c["book"], day_ganzhi):
            continue
        _QUOTE_RECENT.append(c["text"])
        if len(_QUOTE_RECENT) > _RECENT_MAX:
            _QUOTE_RECENT.pop(0)
        result = {"quote": c["text"], "book": c["book"],
                  "translation": "", "matched_advice": True, "date": date_str}
        QUOTE_CACHE[date_str] = result
        return result
    QUOTE_CACHE[date_str] = None
    logger.info("金句管线: %s 无合格金句(宁缺毋滥)", date_str)
    return None
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python3 scripts/test_jian_quote.py`
Expected: `ALL PASS (4)`

- [ ] **Step 5: Commit**

```bash
git add src/engines/jian_quote.py scripts/test_jian_quote.py
git commit -m "feat(jian): 古籍金句管线(干支→RAG→筛选→LLM核对→宁缺毋滥)"
```

---

### Task 5: 轻私语管线

**Files:**
- Create: `src/engines/jian_private.py`
- Test: `scripts/test_jian_private.py`

**Interfaces:**
- Consumes: `src/memory/user_memory.UserMemory.get_relevant_memories`(L3 召回)
- Produces: `generate_private_line(user_id: str, category: str = "") -> str`(分类模板;无记忆/红线过滤后通用句)

- [ ] **Step 1: 写失败测试**

创建 `scripts/test_jian_private.py`:
```python
"""轻私语管线测试:分类模板+红线(不点名事件/人物/原话)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest.mock as mock
from src.engines.jian_private import generate_private_line, _CATEGORY_TEMPLATES

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

# 1. 有事业类记忆 → 事业模板
fake_mem = [{"type": "topic", "subject": "事业", "content": "最近在找工作"}]
with mock.patch("src.engines.jian_private._recall", return_value=fake_mem):
    line = generate_private_line("u1")
    check("事业模板", "事业" in line and "找工作" not in line)  # 不点破具体事件

# 2. 无记忆 → 通用句
with mock.patch("src.engines.jian_private._recall", return_value=[]):
    line2 = generate_private_line("u2")
    check("无记忆通用句", len(line2) > 0)

# 3. 红线:任何输出不包含具体事件词
with mock.patch("src.engines.jian_private._recall", return_value=[{"type": "event", "subject": "换工作", "content": "2026年3月跳槽到字节"}]):
    line3 = generate_private_line("u3")
    check("红线过滤事件词", "字节" not in line3 and "跳槽" not in line3 and "3月" not in line3)

print(f"\nALL PASS ({ok})")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python3 scripts/test_jian_private.py`
Expected: `ModuleNotFoundError: No module named 'src.engines.jian_private'`

- [ ] **Step 3: 实现管线**

创建 `src/engines/jian_private.py`:
```python
"""轻私语管线:分类模板化输出,红线=不点名事件/人物/原话。"""
import logging
logger = logging.getLogger(__name__)

_CATEGORY_TEMPLATES = {
    "事业": "近日心事多与事业有关,今日宜主动一步。",
    "感情": "心中若有放不下的人,今日宜先与自己和解。",
    "健康": "近来易倦,今日宜早歇,养足精神再出发。",
    "财运": "财宜细水长流,今日花销三思而后行。",
}
_GENERIC = "今日诸事,宜缓不宜急。"

def _recall(user_id: str) -> list:
    """L3 记忆召回,取最相关 1 条(按 topic/event 分类)。"""
    try:
        from src.memory.user_memory import UserMemory
        mem = UserMemory()
        entries = mem.list_entries(user_id)
        if not entries:
            return []
        for e in entries[:5]:
            if e.get("type") in ("topic", "event", "profile"):
                return [e]
    except Exception as e:
        logger.warning("轻私语召回失败: %s", e)
    return []

def _classify(entry: dict) -> str:
    subj = str(entry.get("subject") or "") + str(entry.get("content") or "")
    for cat in _CATEGORY_TEMPLATES:
        if cat in subj:
            return cat
    return ""

def generate_private_line(user_id: str, category: str = "") -> str:
    if category in _CATEGORY_TEMPLATES:
        return _CATEGORY_TEMPLATES[category]
    entries = _recall(user_id)
    if not entries:
        return _GENERIC
    cat = _classify(entries[0])
    return _CATEGORY_TEMPLATES.get(cat, _GENERIC)
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python3 scripts/test_jian_private.py`
Expected: `ALL PASS (3)`

- [ ] **Step 5: Commit**

```bash
git add src/engines/jian_private.py scripts/test_jian_private.py
git commit -m "feat(jian): 轻私语管线(分类模板+红线:不点名事件/人物/原话)"
```

---

### Task 6: 每日预生成 + 按偏好时间调度发送

**Files:**
- Modify: `src/main.py`(扩展 `_daily_precompute_worker` 与 `_daily_push_worker`)
- Test: `scripts/test_jian_scheduler.py`

**Interfaces:**
- Consumes: Task 1 DAO、Task 2 封装、Task 4 金句、Task 5 私语
- Produces: `_precompute_jian_for(date_str) -> dict`(晨笺内容:宜忌+金句+通用私语,缓存 `date:jian:{date_str}`);`_send_jian_batch(dao, now_hm: str) -> dict`(按偏好时间下发,统计 {total, pushed, skipped, errors});晨笺消息含轻私语(用户维度,发送时生成)

- [ ] **Step 1: 写失败测试**

创建 `scripts/test_jian_scheduler.py`:
```python
"""调度逻辑测试:预生成缓存 + 按偏好时间批量发送(mock 发送通道)"""
import os, sys, tempfile, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest.mock as mock
from src.storage.jian_dao import JianPrefDAO
import src.main as main_mod

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

fd, path = tempfile.mkstemp(suffix='.db'); os.close(fd)
dao = JianPrefDAO(sqlite3.connect(path))
dao.upsert_pref("u1", {"jian_enabled": 1, "jian_time": "07:30", "night_enabled": 0, "night_time": "", "bound_status": "bound"})
dao.upsert_pref("u2", {"jian_enabled": 1, "jian_time": "08:00", "night_enabled": 0, "night_time": "", "bound_status": "bound"})

# 1. 预生成缓存
with mock.patch("src.engines.jian_quote.generate_daily_quote", return_value={"quote": "金句", "book": "穷通宝鉴"}):
    content = main_mod._precompute_jian_for("2026-08-11")
    check("预生成含宜忌", "suitable" in content and "quote" in content)

# 2. 按 07:30 只推 u1
sent = []
with mock.patch("src.services.wechat_mp.send_template",
                side_effect=lambda oid, tpl, data, url="": sent.append(oid) or {}), \
     mock.patch("src.services.wechat_mp.mp_ready", return_value=True):
    stats = main_mod._send_jian_batch(dao, "07:30", "jian")
    check("7:30 只推 u1", sent == ["u1"] and stats["pushed"] == 1)

print(f"\nALL PASS ({ok})")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python3 scripts/test_jian_scheduler.py`
Expected: `AttributeError: module 'src.main' has no attribute '_precompute_jian_for'`

- [ ] **Step 3: 实现**

在 `src/main.py` 增加(放在现有 worker 旁):
```python
async def _daily_jian_precompute():
    """每小时检查:当日晨笺内容未生成则预生成(金句+宜忌+通用私语)。"""
    from datetime import datetime, timezone, timedelta
    while True:
        try:
            now = datetime.now(timezone(timedelta(hours=8)))
            date_str = now.strftime("%Y-%m-%d")
            _precompute_jian_for(date_str)
        except Exception as e:
            logger.error("晨笺预生成异常: %s", e)
        await asyncio.sleep(3600)

def _precompute_jian_for(date_str: str) -> dict:
    from src.engines.calendar import LuckyCalendar
    cache = get_cache()
    key = f"date:jian:{date_str}"
    hit = cache.get(key)
    if hit:
        return hit
    from datetime import date as dt_date
    cal = LuckyCalendar("")
    day_stem, day_branch = cal._day_stem_branch(date_str)
    day_ganzhi = f"{day_stem}{day_branch}"
    from src.engines.jian_quote import generate_daily_quote
    quote = generate_daily_quote(date_str, day_ganzhi) or {}
    content = {
        "date": date_str, "day_ganzhi": day_ganzhi,
        "suitable": cal.daily_suitable(date_str) if hasattr(cal, "daily_suitable") else ["出行", "洽谈", "早起"],
        "unsuitable": cal.daily_unsuitable(date_str) if hasattr(cal, "daily_unsuitable") else ["借贷", "熬夜"],
        "quote": quote.get("quote", ""), "book": quote.get("book", ""),
        "generic_line": "今日诸事,宜缓不宜急。",
    }
    cache.set(key, content, ttl_seconds=3600 * 26)
    return content

def _send_jian_batch(dao, now_hm: str, kind: str = "jian") -> dict:
    """按偏好时间下发:晨笺(kind=jian)或晚安(kind=night)。"""
    from src.services.wechat_mp import send_template, mp_ready, _env
    stats = {"total": 0, "pushed": 0, "skipped": 0, "errors": 0}
    if not mp_ready():
        logger.info("服务号未配置,晨笺发送跳过")
        return stats
    uids = dao.list_enabled_at(now_hm, kind)
    stats["total"] = len(uids)
    tpl_key = "MP_JIAN_TEMPLATE_ID" if kind == "jian" else "MP_NIGHT_TEMPLATE_ID"
    tpl_id = _env(tpl_key)
    for uid in uids:
        try:
            pref = dao.get_pref(uid)
            openid = (pref or {}).get("mp_openid", "")
            if not openid:
                stats["skipped"] += 1
                continue
            from datetime import datetime, timezone, timedelta
            date_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
            if kind == "jian":
                content = _precompute_jian_for(date_str)
                from src.engines.jian_private import generate_private_line
                line = generate_private_line(uid)
                data = {
                    "thing1": {"value": f"{content.get('day_ganzhi','')}日"},
                    "thing2": {"value": f"宜{','.join(content.get('suitable',[])[:3])} 忌{','.join(content.get('unsuitable',[])[:3])}"},
                    "thing3": {"value": (content.get("quote","") or "")[:20]},
                    "thing4": {"value": line[:20]},
                }
                url = "pages/today/today"
            else:
                data = {"thing1": {"value": "夜深了,灯还亮着"}, "thing2": {"value": "明日运势:宜静不宜动,睡个好觉"}}
                url = "pages/chat/chat"
            send_template(openid, tpl_id, data, url=f"https://yilichat.com/{url}")
            stats["pushed"] += 1
        except Exception as e:
            logger.warning("晨笺发送失败 uid=%s: %s", uid, e)
            stats["errors"] += 1
    logger.info("晨笺批次完成: %s", stats)
    return stats
```

在 `_daily_push_worker`(或独立 worker)每分钟分支加入:
```python
if current_time.endswith(":00") or True:  # 每分钟检查,按 DAO 精确匹配
    from src.storage.jian_dao import JianPrefDAO
    jdao = JianPrefDAO(dao.conn)
    _send_jian_batch(jdao, current_time, "jian")
    _send_jian_batch(jdao, current_time, "night")
```
(实际实现时与现有 push worker 合并,复用每分钟循环;`dao.conn` 以现有全局 dao 为准。)

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python3 scripts/test_jian_scheduler.py`
Expected: `ALL PASS (2)`

- [ ] **Step 5: Commit**

```bash
git add src/main.py scripts/test_jian_scheduler.py
git commit -m "feat(jian): 晨笺预生成+按偏好时间调度发送(晨笺/晚安双通道)"
```

---

### Task 7: 晨笺内容 API(今日页晨笺卡数据)

**Files:**
- Modify: `src/api/jian.py`(加路由)
- Test: `scripts/test_jian_api.py`(追加用例)

**Interfaces:**
- Consumes: Task 6 `_precompute_jian_for`
- Produces: `GET /api/jian/today -> {date, day_ganzhi, suitable, unsuitable, quote, book, private_line, question}`(private_line 按用户生成;question 固定句式)

- [ ] **Step 1: 追加测试**

在 `scripts/test_jian_api.py` 末尾追加:
```python
# 6. 今日晨笺内容
with mock.patch("src.main._precompute_jian_for", return_value={
    "date": "2026-08-11", "day_ganzhi": "庚申",
    "suitable": ["出行", "洽谈"], "unsuitable": ["借贷"],
    "quote": "申月金旺", "book": "穷通宝鉴"}):
    r = client.get("/api/jian/today", headers=h)
    d = r.json()
    check("今日晨笺", r.status_code == 200 and d["day_ganzhi"] == "庚申")
    check("含私语", "private_line" in d and len(d["private_line"]) > 0)
    check("含小问", "question" in d)
```
(文件头补 `import unittest.mock as mock`)

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python3 scripts/test_jian_api.py`
Expected: 最后 3 项 FAIL(404)

- [ ] **Step 3: 实现路由**

在 `src/api/jian.py` 追加:
```python
from src.main import _precompute_jian_for  # 注意:循环 import 风险,改用模块内延迟导入

@router.get("/today")
def today_jian(uid: str = Depends(require_user)):
    from datetime import datetime, timezone, timedelta
    from src.main import _precompute_jian_for
    date_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    content = _precompute_jian_for(date_str)
    from src.engines.jian_private import generate_private_line
    line = generate_private_line(uid)
    return {
        "date": content["date"], "day_ganzhi": content["day_ganzhi"],
        "suitable": content["suitable"], "unsuitable": content["unsuitable"],
        "quote": content.get("quote", ""), "book": content.get("book", ""),
        "private_line": line,
        "question": "今天最想做成的一件事是什么?",
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python3 scripts/test_jian_api.py`
Expected: `ALL PASS (8)`

- [ ] **Step 5: Commit**

```bash
git add src/api/jian.py scripts/test_jian_api.py
git commit -m "feat(jian): 今日晨笺内容 API(宜忌+金句+私语+小问)"
```

---

### Task 8: 前端 · 开启引导页(主动同意+时间自选)

**Files:**
- Create: `miniprogram/pages/jian_onboard/jian_onboard.{js,json,wxml,wxss}`(simple/fusion 同)
- Modify: `miniprogram/app.json`(三版注册页面)

**Interfaces:**
- Consumes: `PUT /api/jian/prefs`、`PUT /api/jian/bind`(api.js 加方法);服务号关注引导(展示服务号二维码图/跳转)
- Produces: 开启流程:同意文案 → 时间自选(晨笺/晚安两档,默认 07:30/23:00 可改)→ 确认 → 写 prefs+绑定向导 → 完成态

**实现要点(wxml 结构)**:
- 墨韵视觉:宣纸底、朱砂印章「启」、双线笺框、时间选择用 picker(晨笺区间 06:00-10:00 步进 15 分钟;晚安 21:00-24:00)
- 文案明确:"每天早/晚一条运势推送,可随时关闭"(不得误导)
- 三态:未开启(展示引导)/ 已开启(展示当前设置,可改)/ 未绑定服务号(展示绑定引导)
- api.js 新增 `getJianPrefs()/putJianPrefs()/bindMp()`
- 入口:今日页晨笺卡未开启时"开启晨笺"按钮 + 设置页"消息订阅"区

**验证**:
- [ ] 三版 auto-preview 编译全绿
- [ ] IDE 自动化:今日页点"开启晨笺"→ 引导页 → 选时间 → 确认 → 回到今日页显示晨笺卡;console 无报错;截图+vision 确认视觉(无重叠/破图)

**Commit**:
```bash
git add miniprogram/pages/jian_onboard miniprogram_simple/pages/jian_onboard miniprogram_fusion/pages/jian_onboard miniprogram/app.json miniprogram_simple/app.json miniprogram_fusion/app.json
git commit -m "feat(jian): 开启引导页三版(主动同意+时间自选)"
```

---

### Task 9: 前端 · 今日页晨笺卡

**Files:**
- Modify: `miniprogram/pages/today/today.{js,wxml,wxss}`(三版)

**Interfaces:**
- Consumes: `GET /api/jian/today`;收藏复用现有收藏逻辑(kept 消息)
- Produces: 今日页顶部晨笺卡:干支+宜忌+金句(可展开:书名+白话)+轻私语+今日小问(点击进对话)+收藏钮;未开启时显示"开启晨笺"引导入口

**验证**:
- [ ] 三版编译全绿
- [ ] IDE 截图+vision:晨笺卡完整渲染(展开态/收起态)、收藏成功、小问跳对话
- [ ] Commit(三版)

---

### Task 10: 前端 · 设置页订阅区 + 笺匣"笺"分类

**Files:**
- Modify: `miniprogram/pages/settings/settings.{js,wxml,wxss}`(三版,"消息订阅"区:晨笺/晚安开关、时间 picker、绑定状态、个性化私语开关)
- Modify: `miniprogram/pages/favorites/favorites.{js,wxml}`(三版:新增"笺"分类 tab,展示收藏的晨笺)

**验证**:
- [ ] 三版编译全绿
- [ ] IDE 截图+vision:设置页订阅区(开关/时间/状态)、收藏页笺分类
- [ ] Commit(三版)

---

### Task 11: 集成验证 + 上线准备

**Files:** 无新文件

- [ ] 全量回归:`.venv/bin/python3 scripts/test_jian_pref.py && .venv/bin/python3 scripts/test_wechat_mp.py && .venv/bin/python3 scripts/test_jian_api.py && .venv/bin/python3 scripts/test_jian_quote.py && .venv/bin/python3 scripts/test_jian_private.py && .venv/bin/python3 scripts/test_jian_scheduler.py && .venv/bin/python3 scripts/test_p2.py` 全绿
- [ ] 三版 auto-preview 全绿 + 关键屏截图 vision 复核(今日页晨笺卡/引导页/设置页/收藏页)
- [ ] 服务重启(8767)+ /api/health + /api/jian/prefs 实测
- [ ] 交付清单:.env 需配置 MP_APP_ID/MP_APP_SECRET/MP_JIAN_TEMPLATE_ID/MP_NIGHT_TEMPLATE_ID(服务号注册与模板申请为用户侧,文档注明)
- [ ] Commit 收尾
