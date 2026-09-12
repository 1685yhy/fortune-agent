# -*- coding: utf-8 -*-
"""k31：【必修】k19 迁移脚本 dry-run 零写入契约（persons 侧）+ k35 字节级加固。

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

k35 追加（§5/§6）：
- A7：dry-run **字节级**零写入（非 WAL 库上 `journal_mode` 不变、不建表/不加列、
  不产 `-wal/-shm`）——主连接 `mode=ro` + `PersonDAO.readonly`；含判别力对照
  （老路径 `PersonDAO(db)`→`init_db` 必改这些位 = 探针非恒真）；
- A6：`--execute` 收尾语文案与事实一致（扫描期 persons 可能自愈建卡/提升默认）；
- `--execute` 行为零变化由 §4 既有例 + 本批差分跑（见报告）双锁。

隔离：全在 tmp_path 临时库上跑（不动任何真实库）；零网络零 LLM。
"""
import hashlib
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


# ═══════ 5. k35/A7：dry-run 字节级零写入（非 WAL 库） ═══════

def _mk_legacy_non_wal_db(path):
    """非 WAL legacy 库（A7 靶）：users 缺 push_enabled/status 等列、persons 存在。
    不用 init_db（那会把库切成 WAL）——纯裸连接建表，journal_mode 保持 delete。"""
    conn = sqlite3.connect(path)
    conn.executescript("""
CREATE TABLE users (
    user_id TEXT PRIMARY KEY,
    bazi_info TEXT,
    updated_at TEXT
);
CREATE TABLE persons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    relation TEXT DEFAULT '其他',
    is_default INTEGER DEFAULT 0,
    birth_enc TEXT,
    created_at TEXT,
    updated_at TEXT
);
""")
    conn.execute("INSERT INTO users (user_id, bazi_info, updated_at)"
                 " VALUES (?,?,?)",
                 ("u_polluted",
                  _encrypt_text(json.dumps({"year": 1995, "month": 3, "day": 28,
                                            "hour": 9, "minute": 0,
                                            "city": "长春", "gender": "男",
                                            "calendar": "solar",
                                            "bazi": POLLUTED},
                                           ensure_ascii=False)), None))
    conn.execute("INSERT INTO users (user_id, bazi_info, updated_at)"
                 " VALUES (?,?,?)",
                 ("u_noperson",
                  _encrypt_text(json.dumps({"year": 1995, "month": 3, "day": 28,
                                            "hour": 9, "minute": 0,
                                            "city": "长春", "gender": "男",
                                            "calendar": "solar", "bazi": OWN},
                                           ensure_ascii=False)), None))
    conn.commit()
    conn.close()
    return path


def _fs_state(db):
    """库「写没写」硬证据：文件 sha + 头 18:19（journal 模式）+ 伴生文件
    + schema + users 列（只读连接读，自身零写入）。"""
    with open(db, "rb") as f:
        blob = f.read()
    n = os.path.basename(db)
    d = os.path.dirname(db)
    files = sorted(x for x in os.listdir(d)
                   if x in (n, n + "-wal", n + "-shm", n + "-journal"))
    conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    try:
        schema = conn.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
    finally:
        conn.close()
    return {"sha": hashlib.sha256(blob).hexdigest(), "size": len(blob),
            "jhdr": bytes(blob[18:20]), "files": files, "schema": schema,
            "users_cols": cols}


def test_a7_probe_detects_writes_positive_control(tmp_path):
    """判别力对照（探针非恒真）：老路径 `PersonDAO(db)` → `init_db` 必改这些位
    —— 若本断言失败，说明下面的零写入断言可能是恒真/测不到东西。"""
    db = _mk_legacy_non_wal_db(str(tmp_path / "ctl.db"))
    before = _fs_state(db)
    assert before["jhdr"] == b"\x01\x01"            # 起点：非 WAL
    PersonDAO(db)                                    # 老路径：无条件 init_db
    after = _fs_state(db)
    assert after != before
    assert after["jhdr"] == b"\x02\x02", "init_db 应把库持久切成 WAL"
    assert len(after["schema"]) > len(before["schema"]), "init_db 应建表"
    assert len(after["users_cols"]) > len(before["users_cols"]), "应 ALTER 加列"


def test_a7_dry_run_non_wal_byte_level_zero_write(tmp_path, capsys):
    """A7 核心：非 WAL 库 dry-run 后 journal_mode/表结构/-wal/-shm/文件字节全不变。

    旧代码在此必失败：`PersonDAO(args.db)` → `init_db` → `PRAGMA
    journal_mode=WAL`（持久切换）+ `CREATE TABLE` + `_migrate_db` ALTER。
    """
    db = _mk_legacy_non_wal_db(str(tmp_path / "k35.db"))
    before = _fs_state(db)
    assert before["jhdr"] == b"\x01\x01"
    assert M.main(["--db", db]) == 0
    out = capsys.readouterr().out
    assert "[dry-run]" in out and "未写入" in out
    assert "待清理 1 行" in out                      # 判定面照常工作
    after = _fs_state(db)
    assert after["sha"] == before["sha"], "dry-run 改了库文件字节"
    assert after["jhdr"] == before["jhdr"], "dry-run 切了 journal_mode"
    assert after["files"] == before["files"], "dry-run 产出 -wal/-shm"
    assert after["schema"] == before["schema"], "dry-run 建表/改 schema"
    assert after["users_cols"] == before["users_cols"], "dry-run ALTER 加列"
    # 幂等：再跑一次仍全等
    assert M.main(["--db", db]) == 0
    capsys.readouterr()
    assert _fs_state(db) == after


def test_a7_person_dao_readonly_skips_init_db(tmp_path):
    """person_dao 只读路径单测：`PersonDAO.readonly` 不建 WAL/不建表；
    `list_persons` 照常可读；默认构造（老行为）仍初始化库（未变）。"""
    db = _mk_legacy_non_wal_db(str(tmp_path / "ro.db"))
    before = _fs_state(db)
    pdao = PersonDAO.readonly(db)
    assert pdao.list_persons("u_polluted") == []      # 只读可查（表存在，空）
    assert _fs_state(db) == before, "只读构造产生了写入"
    PersonDAO(db)                                      # 老路径行为保持不变
    assert _fs_state(db) != before


def test_a7_dry_run_fails_safe_on_missing_persons_table(tmp_path, capsys):
    """无 persons 表的极端旧库：旧代码 dry-run 静态建表；新代码不建表但
    备注以「无默认命主」兜底，且仍零写入（拒绝建表而非报错退出）。"""
    db = str(tmp_path / "nop.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE users (user_id TEXT PRIMARY KEY, bazi_info TEXT,"
        " updated_at TEXT);")
    conn.execute("INSERT INTO users VALUES (?,?,?)",
                 ("u_polluted", _encrypt_text(json.dumps(
                     {"year": 1995, "month": 3, "day": 28, "hour": 9,
                      "minute": 0, "city": "长春", "gender": "男",
                      "calendar": "solar", "bazi": POLLUTED},
                     ensure_ascii=False)), None))
    conn.commit()
    conn.close()
    before = _fs_state(db)
    assert M.main(["--db", db]) == 0
    out = capsys.readouterr().out
    assert "无默认命主" in out
    assert "待清理 1 行" in out
    assert _fs_state(db) == before


# ═══════ 6. k35/A6：--execute 收尾语文案与事实一致 ═══════

def test_a6_execute_reminder_text_matches_facts(k31_db, tmp_path, capsys):
    """收尾语须承认扫描期 persons 可能自愈（u_noperson 0→1 是既有行为），
    不得再宣告「persons 未动」；chart_records 确实未动。"""
    bak = str(tmp_path / "pre.db")
    audit = str(tmp_path / "audit.jsonl")
    assert _run_main(["--db", k31_db, "--execute", "--backup", bak,
                      "--audit", audit]) == 0
    out = capsys.readouterr().out
    assert "扫描期 persons 可能" in out
    assert "自愈建卡/提升默认" in out
    assert "chart_records 未动" in out
    assert "persons/chart_records 未动" not in out, "旧文案仍在"
    # 事实对齐：扫描期确实发生 persons 自愈（既有行为，与本批文案一致）
    assert _count_persons(k31_db, "u_noperson") == 1
