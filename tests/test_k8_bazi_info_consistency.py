# -*- coding: utf-8 -*-
"""k8：bazi_info 画像层一致性修复批（2026-09-05，21:44 事故根因）。

事故：W1 G1 自愈「dict(旧行) 起手、只覆写不等 birth 键、保留旧行 bazi 键」
→ persons(当时错 1995-03-28 男 长春) 抄进 bazi_info，08-16 污染的他人
「2026-08-18 0点盘 丙午丙申甲子甲子」四柱被原样带到 09-04 → 画像层出现
「1995 出生 + 非本人四柱」畸形行（users.bazi_info 在 bazi_info 中携带
bazi 键=四柱键）。修复 = A 自愈全量重建（丢弃非 birth 键）+ B dao 写侧
一致性守卫（birth 键与 bazi 四柱矛盾 → 丢弃 bazi 键）+ C 显示层只读
persons/匹配 chart_records（get_user_birth_profile_full）。

隔离：全部用 tmp_path 真实 SQLite（UserDAO/PersonDAO/ChartDAO 同库同生产
形态），零网络零 LLM（守卫复算 = BaziEngine 纯规则零 LLM，~26ms/次），
不写生产库（/home/a/data、/mnt/d）与 data/memory。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
import logging  # noqa: E402

from src.storage.birth_profile import (  # noqa: E402
    get_user_birth_profile, get_user_birth_profile_full,
)
from src.storage.dao import UserDAO  # noqa: E402
from src.storage.person_dao import PersonDAO  # noqa: E402
from src.storage.chart_dao import ChartDAO  # noqa: E402

# 21:44 事故实锤四柱 = 2026-08-18 0点(子时)排盘（他人/择时盘）
POLLUTED_PILLARS = ["丙午", "丙申", "甲子", "甲子"]


def _real_db(tmp_path):
    """同库三 DAO（与生产一致：persons/bazi_info/chart_records 同 db 文件）。"""
    db = str(tmp_path / "u.db")
    dao = UserDAO(db)
    pdao = PersonDAO(db)
    return dao, pdao, db


def _mk_person(pdao, user_id, birth):
    """建默认命主（birth 为 person 字段形态：birth_year 等）。"""
    return pdao.create_person(
        user_id, name="我", relation="自己", is_default=True, birth=birth)


@pytest.fixture(autouse=True)
def _clean_logger_state():
    yield


# ================================================================
# 1) 21:44 事故复现：W1 自愈 → 全量重建、丢弃 bazi 键、warning、persons 未动
# ================================================================

def test_k8_accident_rebuild_drops_polluted_pillars(tmp_path, caplog):
    """事故复现：persons=1995-03-28 9点男长春 + bazi_info 残留他人盘四柱
    （丙午丙申甲子甲子，2026-08-18 0点盘）→ 读路径自愈后：
    - bazi_info 以 persons 全量重建（year=1995 等 8 键）；
    - 无 bazi 键残留（旧行四柱被丢弃，四柱只属于 chart_records）；
    - logger.warning 含 user_id；
    - persons 未被触碰（birth 原样）。"""
    dao, pdao, _ = _real_db(tmp_path)
    _mk_person(pdao, "u21", {
        "gender": "男", "birth_year": 1995, "birth_month": 3, "birth_day": 28,
        "birth_hour": 9, "birth_minute": 0, "calendar": "solar",
        "city": "吉林省长春市"})
    # 读前脏态 = 事故 B 库既有行（2026-08-18 0点 北京 unknown + 他人盘四柱）
    dao.save_user_bazi("u21", {
        "year": 2026, "month": 8, "day": 18, "hour": 0, "minute": 0,
        "city": "北京", "gender": "unknown", "bazi": list(POLLUTED_PILLARS),
    })
    with caplog.at_level(logging.WARNING, logger="src.storage.birth_profile"):
        saved = get_user_birth_profile(dao, "u21")
    assert saved is not None
    # persons 优先读取不受影响
    assert saved["year"] == 1995 and saved["gender"] == "男"
    assert saved["city"] == "吉林省长春市"
    # 自愈后行 = persons 全量重建，且无 bazi 键
    row = dao.get_user_bazi("u21")
    assert row["year"] == 1995 and row["month"] == 3 and row["day"] == 28
    assert row["hour"] == 9 and row["city"] == "吉林省长春市"
    assert row["gender"] == "男"
    assert "bazi" not in row, "旧行 bazi 四柱键必须被丢弃，不得残留"
    # warning 打过且含 user_id
    assert any("u21" in r.message and "自愈" in r.message
               for r in caplog.records), [r.message for r in caplog.records]
    # persons 未动
    p = pdao.list_persons("u21")[0]
    assert p["birth_year"] == 1995 and p["gender"] == "男"
    assert p["city"] == "吉林省长春市"


def test_k8_heal_aligns_dirty_bazi_info_to_fixed_persons(tmp_path):
    """persons 已修为真实档案 1999 + bazi_info 仍是 1995 脏值（事故 09-04
    「persons 修好、画像层没对齐」形态）→ 读后 bazi_info 对齐 1999 且收敛
    （二次读取不再写）。"""
    dao, pdao, _ = _real_db(tmp_path)
    _mk_person(pdao, "u2", {
        "gender": "男", "birth_year": 1999, "birth_month": 3, "birth_day": 28,
        "birth_hour": 9, "birth_minute": None, "calendar": "lunar",
        "city": "吉林省长春市"})
    dao.save_user_bazi("u2", {
        "year": 1995, "month": 3, "day": 28, "hour": 9, "minute": 0,
        "city": "吉林省长春市", "gender": "男",
        "bazi": list(POLLUTED_PILLARS),
    })
    out = get_user_birth_profile(dao, "u2")
    assert out["year"] == 1999 and out["calendar"] == "lunar"
    row = dao.get_user_bazi("u2")
    assert row["year"] == 1999 and row["month"] == 3 and row["day"] == 28
    assert row["calendar"] == "lunar"          # lunar 标记随 persons 回写
    assert "bazi" not in row
    # 收敛：再读一次（persons 不变 → 不再写）
    import json
    before = json.dumps(row, sort_keys=True, ensure_ascii=False)
    get_user_birth_profile(dao, "u2")
    after = json.dumps(dao.get_user_bazi("u2"), sort_keys=True,
                       ensure_ascii=False)
    assert after == before


def test_k8_heal_writes_birth_info_when_bazi_info_missing(tmp_path):
    """persons-only：bazi_info 缺失 → 首建 8 键（无 bazi 键、无日志告警噪音
    需求：首建 info 级）。"""
    dao, pdao, _ = _real_db(tmp_path)
    _mk_person(pdao, "u3", {
        "gender": "女", "birth_year": 1999, "birth_month": 3, "birth_day": 28,
        "birth_hour": 9, "birth_minute": None, "calendar": "lunar",
        "city": "长春"})
    get_user_birth_profile(dao, "u3")
    row = dao.get_user_bazi("u3")
    assert row["year"] == 1999 and row["gender"] == "女"
    assert row["calendar"] == "lunar"
    assert "bazi" not in row


# ================================================================
# 2) dao 写侧一致性守卫（B）：矛盾丢弃 / 一致通过 / lunar 同口径
# ================================================================

def _engine_pillars(year, month, day, hour, minute, city, gender):
    from src.engines.bazi import BaziEngine
    return list(BaziEngine().calculate(
        year, month, day, hour, minute, city, gender).bazi)


def test_k8_guard_discards_contradictory_bazi_key(tmp_path, caplog):
    """守卫：birth 键（1995-03-28 9点长春男）与 bazi 四柱（2026-08-18 盘）
    矛盾 → 落库行丢弃 bazi 键 + warning 含 user_id。"""
    dao, _, _ = _real_db(tmp_path)
    with caplog.at_level(logging.WARNING, logger="src.storage.dao"):
        dao.save_user_bazi("g1", {
            "year": 1995, "month": 3, "day": 28, "hour": 9, "minute": 0,
            "city": "吉林省长春市", "gender": "男",
            "bazi": list(POLLUTED_PILLARS),
        })
    row = dao.get_user_bazi("g1")
    assert row["year"] == 1995                       # birth 键原样落库
    assert "bazi" not in row, "矛盾四柱必须被丢弃"
    assert any("g1" in r.message and "丢弃" in r.message
               for r in caplog.records), [r.message for r in caplog.records]


def test_k8_guard_keeps_consistent_bazi(tmp_path, caplog):
    """守卫：birth 键与 bazi 四柱一致（真实引擎同参复算）→ 原样通过
    （不误伤正常排盘落库，W2/W3 主链行为不变）。"""
    dao, _, _ = _real_db(tmp_path)
    pillars = _engine_pillars(1999, 5, 13, 9, 0, "吉林省长春市", "男")
    assert pillars == ["己卯", "己巳", "乙丑", "辛巳"]  # R2-5 问真锚点
    with caplog.at_level(logging.WARNING, logger="src.storage.dao"):
        dao.save_user_bazi("g2", {
            "year": 1999, "month": 5, "day": 13, "hour": 9, "minute": 0,
            "city": "吉林省长春市", "gender": "男", "bazi": list(pillars),
        })
    row = dao.get_user_bazi("g2")
    assert row["bazi"] == pillars
    assert not [r for r in caplog.records if "丢弃" in r.message]


def test_k8_guard_lunar_write_same_caliber_as_R2_5(tmp_path):
    """守卫 lunar 口径：落库 birth 键保留原始农历 y/m/d + calendar=lunar，
    bazi 按转公历（1999-03-28 lunar = 1999-05-13 solar）复算一致 → 不误伤
    （R2-5/R2-6 写路径同口径：存储=原始输入事实源）。"""
    dao, _, _ = _real_db(tmp_path)
    solar_pillars = _engine_pillars(1999, 5, 13, 9, 0, "吉林省长春市", "男")
    dao.save_user_bazi("g3", {
        "year": 1999, "month": 3, "day": 28, "hour": 9, "minute": 0,
        "city": "吉林省长春市", "gender": "男", "calendar": "lunar",
        "bazi": list(solar_pillars),
    })
    row = dao.get_user_bazi("g3")
    assert row["bazi"] == solar_pillars            # 未被误丢
    assert row["calendar"] == "lunar"


def test_k8_guard_skips_birth_only_writes(tmp_path):
    """守卫不介入无 bazi 键写入（/api/user/bazi POST、自愈重建等 birth-only）。"""
    dao, _, _ = _real_db(tmp_path)
    dao.save_user_bazi("g4", {
        "year": 1999, "month": 3, "day": 28, "hour": 9, "minute": None,
        "city": "长春", "gender": "女", "calendar": "lunar"})
    row = dao.get_user_bazi("g4")
    assert row["year"] == 1999 and row["gender"] == "女"


# ================================================================
# 3) 显示层只读 persons + 匹配 chart_records（C，get_user_birth_profile_full）
# ================================================================

def test_k8_full_profile_persons_plus_matching_chart(tmp_path):
    """persons + birth 匹配的 chart_records（self 盘）→ bazi/盘面扩展齐；
    即使 bazi_info 旧行带他人盘四柱也不影响（full 不读 bazi_info.bazi）。"""
    dao, pdao, db = _real_db(tmp_path)
    _mk_person(pdao, "f1", {
        "gender": "男", "birth_year": 1995, "birth_month": 3, "birth_day": 28,
        "birth_hour": 9, "birth_minute": 0, "calendar": "solar",
        "city": "吉林省长春市"})
    # 自愈重建后 bazi_info 无 bazi；chart_records 落 self 盘
    get_user_birth_profile(dao, "f1")
    chart_dao = ChartDAO(db)
    chart_dao.save_chart("f1", 1, {
        "year": 1995, "month": 3, "day": 28, "hour": 9, "minute": 0,
        "city": "吉林省长春市", "gender": "男", "calendar": "solar"},
        {"bazi": ["乙亥", "己卯", "戊午", "丁巳"], "day_master": "戊",
         "geju": "正官格"})
    full = get_user_birth_profile_full(dao, "f1", chart_dao=chart_dao)
    assert full["year"] == 1995 and full["gender"] == "男"
    assert full["bazi"] == ["乙亥", "己卯", "戊午", "丁巳"]
    assert full["day_master"] == "戊" and full["geju"] == "正官格"


def test_k8_full_profile_ignores_unmatched_other_person_chart(tmp_path):
    """persons + 不匹配 chart（他人/择时盘：birth 2026-08-18 vs 档案
    1995-03-28；事故形态）→ 无 bazi 键输出（显示层绝不消费错配四柱）。"""
    dao, pdao, db = _real_db(tmp_path)
    _mk_person(pdao, "f2", {
        "gender": "男", "birth_year": 1995, "birth_month": 3, "birth_day": 28,
        "birth_hour": 9, "birth_minute": 0, "calendar": "solar",
        "city": "吉林省长春市"})
    chart_dao = ChartDAO(db)
    chart_dao.save_chart("f2", None, {
        "year": 2026, "month": 8, "day": 18, "hour": 0, "minute": 0,
        "city": "北京", "gender": "unknown", "calendar": "solar"},
        {"bazi": list(POLLUTED_PILLARS), "day_master": "甲"})
    full = get_user_birth_profile_full(dao, "f2", chart_dao=chart_dao)
    assert full["year"] == 1995                      # birth 照常（persons）
    assert "bazi" not in full, "错配盘四柱不得进入显示层"
    assert "day_master" not in full


def test_k8_full_profile_legacy_bazi_info_only_row(tmp_path):
    """bazi_info-only 旧行（无 persons）→ birth 键兜底可读，但行内 bazi 键
    不向显示层输出（无匹配 chart_records 时）。"""
    dao, _, _ = _real_db(tmp_path)
    dao.save_user_bazi("f3", {
        "year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
        "city": "北京", "gender": "男", "calendar": "solar",
        "bazi": list(POLLUTED_PILLARS),
    })
    full = get_user_birth_profile_full(dao, "f3")
    assert full["year"] == 1990 and full["gender"] == "男"
    assert "bazi" not in full


def test_k8_full_profile_none_when_no_data(tmp_path):
    dao, _, _ = _real_db(tmp_path)
    assert get_user_birth_profile_full(dao, "f-none") is None


# ================================================================
# 4) 端点级：维护/画像显示 GET /api/user/profile（C1 事故显示面）
# ================================================================

def _profile_api(tmp_path, name):
    """挂 src.main app + user_api 真实 DAO 装配（仿 test_profile_consistency）。"""
    from fastapi.testclient import TestClient  # noqa: E402
    from src.main import app  # noqa: E402
    from src.api import user as user_api  # noqa: E402
    from src.security.auth import AuthHandler, JWTHandler, set_auth_handler  # noqa: E402
    db = str(tmp_path / f"{name}.db")
    udao = UserDAO(db)
    user_api.setup(udao, None, AuthHandler())
    set_auth_handler(AuthHandler())

    def _headers(uid):
        return {"Authorization": "Bearer " + JWTHandler(
            os.environ["JWT_SECRET_KEY"]).create_token(uid)}
    return TestClient(app), udao, db, _headers


def test_k8_profile_page_shows_persons_not_stale_bazi_info(tmp_path):
    """事故显示面（用户实诉「维护档案显示1995年」）：persons 已修 1999、
    users.bazi_info 仍是 1995 脏值（含他人盘四柱）→ GET /api/user/profile
    必须返回 persons 1999，bazi_info 不含 bazi 键，has_bazi 不以旧行
    bazi 键为真（四柱只属 chart_records，未排盘/无匹配盘 → False）。"""
    client, udao, db, headers = _profile_api(tmp_path, "pf_acc")
    uid = "wx_k8_prof_acc"
    pdao = PersonDAO(db)
    pdao.create_person(uid, name="我", relation="自己", is_default=True, birth={
        "gender": "男", "birth_year": 1999, "birth_month": 3, "birth_day": 28,
        "birth_hour": 9, "birth_minute": None, "calendar": "lunar",
        "city": "吉林省长春市"})
    udao.save_user_bazi(uid, {   # 事故脏态（21:44 写入形态）
        "year": 1995, "month": 3, "day": 28, "hour": 9, "minute": 0,
        "city": "吉林省长春市", "gender": "男",
        "bazi": list(POLLUTED_PILLARS),
    })
    r = client.get("/api/user/profile", headers=headers(uid))
    assert r.status_code == 200, r.text
    body = r.json()
    info = body["bazi_info"]
    assert info["year"] == 1999, "维护页必须显示 persons 真实档案（1999），"
    "不得显示 users.bazi_info 旧值 1995"
    assert info["calendar"] == "lunar"
    assert "bazi" not in info, "bazi_info 响应不得携带画像 bazi 键"
    assert body["has_bazi"] is False       # 无匹配 chart_records → 不假称有盘
    assert body["bazi_label"] == ""


def test_k8_profile_page_pillars_from_matching_chart_records(tmp_path):
    """排盘落库（chart_records）后：维护页 has_bazi/bazi_label 以匹配盘四柱
    为真（不读 users.bazi_info.bazi）。"""
    client, udao, db, headers = _profile_api(tmp_path, "pf_chart")
    uid = "wx_k8_prof_chart"
    pdao = PersonDAO(db)
    pdao.create_person(uid, name="我", relation="自己", is_default=True, birth={
        "gender": "男", "birth_year": 1999, "birth_month": 5, "birth_day": 13,
        "birth_hour": 9, "birth_minute": 0, "calendar": "solar",
        "city": "吉林省长春市"})
    # W3 同形态：bazi_info 写一致盘（守卫通过）+ chart_records 落盘
    pillars = _engine_pillars(1999, 5, 13, 9, 0, "吉林省长春市", "男")
    udao.save_user_bazi(uid, {
        "year": 1999, "month": 5, "day": 13, "hour": 9, "minute": 0,
        "city": "吉林省长春市", "gender": "男", "bazi": list(pillars)})
    ChartDAO(db).save_chart(uid, 1, {
        "year": 1999, "month": 5, "day": 13, "hour": 9, "minute": 0,
        "city": "吉林省长春市", "gender": "男", "calendar": "solar"},
        {"bazi": list(pillars), "day_master": pillars[2][0]})
    r = client.get("/api/user/profile", headers=headers(uid))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_bazi"] is True
    assert body["bazi_info"]["year"] == 1999
    assert body["bazi_info"].get("calendar", "solar") == "solar"
