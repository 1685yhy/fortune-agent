# -*- coding: utf-8 -*-
"""k32（A18）qian_saves 整表迁移包显式事务：失败回滚、外层事务兼容。

审计位置记为 `src/api/qian.py`，实际迁移函数在 `src/storage/qian_dao.py`
（`QianDAO._migrate_kind`，qian.py 无迁移代码）——按真实位置实施。

旧代码：RENAME/CREATE 为 DDL（sqlite3 只在 DML 前隐式开事务 → DDL 各自
autocommit），COPY 失败时旧表已改名、新表为空 → 旧收藏落在 qian_saves_old
对外不可达（数据不可达事故）。本文件锁「失败必回滚 + 可重试 + 外层事务不炸」。

隔离：tmp_path 独立 sqlite；连接代理注入失败点，无网络。
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from src.storage.qian_dao import QianDAO  # noqa: E402

_OLD_DDL = """CREATE TABLE qian_saves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    no INTEGER NOT NULL,
    drawn_at REAL,
    UNIQUE (user_id, no)
)"""
_OLD_ROWS = [("u1", 7, 100.0), ("u1", 12, 200.0), ("u2", 7, 300.0)]


class _FailOn:
    """sqlite3 连接代理：命中前缀的 execute 抛错，其余属性透传。"""

    def __init__(self, real, prefix):
        self._real, self._prefix = real, prefix

    def execute(self, sql, *args):
        if str(sql).strip().startswith(self._prefix):
            raise sqlite3.OperationalError("disk I/O error (injected)")
        return self._real.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _old_db(tmp_path, name="old.db"):
    conn = sqlite3.connect(str(tmp_path / name))
    conn.execute(_OLD_DDL)
    conn.executemany("INSERT INTO qian_saves (user_id, no, drawn_at) "
                     "VALUES (?,?,?)", _OLD_ROWS)
    conn.commit()
    return conn


def test_a18_migration_failure_rolls_back(tmp_path):
    """COPY 失败 → 回滚到旧表（数据仍可达），异常向上抛（旧代码必失败）。"""
    conn = _old_db(tmp_path)
    proxy = _FailOn(conn, "INSERT INTO qian_saves")
    with pytest.raises(sqlite3.OperationalError):
        QianDAO(proxy)

    # 1) 旧表原样保留（表名仍是 qian_saves、无 kind 列、3 行数据可读）
    cols = [r[1] for r in conn.execute("PRAGMA table_info(qian_saves)")]
    assert "kind" not in cols, f"迁移未回滚（新表已建）：{cols}"
    assert conn.execute("SELECT user_id, no, drawn_at FROM qian_saves "
                        "ORDER BY id").fetchall() == _OLD_ROWS
    # 2) 不留中间态残骸
    leftovers = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND "
        "name LIKE 'qian_saves%'").fetchall()
    assert [r[0] for r in leftovers] == ["qian_saves"], leftovers
    # 3) 失败后可重试：同一连接再构造 → 迁移成功（数据零丢失）
    dao = QianDAO(conn)
    rows = conn.execute("SELECT user_id, no, kind, drawn_at FROM qian_saves "
                        "ORDER BY id").fetchall()
    assert rows == [("u1", 7, "original", 100.0), ("u1", 12, "original", 200.0),
                    ("u2", 7, "original", 300.0)]
    assert dao.save("u1", 7, "guanyin") == (True, False)
    conn.close()


def test_a18_migration_failure_at_drop_rolls_back(tmp_path):
    """DROP 失败（最后一步）同样回滚——旧表/数据完整可读（旧代码必失败）。"""
    conn = _old_db(tmp_path, "old_drop.db")
    proxy = _FailOn(conn, "DROP TABLE qian_saves_old")
    with pytest.raises(sqlite3.OperationalError):
        QianDAO(proxy)
    assert conn.execute("SELECT count(*) FROM qian_saves").fetchone()[0] == 3
    assert [r[1] for r in conn.execute("PRAGMA table_info(qian_saves)")][:4] == \
        ["id", "user_id", "no", "drawn_at"]
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND "
        "name LIKE 'qian_saves%'").fetchall()]
    assert names == ["qian_saves"], names
    conn.close()


def test_a18_migration_inside_outer_transaction(tmp_path):
    """迁移在调用方**已有外层事务**内执行：不炸（SAVEPOINT 语义）、外层写不丢。

    `BEGIN` 实现在此处会报 cannot start a transaction within a transaction。
    """
    conn = sqlite3.connect(str(tmp_path / "outer.db"))
    conn.execute(_OLD_DDL)
    conn.executemany("INSERT INTO qian_saves (user_id, no, drawn_at) "
                     "VALUES (?,?,?)", _OLD_ROWS)
    conn.commit()
    conn.execute("BEGIN")
    conn.execute("INSERT INTO qian_saves (user_id, no, drawn_at) "
                 "VALUES ('u3', 20, 400.0)")  # 外层未提交写
    dao = QianDAO(conn)  # 触发迁移（savepoint 内）
    assert dao.list_history("u3")[0]["no"] == 20
    conn.commit()  # 外层提交
    rows = conn.execute("SELECT user_id, no, kind FROM qian_saves "
                        "ORDER BY id").fetchall()
    assert ("u3", 20, "original") in rows
    assert len(rows) == 4
    conn.close()


def test_a18_new_db_no_savepoint_leak(tmp_path):
    """新库（已含 kind 列）不执行迁移：无 savepoint 残留、表可写。"""
    conn = sqlite3.connect(str(tmp_path / "fresh.db"))
    dao = QianDAO(conn)
    assert dao.save("u1", 1, "guandi") == (True, False)
    assert conn.in_transaction is False
    conn.close()
