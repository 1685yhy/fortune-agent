# -*- coding: utf-8 -*-
"""k19 review 条件①：迁移脚本端到端测试（tmp-sqlite，真实 DB 形态）。

覆盖 scan/execute 全路径：真实分拣（矛盾/一致/孤儿/解密失败宁保）、
--execute 缺 --backup / 缺 --audit 拒绝（argparse exit 2）、备份目标已存在
拒绝、audit jsonl 输出、幂等重跑、清理只删 bazi 键（birth 8 键 + solar_time
镜像键原样保留、birth 值不被改、一致行与解密失败行不动）。

脚本：scripts/migrate_stale_bazi_keys.py（同进程直调 main()，断言退出码）。
零网络零 LLM（分拣复算 = BaziEngine 纯规则）。
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
from src.storage.chart_dao import ChartDAO  # noqa: E402
from src.engines.bazi import BaziEngine  # noqa: E402

import migrate_stale_bazi_keys as M  # noqa: E402

ENG = BaziEngine()
POLLUTED = list(ENG.calculate(2026, 8, 18, 0, 0, "北京", "男").bazi)  # 他人盘
OWN = list(ENG.calculate(1995, 3, 28, 9, 0, "长春", "男").bazi)       # 本人盘


def _put(conn, uid, info):
    conn.execute("INSERT INTO users (user_id, bazi_info) VALUES (?,?)",
                 (uid, _encrypt_text(json.dumps(info, ensure_ascii=False))))


@pytest.fixture
def e2e_db(tmp_path):
    """样本库（三表同文件=生产形态）：
    u_polluted   本人 birth 1995 + 他人盘 bazi（21:44 族）→ stale_contradicts
                 + persons 默认命主 1999（已修）→ 分裂备注；chart_records
                 仅有他人盘 2026-08-18 → 无盘证
    u_consistent birth 与 bazi 一致（本人盘镜像）→ keep_self_corroborated
    u_orphan     bazi 在但 birth 键不齐（08-16 孤儿族）→ stale_orphan
    u_encfail    bazi_info 明文非法 JSON（无法解析）→ keep_decrypt_failed 宁保
    """
    db = str(tmp_path / "e2e.db")
    init_db(db)
    conn = sqlite3.connect(db)
    _put(conn, "u_polluted", {
        "year": 1995, "month": 3, "day": 28, "hour": 9, "minute": 0,
        "city": "长春", "gender": "男", "calendar": "solar",
        "solar_time": 0, "bazi": POLLUTED})
    _put(conn, "u_consistent", {
        "year": 1995, "month": 3, "day": 28, "hour": 9, "minute": 0,
        "city": "长春", "gender": "男", "calendar": "solar", "bazi": OWN})
    _put(conn, "u_orphan", {"hour": 0, "city": "北京", "bazi": POLLUTED})
    conn.execute("INSERT INTO users (user_id, bazi_info) VALUES (?,?)",
                 ("u_encfail", "{not-json!!"))
    conn.commit()
    conn.close()
    # 先提交并关闭裸连接，再经 DAO 建 chart_records/persons（防写锁）
    ChartDAO(db).save_chart(
        "u_polluted", None,
        {"year": 2026, "month": 8, "day": 18, "hour": 0, "minute": 0,
         "city": "北京", "gender": "男", "calendar": "solar"},
        {"bazi": POLLUTED})
    pdao = PersonDAO(db)
    pdao.create_person("u_polluted", name="我", relation="自己",
                       is_default=True,
                       birth={"gender": "男", "birth_year": 1999,
                              "birth_month": 3, "birth_day": 28,
                              "birth_hour": 9, "birth_minute": 0,
                              "city": "长春", "calendar": "solar"})
    return db


def _decrypt(raw):
    return _decrypt_or_plain(raw)


def _get_bazi(db, uid):
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT bazi_info FROM users WHERE user_id=?",
                       (uid,)).fetchone()
    conn.close()
    return json.loads(_decrypt(row[0]) or "{}") if row and row[0] else None


def _run_main(args):
    try:
        return M.main(args)
    except SystemExit as e:
        return int(e.code or 0)


# ═══════ 1. scan 真实分拣 ═══════

def test_scan_real_classification(e2e_db):
    conn = sqlite3.connect(e2e_db)
    stale, keep = M.scan_stale(conn, PersonDAO(e2e_db))
    conn.close()
    d = {it["user_id"]: it for it in stale}
    k = {it["user_id"]: it for it in keep}

    assert d["u_polluted"]["verdict"] == M.STALE_CONTRADICT
    assert "1999 与 bazi_info birth=1995 不一致" in d["u_polluted"]["note"]
    assert k["u_consistent"]["verdict"] == M.KEEP_SELF
    assert d["u_orphan"]["verdict"] == M.STALE_ORPHAN
    # 解密失败行宁保（不进待清理、进保留清单并标注人工核查）
    assert k["u_encfail"]["verdict"] == "keep_decrypt_failed"
    assert "u_encfail" not in d


# ═══════ 2. CLI 安全闸门 ═══════

def test_execute_without_backup_refused(e2e_db, capsys):
    assert _run_main(["--db", e2e_db, "--execute"]) == 2
    assert "--backup" in capsys.readouterr().err


def test_execute_missing_audit_refused(e2e_db, capsys):
    rc = _run_main(["--db", e2e_db, "--execute",
                    "--backup", e2e_db + ".bak"])
    assert rc == 2
    assert "--audit" in capsys.readouterr().err


def test_dry_run_default_no_write(e2e_db, capsys):
    assert _run_main(["--db", e2e_db]) == 0
    out = capsys.readouterr().out
    assert "待清理 2 行" in out          # u_polluted + u_orphan
    assert "[dry-run]" in out
    assert "bazi" in _get_bazi(e2e_db, "u_polluted")   # 零写入


def test_backup_exists_refused_before_any_write(e2e_db, tmp_path, capsys):
    bak = str(tmp_path / "pre.db")
    with open(bak, "w") as f:
        f.write("x")                     # 备份目标已存在
    rc = _run_main(["--db", e2e_db, "--execute", "--backup", bak,
                    "--audit", str(tmp_path / "a.jsonl")])
    assert rc == 2
    assert "拒绝" in capsys.readouterr().out
    assert "bazi" in _get_bazi(e2e_db, "u_polluted")   # 库未动


# ═══════ 3. execute：备份/审计/只删 bazi 键/幂等 ═══════

def test_execute_full_flow(e2e_db, tmp_path, capsys):
    bak = str(tmp_path / "pre.db")
    audit = str(tmp_path / "audit.jsonl")
    assert _run_main(["--db", e2e_db, "--execute", "--backup", bak,
                      "--audit", audit]) == 0
    out = capsys.readouterr().out
    assert "清理 2 行" in out
    assert os.path.exists(bak) and os.path.exists(audit)

    # audit 行：两条待清理（矛盾+孤儿），各带判定与 removed_bazi
    lines = [json.loads(l) for l in open(audit, encoding="utf-8")]
    assert {l["user_id"] for l in lines} == {"u_polluted", "u_orphan"}
    assert all(l.get("removed_bazi") for l in lines)
    assert all(l["verdict"] in M.STALE_VERDICTS for l in lines)

    # 清理只删 bazi 键：birth 8 键原样 + solar_time 镜像键保留 + 值未改
    info = _get_bazi(e2e_db, "u_polluted")
    assert "bazi" not in info
    for k in ("year", "month", "day", "hour", "minute", "city", "gender",
              "calendar"):
        assert k in info and info[k] not in (None, "")
    assert info["solar_time"] == 0
    assert info["year"] == 1995
    # 一致行原样保留 bazi；解密失败行原样未动
    assert "bazi" in _get_bazi(e2e_db, "u_consistent")
    conn = sqlite3.connect(e2e_db)
    row = conn.execute("SELECT bazi_info FROM users WHERE user_id='u_encfail'"
                       ).fetchone()
    conn.close()
    assert row[0] == "{not-json!!"


def test_execute_idempotent_rerun(e2e_db, tmp_path, capsys):
    bak = str(tmp_path / "pre.db")
    audit = str(tmp_path / "audit.jsonl")
    assert _run_main(["--db", e2e_db, "--execute", "--backup", bak,
                      "--audit", audit]) == 0
    capsys.readouterr()
    # 重跑 dry-run → 0 行待清理
    assert _run_main(["--db", e2e_db]) == 0
    assert "待清理 0 行" in capsys.readouterr().out
    # 重跑 execute（新备份路径）→ 幂等无操作
    rc = _run_main(["--db", e2e_db, "--execute",
                    "--backup", bak + "2", "--audit", audit + "2"])
    assert rc == 0
    assert "无待清理行" in capsys.readouterr().out
