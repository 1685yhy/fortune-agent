# -*- coding: utf-8 -*-
"""k31：【必修】k19 迁移脚本 dry-run 零写入契约（persons 侧）。

背景（k30 审查发现 A）：`scripts/migrate_stale_bazi_keys.py` 的 `--dry-run`
承诺「零写入」，但 `persons_note` 调 `get_default_person(user_id)`（默认
`auto_migrate=True`）→ 两条静默写路径：
  (a) 无任何 person → `migrate_legacy_bazi` 建「我」（INSERT，count_persons 0→1）；
  (b) 有 person 但无默认行 → 把最早者提升为默认（UPDATE is_default=1）。
独立审查探针实测 (a) 复现；本文件对 (a)(b) 都加断言（旧代码两例必失败）。

覆盖：
- dry-run 后 persons 行数/行内容/is_default 全部不变（行为可判别，非恒真）；
- dry-run 输出仍说「未写入」且备注语义不变（只读口径取默认人：默认在前）；
- `--execute` 路径**行为不变**（本次只收 dry-run 语义：扫描期该建就建、
  该提升就提升）——防后续误把收紧扩到执行路径；
- `persons_note` 只读口径单测：默认**不调用** `get_default_person`。

隔离：全在 tmp_path 临时库上跑（不动任何真实库）；零网络零 LLM。
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"
os.environ["ENCRYPTION_KEY"] = "e2e-test-encryption-key-32-bytes-long!!"

import pytest  # noqa: E402

from src.storage.dao import _encrypt_text, _decrypt_or_plain  # noqa: E402
from src.storage.models import init_db  # noqa: E402
from src.storage.person_dao import PersonDAO  # noqa: E402
from src.engines.bazi import BaziEngine  # noqa: E402

import migrate_stale_bazi_keys as M  # noqa: E402

ENG = BaziEngine()
POLLUTED = list(ENG.calculate(2026, 8, 18, 0, 0, "北京", "男").bazi)  # 他人盘
OWN = list(ENG.calculate(1995, 3, 28, 9, 0, "长春", "男").bazi)       # 本人盘


def _put(conn, uid, info):
    conn.execute("INSERT INTO users (user_id, bazi_info) VALUES (?,?)",
                 (uid, _encrypt_text(json.dumps(info, ensure_ascii=False))))


@pytest.fixture
def k31_db(tmp_path):
    """样本库（三表同文件=生产形态）：

    u_noperson   本人自洽 bazi（keep_self_corroborated）+ **无 persons 行**
                 → 旧代码 dry-run 走 (a) 静默建「我」（审查实测 0→1）
    u_nondefault 本人自洽 bazi + 1 条 is_default=0 的 person（无默认行）
                 → 旧代码 dry-run 走 (b) 静默提升默认（本次新增覆盖）
    u_polluted   本人 birth 1995 + 他人盘 bazi（21:44 族，待清理）
                 + 默认命主 1999 → 备注显示分裂（dry-run 只列不写）
    """
    db = str(tmp_path / "k31.db")
    init_db(db)
    own_info = {
        "year": 1995, "month": 3, "day": 28, "hour": 9, "minute": 0,
        "city": "长春", "gender": "男", "calendar": "solar",
        "solar_time": 0, "bazi": OWN}
    conn = sqlite3.connect(db)
    _put(conn, "u_noperson", own_info)
    _put(conn, "u_nondefault", dict(own_info))
    _put(conn, "u_polluted", {**own_info, "bazi": POLLUTED})
    conn.commit()
    conn.close()
    # 先关裸连接再经 DAO 写 persons（防写锁）
    pdao = PersonDAO(db)
    pdao.create_person("u_nondefault", name="家人", relation="父母",
                       is_default=False, mirror=False,
                       birth={"gender": "男", "birth_year": 1995,
                              "birth_month": 3, "birth_day": 28,
                              "birth_hour": 9, "birth_minute": 0,
                              "city": "长春", "calendar": "solar"})
    pdao.create_person("u_polluted", name="我", relation="自己",
                       is_default=True, mirror=False,
                       birth={"gender": "男", "birth_year": 1999,
                              "birth_month": 3, "birth_day": 28,
                              "birth_hour": 9, "birth_minute": 0,
                              "city": "长春", "calendar": "solar"})
    return db


def _snapshot(db):
    """全 persons 表快照（含 is_default）：dry-run 前后必须逐位相等。"""
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT id, user_id, name, relation, is_default, birth_enc "
        "FROM persons ORDER BY id").fetchall()
    users = conn.execute(
        "SELECT user_id, bazi_info FROM users ORDER BY user_id").fetchall()
    conn.close()
    return rows, users


def _count_persons(db, uid):
    return PersonDAO(db).count_persons(uid)


def _persons_of(db, uid):
    return PersonDAO(db).list_persons(uid)


def _run_main(args):
    try:
        return M.main(args)
    except SystemExit as e:
        return int(e.code or 0)


# ═══════ 1. dry-run：persons 侧零写入（旧代码必失败） ═══════

def test_dry_run_creates_no_person(k31_db, capsys):
    """(a) 无 person 用户：dry-run 后 count_persons 仍 0（旧代码 0→1）。

    判别力：旧代码在此失败（get_default_person 默认 auto_migrate=True →
    migrate_legacy_bazi → create_person INSERT）。
    """
    assert _count_persons(k31_db, "u_noperson") == 0
    assert _run_main(["--db", k31_db]) == 0
    out = capsys.readouterr().out
    assert "[dry-run]" in out and "未写入" in out
    assert _count_persons(k31_db, "u_noperson") == 0, "dry-run 静默建了 person"
    assert _persons_of(k31_db, "u_noperson") == []


def test_dry_run_does_not_promote_default(k31_db, capsys):
    """(b) 有 person 无默认行：dry-run 后 is_default 仍 0（旧代码 0→1）。"""
    before = _persons_of(k31_db, "u_nondefault")
    assert len(before) == 1 and before[0]["is_default"] is False
    assert _run_main(["--db", k31_db]) == 0
    capsys.readouterr()
    after = _persons_of(k31_db, "u_nondefault")
    assert len(after) == 1
    assert after[0]["is_default"] is False, "dry-run 静默提升默认（UPDATE 写）"


def test_dry_run_persons_and_users_bitwise_unchanged(k31_db, capsys):
    """全表逐字节快照：dry-run 前后 persons/users 行完全一致（含密文）。"""
    before = _snapshot(k31_db)
    assert _run_main(["--db", k31_db]) == 0
    capsys.readouterr()
    assert _snapshot(k31_db) == before
    # 幂等：再跑一次仍一致
    assert _run_main(["--db", k31_db]) == 0
    capsys.readouterr()
    assert _snapshot(k31_db) == before


def test_dry_run_explicit_flag_also_readonly(k31_db, capsys):
    """显式 --dry-run（非仅默认）同为只读。"""
    assert _run_main(["--db", k31_db, "--dry-run"]) == 0
    capsys.readouterr()
    assert _count_persons(k31_db, "u_noperson") == 0
    assert _persons_of(k31_db, "u_nondefault")[0]["is_default"] is False


# ═══════ 2. 只读口径不改变输出语义（备注仍可用） ═══════

def test_dry_run_note_and_verdicts_unchanged(k31_db, capsys):
    """备注仍是「默认命主 vs bazi_info」一致性：只读口径取默认在前首行。"""
    assert _run_main(["--db", k31_db]) == 0
    out = capsys.readouterr().out
    assert "待清理 1 行" in out                     # u_polluted
    assert "persons默认=1999 与 bazi_info birth=1995 不一致" in out
    assert "persons默认=1995 与 bazi_info 一致" in out  # u_noperson/u_nondefault
    # dry-run 不删待清理行的 bazi 键
    conn = sqlite3.connect(k31_db)
    raw = conn.execute("SELECT bazi_info FROM users WHERE user_id='u_polluted'"
                       ).fetchone()[0]
    conn.close()
    assert "bazi" in json.loads(_decrypt_or_plain(raw))


def test_scan_stale_default_is_readonly(k31_db):
    """直接调 scan_stale（无 auto_migrate 实参）= 只读默认，不产生 person。"""
    conn = sqlite3.connect(k31_db)
    stale, keep = M.scan_stale(conn, PersonDAO(k31_db))
    conn.close()
    assert _count_persons(k31_db, "u_noperson") == 0
    assert _persons_of(k31_db, "u_nondefault")[0]["is_default"] is False
    assert {it["user_id"] for it in stale} == {"u_polluted"}
    assert {it["user_id"] for it in keep} == {"u_noperson", "u_nondefault"}


# ═══════ 3. persons_note 读口径单测 ═══════

class _SpyPdao:
    """记录调用：只读口径不得触碰 get_default_person（写能力读）。"""

    def __init__(self, persons):
        self.persons = persons
        self.calls = []

    def list_persons(self, user_id):
        self.calls.append(("list_persons", user_id))
        return list(self.persons)

    def get_default_person(self, user_id, auto_migrate=True):
        self.calls.append(("get_default_person", user_id, auto_migrate))
        return self.persons[0] if self.persons else None


def test_persons_note_default_readonly():
    spy = _SpyPdao([{"birth_year": 1995}])
    info = {"year": 1995}
    assert "一致" in M.persons_note(spy, "u", info)
    assert spy.calls == [("list_persons", "u")]      # 未调 get_default_person
    # 空 persons → 无默认命主（且仍不迁移建档）
    spy2 = _SpyPdao([])
    assert M.persons_note(spy2, "u", info) == "无默认命主"
    assert spy2.calls == [("list_persons", "u")]


def test_persons_note_execute_legacy_path_unchanged():
    """auto_migrate=True（--execute 专用）仍走旧 get_default_person 口径。"""
    spy = _SpyPdao([{"birth_year": 1999}])
    note = M.persons_note(spy, "u", {"year": 1995}, auto_migrate=True)
    assert spy.calls == [("get_default_person", "u", True)]
    assert "1999 与 bazi_info birth=1995 不一致" in note


# ═══════ 4. --execute 行为不变（本次只收 dry-run 语义） ═══════

def test_execute_path_keeps_legacy_autopromote_and_migrate(k31_db, tmp_path,
                                                           capsys):
    """--execute（扫描期）旧行为保持：该建 person 就建、该提升就提升。

    本次修复只收 dry-run 语义（brief 明示）；此例锁住「不得顺手改执行路径」。
    """
    bak = str(tmp_path / "pre.db")
    audit = str(tmp_path / "audit.jsonl")
    assert _run_main(["--db", k31_db, "--execute", "--backup", bak,
                      "--audit", audit]) == 0
    out = capsys.readouterr().out
    assert "清理 1 行" in out                              # u_polluted
    assert _count_persons(k31_db, "u_noperson") == 1       # 兼容迁移仍发生
    assert _persons_of(k31_db, "u_noperson")[0]["name"] == "我"
    assert _persons_of(k31_db, "u_nondefault")[0]["is_default"] is True
    # 审计/备份仍产出；清理只删 bazi 键
    assert os.path.exists(bak) and os.path.exists(audit)
    conn = sqlite3.connect(k31_db)
    raw = conn.execute("SELECT bazi_info FROM users WHERE user_id='u_polluted'"
                       ).fetchone()[0]
    conn.close()
    info = json.loads(_decrypt_or_plain(raw))
    assert "bazi" not in info and info["year"] == 1995
