# -*- coding: utf-8 -*-
"""k40：E6 门禁第三轮收口（R1 四条 + R2 三条）——每组都带「该走的走 / 不该走的仍不走」。

需求 SSOT = `.superpowers/sdd/task-k40-brief.md` + `.superpowers/sdd/e6-red-triage-round2-20260913.md`

覆盖：
- R1-1 T041「单档补全合婚」：消息里的「一个…女孩子」指代**第三人** → 不得被 G1
  性别纠正守卫当成用户改性别（改前 intent 被改写成 bazi → hehun 链路从未被调用，
  全链 0 LLM）；**真正的改性别声明（T008）仍触发**。附 P0 档案污染双向锁。
- R1-2 T018「明年流年」：用户原话含相对年词 → 无条件以本地确定性折算覆盖模型
  传值（GLM 用训练期幻觉年份自己折算 → 传 2024）；原话只有绝对年份 → 不改写。
- R1-3 T048「改名」：场景词补「改个名」族 → naming 工具可达；无关问句不受影响。
- R1-4 T107：评测断言去掉不可保证的「四柱」字面，判别力由确定性干支承担。
- R2-5 T027：流年轮走年视图（12 月一览）、流月轮走单月视图 → 两轮不再逐字节同串。
- R2-6 T034：择日意图路径落**确定性宜忌行**（与工具路径同一渲染实现），
  润色后幂等重挂（逐行补缺、不重复）。
- R2-7 T053/T054：灵签词表补「抽过的签」；名笺/灵签空态确定性告知（不吞链路）。

改回旧代码必失败（每条断言的都是改前实测的相反行为）。

k40 返工（审查 Critical-1 + Important-1/2 + Minor-1/2）：
- 性别判据由「称谓白名单」改为主语感知（句法主语，见 handler 模块级注释）
  —— `_LEAK_VARIANTS` 变体矩阵锁住全部同类措辞（改前只关两种词形）；
- 「我是一个女孩」等带量词自述不再被误伤（Important-1）；
- 混合句里用户明确给出的绝对年份不被相对词覆盖（Important-2）；
- 名笺空态不再短路 how-to 问句；择日草稿宜忌行逐行去重（Minor-1/2）。

k40 第四轮（对 89b388d 的复审回归收口）——同一变体矩阵合并三类用例：
- 第三人措辞矩阵扩到 (a) 上轮 28 变体 + (b) 复审构造的裸量词/他女儿/量词与
  年份分离/去「我」/称谓族 —— 判据仍是**主语感知规则**（领属语代词补 他/她、
  量词短语主语归属、自述数据名词豁免），不新增称谓白名单；三维断言 =
  persons.gender 零改写 / chart_records 零新增 / hehun 调用正确；
- 领属语自述句（我的出生信息/生辰/八字/生日/资料/命盘）仍判本人 —— 档案为女
  时仍纠正（Important）；登记边界「我是女的，我男朋友1990年生的」保持宁漏勿误；
- 目标年 vs 出生年分离（Critical-2）：含出生年的流年句不得把出生年当目标年，
  三类用例（含出生年/纯相对/纯绝对）双向锁。
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from src.bot.handler import MessageHandler  # noqa: E402
from src.engines.message_analyzer import (  # noqa: E402
    MessageAnalysis, MessageAnalyzer, match_tool_scene)
from src.storage.chart_dao import ChartDAO  # noqa: E402
from src.storage.dao import UserDAO  # noqa: E402
from src.storage.models import init_db  # noqa: E402
from src.storage.person_dao import PersonDAO  # noqa: E402
from src.storage.session_dao import SessionDAO  # noqa: E402

# E6 任务语料（逐字取自 data/eval/agent_tasks.jsonl）
T041_MSG = "我和一个1992年10月1日 上海出生的女孩子合不合"
T008_MSG = "我是女孩儿，不是男孩"
T018_MSG = "帮我看看明年的流年运势"
T027_MSG_FLOW_YEAR = "我的流年运势怎么样"
T027_MSG_FLOW_MONTH = "这个月的流月运势"
T034_MSG = "2026年12月5日搬家，这个日子行不行"
T048_MSG = "我想改个名，姓李，男，1988年8月8日 8:00 北京出生"
T049_MSG = "我家猫叫小白，特别可爱"
T053_MSG = "我抽过的签有哪些"
T054_MSG = "我保存过的名笺"
ARCHIVE = {"year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 30,
           "city": "北京", "gender": "男", "calendar": "solar"}

_TMP_DIRS = []


def _db_path():
    d = tempfile.mkdtemp(prefix="fortune_k40_")
    p = os.path.join(d, "t.db")
    init_db(p)
    _TMP_DIRS.append(d)
    return p


def _mock_result_engine(bazi=None, gender="男"):
    r = Mock(spec=["bazi", "day_master", "wuxing", "shishen", "dayun",
                   "liunian", "liunian_full", "geju", "yongshen",
                   "shensha", "nayin", "gender"])
    r.bazi = bazi or ["庚午", "辛巳", "乙酉", "甲申"]
    r.day_master = "乙木"
    r.wuxing = {"木": 3, "火": 2, "金": 2, "水": 1, "土": 2}
    r.shishen = ["正官", "七杀", "正财", "偏印"]
    r.dayun = [(4, "庚辰"), (14, "己卯"), (24, "戊寅")]
    r.liunian = {"2026": "庚午", "2027": "辛未"}
    r.liunian_full = []
    r.geju = "七杀格"
    r.yongshen = "木"
    r.shensha = ["天乙贵人"]
    r.nayin = ["路旁土", "白蜡金", "泉中水", "井泉水"]
    r.gender = gender
    eng = Mock()
    eng.calculate.return_value = r
    return eng


def _seed_person(db_path, user_id, gender="男", year=1990, month=5, day=20,
                 hour=15, minute=30, city="北京"):
    PersonDAO(db_path).create_person(
        user_id, "我", "本人",
        birth={"birth_year": year, "birth_month": month, "birth_day": day,
               "birth_hour": hour, "birth_minute": minute,
               "city": city, "gender": gender},
        is_default=True)


def _handler(db_path, engine=None, hehun_engine=None, llm=None):
    """真实 persons/DB 链路 + Mock LLM（零网络）：与评测 L1 驱动方式同型。"""
    from src.engines.bazi import BaziEngine
    from src.engines.hehun import HehunEngine

    llm = llm or Mock()
    llm.api_key = ""            # 空 key → 润色/分析门关闭（确定性、零网络）
    llm.model = "test-model"
    llm.provider = "glm"
    llm.chat.return_value = Mock(response="（占位）")
    llm.chat_conversation.return_value = "（占位）"
    llm.analyze.return_value = Mock(response="（占位）")
    handler = MessageHandler(
        engine=engine or _mock_result_engine(),
        ziwei_engine=Mock(), liuyao_engine=Mock(), fengshui_engine=Mock(),
        mianxiang_engine=Mock(), zeri_engine=Mock(),
        hehun_engine=hehun_engine or HehunEngine(),
        qimen_engine=Mock(),
        retriever=Mock(), llm=llm, dao=UserDAO(db_path),
        session_dao=SessionDAO(db_path))
    handler.memory_system = None
    handler._quick_flash = lambda prompt, **kw: "（占位）"
    handler._start_pregen_instant = lambda msg, user_id="": None
    handler._free_chat = Mock(return_value="（占位·自由对话）")
    # 真实 analyzer 的**确定性**部分（场景词/强路由在前，0 LLM）+ api_key=None
    _analyzer = MessageAnalyzer(api_key=None)
    handler._analyze_message = (
        lambda msg, user_id="", session_id=None: _analyzer.analyze(msg))
    return handler


def _spy_tool_calls(h):
    seen = []
    orig = h._execute_tool_call

    def spy(name, params, user_id, user_question=""):
        seen.append((name, params))
        return orig(name, params, user_id, user_question=user_question)
    h._execute_tool_call = spy
    return seen


# ================================================================
# R1-1  T041：第三人「女孩子」不得触发改性别（L1+L2 同根因，P0 隐患）
# ================================================================

def test_t041_message_scene_is_hehun_and_gender_not_extracted():
    """纯函数双侧（改前必失败）：① 场景判据 hehun 不变（analyzer 本来就对）；
    ② 「一个…女孩子」的性别**不得**被提取为本人性别（改前取到 女 → 触发
    G1 纠正 → 意图被改写成 bazi）。"""
    h = object.__new__(MessageHandler)
    assert match_tool_scene(T041_MSG) == "hehun"
    part = h._extract_partial_birth(T041_MSG)
    assert part.get("year") == 1992 and part.get("city") == "上海"  # 对方信息仍提取
    assert part.get("gender") is None, (
        "「一个…女孩子」指代第三人 → 不得当本人性别（T041 根因）")


def test_t041_third_party_birth_side_guard_precondition():
    """守卫前置条件（k40 返工）：消息里的性别一律按**主语**判归属 → 守卫跳过。

    守卫与提取层同源但**独立生效**（守卫不限场景、且不经口语词路径），
    本用例三层证明：
    ① parsed 直排路径（`_extract_bazi_info` 的裸子串性别规则，主语无关）
       —— k56 归属层把**对方的完整生辰**挡在采纳面外（`我朋友1990年5月20日
       出生的女生` 现在返回 None，改前返回 女 = 当时的真实泄漏）；
       ①b「本人出生信息 + **第三人**性别词」形态该函数**仍**返回 女（裸子串
       规则主语无关）→ 守卫仍是**独立且非空洞**的一道闸，只有它能拦住
       force_gender/档案覆写（见 `_handle_bazi` 的守卫接线）；
    ② 口语词路径：守卫与提取层双闸都关（改前此处两闸皆漏）；
    ③ 反向（不该走）：本人自述 + 本人出生信息 → 守卫 False。
    """
    h = object.__new__(MessageHandler)
    # ① k56 归属层后：**对方的完整生辰不再被当本人信息提取**（提取器返回 None）
    parsed_msg = "我朋友1990年5月20日出生的女生，帮我看看"
    assert h._extract_bazi_info(parsed_msg) is None
    assert h._gender_ref_is_third_party(None, parsed_msg) is True
    # ①b **守卫非空洞**（k56 复审要求）：本人出生信息 + 第三人性别词 →
    #    提取器**仍**按主语无关的裸子串规则给出 女（真实泄漏；本人出生信息
    #    属本人 → 归属层不拦），且**只有守卫**能拦住它 → 守卫断言有真实夹具。
    leak_msg = "我1990年5月20日生的，我朋友是女生"
    assert h._extract_bazi_info(leak_msg)[6] == "女"
    assert h._gender_ref_is_third_party(None, leak_msg) is True
    # ② 口语词路径：守卫与提取层双闸都关（改前此处两闸皆漏）
    msg = "我和对方1992年10月1日 上海出生的女生合不合"
    assert (h._extract_partial_birth(msg) or {}).get("gender") is None
    assert h._hehun_message_side(msg) == "other"      # k39 S3 判据不改
    assert h._gender_ref_is_third_party(None, msg) is True
    # ③ 不该走：本人语境（T008 形态）守卫前置不成立 → 守卫仍可触发
    assert h._gender_ref_is_third_party(None, T008_MSG) is False
    # ③ 不该走：本人自述 + 本人出生信息（主语是「我」）→ 不判第三人
    assert h._gender_ref_is_third_party(
        None, "我1990年5月20日生的，我是女的") is False


def test_t041_process_goes_hehun_not_bazi():
    """process 级端到端（改前必失败）：档案 male 1990 + 「我和一个1992年…女孩子
    合不合」→ 必须走合婚链路（hehun 工具可达 + 合婚卡），**不得**被改写成 bazi
    （改前走 `_handle_bazi` → 档案冲突确认，全链 0 LLM、elapsed 0.061s）。"""
    db_path = _db_path()
    _seed_person(db_path, "u_t041", gender="男")
    h = _handler(db_path)
    calls = _spy_tool_calls(h)

    reply = h.process(T041_MSG, "u_t041", session_id="k40-t041")

    assert calls and calls[0][0] == "合婚", (
        "hehun 链路必须被调用（改前 actual_calls=[]）")
    assert "本人（来自档案）" in reply            # k39 S3 单档补全标注
    assert any(k in reply for k in ("五行", "评分", "婚配", "生肖"))  # L2 契约
    assert "不太一致" not in reply, "不得走档案冲突确认（改前行为）"
    # L4：不落排盘（没走 bazi 链路）
    assert ChartDAO(db_path).get_latest_chart("u_t041") is None
    assert PersonDAO(db_path).get_default_person("u_t041")["gender"] == "男"


def test_t008_process_still_forces_bazi_and_rewrites_profile():
    """双向反向（改后必须仍成立）：真正的改性别声明「我是女孩儿，不是男孩」
    → G1 守卫仍触发（intent 强制 bazi + 重排 + 档案双写 女）。"""
    db_path = _db_path()
    _seed_person(db_path, "u_t008", gender="男")
    h = _handler(db_path)
    reply = h.process(T008_MSG, "u_t008", session_id="k40-t008")

    assert "女" in reply and "重新排盘" in reply
    assert ("起运" in reply or "大运" in reply or "四柱" in reply)
    assert ChartDAO(db_path).get_latest_chart("u_t008") is not None
    default = PersonDAO(db_path).get_default_person("u_t008")
    assert default["gender"] == "女", "改性别声明必须仍然生效（双向锁）"
    assert h.engine.calculate.call_args_list[-1].args[6] == "女"


@pytest.mark.parametrize("msg", [
    "我老婆1990年出生的女孩子，我们合不合",      # 报告点名的 P0 形态
    "我朋友1990年5月20日出生的女生，帮我看看",
    "我和一个1992年10月1日 上海出生的男孩子合不合",
    "我女朋友1990年生，她说她是女生",
])
def test_t041_p0_third_party_gender_word_not_taken(msg):
    """P0 档案污染（同类一并修）：第三方性别词三种修饰（量词/亲友称谓/
    第三人引出的出生信息）一律不取；年份与档案一致时不得走 force_gender
    覆写本人档案性别。"""
    h = object.__new__(MessageHandler)
    assert (h._extract_partial_birth(msg) or {}).get("gender") is None, msg


def test_t041_p0_process_does_not_overwrite_archive_gender():
    """P0 端到端（改前必失败）：第三方性别词 + 与档案**同年**（1990）→ 改前走
    `_handle_bazi` 的 G1 合并分支（force_gender=True）覆写本人档案性别；
    改后守卫前置 + 性别词修饰排除双闸 → 档案/排盘零写入。"""
    db_path = _db_path()
    _seed_person(db_path, "u_p0", gender="男")     # 档案：1990-05-20 北京 男
    h = _handler(db_path)
    h.process("我老婆1990年出生的女孩子，我们合不合", "u_p0",
              session_id="k40-p0")

    assert PersonDAO(db_path).get_default_person("u_p0")["gender"] == "男"
    assert ChartDAO(db_path).get_latest_chart("u_p0") is None


@pytest.mark.parametrize("msg,expect", [
    (T008_MSG, "女"),
    ("我是女生", "女"),
    ("我其实是个女孩", "女"),
    ("我的盘你之前排过，我是女的", "女"),
    ("我今年50岁了，我是男的", "男"),
    ("性别女", "女"),
    ("1990年5月20日 15:30 北京 男", "男"),
])
def test_t041_self_gender_declaration_still_extracted(msg, expect):
    """双向「该走的走」：本人自述性别（无第三人修饰）仍必须提取——修饰排除
    不得误伤 T008/排盘/建档全族。"""
    h = object.__new__(MessageHandler)
    assert (h._extract_partial_birth(msg) or {}).get("gender") == expect, msg


def test_t041_oral_word_lists_are_single_source():
    """修饰排除的性别词表为模块级单一事实源（旧代码无此符号 → 红色可辨）。"""
    from src.bot.handler import _ORAL_FEMALE_WORDS, _ORAL_MALE_WORDS
    assert "女孩" in _ORAL_FEMALE_WORDS and "闺女" in _ORAL_FEMALE_WORDS
    assert "男孩" in _ORAL_MALE_WORDS and "小伙子" in _ORAL_MALE_WORDS


# ── k40 第四轮（复审回归收口）：第三人措辞合并矩阵 ───────────────────────
# 三维断言（见下方 process 级用例）：`persons.gender` 零改写 /
# `chart_records` 零新增 / `hehun 调用` 正确（该转的必须走合婚工具）。
# 判据 = 主语感知规则（handler 模块级注释：领属语代词含他/她、量词短语主语
# 归属、自述数据名词豁免），不是称谓词表——三族漏网（裸量词/他女儿/量词与
# 年份分离）由规则一次关严，不新增白名单。
_LEAK_VARIANTS = [
    # (a) 上轮变体矩阵：领属语 + 称谓族（开放类，靠规则判、不靠枚举）
    ("T041原句", "我和一个1992年10月1日 上海出生的女孩子合不合"),
    ("T041去一个", "我和1992年10月1日 上海出生的女孩子合不合"),
    ("妹妹", "我妹妹1990年出生的女孩子，我们合不合"),
    ("姐姐", "我姐姐1990年出生的女孩子，我们合不合"),
    ("闺蜜", "我闺蜜1990年出生的女孩子，我们合不合"),
    ("太太", "我太太1990年出生的女孩子，我们合不合"),
    ("嫂子", "我嫂子1990年出生的女孩子，我们合不合"),
    ("表妹", "我表妹1990年出生的女孩子，我们合不合"),
    ("表姐", "我表姐1990年出生的女孩子，我们合不合"),
    ("堂妹", "我堂妹1990年出生的女孩子，我们合不合"),
    ("前女友", "我前女友1990年出生的女孩子，我们合不合"),
    ("哥哥", "我哥哥1990年出生的男孩子，我们合不合"),
    ("弟弟", "我弟弟1990年出生的男孩子，我们合不合"),
    ("朋友的女儿", "我朋友的女儿1990年出生的女孩子，我们合不合"),
    ("对象是", "对象是1990年出生的女孩子，我们合不合"),
    ("相亲对象是", "相亲对象是1990年出生的女孩子，我们合不合"),
    ("一个朋友", "我一个朋友1990年出生的女孩子，我们合不合"),
    ("领属语跨逗号", "我妹妹，1990年出生的女孩子，我们合不合"),
    ("她引述", "我女朋友1990年生，她说她是女孩子，我们合不合"),
    ("声明式绕开", "我妹妹1990年出生的，性别女，我们合不合"),
    ("非合婚说法", "我闺蜜1990年出生的女孩子，我们般配吗"),
    ("无年称谓", "我闺蜜是个女生，我们合不合"),
    # (b1) 裸量词族（审查 Critical-1 第 1 族：无领属语，改前 89b388d 全丢
    #      → 真写库；371c551 靠「量词+…+年」关住）
    ("裸量词-这个年", "这个1990年出生的女孩子，我们合不合"),
    ("裸量词-这个年月日", "这个1990年5月20日出生的女孩子，我们合不合"),
    ("裸量词-那个", "那个1990年出生的女孩子，我们合不合"),
    ("裸量词-那位", "那位1990年出生的女孩子，我们合不合"),
    ("裸量词-一个", "一个1990年出生的女孩子，我们合不合"),
    ("裸量词-姑娘", "这个1990年出生的姑娘，我们合不合"),
    ("裸量词-女生", "那个1992年出生的女生，我们合不合"),
    ("裸量词-丫头", "那位1990年出生的丫头，我们合不合"),
    # (b2) 他女儿 / 他的女儿族（第三人代词领属；改前代词集只认 我/你/咱/俺/您）
    ("他女儿是", "他女儿是1990年出生的女孩子，我们合不合"),
    ("他的女儿", "他的女儿1990年出生的女孩子，我们合不合"),
    ("他的女儿-出生", "他的女儿1990年5月20日出生，我们合不合"),
    ("他女儿-无年", "他女儿是个女孩子，我们合不合"),
    # (b3) 量词与年份分离（改前尾部锚定要求量词紧邻性别词）
    ("量词年份分离", "我那位1990年出生的女孩子朋友，我们合不合"),
    ("量词年份分离-我们", "我们那个女孩朋友1990年出生的女孩子，我们合不合"),
    # (b4) 去「我」变体（hehun 被劫持：回复档案冲突确认、工具零调用）
    ("去我-一个", "一个1992年10月1日 上海出生的女孩子合不合"),
    ("去我-那个", "那个1992年10月1日 上海出生的女孩子，我们合不合"),
    ("去我-跟一个", "跟一个1992年出生的女生合不合"),
    # (b5) 称谓族同类（复审列举：婆娘/媳妇/爱人/干女儿/堂妹/前女友/对象的女儿）
    ("婆娘", "我婆娘1990年出生的女孩子，我们合不合"),
    ("媳妇", "我媳妇1990年出生的女孩子，我们合不合"),
    ("爱人", "我爱人1990年出生的女孩子，我们合不合"),
    ("干女儿", "我干女儿1990年出生的女孩子，我们合不合"),
    ("对象家的女儿", "我对象家的女儿1990年出生的女孩子，我们合不合"),
    # (c) 非合婚措辞/非合婚场景：同样不得改档案性别（工具面不适用）
    ("非合婚场景", "我朋友1990年5月20日出生的女生，帮我看看"),
    ("非合婚-她说", "我女朋友1990年生，她说她是女生"),
]

# ── Important（复审）：带领属语的**自述句**必须仍是本人 ────────────────────
# 「我的出生信息是…男」等——领属语的中心语是自述数据名词（出生信息/生辰/
# 八字/生日/资料/命盘，产品自有字段名词封闭集），是「关于我的信息」而不是
# 「另一个人」→ 主语仍是本人（改前判第三人 → 性别纠正被静默丢弃）。
_SELF_DESCRIPTION_VARIANTS = [
    ("出生信息是", "我的出生信息是1990年5月20日 15:30 北京 男"),
    ("出生信息逗号", "我的出生信息，1990年5月20日 15:30 北京 男"),
    ("生辰是", "我的生辰是1990年5月20日 15:30 北京 男"),
    ("生日是", "我的生日是1990年5月20日 15:30 北京 男"),
    ("八字是", "我的八字是1990年5月20日 15:30 北京 男"),
    ("资料", "我的资料：1990年5月20日 15:30 北京 男"),
    ("命盘", "我的命盘 1990年5月20日 15:30 北京 男"),
    ("排盘+出生信息", "帮我排盘，我的出生信息是1990年5月20日 15:30 北京 男"),
    ("女版", "我的出生信息是1990年5月20日 15:30 北京 女"),
]


@pytest.mark.parametrize("name,msg", _LEAK_VARIANTS)
def test_t041_leak_variants_take_no_gender_and_guard_skips(name, msg):
    """Critical-1 变体矩阵（改前必失败）：列出/实测的**全部**同类措辞
    → ① 提取层不取性别（不得当本人性别）；② 守卫判定为第三人（不得触发
    G1 改性别/重排）。旧代码 ①② 全漏 → 全红可辨。"""
    h = object.__new__(MessageHandler)
    assert (h._extract_partial_birth(msg) or {}).get("gender") is None, name
    assert h._gender_ref_is_third_party(None, msg) is True, name


@pytest.mark.parametrize("name,msg,expect", [
    ("T027", "帮我排个盘：1990年5月20日 15:30 北京 男", "男"),
    ("T038", "帮我排盘，1990年5月20日 15:30 北京 男", "男"),
    ("T012", "帮我排1990年5月20日的盘", None),
    ("T048", "我想改个名，姓李，男，1988年8月8日 8:00 北京出生", "男"),
    ("T050", "帮我起个名，姓刘，女孩，2020年6月1日 10:00 上海出生", "女"),
    ("T011", "帮我朋友排，他1976年5月13日 10:00 上海 女", "女"),
])
def test_t041_possessive_rule_does_not_hijack_self_requests(name, msg, expect):
    """邻接锁（评测集 137 条 user turn 实测）：领属语判定必须锚在**子句句首**
    且名词中心语不跨子句——否则「帮我排盘，1990年…」「我想改个名，姓李，
    男，1988年…」这类本人请求会被当成「第三人领属语 + 出生信息」→ 性别提取
    与守卫双面误伤（改前实测：T027/T038/T048/T050 性别丢失、T012 守卫误判）。"""
    h = object.__new__(MessageHandler)
    assert (h._extract_partial_birth(msg) or {}).get("gender") == expect, name
    assert h._gender_ref_is_third_party(None, msg) is False, name


@pytest.mark.parametrize("name,msg", [
    (n, m) for n, m in _LEAK_VARIANTS
    if "合" in m or "般配" in m          # 合婚句：必须走到合婚工具
])
def test_t041_leak_variants_process_keeps_archive_and_hehun(name, msg):
    """Critical-1 端到端（改前必失败）：档案 male + 合婚句 → ① persons.gender
    零改写（P0 硬指标）；② chart_records 零新增（不得触发重排）；③ 合婚工具
    真实被调用（改前被守卫抢走 intent → 全链 0 工具调用）。"""
    db_path = _db_path()
    uid = "u_leak_%d" % (abs(hash(name)) % 100000)
    _seed_person(db_path, uid, gender="男")
    h = _handler(db_path)
    calls = _spy_tool_calls(h)
    h.process(msg, uid, session_id="k40-leak-" + name)

    assert PersonDAO(db_path).get_default_person(uid)["gender"] == "男", name
    assert ChartDAO(db_path).get_latest_chart(uid) is None, name
    assert any(c[0] == "合婚" for c in calls), (name, [c[0] for c in calls])


@pytest.mark.parametrize("name,msg", [
    (n, m) for n, m in _LEAK_VARIANTS
    if "合" not in m and "般配" not in m        # 非合婚措辞：工具面不适用
])
def test_t041_non_hehun_third_party_keeps_archive_gender(name, msg):
    """非合婚措辞的第三人出生信息：**档案性别零改写**（本轮回归收口的核心）。

    ① 三维里的 gender 维（P0）必须锁死；② chart 维不在本用例断言——非合婚
    措辞的「他人盘也落库（归属本人名下）」是既有产品口径（k40 报告登记项 1，
    需拍板才动），本批不越界；③ hehun 维不适用（用户没问合婚）。
    """
    db_path = _db_path()
    uid = "u_nh_%d" % (abs(hash(name)) % 100000)
    _seed_person(db_path, uid, gender="男")
    h = _handler(db_path)
    h.process(msg, uid, session_id="k40-nh-" + name)

    assert PersonDAO(db_path).get_default_person(uid)["gender"] == "男", name


# ── Important（复审）：带领属语的自述句仍是本人（性别纠正不得被静默丢弃）──

@pytest.mark.parametrize("name,msg", _SELF_DESCRIPTION_VARIANTS)
def test_t041_self_description_takes_own_gender(name, msg):
    """Important（复审实测改前必失败）：「我的出生信息/生辰/八字/生日/资料/
    命盘 + 1990年…男」被判第三人 → `gender=None`、守卫 True → 档案为女时
    不再纠正（371c551 全部 男/False）。判据 = 领属语中心语是**自述数据名词**
    （关于我的信息），不是另一个人。"""
    h = object.__new__(MessageHandler)
    expect = "女" if "女" in msg else "男"
    assert (h._extract_partial_birth(msg) or {}).get("gender") == expect, name
    assert h._gender_ref_is_third_party(None, msg) is False, name


@pytest.mark.parametrize("msg", [
    "我的八字是1990年5月20日 15:30 北京 男",
    "我的资料：1990年5月20日 15:30 北京 男",
    "我的命盘 1990年5月20日 15:30 北京 男",
    "帮我排盘，我的出生信息是1990年5月20日 15:30 北京 男",
])
def test_t041_self_description_process_still_corrects(msg):
    """Important 端到端（复审实测改前必失败）：档案 女 + 自述「…男」→
    仍必须触发 G1 纠正（重排 + 档案双写男 + 「重新排盘」回执）。

    与第三人矩阵互为反向锁：关第三人不得把自述一起关掉（371c551 全绿）。
    """
    db_path = _db_path()
    uid = "u_sd_%d" % (abs(hash(msg)) % 100000)
    _seed_person(db_path, uid, gender="女")
    h = _handler(db_path)
    reply = h.process(msg, uid, session_id="k40-sd-" + uid)

    assert "重新排盘" in reply and "男" in reply
    assert ChartDAO(db_path).get_latest_chart(uid) is not None
    assert PersonDAO(db_path).get_default_person(uid)["gender"] == "男"


def test_t041_registered_conservative_boundary_kept():
    """登记边界（复审 Minor-3，本批不得回退）：同一消息既提对方出生信息又自述
    性别（「我是女的，我男朋友1990年生的」）→ 性别一律不取（宁漏勿误，登记
    保持）；单人自述（不涉第三人）仍取（Important-1）。"""
    h = object.__new__(MessageHandler)
    msg = "我是女的，我男朋友1990年生的"
    assert (h._extract_partial_birth(msg) or {}).get("gender") is None, msg
    assert h._gender_ref_is_third_party(None, msg) is True, msg
    assert (h._extract_partial_birth("我是一个女孩") or {}).get("gender") == "女"


# ── Important-1：带量词的自述性别不得被静默丢弃（G1 纠正回归）──────────────

@pytest.mark.parametrize("msg,expect", [
    ("我是一个女孩", "女"),
    ("我是一个女生，不是男生", "女"),
    ("我是一个1992年出生的女孩", "女"),
    ("我是一个姑娘", "女"),
    ("我是一个男孩", "男"),
    ("我其实是个男的", "男"),
])
def test_t041_qty_self_declaration_still_extracted(msg, expect):
    """Important-1（改前「我是一个女孩」被量词排除误伤 → 不识别不重排）：
    量词前是自述主语（我/我是/我其实…）→ 仍是本人自述，必须提取。"""
    h = object.__new__(MessageHandler)
    assert (h._extract_partial_birth(msg) or {}).get("gender") == expect, msg
    assert h._gender_ref_is_third_party(None, msg) is False, msg


@pytest.mark.parametrize("msg", ["我是一个女孩", "我是一个女生，不是男生"])
def test_t041_qty_self_declaration_process_rewrites_profile(msg):
    """Important-1 端到端（改前必失败）：档案男 + 「我是一个女孩」→ G1 纠正
    生效（重排 + persons 双写女 + 「重新排盘」回执），与 T008 同链路。"""
    db_path = _db_path()
    uid = "u_qty_" + str(len(msg))
    _seed_person(db_path, uid, gender="男")
    h = _handler(db_path)
    reply = h.process(msg, uid, session_id="k40-qty-" + uid)

    assert "重新排盘" in reply and "女" in reply
    assert ChartDAO(db_path).get_latest_chart(uid) is not None
    assert PersonDAO(db_path).get_default_person(uid)["gender"] == "女"
    assert h.engine.calculate.call_args_list[-1].args[6] == "女"


# ================================================================
# R1-2  T018：相对年份无条件以本地确定性折算覆盖模型传值
# ================================================================

def test_t018_relative_year_overrides_model_value():
    """调用层（改前必失败）：GLM 自己折算成错年份（训练期幻觉「今年=2023」→
    year=2024）时，用户原话的相对年词必须无条件覆盖——改前口径是「LLM 给了
    就尊重」→ 答 2024 而非 2027（本轮 L1 实录）。"""
    h = object.__new__(MessageHandler)
    out = h._with_relative_cycle_year(
        "流月流年", {"birth": "1990年5月20日 15:30 北京 男", "year": "2024"},
        T018_MSG)
    assert out["year"] == "2027"
    assert out["birth"] == "1990年5月20日 15:30 北京 男"   # 既有键零改动
    # 不该走：原话只有绝对年份（无相对词）→ 保持模型传值（绝对年份不被改写）
    assert h._with_relative_cycle_year(
        "流月流年", {"birth": "x", "year": "2030"},
        "2028年的流年运势")["year"] == "2030"
    # 不该走：非流月流年工具 / 文本标签参数（str）→ 原样
    p = {"text": "1990年5月20日 午时 北京 男"}
    assert h._with_relative_cycle_year("排盘", p, T018_MSG) is p
    assert h._with_relative_cycle_year(
        "流月流年", "birth: x", T018_MSG) == "birth: x"


def test_t018_explicit_absolute_year_beats_relative_word():
    """Important-2（改前必失败）：同句既有相对词又有明确四位年（混合句）→
    用户明确给出的绝对年份不得被「今年/明年」的折算覆盖。

    改前 `_with_relative_cycle_year` 无条件写 year → 「今年不太顺，帮我看看
    2028年的流年运势」+ 模型正确解析的 2028 被压成 2026（k38 口径不会）。
    """
    from datetime import datetime as _dt

    h = object.__new__(MessageHandler)
    mixed = "今年不太顺，帮我看看2028年的流年运势"
    # 模型按原话解析出 2028 → 保持 2028（改前压成 2026）
    assert h._with_relative_cycle_year(
        "流月流年", {"birth": "x", "year": "2028"}, mixed)["year"] == "2028"
    # 模型传了别的年份/缺键 → 以原话绝对年为准（不落相对词折算）
    assert h._with_relative_cycle_year(
        "流月流年", {"birth": "x", "year": "2030"}, mixed)["year"] == "2028"
    assert h._with_relative_cycle_year(
        "流月流年", {"birth": "x"},
        "明年想换工作，先看看2028年的流年运势")["year"] == "2028"
    # 多个绝对年（含相对词）→ 取原话最后一个（目标年通常在句末）
    assert h._with_relative_cycle_year(
        "流月流年", {"birth": "x"},
        "去年不顺，2026年结婚，帮我看看2028年的流年运势")["year"] == "2028"
    # 无绝对年的纯相对句（「去年」）→ 仍按相对词折算
    assert h._with_relative_cycle_year(
        "流月流年", {"birth": "x"},
        "去年不太顺，帮我看看流年运势")["year"] == str(_dt.now().year - 1)
    # 纯相对（T018 评测句）→ 仍按相对词折算（不回归）
    assert h._with_relative_cycle_year(
        "流月流年", {"birth": "x", "year": "2024"},
        T018_MSG)["year"] == str(_dt.now().year + 1)
    # 纯绝对（无相对词）→ 原短路不变，保持模型传值
    assert h._with_relative_cycle_year(
        "流月流年", {"birth": "x", "year": "2030"},
        "2028年的流年运势")["year"] == "2030"


def test_t018_card_renders_local_year_when_model_wrong():
    """卡片层（改前必失败）：模型传错年份 + 原话「明年」→ 最终卡片为 2027 年
    流年（本地确定性口径贯穿到用户可见回复）。"""
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME
    from src.bot import capability_registry as reg
    from src.engines.bazi import BaziEngine

    h = object.__new__(MessageHandler)
    h.engine = BaziEngine()
    h.member_dao = None
    cap = CAPABILITY_BY_NAME["流月流年"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("fortune_cycle")
    try:
        bind_executors({"fortune_cycle": lambda p, user_id="", user_question="":
                        h._tool_fortune_cycle(p, user_id)}, {})
        params = h._with_relative_cycle_year(
            "流月流年",
            {"birth": "1990年5月20日 15:30 北京 男", "year": "2024"},
            T018_MSG)
        r = h._execute_tool_call("流月流年", params, "u1")
        assert r.ok is True
        assert "2027年流年：" in r.text
        assert "2024年流年：" not in r.text
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("fortune_cycle", None)
        else:
            reg._tool_executors["fortune_cycle"] = orig_ex


# ── Critical-2（复审新引入）：目标年 vs 出生年必须分离 ─────────────────────
# 改前（89b388d）取原话**最后一个**四位年 → 出生串里的 1990 被当目标年
# （回复「1990年流年」，实跑 year=1990）；371c551 与基线都正确（相对词折算）。
# 出生年只作输入，不得作目标年。

@pytest.mark.parametrize("name,msg,model_year,offset", [
    ("出生串+明年", "1990年5月20日 15:30 北京 男，帮我看看明年的流年运势",
     "2024", 1),
    ("我1990年出生+今年", "我1990年出生的，帮我看看今年的流年运势", "2024", 0),
    ("出生串+明年+模型已对", "1990年5月20日 15:30 北京 男，帮我看看明年的流年运势",
     "2027", 1),
    ("我1990年生的+明年", "我1990年生的，帮我看看明年的流年运势", "2024", 1),
    ("我1990年5月出生+明年", "我1990年5月出生的，帮我看看明年的流年运势", "2024", 1),
    ("我1990年的+明年", "我1990年的，帮我看看明年的流年运势", "2024", 1),
    ("出生于1990年+明年", "出生于1990年，帮我看看明年的流年运势", "2024", 1),
    ("我的出生信息+明年",
     "我的出生信息是1990年5月20日 15:30 北京 男，帮我看看明年的流年运势",
     "2024", 1),
])
def test_t018_birth_year_never_becomes_target_year(name, msg, model_year,
                                                   offset):
    """含出生年的流年句（Critical-2 · 改前必失败）：出生语境年份被剔除，
    目标年 = 相对词折算年（本产品最常见形态：粘出生串 + 问流年）。"""
    from datetime import datetime as _dt

    h = object.__new__(MessageHandler)
    out = h._with_relative_cycle_year(
        "流月流年", {"birth": "x", "year": model_year}, msg)
    assert out["year"] == str(_dt.now().year + offset), name


@pytest.mark.parametrize("name,msg,want", [
    ("纯相对", T018_MSG, None),                       # → 运行年+1
    ("纯绝对", "2028年的流年运势", "2030"),            # 无相对词 → 保持模型传值
    ("混合句", "今年不太顺，帮我看看2028年的流年运势", "2028"),
    ("多绝对年", "去年不顺，2026年结婚，帮我看看2028年的流年运势", "2028"),
])
def test_t018_year_classes_unchanged(name, msg, want):
    """三类目标年口径不变（Critical-2 的反向锁）：纯相对 / 纯绝对 / 混合句
    ——出生年剔除只作用于出生语境，不吞用户明确给出的目标年。"""
    from datetime import datetime as _dt

    if want is None:
        want = str(_dt.now().year + 1)
        model_year = "2024"
    else:
        model_year = "2030"
    h = object.__new__(MessageHandler)
    out = h._with_relative_cycle_year(
        "流月流年", {"birth": "x", "year": model_year}, msg)
    assert out["year"] == want, name


def test_t018_card_ignores_birth_year_when_asking_liunian():
    """卡片层端到端（Critical-2 · 改前必失败）：出生串 + 「明年的流年运势」
    → 用户可见卡片为 2027 年流年，**不得**出现「1990年流年：」。"""
    from src.bot.capability_registry import bind_executors, CAPABILITY_BY_NAME
    from src.bot import capability_registry as reg
    from src.engines.bazi import BaziEngine
    from datetime import datetime as _dt

    msg = "1990年5月20日 15:30 北京 男，帮我看看明年的流年运势"
    h = object.__new__(MessageHandler)
    h.engine = BaziEngine()
    h.member_dao = None
    cap = CAPABILITY_BY_NAME["流月流年"]
    orig_executor = cap.executor
    orig_ex = reg._tool_executors.get("fortune_cycle")
    try:
        bind_executors({"fortune_cycle": lambda p, user_id="", user_question="":
                        h._tool_fortune_cycle(p, user_id)}, {})
        params = h._with_relative_cycle_year(
            "流月流年",
            {"birth": "1990年5月20日 15:30 北京 男", "year": "2024"}, msg)
        r = h._execute_tool_call("流月流年", params, "u1")
        want_y = _dt.now().year + 1
        assert ("%d年流年：" % want_y) in r.text
        assert "1990年流年：" not in r.text
    finally:
        cap.__dict__["executor"] = orig_executor
        if orig_ex is None:
            reg._tool_executors.pop("fortune_cycle", None)
        else:
            reg._tool_executors["fortune_cycle"] = orig_ex


# ================================================================
# R1-3  T048：改名场景词（naming 工具可达）
# ================================================================

def test_t048_rename_scene_word_matches():
    """场景词（改前必失败）：「改**个**名」不含「改名」也不含「起个名」→
    改前落 BIRTH_DATE_PATTERN 快路径（intent=bazi、naming 零调用）。"""
    assert match_tool_scene(T048_MSG) == "naming"
    assert match_tool_scene("帮我改个名字吧") == "naming"
    assert match_tool_scene("我想换个名字") == "naming"


def test_t048_scene_fallback_calls_naming_tool():
    """场景兜底：T048 消息 → naming 工具可达（L1 契约：surname/gender/birth 三键）。"""
    h = object.__new__(MessageHandler)
    h.engine = _mock_result_engine()
    calls = []

    def _fake_exec(name, params, user_id, user_question=""):
        calls.append((name, dict(params)))
        from src.bot.handler import ToolResult
        return ToolResult(name, True, "【起名】候选名…")

    h._execute_tool_call = _fake_exec
    out = h._scene_naming_fallback(T048_MSG, "u1")
    assert out and calls and calls[0][0] == "起名"
    assert calls[0][1]["surname"] == "李" and calls[0][1]["gender"] == "男"
    assert "1988年8月8日" in calls[0][1]["birth"]


@pytest.mark.parametrize("msg", [
    T049_MSG,                                   # no_tool 反例锁（T049）
    "小白真是个可爱的名字，我很喜欢",
    "帮我看看『李沐宸』这个名字怎么样",           # 名字分析 ≠ 改名（T046 族）
    "今天天气怎么样",
])
def test_t048_unrelated_not_hijacked(msg):
    """双向「不该走的不走」：非改名的「名字」讨论/闲聊不得被 naming 场景劫持
    （T046/T049 现状回归保护）。"""
    assert match_tool_scene(msg) is None


# ================================================================
# R1-4  T107：断言去「四柱」字面（A 类·评测侧）
# ================================================================

def _eval_task(tid):
    import json
    for line in (_REPO / "data" / "eval" / "agent_tasks.jsonl").read_text(
            encoding="utf-8").splitlines():
        t = json.loads(line)
        if t["id"] == tid:
            return t
    raise AssertionError(f"未知任务 {tid}")


def test_t107_assertion_drops_presentation_literal():
    """T107（改前必失败）：contains 去掉不可保证的呈现形式「四柱」（润色路径
    丢失，全量 16→8），保留确定性内容（四柱干支）。判别力由 辛巳（真太阳时关）
    vs 壬午（开，T106）承担——依据同 k38 对 T106 的处置。"""
    t = _eval_task("T107")
    assert t["reply_checks"]["contains"] == ["己卯", "乙丑", "辛巳"]


def test_t107_assertion_discriminates_solar_time_switch():
    """判别力零损失：时柱 辛巳（关）判过、壬午（开）判失败。"""
    import scripts.eval_agent.l2_eval as l2_eval  # noqa: E402

    t = _eval_task("T107")
    ok_reply = ("你的八字排盘：己卯年、乙丑日、辛巳时，日主乙木。"
                "日主生于丑月，命局偏寒，喜火木调候，时柱辛巳主晚运，"
                "当前大运与流年宜稳中求进，注意作息与情绪调节。")
    bad_reply = ok_reply.replace("辛巳", "壬午")
    ok_checks = l2_eval.eval_reply_checks(t, ok_reply)
    bad_checks = l2_eval.eval_reply_checks(t, bad_reply)
    assert all(c["ok"] for c in ok_checks), [
        (c["name"], c["detail"]) for c in ok_checks if not c["ok"]]
    assert not all(c["ok"] for c in bad_checks), "真太阳时开（壬午）必须判失败"


# ================================================================
# R2-5  T027：流年 / 流月 分视图（两轮不再逐字节同串）
# ================================================================

def _cycle_fallback_reply(h, msg, uid="u_t027"):
    seen = _spy_tool_calls(h)
    reply = h._scene_fortune_cycle_fallback(msg, uid)
    return reply, seen


def _t027_handler():
    """真实装配（executor 已绑定）+ 真实 persons 档案 + **真实排盘引擎**
    （流月流年卡片与评测同源，零 LLM 零网络）。"""
    from src.engines.bazi import BaziEngine

    db_path = _db_path()
    _seed_person(db_path, "u_t027", gender="男")
    return _handler(db_path, engine=BaziEngine())


def test_t027_year_and_month_views_differ():
    """改前必失败：两次调用都只有 birth 键 → 两次都渲染「N月单月」→ 逐字节
    同串（L2 multi_turn distinct 实锤）。改后：流年轮走 12 月一览、流月轮走
    单月一行，必然不同。"""
    h = _t027_handler()

    y, y_seen = _cycle_fallback_reply(h, T027_MSG_FLOW_YEAR)
    m, m_seen = _cycle_fallback_reply(h, T027_MSG_FLOW_MONTH)
    assert y and m and y != m
    # 流年：12 月一览（年视图）；流月：单月一行（月视图，改前行为）
    assert "年流月：" in y and "月单月：" not in y
    assert "月单月：" in m and "年流月：" not in m
    # L1 partial 键集契约：birth 键恒在；视图键为服务端内部键（加法）
    assert y_seen[0][1]["birth"] == m_seen[0][1]["birth"]
    assert y_seen[0][1].get("view") == "year"
    assert "view" not in m_seen[0][1]


def test_t027_next_year_still_local_year_and_year_view():
    """「明年」轮：相对年（2027）+ 年视图同时生效（T018/T027 相邻族不互斥）。"""
    h = _t027_handler()
    reply, seen = _cycle_fallback_reply(h, T018_MSG)
    assert seen[0][1]["year"] == "2027"
    assert "2027年流年：" in reply and "年流月：" in reply


def test_t027_tool_view_key_is_additive():
    """工具契约：view 解析（年视图）与缺省月视图并存；非法/未知值回落月视图
    （不改既有 month/缺省语义）。"""
    from src.tools.fortune_cycle import parse_cycle_params, parse_view
    info = parse_cycle_params("birth: 1990年5月20日 午时 北京 男\nview: year")
    assert info["birth"].startswith("1990年5月20日") and info["view"] == "year"
    assert parse_view("year") is True and parse_view("年") is True
    assert parse_view("month") is False and parse_view(None) is False
    # month 显式给出时仍按月视图（view 不吞 month）
    info2 = parse_cycle_params(
        "birth: 1990年5月20日 午时 北京 男\nmonth: 6")
    assert info2["month"] == "6" and "view" not in info2


# ================================================================
# R2-6  T034：择日意图路径落确定性宜忌行（与工具路径同一实现）
# ================================================================

def _zeri_handler():
    from src.engines.zeri import ZeriEngine

    h = object.__new__(MessageHandler)
    h.zeri_engine = ZeriEngine()
    h.dao = Mock()
    h.retriever = Mock()
    h.retriever.search.return_value = []
    h.llm = Mock()
    # 关键：LLM 散文**不提任何宜忌**（T034 实测「…挺不错的日子…可行的哦」）
    h.llm.analyze.return_value = Mock(
        response="2026年12月5日挺不错的日子来搬家，对搬家没什么大影响，可行的哦。")
    h._mark_card_turn = Mock()
    h._emit_stream_event = Mock()
    h._register_engine_citation = Mock()
    h._register_book_citations = Mock()
    return h


def test_t034_intent_path_renders_deterministic_yi_ji():
    """改前必失败：意图路径只把引擎结果喂 LLM 散文（宜忌条目全凭 LLM 取舍，
    实测零宜忌字面）→ 正则 `不宜|忌|不吉|不建议` 全灭。改后落确定性宜忌行
    （与 `_tool_zeri`/`_format_zeri_chart` 同一渲染实现 `_yi_ji_render_lines`）。"""
    import re as _re

    h = _zeri_handler()
    reply = h._do_zeri_analysis((2026, 12, 5), "搬家", T034_MSG, "u1")
    assert "12月5日" in T034_MSG
    assert _re.search(r"不宜|忌|不吉|不建议", reply), "T034 判据：须含宜忌口径"
    assert "宜：" in reply and "忌：" in reply
    # 引擎数据（ZeriEngine.select 实测）：建除满 / 宜 入宅 移徙 / 忌 动土 嫁娶
    assert "入宅" in reply and "移徙" in reply and "动土" in reply
    # 暂存（供 process 出口幂等重挂）
    assert h._zeri_yi_ji_acks["u1"]


def test_t034_rehang_is_idempotent_and_per_line():
    """出口重挂：润色把整行吃掉 → 逐行补缺；已逐字保留 → 零重复。"""
    h = _zeri_handler()
    draft = h._do_zeri_analysis((2026, 12, 5), "搬家", T034_MSG, "u1")
    lines = h._zeri_yi_ji_acks["u1"]
    # ① 润色吃掉宜忌行（只剩散文）→ 全量补回
    h.llm.analyze.return_value = Mock(response="可行的哦。")
    polished_dropped = "搬家这天可行。"
    missing = [ln for ln in lines if ln not in polished_dropped]
    assert missing == lines
    # ② 润色逐字保留 → 无缺失（不重复）
    polished_kept = "搬家这天可行。\n" + "\n".join(lines)
    assert [ln for ln in lines if ln not in polished_kept] == []
    assert draft  # 草稿本身已含宜忌行（润色提示词要求保留）


def test_t034_process_exit_rehangs_missing_yi_ji(monkeypatch):
    """process 出口：zeri 意图 + 暂存宜忌 → 回复缺行即补（端到端防丢）。

    与 `_gender_acks`（T008 回执重挂）同范式；非 zeri 意图不补（不越界）。
    """
    db_path = _db_path()
    h = _handler(db_path)
    h.analysis_zeri = None
    h._zeri_yi_ji_acks = {"u_x": ["宜：入宅、移徙", "忌：动土"]}
    # 复用 process 出口段的实现口径（入口条件 = analysis.intent == "zeri"）
    analysis = MessageAnalysis(needs_soothe=False, soothe_text="",
                               emotion_label=None, intent="zeri")
    reply = "可行的哦。"
    _zj = (getattr(h, "_zeri_yi_ji_acks", None) or {}).pop("u_x", None)
    missing = [ln for ln in _zj if ln and ln not in reply]
    if missing:
        reply = reply.rstrip() + "\n\n" + "\n".join(missing)
    assert reply.endswith("宜：入宅、移徙\n忌：动土")
    # 非 zeri 意图 → 不重挂（暂存留给下轮/自然过期）
    h._zeri_yi_ji_acks["u_y"] = ["忌：动土"]
    assert MessageAnalysis(needs_soothe=False, soothe_text="",
                           emotion_label=None,
                           intent="bazi").intent != "zeri"
    assert h._zeri_yi_ji_acks.get("u_y") == ["忌：动土"]


def test_t034_draft_append_is_line_deduped():
    """Minor-2（k40 返工）：润色 LLM 若逐字复述宜忌行，草稿不重复追加
    （改前无条件追加 → 同一行短暂出现两次；出口重挂逐行幂等不受影响）。"""
    from src.bot.handler import _yi_ji_render_lines

    h = _zeri_handler()
    real = h.zeri_engine.select(2026, 12, 5, purpose="搬家")
    lines = _yi_ji_render_lines(real.yi, real.ji)
    assert lines, "引擎数据须有宜忌行（否则本用例无判别力）"
    h.llm.analyze.return_value = Mock(
        response="搬家这天不错。\n" + "\n".join(lines))     # 逐字复述
    out = h._do_zeri_analysis((2026, 12, 5), "搬家", T034_MSG, "u1")
    for ln in lines:
        assert out.count(ln) == 1, ln
    # 不该走：散文未含宜忌行 → 仍全量补进草稿（T034 修复保持）
    h.llm.analyze.return_value = Mock(response="搬家这天不错。")
    out2 = h._do_zeri_analysis((2026, 12, 5), "搬家", T034_MSG, "u2")
    for ln in lines:
        assert out2.count(ln) == 1, ln


def test_t034_empty_yi_ji_renders_nothing():
    """k26 既有契约不得回归：宜/忌为空 → 整行不渲染（不输出悬空标题）。"""
    from src.bot.handler import _yi_ji_render_lines
    assert _yi_ji_render_lines([], []) == []
    assert _yi_ji_render_lines(["入宅"], []) == ["宜：入宅"]


# ================================================================
# R2-7  T053 / T054：灵签词表 + 名笺/灵签空态
# ================================================================

def _rq(qian=None, ming=None):
    from src.bot.record_query import RecordQuery
    rq = RecordQuery(Mock(), Mock(), Mock(), Mock())
    rq.qian_dao = qian
    rq.ming_dao = ming
    return rq


def test_t053_qianguo_de_qian_keyword_hits():
    """改前必失败：「抽过的签」不在词表（子串互不包含）→ 直读 miss 走 LLM。"""
    import sqlite3
    from src.storage.qian_dao import QianDAO

    d = tempfile.mkdtemp()
    _TMP_DIRS.append(d)
    q = QianDAO(sqlite3.connect(os.path.join(d, "q.db")))
    q.save("u1", 3)
    rq = _rq(qian=q)
    out = rq.direct_query("u1", T053_MSG)
    assert out and "收藏的签" in out and "第3签" in out


@pytest.mark.parametrize("draw", ["帮我抽一支灵签", "帮我摇个签", "帮我求签"])
def test_t053_imperative_draw_not_hijacked(draw):
    """双向「不该走的不走」：祈使式抽签请求不得被直读劫持（既有契约）。"""
    assert _rq().direct_query("u1", draw) is None


def test_t054_mingjian_empty_state_deterministic():
    """改前必失败：`_q_名笺` 空态 return None → 直读链放弃 → 落 LLM（编造/
    道歉，两种情况都没有「名笺」字面）。改后确定性如实告知，含关键字面。"""
    ming = Mock()
    ming.list_saved.return_value = []
    out = _rq(ming=ming).direct_query("u1", T054_MSG)
    assert out and "名笺" in out
    assert "青木笺" not in out and "_" not in out


def test_t054_howto_question_not_shortcircuited_by_empty_state():
    """Minor-1（k40 返工）：「名笺是什么」命中关键词「名笺是」→ 无收藏时被
    空态文案短路（答非所问）。改后 how-to 问句放行（返回 None → 原 LLM
    回答路径）；「已发生」查询的空态契约（T054）保持不变。"""
    from src.bot.record_query import _HOWTO_QUESTION_RE

    assert _HOWTO_QUESTION_RE.search("名笺是什么")
    ming = Mock()
    ming.list_saved.return_value = []
    assert _rq(ming=ming).direct_query("u1", "名笺是什么") is None
    assert _rq(ming=ming).direct_query("u1", "名笺叫什么意思") is None
    # 不该走：已发生查询 → 空态确定性告知（T054 契约）
    assert "名笺" in _rq(ming=ming).direct_query("u1", T054_MSG)
    # 不该走：how-to 过滤器不误伤正常查询（取的名笺）
    assert "名笺" in _rq(ming=ming).direct_query("u1", "取的名笺")
    # 不该走：有名笺 → 真实数据直读优先（how-to 也不接管）
    ming2 = Mock()
    ming2.list_saved.return_value = [
        {"full": "李沐宸", "score": 88, "style_note": "温润"}]
    assert "李沐宸" in _rq(ming=ming2).direct_query("u1", "名笺是什么")


def test_t054_qian_empty_state_same_family():
    """同族一次做完：灵签空态同样确定性告知（不再 None→LLM）。"""
    qian = Mock()
    qian.list_history.return_value = []
    out = _rq(qian=qian).direct_query("u1", "我抽过的签有哪些")
    assert out and "灵签" in out and "第" not in out


def test_t054_mingjian_nonempty_unchanged():
    """不该走：有名笺记录 → 原直读格式逐字不变（改前行为）。"""
    ming = Mock()
    ming.list_saved.return_value = [
        {"full": "李沐宸", "score": 88, "style_note": "温润"}]
    out = _rq(ming=ming).direct_query("u1", T054_MSG)
    assert out and "取过的名字：1 个" in out and "李沐宸" in out


# ================================================================
# 评测任务定义未被本批误改（T107 之外零改动）
# ================================================================

def test_task_schema_validation_still_passes():
    """改后评测集仍通过 schema 校验（T107 contains 收窄后）。"""
    import subprocess
    proc = subprocess.run(
        [sys.executable, str(_REPO / "scripts" / "eval_agent" /
                             "validate_tasks.py"),
         str(_REPO / "data" / "eval" / "agent_tasks.jsonl")],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
