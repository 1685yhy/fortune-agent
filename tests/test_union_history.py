"""合盘历史记录 — 脱敏归档 + 列表接口（v2026-08-17 PM：合盘记录入口）。

- DAO.get_user_hehun_records：只回 intent='hehun' 且 chart.type='yuan_union'
  的脱敏摘要（不含生辰）；自动解密。
- GET /api/union/history：只显示自己的记录（JWT sub 过滤）。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

from fastapi.testclient import TestClient  # noqa: E402

import src.storage.dao as dao_mod  # noqa: E402
from src.security.auth import AuthHandler, JWTHandler, set_auth_handler  # noqa: E402
from src.storage.dao import UserDAO  # noqa: E402

from src.main import app  # noqa: E402
from src.api import union as union_api  # noqa: E402
from src.engines.bazi import BaziEngine  # noqa: E402
from src.engines.hehun import HehunEngine  # noqa: E402


def _make_dao(tmp_path):
    path = os.path.join(str(tmp_path), "test.db")
    dao_mod._DB_PATH = path
    return UserDAO(path)


def test_dao_hehun_records_filter_and_decrypt(tmp_path):
    dao = _make_dao(tmp_path)
    uid = "u_hist_1"
    # 符合：intent=hehun + yuan_union
    dao.save_consultation(uid, "双人合盘：契合78分（情投意合）",
                          chart_result={
                              "type": "yuan_union", "score": 78, "level": "情投意合",
                              "relation": "恋人", "dimensions": {"wuxing": {"score": 30, "max": 40}},
                          },
                          intent="hehun")
    # 排除：intent=hehun 但 chart 非 yuan_union
    dao.save_consultation(uid, "其他咨询",
                          chart_result={"type": "other", "score": 99}, intent="hehun")
    # 排除：intent 非 hehun
    dao.save_consultation(uid, "八字咨询", chart_result={"type": "yuan_union", "score": 50},
                          intent="bazi")

    records = dao.get_user_hehun_records(uid)
    assert len(records) == 1
    rec = records[0]
    assert rec["chart"]["score"] == 78
    assert rec["chart"]["type"] == "yuan_union"
    assert rec["created_at"]

    # 用户隔离：他人查不到
    assert dao.get_user_hehun_records("u_other") == []


def test_union_history_api(tmp_path):
    dao = _make_dao(tmp_path)
    # 预置一条脱敏记录
    dao.save_consultation("u_api_1", "双人合盘：契合66分（细水长流）",
                          chart_result={"type": "yuan_union", "score": 66, "level": "细水长流",
                                        "relation": "朋友"},
                          intent="hehun")

    # 挂载引擎与 DAO
    union_api.setup(HehunEngine(), BaziEngine(), dao=dao, member_dao=None)
    # JWT_SECRET_KEY 已在模块顶部设置 → AuthHandler 内部 JWTHandler 同密钥
    set_auth_handler(AuthHandler())

    client = TestClient(app)
    token = JWTHandler("test-secret-key-32-bytes-long!!").create_token("u_api_1")
    headers = {"Authorization": f"Bearer {token}"}

    r = client.get("/api/union/history", headers=headers)
    assert r.status_code == 200, r.text
    records = r.json().get("records", [])
    assert len(records) == 1
    assert records[0]["chart"]["score"] == 66

    # 他人 token 看不到
    token2 = JWTHandler("test-secret-key-32-bytes-long!!").create_token("u_api_2")
    r2 = client.get("/api/union/history", headers={"Authorization": f"Bearer {token2}"})
    assert r2.status_code == 200
    assert r2.json().get("records", []) == []

    # 未登录 401
    r3 = client.get("/api/union/history")
    assert r3.status_code == 401
