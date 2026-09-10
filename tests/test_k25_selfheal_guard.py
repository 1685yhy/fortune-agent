# -*- coding: utf-8 -*-
"""k25 ④-6 自愈收口：读路径去除写副作用（2026-09-10）。

依据：docs/superpowers/plans/2026-09-10-k19-profile-migration.md §4
「项 3：④-6 读路径不写库评估」短期项 (a)(b)：

(a) 读路径自愈回写改直写 SQL 镜像（src/storage/person_dao.py
    mirror_bazi_info_to_users，k11c F3 镜像同款）——绕开
    UserDAO.save_user_bazi 的 consultation_count+1 副作用与
    _guard_bazi_pillars 写守卫复算；行缺失（persons-only）仍首建，
    consultation_count 落 DEFAULT 0（读不是咨询）。
(b) persons 写侧（update_person 默认行 / create_person 建档）补
    bazi_info 8 键镜像漏斗——收敛时机从「读时」前移到「写时」，
    读路径自愈降为低频兜底。

锁线手段（可观测，旧行为必失败）：
- consultation_count 读写计数（旧：读路径自愈 +1）；
- UserDAO.save_user_bazi / UserDAO._guard_bazi_pillars 调用 spy
  （旧：读路径各 1 次）；
- mirror 单点 spy + 写后读零写（收敛判定）。

隔离：全部用 tmp_path 真实 SQLite（UserDAO/PersonDAO 同库同生产形态），
零网络零 LLM，不写生产库与 data/。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402

from src.storage.birth_profile import get_user_birth_profile  # noqa: E402
from src.storage.dao import UserDAO  # noqa: E402
from src.storage.models import connect as db_connect  # noqa: E402
from src.storage.person_dao import (  # noqa: E402
    PersonDAO, bazi_info_of_person, mirror_bazi_info_to_users,
)

# 21:44 事故实锤四柱（2026-08-18 0点子时盘，他人/择时盘污染源）
POLLUTED_PILLARS = ["丙午", "丙申", "甲子", "甲子"]

# 默认档案（persons 单一事实源）：1999-03-28 9点 长春 女
PERSON_A = {"gender": "女", "birth_year": 1999, "birth_month": 3,
            "birth_day": 28, "birth_hour": 9, "birth_minute": None,
            "calendar": "solar", "city": "长春"}


def _db(tmp_path):
    """同库两 DAO（与生产一致：persons/bazi_info 同 db 文件）。"""
    db = str(tmp_path / "k25.db")
    return UserDAO(db), PersonDAO(db), db


def _mk_default(pdao, user_id, **over):
    birth = dict(PERSON_A)
    birth.update(over)
    return pdao.create_person(user_id, name="我", relation="自己",
                              is_default=True, birth=birth)


def _count(db, user_id):
    """users.consultation_count（行不存在 → None）。"""
    conn = db_connect(db)
    try:
        row = conn.execute(
            "SELECT consultation_count FROM users WHERE user_id=?",
            (user_id,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _set_count(db, user_id, n):
    conn = db_connect(db)
    try:
        conn.execute("UPDATE users SET consultation_count=? WHERE user_id=?",
                     (n, user_id))
        conn.commit()
    finally:
        conn.close()


def _spy(monkeypatch, owner, name, calls, key):
    """把 owner.name 包一层计数 spy（转调原实现）。"""
    orig = getattr(owner, name)

    def _wrapped(*a, **kw):
        calls[key] += 1
        return orig(*a, **kw)

    monkeypatch.setattr(owner, name, _wrapped)


# ================================================================
# (a) 读路径自愈 —— 不再 bump consultation_count / 不过守卫 / 不过
#     save_user_bazi 漏斗（对外值语义不变，旧行为必失败）
# ================================================================

def test_k25_read_heal_does_not_bump_consultation_count(tmp_path):
    """陈旧 bazi_info → 读路径自愈重建，且 consultation_count 零变化。

    旧行为：dao.save_user_bazi 每次调用 `consultation_count+1` → 读一次
    档案 = 记一次咨询（与 #82 授权/统计语义冲突面）→ 本用例在旧代码下
    断言失败（8 != 7）。"""
    dao, pdao, db = _db(tmp_path)
    _mk_default(pdao, "u1")                       # users 行不存在 → 建档镜像 no-op
    dao.save_user_bazi("u1", {
        "year": 1995, "month": 3, "day": 28, "hour": 9, "minute": 0,
        "city": "长春", "gender": "男"})
    _set_count(db, "u1", 7)

    out = get_user_birth_profile(dao, "u1")
    assert out["year"] == 1999 and out["gender"] == "女"   # 读语义不变
    row = dao.get_user_bazi("u1")                          # 自愈仍发生
    assert row["year"] == 1999 and row["gender"] == "女"
    assert row["city"] == "长春"
    assert _count(db, "u1") == 7, "读路径不得 bump consultation_count"


def test_k25_read_heal_skips_dao_save_and_write_guard(tmp_path, monkeypatch):
    """读路径自愈不再经 UserDAO.save_user_bazi / _guard_bazi_pillars。

    旧行为：自愈走 dao.save_user_bazi → 漏斗内 _guard_bazi_pillars 复算
    （复算成本 ~26ms + 误判面）→ 本用例旧代码下 spy 计数为 {save:1, guard:1}。"""
    dao, pdao, db = _db(tmp_path)
    _mk_default(pdao, "u2")
    dao.save_user_bazi("u2", {
        "year": 1995, "month": 3, "day": 28, "hour": 9, "minute": 0,
        "city": "长春", "gender": "男", "bazi": list(POLLUTED_PILLARS)})
    calls = {"save": 0, "guard": 0}
    _spy(monkeypatch, UserDAO, "save_user_bazi", calls, "save")
    _spy(monkeypatch, UserDAO, "_guard_bazi_pillars", calls, "guard")

    out = get_user_birth_profile(dao, "u2")
    assert out["year"] == 1999 and out["gender"] == "女"
    row = dao.get_user_bazi("u2")
    assert row["year"] == 1999 and row["gender"] == "女"    # 回写仍生效
    assert "bazi" not in row                                # k8 全量重建语义保持
    assert calls == {"save": 0, "guard": 0}, (
        "读路径自愈必须直写镜像：不得过 save_user_bazi 漏斗，"
        f"更不得触发写守卫复算（实测 {calls}）")


def test_k25_read_heal_first_build_row_without_count(tmp_path):
    """persons-only（无 users 行）→ 自愈仍首建 bazi_info（k8 契约保持），
    但 consultation_count 落 DEFAULT 0（读不是咨询）。

    旧行为：save_user_bazi 的 INSERT 分支写 consultation_count=1 →
    旧代码下断言 0 == 1 失败。"""
    dao, pdao, db = _db(tmp_path)
    _mk_default(pdao, "u3")
    assert _count(db, "u3") is None                # 前置：行不存在

    out = get_user_birth_profile(dao, "u3")
    assert out["year"] == 1999 and out["gender"] == "女"
    row = dao.get_user_bazi("u3")                   # 首建契约（k8 用例保持）
    assert row is not None and row["year"] == 1999 and row["gender"] == "女"
    assert "bazi" not in row
    assert _count(db, "u3") == 0, "读路径首建不得记 1 次咨询"


def test_k25_read_heal_payload_identical_to_person_mirror(tmp_path):
    """回写 payload == 读返回 out == bazi_info_of_person(行)（三处同构）。

    锁「两库一致后读路径零写」的构造前提：自愈写入值 = 写侧镜像值 =
    读返回形态（单一实现），任意漂移都会让收敛判定失效。"""
    dao, pdao, db = _db(tmp_path)
    _mk_default(pdao, "u4")
    dao.save_user_bazi("u4", {
        "year": 1995, "month": 3, "day": 28, "hour": 9, "minute": 0,
        "city": "长春", "gender": "男", "bazi": list(POLLUTED_PILLARS)})

    out = get_user_birth_profile(dao, "u4")
    person = pdao.list_persons("u4")[0]
    row = dao.get_user_bazi("u4")
    assert row == bazi_info_of_person(person)
    assert set(out) == set(row)                     # 读返回同键集（8 + solar_time）
    assert out["solar_time"] == row["solar_time"] == 1
    assert "bazi" not in row


# ================================================================
# (b) persons 写侧镜像漏斗 —— 收敛时机前移到「写时」
# ================================================================

def test_k25_update_default_person_mirrors_at_write_time(tmp_path):
    """默认行出生数据改写 → 写时立即镜像（无需任何读路径介入）。

    旧行为：写侧不镜像（仅 solar_time 翻转有单键镜像）→ 收敛要等下一次
    读路径自愈 → 旧代码下 bazi_info 仍停在 1995，本用例失败。"""
    dao, pdao, db = _db(tmp_path)
    p = _mk_default(pdao, "u5")                     # 建档时 users 行不存在
    dao.save_user_bazi("u5", {
        "year": 1995, "month": 3, "day": 28, "hour": 9, "minute": 0,
        "city": "长春", "gender": "男", "bazi": list(POLLUTED_PILLARS)})
    _set_count(db, "u5", 7)

    updated = pdao.update_person("u5", p["id"], birth={"birth_year": 2000})
    assert updated["birth_year"] == 2000
    row = dao.get_user_bazi("u5")                   # 零读路径介入
    assert row["year"] == 2000 and row["gender"] == "女"
    assert row["month"] == 3 and row["day"] == 28 and row["hour"] == 9
    assert row["city"] == "长春"                     # 未提供值不覆盖（合并语义）
    assert "bazi" not in row                        # k8：四柱键写时即丢弃
    assert row["solar_time"] == 1
    assert _count(db, "u5") == 7, "镜像不得 bump consultation_count"


def test_k25_write_mirror_makes_read_heal_noop(tmp_path, monkeypatch):
    """写侧镜像后，读路径判定非 stale → 零写（自愈降为低频兜底）。"""
    dao, pdao, db = _db(tmp_path)
    p = _mk_default(pdao, "u6")
    dao.save_user_bazi("u6", {"year": 1995, "month": 3, "day": 28,
                              "hour": 9, "minute": 0, "city": "长春",
                              "gender": "男"})
    pdao.update_person("u6", p["id"], birth={"birth_year": 2000})
    _set_count(db, "u6", 7)
    calls = {"mirror": 0}
    _spy(monkeypatch, sys.modules["src.storage.person_dao"],
         "mirror_bazi_info_to_users", calls, "mirror")

    before = dao.get_user_bazi("u6")
    out = get_user_birth_profile(dao, "u6")
    assert out["year"] == 2000 and out["gender"] == "女"
    assert calls["mirror"] == 0, "已收敛 → 读路径不得再写"
    assert dao.get_user_bazi("u6") == before
    assert _count(db, "u6") == 7


def test_k25_non_default_update_never_touches_bazi_info(tmp_path):
    """非默认命主改档 → ② 源（bazi_info 恒为默认档案镜像）零改动。"""
    dao, pdao, db = _db(tmp_path)
    _mk_default(pdao, "u7")
    other = pdao.create_person("u7", "妈", "父母", birth={
        "gender": "女", "birth_year": 1968, "birth_month": 8, "birth_day": 8,
        "birth_hour": 10, "birth_minute": 0, "calendar": "solar",
        "city": "长春"})
    dao.save_user_bazi("u7", {"year": 1999, "month": 3, "day": 28, "hour": 9,
                              "minute": 0, "city": "长春", "gender": "女"})
    _set_count(db, "u7", 5)

    pdao.update_person("u7", other["id"], birth={"birth_year": 1970})
    row = dao.get_user_bazi("u7")
    assert row["year"] == 1999 and row["gender"] == "女"
    assert _count(db, "u7") == 5


def test_k25_default_person_without_birth_year_never_wipes_bazi_info(tmp_path):
    """默认行无出生年 → 出生形态更新（仅开关）不得以空 payload 覆盖 ② 源。

    k25 自审补口：镜像漏斗触发条件与 create_person 对齐（默认行 **且有出生
    年**）。无此条件时，仅翻转真太阳时开关就会写出全 None birth payload 覆盖
    ② 源既有年份（k11c 原块以 `_mdata.get("year")` 保护过同一场景，本漏斗
    不得放宽）——旧形态下 ② 源 year 被清空，本用例失败。此情形应交由 k11c
    单键镜像块按旧语义处理（仅行存在且含出生年才写 solar_time）。
    """
    dao, pdao, db = _db(tmp_path)
    p = pdao.create_person("u12", name="我", relation="自己", is_default=True,
                           birth={"gender": "女"})      # 无出生年（占位行）
    assert p["is_default"] and p["birth_year"] is None
    legacy = {"year": 1999, "month": 3, "day": 28, "hour": 9, "minute": 0,
              "city": "长春", "gender": "女", "bazi": ["己卯", "丁卯", "戊午",
                                                     "丁巳"]}
    dao.save_user_bazi("u12", dict(legacy))      # ② 源既有档案（含 bazi 键）
    _set_count(db, "u12", 5)

    pdao.update_person("u12", p["id"], birth={"solar_time": 0})
    row = dao.get_user_bazi("u12")
    assert row["year"] == 1999, "无出生年默认行不得以空 payload 覆盖 ② 源"
    assert row["solar_time"] == 0                # k11c 单键镜像语义保持
    assert _count(db, "u12") == 5


def test_k25_create_default_person_mirrors(tmp_path):
    """建档（默认行 + 出生年）→ 写时立即镜像 ② 源。

    旧行为：建档不镜像 → bazi_info 停在空行 {} → 旧代码下 KeyError/失败。"""
    dao, pdao, db = _db(tmp_path)
    dao.save_user_bazi("u8", {})                    # api/user.py「创建空记录」形态
    _set_count(db, "u8", 5)

    _mk_default(pdao, "u8")
    row = dao.get_user_bazi("u8")
    assert row["year"] == 1999 and row["gender"] == "女"
    assert row["city"] == "长春" and row["calendar"] == "solar"
    assert row["solar_time"] == 1
    assert _count(db, "u8") == 5


def test_k25_create_non_default_person_does_not_mirror(tmp_path):
    """非默认命主建档 → ② 源零改动（不得写入他人出生数据）。"""
    dao, pdao, db = _db(tmp_path)
    _mk_default(pdao, "u9")
    dao.save_user_bazi("u9", {"year": 1999, "month": 3, "day": 28, "hour": 9,
                              "minute": 0, "city": "长春", "gender": "女"})
    _set_count(db, "u9", 5)

    pdao.create_person("u9", "朋友", "朋友", birth={
        "gender": "男", "birth_year": 1980, "birth_month": 1, "birth_day": 1,
        "birth_hour": 8, "birth_minute": 0, "calendar": "solar",
        "city": "北京"})
    row = dao.get_user_bazi("u9")
    assert row["year"] == 1999, "非默认建档不得覆盖 ② 源"
    assert _count(db, "u9") == 5


def test_k25_promotion_to_default_mirrors_promoted_person(tmp_path):
    """命主被提升为默认 → ② 源随默认换人镜像（update_person is_default）。"""
    dao, pdao, db = _db(tmp_path)
    _mk_default(pdao, "u10")
    other = pdao.create_person("u10", "爸", "父母", birth={
        "gender": "男", "birth_year": 1968, "birth_month": 8, "birth_day": 8,
        "birth_hour": 10, "birth_minute": 0, "calendar": "solar",
        "city": "长春"})
    dao.save_user_bazi("u10", {"year": 1999, "month": 3, "day": 28,
                               "hour": 9, "minute": 0, "city": "长春",
                               "gender": "女"})
    _set_count(db, "u10", 5)

    pdao.update_person("u10", other["id"], is_default=True)
    row = dao.get_user_bazi("u10")
    assert row["year"] == 1968 and row["gender"] == "男"
    assert _count(db, "u10") == 5


def test_k25_migrate_legacy_does_not_rewrite_bazi_info(tmp_path):
    """兼容迁移（② 源 → person 反向路径）不镜像：迁移后 bazi_info 原样。

    迁移是从 bazi_info 建 persons，回写镜像会（a）丢旧行 bazi 四柱键、
    （b）把 solar_time 归一为生效值 —— 破坏 k19 迁移脚本 dry-run「只分类
    不改数据」语义与旧行判定证据。故 create_person(mirror=False)。"""
    dao, pdao, db = _db(tmp_path)
    legacy = {"year": 1995, "month": 3, "day": 28, "hour": 9, "minute": 0,
              "city": "长春", "gender": "男", "calendar": "solar",
              "solar_time": 0, "bazi": ["乙亥", "己卯", "戊午", "丁巳"]}
    dao.save_user_bazi("u11", dict(legacy))
    _set_count(db, "u11", 7)

    p = pdao.migrate_legacy_bazi("u11")
    assert p is not None and p["birth_year"] == 1995 and p["is_default"]
    assert dao.get_user_bazi("u11") == legacy          # ② 源原样（含 bazi 键）
    assert _count(db, "u11") == 7


def test_k25_mirror_helper_row_missing_noop_and_no_create(tmp_path):
    """镜像单点直测：行缺失且 create_if_missing=False → no-op 不建行；
    create_if_missing=True → 首建且 consultation_count=0（读路径专用形态）。"""
    _, _, db = _db(tmp_path)
    payload = bazi_info_of_person(PERSON_A)

    assert mirror_bazi_info_to_users(db, "n1", payload) is False
    assert _count(db, "n1") is None
    assert mirror_bazi_info_to_users(db, "n1", payload,
                                     create_if_missing=True) is True
    assert _count(db, "n1") == 0
    row = UserDAO(db).get_user_bazi("n1")
    assert row == payload


@pytest.mark.parametrize("bad_payload", [{"year": {1, 2}}, None])
def test_k25_mirror_helper_failure_never_raises(tmp_path, bad_payload):
    """镜像失败路径不抛：db 打不开 / payload 不可 JSON 序列化 → False，
    且不落半行（主流程与库状态都不受污染）。"""
    assert mirror_bazi_info_to_users(
        str(tmp_path / "no" / "such" / "dir" / "x.db"), "u",
        {"year": 1999}) is False
    _, _, db = _db(tmp_path)
    assert mirror_bazi_info_to_users(db, "bad", bad_payload) is False
    assert _count(db, "bad") is None
