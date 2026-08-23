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
