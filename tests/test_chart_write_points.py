"""对话数据复用体系 Task 4：排盘三写入点接入 chart_records。

契约：对话排盘（_save_bazi_records）、工具排盘（_tool_bazi）、表单排盘
（POST /api/paipan）执行后，chart_records 必有对应记录（重看 0 重跑）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
from unittest.mock import Mock  # noqa: E402

from src.bot.handler import MessageHandler  # noqa: E402


def _result():
    """BaziResult 形状的 Mock（四柱/日主/大运/流年/神煞等落库所需字段）。"""
    r = Mock()
    r.bazi = ["庚午", "辛巳", "甲申", "壬申"]
    r.day_master = "甲"
    r.dayun = [["0", "庚辰"]]
    r.liunian = {"2026": "丙午"}
    r.shensha = ["天乙贵人"]
    r.nayin = ["路旁土"]
    r.wuxing = {"木": 2}
    r.shishen = ["比肩"]
    r.geju = "正官格"
    r.yongshen = "甲木"
    r.raw_data = {}
    return r


def _handler(tmp_path):
    """object.__new__ 手工装配的 MessageHandler（测试路径，不跑 __init__）。"""
    from src.storage.chart_dao import ChartDAO
    h = object.__new__(MessageHandler)
    dao = Mock()
    dao.db_path = str(tmp_path / "p.db")      # 与 _sync_person_profile 同源
    h.dao = dao
    h.memory_system = None
    h._analysis_facts = {}
    h._citations = {}
    h.chart_dao = ChartDAO(str(tmp_path / "c.db"))
    return h


def _person_id_of(db_path: str, user_id: str):
    """直查 chart_records 的 person_id（ChartDAO 行转 dict 不含该列）。"""
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT person_id FROM chart_records WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user_id,)).fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------- 对话排盘
def test_save_bazi_records_writes_chart(tmp_path):
    """对话排盘：_save_bazi_records 落库后 chart_records 有记录且 bazi 正确。"""
    from src.storage.person_dao import PersonDAO
    h = _handler(tmp_path)
    birth = {"year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
             "city": "北京", "gender": "男"}
    h._save_bazi_records(_result(), birth, "帮我看看八字", "u1")
    r = h.chart_dao.get_latest_chart("u1")
    assert r is not None
    assert r["bazi_json"]["bazi"] == ["庚午", "辛巳", "甲申", "壬申"]
    assert r["birth"]["year"] == 1990
    # 默认命主也建了（同一 dao.db_path），subject=self 的 person_id 已挂上
    p = PersonDAO(str(tmp_path / "p.db")).get_default_person("u1")
    assert p["birth_year"] == 1990
    assert _person_id_of(str(tmp_path / "c.db"), "u1") == p["id"]


def test_save_bazi_records_subject_other_writes_chart(tmp_path):
    """subject=other（帮他人排盘）：也落库，但 person_id=None（归属本人名下）。"""
    h = _handler(tmp_path)
    h._analysis_facts = {"u3": {"subject": "other", "relation": "爸爸"}}
    birth = {"year": 1962, "month": 8, "day": 15, "hour": 9, "minute": 0,
             "city": "北京", "gender": "男"}
    h._save_bazi_records(_result(), birth, "帮我爸看看", "u3")
    r = h.chart_dao.get_latest_chart("u3")
    assert r is not None
    assert r["bazi_json"]["bazi"] == ["庚午", "辛巳", "甲申", "壬申"]
    assert r["birth"]["year"] == 1962
    assert _person_id_of(str(tmp_path / "c.db"), "u3") is None


# ---------------------------------------------------------------- 工具排盘
def test_tool_bazi_writes_chart(tmp_path):
    """工具排盘：_tool_bazi 成功路径落库 chart_records（与对话排盘同口径）。"""
    from src.engines.bazi import BaziEngine
    h = _handler(tmp_path)
    h.engine = BaziEngine()
    tr = h._tool_bazi("1990年5月20日 15点 北京 男", "u2")
    assert tr.ok
    r = h.chart_dao.get_latest_chart("u2")
    assert r is not None
    # 真太阳时修正后引擎确定性输出（北京 15:00 → 未时；与对话排盘同源同口径）
    assert r["bazi_json"]["bazi"] == ["庚午", "辛巳", "乙酉", "癸未"]
    assert r["bazi_json"]["day_master"] == "乙木"
    assert r["birth"]["gender"] == "男"


# ---------------------------------------------------------------- 表单排盘（API）
def test_paipan_api_writes_chart(tmp_path):
    """表单排盘：POST /api/paipan 成功后 chart_records 有记录（本人 uid 名下）。

    YAN 锚点盘（闫海洋 1999-05-13 11:25 北京 男）：己卯 己巳 乙丑 壬午。
    birthHour=6 为时辰序号（午时 → 归一化时钟小时 11）。
    """
    from fastapi.testclient import TestClient
    from src.main import app
    from src.api import paipan as paipan_api
    from src.engines.bazi import BaziEngine
    from src.security.auth import AuthHandler, JWTHandler, set_auth_handler
    from src.storage.chart_dao import ChartDAO

    db_path = str(tmp_path / "api.db")
    paipan_api.setup(BaziEngine())
    paipan_api.setup_db(db_path)
    set_auth_handler(AuthHandler())
    uid = "u_paipan_chart_1"
    token = JWTHandler("test-secret-key-32-bytes-long!!").create_token(uid)
    r = TestClient(app).post("/api/paipan", json={
        "birthYear": 1999, "birthMonth": 5, "birthDay": 13,
        "birthHour": 6, "minute": 25, "gender": "male", "city": "北京",
    }, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    rec = ChartDAO(db_path).get_latest_chart(uid)
    assert rec is not None
    assert rec["bazi_json"]["bazi"] == ["己卯", "己巳", "乙丑", "壬午"]
    assert rec["birth"]["year"] == 1999
    assert rec["birth"]["hour"] == 11
    assert rec["birth"]["gender"] == "男"
