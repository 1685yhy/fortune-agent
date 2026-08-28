"""排盘历史回看（batch3 B3-4）：GET /api/paipan/history + GET /api/paipan/history/{id}.

- GET /api/paipan/history：只显示自己的 · 脱敏摘要（生辰摘要/四柱/日主/一句话结论/
  排盘时间），不含全量盘面（bazi_json 不出现）；limit 默认 50；从未排过盘
  （chart_records 表尚未创建）→ 200 空列表（只读查询，不建表不写库）。
- GET /api/paipan/history/{id}：归属校验（id + user_id 双条件）→ 完整盘面
  （重看 0 重跑：直接回读落库结果，不重新计算、不再落新记录）。
- 隐私红线：未登录 401；他人记录 404（不泄露存在性）。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

from fastapi.testclient import TestClient  # noqa: E402

from src.main import app  # noqa: E402
from src.api import paipan as paipan_api  # noqa: E402
from src.engines.bazi import BaziEngine  # noqa: E402
from src.security.auth import AuthHandler, JWTHandler, set_auth_handler  # noqa: E402
from src.storage.chart_dao import ChartDAO  # noqa: E402

# 闫海洋命例（与 test_paipan_api 同锚点盘：己卯 己巳 乙丑 壬午）
YAN = {
    "birthYear": 1999, "birthMonth": 5, "birthDay": 13,
    "birthHour": 6, "minute": 25, "gender": "male", "city": "北京",
}

SECRET = "test-secret-key-32-bytes-long!!"


def _setup(tmp_path):
    """注入引擎 + 落库路径 + 鉴权（每个测试独立 tmp db）。"""
    paipan_api.setup(BaziEngine())
    paipan_api.setup_db(os.path.join(str(tmp_path), "t.db"))
    set_auth_handler(AuthHandler())
    return TestClient(app)


def _headers(uid="u_hist_1"):
    return {"Authorization": f"Bearer {JWTHandler(SECRET).create_token(uid)}"}


def _post_paipan(client, uid, payload=None):
    return client.post("/api/paipan", json=payload or YAN, headers=_headers(uid))


# ---------------------------------------------------------------- 主流程：本人记录 + 脱敏
def test_history_own_records_desensitized(tmp_path):
    """排一次盘 → 历史列表 1 条脱敏摘要（生辰摘要/四柱/日主/结论/时间），不含全量盘面。"""
    client = _setup(tmp_path)
    assert _post_paipan(client, "u_hist_1").status_code == 200

    r = client.get("/api/paipan/history", headers=_headers("u_hist_1"))
    assert r.status_code == 200, r.text
    records = r.json()["records"]
    assert len(records) == 1
    rec = records[0]

    # 用户视角字段：谁（person_id 供前端解析姓名）/ 四柱 / 日主 / 摘要 / 结论 / 时间
    assert rec["id"] > 0
    assert rec["person_id"] is None          # 表单排盘不绑档案
    assert rec["bazi"] == ["己卯", "己巳", "乙丑", "壬午"]
    assert rec["day_master"] == "乙木"
    assert "八字排盘" in rec["summary"]
    assert "男命" in rec["summary"]
    assert "1999年5月13日" in rec["summary"]
    assert "午时" in rec["summary"]
    assert "北京" in rec["summary"]
    assert "日主乙木" in rec["conclusion"]   # 一句话结论（日主 + 月令旺衰）
    assert "巳月火旺" in rec["conclusion"]
    assert rec["created_at"]
    assert rec["birth"]["gender"] in ("male", "男")

    # 脱敏红线：列表绝不回全量盘面（bazi_json/大运/流年/神煞等）
    assert "bazi_json" not in rec
    assert "chart" not in rec
    for big in ("pillars", "dayun", "liunian_full", "wuxing_energy",
                "shensha_detail", "chenggu", "meta"):
        assert big not in rec


def test_history_user_isolation_and_empty(tmp_path):
    """归属校验：每个用户只能看到自己的记录；新用户/从未排盘 → 空列表。"""
    client = _setup(tmp_path)
    assert _post_paipan(client, "u_a", YAN).status_code == 200
    assert _post_paipan(client, "u_b", YAN).status_code == 200
    assert _post_paipan(client, "u_a", dict(YAN, birthDay=14)).status_code == 200

    ra = client.get("/api/paipan/history", headers=_headers("u_a"))
    rb = client.get("/api/paipan/history", headers=_headers("u_b"))
    assert len(ra.json()["records"]) == 2     # u_a 两条
    assert len(rb.json()["records"]) == 1     # u_b 一条
    # 时间倒序：后排的（5/14）在前
    assert ra.json()["records"][0]["birth"]["day"] == 14

    # 从未排过盘的用户 → 空列表（200，非 404）
    rc = client.get("/api/paipan/history", headers=_headers("u_c"))
    assert rc.status_code == 200
    assert rc.json()["records"] == []


def test_history_no_table_yet(tmp_path):
    """chart_records 表尚未创建（从未排过盘）→ 200 空列表（只读查询兜底，不建表）。"""
    client = _setup(tmp_path)
    r = client.get("/api/paipan/history", headers=_headers("u_fresh"))
    assert r.status_code == 200, r.text
    assert r.json()["records"] == []


def test_history_limit(tmp_path):
    """limit 参数生效（默认 50，显式 limit=2 只回 2 条）。"""
    client = _setup(tmp_path)
    for day in (13, 14, 15):
        assert _post_paipan(client, "u_lim", dict(YAN, birthDay=day)).status_code == 200

    r = client.get("/api/paipan/history", headers=_headers("u_lim"))
    assert len(r.json()["records"]) == 3
    r2 = client.get("/api/paipan/history?limit=2", headers=_headers("u_lim"))
    assert len(r2.json()["records"]) == 2
    assert r2.json()["records"][0]["birth"]["day"] == 15  # 最新在前


def test_history_401_unauthorized(tmp_path):
    """未登录 → 401（require_user 鉴权红线）。"""
    client = _setup(tmp_path)
    r = client.get("/api/paipan/history")
    assert r.status_code == 401
    r2 = client.get("/api/paipan/history/1")
    assert r2.status_code == 401


# ---------------------------------------------------------------- 详情回看：归属校验 + 全字段
def test_history_detail_owner_full_chart(tmp_path):
    """详情接口（归属校验通过）：返回完整盘面（重看 0 重跑，含 pillars/大运/流年）。"""
    client = _setup(tmp_path)
    assert _post_paipan(client, "u_d", YAN).status_code == 200
    rec = client.get("/api/paipan/history", headers=_headers("u_d")).json()["records"][0]

    r = client.get(f"/api/paipan/history/{rec['id']}", headers=_headers("u_d"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == rec["id"]
    assert body["birth"]["year"] == 1999
    assert body["chart"]["bazi"] == ["己卯", "己巳", "乙丑", "壬午"]
    assert body["chart"]["day_master"] == "乙木"
    # 全量盘面在场（与 POST /api/paipan 响应同源落库）
    assert len(body["chart"]["pillars"]) == 4
    assert len(body["chart"]["dayun"]) == 12
    assert len(body["chart"]["liunian_full"]) == 30
    assert body["chart"]["chenggu"]["weight_text"] == "五两五钱"
    assert body["created_at"]


def test_history_detail_not_owner_404(tmp_path):
    """他人记录 → 404（id + user_id 双条件归属校验，不泄露存在性）。"""
    client = _setup(tmp_path)
    assert _post_paipan(client, "u_owner", YAN).status_code == 200
    rec = client.get("/api/paipan/history", headers=_headers("u_owner")).json()["records"][0]

    r = client.get(f"/api/paipan/history/{rec['id']}", headers=_headers("u_other"))
    assert r.status_code == 404
    # 不存在的 id（本人）同样 404
    r2 = client.get("/api/paipan/history/99999", headers=_headers("u_owner"))
    assert r2.status_code == 404


# ---------------------------------------------------------------- 聊天路径（缩减字段）记录
def test_history_chat_path_record_geju(tmp_path):
    """聊天/工具路径落库的缩减记录（无 pillars/wuxing_energy，但有 geju）也可回看。"""
    client = _setup(tmp_path)
    db_path = os.path.join(str(tmp_path), "t.db")
    dao = ChartDAO(db_path)
    dao.save_chart(
        "u_chat", 3,
        {"year": 1988, "month": 8, "day": 20, "hour": 12, "minute": 0,
         "city": "上海", "gender": "男", "calendar": "solar"},
        {"bazi": ["戊辰", "庚申", "乙酉", "壬午"], "day_master": "乙木",
         "geju": "正官格", "shishen": [], "nayin": []})

    recs = client.get("/api/paipan/history", headers=_headers("u_chat")).json()["records"]
    assert len(recs) == 1
    rec = recs[0]
    assert rec["person_id"] == 3                        # 聊天路径挂档案 → 前端可解析姓名
    assert rec["bazi"] == ["戊辰", "庚申", "乙酉", "壬午"]
    assert rec["day_master"] == "乙木"
    assert "日主乙木" in rec["conclusion"]
    assert "正官格" in rec["conclusion"]                # 聊天路径已存 geju → 结论带格局
    assert "午时" in rec["summary"]
    assert "上海" in rec["summary"]

    # 详情回看：缩减记录同样可读（bazi/day_master/geju 在场）
    d = client.get(f"/api/paipan/history/{rec['id']}", headers=_headers("u_chat"))
    assert d.status_code == 200
    assert d.json()["chart"]["geju"] == "正官格"
    assert d.json()["person_id"] == 3
