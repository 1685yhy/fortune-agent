# -*- coding: utf-8 -*-
"""k19 ④-4 档案年份合理性守卫（2026-09-10，保守版——默认仅告警）。

21:44 事故前置根因 A = 默认命主 persons 曾整 2.5 周存错年份（1995 实为
1999）；本批在默认命主行写入侧（PersonDAO.update_person / create_person
顶替既有默认）加年份守卫：birth_year 与既有默认命主差 > 2 且非明示纠正
句式 → 默认仅 logger.warning（绝不拒绝，防误伤真纠正）；YEAR_SHIFT_REJECT
= True 为预留参数位（未来拍板可开拒绝，明示纠正/表单显式提交永远豁免）。

隔离：tmp_path 真实 SQLite（同生产形态），零网络零 LLM。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import logging  # noqa: E402

import pytest  # noqa: E402
from src.storage.person_dao import (  # noqa: E402
    PersonDAO, FORM_EXPLICIT_CTX, YEAR_SHIFT_MAX_GAP, YEAR_SHIFT_REJECT,
    is_correction_text, year_shift_exceeds,
)
from src.storage.dao import UserDAO  # noqa: E402


def _mk(pdao, user_id, year, name="我", relation="自己", is_default=True):
    return pdao.create_person(user_id, name=name, relation=relation,
                              is_default=is_default,
                              birth={"gender": "女",
                                     "birth_year": year, "birth_month": 3,
                                     "birth_day": 28, "birth_hour": 9,
                                     "birth_minute": 55, "calendar": "solar",
                                     "city": "长春"})


# ================================================================
# 判定函数单测（纯逻辑，无 DB）
# ================================================================

def test_year_shift_exceeds_threshold():
    # 1995 vs 1999 = 4 > 2 → 可拦截族（21:44 实锤差值）
    assert year_shift_exceeds(1995, 1999) is True
    assert year_shift_exceeds(1999, 1995) is True
    # 差 1-2 年（年龄推算/年份上下浮动）→ 不触发
    assert year_shift_exceeds(1999, 2000) is False
    assert year_shift_exceeds(1999, 1997) is False
    # 缺值/非法 → 不触发（正常建档/部分档案）
    assert year_shift_exceeds(None, 1999) is False
    assert year_shift_exceeds(1999, None) is False
    assert year_shift_exceeds(0, 1999) is False
    assert year_shift_exceeds("abc", 1999) is False


def test_is_correction_text_markers():
    # 明示纠正句式 → True（豁免告警/拒绝）
    assert is_correction_text("我其实是1999年出生的，不是1995") is True
    assert is_correction_text("之前说错了，应该是1999年") is True
    assert is_correction_text("更正一下，1999年3月28日") is True
    assert is_correction_text("不对，我1999年生的") is True
    # 表单哨兵 → True（用户亲手编辑 = 显式）
    assert is_correction_text(FORM_EXPLICIT_CTX) is True
    # 空/普通声明 → False
    assert is_correction_text("") is False
    assert is_correction_text("帮我排盘，1999年3月28日10点55分上海") is False
    assert is_correction_text("1995年3月28日 广州 女") is False


# ================================================================
# 三态：正常建档 / 真纠正 / 异常大差（DAO 集成）
# ================================================================

def test_state_normal_first_creation_no_warn(tmp_path, caplog):
    """正常建档：无既有默认命主 → 任意年份直接建档、零告警。"""
    dao = UserDAO(str(tmp_path / "u.db"))
    pdao = PersonDAO(str(tmp_path / "u.db"))
    with caplog.at_level(logging.WARNING, logger="src.storage.person_dao"):
        p = _mk(pdao, "u1", 1999)
    assert p is not None and p["birth_year"] == 1999
    assert "④-4" not in caplog.text


def test_state_genuine_correction_ctx_no_warn(tmp_path, caplog):
    """真纠正：既有默认 1995（错），对话含明示纠正句式改 1999 → 零告警写入。"""
    pdao = PersonDAO(str(tmp_path / "u.db"))
    p = _mk(pdao, "u1", 1995)
    with caplog.at_level(logging.WARNING, logger="src.storage.person_dao"):
        p2 = pdao.update_person(
            "u1", p["id"], birth={"birth_year": 1999},
            birth_ctx="我其实是1999年3月28日出生，之前填错了")
    assert p2 is not None and p2["birth_year"] == 1999
    assert "④-4" not in caplog.text
    # 表单显式提交同样豁免（用户亲手编辑档案）
    with caplog.at_level(logging.WARNING, logger="src.storage.person_dao"):
        p3 = pdao.update_person(
            "u1", p["id"], birth={"birth_year": 2001},
            birth_ctx=FORM_EXPLICIT_CTX)
    assert p3 is not None and p3["birth_year"] == 2001
    assert "④-4" not in caplog.text


def test_state_abnormal_big_gap_warns_and_blocks(tmp_path, caplog):
    """异常大差（21:44 族）：既有默认 1999，新写入 1995 无纠正句式 → 告警日志
    + **写入被拒绝**（返回原行、年份仍是 1999）。

    k48（2026-09-15）行为升级：本条原断言"告警但照写"（k19 保守版），产品针对
    用户实机「档案被职场文本污染 → 同会话两张盘」拍板改为**先问后写**——
    storage 层拒绝静默改写，上游 handler 在同一判定（birth_conflict_fields）
    下回一句确认，用户确认后才写。见 tests/test_k48_pollution_guard_ask.py。"""
    pdao = PersonDAO(str(tmp_path / "u.db"))
    p = _mk(pdao, "u1", 1999)
    with caplog.at_level(logging.WARNING, logger="src.storage.person_dao"):
        p2 = pdao.update_person("u1", p["id"],
                                birth={"birth_year": 1995, "birth_month": 3,
                                       "birth_day": 8})
    assert p2 is not None and p2["birth_year"] == 1999, "冲突写入必须被拦下"
    assert "④-4" in caplog.text
    assert "1999" in caplog.text and "1995" in caplog.text
    assert "拒绝" in caplog.text


def test_non_default_person_no_guard(tmp_path, caplog):
    """非默认命主（家人/朋友档案）年份大差 → 不告警（其年份本就可能不同）。"""
    pdao = PersonDAO(str(tmp_path / "u.db"))
    _mk(pdao, "u1", 1999)                       # 默认命主 1999
    friend = pdao.create_person(
        "u1", name="爸爸", relation="父母", is_default=False,
        birth={"gender": "男", "birth_year": 1976, "birth_month": 5,
               "birth_day": 13, "calendar": "solar"})
    with caplog.at_level(logging.WARNING, logger="src.storage.person_dao"):
        p2 = pdao.update_person("u1", friend["id"],
                                birth={"birth_year": 1968})  # 差 8 年
    assert p2 is not None and p2["birth_year"] == 1968
    assert "④-4" not in caplog.text


def test_small_gap_no_warn(tmp_path, caplog):
    """差 ≤ 2 年（含年龄推算年份上下浮动）→ 不告警。"""
    pdao = PersonDAO(str(tmp_path / "u.db"))
    p = _mk(pdao, "u1", 1999)
    with caplog.at_level(logging.WARNING, logger="src.storage.person_dao"):
        p2 = pdao.update_person("u1", p["id"], birth={"birth_year": 2000})
    assert p2 is not None and p2["birth_year"] == 2000
    assert "④-4" not in caplog.text


def test_reject_param_slot(tmp_path, caplog, monkeypatch):
    """可选参数位：YEAR_SHIFT_REJECT=True 时非纠正大差写入被拒绝（返回原行）；
    明示纠正句式 / 表单哨兵 / 差≤2 仍放行——未来拍板开启不误伤。"""
    import src.storage.person_dao as pd
    pdao = PersonDAO(str(tmp_path / "u.db"))
    p = _mk(pdao, "u1", 1999)
    monkeypatch.setattr(pd, "YEAR_SHIFT_REJECT", True)

    # 无纠正大差 → 拒绝（原行返回、年份未变、warning 含「拒绝」）
    with caplog.at_level(logging.WARNING, logger="src.storage.person_dao"):
        back = pdao.update_person("u1", p["id"],
                                  birth={"birth_year": 1995})
    assert back is not None and back["birth_year"] == 1999
    assert "④-4" in caplog.text and "拒绝" in caplog.text

    # 明示纠正（对话句式）→ 拒绝模式仍豁免
    with caplog.at_level(logging.WARNING, logger="src.storage.person_dao"):
        p2 = pdao.update_person("u1", p["id"],
                                birth={"birth_year": 1995},
                                birth_ctx="我之前说错了，其实是1995年")
    assert p2 is not None and p2["birth_year"] == 1995

    # 差 ≤ 2 → 放行
    with caplog.at_level(logging.WARNING, logger="src.storage.person_dao"):
        p3 = pdao.update_person("u1", p["id"], birth={"birth_year": 1997})
    assert p3 is not None and p3["birth_year"] == 1997

    monkeypatch.undo()


def test_create_default_replacing_existing_guard(tmp_path, caplog):
    """create_person(is_default=True) 顶替既有默认命主（无纠正大差）→ 告警但照建；
    明示纠正顶替 → 不告警。"""
    pdao = PersonDAO(str(tmp_path / "u.db"))
    old = _mk(pdao, "u1", 1999, name="我")
    with caplog.at_level(logging.WARNING, logger="src.storage.person_dao"):
        new1 = pdao.create_person(
            "u1", name="我", relation="自己", is_default=True,
            birth={"gender": "女", "birth_year": 1995, "birth_month": 3,
                   "birth_day": 8, "calendar": "solar"})
    assert new1 is not None and new1["is_default"] is True
    assert "④-4" in caplog.text
    caplog.clear()
    # 复原 1999 → 纠正句式顶替零告警
    with caplog.at_level(logging.WARNING, logger="src.storage.person_dao"):
        new2 = pdao.create_person(
            "u1", name="我", relation="自己", is_default=True,
            birth={"gender": "女", "birth_year": 1999, "birth_month": 3,
                   "birth_day": 28, "calendar": "solar"},
            birth_ctx="刚才填错了，我应该是1999年")
    assert new2 is not None and new2["birth_year"] == 1999
    assert "④-4" not in caplog.text
    # 顶替后旧行非默认
    assert pdao.get_person("u1", old["id"])["is_default"] is False
