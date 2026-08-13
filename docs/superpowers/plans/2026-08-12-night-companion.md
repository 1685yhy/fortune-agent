# 深夜陪伴 · 灯下漫谈 实施计划

> **决策记录(终审 #3,2026-08-13):** 夜色主题保留暖纸(#F4EBD6 系),**不引入**深墨蓝/深色模式。理由:用户 2026-08-10 已拍板"夜间模式去掉,只留白天一个颜色",暖纸是白天配色的加深,符合既定决策;不修改代码。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现"深夜陪伴·灯下漫谈":21:00-01:00 深夜模式(时段判定+夜色宣纸主题)、今日页深夜入口(晚间出现/白天不出现)、枕边灯语管线(当天 L2 摘要→分类级温柔锚点→150-200 字独白→TTS 8768)、倾诉临时记忆模式(deepNight 参数,默认不落库+"要我记得吗"单轮分类级落库)、守夜人成就(连续 7 夜印章,本地存储)、晨笺 23:00 晚安推送改造为深夜第一入口、三版小程序同步。

**Architecture:** 复用晨笺全部底座:后端沿用 `_daily_jian_precompute`/`_send_jian_batch`/`_daily_push_worker` 的 worker 框架(灯语 22:30 预生成、晚安推送 night 分支改深夜版、temp 消息 24h 清理各加一个 worker);灯语生成管线独立 `src/engines/night_soliloquy.py`(L2 摘要→分类级锚点→LLM 独白→红线校验→兜底→TTS),灯语库/深夜偏好独立 DAO(与 jian_dao 同模式);倾诉走现有对话管线但 deepNight 时跳过 L2/L3/演化链写入并给会话消息打 temp 标记;前端三版(墨韵为准)新增夜色主题 token、深夜入口横幅、聊天页深夜模式(灯笼动效/挽留劝睡/灯语卡/要我记得吗按钮/12356 安全条)、灯下印记页、设置页深夜陪伴区。

**Tech Stack:** FastAPI + httpx + sqlite(现有 dao 模式)、deepseek-v4-flash(Anthropic 兼容端点,复用 `src/llm/client.deepseek_anthropic_completion`)、TTS 8768(edge-tts,柔缓女声 rate=-10%)、L2 摘要(SessionDAO.get_summary)、L3 记忆(UserMemory.add_entry)、微信服务号模板消息(复用 wechat_mp)、小程序原生(三版)、测试用 scripts/test_*.py 脚本(TestClient+断言,无网络依赖)。

## Global Constraints

- 所有后端代码遵循现有模式:DAO 在 `src/storage/*_dao.py`,API 在 `src/api/*.py`(挂 require_user 鉴权),引擎在 `src/engines/*.py`,env 经 `src/config.load_env_file(".env")`,复用 LLM 用 `deepseek_anthropic_completion`(Anthropic 兼容端点+关闭 thinking,见 src/llm/client.py)
- 测试文件放 `scripts/test_*.py`,运行 `.venv/bin/python3 scripts/test_xxx.py`,全部命令 `cd /mnt/e/fortune-agent` 后执行;LLM/TTS/网络一律 mock,测试必须离线可跑
- 前端三版同步:miniprogram(墨韵,正版)/ miniprogram_simple / miniprogram_fusion,结构一致仅皮肤
- 隐私红线(方案 §4):深夜对话默认临时不记录——deepNight 时跳过 L2 摘要压缩 / L3 事实与事件 / 演化链 / 情绪与主题计数;会话消息打 temp 标记,服务端 24h 硬清理兜底;"要我记得吗"只存单轮、只存分类级(不点破人名/事件/原话),一次同意仅当晚有效,次日回默认;灯语 LLM 只见分类级锚点不见原文(设计上杜绝引用原文)
- 时段红线:深夜模式默认 21:00-01:00(三档预设:早睡党 20:00-23:00 / 标准 21:00-01:00 / 夜猫子 22:00-02:00);**从晚安推送进入以入口为准**,白天也强制深夜模式(前端 options.entry='night' 短路时间判定);01:00 后熄灯回归白天,白天无任何深夜残留
- 高危关键词:检测到自伤/自杀类表述 → 后端已有 `_SELF_HARM_RE` + safety_flag 转介留痕,前端补 12356 心理援助热线提示卡(方案 §三)
- 免费/会员(方案 §五):深夜对话/点灯/守夜人/倾诉临时模式/"要我记得吗"全免费;枕边灯语文字版免费、语音版会员、历史回看免费近 3 夜/会员全部
- 复用清单:TTS(httpx 调 8768,rate -10%)、L2 摘要、L3 记忆、晨笺推送(wechat_mp + _send_jian_batch night 分支)、干支金句(engines.calendar.LuckyCalendar._day_stem_branch + engines.jian_quote.generate_daily_quote)、三版结构
- 不 commit 敏感配置(.env 已 gitignore);服务号模板(MP_NIGHT_TEMPLATE_ID)文案为深夜版,申请为用户侧

---

### Task 1: 深夜时段判定引擎

**Files:**
- Create: `src/engines/night_mode.py`
- Test: `scripts/test_night_mode.py`

**Interfaces:**
- Consumes: 无(纯时间逻辑)
- Produces: `NIGHT_PRESETS`(dict:early/standard/night → (start_hour, end_hour),end>start 表示跨日);`DEFAULT_PRESET = "standard"`;`night_window(preset) -> tuple`;`is_night_mode(now: datetime = None, preset: str = "standard") -> bool`(now 无时区视为北京时间;跨日处理:次日 0 点起至 end 点前仍算深夜,end 点整熄灯);`bj_now() -> datetime`

- [ ] **Step 1: 写失败测试**

创建 `scripts/test_night_mode.py`:
```python
"""深夜时段判定引擎测试:三档预设 + 跨日 + 边界(纯逻辑,无网络)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime
from src.engines.night_mode import is_night_mode, night_window

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

def t(h, m=0):
    return datetime(2026, 8, 12, h, m)

# 标准档 21:00-01:00
check("标准 21:00 亮灯", is_night_mode(t(21, 0)))
check("标准 20:59 未亮", not is_night_mode(t(20, 59)))
check("标准 23:59 深夜", is_night_mode(t(23, 59)))
check("标准 00:30 深夜(跨日)", is_night_mode(t(0, 30)))
check("标准 01:00 熄灯", not is_night_mode(t(1, 0)))

# 早睡党 20:00-23:00(不跨日)
check("早睡 20:00 亮灯", is_night_mode(t(20, 0), "early"))
check("早睡 23:30 熄灯", not is_night_mode(t(23, 30), "early"))
check("早睡 00:30 不跨日", not is_night_mode(t(0, 30), "early"))

# 夜猫子 22:00-02:00
check("夜猫 01:30 深夜", is_night_mode(t(1, 30), "night"))
check("夜猫 02:00 熄灯", not is_night_mode(t(2, 0), "night"))

# 白天 / 未知档位回退
check("白天 15:00 全档熄灯",
      not is_night_mode(t(15, 0)) and not is_night_mode(t(15, 0), "night"))
check("未知档位回退标准", is_night_mode(t(22, 0), "???") and night_window("???") == (21, 1))

print(f"\nALL PASS ({ok})")
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /mnt/e/fortune-agent && .venv/bin/python3 scripts/test_night_mode.py`
Expected: `ModuleNotFoundError: No module named 'src.engines.night_mode'`

- [ ] **Step 3: 实现引擎**

创建 `src/engines/night_mode.py`:
```python
"""深夜时段判定引擎(方案·灯下漫谈):默认 21:00-01:00,三档预设,跨日处理。

- 深夜窗 = [start, end);end > start 表示跨日(次日 end 点整熄灯,01:00 已属白天);
- 三档:早睡党 20:00-23:00 / 标准 21:00-01:00 / 夜猫子 22:00-02:00;
- 从晚安推送进入以入口为准,不校验时间(前端 options.entry='night' 短路本判定)。
"""
from datetime import datetime, timezone, timedelta

# preset -> (start_hour, end_hour);end > start 即跨日
NIGHT_PRESETS = {
    "early": (20, 23),
    "standard": (21, 1),
    "night": (22, 2),
}
DEFAULT_PRESET = "standard"
BJ_TZ = timezone(timedelta(hours=8))


def bj_now() -> datetime:
    """当前北京时间。"""
    return datetime.now(BJ_TZ)


def night_window(preset: str) -> tuple:
    """档位 -> (start_hour, end_hour);未知档位回退标准。"""
    return NIGHT_PRESETS.get(preset, NIGHT_PRESETS[DEFAULT_PRESET])


def is_night_mode(now: datetime = None, preset: str = DEFAULT_PRESET) -> bool:
    """是否深夜模式。now 无时区视为北京时间。

    窗语义:end > start 为同日窗 [start, end);end < start 为跨日窗
    [start, 24) ∪ [0, end)(次日 end 点整熄灯,01:00 已属白天)。
    """
    if now is None:
        now = bj_now()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=BJ_TZ)
    start, end = night_window(preset)
    h = now.hour
    if end > start:
        return start <= h < end          # 同日窗(早睡党 20:00-23:00)
    return start <= h or h < end         # 跨日窗(标准/夜猫子,含次日 0 点至 end 点前)
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python3 scripts/test_night_mode.py`
Expected: `ALL PASS (12)`

- [ ] **Step 5: Commit**

```bash
git add src/engines/night_mode.py scripts/test_night_mode.py
git commit -m "feat(night): 深夜时段判定引擎(21:00-01:00 三档预设,跨日处理)"
```

---

### Task 2: 深夜偏好 DAO + 偏好/状态 API

**Files:**
- Create: `src/storage/night_dao.py`(含 night_prefs 表 + night_remember 表)
- Create: `src/api/night.py`(prefs/status 路由)
- Modify: `src/main.py`(挂 router,参考现有 include_router)
- Test: `scripts/test_night_api.py`

**Interfaces:**
- Consumes: `get_conn()`(src.storage.dao,_DB_PATH 测试注入)
- Produces: `NightPrefDAO(conn)`:`get_pref(user_id) -> dict|None`(键:user_id/preset/effect_enabled/keep_enabled/lamp_timer_min/whisper_enabled/updated_at);`upsert_pref(user_id, prefs: dict)`;`get_remember(user_id, date) -> str|None`(当晚已记分类);`set_remember(user_id, date, category)`(当晚一次红线)
- API:`GET /api/night/prefs -> {prefs}`;`PUT /api/night/prefs` body `{preset?, effect_enabled?, keep_enabled?, lamp_timer_min?, whisper_enabled?}`(preset 限 early/standard/night,lamp_timer_min 限 5/10/15/30,非法 400;未登录 401);`GET /api/night/status -> {night_mode, preset, window}`

- [ ] **Step 1: 写失败测试**

创建 `scripts/test_night_api.py`:
```python
"""深夜偏好/状态 API 测试(TestClient+临时 DB+假 token)"""
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
TOKEN = "dev-token-test-user-night"  # dev 假 token 机制,同 test_jian_api
h = {"Authorization": f"Bearer {TOKEN}"}

# 1. 未登录 401
check("未登录 401", client.get("/api/night/prefs").status_code == 401)

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
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python3 scripts/test_night_api.py`
Expected: `ModuleNotFoundError: No module named 'src.storage.night_dao'`

- [ ] **Step 3: 实现 DAO 与 API**

创建 `src/storage/night_dao.py`:
```python
"""深夜偏好存储(方案·灯下漫谈)。表 night_prefs: 时段档位/点灯动效/深夜挽留/
灯语定时关闭/私语开关;表 night_remember: "要我记得吗"单轮落库(每用户每晚一次)。"""
import time, threading

class NightPrefDAO:
    def __init__(self, conn):
        self.conn = conn
        self._lock = threading.Lock()
        self.conn.execute("""CREATE TABLE IF NOT EXISTS night_prefs (
            user_id TEXT PRIMARY KEY,
            preset TEXT DEFAULT 'standard',
            effect_enabled INTEGER DEFAULT 1,
            keep_enabled INTEGER DEFAULT 1,
            lamp_timer_min INTEGER DEFAULT 15,
            whisper_enabled INTEGER DEFAULT 1,
            updated_at REAL
        )""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS night_remember (
            user_id TEXT NOT NULL,
            date TEXT NOT NULL,
            category TEXT NOT NULL,
            created_at REAL,
            PRIMARY KEY (user_id, date)
        )""")
        self.conn.commit()

    def get_pref(self, user_id: str):
        row = self.conn.execute("SELECT * FROM night_prefs WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.execute("SELECT * FROM night_prefs").description]
        return dict(zip(cols, row))

    def upsert_pref(self, user_id: str, prefs: dict):
        with self._lock:
            cur = self.conn.execute("SELECT * FROM night_prefs WHERE user_id=?", (user_id,)).fetchone()
            cols = [d[0] for d in self.conn.execute("SELECT * FROM night_prefs").description]
            data = dict(zip(cols, cur)) if cur else {}
            data.update(prefs)
            data["user_id"] = user_id
            data["updated_at"] = time.time()
            defaults = {"preset": "standard", "effect_enabled": 1, "keep_enabled": 1,
                        "lamp_timer_min": 15, "whisper_enabled": 1}
            for k, v in defaults.items():
                data.setdefault(k, v)
            self.conn.execute("""INSERT INTO night_prefs (user_id, preset, effect_enabled,
                keep_enabled, lamp_timer_min, whisper_enabled, updated_at)
                VALUES (:user_id,:preset,:effect_enabled,:keep_enabled,:lamp_timer_min,:whisper_enabled,:updated_at)
                ON CONFLICT(user_id) DO UPDATE SET
                preset=excluded.preset, effect_enabled=excluded.effect_enabled,
                keep_enabled=excluded.keep_enabled, lamp_timer_min=excluded.lamp_timer_min,
                whisper_enabled=excluded.whisper_enabled, updated_at=excluded.updated_at""", data)
            self.conn.commit()

    def get_remember(self, user_id: str, date: str) -> str | None:
        """当晚是否已"要我记得吗"落库;返回已记分类或 None。"""
        row = self.conn.execute(
            "SELECT category FROM night_remember WHERE user_id=? AND date=?",
            (user_id, date)).fetchone()
        return row[0] if row else None

    def set_remember(self, user_id: str, date: str, category: str):
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO night_remember (user_id, date, category, created_at) "
                "VALUES (?,?,?,?)", (user_id, date, category, time.time()))
            self.conn.commit()
```

创建 `src/api/night.py`:
```python
"""深夜陪伴 API(方案·灯下漫谈):偏好读写 + 深夜状态。全挂 require_user。
灯语(lamp/*)与倾诉单轮落库(remember)在 Task 4/6 追加到本模块。"""
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from src.storage.night_dao import NightPrefDAO
from src.security.auth import require_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/night", tags=["night"])

_pref_dao: Optional[NightPrefDAO] = None

def _pdao() -> NightPrefDAO:
    global _pref_dao
    if _pref_dao is None:
        from src.storage.dao import get_conn
        _pref_dao = NightPrefDAO(get_conn())
    return _pref_dao

_PRESET_SET = {"early", "standard", "night"}
_TIMER_SET = {5, 10, 15, 30}

class NightPrefUpdate(BaseModel):
    preset: Optional[str] = None
    effect_enabled: Optional[bool] = None
    keep_enabled: Optional[bool] = None
    lamp_timer_min: Optional[int] = None
    whisper_enabled: Optional[bool] = None

def _bj_today() -> str:
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

@router.get("/prefs")
def get_prefs(uid: str = Depends(require_user)):
    prefs = _pdao().get_pref(uid) or {"user_id": uid, "preset": "standard",
                                      "effect_enabled": 1, "keep_enabled": 1,
                                      "lamp_timer_min": 15, "whisper_enabled": 1}
    return {"prefs": prefs}

@router.put("/prefs")
def put_prefs(body: NightPrefUpdate, uid: str = Depends(require_user)):
    patch = {}
    if body.preset is not None:
        if body.preset not in _PRESET_SET:
            raise HTTPException(status_code=400, detail=f"非法时段档位: {body.preset}")
        patch["preset"] = body.preset
    if body.lamp_timer_min is not None:
        if body.lamp_timer_min not in _TIMER_SET:
            raise HTTPException(status_code=400, detail="定时关闭仅支持 5/10/15/30 分钟")
        patch["lamp_timer_min"] = body.lamp_timer_min
    for k in ("effect_enabled", "keep_enabled", "whisper_enabled"):
        v = getattr(body, k)
        if v is not None:
            patch[k] = 1 if v else 0
    _pdao().upsert_pref(uid, patch)
    return {"prefs": _pdao().get_pref(uid)}

@router.get("/status")
def night_status(uid: str = Depends(require_user)):
    from src.engines.night_mode import is_night_mode, night_window
    prefs = _pdao().get_pref(uid) or {}
    preset = prefs.get("preset", "standard")
    start, end = night_window(preset)
    return {"night_mode": is_night_mode(preset=preset),
            "preset": preset,
            "window": f"{start:02d}:00-{end:02d}:00"}
```

在 `src/main.py` 挂载(与现有 router 并列):
```python
from src.api.night import router as night_router
app.include_router(night_router)          # /api/night/prefs|status
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python3 scripts/test_night_api.py`
Expected: `ALL PASS (9)`

- [ ] **Step 5: Commit**

```bash
git add src/storage/night_dao.py src/api/night.py src/main.py scripts/test_night_api.py
git commit -m "feat(night): 深夜偏好 DAO+偏好/状态 API(时段档位/动效/挽留/灯语定时/私语)"
```

---

### Task 3: 枕边灯语管线

**Files:**
- Create: `src/engines/night_soliloquy.py`
- Test: `scripts/test_night_soliloquy.py`

**Interfaces:**
- Consumes: `SessionDAO.get_summary/get_history`(L2 摘要与当日对话判定)、`LuckyCalendar._day_stem_branch`(干支)、`generate_daily_quote`(晨笺金句,复用)、`get_cache()`(date:generic_daily 兜底)、env `DEEPSEEK_API_KEY`、TTS 8768
- Produces: `extract_anchors(summary_text) -> list`(分类级锚点,≤3,绝不取原文);`has_today_chat(session_dao, user_id, date_str) -> bool`(北京时间当日对话判定);`build_soliloquy(user_id, date_str, session_dao, llm_fn=None, whisper=True) -> dict`(返回 {date, text, anchors, fallback};LLM 失败/红线不过重试 1 次后兜底);`_fallback_soliloquy(date_str, tomorrow_content) -> str`(纯模板,无对话/无锚点/私语关时兜底,宁缺毋滥不编造记忆);`synth_lamp_audio(text) -> str`(TTS 8768 柔缓女声 rate=-10%;失败返回空串,文字版仍可用)

- [ ] **Step 1: 写失败测试**

创建 `scripts/test_night_soliloquy.py`:
```python
"""枕边灯语管线测试:锚点提取(分类级) + 独白生成 + 红线校验 + 兜底 + TTS(mock)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest.mock as mock
from src.engines.night_soliloquy import (
    build_soliloquy, extract_anchors, _validate_soliloquy,
    _fallback_soliloquy, synth_lamp_audio)

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

# 1. 锚点提取:分类级(不含原文词)
anchors = extract_anchors("用户今天聊了面试和加班,说最近睡不踏实")
check("锚点分类级", set(anchors) <= {"事业", "感情", "健康", "财运"} and "事业" in anchors)
check("空摘要无锚点", extract_anchors("") == [])

# 2. 兜底模板结构
tpl = _fallback_soliloquy("2026-08-12", {"day_ganzhi": "庚午", "suitable": ["早睡", "静心"],
                                          "unsuitable": ["熬夜"], "quote": "火气偏旺,宜静"})
check("兜底含灯还亮着", "灯还亮着" in tpl)
check("兜底含明日宜忌", "庚午" in tpl)
check("兜底落款", tpl.rstrip().endswith("灯下的人"))

# 3. 红线校验:长度/开场/落款
check("合格独白通过",
      _validate_soliloquy("灯还亮着。" + "今天的事,我记得。" * 15 + "晚安。灯下的人"))
check("超长拒绝", not _validate_soliloquy("灯还亮着。" + "好" * 300 + "晚安。灯下的人"))
check("无落款拒绝", not _validate_soliloquy("灯还亮着。" + "今天的事。" * 10))

class FakeSDAO:
    """最小会话 DAO 桩:可配摘要与当日历史。"""
    def __init__(self, summary="", today=True):
        self.summary = summary; self.today = today
    def get_summary(self, user_id):
        return {"summary": self.summary} if self.summary else None
    def get_history(self, user_id, limit=50):
        return [{"created_at": "2026-08-12 10:00:00"}] if self.today else []

def fake_llm(api_key, messages, **kw):
    return ("灯还亮着。\n今天你聊了工作的事,我记得。\n"
            "我知道你今天很累,辛苦了,先把灯点着。\n"
            "不用急着把话说完,我就在这里陪着你。\n"
            "明日庚午日,火气偏旺,宜早睡,心火自平。\n"
            "你只管睡。天大的事,等太阳升起来再说。\n晚安。灯下的人")

# 4. 有摘要+锚点 → LLM 独白成功(结构完整)
with mock.patch("src.engines.night_soliloquy._default_llm", side_effect=fake_llm):
    r = build_soliloquy("u1", "2026-08-12", FakeSDAO("用户聊了面试"), whisper=True)
    check("独白生成成功", r["fallback"] is False and r["text"].startswith("灯还亮着")
          and len(r["text"]) >= 100 and r["text"].rstrip().endswith("灯下的人"))
    check("独白含锚点", "事业" in r["anchors"])

    # 5. 今日无对话 → 兜底(宁缺毋滥,不编造记忆)
    r2 = build_soliloquy("u2", "2026-08-12", FakeSDAO("", today=False), whisper=True)
    check("无对话兜底", r2["fallback"] is True and "灯还亮着" in r2["text"])

    # 6. 私语关闭 → 兜底(纯金句+宜忌模板)
    r3 = build_soliloquy("u3", "2026-08-12", FakeSDAO("用户聊了面试"), whisper=False)
    check("私语关→兜底", r3["fallback"] is True)

# 7. LLM 异常 → 兜底
with mock.patch("src.engines.night_soliloquy._default_llm", side_effect=Exception("boom")):
    r4 = build_soliloquy("u4", "2026-08-12", FakeSDAO("用户聊了面试"), whisper=True)
    check("LLM 失败兜底", r4["fallback"] is True)

# 8. TTS:成功拼完整 URL;失败返回空串
fake_ok = mock.Mock(); fake_ok.status_code = 200
fake_ok.json = lambda: {"audio_url": "/audio/abc.mp3", "duration_ms": 70000}
fake_bad = mock.Mock(); fake_bad.status_code = 502
with mock.patch("httpx.post", return_value=fake_ok):
    check("TTS 成功", synth_lamp_audio("灯还亮着") == "http://127.0.0.1:8768/audio/abc.mp3")
with mock.patch("httpx.post", return_value=fake_bad):
    check("TTS 失败空串", synth_lamp_audio("灯还亮着") == "")

print(f"\nALL PASS ({ok})")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python3 scripts/test_night_soliloquy.py`
Expected: `ModuleNotFoundError: No module named 'src.engines.night_soliloquy'`

- [ ] **Step 3: 实现管线**

创建 `src/engines/night_soliloquy.py`:
```python
"""枕边灯语管线(方案·灯下漫谈):当天 L2 摘要 → 分类级温柔锚点 →
150-200 字深夜独白(LLM) → 红线校验 → 兜底模板 → TTS(8768)。

红线:
- LLM 提示词只给"分类级锚点"(事业/感情/健康/财运),绝不给原文 → 设计上杜绝引用原文;
- 输出校验:100-260 字、以"灯还亮着"开场、以"晚安。灯下的人"落款、无 URL;
- 当日无对话 / 私语开关关闭 / LLM 两次失败 → 纯"明日宜忌+古籍金句+通用晚安"兜底,
  宁缺毋滥,绝不编造记忆。
"""
import logging
import os

import httpx

logger = logging.getLogger(__name__)

CATEGORY_KEYWORDS = {
    "事业": ["事业", "工作", "面试", "加班", "升职", "跳槽", "老板", "同事", "辞职", "上班"],
    "感情": ["感情", "恋爱", "分手", "思念", "结婚", "对象", "喜欢", "想念", "心碎", "前任", "想起"],
    "健康": ["健康", "失眠", "睡不着", "累", "疲惫", "身体", "医院", "生病", "熬夜"],
    "财运": ["财运", "钱", "收入", "开销", "理财", "还债", "工资", "欠款"],
}

_NIGHT_PROMPT = """你是易理明灯,深夜陪伴人格(豆包式五步安慰法降速版)。
请为今日写一段 150-200 字的「枕边灯语」深夜独白。口吻:短句、无说教、不追问、不解决,只陪。

结构(严格按序):
1. 开场一句「灯还亮着」
2. 一句今天的事——只能引用下面给出的「分类级锚点」(如:事业/感情/健康),
   绝对不许提具体人名、具体事件、原文或细节
3. 一句抚慰
4. 一句明日宜忌(给出明日干支与宜忌)
5. 落款「晚安。灯下的人」

规则:
- 全文 150-200 字,短句为主,句子之间用换行
- 锚点只作为"记得你今天聊过这一类事"的依据,不引用用户任何原话
- 不提问、不建议、不说教
直接输出独白正文,不要任何额外说明。"""


def extract_anchors(summary_text: str) -> list:
    """从 L2 摘要提取 1-3 个分类级温柔锚点(绝不取原文)。"""
    if not summary_text:
        return []
    out = []
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(k in summary_text for k in kws):
            out.append(cat)
    return out[:3]


def has_today_chat(session_dao, user_id: str, date_str: str) -> bool:
    """北京时间当日是否有过对话(sessions.created_at 为 UTC,需 +8 换算)。"""
    try:
        history = session_dao.get_history(user_id, limit=50)
        from datetime import datetime, timedelta
        for h in history:
            ca = str(h.get("created_at", ""))[:19]  # YYYY-MM-DD HH:MM:SS(UTC)
            if not ca:
                continue
            try:
                bj = datetime.fromisoformat(ca.replace(" ", "T")) + timedelta(hours=8)
                if bj.strftime("%Y-%m-%d") == date_str:
                    return True
            except ValueError:
                if ca[:10] == date_str:
                    return True
        return False
    except Exception as e:
        logger.warning("灯语当日对话判定失败: %s", e)
        return False


def _tomorrow_content(date_str: str) -> dict:
    """明日干支/宜忌/古籍金句(复用晨笺干支+金句管线;generic 缓存兜底)。"""
    from datetime import date, timedelta
    from src.engines.calendar import LuckyCalendar
    from src.engines.jian_quote import generate_daily_quote
    t = (date.fromisoformat(date_str) + timedelta(days=1)).isoformat()
    cal = LuckyCalendar("")
    stem, branch = cal._day_stem_branch(t)
    quote = generate_daily_quote(t, f"{stem}{branch}") or {}
    generic = {}
    try:
        from src.utils.cache import get_cache
        generic = get_cache().get(f"date:generic_daily:{t}") or {}
    except Exception:
        generic = {}
    return {
        "date": t, "day_ganzhi": f"{stem}{branch}",
        "suitable": generic.get("suitable") or ["早睡", "静心", "安神"],
        "unsuitable": generic.get("unsuitable") or ["熬夜", "焦躁"],
        "quote": quote.get("quote", ""), "book": quote.get("book", ""),
    }


def _fallback_soliloquy(date_str: str, tc: dict) -> str:
    """纯模板兜底:明日宜忌 + 古籍金句 + 通用晚安(无记忆/私语关时)。"""
    quote = (tc.get("quote") or "").strip()
    q_line = f"明日{tc['day_ganzhi']}日,宜{'、'.join((tc.get('suitable') or [])[:3])}。"
    if quote:
        q_line += f"「{quote}」"
    return (f"灯还亮着。\n今夜没有太多话要说,也好。{q_line}\n"
            "你只管睡。天大的事,等太阳升起来再说。\n晚安。灯下的人")


def _validate_soliloquy(text: str) -> bool:
    """红线校验:长度 100-260 / 灯还亮着开场 / 灯下的人落款 / 无 URL。"""
    t = (text or "").strip()
    if not (100 <= len(t) <= 260):
        return False
    if "灯还亮着" not in t:
        return False
    if not t.rstrip().endswith("灯下的人"):
        return False
    if any(b in t for b in ("http", "www.", "{{")):
        return False
    return True


def _default_llm(api_key: str, messages: list, **kw) -> str:
    from src.llm.client import deepseek_anthropic_completion
    return deepseek_anthropic_completion(api_key, messages, **kw)


def build_soliloquy(user_id: str, date_str: str, session_dao, llm_fn=None,
                    whisper: bool = True) -> dict:
    """生成当日灯语。返回 {date, text, anchors, fallback}。"""
    tc = _tomorrow_content(date_str)
    anchors = []
    if whisper and has_today_chat(session_dao, user_id, date_str):
        try:
            summary = session_dao.get_summary(user_id) or {}
            anchors = extract_anchors(summary.get("summary", "") or "")
        except Exception as e:
            logger.warning("灯语摘要读取失败 user=%s: %s", user_id, e)
    if not anchors:
        return {"date": date_str, "text": _fallback_soliloquy(date_str, tc),
                "anchors": [], "fallback": True}
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        return {"date": date_str, "text": _fallback_soliloquy(date_str, tc),
                "anchors": anchors, "fallback": True}
    fn = llm_fn or _default_llm
    prompt = (f"{_NIGHT_PROMPT}\n\n【明日】{tc['day_ganzhi']}日,"
              f"宜{'、'.join((tc.get('suitable') or [])[:3])},"
              f"忌{'、'.join((tc.get('unsuitable') or [])[:3])}。\n"
              f"【今日分类级锚点】{'/'.join(anchors)}")
    for attempt in (1, 2):
        try:
            text = (fn(api_key, [{"role": "user", "content": prompt}],
                       model="deepseek-v4-flash", max_tokens=600, temperature=0.8,
                       timeout=30.0) or "").strip()
        except Exception as e:
            logger.warning("灯语生成失败(尝试 %s) user=%s: %s", attempt, user_id, e)
            continue
        if _validate_soliloquy(text):
            return {"date": date_str, "text": text, "anchors": anchors, "fallback": False}
        logger.warning("灯语校验未通过(尝试 %s) user=%s", attempt, user_id)
    return {"date": date_str, "text": _fallback_soliloquy(date_str, tc),
            "anchors": anchors, "fallback": True}


def synth_lamp_audio(text: str) -> str:
    """TTS(8768):柔缓女声、语速 -10%。失败返回空串(文字版仍可用)。"""
    try:
        r = httpx.post("http://127.0.0.1:8768/tts",
                       json={"text": (text or "")[:450],
                             "voice": "zh-CN-XiaoyiNeural", "rate": "-10%"},
                       timeout=90)
        if r.status_code != 200:
            logger.warning("灯语 TTS 失败: %s", r.status_code)
            return ""
        url = r.json().get("audio_url", "")
        if url.startswith("/"):
            url = f"http://127.0.0.1:8768{url}"
        return url
    except Exception as e:
        logger.warning("灯语 TTS 异常: %s", e)
        return ""
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python3 scripts/test_night_soliloquy.py`
Expected: `ALL PASS (15)`

- [ ] **Step 5: Commit**

```bash
git add src/engines/night_soliloquy.py scripts/test_night_soliloquy.py
git commit -m "feat(night): 枕边灯语管线(L2摘要→分类级锚点→150-200字独白→红线校验→兜底→TTS)"
```

---

### Task 4: 灯语库 DAO + 灯语 API + 22:30 预生成 worker

**Files:**
- Create: `src/storage/lamp_dao.py`
- Modify: `src/api/night.py`(追加 lamp/today、lamp/favorite、lamp/history)
- Modify: `src/main.py`(灯语预生成 worker + lifespan 启动/清理)
- Test: `scripts/test_lamp_api.py`

**Interfaces:**
- Consumes: Task 3 `build_soliloquy/synth_lamp_audio`、Task 2 `NightPrefDAO`(whisper_enabled)、`MemberDAO`(会员判定:语音版权益)、`JianPrefDAO`(night_enabled 订阅用户预生成)、`SessionDAO`
- Produces: `LampDAO(conn)`:表 `night_lamp`(user_id+date 唯一);`get_lamp(user_id, date)`;`upsert_lamp(user_id, date, text, audio_url="")`;`set_favorite(user_id, date, fav)`;`list_history(user_id, limit)`
- API:`GET /api/night/lamp/today -> {lamp: {date, text, favorited, audio_url?}}`(生成后入库缓存,当日只生成一次;语音版仅会员);`POST /api/night/lamp/favorite` body `{date}` -> `{favorited}`(404 不存在);`GET /api/night/lamp/history -> {lamps, member}`(免费仅近 3 夜且剥离 audio_url,会员全部)
- 预生成:`_night_lamp_precompute()`(每小时检查,22:30-23:30 窗口执行);`_prewarm_night_lamps(date_str, limit=50) -> {total, ok, skipped, failed}`(为 night_enabled=1 且 bound 的订阅用户预生成,会员才合成语音防 TTS 成本滥用)

- [ ] **Step 1: 写失败测试**

创建 `scripts/test_lamp_api.py`:
```python
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

client = TestClient(app)
TOKEN = "dev-token-test-user-night"
h = {"Authorization": f"Bearer {TOKEN}"}
_ldao = night_mod._lamp_dao

# 1. 未登录 401
check("未登录 401", client.get("/api/night/lamp/today").status_code == 401)

# 2. 今日灯语(免费:生成+入库,无语音)
with mock.patch("src.engines.night_soliloquy.build_soliloquy",
                return_value={"date": "2026-08-12", "text": "灯还亮着。晚安。灯下的人",
                              "anchors": ["事业"], "fallback": False}), \
     mock.patch("src.storage.session_dao.SessionDAO", return_value=object()):
    r = client.get("/api/night/lamp/today", headers=h)
    d = r.json()["lamp"]
    check("今日灯语生成", r.status_code == 200 and d["text"].startswith("灯还亮着"))
    check("免费无语音", "audio_url" not in d)
check("灯语落库", _ldao.get_lamp(TOKEN, "2026-08-12") is not None)

# 3. 收藏/取消收藏/404
r = client.post("/api/night/lamp/favorite", headers=h, json={"date": "2026-08-12"})
check("收藏", r.status_code == 200 and r.json()["favorited"] is True)
check("库内 favorite=1", _ldao.get_lamp(TOKEN, "2026-08-12")["favorite"] == 1)
check("取消收藏", client.post("/api/night/lamp/favorite", headers=h,
                           json={"date": "2026-08-12"}).json()["favorited"] is False)
check("不存在 404", client.post("/api/night/lamp/favorite", headers=h,
                             json={"date": "2020-01-01"}).status_code == 404)

# 4. 历史:免费仅近 3 夜
for day, txt in (("2026-08-09", "灯语A"), ("2026-08-10", "灯语B"),
                 ("2026-08-11", "灯语C"), ("2026-08-12", "灯语D")):
    _ldao.upsert_lamp(TOKEN, day, txt)
r = client.get("/api/night/lamp/history", headers=h)
lamps = r.json()["lamps"]
check("免费仅近3夜", len(lamps) == 3 and lamps[0]["date"] == "2026-08-12")
check("免费无语音字段", all("audio_url" not in l for l in lamps))

# 5. 会员:历史全部 + 今日含语音
night_mod._member_dao = FakeMember("pro")
_ldao.upsert_lamp(TOKEN, "2026-08-12", "灯语D", "http://127.0.0.1:8768/audio/d.mp3")
r = client.get("/api/night/lamp/history", headers=h)
check("会员全量", len(r.json()["lamps"]) == 4)
check("会员今日灯语带语音",
      "audio_url" in client.get("/api/night/lamp/today", headers=h).json()["lamp"])

# 6. 预生成 worker:订阅用户生成(ok),已入库跳过(skipped)
c = dao_mod.get_conn()
c.execute("INSERT INTO jian_prefs (user_id, jian_enabled, night_enabled, bound_status, mp_openid) "
          "VALUES (?, 0, 1, 'bound', 'oX')", (TOKEN,))
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

print(f"\nALL PASS ({ok})")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python3 scripts/test_lamp_api.py`
Expected: `ModuleNotFoundError: No module named 'src.storage.lamp_dao'`

- [ ] **Step 3: 实现 DAO / API / worker**

创建 `src/storage/lamp_dao.py`:
```python
"""灯语库存储(方案·灯下漫谈)。表 night_lamp: 每用户每夜一条灯语(文字+语音),可收藏。"""
import time

class LampDAO:
    def __init__(self, conn):
        self.conn = conn
        self.conn.execute("""CREATE TABLE IF NOT EXISTS night_lamp (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            date TEXT NOT NULL,
            text TEXT NOT NULL,
            audio_url TEXT DEFAULT '',
            favorite INTEGER DEFAULT 0,
            created_at REAL,
            UNIQUE(user_id, date)
        )""")
        self.conn.commit()

    def get_lamp(self, user_id: str, date: str):
        row = self.conn.execute(
            "SELECT * FROM night_lamp WHERE user_id=? AND date=?",
            (user_id, date)).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.execute("SELECT * FROM night_lamp").description]
        return dict(zip(cols, row))

    def upsert_lamp(self, user_id: str, date: str, text: str, audio_url: str = ""):
        self.conn.execute(
            """INSERT INTO night_lamp (user_id, date, text, audio_url, created_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(user_id, date) DO UPDATE SET
               text=excluded.text, audio_url=excluded.audio_url""",
            (user_id, date, text, audio_url, time.time()))
        self.conn.commit()

    def set_favorite(self, user_id: str, date: str, fav: int):
        self.conn.execute(
            "UPDATE night_lamp SET favorite=? WHERE user_id=? AND date=?",
            (fav, user_id, date))
        self.conn.commit()

    def list_history(self, user_id: str, limit: int = 100) -> list:
        rows = self.conn.execute(
            "SELECT * FROM night_lamp WHERE user_id=? ORDER BY date DESC LIMIT ?",
            (user_id, limit)).fetchall()
        cols = [d[0] for d in self.conn.execute("SELECT * FROM night_lamp").description]
        return [dict(zip(cols, r)) for r in rows]
```

在 `src/api/night.py` 追加(顶部补 `from src.storage.lamp_dao import LampDAO` 与 `_lamp_dao` 全局、`is_member` 判定):
```python
_lamp_dao: Optional[LampDAO] = None
_member_dao: Optional[object] = None  # 会员判定(语音版权益),由 main lifespan 注入

def _ldao() -> LampDAO:
    global _lamp_dao
    if _lamp_dao is None:
        from src.storage.dao import get_conn
        _lamp_dao = LampDAO(get_conn())
    return _lamp_dao

def is_member(uid: str) -> bool:
    if _member_dao is None:
        return False
    try:
        m = _member_dao.get_membership(uid) or {}
        return (m.get("plan") or "free") != "free"
    except Exception:
        return False

@router.get("/lamp/today")
def lamp_today(uid: str = Depends(require_user)):
    """今日枕边灯语:生成后入库缓存,当日只生成一次;语音版仅会员(免费仅文字)。"""
    date_str = _bj_today()
    dao = _ldao()
    lamp = dao.get_lamp(uid, date_str)
    if not lamp:
        from src.storage.session_dao import SessionDAO
        from src.engines.night_soliloquy import build_soliloquy, synth_lamp_audio
        from src.config import load_settings
        sdao = SessionDAO(str(load_settings().db_path))
        prefs = _pdao().get_pref(uid) or {}
        result = build_soliloquy(uid, date_str, sdao,
                                 whisper=bool(prefs.get("whisper_enabled", 1)))
        audio = synth_lamp_audio(result["text"]) if is_member(uid) else ""
        dao.upsert_lamp(uid, date_str, result["text"], audio)
        lamp = dao.get_lamp(uid, date_str)
    resp = {"date": lamp["date"], "text": lamp["text"],
            "favorited": bool(lamp["favorite"])}
    if is_member(uid) and lamp.get("audio_url"):
        resp["audio_url"] = lamp["audio_url"]
    return {"lamp": resp}

class FavBody(BaseModel):
    date: str

@router.post("/lamp/favorite")
def lamp_favorite(body: FavBody, uid: str = Depends(require_user)):
    dao = _ldao()
    lamp = dao.get_lamp(uid, body.date)
    if not lamp:
        raise HTTPException(status_code=404, detail="该夜灯语不存在")
    fav = 0 if lamp["favorite"] else 1
    dao.set_favorite(uid, body.date, fav)
    return {"favorited": bool(fav)}

@router.get("/lamp/history")
def lamp_history(uid: str = Depends(require_user)):
    """灯语历史:免费仅近 3 夜且剥离语音(红线);会员全部。"""
    lamps = _ldao().list_history(uid, limit=100)
    member = is_member(uid)
    if not member:
        lamps = lamps[:3]
        for l in lamps:
            l.pop("audio_url", None)
    return {"lamps": lamps, "member": member}
```

在 `src/main.py` 增加 worker 与预生成函数(放在晨笺 worker 旁):
```python
async def _night_lamp_precompute():
    """Task 4: 每小时检查,22:30-23:30 窗口预生成当日灯语(订阅用户,限速)。"""
    while True:
        try:
            now = datetime.now(timezone(timedelta(hours=8)))
            hm = now.strftime("%H:%M")
            if "22:30" <= hm <= "23:30":
                _prewarm_night_lamps(now.strftime("%Y-%m-%d"))
        except Exception as e:
            logger.error("灯语预生成异常: %s", e)
        await asyncio.sleep(3600)

def _prewarm_night_lamps(date_str: str, limit: int = 50) -> dict:
    """为已开启晚安推送的订阅用户预生成灯语(会员才合成语音,防 TTS 成本滥用)。"""
    from src.storage.dao import get_conn
    from src.storage.jian_dao import JianPrefDAO
    from src.storage.night_dao import NightPrefDAO
    from src.storage.lamp_dao import LampDAO
    from src.storage.session_dao import SessionDAO
    from src.storage.member_dao import MemberDAO
    from src.engines.night_soliloquy import build_soliloquy, synth_lamp_audio
    conn = get_conn()
    rows = conn.execute(
        "SELECT user_id FROM jian_prefs WHERE night_enabled=1 AND bound_status='bound' LIMIT ?",
        (limit,)).fetchall()
    stats = {"total": len(rows), "ok": 0, "skipped": 0, "failed": 0}
    ldao, pdao = LampDAO(conn), NightPrefDAO(conn)
    sdao = SessionDAO(str(load_settings().db_path))
    mdao = MemberDAO(str(load_settings().db_path))
    for (uid,) in rows:
        try:
            if ldao.get_lamp(uid, date_str):
                stats["skipped"] += 1
                continue
            prefs = pdao.get_pref(uid) or {}
            result = build_soliloquy(uid, date_str, sdao,
                                     whisper=bool(prefs.get("whisper_enabled", 1)))
            audio = ""
            if (mdao.get_membership(uid) or {}).get("plan", "free") != "free":
                audio = synth_lamp_audio(result["text"])
            ldao.upsert_lamp(uid, date_str, result["text"], audio)
            stats["ok"] += 1
        except Exception as e:
            logger.warning("灯语预生成失败 uid=%s: %s", uid, e)
            stats["failed"] += 1
    logger.info("灯语预生成完成(%s): %s", date_str, stats)
    return stats
```

在 `src/main.py` lifespan 内启动(晨笺 worker 旁)并在 yield 后取消:
```python
    # Task 4: 灯语 22:30 预生成 worker
    _night_lamp_task = asyncio.create_task(_night_lamp_precompute())
    logger.info("灯语预生成 worker 已启动 (22:30-23:30 预生成当日灯语)")

    # Task 5: 倾诉临时会话 24h 硬清理 worker(见 Task 5)
    _night_cleanup_task = asyncio.create_task(_night_temp_cleanup())
```
(`_night_lamp_task`/`_night_cleanup_task` 加入模块级全局与 cleanup 分支的 cancel;`_night_temp_cleanup` 本体在 Task 5 实现,先建 worker 会挂——按任务顺序 Task 4 提交前,cleanup worker 在 Task 5 才加,故 Task 4 只加 `_night_lamp_task` 的启动与取消,`_night_temp_cleanup` 相关两行留到 Task 5 一并加。)

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python3 scripts/test_lamp_api.py`
Expected: `ALL PASS (14)`

- [ ] **Step 5: Commit**

```bash
git add src/storage/lamp_dao.py src/api/night.py src/main.py scripts/test_lamp_api.py
git commit -m "feat(night): 灯语库 DAO+灯语 API(今日/收藏/历史近3夜)+22:30 预生成 worker"
```

---

### Task 5: 倾诉临时通道 deepNight(不落 L2/L3 + temp 消息 24h 硬清理 + 深夜语气层)

**Files:**
- Create: `src/bot/night_persona.py`(深夜人格语气层常量)
- Modify: `src/main.py`(ChatRequest.deep_night、/api/chat 传参、`_night_temp_cleanup` worker)
- Modify: `src/api/chat_stream.py`(ChatStreamer 传 deep_night 给 handler)
- Modify: `src/bot/handler.py`(process 加 deep_night 参数;深夜时跳过 L2/L3/演化链/情绪与主题;add_message 传 temp;free_chat 注入深夜语气层;confidant 同)
- Modify: `src/storage/session_dao.py`(add_message 加 temp/temp_expire_at 列迁移 + cleanup_temp)
- Test: `scripts/test_night_temp.py`

**Interfaces:**
- Consumes: 现有 `handler.process`(message, user_id, stream_cb)、`SessionDAO.add_message`
- Produces: `handler.process(message, user_id, stream_cb=None, deep_night=False)`(向后兼容新关键字参数);`SessionDAO.add_message(..., temp=False)`(temp 消息带 24h 过期时间);`SessionDAO.cleanup_temp(now_iso="") -> int`(删除过期 temp 消息);`_night_temp_cleanup()`(每小时 worker);`NIGHT_TONE_HINT`(深夜语气层,注入 _free_chat extra_hint 与 _handle_confidant)

- [ ] **Step 1: 写失败测试**

创建 `scripts/test_night_temp.py`:
```python
"""倾诉临时通道测试:temp 会话落库+24h 清理 + handler deepNight 跳过记忆管线+语气层注入"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest.mock as mock
from datetime import datetime, timedelta
from src.storage.session_dao import SessionDAO

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

fd, path = tempfile.mkstemp(suffix='.db'); os.close(fd)
sdao = SessionDAO(path)

# 1. temp 消息落库 + 带过期时间
now = datetime.utcnow()
sdao.add_message("u1", "user", "今天加班到十一点,回来屋里黑着", temp=True)
sdao.add_message("u1", "assistant", "辛苦了。", temp=True)
sdao.add_message("u1", "user", "普通消息")
h = sdao.get_history("u1", limit=10)
check("temp 标记落库",
      sum(1 for m in h if m.get("temp") == 1) == 2
      and sum(1 for m in h if m.get("temp") == 0) == 1)
check("temp 带过期时间", all(m.get("temp_expire_at") for m in h if m.get("temp") == 1))

# 2. 24h 硬清理:未过期不清,过期删除
check("未过期不清理", sdao.cleanup_temp((now + timedelta(hours=10)).isoformat()) == 0)
check("24h 后清理 temp", sdao.cleanup_temp((now + timedelta(hours=25)).isoformat()) == 2)
check("temp 已清空", all(m.get("temp") != 1 for m in sdao.get_history("u1", 10)))

# 3. handler:deepNight 跳过 L2/L3/演化链,消息 temp 落库,语气层注入
import src.bot.handler as handler_mod
from src.bot.handler import MessageHandler
from src.bot.night_persona import NIGHT_TONE_HINT

class FakeLLM:
    api_key = ""
    model = "deepseek-v4-flash"
class FakeDAO:
    db_path = path

with mock.patch.object(handler_mod, "MemberDAO", return_value=None), \
     mock.patch.object(handler_mod, "PreferenceDAO", return_value=None), \
     mock.patch("src.bot.handler.UserMemory"):
    h = MessageHandler(None, None, None, None, None, None, None,
                       FakeLLM(), FakeDAO(), session_dao=sdao)
h._deep_night = {}

analysis = mock.Mock(intent=None, emotion_label=None, needs_soothe=False,
                     soothe_text="", is_sharing=False, facts={})
calls = {"persist": 0, "capture": 0, "evolution": 0}
def spy_persist(uid, msg, facts): calls["persist"] += 1
def spy_capture(uid, msg): calls["capture"] += 1
def spy_evo(uid, topic, reply): calls["evolution"] += 1

captured = {}
def spy_free_chat(msg, user_id, emotion_label=None, extra_hint="", stream_cb=None):
    captured["hint"] = extra_hint or ""
    return "深夜回复"

with mock.patch.object(h, "_analyze_message", return_value=analysis), \
     mock.patch.object(h, "_persist_facts_entries", side_effect=spy_persist), \
     mock.patch.object(h, "_capture_key_event", side_effect=spy_capture), \
     mock.patch.object(h, "_record_evolution", side_effect=spy_evo), \
     mock.patch.object(h, "_free_chat", side_effect=spy_free_chat), \
     mock.patch.object(h, "_run_tool_loop", return_value="深夜回复"), \
     mock.patch.object(h, "_get_welcome_back", return_value=""), \
     mock.patch.object(h, "_consume_quota"), \
     mock.patch("src.bot.handler.is_cacheable", return_value=False):
    h.process("今天加班到十一点,有点想哭", "u2", deep_night=True)
    check("deepNight 跳过 L2 事实", calls["persist"] == 0)
    check("deepNight 跳过事件捕捉", calls["capture"] == 0)
    check("deepNight 跳过演化链", calls["evolution"] == 0)
    check("deepNight 注入深夜语气层", "深夜陪伴模式" in captured["hint"])
    check("deepNight 消息 temp 落库",
          all(m["temp"] == 1 for m in sdao.get_history("u2", 10)))

    # 4. 白天:记忆管线照常,消息非 temp,无深夜语气层
    h.process("今天面试顺利吗", "u3", deep_night=False)
    check("白天记忆管线照常", calls["persist"] == 1 and calls["capture"] == 1)
    check("白天消息非 temp", all(m["temp"] == 0 for m in sdao.get_history("u3", 10)))
    check("白天无深夜语气层", "深夜陪伴模式" not in captured["hint"])
    check("语气层常量存在", len(NIGHT_TONE_HINT) > 50)

print(f"\nALL PASS ({ok})")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python3 scripts/test_night_temp.py`
Expected: `ModuleNotFoundError: No module named 'src.bot.night_persona'`

- [ ] **Step 3: 实现**

创建 `src/bot/night_persona.py`:
```python
"""深夜人格语气层(方案·灯下漫谈):与白天同一个豆包式人格内核,只降速放软——
"灯是同一个人,只是为你把声音放轻",不是换了个人。"""
NIGHT_TONE_HINT = (
    "【深夜陪伴模式】现在是深夜,用户来此求陪伴而非求解。请切换深夜口吻:"
    "语速放缓、句子变短(每句不超过 20 字)、少反问、不追问细节、不主动解决问题、"
    "多“我在/我听着/不急,慢慢说”。凌晨 0 点后:建议更少、陪伴句更多;"
    "若用户倾诉,先接住情绪再回应。"
    "若用户表达自伤/自杀念头:停止任何话术,直接建议联系心理援助热线 12356,"
    "并说“白天我会陪你联系专业人士”。(深夜红线,优先级最高)"
)
```

`src/storage/session_dao.py` 修改:
```python
    def __init__(self, db_path: str):
        self.db_path = db_path
        init_db(db_path)
        # Task 5 迁移: temp 倾诉消息标记 + 24h 过期时间(老库 ALTER 兼容)
        conn = self._connect()
        try:
            cols = [d[1] for d in conn.execute("PRAGMA table_info(sessions)")]
            if "temp" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN temp INTEGER DEFAULT 0")
            if "temp_expire_at" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN temp_expire_at TEXT DEFAULT ''")
            conn.commit()
        finally:
            conn.close()

    def add_message(self, user_id, role, content, intent=None, emotion=None,
                    tool_calls=None, retrieval_hit=None, model=None, safety_flag=None,
                    temp: bool = False):
        """保存一条聊天消息(content 加密落库)。

        temp: 倾诉临时消息(深夜默认模式)——带 24h 过期时间,
        由 cleanup_temp 硬清理兜底(方案§4:服务端 24h 硬清理)。
        """
        content_enc = _encrypt_text(content)
        temp_expire_at = ""
        if temp:
            from datetime import datetime, timedelta
            temp_expire_at = (datetime.utcnow() + timedelta(hours=24)).isoformat()
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO sessions
                   (user_id, role, content, intent, emotion, tool_calls,
                    retrieval_hit, model, safety_flag, temp, temp_expire_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, role, content_enc, intent, emotion, tool_calls,
                 retrieval_hit, model, safety_flag, 1 if temp else 0, temp_expire_at),
            )
            conn.commit()
        finally:
            conn.close()
        self._cleanup(user_id)

    def cleanup_temp(self, now_iso: str = "") -> int:
        """删除过期的临时倾诉消息(24h 硬清理兜底),返回删除条数。"""
        from datetime import datetime
        now_iso = now_iso or datetime.utcnow().isoformat()
        conn = self._connect()
        try:
            cur = conn.execute(
                "DELETE FROM sessions WHERE temp=1 AND temp_expire_at != '' AND temp_expire_at < ?",
                (now_iso,))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()
```
`get_history` 的 SELECT 与字段映射追加 temp/temp_expire_at:
```python
                """SELECT id, user_id, role, content, intent, emotion,
                          tool_calls, retrieval_hit, model, safety_flag,
                          temp, temp_expire_at, created_at
                   FROM sessions ..."""
```
映射尾部改为:`"safety_flag": r[9], "temp": r[10], "temp_expire_at": r[11], "created_at": r[12]`

`src/main.py` 修改:
```python
class ChatRequest(BaseModel):
    ...
    deep_night: bool = False  # Task 5: 深夜倾诉模式(默认临时不记录+深夜语气层)

# /api/chat 内:
            reply = await loop.run_in_executor(
                None, lambda: handler.process(req.message, req.user_id, deep_night=req.deep_night))

async def _night_temp_cleanup():
    """Task 5: 每小时清理过期的临时倾诉消息(24h 硬清理兜底)。"""
    while True:
        try:
            from src.storage.session_dao import SessionDAO
            sdao = SessionDAO(str(load_settings().db_path))
            removed = sdao.cleanup_temp()
            if removed:
                logger.info("倾诉临时消息清理: %s 条", removed)
        except Exception as e:
            logger.warning("临时消息清理异常: %s", e)
        await asyncio.sleep(3600)
```
(lifespan 中与 Task 4 的 `_night_lamp_task` 一起启动 `_night_cleanup_task = asyncio.create_task(_night_temp_cleanup())`,cleanup 分支取消两者;模块级补 `_night_lamp_task = None` / `_night_cleanup_task = None`。)

`src/api/chat_stream.py` 修改(events 的 `_run` 内):
```python
                else:
                    reply = self.handler.process(req.message, user_id, stream_cb=stream_cb,
                                                 deep_night=bool(getattr(req, "deep_night", False)))
```

`src/bot/handler.py` 修改(方案 §四 倾诉记忆管线):
```python
    # __init__ 内,在 self._analysis_facts 旁:
        self._deep_night: dict = {}   # Task 5: user_id -> deepNight(倾诉临时模式)

    def process(self, message: str, user_id: str,
                stream_cb: Optional[Callable] = None, deep_night: bool = False) -> str:
        msg = message.strip()
        self._deep_night[user_id] = bool(deep_night)
        deep = self._deep_night.get(user_id, False)
        self._pop_tool_log(user_id)
        self._citations.pop(user_id, None)
        ...
```
process 内的跳过与传参(全部精确替换点):
1. 额度警告 add_message(约 1787 行)与"会员"升级 add_message(约 1815 行):末尾加 `, temp=deep`
2. `self._persist_facts_entries(user_id, msg, analysis.facts or {})` → 包 `if not deep:`
3. `if self.memory_system and analysis.emotion_label:` → 加 `and not deep:`(mood 不入记忆)
4. topic 计数与提示块 `if topic and self.memory_system:`(record_topic 与 topic_hint 两处) → 加 `and not deep:`;`if topic and self.memory_system:` 的 get_topic_count 分支同步
5. `self._capture_key_event(user_id, msg)` → 包 `if not deep:`
6. 用户消息 add_message:末尾加 `, temp=deep`
7. free_chat 调用处(约 1935 行):
```python
            hints = [h for h in (topic_hint, analysis_hint) if h]
            if deep:
                from src.bot.night_persona import NIGHT_TONE_HINT
                hints.insert(0, NIGHT_TONE_HINT)
            reply = self._free_chat(msg, user_id, emotion_label=analysis.emotion_label,
                                    extra_hint="\n".join(hints), stream_cb=stream_cb)
```
8. 两条 `self._record_evolution(user_id, topic, reply)`(约 1962/2037 行) → 包 `if not deep:`
9. 两条 assistant add_message(约 1949/2023 行):末尾加 `, temp=deep`
10. `_free_chat` 内 `summary = self._maybe_compact(user_id)`(约 4247 行) →
    `if self.session_dao and not self._deep_night.get(user_id, False):`(深夜不压缩 → 内容不入 L2 摘要)
11. `_handle_confidant` 开头(约 4032 行,在 `from src.llm.prompts import CONFIDANT_PROMPT` 之后):
```python
        if self._deep_night.get(user_id, False):
            from src.bot.night_persona import NIGHT_TONE_HINT
            msg = msg + "\n\n" + NIGHT_TONE_HINT
```
    其内部 assistant add_message(`if self.session_dao:` 处)末尾加 `, temp=self._deep_night.get(user_id, False)`
12. 实现时 grep 校验:process 调用链内(含 _handle_xuetang/_handle_advisor 内部)所有 `add_message(` 调用点均按上述方式补 temp 参数,确保深夜会话消息全部带临时标记(红线:一条不漏)。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python3 scripts/test_night_temp.py`
Expected: `ALL PASS (14)`

- [ ] **Step 5: Commit**

```bash
git add src/bot/night_persona.py src/bot/handler.py src/storage/session_dao.py src/main.py src/api/chat_stream.py scripts/test_night_temp.py
git commit -m "feat(night): 倾诉临时通道 deepNight(不落L2/L3+temp消息24h硬清理+深夜语气层)"
```

---

### Task 6: "要我记得吗"单轮落库(仅分类级,当晚一次)

**Files:**
- Create: `src/engines/night_classify.py`
- Modify: `src/api/night.py`(追加 POST /api/night/remember)
- Test: `scripts/test_night_remember.py`

**Interfaces:**
- Consumes: Task 2 `NightPrefDAO.get_remember/set_remember`(当晚一次)、`UserMemory.add_entry`(L3,source="night_remember")
- Produces: `classify_night_topic(message) -> str`(事业/感情/健康/财运/其他,关键词规则纯逻辑);`POST /api/night/remember` body `{message: str}` -> `{remembered: bool, category: str, reason?: "tonight_done"}`(仅分类级落库:内容为"深夜倾诉中提及「X」相关心事",绝不存原文/人名/事件;当晚已记则返回 tonight_done)

- [ ] **Step 1: 写失败测试**

创建 `scripts/test_night_remember.py`:
```python
"""'要我记得吗'单轮落库测试:分类级脱敏 + 当晚一次 + API"""
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
TOKEN = "dev-token-test-user-night"
h = {"Authorization": f"Bearer {TOKEN}"}

r = client.post("/api/night/remember", headers=h,
                json={"message": "你记得我下周要面试吗"})
check("单轮落库", r.status_code == 200 and r.json()["remembered"] is True
      and r.json()["category"] == "事业")

from src.memory.user_memory import UserMemory
entries = UserMemory(base_dir=memdir).list_entries(TOKEN)
check("分类级入库", len(entries) == 1 and entries[0]["type"] == "topic"
      and entries[0]["subject"] == "事业")
check("原文不落库", all("面试" not in e["content"] for e in entries))
check("当晚仅一次", client.post("/api/night/remember", headers=h,
                             json={"message": "面试"}).json()["remembered"] is False)

print(f"\nALL PASS ({ok})")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python3 scripts/test_night_remember.py`
Expected: `ModuleNotFoundError: No module named 'src.engines.night_classify'`

- [ ] **Step 3: 实现**

创建 `src/engines/night_classify.py`:
```python
"""深夜倾诉分类器(方案·灯下漫谈):消息 → 分类级主题。
红线:只输出分类标签,绝不落库原文/人名/事件(与灯语锚点同规则)。"""
CATEGORY_KEYWORDS = {
    "事业": ["事业", "工作", "面试", "加班", "升职", "跳槽", "老板", "同事", "辞职", "上班"],
    "感情": ["感情", "恋爱", "分手", "思念", "结婚", "对象", "喜欢", "想念", "心碎", "前任", "想起"],
    "健康": ["健康", "失眠", "睡不着", "累", "疲惫", "身体", "医院", "生病", "熬夜"],
    "财运": ["财运", "钱", "收入", "开销", "理财", "还债", "工资", "欠款"],
}
DEFAULT = "其他"


def classify_night_topic(message: str) -> str:
    """消息 → 分类级主题(关键词规则,命中第一个分类即返回)。"""
    if not message:
        return DEFAULT
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(k in message for k in kws):
            return cat
    return DEFAULT
```

在 `src/api/night.py` 追加:
```python
class RememberBody(BaseModel):
    message: str

@router.post("/remember")
def night_remember(body: RememberBody, uid: str = Depends(require_user)):
    """"要我记得吗"单轮落库:仅分类级(不存人名/事件/原话),一晚只记一次。"""
    from src.engines.night_classify import classify_night_topic
    date_str = _bj_today()
    if _pdao().get_remember(uid, date_str):
        return {"remembered": False, "reason": "tonight_done", "category": ""}
    category = classify_night_topic(body.message or "")
    if category != "其他":
        from src.memory.user_memory import UserMemory
        UserMemory().add_entry(uid, "topic",
                               f"深夜倾诉中提及「{category}」相关心事(分类级,不记细节)",
                               subject=category, ttl_days=1, source="night_remember")
        _pdao().set_remember(uid, date_str, category)
    return {"remembered": True, "category": category}
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python3 scripts/test_night_remember.py`
Expected: `ALL PASS (9)`

- [ ] **Step 5: Commit**

```bash
git add src/engines/night_classify.py src/api/night.py scripts/test_night_remember.py
git commit -m "feat(night): 要我记得吗单轮落库(分类级脱敏,仅当晚一次)"
```

---

### Task 7: 守夜人成就(7 夜,本地存储)+ 灯下印记页

**Files:**
- Create: `miniprogram/utils/nightWatch.js`(simple/fusion 同)
- Create: `miniprogram/pages/night_mark/night_mark.{js,json,wxml,wxss}`(三版)
- Modify: `miniprogram/app.json`(三版注册页面)
- Modify: `miniprogram/pages/me/me.{js,wxml}`(三版:"灯下印记"入口行)

**Interfaces:**
- Consumes: 无后端(本地存储 wx.setStorageSync,键 `ylm_night_watch`)
- Produces: `nightWatch.touch(ts)`(深夜对话每发一条消息调用:登记当夜 firstTs/lastTs/msgs);`isActiveNight(entry) -> bool`(≥1 条消息且 ≥1 分钟 = 计 1 夜);`computeStreak(log, today) -> int`(从当天往回数连续达标夜数,中断重计;今夜未达标不计);`getState() -> {streak, display, target:7, nightsToday, achieved, longLit}`(display = 第 X/7 夜;30 夜升"长明灯");`recentNights(n)`(近 n 夜达标/进行中/未达标,印记页网格)
- 灯下印记页:篆刻印章(守夜人/长明灯两态)+ 进度"第 X/7 夜" + 近 7 夜格 + 分享截图卡(墨韵画法,复用 today.js onShot 的 node canvas 模式)

**实现要点**:
- `nightWatch.js` 完整代码:
```javascript
/* 守夜人成就(方案·灯下漫谈)— 本地存储:连续 7 夜点亮「守夜人」印章,30 夜升「长明灯」。
   计夜规则:21:00-04:00 有 ≥1 次 ≥1 分钟对话计 1 夜;中断重计。
   调用点:聊天页深夜模式每发送一条消息 → nightWatch.touch(Date.now())。 */
const KEY = 'ylm_night_watch';
const TARGET = 7;
const LONG_TARGET = 30;

/* 北京时间日期串(YYYY-MM-DD):本地时钟 +8h 后取 UTC 日期 */
function bjDateStr(ts) {
  return new Date((ts || Date.now()) + 8 * 3600e3).toISOString().slice(0, 10);
}

function _load() {
  try { return wx.getStorageSync(KEY) || { log: [] }; } catch (e) { return { log: [] }; }
}
function _save(state) {
  try { wx.setStorageSync(KEY, state); } catch (e) { /* ignore */ }
}

/* 深夜对话发送消息时调用:登记当夜(首条/最后条时间戳/条数) */
function touch(ts) {
  const t = ts || Date.now();
  const state = _load();
  const date = bjDateStr(t);
  const log = (state.log || []).slice();
  let entry = log.find((e) => e.date === date);
  if (!entry) { entry = { date, firstTs: t, lastTs: t, msgs: 0 }; log.push(entry); }
  entry.lastTs = Math.max(entry.lastTs, t);
  entry.msgs += 1;
  log.sort((a, b) => (a.date < b.date ? -1 : 1));
  while (log.length > 90) log.shift();   // 防膨胀,最多 90 夜
  _save({ log });
  return entry;
}

/* 当夜是否计夜:≥1 条消息且持续 ≥1 分钟 */
function isActiveNight(entry) {
  return !!entry && entry.msgs >= 1 && entry.lastTs - entry.firstTs >= 60000;
}

/* 连续达标夜数:从当天往回数;今夜未达标不计(从昨夜继续),中断即停 */
function computeStreak(log, today) {
  const byDate = {};
  (log || []).forEach((e) => { byDate[e.date] = e; });
  const t = today || bjDateStr(Date.now());
  let streak = 0;
  let d = new Date(t + 'T00:00:00Z');
  for (let i = 0; i < 90; i++) {
    const key = d.toISOString().slice(0, 10);
    const e = byDate[key];
    if (i === 0 && !(e && isActiveNight(e))) {
      d = new Date(d.getTime() - 86400e3);   // 今夜未完成:跳过,从昨夜继续
      continue;
    }
    if (!(e && isActiveNight(e))) break;     // 中断重计
    streak += 1;
    d = new Date(d.getTime() - 86400e3);
  }
  return streak;
}

/* 成就总览:display = 第 X 夜(今夜进行中 +1);achieved = 点亮守夜人;longLit = 长明灯 */
function getState() {
  const log = _load().log || [];
  const tonight = log.find((e) => e.date === bjDateStr(Date.now()));
  const nightsToday = isActiveNight(tonight);
  const streak = computeStreak(log);
  const display = streak + (nightsToday ? 0 : 1);
  return {
    streak,
    display,
    target: TARGET,
    longTarget: LONG_TARGET,
    nightsToday,
    achieved: display >= TARGET,
    longLit: display >= LONG_TARGET,
  };
}

/* 近 n 夜网格:每夜 {date, done, pending} */
function recentNights(n) {
  const log = _load().log || [];
  const byDate = {};
  log.forEach((e) => { byDate[e.date] = e; });
  const out = [];
  let d = new Date(bjDateStr(Date.now()) + 'T00:00:00Z');
  for (let i = 0; i < n; i++) {
    const key = d.toISOString().slice(0, 10);
    const e = byDate[key];
    out.push({
      date: key.slice(5),
      done: !!(e && isActiveNight(e)),
      pending: !!e && !isActiveNight(e),
    });
    d = new Date(d.getTime() - 86400e3);
  }
  return out;
}

module.exports = { touch, isActiveNight, computeStreak, getState, recentNights, bjDateStr, TARGET, LONG_TARGET };
```
- `night_mark.js`(三版):
```javascript
// 灯下印记 — 守夜人印章(连续 7 夜)/ 长明灯(30 夜)。数据源:本地 nightWatch 成就。
const nightWatch = require('../../utils/nightWatch');

Page({
  data: {
    navOff: 0,
    display: 0, target: 7, achieved: false, longLit: false,
    nights: [],      // 近 7 夜网格
  },
  onLoad() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const off = (info.statusBarHeight || 47) - 47;
    if (off !== 0) this.setData({ navOff: off });
    this._load();
  },
  onShow() { this._load(); },   // 从聊天页返回即刷新
  _load() {
    const st = nightWatch.getState();
    this.setData({
      display: st.display, target: st.target,
      achieved: st.achieved, longLit: st.longLit,
      nights: nightWatch.recentNights(7),
    });
  },
  /* 分享截图:印章卡 → node canvas → 保存相册(复用 today.js onShot 模式) */
  onShareCard() {
    wx.createSelectorQuery()
      .select('#sealCard')
      .fields({ node: true, size: true })
      .exec((res) => {
        const info = res && res[0];
        if (!info || !info.node) {
          wx.showToast({ title: '绘制暂不可用', icon: 'none' });
          return;
        }
        const canvas = info.node;
        const dpr = wx.getWindowInfo ? wx.getWindowInfo().pixelRatio : 2;
        canvas.width = 600 * dpr;
        canvas.height = 800 * dpr;
        const ctx = canvas.getContext('2d');
        ctx.scale(dpr, dpr);
        // 墨韵画法:宣纸底 + 朱砂印章方框 + 白字「守夜人」 + 进度文案
        ctx.fillStyle = '#F5EFE1';
        ctx.fillRect(0, 0, 600, 800);
        ctx.strokeStyle = '#A93A2C';
        ctx.lineWidth = 6;
        ctx.strokeRect(180, 150, 240, 240);
        ctx.fillStyle = '#A93A2C';
        ctx.font = 'bold 72px serif';
        ctx.textAlign = 'center';
        ctx.fillText('守 夜 人', 300, 300);
        ctx.font = '32px serif';
        ctx.fillText(`第 ${this.data.display}/7 夜`, 300, 480);
        ctx.fillText('七夜了。你睡,我守。', 300, 560);
        wx.canvasToTempFilePath({
          canvas, fileType: 'png',
          success: (r) => {
            wx.saveImageToPhotosAlbum({
              filePath: r.tempFilePath,
              success: () => wx.showToast({ title: '已存入相册', icon: 'none' }),
              fail: () => wx.showToast({ title: '请授权相册权限', icon: 'none' }),
            });
          },
        }, this);
      });
  },
});
```
- `night_mark.wxml`(三版):navrow 返回 + 印章卡 `#sealCard`(篆刻样式:朱砂红方框 + 白字"守夜人"或"长明灯")+ "第 X/7 夜" 进度 + 近 7 夜网格(7 个方框,达标红点亮,进行中半亮)+ 分享按钮;未达成时印章灰显"未点亮"。
- `me.js`(三版):BASE_ROWS 在"开灯提醒"行上方插入 `{ icon: '/assets/images/ic-seal.png', label: '灯下印记 · 守夜人', action: 'nightmark' }`;行点击分支加:
```javascript
      if (r.action === 'nightmark') { wx.navigateTo({ url: '/pages/night_mark/night_mark' }); return; }
```
- `app.json`(三版)注册 `pages/night_mark/night_mark`。

**验证**:
- [ ] 三版 auto-preview 编译全绿
- [ ] IDE 自动化:我的页 → 灯下印记 → 印章卡渲染(未达成灰态)→ 分享截图保存相册;console 无报错;截图+vision 确认视觉(印章居中/无重叠/网格对齐)
- [ ] 逻辑自测:IDE console 里跑 `nightWatch.touch` 注入 7 个连续夜间数据 → 印记页显示"第 7/7 夜"点亮态(临时注入后清除 storage)

**Commit**:
```bash
git add miniprogram/utils/nightWatch.js miniprogram/pages/night_mark miniprogram_simple/utils/nightWatch.js miniprogram_simple/pages/night_mark miniprogram_fusion/utils/nightWatch.js miniprogram_fusion/pages/night_mark miniprogram/app.json miniprogram_simple/app.json miniprogram_fusion/app.json miniprogram/pages/me miniprogram_simple/pages/me miniprogram_fusion/pages/me
git commit -m "feat(night): 守夜人成就本地存储+灯下印记页(7夜印章/30夜长明灯,三版)"
```

---

### Task 8: 夜色宣纸主题 + 深夜入口 + 聊天页深夜模式(三版)

**Files:**
- Create: `miniprogram/utils/nightMode.js`(三版)
- Modify: `miniprogram/app.wxss`(三版:夜色 token 组 + 灯笼动效)
- Modify: `miniprogram/pages/today/today.{js,wxml,wxss}`(三版:深夜入口横幅,晚间出现白天不出现)
- Modify: `miniprogram/pages/chat/chat.{js,wxml,wxss}`(三版:deepNight 模式/灯笼动效/挽留劝睡/灯语卡/要我记得吗按钮/12356 安全条/临时倾诉提示)
- Modify: `miniprogram/utils/api.js`(三版:night 系列方法 + chat/chatStream 传 deep_night)

**Interfaces:**
- Consumes: `GET /api/night/prefs`(时段档位/动效/挽留,本地缓存键 `ylm_night_prefs`)、`GET /api/night/lamp/today`、`POST /api/night/lamp/favorite`、`POST /api/night/remember`、`/api/chat/stream`(deep_night 参数)、`nightWatch.touch`
- Produces: `nightMode.isNightMode(preset, ts)`(与后端同规则)、`nightMode.nightWindow(preset)`、`nightMode.bjHour(ts)`;今日页深夜入口横幅(21:00 后出现,白天不出现);聊天页 deepNight 模式(夜色宣纸主题 + 灯笼 2 秒渐亮每日一次可关 + 输入框"慢慢说,我听着。" + 顶部"灯下漫谈·今夜说的话,天亮就忘" + 23:00-01:00 灯语卡(文字免费/语音会员/收藏/定时关闭 5-30 分钟)+ 挽留条(静默 25-40 分钟一次/夜)+ 0 点后劝睡条 + "要我记得吗"按钮 + 12356 安全提示条 + 每发一条 nightWatch.touch)

**实现要点**:

`utils/nightMode.js`(三版):
```javascript
/* 深夜模式判定(方案·灯下漫谈)— 与后端 src/engines/night_mode.py 同规则。
   从晚安推送进入(entry='night')由页面短路本判定,以入口为准不校验时间。 */
const PRESETS = { early: [20, 23], standard: [21, 1], night: [22, 2] };
const PRESET_LABEL = {
  early: '早睡党 20:00-23:00',
  standard: '标准 21:00-01:00',
  night: '夜猫子 22:00-02:00',
};

function bjHour(ts) {
  return new Date((ts || Date.now()) + 8 * 3600e3).getUTCHours();
}

function isNightMode(preset, ts) {
  const w = PRESETS[preset] || PRESETS.standard;
  const h = bjHour(ts);
  if (w[1] > w[0]) return h >= w[0] && h < w[1];   // 同日窗(早睡党 20:00-23:00)
  return h >= w[0] || h < w[1];                     // 跨日窗(标准/夜猫子,次日 end 点前仍算)
}

function nightWindow(preset) {
  const w = PRESETS[preset] || PRESETS.standard;
  const p = (n) => String(n).padStart(2, '0');
  return `${p(w[0])}:00-${p(w[1])}:00`;
}

module.exports = { PRESETS, PRESET_LABEL, isNightMode, nightWindow, bjHour };
```

`app.wxss`(三版)追加夜色 token 组与灯笼动效:
```css
/* 夜色宣纸主题(方案·灯下漫谈):深墨蓝底 + 灯笼暖光。根视图挂 .night 类,变量级联。 */
.night {
  --paper: #18222F;
  --paper-deep: #101824;
  --paper-card: #223043;
  --ink: #EAE4D6;
  --ink-mid: #B7C0CE;
  --ink-soft: #8B95A5;
  --ink-faint: #66707F;
  --ink-primary: #EAE4D6;
  --ink-secondary: #B7C0CE;
  --ink-muted: #8B95A5;
  --acc: #E8A34B;            /* 灯笼暖光 */
  --acc-soft: rgba(232, 163, 75, .18);
}
.lamp-fade { animation: lampFade 2s ease-out; }
@keyframes lampFade { from { opacity: .12; } to { opacity: 1; } }
```

`utils/api.js`(三版)追加:
```javascript
// ---- 深夜陪伴(灯下漫谈)----
function getNightPrefs() { return request('/api/night/prefs', { method: 'GET' }); }
function putNightPrefs(patch) { return request('/api/night/prefs', { method: 'PUT', data: patch }); }
function getNightStatus() { return request('/api/night/status', { method: 'GET' }); }
function getLampToday() { return request('/api/night/lamp/today', { method: 'GET' }); }
function favLamp(date) { return request('/api/night/lamp/favorite', { method: 'POST', data: { date } }); }
function getLampHistory() { return request('/api/night/lamp/history', { method: 'GET' }); }
function rememberNight(message) { return request('/api/night/remember', { method: 'POST', data: { message } }); }
```
`chat(message, scenario, history, options)` 与 `chatStream(message, handlers, options)` 的 data 各加一行:
```javascript
  if (options.deepNight) data.deep_night = true;
```
module.exports 尾部补 `getNightPrefs, putNightPrefs, getNightStatus, getLampToday, favLamp, getLampHistory, rememberNight`。

`today.js`(三版):
- data 加 `night: { mode: false, banner: true }`
- 新增 `_loadNight()`(onShow/onLoad 调用):
```javascript
  async _loadNight() {
    let preset = 'standard';
    try {
      const cached = wx.getStorageSync('ylm_night_prefs');
      if (cached && cached.preset) preset = cached.preset;
      const res = await api.getNightPrefs();
      const p = (res && res.prefs) || {};
      if (p.preset) { preset = p.preset; wx.setStorageSync('ylm_night_prefs', p); }
    } catch (e) { /* 缓存兜底,静默 */ }
    const mode = require('../../utils/nightMode').isNightMode(preset);
    if (mode !== this.data.night.mode) this.setData({ 'night.mode': mode });
  },
```
- 新增 `onNightEntry()`:
```javascript
  /* 深夜入口:进入深夜对话页(以入口为准,强制 deepNight) */
  onNightEntry() {
    try {
      const app = getApp();
      if (app && app.globalData) app.globalData.deepNight = true;
    } catch (e) { /* ignore */ }
    wx.reLaunch({ url: '/pages/chat/chat?entry=night' });
  },
```
- today.wxml 顶部(日期区下方)插入横幅(仅 `night.mode` 时渲染):
```xml
  <!-- 深夜入口横幅(21:00 后出现,白天不出现) -->
  <view wx:if="{{night.mode}}" class="night-banner lamp-fade" hover-class="night-banner-active" hover-stay-time="0" bindtap="onNightEntry">
    <view class="nb-lantern"><text class="nb-lantern-char">灯</text></view>
    <view class="nb-mid">
      <view class="nb-title">灯下漫谈 · 夜深了,灯还亮着</view>
      <view class="nb-sub">今夜说的话,天亮就忘。进来坐坐。</view>
    </view>
    <view class="nb-go">进去坐坐</view>
  </view>
```
- today.wxss(三版)补 night-banner 样式:宣纸卡底、灯笼暖光圆点、朱砂描边。

`chat.js`(三版)深夜模式改造(数据字段 + 方法,接入现有 onLoad/onShow/onSend/_send):
```javascript
// data 追加:
    nightMode: false,          // 深夜模式(夜色主题+语气)
    lampLit: false,            // 灯笼动效本轮是否已播
    lamp: { show: false, date: '', text: '', audioUrl: '', favorited: false,
            timerMin: 15, playing: false },
    keepBar: { show: false, text: '不用急着回。我就在这,灯给你留着。' },
    sleepBar: false,
    rememberPrompt: { show: false, msgId: '', userText: '' },
    safetyCard: { show: false, text: '' },
    notKeep: false,            // 倾诉临时模式提示条(默认不记录)

// onLoad 开头(在现有初始化之前):
  onLoad(options) {
    options = options || {};
    const app = getApp();
    const forceNight = options.entry === 'night'
      || (app && app.globalData && app.globalData.deepNight);
    if (app && app.globalData) app.globalData.deepNight = false; // 消费即清
    this._nightPreset = 'standard';
    try {
      const cached = wx.getStorageSync('ylm_night_prefs');
      if (cached && cached.preset) this._nightPreset = cached.preset;
    } catch (e) { /* ignore */ }
    const nightMode = require('../../utils/nightMode');
    this.data.nightMode = forceNight || nightMode.isNightMode(this._nightPreset);
    if (this.data.nightMode) this._enterNight();
    this._loadNightPrefs();           // 异步拉取档位/动效/挽留并缓存
    // ...现有初始化
  },

  /* 进入深夜模式:夜色主题 + 灯笼动效(每日一次,可关) + 临时倾诉提示 */
  _enterNight() {
    this.setData({ nightMode: true, notKeep: true });
    try { wx.setNavigationBarColor({ frontColor: '#ffffff', backgroundColor: '#18222F' }); } catch (e) {}
    try { wx.setBackgroundColor({ backgroundColor: '#18222F' }); } catch (e) {}
    const nightMode = require('../../utils/nightMode');
    const h = nightMode.bjHour(Date.now());
    if (h >= 23 || h === 0) this._loadLamp();        // 23:00-01:00 灯语卡
    if (h >= 0 && h < 4) this.setData({ sleepBar: true });  // 0 点后劝睡
    if (!this.data.lampLit && this._effectEnabled() && wx.getStorageSync('ylm_lamp_lit_date') !== this._bjDate()) {
      this.setData({ lampLit: true });
      wx.setStorageSync('ylm_lamp_lit_date', this._bjDate());
    }
    this._armKeepTimer();                             // 挽留定时器
  },

  _bjDate() { return new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10); },
  _effectEnabled() {
    try { const p = wx.getStorageSync('ylm_night_prefs'); return !p || p.effect_enabled !== 0; } catch (e) { return true; }
  },

  async _loadNightPrefs() {
    try {
      const res = await api.getNightPrefs();
      const p = (res && res.prefs) || {};
      wx.setStorageSync('ylm_night_prefs', p);
      if (!this.data.nightMode && p.preset) {
        this.data.nightMode = require('../../utils/nightMode').isNightMode(p.preset);
        if (this.data.nightMode) this._enterNight();
      }
    } catch (e) { /* 静默 */ }
  },
```
发送钩子(_send/流式成功回调内,deepNight 时):
```javascript
  _nightTouch() {
    if (!this.data.nightMode) return;
    try { require('../../utils/nightWatch').touch(Date.now()); } catch (e) { /* ignore */ }
  },
```
灯语卡:
```javascript
  async _loadLamp() {
    try {
      const res = await api.getLampToday();
      const l = (res && res.lamp) || {};
      if (!l || !l.text) return;
      const p = wx.getStorageSync('ylm_night_prefs') || {};
      this.setData({
        'lamp.show': true, 'lamp.date': l.date, 'lamp.text': l.text,
        'lamp.audioUrl': l.audio_url || '',
        'lamp.favorited': !!l.favorited,
        'lamp.timerMin': p.lamp_timer_min || 15,
      });
    } catch (e) { /* 静默 */ }
  },
  onLampPlay() {
    if (!this.data.lamp.audioUrl) { wx.showToast({ title: '语音版为会员权益', icon: 'none' }); return; }
    if (!this._lampAudio) this._lampAudio = wx.createInnerAudioContext();
    const a = this._lampAudio;
    a.src = this.data.lamp.audioUrl;
    a.play();
    this.setData({ 'lamp.playing': true });
    if (this._lampTimer) clearTimeout(this._lampTimer);
    this._lampTimer = setTimeout(() => { a.stop(); this.setData({ 'lamp.playing': false }); },
      this.data.lamp.timerMin * 60 * 1000);   // 定时关闭(默认 15 分钟)
  },
  onLampTimerChange(e) {
    this.setData({ 'lamp.timerMin': Number(e.detail.value) });
    if (this._lampAudio && this.data.lamp.playing) { /* 重新计时 */ 
      if (this._lampTimer) clearTimeout(this._lampTimer);
      this._lampTimer = setTimeout(() => { this._lampAudio.stop(); this.setData({ 'lamp.playing': false }); },
        this.data.lamp.timerMin * 60 * 1000);
    }
  },
  async onLampFav() {
    try {
      const res = await api.favLamp(this.data.lamp.date);
      this.setData({ 'lamp.favorited': !!res.favorited });
      wx.showToast({ title: res.favorited ? '已收藏 · 入笺匣' : '已取消收藏', icon: 'none' });
    } catch (e) { wx.showToast({ title: '操作失败', icon: 'none' }); }
  },
```
挽留(静默 25-40 分钟,1 次/夜):
```javascript
  _armKeepTimer() {
    if (this._keepTimer) clearTimeout(this._keepTimer);
    if (this.data.sleepBar) return;
    if (wx.getStorageSync('ylm_keep_date') === this._bjDate()) return;
    const p = wx.getStorageSync('ylm_night_prefs') || {};
    if (p.keep_enabled === 0) return;
    const waitMs = (25 + Math.floor(Math.random() * 16)) * 60 * 1000;  // 25-40 分钟
    this._keepTimer = setTimeout(() => {
      this.setData({ 'keepBar.show': true });
      wx.setStorageSync('ylm_keep_date', this._bjDate());
    }, waitMs);
  },
```
"要我记得吗"按钮(流式 done 后扫描回复;每夜一次):
```javascript
  _scanRemember(replyText) {
    if (this.data.nightMode && /要记住|要我记|帮我记住/.test(replyText || '')
        && wx.getStorageSync('ylm_remember_date') !== this._bjDate()) {
      const host = require('../../utils/streamHost');
      const msgs = (host.getState && host.getState().messages) || [];
      const lastUser = msgs.slice().reverse().find((m) => m && m.role === 'user');
      this.setData({ rememberPrompt: { show: true, msgId: '', userText: (lastUser && lastUser.content) || '' } });
    }
  },
  async onRememberYes() {
    const t = this.data.rememberPrompt.userText;
    this.setData({ rememberPrompt: { show: false, msgId: '', userText: '' } });
    wx.setStorageSync('ylm_remember_date', this._bjDate());
    if (!t) return;
    try {
      const res = await api.rememberNight(t);
      wx.showToast({ title: res.remembered ? '已记下 · 仅今晚有效' : '今晚已经记过啦', icon: 'none' });
    } catch (e) { wx.showToast({ title: '记录失败', icon: 'none' }); }
  },
  onRememberNo() {
    this.setData({ rememberPrompt: { show: false, msgId: '', userText: '' } });
    wx.setStorageSync('ylm_remember_date', this._bjDate());
  },
```
12356 安全条(输入与回复双向检测):
```javascript
  _checkSafety(text) {
    if (!text) return false;
    if (/自杀|自伤|轻生|不想活|活不下去|想死|结束生命/.test(text)) {
      this.setData({ safetyCard: {
        show: true,
        text: '我听到你了。请先拨打心理援助热线 12356(24 小时),白天我会陪你联系专业人士。你很重要。',
      } });
      return true;
    }
    return false;
  },
```
chat.wxml(三版)根节点加夜色类与各卡片(插入现有结构):
```xml
<view class="screen {{nightMode ? 'night' : ''}}" style="--nav-off:{{navOff}}px" bindtap="onListTap">
  <!-- 深夜标识:灯笼 + 倾诉临时提示 -->
  <view wx:if="{{nightMode}}" class="night-head {{lampLit ? 'lamp-fade' : ''}}">
    <image class="night-lantern" src="/assets/images/ic-lantern-on.png"></image>
    <text>灯下漫谈 · 今夜说的话,天亮就忘</text>
  </view>
  <!-- 灯语卡(23:00-01:00) -->
  <view wx:if="{{lamp.show}}" class="lamp-card">
    <view class="lamp-title">枕边灯语</view>
    <view class="lamp-text">{{lamp.text}}</view>
    <view class="lamp-actions">
      <view class="lamp-btn" bindtap="onLampPlay">{{lamp.playing ? '暂停' : '播放'}}</view>
      <view class="lamp-btn" bindtap="onLampFav">{{lamp.favorited ? '已收藏' : '收藏'}}</view>
      <picker class="lamp-timer" range="{{[5,10,15,30]}}" value="2" bindchange="onLampTimerChange">
        <view class="lamp-btn">定时关闭 {{lamp.timerMin}} 分钟 ▾</view>
      </picker>
    </view>
  </view>
  <!-- 挽留条(静默 25-40 分钟) -->
  <view wx:if="{{keepBar.show}}" class="night-keep">{{keepBar.text}}</view>
  <!-- 0 点后劝睡 -->
  <view wx:if="{{sleepBar}}" class="night-keep">天都快亮了。我给你留一盏小灯,明天接着陪你聊。睡吧。</view>
  <!-- 要我记得吗 -->
  <view wx:if="{{rememberPrompt.show}}" class="keep-prompt">
    <text>这句,要帮你记住吗?</text>
    <view class="kp-btn" bindtap="onRememberYes">要记住</view>
    <view class="kp-btn" bindtap="onRememberNo">不用</view>
  </view>
  <!-- 12356 心理援助 -->
  <view wx:if="{{safetyCard.show}}" class="safety-card">
    <text>{{safetyCard.text}}</text>
  </view>
```
chat.wxss(三版)补 night-head/lamp-card/keep-prompt/safety-card/night-keep 样式(夜色 token:深底浅字、灯笼暖光边框、朱砂描边)。

**验证**:
- [ ] 三版 auto-preview 编译全绿
- [ ] IDE 自动化(设置时间):白天(15:00)→ 今日页无深夜横幅;晚间(21:30)→ 横幅出现,点入聊天页 → 夜色主题+灯笼动效+输入框"慢慢说,我听着。";灯语卡(23:00 后)渲染文字,免费无播放钮;会员 mock 后显示播放钮且可播放(8768 需启动);"要我记得吗"触发后按钮落库成功;0 点后劝睡条出现;自伤关键词输入 → 12356 卡;console 无报错;截图+vision 复核视觉

**Commit**(三版):
```bash
git add miniprogram/utils/nightMode.js miniprogram/utils/api.js miniprogram/app.wxss miniprogram/pages/today miniprogram/pages/chat miniprogram_simple/utils/nightMode.js miniprogram_simple/utils/api.js miniprogram_simple/app.wxss miniprogram_simple/pages/today miniprogram_simple/pages/chat miniprogram_fusion/utils/nightMode.js miniprogram_fusion/utils/api.js miniprogram_fusion/app.wxss miniprogram_fusion/pages/today miniprogram_fusion/pages/chat
git commit -m "feat(night): 夜色宣纸主题+深夜入口+聊天页深夜模式(灯笼动效/挽留劝睡/灯语卡/要我记得吗/12356,三版)"
```

---

### Task 9: 晨笺 23:00 晚安推送改造为深夜第一入口

**Files:**
- Modify: `src/main.py`(`_send_jian_batch` night 分支改深夜版文案 + 落地页 entry=night)
- Test: `scripts/test_night_push.py`

**Interfaces:**
- Consumes: Task 6 晨笺 `_precompute_jian_for`(当日宜忌)、`send_template`(复用 wechat_mp)
- Produces: 晚安模板消息深夜版:thing1"明灯 · 夜话" / thing2"夜深了,灯还亮着" / thing3"明日宜X 忌Y" / thing4"今夜说的话,天亮就忘";url 落地 `pages/chat/chat?entry=night`(前端以入口为准强进深夜模式,白天点开也生效);零新增推送通道

- [ ] **Step 1: 写失败测试**

创建 `scripts/test_night_push.py`:
```python
"""晚安推送深夜版测试:深夜文案 + 入口落地页(复用晨笺 _send_jian_batch night 分支)"""
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
dao.upsert_pref("u1", {"night_enabled": 1, "night_time": "23:00", "jian_enabled": 0,
                       "bound_status": "bound", "mp_openid": "oU1"})

sent = []
def fake_send(oid, tpl, data, url=""):
    sent.append({"oid": oid, "tpl": tpl, "data": data, "url": url}); return {}

with mock.patch("src.services.wechat_mp.send_template", side_effect=fake_send), \
     mock.patch("src.services.wechat_mp.mp_ready", return_value=True), \
     mock.patch("src.main._precompute_jian_for", return_value={
         "date": "2026-08-12", "day_ganzhi": "庚午",
         "suitable": ["早睡", "静心"], "unsuitable": ["熬夜"], "quote": "火气偏旺"}):
    stats = main_mod._send_jian_batch(dao, "23:00", "night")
    check("推送 u1", stats["pushed"] == 1 and sent[0]["oid"] == "oU1")
    d = sent[0]["data"]
    check("深夜版标题", d["thing1"]["value"] == "明灯 · 夜话")
    check("深夜陪伴承诺", d["thing4"]["value"] == "今夜说的话,天亮就忘")
    check("灯语宜忌", d["thing3"]["value"].startswith("明日宜"))
    check("落地页 entry=night", sent[0]["url"].endswith("pages/chat/chat?entry=night"))

print(f"\nALL PASS ({ok})")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python3 scripts/test_night_push.py`
Expected: `FAIL: 深夜版标题`(当前 night 分支 thing1 为干支+夜深文案)

- [ ] **Step 3: 实现**

`src/main.py` 的 `_send_jian_batch` night 分支(约 273-285 行)整体替换为:
```python
            else:
                # 深夜版晚安(方案·灯下漫谈):深夜陪伴第一入口。
                # 落地页带 entry=night,前端以入口为准强进深夜模式(白天点开也生效);
                # thing 字段均 ≤20 字(微信模板消息上限);按钮文案由服务号模板配置,
                # 当前以 thing4 承诺文案 + 落地页入口承接「点一盏灯,说说话」。
                night_content = _precompute_jian_for(date_str)
                data = {
                    "thing1": {"value": "明灯 · 夜话"[:20]},
                    "thing2": {"value": "夜深了,灯还亮着"[:20]},
                    "thing3": {"value": (
                        f"明日宜{','.join(night_content.get('suitable', [])[:3])}"
                        f" 忌{','.join(night_content.get('unsuitable', [])[:3])}"
                    )[:20]},
                    "thing4": {"value": "今夜说的话,天亮就忘"[:20]},
                }
                url = "pages/chat/chat?entry=night"
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python3 scripts/test_night_push.py`
Expected: `ALL PASS (5)`

- [ ] **Step 5: Commit**

```bash
git add src/main.py scripts/test_night_push.py
git commit -m "feat(night): 晨笺23:00晚安推送改造深夜版(明灯夜话+天亮就忘+entry=night落地)"
```

---

### Task 10: 设置页"深夜陪伴"区(三版)

**Files:**
- Modify: `miniprogram/pages/settings/settings.{js,wxml,wxss}`(三版)

**Interfaces:**
- Consumes: `GET/PUT /api/night/prefs`(Task 2);本地 `ylm_jian_whisper`(晨笺私语既有键)与服务端 whisper_enabled 双向同步
- Produces: 设置页"深夜陪伴"区(方案 §三关键规则):时段档位 picker(三档:早睡党 20:00-23:00 / 标准 21:00-01:00 / 夜猫子 22:00-02:00)、点灯动效开关、深夜挽留开关、灯语定时关闭 picker(5/10/15/30 分钟)、私语开关(联动晨笺,文案注明"关闭后灯语与晨笺只说宜忌金句")、倾诉模式说明("今夜说的话,天亮就忘。想让我记住的,点那里。")

**实现要点(settings.js,三版)**:
- data 加 `nightPresets`(三档 label 数组)/ `nightPresetIdx` / `nightEffectOn` / `nightKeepOn` / `lampTimerOptions:[5,10,15,30]` / `lampTimerIdx` / `nightLoading`
- `onShow` 水合(与 jian prefs 并行):
```javascript
  async _loadNightPrefs() {
    try {
      const res = await api.getNightPrefs();
      const p = (res && res.prefs) || {};
      const nightMode = require('../../utils/nightMode');
      const labels = Object.keys(nightMode.PRESET_LABEL);
      const idx = Math.max(0, labels.indexOf(p.preset || 'standard'));
      const tIdx = Math.max(0, [5, 10, 15, 30].indexOf(p.lamp_timer_min || 15));
      this.setData({
        nightLoading: false,
        nightPresetIdx: idx,
        nightEffectOn: p.effect_enabled !== 0,
        nightKeepOn: p.keep_enabled !== 0,
        lampTimerIdx: tIdx,
        whisperOn: p.whisper_enabled !== 0,
      });
      try { wx.setStorageSync('ylm_night_prefs', p); } catch (e) {}
    } catch (e) { this.setData({ nightLoading: false }); }
  },
```
- 各控件变更处理:
```javascript
  onNightPresetChange(e) {
    const nightMode = require('../../utils/nightMode');
    const labels = Object.keys(nightMode.PRESET_LABEL);
    const preset = labels[Number(e.detail.value)] || 'standard';
    this.setData({ nightPresetIdx: Number(e.detail.value) });
    api.putNightPrefs({ preset }).catch(() => wx.showToast({ title: '保存失败', icon: 'none' }));
  },
  onNightEffectSwitch(e) {
    this.setData({ nightEffectOn: e.detail.value });
    api.putNightPrefs({ effect_enabled: e.detail.value }).catch(() => {});
  },
  onNightKeepSwitch(e) {
    this.setData({ nightKeepOn: e.detail.value });
    api.putNightPrefs({ keep_enabled: e.detail.value }).catch(() => {});
  },
  onLampTimerChange(e) {
    const min = [5, 10, 15, 30][Number(e.detail.value)] || 15;
    this.setData({ lampTimerIdx: Number(e.detail.value) });
    api.putNightPrefs({ lamp_timer_min: min }).catch(() => {});
  },
  onNightWhisperSwitch(e) {
    this.setData({ whisperOn: e.detail.value });
    try { wx.setStorageSync('ylm_jian_whisper', e.detail.value ? 'on' : 'off'); } catch (err) {}
    api.putNightPrefs({ whisper_enabled: e.detail.value }).catch(() => {});
  },
```
- settings.wxml(三版)在"消息订阅"区之后插入"深夜陪伴"区(样式沿用 stj-* 行结构):
```xml
  <!-- 深夜陪伴(方案·灯下漫谈:时段档位/动效/挽留/灯语定时/私语/倾诉说明) -->
  <view class="op-label"><text>深夜陪伴</text></view>
  <view class="me-row row-line" hover-class="me-row-active" hover-stay-time="0">
    <text class="me-label">深夜时段</text>
    <picker range="{{nightPresetLabels}}" value="{{nightPresetIdx}}" bindchange="onNightPresetChange">
      <view class="me-val">{{nightPresetLabels[nightPresetIdx]}} ▾</view>
    </picker>
  </view>
  <view class="me-row row-line">
    <text class="me-label">点灯动效</text>
    <switch class="stj-switch" checked="{{nightEffectOn}}" disabled="{{nightLoading}}" bindchange="onNightEffectSwitch" color="#A93A2C" />
  </view>
  <view class="me-row row-line">
    <text class="me-label">深夜挽留</text>
    <switch class="stj-switch" checked="{{nightKeepOn}}" disabled="{{nightLoading}}" bindchange="onNightKeepSwitch" color="#A93A2C" />
  </view>
  <view class="me-row row-line">
    <text class="me-label">灯语定时关闭</text>
    <picker range="{{lampTimerOptions}}" value="{{lampTimerIdx}}" bindchange="onLampTimerChange">
      <view class="me-val">{{lampTimerOptions[lampTimerIdx]}} 分钟 ▾</view>
    </picker>
  </view>
  <view class="me-row row-line">
    <text class="me-label">私语(联动晨笺)</text>
    <switch class="stj-switch" checked="{{whisperOn}}" disabled="{{nightLoading}}" bindchange="onNightWhisperSwitch" color="#A93A2C" />
  </view>
  <view class="stj-note">今夜说的话,天亮就忘。想让我记住的,点那里。</view>
  <view class="stj-note">关闭私语后,灯语与晨笺只出宜忌金句。</view>
```

**验证**:
- [ ] 三版 auto-preview 编译全绿
- [ ] IDE 自动化:设置页深夜陪伴区各控件变更 → 重新进入页面状态保持(水合);GET/PUT /api/night/prefs 实测;截图+vision 复核(区布局/无重叠)

**Commit**(三版):
```bash
git add miniprogram/pages/settings miniprogram_simple/pages/settings miniprogram_fusion/pages/settings
git commit -m "feat(night): 设置页深夜陪伴区(时段档位/动效/挽留/灯语定时/私语联动,三版)"
```

---

### Task 11: 集成验证 + 上线准备

**Files:** 无新文件

- [ ] 全量回归:
```bash
cd /mnt/e/fortune-agent
.venv/bin/python3 scripts/test_night_mode.py && \
.venv/bin/python3 scripts/test_night_api.py && \
.venv/bin/python3 scripts/test_night_soliloquy.py && \
.venv/bin/python3 scripts/test_lamp_api.py && \
.venv/bin/python3 scripts/test_night_temp.py && \
.venv/bin/python3 scripts/test_night_remember.py && \
.venv/bin/python3 scripts/test_night_push.py && \
.venv/bin/python3 scripts/test_jian_pref.py && \
.venv/bin/python3 scripts/test_jian_api.py && \
.venv/bin/python3 scripts/test_jian_scheduler.py && \
.venv/bin/python3 scripts/test_p2.py && \
.venv/bin/python3 scripts/test_stream.py
```
全部 `ALL PASS` 全绿
- [ ] 三版 auto-preview 全绿 + 关键屏截图 vision 复核(今日页深夜横幅 / 聊天页夜色主题与灯语卡 / 灯下印记页 / 设置页深夜陪伴区)
- [ ] 服务重启(8767)+ `curl localhost:8767/api/health` + `curl -H "Authorization: Bearer <dev>" localhost:8767/api/night/status` 实测;8768 TTS 服务确认存活(`curl localhost:8768/tts/health`)
- [ ] 隐私红线自检:deepNight 会话后 `SELECT temp FROM sessions WHERE user_id=...` 全 1;24h 后 cleanup 生效(可手动调 cleanup_temp 验证);灯语文本 grep 无用户原话;`night_remember` 表无原文字段
- [ ] 交付清单:.env 需配置 `DEEPSEEK_API_KEY`(灯语独白 LLM);服务号 `MP_NIGHT_TEMPLATE_ID` 模板文案更新为深夜版(thing1 明灯·夜话 / thing2 夜深了,灯还亮着 / thing3 明日宜忌 / thing4 今夜说的话,天亮就忘,申请按钮"点一盏灯,说说话"为用户侧);TTS 8768 启动命令见 scripts/tts_server.py(柔缓女声 rate -10% 已内置)
- [ ] Commit 收尾
