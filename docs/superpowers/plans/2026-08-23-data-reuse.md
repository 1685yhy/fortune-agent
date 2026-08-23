# 对话数据复用体系 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 数据源统一（persons 单一事实源）+ 排盘结果落库 + 存量数据全类型直读 + 问事快路径，让对话"问什么直读什么"，页面与答案永远一致。

**Architecture:** ① 修根：对话排盘不再无条件覆盖档案，默认命主（persons）为唯一权威，profile/登录同源；② 新增 chart_records 落库排盘结果（AES 密文），重看 0 重跑；③ 意图层新增"查记录"直读分支 + LLM 可调 `查记录` 工具（TOOL_REGISTRY 扩展），覆盖档案/解梦/历史/签/名笺/灯语/择吉/晨笺/收藏/会员 10 类；④ 补两个缺口：晨笺内容落库（jian_cards）、收藏后端化（favorites + 本地导入）；⑤ 问事路径跳过预检索（RAG 延迟化），注入画像+已存结果，4-7 次 LLM → 1-2 次。

**Tech Stack:** Python 3.10+ / FastAPI / SQLite（AES-256-GCM via src/security/encryption.py DataEncryptor）/ 微信小程序（前端收藏改造）

## Global Constraints

- 加密：所有新表敏感字段必须走 `dao.py` 的 `_encrypt_text` / `_decrypt_or_plain`（DataEncryptor 版本化 AES-256-GCM），解密仅服务端内存
- 红线不松：合盘 TA 生辰、取名生辰零落库（hehun/ming 路径不动）；share_entries 匿名分享保持无归属
- 向量库只增不改；RAG 检索算法不动（只改调用时机）
- 降级模式（无额度）不受影响：仍零 LLM
- 同步运行副本（/home/a/fortune-run）：禁 rsync --delete，新表靠运行副本手工建表（SQLite 文件复制或启动时 init_db 自动建——SCHEMA_SQL 用 CREATE TABLE IF NOT EXISTS，运行副本重启即自动建表；若新表在独立 DAO 文件则运行副本需更新代码）
- 测试运行：`cd /mnt/e/fortune-agent-deploy && python -m pytest tests/<file> -v`（各测试文件自带 sys.path 注入，无 conftest）
- 开发时段约束：9-12 / 14-18 之外执行

---

### Task 1: 年份守卫移除——默认命主为唯一事实源

**Files:**
- Modify: `src/bot/handler.py:2031-2091`（`_sync_person_profile` self 分支）
- Test: `tests/test_person_sync.py`（新建）

**Interfaces:**
- Consumes: `PersonDAO.get_default_person/create_person/update_person`（person_dao.py，签名见探索事实 §3）
- Produces: 无新签名；行为契约——`_sync_person_profile(user_id, birth, subject="self")` 后，用户必有默认命主，且其出生信息 == 本次 birth（年份不同也更新，不再新建"命主N"）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_person_sync.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from unittest.mock import Mock
from src.bot.handler import MessageHandler

def _make_handler():
    h = object.__new__(MessageHandler)
    h._analysis_facts = {}
    h.memory_system = None
    return h

def test_sync_person_updates_default_when_year_differs():
    """年份不同→更新默认命主，不新建'命主N'（单一事实源）"""
    from src.storage.person_dao import PersonDAO
    pdao = PersonDAO(":memory:")
    pdao.create_person("u1", name="我", relation="自己", is_default=True,
                       birth={"gender":"男","birth_year":1990,"birth_month":5,
                              "birth_day":20,"birth_hour":15,"birth_minute":0,
                              "calendar":"solar","city":"北京"})
    h = _make_handler()
    h.person_dao = pdao  # _sync_person_profile 内部用 self.person_dao
    h.dao = Mock()
    h._sync_person_profile("u1",
        {"year":1991,"month":6,"day":21,"hour":9,"minute":0,"city":"上海","gender":"女"},
        subject="self")
    persons = pdao.list_persons("u1")
    assert len(persons) == 1, "年份不同不得新建命主"
    assert persons[0]["birth_year"] == 1991
    assert persons[0]["is_default"] == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_person_sync.py -v`
Expected: FAIL（当前逻辑年份不同→新建"命主N"，len(persons)==2）

- [ ] **Step 3: 修改 `_sync_person_profile`**

在 `src/bot/handler.py:2060-2072`，将 self 分支（subject=="self"）替换为：

```python
default = pdao.get_default_person(user_id)
if default:
    # 单一事实源：默认命主唯一，出生信息以最新排盘为准（年份不同也更新）
    pdao.update_person(user_id, default["id"], birth=b)
else:
    pdao.create_person(user_id, name="我", relation="自己",
                       is_default=True, birth=b)
```

subject=other 分支（L2073-2088）不动——帮他人排盘仍按关系/姓名新建（find_person_by_birth 复用逻辑保留）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_person_sync.py -v`
Expected: PASS

- [ ] **Step 5: 补回归测试——other 分支不受影响**

在 test_person_sync.py 追加：

```python
def test_sync_person_other_still_creates():
    """subject=other 帮他人排盘→仍新建命主（多人档案）"""
    from src.storage.person_dao import PersonDAO
    pdao = PersonDAO(":memory:")
    pdao.create_person("u1", name="我", relation="自己", is_default=True,
                       birth={"gender":"男","birth_year":1990,"birth_month":5,
                              "birth_day":20,"birth_hour":15,"birth_minute":0,
                              "calendar":"solar","city":"北京"})
    h = _make_handler()
    h.person_dao = pdao
    h.dao = Mock()
    h._analysis_facts = {"u1": {"subject": "other"}}
    h._sync_person_profile("u1",
        {"year":1991,"month":6,"day":21,"hour":9,"minute":0,"city":"上海","gender":"女"},
        subject="other", facts={"name":"父亲","relation":"父母"})
    assert len(pdao.list_persons("u1")) == 2
```

Run: `python -m pytest tests/test_person_sync.py -v` → 全 PASS

- [ ] **Step 6: Commit**

```bash
git add src/bot/handler.py tests/test_person_sync.py
git commit -m "fix(data): 默认命主唯一事实源——年份不同更新而非新建命主N"
```

---

### Task 2: profile 同源 + 登录响应补 bazi + 迁移验证

**Files:**
- Modify: `src/api/user.py:557-618`（user_profile）、`src/api/user.py:237-326`（user_login）
- Test: `tests/test_profile_consistency.py`（新建）

**Interfaces:**
- Consumes: `PersonDAO.default_person_bazi_info(user_id)`（person_dao.py L360-380，返回与旧 bazi_info 同名字段 dict）、`UserDAO.get_user_bazi(user_id)`
- Produces: profile 返回体 `bazi_info`（生日，来自默认命主）与 `has_bazi`/`bazi_label`（四柱）**同源**——四柱优先读 chart_records（Task 3 后实现，此任务先读 bazi_info，Task 4 接入后自动同源）；login 返回体新增 `"bazi": {...}`（默认命主出生信息 dict 或 null）

- [ ] **Step 1: 写失败测试（profile 生日与四柱来源一致）**

```python
# tests/test_profile_consistency.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from src.storage.person_dao import PersonDAO
from src.storage.dao import UserDAO

def test_profile_same_source_birth_and_chart(tmp_path):
    """同一个人：persons 默认命主生日 == users.bazi_info 生日（同步后一致）"""
    db = str(tmp_path / "t.db")
    dao = UserDAO(db)
    pdao = PersonDAO(db)
    dao.save_user_bazi("u1", {"year":1990,"month":5,"day":20,"hour":15,
                              "minute":0,"city":"北京","gender":"男",
                              "bazi":["庚午","辛巳","甲申","壬申"]})
    pdao.create_person("u1", name="我", relation="自己", is_default=True,
                       birth={"gender":"男","birth_year":1990,"birth_month":5,
                              "birth_day":20,"birth_hour":15,"birth_minute":0,
                              "calendar":"solar","city":"北京"})
    bazi_info = pdao.default_person_bazi_info("u1")
    chart = dao.get_user_bazi("u1")
    assert bazi_info["year"] == chart["year"] == 1990
    assert bazi_info["month"] == chart["month"] == 5
    assert bazi_info["day"] == chart["day"] == 20
    assert bazi_info["hour"] == chart["hour"] == 15
    assert bazi_info["gender"] == chart["gender"] == "男"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_profile_consistency.py -v`
Expected: 取决于 T1 是否已合入（T1 已移除年份守卫后本测试即 PASS——若 PASS 则本测试转为回归守护，继续 Step 3-5 的接口改造）

- [ ] **Step 3: profile 四柱同源改造**

`src/api/user.py:586` 附近：`chart = _dao.get_user_bazi(user_id)` 前加注释说明同源契约，并改为（chart_records 接入后由 Task 4 改读新表，此处先保证 bazi_info 与 persons 同步后的同源断言）：

```python
# 同源契约：birth（persons 默认命主）与 chart（users.bazi_info 快照）必须由同一保存
# 路径写入（_save_bazi_records 排盘后统一同步，见 handler.py）。若不一致则数据缺陷。
chart = _dao.get_user_bazi(user_id)
```

（逻辑不变，契约以测试守护；Task 4 后 chart 优先读 chart_records。）

- [ ] **Step 4: 登录响应补 bazi**

`src/api/user.py` user_login 返回体（L318-326）增加：

```python
# 登录即带档案：默认命主出生信息（供前端 globalData 直接使用）
default_person = pdao.get_default_person(user_id)
bazi = default_person_bazi_info(user_id) if default_person else None
# → 返回体加 "bazi": bazi
```

注意 user_login 需获取 pdao（若未注入则用 `PersonDAO(_dao.db_path)`）。

- [ ] **Step 5: 迁移验证测试（老用户自动补建）**

```python
def test_legacy_user_auto_migrated_to_person(tmp_path):
    """老用户：仅 users.bazi_info 有档案 → 首次 get_default_person 自动补建命主"""
    db = str(tmp_path / "t.db")
    dao = UserDAO(db)
    dao.save_user_bazi("u2", {"year":1985,"month":3,"day":10,"hour":8,
                              "minute":0,"city":"广州","gender":"女",
                              "bazi":["乙丑","己卯","戊子","丙辰"]})
    pdao = PersonDAO(db)
    p = pdao.get_default_person("u2")  # auto_migrate=True 默认
    assert p is not None
    assert p["name"] == "我" and p["relation"] == "自己"
    assert p["birth_year"] == 1985
    assert p["is_default"] == 1
```

Run: `python -m pytest tests/test_profile_consistency.py -v` → 全 PASS

- [ ] **Step 6: 接口级测试（可选加）**

仿照 test_paipan_api.py 模式（JWT_SECRET_KEY + TestClient + JWTHandler 造 token），断言 `GET /api/user/profile` 返回的 `bazi_info` 生日与 `bazi_label` 四柱对应同一生辰。

- [ ] **Step 7: Commit**

```bash
git add src/api/user.py tests/test_profile_consistency.py
git commit -m "feat(api): profile 同源契约 + 登录响应补 bazi 档案 + 老用户迁移守护"
```

---

### Task 3: chart_records 表 + ChartDAO（排盘结果落库）

**Files:**
- Create: `src/storage/chart_dao.py`
- Modify: `src/storage/dao.py`（cleanup 覆盖——Task 11 统一做，此处可先不动）
- Test: `tests/test_chart_dao.py`（新建）

**Interfaces:**
- Produces: `ChartDAO(db_path)` 类，方法：
  - `save_chart(user_id, person_id, birth: dict, result: dict) -> int`（AES 密文落库，返回 id）
  - `get_latest_chart(user_id, person_id=None) -> Optional[dict]`（最近一条，解密展开；`{id, birth, bazi_json(解开的 result), created_at}`）
  - `list_charts(user_id, limit=10) -> List[dict]`
  - 归属校验：所有查询 `WHERE user_id=?`，无跨用户路径

- [ ] **Step 1: 写失败测试**

```python
# tests/test_chart_dao.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from src.storage.chart_dao import ChartDAO

def test_save_and_get_latest(tmp_path):
    dao = ChartDAO(str(tmp_path / "c.db"))
    birth = {"year":1990,"month":5,"day":20,"hour":15,"minute":0,
             "city":"北京","gender":"男","calendar":"solar"}
    result = {"bazi":["庚午","辛巳","甲申","壬申"],"day_master":"甲",
              "dayun":[["0","庚辰"]],"liunian":{"2026":"丙午"},"shensha":["天乙贵人"]}
    dao.save_chart("u1", person_id=1, birth=birth, result=result)
    r = dao.get_latest_chart("u1")
    assert r is not None
    assert r["bazi_json"]["bazi"] == ["庚午","辛巳","甲申","壬申"]
    assert r["birth"]["year"] == 1990
    # 密文存储：裸读表无明文
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "c.db"))
    row = conn.execute("SELECT bazi_enc, birth_enc FROM chart_records").fetchone()
    conn.close()
    assert ":" in row[0] and "庚午" not in row[0]  # 密文
    assert "1990" not in row[1]

def test_latest_only_and_user_isolation(tmp_path):
    dao = ChartDAO(str(tmp_path / "c.db"))
    birth = {"year":1990,"month":5,"day":20,"hour":15,"minute":0,"city":"北京","gender":"男"}
    dao.save_chart("u1", 1, birth, {"bazi":["a","b","c","d"]})
    dao.save_chart("u1", 1, birth, {"bazi":["e","f","g","h"]})
    dao.save_chart("u2", 2, birth, {"bazi":["x","y","z","w"]})
    r = dao.get_latest_chart("u1")
    assert r["bazi_json"]["bazi"] == ["e","f","g","h"]
    assert dao.get_latest_chart("u2")["bazi_json"]["bazi"] == ["x","y","z","w"]
    assert dao.get_latest_chart("u3") is None
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_chart_dao.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 ChartDAO**

```python
# src/storage/chart_dao.py
"""排盘结果持久化（对话/表单排盘统一落库，重看 0 重跑 0 生成）。"""
import json, logging
from src.storage.dao import _encrypt_text, _decrypt_or_plain, get_conn

logger = logging.getLogger(__name__)

SCHEMA_CHART = """
CREATE TABLE IF NOT EXISTS chart_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    person_id INTEGER,
    birth_enc TEXT,        -- AES: {year,month,day,hour,minute,city,gender,calendar}
    bazi_enc TEXT,         -- AES: BaziResult 全字段 JSON（四柱/大运/流年/神煞等）
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chart_user ON chart_records(user_id, created_at);
"""

class ChartDAO:
    def __init__(self, db_path: str):
        self.db_path = db_path
        conn = get_conn(db_path)
        try:
            conn.executescript(SCHEMA_CHART)
            conn.commit()
        finally:
            conn.close()

    def save_chart(self, user_id: str, person_id, birth: dict, result: dict) -> int:
        birth_enc = _encrypt_text(json.dumps(birth, ensure_ascii=False))
        bazi_enc = _encrypt_text(json.dumps(result, ensure_ascii=False, default=str))
        conn = get_conn(self.db_path)
        try:
            cur = conn.execute(
                "INSERT INTO chart_records (user_id, person_id, birth_enc, bazi_enc) "
                "VALUES (?,?,?,?)", (user_id, person_id, birth_enc, bazi_enc))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def _row_to_dict(self, row):
        if not row:
            return None
        return {"id": row[0], "birth": json.loads(_decrypt_or_plain(row[3]) or "{}"),
                "bazi_json": json.loads(_decrypt_or_plain(row[4]) or "{}"),
                "created_at": row[5]}

    def get_latest_chart(self, user_id: str, person_id=None) -> dict | None:
        if person_id is not None:
            row = get_conn(self.db_path).execute(
                "SELECT id, user_id, person_id, birth_enc, bazi_enc, created_at "
                "FROM chart_records WHERE user_id=? AND person_id=? "
                "ORDER BY id DESC LIMIT 1", (user_id, person_id)).fetchone()
        else:
            row = get_conn(self.db_path).execute(
                "SELECT id, user_id, person_id, birth_enc, bazi_enc, created_at "
                "FROM chart_records WHERE user_id=? ORDER BY id DESC LIMIT 1",
                (user_id,)).fetchone()
        return self._row_to_dict(row)

    def list_charts(self, user_id: str, limit: int = 10) -> list:
        rows = get_conn(self.db_path).execute(
            "SELECT id, user_id, person_id, birth_enc, bazi_enc, created_at "
            "FROM chart_records WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit)).fetchall()
        return [self._row_to_dict(r) for r in rows]
```

（列序：id, user_id, person_id, birth_enc, bazi_enc, created_at——`_row_to_dict` 索引对应此序。）

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_chart_dao.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/storage/chart_dao.py tests/test_chart_dao.py
git commit -m "feat(data): chart_records 排盘结果落库（AES 密文，归属隔离）"
```

---

### Task 4: 排盘写入点接入 chart_records

**Files:**
- Modify: `src/bot/handler.py:3219-3267`（`_save_bazi_records`）、`src/bot/handler.py:1246-1263`（`_tool_bazi`）、`src/api/paipan.py:72-86`（表单排盘）
- Test: `tests/test_chart_write_points.py`（新建）

**Interfaces:**
- Consumes: `ChartDAO.save_chart`（Task 3）
- Produces: 契约——`_save_bazi_records` 执行后必有 chart_records 行；`POST /api/paipan`（本人排盘）执行后亦有

- [ ] **Step 1: 写失败测试**

```python
# tests/test_chart_write_points.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from unittest.mock import Mock
from src.bot.handler import MessageHandler

def _result():
    r = Mock()
    r.bazi = ["庚午","辛巳","甲申","壬申"]
    r.day_master = "甲"
    r.dayun = [["0","庚辰"]]
    r.liunian = {"2026":"丙午"}
    r.shensha = ["天乙贵人"]
    r.nayin = ["路旁土"]
    r.wuxing = {"木":2}
    r.shishen = ["比肩"]
    r.geju = "正官格"
    r.yongshen = "甲木"
    r.raw_data = {}
    return r

def test_save_bazi_records_writes_chart(tmp_path):
    from src.storage.chart_dao import ChartDAO
    from src.storage.person_dao import PersonDAO
    h = object.__new__(MessageHandler)
    h.dao = Mock()
    h.memory_system = None
    h._analysis_facts = {}
    h.person_dao = PersonDAO(str(tmp_path / "p.db"))
    h.chart_dao = ChartDAO(str(tmp_path / "c.db"))
    birth = {"year":1990,"month":5,"day":20,"hour":15,"minute":0,
             "city":"北京","gender":"男"}
    h._save_bazi_records(_result(), birth, "帮我看看八字", "u1")
    r = h.chart_dao.get_latest_chart("u1")
    assert r is not None
    assert r["bazi_json"]["bazi"] == ["庚午","辛巳","甲申","壬申"]
    # 默认命主也建了
    p = h.person_dao.get_default_person("u1")
    assert p["birth_year"] == 1990
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_chart_write_points.py -v`
Expected: FAIL（_save_bazi_records 未写 chart_records，get_latest_chart 返回 None）

- [ ] **Step 3: 接入三个写入点**

a) `_save_bazi_records`（handler.py:3219-3267）在 `self.dao.save_consultation(...)` 后追加：

```python
    # 排盘结果落库（重看 0 重跑；subject=other 帮他人排盘也落库但归属本人名下）
    if hasattr(self, "chart_dao"):
        person = self.person_dao.get_default_person(user_id) \
            if _subject == "self" else None
        self.chart_dao.save_chart(
            user_id, (person or {}).get("id"),
            {"year": year, "month": month, "day": day, "hour": hour,
             "minute": minute, "city": city, "gender": gender,
             "calendar": "solar"},
            {"bazi": result.bazi, "day_master": getattr(result, "day_master", ""),
             "wuxing": getattr(result, "wuxing", {}),
             "shishen": getattr(result, "shishen", []),
             "dayun": getattr(result, "dayun", []),
             "liunian": getattr(result, "liunian", {}),
             "liunian_full": getattr(result, "liunian_full", []),
             "shensha": getattr(result, "shensha", []),
             "geju": getattr(result, "geju", ""),
             "yongshen": getattr(result, "yongshen", ""),
             "nayin": getattr(result, "nayin", []),
             "taiyuan": getattr(result, "taiyuan", ""),
             "qiyun_detail": getattr(result, "qiyun_detail", None)})
```

b) handler `__init__`（L385-402）加 `self.chart_dao = ChartDAO(self.dao.db_path)`（导入 ChartDAO；db_path 属性在 `self.dao.db_path`——测试 mock_dao 有 `db_path` 属性，见探索 §11 make_mock_handler，安全）。

c) `_tool_bazi`（L1246-1263）成功路径在 `self.dao.save_user_bazi(...)`/`_sync_person_profile(...)`/`save_consultation(...)` 后追加同样落库（复用 `_save_bazi_records` 的落库段，抽一个 `_persist_chart_result(user_id, result, birth, subject)` 私有方法防重复）。

d) `src/api/paipan.py:72-86`：paipan 成功计算后：

```python
    from src.storage.chart_dao import ChartDAO
    try:
        ChartDAO(req_db_path_or_shared).save_chart(
            uid, person.get("id") if hasattr(person, "get") else None,
            {"year": person.year, "month": person.month, "day": person.day,
             "hour": person.hour, "minute": person.minute, "city": person.city,
             "gender": person.gender, "calendar": "solar"},
            serialize_bazi(result, engine, person))
    except Exception as e:
        logger.warning("paipan 落库失败 %s", e)  # 不阻塞主流程
```

（paipan 模块需拿到与后端一致的 db 路径：优先从 setup() 注入，注入方式照 setup(engine) 模式新增 setup_db(path) 或在 paipan 模块内用与 main 相同的 DB 路径常量。）

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_chart_write_points.py -v`
Expected: PASS；再跑 `python -m pytest tests/test_bot.py tests/test_paipan_api.py -v` 确认无回归

- [ ] **Step 5: Commit**

```bash
git add src/bot/handler.py src/api/paipan.py tests/test_chart_write_points.py
git commit -m "feat(data): 排盘三写入点接入 chart_records（对话/工具/表单）"
```

---

### Task 5: 重看盘直读（0 引擎 0 LLM）

**Files:**
- Modify: `src/bot/handler.py`（`_handle_bazi` 前新增直读分支）
- Test: `tests/test_chart_reuse.py`（新建）

**Interfaces:**
- Consumes: `ChartDAO.get_latest_chart`（Task 3）
- Produces: 私有方法 `_try_reuse_chart(user_id, msg) -> Optional[str]`——命中返回直读文本，未命中返回 None（调用方继续原流程）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_chart_reuse.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from unittest.mock import Mock
from src.bot.handler import MessageHandler

REUSE_PATTERNS = ["我的盘", "我的八字", "上次的盘", "重新看看我的盘", "我的命盘"]

def test_reuse_keywords_return_chart_without_engine(tmp_path):
    from src.storage.chart_dao import ChartDAO
    h = object.__new__(MessageHandler)
    h.chart_dao = ChartDAO(str(tmp_path / "c.db"))
    h.chart_dao.save_chart("u1", 1,
        {"year":1990,"month":5,"day":20,"hour":15,"minute":0,"city":"北京","gender":"男"},
        {"bazi":["庚午","辛巳","甲申","壬申"],"day_master":"甲",
         "dayun":[["0","庚辰"]],"liunian":{"2026":"丙午"},"shensha":["天乙贵人"],
         "geju":"正官格","yongshen":"甲木"})
    text = h._try_reuse_chart("u1", "看看我的盘")
    assert text is not None
    assert "庚午" in text and "辛巳" in text and "甲申" in text and "壬申" in text
    assert "丙午" in text

def test_no_chart_returns_none(tmp_path):
    from src.storage.chart_dao import ChartDAO
    h = object.__new__(MessageHandler)
    h.chart_dao = ChartDAO(str(tmp_path / "c.db"))
    assert h._try_reuse_chart("u1", "看看我的盘") is None
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_chart_reuse.py -v`
Expected: FAIL（AttributeError: _try_reuse_chart 不存在）

- [ ] **Step 3: 实现直读**

在 handler.py 新增（放在 `_handle_bazi` 之前）：

```python
REUSE_KEYWORDS = ("我的盘", "我的八字", "上次的盘", "我的命盘", "重新看", "再看")

def _try_reuse_chart(self, user_id: str, msg: str) -> str | None:
    """重看盘直读：问'我的盘/我的八字'等且有已存结果 → 0 引擎 0 LLM 秒回。"""
    if not msg or not any(kw in msg for kw in self.REUSE_KEYWORDS):
        return None
    if "排" in msg and ("一次" in msg or "重新排" in msg):
        return None  # 明确要重新排盘 → 走全流程
    chart = getattr(self, "chart_dao", None) and self.chart_dao.get_latest_chart(user_id)
    if not chart:
        return None
    r = chart["bazi_json"]
    b = chart["birth"]
    bazi = r.get("bazi") or []
    lines = [f"这是你最近排过的盘（{chart['created_at']}）："]
    if bazi:
        stems = ["年柱","月柱","日柱","时柱"]
        lines += [f"{stems[i]}：{g}" for i, g in enumerate(bazi[:4])]
    lines.append(f"日主：{r.get('day_master','')} · 格局：{r.get('geju','') or '—'}")
    if r.get("dayun"):
        lines.append("大运：" + " → ".join(f"{a}岁{ganzhi}" for a, ganzhi in r["dayun"][:6]))
    if r.get("liunian"):
        lines.append("流年：" + "、".join(f"{y}年{g}" for y, g in list(r["liunian"].items())[:5]))
    if r.get("shensha"):
        lines.append("神煞：" + "、".join(r["shensha"][:8]))
    lines.append("（直接看的已存结果；要重新详细分析就说'重新帮我分析'）")
    return "\n".join(lines)
```

调用点：`_handle_bazi`（L2823）入口、`process()` 主入口（`_quick_intent` 判定为 bazi 前的通用路径）——在意图判定/引擎调用**之前**调用 `_try_reuse_chart`，命中即短路返回（不带 stream，直接返回文本）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_chart_reuse.py -v` → PASS
再跑 `python -m pytest tests/test_bot.py -v` 确认主链路无回归

- [ ] **Step 5: Commit**

```bash
git add src/bot/handler.py tests/test_chart_reuse.py
git commit -m "feat(chat): 重看盘直读已存结果（0 引擎 0 LLM 秒回）"
```

---

### Task 6: "查记录"意图直读分支（10 类存量数据）

**Files:**
- Create: `src/bot/record_query.py`（直读逻辑独立模块，handler 注入）
- Modify: `src/bot/handler.py`（`process()` 主流程在路由判定前接入）
- Test: `tests/test_record_query.py`（新建）

**Interfaces:**
- Produces: `RecordQuery(dao, person_dao, session_dao, chart_dao, qian_dao, ming_dao, lamp_dao, zeri_dao, jian_dao, member_dao)` 类，方法 `direct_query(user_id, msg) -> str | None`（命中返回直读文本，否则 None）；handler 加 `self.record_query = RecordQuery(...)`，在 `_route_by_scenario` 之前调用

- [ ] **Step 1: 写失败测试（每类一条）**

```python
# tests/test_record_query.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from src.bot.record_query import RecordQuery

def _rq(tmp_path, **kw):
    from src.storage.dao import UserDAO
    from src.storage.person_dao import PersonDAO
    from src.storage.session_dao import SessionDAO
    from src.storage.chart_dao import ChartDAO
    db = str(tmp_path / "r.db")
    return RecordQuery(UserDAO(db), PersonDAO(db), SessionDAO(db),
                       ChartDAO(db), **kw)

def test_query_archive(tmp_path):
    rq = _rq(tmp_path)
    rq.dao.save_user_bazi("u1", {"year":1990,"month":5,"day":20,"hour":15,
                                 "minute":0,"city":"北京","gender":"男",
                                 "bazi":["庚午","辛巳","甲申","壬申"]})
    out = rq.direct_query("u1", "我的档案是什么")
    assert out and "庚午" in out

def test_query_dream(tmp_path):
    rq = _rq(tmp_path)
    rq.dao.save_consultation("u1", "梦见在水里游", "dream", "鱼水之象……")
    out = rq.direct_query("u1", "我以前解过什么梦")
    assert out and "鱼水之象" in out

def test_query_history(tmp_path):
    rq = _rq(tmp_path)
    rq.session_dao.save_summary("u1", "用户关心财运，最近看房", ["关注置业"])
    out = rq.direct_query("u1", "我之前聊过什么")
    assert out and "财运" in out

def test_query_qian_lamp_zeri(tmp_path):
    rq = _rq(tmp_path)
    # 灵签
    from src.storage.qian_dao import QianSaveDAO
    q = QianSaveDAO(str(tmp_path / "q.db"))
    q.save("u1", 3)
    rq.qian_dao = q
    assert rq.direct_query("u1", "我摇过什么签") is not None
    # 灯语
    from src.storage.lamp_dao import LampDAO
    lamp = LampDAO(str(tmp_path / "l.db"))
    lamp.save("u1", "2026-08-23", "今日宜静心")
    rq.lamp_dao = lamp
    assert "宜静心" in rq.direct_query("u1", "我昨晚的灯语")
    # 择吉
    from src.storage.zeri_dao import ZeriDAO
    z = ZeriDAO(str(tmp_path / "z.db"))
    z.save_plan("u1", "结婚", "2026-09-01", {"date":"2026-09-01"}, [{"item":"登记"}])
    rq.zeri_dao = z
    assert rq.direct_query("u1", "我选的吉日") is not None
```

（qian_dao/lamp_dao/zeri_dao/ming_dao 的实际方法名以探索事实 §1.2 为准：qian_dao 有 save；lamp_dao 表 user_id+date；zeri_plans 由 ZeriDAO 管理——实施时核对方法签名，测试按实际签名微调但语义不变。）

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_record_query.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 RecordQuery**

```python
# src/bot/record_query.py
"""存量数据直读：对话问'我的档案/解梦/历史/收藏…' → 直读秒回，不重走全流程。"""
import json, logging
from src.storage.dao import _decrypt_or_plain

logger = logging.getLogger(__name__)

# 类别 → 触发关键词（命中即直读）
CATEGORY_KEYWORDS = {
    "档案": ["档案", "生辰", "出生信息", "我的八字信息", "什么时辰"],
    "排盘": ["排过", "排的盘", "我的盘", "上次的盘"],
    "解梦": ["解过什么梦", "以前.*梦", "梦的解读", "上次那个梦"],
    "历史": ["之前聊过", "以前说过", "历史对话", "上次聊", "之前说过什么"],
    "灵签": ["摇过什么签", "抽过什么签", "我的签"],
    "名笺": ["取过什么名", "起过什么名", "我的名字"],
    "灯语": ["灯语", "昨晚的灯"],
    "择吉": ["选的日子", "吉日", "择吉计划", "办事清单"],
    "晨笺": ["晨笺", "今早的", "早上的运势"],
    "收藏": ["收藏过", "我收藏的"],
    "会员": ["会员", "我花了多少", "充值", "额度"],
}

class RecordQuery:
    def __init__(self, dao, person_dao, session_dao, chart_dao,
                 qian_dao=None, ming_dao=None, lamp_dao=None,
                 zeri_dao=None, jian_dao=None, member_dao=None):
        self.dao, self.person_dao = dao, person_dao
        self.session_dao, self.chart_dao = session_dao, chart_dao
        self.qian_dao = qian_dao or (lambda: None)
        # ... 其余懒加载（__getattr__ 或 None 判断，避免构造时依赖）

    def direct_query(self, user_id: str, msg: str) -> str | None:
        if not msg:
            return None
        import re
        for cat, kws in CATEGORY_KEYWORDS.items():
            for kw in kws:
                if re.search(kw, msg):
                    handler = getattr(self, f"_q_{cat}", None)
                    if handler:
                        out = handler(user_id)
                        if out:
                            return out
        return None

    # —— 各类直读（解密仅服务端内存）——
    def _q_档案(self, user_id):
        p = self.person_dao.get_default_person(user_id)
        if not p:
            return "还没有档案，告诉我出生年月日时我帮你建档。"
        return (f"你的档案（命主：{p.get('name')}）："
                f"{p.get('birth_year')}年{p.get('birth_month')}月{p.get('birth_day')}日"
                f"{p.get('birth_hour')}时 · 出生地{p.get('city') or '未填'} · {p.get('gender') or '性别未知'}")

    def _q_排盘(self, user_id):
        c = self.chart_dao.get_latest_chart(user_id)
        if not c:
            return None
        bazi = c["bazi_json"].get("bazi") or []
        return "最近排过的盘：" + " ".join(bazi) + \
               f"（{c['created_at']}，日主 {c['bazi_json'].get('day_master','')}）"

    def _q_解梦(self, user_id):
        rows = self.dao.get_user_consultations(user_id, intent="dream", limit=5)
        if not rows:
            return None
        parts = [f"解过 {len(rows)} 次梦："]
        for r in rows:
            parts.append(f"· {_decrypt_or_plain(r.get('question',''))[:30]} → "
                         f"{_decrypt_or_plain(r.get('analysis',''))[:50]}")
        return "\n".join(parts)

    def _q_历史(self, user_id):
        s = self.session_dao.get_summary(user_id)
        if not s or not s.get("summary"):
            return None
        mem = ""
        if s.get("memories"):
            mem = "，记得：" + "；".join(str(m) for m in s["memories"][:5])
        return f"之前的聊天摘要：{s['summary'][:200]}{mem}"

    def _q_灵签(self, user_id):
        if not self.qian_dao: return None
        saves = self.qian_dao.list_saves(user_id)  # 实际方法名按 qian_dao 核对
        return f"收藏的签：{len(saves)} 支（签号 {[s['no'] for s in saves[:10]]}）" if saves else None

    def _q_名笺(self, user_id): ...   # ming_dao.list_saves → 姓名/分数/风格
    def _q_灯语(self, user_id):
        if not self.lamp_dao: return None
        l = self.lamp_dao.get_latest(user_id)  # 实际方法名按 lamp_dao 核对
        return f"最近一条灯语（{l['date']}）：{l['text']}" if l else None
    def _q_择吉(self, user_id):
        if not self.zeri_dao: return None
        plans = self.zeri_dao.list_plans(user_id, limit=3)
        return "\n".join(f"· {p['scene']} → {p['lucky_date']}" for p in plans) if plans else None
    def _q_晨笺(self, user_id):
        if not self.jian_dao: return None
        card = self.jian_dao.get_card(user_id)  # Task 8 实现
        return f"今早的晨笺：{card['card_json']['day_ganzhi']} 宜{'/'.join(card['card_json'].get('suitable',[]))}" if card else None
    def _q_收藏(self, user_id):
        if not hasattr(self, "fav_dao") or not self.fav_dao: return None
        favs = self.fav_dao.list_favorites(user_id, limit=10)  # Task 9 实现
        return "\n".join(f"· {f['type']}: {f['summary'][:40]}" for f in favs) if favs else None
    def _q_会员(self, user_id):
        m = self.dao.get_membership(user_id)  # 实际方法名核对 member_dao/dao
        if not m: return None
        return f"会员档位：{m.get('plan')}，额度 {m.get('queries_used')}/{m.get('queries_limit') or '不限'}"
```

（各 DAO 实际方法名在实施时对照代码核对，语义不变。`get_user_consultations` 若不支持 intent 过滤，实施时在 dao.py 扩展该方法加 intent 参数——向后兼容，默认不过滤。）

- [ ] **Step 4: 接入 handler 主流程**

`handler.py` `process()` 中、`_route_by_scenario` 调用之前插入：

```python
    # 存量数据直读：问档案/解梦/历史等 → 秒回，不重走全流程
    if hasattr(self, "record_query"):
        direct = self.record_query.direct_query(user_id, msg)
        if direct:
            return direct
```

`__init__` 装配 `self.record_query = RecordQuery(self.dao, self.person_dao, self.session_dao, self.chart_dao, ...)`（各轻量 DAO 按 handler 已有引用注入；没有的懒加载）。

- [ ] **Step 5: 运行确认通过**

Run: `python -m pytest tests/test_record_query.py -v` → PASS；`python -m pytest tests/test_bot.py -v` 无回归

- [ ] **Step 6: Commit**

```bash
git add src/bot/record_query.py src/bot/handler.py tests/test_record_query.py
git commit -m "feat(chat): 存量数据全类型直读（档案/解梦/历史/签/名笺/灯语/择吉/会员）"
```

---

### Task 7: query_records 工具（LLM 可调用）

**Files:**
- Modify: `src/bot/tool_calls.py:80-112`（TOOL_REGISTRY）、`src/bot/handler.py:1196-1212`（`_execute_tool_call` 分发）
- Test: `tests/test_tool_records.py`（新建）

**Interfaces:**
- Consumes: `RecordQuery.direct_query`（Task 6）
- Produces: TOOL_REGISTRY 新条目 `查记录{key:"records"}`；`_execute_tool_call` 支持 name=="records" → `_tool_query_records`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_tool_records.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from src.bot.tool_calls import parse_tool_calls, TOOL_REGISTRY

def test_registry_has_records():
    assert any(t.get("key") == "records" for t in TOOL_REGISTRY)

def test_parse_records_call():
    calls = parse_tool_calls("<tool_call>查记录: 我的档案</tool_call>")
    assert calls and calls[0].name == "查记录"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_tool_records.py -v`
Expected: FAIL

- [ ] **Step 3: 注册工具**

TOOL_REGISTRY 追加：

```python
{"key": "records", "name": "查记录", "desc": "查用户自己的存量数据（档案/解梦/历史对话/签/名笺/灯语/择吉/晨笺/收藏/会员）。输入想查的内容描述，如'我的档案''以前解过什么梦'", "requires": "用户本人数据"},
```

`_execute_tool_call` 分发分支：

```python
if name in ("查记录", "records"):
    return self._tool_query_records(params, user_id)
```

`_tool_query_records`：

```python
def _tool_query_records(self, params: str, user_id: str):
    """存量数据工具：RecordQuery 直读，无记录返回'未查到'（区别于'没查'）。"""
    try:
        out = self.record_query.direct_query(user_id, params)
        if not out:
            return ToolResult("records", False, "未查到相关记录，可建议用户先建档/使用功能")
        return ToolResult("records", True, out)
    except Exception as e:
        logger.warning("查记录工具失败 %s", e)
        return ToolResult("records", False, "查询失败，稍后再试")
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_tool_records.py -v` → PASS；`python -m pytest tests/test_bot.py -v` 无回归

- [ ] **Step 5: Commit**

```bash
git add src/bot/tool_calls.py src/bot/handler.py tests/test_tool_records.py
git commit -m "feat(chat): 查记录工具（LLM 对话中可主动查用户存量数据）"
```

---

### Task 8: 晨笺内容落库（缺口①）

**Files:**
- Modify: `src/storage/jian_dao.py`（新表 jian_cards + 方法）、`src/main.py`（`_send_jian_batch` 发送时落库）
- Test: `tests/test_jian_cards.py`（新建）

**Interfaces:**
- Produces: `JianPrefDAO.save_card(user_id, date, card_dict)` / `get_card(user_id, date=None) -> Optional[dict]`（card_json 密文落库；get 最近一张）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_jian_cards.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from src.storage.jian_dao import JianPrefDAO

def test_card_save_and_get(tmp_path):
    dao = JianPrefDAO(str(tmp_path / "j.db"))
    card = {"date":"2026-08-23","day_ganzhi":"甲子",
            "suitable":["出行","洽谈"],"unsuitable":["借贷"],
            "quote":"金句","book":"古籍","private_line":"今日宜静心"}
    dao.save_card("u1", "2026-08-23", card)
    r = dao.get_card("u1")
    assert r and r["card_json"]["day_ganzhi"] == "甲子"
    assert "宜静心" in r["card_json"]["private_line"]
    # 密文
    import sqlite3
    row = sqlite3.connect(str(tmp_path / "j.db")).execute(
        "SELECT card_enc FROM jian_cards").fetchone()
    assert ":" in row[0] and "甲子" not in row[0]
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_jian_cards.py -v`
Expected: FAIL（jian_cards 表不存在）

- [ ] **Step 3: 实现**

jian_dao.py 建表追加：

```sql
CREATE TABLE IF NOT EXISTS jian_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    date TEXT NOT NULL,
    card_enc TEXT NOT NULL,      -- AES: {date, day_ganzhi, suitable, unsuitable, quote, book, private_line}
    created_at REAL,
    UNIQUE(user_id, date)
);
```

方法（照 JianPrefDAO 模式）：

```python
def save_card(self, user_id, date, card: dict):
    conn = get_conn(self.db_path)
    try:
        conn.execute(
            "INSERT INTO jian_cards (user_id, date, card_enc, created_at) "
            "VALUES (?,?,?,?) ON CONFLICT(user_id, date) DO UPDATE SET card_enc=?",
            (user_id, date, _encrypt_text(json.dumps(card, ensure_ascii=False)),
             time.time(), _encrypt_text(json.dumps(card, ensure_ascii=False))))
        conn.commit()
    finally:
        conn.close()

def get_card(self, user_id, date=None):
    conn = get_conn(self.db_path)
    try:
        if date:
            row = conn.execute("SELECT card_enc, date FROM jian_cards "
                               "WHERE user_id=? AND date=?", (user_id, date)).fetchone()
        else:
            row = conn.execute("SELECT card_enc, date FROM jian_cards "
                               "WHERE user_id=? ORDER BY date DESC LIMIT 1",
                               (user_id,)).fetchone()
        if not row:
            return None
        return {"date": row[1], "card_json": json.loads(_decrypt_or_plain(row[0]) or "{}")}
    finally:
        conn.close()
```

main.py `_send_jian_batch`（L330-398）用户维度消息组装后追加：

```python
    try:
        dao.jian_prefs.save_card(user_id, today_str, {
            "date": today_str, "day_ganzhi": thing1, "suitable": suitable,
            "unsuitable": unsuitable, "quote": thing3, "book": book,
            "private_line": thing4})
    except Exception as e:
        logger.warning("晨笺落库失败 %s", e)
```

（dao.jian_prefs 的访问路径按 main.py 现有装配核对；`today_str`/thing 变量取自发送上下文。）

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_jian_cards.py -v` → PASS
手工回归：`python -m pytest tests/test_bot.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/storage/jian_dao.py src/main.py tests/test_jian_cards.py
git commit -m "feat(data): 晨笺内容落库 jian_cards（缺口①补上，对话可直读'我的晨笺'）"
```

---

### Task 9: 收藏后端化（缺口②）

**Files:**
- Create: `src/storage/favorite_dao.py`、`src/api/favorites.py`
- Modify: `src/api/router`（注册路由）、`miniprogram/pages/favorites/favorites.js`、`miniprogram/pages/history/history.js`、`miniprogram/utils/api.js`
- Test: `tests/test_favorites_api.py`（新建）

**Interfaces:**
- Produces: `FavoriteDAO(db_path)`：`add(user_id, type, ref_id, summary) -> int`（UNIQUE(user_id,type,ref_id) 幂等）/ `remove(user_id, type, ref_id) -> None` / `list_favorites(user_id, limit=50) -> List[dict]` / `has(user_id, type, ref_id) -> bool`；API：`POST /api/favorites`（add）、`DELETE /api/favorites?type=&ref_id=`、`GET /api/favorites`、`POST /api/favorites/import`（本地存量导入，幂等标记）

- [ ] **Step 1: 写失败测试（DAO + API）**

```python
# tests/test_favorites_api.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from src.storage.favorite_dao import FavoriteDAO

def test_favorite_crud(tmp_path):
    dao = FavoriteDAO(str(tmp_path / "f.db"))
    dao.add("u1", "chat", "msg-1", "收藏的回复摘要")
    dao.add("u1", "chat", "msg-1", "重复添加幂等")
    assert len(dao.list_favorites("u1")) == 1
    assert dao.has("u1", "chat", "msg-1")
    dao.remove("u1", "chat", "msg-1")
    assert not dao.has("u1", "chat", "msg-1")
    dao.add("u2", "chat", "msg-9", "别家")
    assert len(dao.list_favorites("u1")) == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_favorites_api.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 DAO + API**

favorite_dao.py 表：

```sql
CREATE TABLE IF NOT EXISTS favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    type TEXT NOT NULL,        -- chat/jian/qian/ming/lamp
    ref_id TEXT NOT NULL,
    summary TEXT DEFAULT '',
    imported INTEGER DEFAULT 0,  -- 1=本地导入批次（防重复导入）
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, type, ref_id)
);
```

API（照 api/user.py 模式：`require_user` 鉴权，归属强制 user_id=当前用户，`type` 白名单 chat/jian/qian/ming/lamp 校验，summary 截 100 字）。导入端点：body `{items: [{type, ref_id, summary}...]}`，逐条 add（UNIQUE 幂等天然防重），返回导入条数。

- [ ] **Step 4: 前端改造（miniprogram）**

- `favorites.js`：收藏/取消收藏从 wx storage（kept=true 标记）改为调 `POST/DELETE /api/favorites`；列表页改拉 `GET /api/favorites`；启动时检测本地 `ylm_chat_messages` 中 kept 条目未导入 → 调 `POST /api/favorites/import` 一次（imported 标记 + 本地清标记）
- `history.js`：收藏按钮走后端
- `utils/api.js`：新增 favorites 系列方法

（前端为代码推断改造，标注"需真机复核"。）

- [ ] **Step 5: 运行确认通过**

Run: `python -m pytest tests/test_favorites_api.py -v` → PASS；`python -m pytest tests/test_bot.py -v` 无回归

- [ ] **Step 6: Commit**

```bash
git add src/storage/favorite_dao.py src/api/favorites.py src/api/router.py miniprogram/pages/favorites/favorites.js miniprogram/pages/history/history.js miniprogram/utils/api.js tests/test_favorites_api.py
git commit -m "feat(fav): 收藏后端化（表+API+前端迁移+本地导入，缺口②补上）"
```

---

### Task 10: 问事快路径（跳过预检索 + 画像注入）

**Files:**
- Modify: `src/bot/handler.py:3025`（`_do_bazi_analysis` 的 retriever.search）、prompt 组装处（L3036-3079 附近）
- Test: `tests/test_fastpath.py`（新建）

**Interfaces:**
- Consumes: `ChartDAO.get_latest_chart`（Task 3）、`session_dao.get_summary`
- Produces: 行为契约——已建档/有已存结果时主分析路径**不自动调 retriever.search**（LLM 需要时经 tool_loop 的 search 工具兜底）；prompt 注入最近 chart_records 摘要 + 画像摘要

- [ ] **Step 1: 写失败测试**

```python
# tests/test_fastpath.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from unittest.mock import Mock

def test_analysis_skips_presearch_when_archive_exists():
    """有档案+已存盘 → 主分析不再自动 RAG 预检索（LLM 需要时 tool_loop 兜底）"""
    from src.bot.handler import MessageHandler
    h = object.__new__(MessageHandler)
    h.engine = Mock()
    h.llm = Mock()
    h.llm.analyze.return_value = Mock(response="分析")
    h.dao = Mock()
    h.dao.db_path = ":memory:"
    h.dao.get_user_bazi.return_value = {"year":1990,"bazi":["庚午","辛巳","甲申","壬申"]}
    h.retriever = Mock()
    h.retriever.search.return_value = []
    h.memory_system = None
    h._analysis_facts = {}
    h.tool_logs = {}  # 若 process 路径使用
    # 直接测 _do_bazi_analysis 的检索调用条件函数
    fast = h._should_fastpath("u1", {"year":1990,"month":5,"day":20,"hour":15,
                                     "minute":0,"city":"北京","gender":"男"})
    assert fast is True
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_fastpath.py -v`
Expected: FAIL（_should_fastpath 不存在）

- [ ] **Step 3: 实现**

handler.py 新增：

```python
def _should_fastpath(self, user_id: str, birth: dict) -> bool:
    """快路径门控：有已存排盘结果（同生辰）或档案时跳过 RAG 预检索。"""
    try:
        chart = self.chart_dao.get_latest_chart(user_id)
        if chart and chart.get("birth"):
            b = chart["birth"]
            if (b.get("year") == birth.get("year") and b.get("month") == birth.get("month")
                    and b.get("day") == birth.get("day")):
                return True
        if self.dao.get_user_bazi(user_id):
            return True
    except Exception:
        pass
    return False
```

`_do_bazi_analysis`（L3025 附近）：

```python
    # 快路径：已有档案/已存盘 → 跳过 RAG 预检索（LLM 需要时经 tool_loop 查古籍）
    if self._should_fastpath(user_id, {"year": year, "month": month, "day": day,
                                       "hour": hour, "minute": minute,
                                       "city": city, "gender": gender}):
        refs = []
        logger.info("fastpath: 用户 %s 有存量数据，跳过预检索", user_id)
    else:
        refs = self.retriever.search(f"{result.day_master} {question}",
                                     category="bazi", top_k=15)
```

prompt 注入（L3073 llm.analyze 的 extra_system_prompt 组装处追加）：

```python
    _chart = self.chart_dao.get_latest_chart(user_id) if hasattr(self, "chart_dao") else None
    if _chart:
        _b = _chart["bazi_json"]
        extra = (extra + f"\n【用户已存排盘结果】{_b.get('day_master','')}日主，"
                          f"格局{_b.get('geju','')}，用神{_b.get('yongshen','')}。"
                          f"回答须与此一致，不矛盾。") if extra else (
                          f"【用户已存排盘结果】{_b.get('day_master','')}日主，"
                          f"格局{_b.get('geju','')}，用神{_b.get('yongshen','')}。"
                          f"回答须与此一致，不矛盾。")
```

（extra_system_prompt 变量名以 L3073 实际为准，语义不变。）

- [ ] **Step 4: 运行确认通过 + 回归**

Run: `python -m pytest tests/test_fastpath.py -v` → PASS
回归：`python -m pytest tests/test_bot.py tests/test_handler_analysis_flow.py -v`（确认主链路、无档案路径仍有检索）

- [ ] **Step 5: Commit**

```bash
git add src/bot/handler.py tests/test_fastpath.py
git commit -m "perf(chat): 问事快路径——已建档跳过 RAG 预检索 + 已存结果注入防矛盾"
```

---

### Task 11: 注销清理覆盖新表 + QA 用例库回补

**Files:**
- Modify: `src/storage/dao.py:291-292`（cleanup_cancelled_accounts 表元组）、`docs/superpowers/qa/acceptance-cases.md`
- Test: `tests/test_cleanup_new_tables.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_cleanup_new_tables.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from src.storage.dao import UserDAO
from src.storage.chart_dao import ChartDAO
from src.storage.favorite_dao import FavoriteDAO
from src.storage.jian_dao import JianPrefDAO

def test_cleanup_covers_new_tables(tmp_path):
    db = str(tmp_path / "t.db")
    dao = UserDAO(db)
    ChartDAO(db).save_chart("u9", 1, {"year":1990,"month":5,"day":20,"hour":15,
                                      "minute":0,"city":"北京","gender":"男"},
                            {"bazi":["a","b","c","d"]})
    FavoriteDAO(db).add("u9", "chat", "m1", "s")
    JianPrefDAO(db).save_card("u9", "2026-08-23", {"day_ganzhi":"甲子"})
    dao.cancel_user("u9", "2026-05-01 00:00:00")  # 90 天前 → 可清理
    stats = dao.cleanup_cancelled_accounts(retention_days=90)
    # 断言新表数据随注销清除
    assert ChartDAO(db).get_latest_chart("u9") is None
    assert len(FavoriteDAO(db).list_favorites("u9")) == 0
    assert JianPrefDAO(db).get_card("u9") is None
```

（cancel_user 的实际签名与日期参数按 dao.py L242-268 核对。）

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_cleanup_new_tables.py -v`
Expected: FAIL（新表未清理）

- [ ] **Step 3: 修改清理元组**

`src/storage/dao.py:291-292` 表元组追加 `"chart_records", "favorites", "jian_cards"`（原元组逐字保留）：

```python
("consultations", "sessions", "session_summaries", "memberships",
 "payments", "push_log", "persons", "chart_records", "favorites", "jian_cards")
```

- [ ] **Step 4: 用例库回补**

`docs/superpowers/qa/acceptance-cases.md` 域 2/域 4 追加：

```markdown
| AC-CONS-006 | 重看盘 0 重跑 | 对话排盘后问"我的盘/我的八字" | 秒回已存结果，四柱与首次一致，无重新计算提示 |
| AC-CONS-007 | 存量直读逐类 | 问档案/解梦/历史对话/签/名笺/灯语/择吉/晨笺/收藏/会员各一次 | 每类直接给出已存数据，不触发重新生成 |
| AC-CONS-008 | 页面档案同源 | 我的页档案 vs 对话问"我的档案" vs 今日页 | 出生信息三处完全一致 |
| AC-CHAT-009 | 快路径 | 已建档用户问"今年财运" | 首字时间较无档案用户明显缩短，回答与已存盘一致 |
| AC-CHAT-010 | 晨笺直读 | 订阅晨笺收到后问"我的晨笺" | 可读当日晨笺内容（干支/宜忌/私语） |
| AC-CHAT-011 | 收藏后端化 | 收藏对话→退出重登→问"我收藏了什么" | 收藏仍在，可列出（本地已导入） |
```

- [ ] **Step 5: 运行确认通过 + Commit**

Run: `python -m pytest tests/test_cleanup_new_tables.py -v` → PASS
Commit:

```bash
git add src/storage/dao.py docs/superpowers/qa/acceptance-cases.md tests/test_cleanup_new_tables.py
git commit -m "feat(data): 注销清理覆盖新表 + QA 用例库回补（直读/快路径/晨笺/收藏）"
```

---

### Task 12: 部署运行副本 + qa-tester 独立验收

**Files:**
- 部署：同步 /mnt/e/fortune-agent-deploy → /home/a/fortune-run（**禁 rsync --delete**；新表由运行副本重启时 init_db/各 DAO executescript 自动建，需确认各新 DAO 在启动链路中被实例化——否则在运行副本手工执行建表 SQL）
- Test: `tests/` 全量回归 + qa-tester 黑盒验收

- [ ] **Step 1: 全量回归**

Run: `cd /mnt/e/fortune-agent-deploy && python -m pytest tests/ -v`（或 `-q` 汇总）
Expected: 全绿（含既有 51 文件 + 新增）

- [ ] **Step 2: 同步运行副本并重启**

```bash
rsync -a --exclude .venv --exclude data /mnt/e/fortune-agent-deploy/ /home/a/fortune-run/   # 禁 --delete
# 运行副本补建新表（若启动链路未覆盖）：sqlite3 或重启后验证
# 重启服务（约 11 分钟慢启动属正常），curl 健康检查 200
```

- [ ] **Step 3: qa-tester 独立验收**

派 qa-tester 角色（黑盒，不读实现）：按用例库 AC-CONS-006~008、AC-CHAT-009~011 + 回归抽样，对 8767 实例实测，输出通过率与缺陷清单（Critical/Important/Minor）。

- [ ] **Step 4: 缺陷闭环 + 汇报**

Critical/Important 缺陷 → 修复子代理 → 复测原用例 → 通过才算关。向 PM 汇报验收报告，等待真机复核。

- [ ] **Step 5: Commit 收尾**

```bash
git add -A && git commit -m "chore: 数据复用体系合入收尾（部署+验收）"
```
