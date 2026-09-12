#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k39 S3 测试：合盘「单档补全」——允许档案代表本人（显式标注 + 可手动改）。

改前：合盘一方缺信息 → 一律吐同一句引导卡（「想看看你们合不合？给我双方
生辰即可直接测算…」）——本人信息明明已在档案里，仍要用户再输一遍
（E6 T040/T041，hehun/P1）。改后：本人缺失时用**默认命主档案**补全，
显式标注来源、手填优先（覆盖档案），只差对方时确定性补问。

覆盖（brief S3 三个测试要求 + 双入口同一口径）：
1. 档案补全生效：本人取档案（T040 形态：消息零出生信息 → hehun 工具可达、
   birth_a=档案、birth_b 留空补问）；
2. 手填覆盖生效：消息里给了本人信息 → 以手填为准（不带「来自档案」标注）；
3. 未建档时行为不变：无档案 + 无信息 → 改前引导卡逐字不变；
4. 单档补全直接成局（T041 形态：本人从档案 + 对方从消息 → 直接出合婚卡，
   不得要求重复提供本人信息）；
5. `_scene_hehun_fallback` 与 `_handle_hehun` **同一实现同一文案**
   （服务端不两套）。

红线：真实引擎（BaziEngine/HehunEngine）零 LLM 调用；档案访问器按仓库既有
测试口径（tests/test_g1_gender_contract.py）可 inject；另有真实 persons 链
一例证明档案读数真通。
"""
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from src.bot.handler import MessageHandler  # noqa: E402

ARCHIVE = {"year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 30,
           "city": "北京", "gender": "男", "calendar": "solar"}
ARCHIVE_TEXT = "1990年5月20日 15时30分 北京 男"
# E6 任务语料（逐字）
T040_MSG = "帮我合个婚，看看我们配不配"
T041_MSG = "我和一个1992年10月1日 上海出生的女孩子合不合"
T039_MSG = "男1990年5月20日 15:30 北京，和女1992年10月1日 上海，我们合不合"
NO_ARCHIVE_MSG = "帮我合婚"


def _handler(archive=None, tmp_path=None):
    """真实引擎装配（合婚工具真跑）；档案访问器可 inject（既有测试口径）。"""
    from src.engines.bazi import BaziEngine
    from src.engines.hehun import HehunEngine

    mock_llm = Mock()
    mock_llm.api_key = "test-key"
    mock_llm.model = "deepseek-flash"
    mock_llm.provider = "glm"
    mock_dao = Mock()
    mock_dao.db_path = ""
    mock_session = Mock()
    mock_session.get_context_for_llm.return_value = []
    mock_session.add_message.return_value = None

    h = MessageHandler(
        engine=BaziEngine(), ziwei_engine=Mock(), liuyao_engine=Mock(),
        fengshui_engine=Mock(), mianxiang_engine=Mock(), zeri_engine=Mock(),
        dream_engine=Mock(), hehun_engine=HehunEngine(),
        qimen_engine=Mock(), xingming_engine=Mock(),
        retriever=Mock(), llm=mock_llm, dao=mock_dao,
        session_dao=mock_session)
    h.memory_system = None  # 测试隔离：不写 data/memory 用户记忆文件
    h._get_user_birth_profile = Mock(
        return_value=(dict(archive) if archive else None))
    return h


def _spy_tool_calls(h):
    """记录 `_execute_tool_call` 的 (name, params)，同时真实执行。"""
    seen = []
    orig = h._execute_tool_call

    def spy(name, params, user_id, user_question=""):
        seen.append((name, params))
        return orig(name, params, user_id, user_question=user_question)

    h._execute_tool_call = spy
    return seen


# ================================================================
# 1. 归属判定（可判定标记）
# ================================================================

def test_message_side_marker_rule():
    h = _handler()
    assert h._hehun_message_side(T041_MSG) == "other"   # 「一个」紧邻出生信息
    assert h._hehun_message_side("我1990年5月20日 北京 男，和 TA 合不合") == "self"
    assert h._hehun_message_side(T040_MSG) == "self"    # 无出生信息
    assert h._hehun_message_side("看看我和他合不合") == "self"


# ================================================================
# 2. 单档补全：档案补全 / 手填覆盖 / 未建档不变
# ================================================================

def test_fill_uses_archive_when_self_missing():
    """T040 形态：消息零出生信息 → 本人取档案，对方留空待补问。"""
    h = _handler(archive=ARCHIVE)
    fill = h._hehun_single_fill(T040_MSG, "u1")
    assert fill["birth_a"] == ARCHIVE_TEXT
    assert fill["birth_b"] == ""
    assert fill["source_a"] == "archive"


def test_fill_other_from_message_self_from_archive():
    """T041 形态：对方从消息提取、本人从档案补全（不要求重复提供本人）。"""
    h = _handler(archive=ARCHIVE)
    fill = h._hehun_single_fill(T041_MSG, "u1")
    assert fill["birth_a"] == ARCHIVE_TEXT and fill["source_a"] == "archive"
    assert fill["birth_b"].startswith("1992年10月1日") and "女" in fill["birth_b"]


def test_fill_manual_self_overrides_archive():
    """手填覆盖生效：消息里的本人信息以手填为准（不带档案来源标注）。"""
    h = _handler(archive=ARCHIVE)
    fill = h._hehun_single_fill("我1988年8月8日 8时 上海 女，合个婚", "u1")
    assert fill["birth_a"].startswith("1988年8月8日")
    assert "女" in fill["birth_a"]
    assert fill["source_a"] == "message"
    assert fill["birth_b"] == ""


def test_fill_both_from_message_unchanged():
    """双方都由消息给出 → 与改前一致（手填优先，不补档案）。"""
    h = _handler(archive=ARCHIVE)
    fill = h._hehun_single_fill(T039_MSG, "u1")
    assert fill["source_a"] == "message"
    assert fill["birth_a"].startswith("1990年5月20日")
    assert fill["birth_b"].startswith("1992年10月1日")


def test_fill_without_archive_and_without_info_is_empty():
    """未建档时行为不变：全空 → 调用方保持改前引导文案。"""
    h = _handler(archive=None)
    assert h._hehun_single_fill(NO_ARCHIVE_MSG, "u1") == {
        "birth_a": "", "birth_b": "", "source_a": ""}


def test_fill_reads_real_persons_archive(tmp_path):
    """真实链路：persons 默认档案 → 补全（不是只靠 Mock 读数）。"""
    from src.storage.dao import UserDAO
    from src.storage.chart_dao import ChartDAO
    from src.storage.person_dao import PersonDAO

    h = object.__new__(MessageHandler)
    h.dao = UserDAO(str(tmp_path / "u.db"))
    h.chart_dao = ChartDAO(str(tmp_path / "c.db"))
    PersonDAO(str(tmp_path / "u.db")).create_person(
        "u_real", name="我", relation="自己", is_default=True,
        birth={"gender": "male", "birth_year": 1990, "birth_month": 5,
               "birth_day": 20, "birth_hour": 15, "birth_minute": 30,
               "calendar": "solar", "city": "北京"})
    fill = h._hehun_single_fill(T040_MSG, "u_real")
    assert fill["birth_a"] == ARCHIVE_TEXT and fill["source_a"] == "archive"


# ================================================================
# 3. 场景兜底：工具可达 + 显式标注 + 不静默降级
# ================================================================

def test_scene_archive_fill_calls_hehun_tool_and_labels_source():
    """T040：一方（本人）从档案补全 → hehun 工具可达 + 显式标注 + 补问对方。"""
    h = _handler(archive=ARCHIVE)
    calls = _spy_tool_calls(h)
    reply = h._scene_hehun_fallback(T040_MSG, "u1")
    assert calls and calls[0][0] == "合婚"          # 工具可达（L1 记 hehun）
    assert calls[0][1]["birth_a"] == ARCHIVE_TEXT   # 本人来自档案
    assert calls[0][1]["birth_b"] == ""             # 对方留空 → 工具补问
    assert h.HEHUN_SELF_FROM_ARCHIVE_LABEL in reply  # 「本人（来自档案）」
    assert ARCHIVE_TEXT in reply                     # 不必再输一遍自己
    assert "另一方" in reply                         # 补问对方（不静默降级）


def test_scene_single_fill_produces_full_result():
    """T041：本人档案 + 对方消息 → 直接出合婚卡（不再要求本人信息）。"""
    h = _handler(archive=ARCHIVE)
    calls = _spy_tool_calls(h)
    reply = h._scene_hehun_fallback(T041_MSG, "u1")
    assert calls and calls[0][1]["birth_a"] == ARCHIVE_TEXT
    assert calls[0][1]["birth_b"].startswith("1992年10月1日")
    assert "本人（来自档案）" in reply
    assert "五行" in reply or "评分" in reply or "婚配" in reply


def test_scene_paired_message_unchanged_no_archive_label():
    """双方都在消息里 → 改前行为（工具卡，无「来自档案」标注）。"""
    h = _handler(archive=ARCHIVE)
    calls = _spy_tool_calls(h)
    reply = h._scene_hehun_fallback(T039_MSG, "u1")
    assert calls and calls[0][1]["birth_b"].startswith("1992年10月1日")
    assert "本人（来自档案）" not in reply


def test_scene_without_archive_keeps_legacy_guide_card():
    """未建档时行为不变：改前引导卡逐字不变（不新造文案）。"""
    h = _handler(archive=None)
    calls = _spy_tool_calls(h)
    reply = h._scene_hehun_fallback(NO_ARCHIVE_MSG, "u1")
    assert calls == []                     # 零工具调用（既有契约）
    assert "双人合盘" in reply and "/pages/hehun/hehun" in reply
    assert "本人（来自档案）" not in reply


# ================================================================
# 4. 两个入口同一口径（服务端不两套）
# ================================================================

def test_handle_hehun_same_fill_and_same_copy():
    """LLM 入口（_handle_hehun）与场景兜底同实现同文案。"""
    h = _handler(archive=ARCHIVE)
    calls = _spy_tool_calls(h)
    ask = h._handle_hehun(T040_MSG, "u1")
    assert "本人（来自档案）" in ask and "另一方" in ask
    assert calls and calls[0][1] == {"birth_a": ARCHIVE_TEXT, "birth_b": ""}
    # 与场景兜底逐字相同（同一出口 `_hehun_tool_reply`）
    h_scene = _handler(archive=ARCHIVE)
    _spy_tool_calls(h_scene)
    assert ask == h_scene._scene_hehun_fallback(T040_MSG, "u1")
    # T041 形态走同一补全 → 直接成局
    h2 = _handler(archive=ARCHIVE)
    _spy_tool_calls(h2)
    card = h2._handle_hehun(T041_MSG, "u1")
    assert "本人（来自档案）" in card
    assert "五行" in card or "评分" in card or "婚配" in card


def test_handle_hehun_without_archive_keeps_legacy_card():
    """未建档时 `_handle_hehun` 行为不变（改前引导卡逐字）。"""
    h = _handler(archive=None)
    reply = h._handle_hehun(NO_ARCHIVE_MSG, "u1")
    assert "给我双方生辰即可直接测算" in reply
    assert "本人（来自档案）" not in reply


def test_label_constant_is_single_source_for_server_and_client():
    """服务端标注文案 = 小程序合盘页同一串（口径一致，不两套）。"""
    wxml = (_REPO / "miniprogram" / "pages" / "hehun" / "hehun.wxml").read_text(
        encoding="utf-8")
    assert MessageHandler.HEHUN_SELF_FROM_ARCHIVE_LABEL == "本人（来自档案）"
    assert MessageHandler.HEHUN_SELF_FROM_ARCHIVE_LABEL in wxml
    js = (_REPO / "miniprogram" / "pages" / "hehun" / "hehun.js").read_text(
        encoding="utf-8")
    assert "p1FromArchive" in js and "p1FromArchive: false" in js
