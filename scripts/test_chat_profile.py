#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
排盘档案打通（chat-ux Task 1）验证

背景：前端「档案」填的生日/时间/性别/城市只存 persons 表，对话排盘此前只查
users.bazi_info（多数用户是登录时创建的 {} 空记录）→ AI 不知道出生信息只能反问。
本任务单向打通：persons 表 → 排盘工具/档案复用/画像注入。

Hermetic（无网络，dao/person_dao 全部 mock）：

A. _get_user_birth_profile（新 helper）：
  A1  bazi_info 空 + persons 有档案 → 映射为 {year,month,day,hour,minute,city,gender}
  A2  bazi_info 有数据（含 year 键）→ 优先返回 bazi_info（persons 存在也不覆盖）
  A2b bazi_info 仅含 year（缺其他键）→ 原样返回不崩（.get 默认生效）
  A3  persons 档案缺键（如无 minute）→ 不崩，.get 默认（None）
  A4  bazi_info 与 persons 都无 → None
  A5  默认档案无出生数据 + 其他档案有 → 选 updated_at 最新的（而非最早创建的）
  A6  默认档案有出生数据 → 保持默认优先（即使其他档案 updated_at 更新）

B. _tool_bazi 工具路径档案回退：
  B1  params 缺生辰 + 档案有 → 直接排盘不询问（非 needs_info，引擎收到档案参数），
      回退填参后 save_user_bazi 收到 year/month/day/hour/minute
  B1b 档案无时辰 → 填参 hour/minute 缺省 0，save_user_bazi 同步 0
  B2  params 缺生辰 + 档案也无 → 仍 needs_info 询问
  B3  params 自带生辰 → 原路径照旧（不查档案）

C. _handle_bazi 档案复用路径：
  C1  档案缺键（无 minute）→ 不再 KeyError，复用成功（缺省 hour/minute=0）
  C2  档案年/月/日不齐 → 不崩，走信息收集（不调 _do_bazi_analysis）
  C3  消息自带生辰 → 原路径照旧

D. 画像注入出生字段：
  D1  get_profile_summary 有原始出生字段 → 补「出生:1990年8月20日 辰时 北京 男(来自用户档案)」
  D2  get_profile_summary 无 bazi → 无出生行，不崩
  D3  时辰缺失 → 出生行不含时辰段，其余字段照写
  D4  handler _collect_key_facts：persons 兜底 → 关键事实含出生行
  D5  _collect_key_facts 无档案 → 无出生行，不崩

用法：
  .venv/bin/python3 scripts/test_chat_profile.py
退出码：0=全部通过；1=有失败
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from src.bot.handler import MessageHandler
from src.memory.user_memory import UserMemory
try:  # Task 1 新增导出（TDD：实现前缺失，逐个断言 RED 而非整体 ImportError）
    from src.memory.user_memory import format_birth_line
except ImportError:
    format_birth_line = None
from src.bot.tool_calls import ToolResult

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = ""):
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail[:300]}" if detail and not cond else ""))
    (PASS if cond else FAIL).append(name)


class _Missing(Exception):
    """TDD 哨兵：新 API 未实现时调用失败的标记（断言仍判失败，不吞真异常）。"""

    def get(self, key, default=None):
        return default


def call(obj, name: str, *a, **kw):
    """按名调用被测方法；未实现（AttributeError）时返回哨兵 → 断言失败并显示原因。"""
    try:
        return getattr(obj, name)(*a, **kw)
    except Exception as e:
        return _Missing(repr(e))


# ---------------------------------------------------------------------------
# Mock 装配：FakeDAO / FakePersonDAO（monkeypatch src.storage.person_dao）/ FakeEngine
# ---------------------------------------------------------------------------

class FakeDAO:
    """mock UserDAO：只提供排盘路径用到的接口。"""

    def __init__(self, db_path: str = ""):
        self.db_path = db_path
        self.bazi_info = None  # 模拟 users.bazi_info 解密后的 dict
        self.saved_bazi = []   # 记录 save_user_bazi 调用
        self.consultations = []

    def get_user_bazi(self, user_id: str) -> Optional[dict]:
        return self.bazi_info

    def save_user_bazi(self, user_id: str, bazi_info: dict):
        self.saved_bazi.append(bazi_info)

    def save_consultation(self, user_id: str, question: str, result, intent: str = "bazi"):
        self.consultations.append((user_id, question))


class FakePersonDAO:
    """mock PersonDAO：list_persons 返回配置的命主列表；写路径 no-op。"""

    persons: list = []

    def __init__(self, db_path: str = ""):
        pass

    def list_persons(self, user_id: str) -> list:
        return list(self.persons)

    def count_persons(self, user_id: str) -> int:
        return len(self.persons)

    def create_person(self, *a, **kw):
        return None

    def update_person(self, *a, **kw):
        return None

    def get_default_person(self, *a, **kw):
        return None

    def find_person_by_birth(self, *a, **kw):
        return None


class FakeResult:
    """mock BaziResult（排盘工具输出）。"""

    def __init__(self):
        self.bazi = ["庚午", "辛巳", "乙酉", "壬午"]
        self.day_master = "乙"
        self.geju = "正官格"
        self.yongshen = "水"


class FakeEngine:
    """mock BaziEngine.calculate：记录参数，返回 FakeResult。"""

    def __init__(self):
        self.calls = []

    def calculate(self, year, month, day, hour, minute, city, gender):
        self.calls.append((year, month, day, hour, minute, city, gender))
        return FakeResult()


_TMP = tempfile.mkdtemp(prefix="chat_profile_")
_MEM_DIR = os.path.join(_TMP, "memory")
os.makedirs(_MEM_DIR, exist_ok=True)

_PERSONA = {  # persons 表行 → _row_to_person 风格 dict
    "id": 1,
    "user_id": "u1",
    "name": "我",
    "relation": "自己",
    "is_default": True,
    "gender": "男",
    "birth_year": 1990,
    "birth_month": 8,
    "birth_day": 20,
    "birth_hour": 7,
    "birth_minute": 30,
    "calendar": "solar",
    "city": "北京",
    "created_at": "2026-01-01T00:00:00",
    "updated_at": "2026-01-02T00:00:00",
}


def build_handler(dao: FakeDAO) -> MessageHandler:
    """构造 MessageHandler：mock dao + FakeEngine + 独立 UserMemory（临时目录）。"""
    import src.storage.person_dao as person_dao_mod
    person_dao_mod.PersonDAO = FakePersonDAO
    handler = MessageHandler(
        None, None, None, None, None, None, None, None, dao,
        dream_engine=None, hehun_engine=None, qimen_engine=None,
        xingming_engine=None, session_dao=None, member_dao=None,
    )
    handler.engine = FakeEngine()
    handler.memory_system = UserMemory(base_dir=_MEM_DIR)
    return handler


# ---------------------------------------------------------------------------
# A. _get_user_birth_profile
# ---------------------------------------------------------------------------
print("— A. _get_user_birth_profile（bazi_info / persons 优先级与容错）")

# A1: bazi_info 空 + persons 有档案 → 映射字段
dao = FakeDAO()
dao.bazi_info = None
FakePersonDAO.persons = [dict(_PERSONA)]
h = build_handler(dao)
p = call(h, "_get_user_birth_profile", "u1")
check("A1 persons 兜底 → 映射出生字段",
      p is not None and p.get("year") == 1990 and p.get("month") == 8
      and p.get("day") == 20 and p.get("hour") == 7 and p.get("minute") == 30
      and p.get("city") == "北京" and p.get("gender") == "男",
      f"profile={p}")

# A2: bazi_info 有数据（含 year 键）→ 优先 bazi_info
dao2 = FakeDAO()
dao2.bazi_info = {"year": 1988, "month": 3, "day": 15, "hour": 11,
                  "minute": 0, "city": "上海", "gender": "女", "bazi": ["甲子"]}
FakePersonDAO.persons = [dict(_PERSONA)]
h2 = build_handler(dao2)
p2 = call(h2, "_get_user_birth_profile", "u1")
check("A2 bazi_info 优先（persons 不覆盖）",
      p2 is not None and p2.get("year") == 1988 and p2.get("city") == "上海"
      and p2.get("gender") == "女",
      f"profile={p2}")

# A2b: bazi_info 仅含 year（缺其他键）→ 原样返回不崩（.get 默认生效）
dao2b = FakeDAO()
dao2b.bazi_info = {"year": 1988}
FakePersonDAO.persons = [dict(_PERSONA)]
h2b = build_handler(dao2b)
p2b = call(h2b, "_get_user_birth_profile", "u1")
check("A2b bazi_info 仅含 year → 原样返回不崩",
      p2b is not None and p2b == dao2b.bazi_info,
      f"profile={p2b}")

# A3: persons 档案缺键（无 minute）→ 不崩，.get 默认 None
person_short = dict(_PERSONA)
person_short.pop("birth_minute", None)
FakePersonDAO.persons = [person_short]
h3 = build_handler(FakeDAO())
p3 = call(h3, "_get_user_birth_profile", "u1")
check("A3 档案缺键（无 minute）→ 不崩，minute=None",
      p3 is not None and p3.get("minute") is None and p3.get("year") == 1990,
      f"profile={p3}")

# A4: bazi_info 与 persons 都无 → None
dao4 = FakeDAO()
dao4.bazi_info = {}  # 登录时创建的空记录
FakePersonDAO.persons = []
h4 = build_handler(dao4)
check("A4 都无 → None", call(h4, "_get_user_birth_profile", "u1") is None)

# A5: 默认档案无出生数据 + 其他档案有 → 选 updated_at 最新的
#    （list_persons 按 created_at ASC 排序，旧实现会选最早创建的）
dao5a = FakeDAO()
dao5a.bazi_info = None
default_no_birth = dict(_PERSONA, id=1, name="我", relation="自己", is_default=True)
for _k in ("birth_year", "birth_month", "birth_day"):
    default_no_birth.pop(_k, None)
older = dict(_PERSONA, id=2, name="妈妈", relation="母亲", is_default=False,
             birth_year=1965, updated_at="2026-02-01T00:00:00")
newer = dict(_PERSONA, id=3, name="爸爸", relation="父亲", is_default=False,
             birth_year=1963, updated_at="2026-03-01T00:00:00")
FakePersonDAO.persons = [default_no_birth, older, newer]
h5a = build_handler(dao5a)
p5a = call(h5a, "_get_user_birth_profile", "u1")
check("A5 默认无出生数据 → 选 updated_at 最新的档案",
      p5a is not None and p5a.get("year") == 1963,
      f"profile={p5a}")

# A6: 默认档案有出生数据 → 保持默认优先（即使其他档案 updated_at 更新）
dao6a = FakeDAO()
dao6a.bazi_info = None
default_self = dict(_PERSONA, id=1, name="我", relation="自己", is_default=True,
                    birth_year=1990, updated_at="2026-01-02T00:00:00")
newer_other = dict(_PERSONA, id=2, name="妈妈", relation="母亲", is_default=False,
                   birth_year=1965, updated_at="2026-05-01T00:00:00")
FakePersonDAO.persons = [default_self, newer_other]
h6a = build_handler(dao6a)
p6a = call(h6a, "_get_user_birth_profile", "u1")
check("A6 默认有出生数据 → 仍选默认档案（优先级不变）",
      p6a is not None and p6a.get("year") == 1990,
      f"profile={p6a}")

# ---------------------------------------------------------------------------
# B. _tool_bazi 工具路径档案回退
# ---------------------------------------------------------------------------
print("— B. _tool_bazi：档案回退 / needs_info")

# B1: params 缺生辰 + 档案有 → 直接排盘不询问
dao5 = FakeDAO()
dao5.bazi_info = None
FakePersonDAO.persons = [dict(_PERSONA)]
h5 = build_handler(dao5)
r5 = call(h5, "_tool_bazi", '{"出生信息": "帮我看看"}', "u1")
check("B1 档案有 → 直接排盘（非 needs_info）",
      r5.ok and not r5.needs_info, f"ok={r5.ok} needs_info={r5.needs_info}")
check("B1 引擎收到档案参数",
      len(h5.engine.calls) == 1 and h5.engine.calls[0][:5] == (1990, 8, 20, 7, 30)
      and h5.engine.calls[0][5] == "北京" and h5.engine.calls[0][6] == "男",
      f"calls={h5.engine.calls}")
check("B1 回退填参后 save_user_bazi 收到档案字段",
      len(dao5.saved_bazi) == 1 and dao5.saved_bazi[0]["year"] == 1990
      and dao5.saved_bazi[0]["month"] == 8 and dao5.saved_bazi[0]["day"] == 20
      and dao5.saved_bazi[0]["hour"] == 7 and dao5.saved_bazi[0]["minute"] == 30,
      f"saved_bazi={dao5.saved_bazi}")

# B1b: 档案无时辰 → 填参 hour/minute 缺省 0，save_user_bazi 同步
dao5b = FakeDAO()
dao5b.bazi_info = None
person_no_time = dict(_PERSONA)
person_no_time.pop("birth_hour", None)
person_no_time.pop("birth_minute", None)
FakePersonDAO.persons = [person_no_time]
h5b = build_handler(dao5b)
r5b = call(h5b, "_tool_bazi", '{"出生信息": "帮我看看"}', "u1")
check("B1b 档案无时辰 → 填参 hour/minute=0",
      r5b.ok and not r5b.needs_info and h5b.engine.calls
      and h5b.engine.calls[0][:5] == (1990, 8, 20, 0, 0),
      f"calls={h5b.engine.calls}")
check("B1b save_user_bazi 收到 hour/minute=0",
      len(dao5b.saved_bazi) == 1 and dao5b.saved_bazi[0]["year"] == 1990
      and dao5b.saved_bazi[0]["month"] == 8 and dao5b.saved_bazi[0]["day"] == 20
      and dao5b.saved_bazi[0]["hour"] == 0 and dao5b.saved_bazi[0]["minute"] == 0,
      f"saved_bazi={dao5b.saved_bazi}")

# B2: params 缺生辰 + 档案也无 → 仍 needs_info
dao6 = FakeDAO()
dao6.bazi_info = {}
FakePersonDAO.persons = []
h6 = build_handler(dao6)
r6 = call(h6, "_tool_bazi", '{"出生信息": ""}', "u1")
check("B2 档案也无 → needs_info",
      not r6.ok and r6.needs_info and "出生" in r6.text,
      f"ok={r6.ok} needs_info={r6.needs_info} text={r6.text[:60]}")

# B3: params 自带生辰 → 原路径照旧（不查档案）
dao7 = FakeDAO()
dao7.bazi_info = None
FakePersonDAO.persons = []  # 档案故意为空
h7 = build_handler(dao7)
r7 = call(h7, "_tool_bazi", "1990年5月20日 午时 北京 男", "u1")
check("B3 消息自带生辰 → 原路径照旧",
      r7.ok and not r7.needs_info and h7.engine.calls
      and h7.engine.calls[0][:5] == (1990, 5, 20, 11, 0),
      f"calls={h7.engine.calls}")

# ---------------------------------------------------------------------------
# C. _handle_bazi 档案复用路径
# ---------------------------------------------------------------------------
print("— C. _handle_bazi：复用路径容错")

# C1: 档案缺键（无 minute）→ 不再 KeyError，复用成功（hour/minute 缺省 0）
dao8 = FakeDAO()
dao8.bazi_info = None
person_no_minute = dict(_PERSONA)
person_no_minute.pop("birth_minute", None)
person_no_minute.pop("birth_hour", None)
FakePersonDAO.persons = [person_no_minute]
h8 = build_handler(dao8)
h8._gen_reuse_acknowledgment = lambda msg, saved: "ack"
recorded = {}

def fake_do_bazi(year, month, day, hour, minute, city, gender,
                 question, user_id, stream_cb=None):
    recorded.update(year=year, month=month, day=day, hour=hour,
                    minute=minute, city=city, gender=gender)
    return "analysis-done"

h8._do_bazi_analysis = fake_do_bazi
reply = call(h8, "_handle_bazi", "看看我的运势", "u1")
check("C1 档案缺键不再 KeyError，复用成功",
      "analysis-done" in reply and recorded.get("year") == 1990
      and recorded.get("day") == 20,
      f"reply={reply[:60]} recorded={recorded}")
check("C1 hour/minute 缺省 0（不崩）",
      recorded.get("hour") == 0 and recorded.get("minute") == 0,
      f"recorded={recorded}")

# C2: 档案年/月/日不齐 → 不崩，走信息收集（不调 _do_bazi_analysis）
dao9 = FakeDAO()
dao9.bazi_info = None
person_incomplete = dict(_PERSONA)
person_incomplete.pop("birth_day", None)
FakePersonDAO.persons = [person_incomplete]
h9 = build_handler(dao9)
h9._do_bazi_analysis = lambda *a, **kw: "SHOULD-NOT-RUN"
h9._gen_info_collection_prompt = lambda msg: "info-collect-prompt"
reply9 = call(h9, "_handle_bazi", "看看我的运势", "u1")
check("C2 年/月/日不齐 → 走信息收集，不排盘",
      reply9 == "info-collect-prompt",
      f"reply={reply9[:60]}")

# C3: 消息自带生辰 → 原路径照旧
dao10 = FakeDAO()
dao10.bazi_info = None
FakePersonDAO.persons = []
h10 = build_handler(dao10)
h10._do_bazi_analysis = lambda year, month, day, hour, minute, city, gender, question, user_id, stream_cb=None: f"direct:{year}-{month}-{day}"
reply10 = call(h10, "_handle_bazi", "帮我排盘 1990年5月20日 午时 北京 男", "u1")
check("C3 消息自带生辰 → 直接分析", reply10.startswith("direct:1990-5-20"),
      f"reply={reply10[:60]}")

# ---------------------------------------------------------------------------
# D. 画像注入出生字段
# ---------------------------------------------------------------------------
print("— D. 画像注入出生字段")

# D1: get_profile_summary 有原始出生字段 → 补出生行
mem = UserMemory(base_dir=_MEM_DIR)
mem.save_bazi_info("u_profile", {
    "year": 1990, "month": 8, "day": 20, "hour": 7, "minute": 30,
    "city": "北京", "gender": "男", "bazi": ["庚午", "辛巳", "乙酉", "壬午"],
})
summary = mem.get_profile_summary("u_profile")
check("D1 画像含出生行（时辰/城市/性别/来源标注）",
      "出生:1990年8月20日 辰时 北京 男(来自用户档案)" in summary,
      f"summary={summary}")

# D2: 无 bazi → 无出生行，不崩
mem2 = UserMemory(base_dir=_MEM_DIR)
mem2.remember("u_none", "last_topic", "career")
s2 = mem2.get_profile_summary("u_none")
check("D2 无 bazi → 无出生行", "出生:" not in s2, f"summary={s2}")

# D3: 时辰缺失 → 不含时辰段，其余字段照写
mem3 = UserMemory(base_dir=_MEM_DIR)
mem3.save_bazi_info("u_nohour", {
    "year": 1990, "month": 8, "day": 20,
    "city": "北京", "gender": "男", "bazi": ["庚午", "辛巳", "乙酉", "壬午"],
})
s3 = mem3.get_profile_summary("u_nohour")
check("D3 时辰缺失 → 不含时辰段",
      "出生:1990年8月20日 北京 男(来自用户档案)" in s3 and "时" not in s3.split("出生:")[1].split("北京")[0],
      f"summary={s3}")

# D4: handler _collect_key_facts：persons 兜底 → 关键事实含出生行
dao11 = FakeDAO()
dao11.bazi_info = None
FakePersonDAO.persons = [dict(_PERSONA)]
h11 = build_handler(dao11)
facts11 = call(h11, "_collect_key_facts", "u1")
check("D4 关键事实含出生行（persons 兜底）",
      any("出生:1990年8月20日 辰时 北京 男(来自用户档案)" in f for f in facts11),
      f"facts={facts11}")

# D5: _collect_key_facts 无档案 → 无出生行，不崩
dao12 = FakeDAO()
dao12.bazi_info = {}
FakePersonDAO.persons = []
h12 = build_handler(dao12)
facts12 = call(h12, "_collect_key_facts", "u1")
check("D5 无档案 → 无出生行", not any("出生:" in f for f in facts12),
      f"facts={facts12}")

# format_birth_line 空输入容错
check("D6 format_birth_line 空输入 → 空串",
      format_birth_line is not None
      and format_birth_line(None) == "" and format_birth_line({}) == "")

# ---------------------------------------------------------------------------
print(f"\n结果：PASS {len(PASS)} / FAIL {len(FAIL)}")
for f in FAIL:
    print(f"  FAILED: {f}")
sys.exit(0 if not FAIL else 1)
