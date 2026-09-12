#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k39 S4 测试：上传图片跟着对话长期保留，清理只删「无任何引用」的孤儿。

改前：图片轮**不落库**（`/api/chat` 的 image 分支绕开 `process()`）→
`sessions` 里没有上传 URL → `_referenced_upload_names` 永远查不到引用 →
72h TTL 一到每个上传都算孤儿被删 → 客户端本地历史图裂。
改后：`_handle_image` 落 user 轮（content 带原样上传 URL）+ assistant 轮，
清理能识别引用；无引用的孤儿照删。

覆盖（brief S4 三个测试要求 + 隐私口径不放宽）：
1. 被引用的图超 TTL **不删**（端到端：真落库 → 真清理）；
2. 无引用孤儿超 TTL **删**；
3. 历史图能渲染（有路径即可：GET /api/chat/uploads/<name> 200）；
   已删图优雅降级（404 而非 500）；
4. 隐私口径不放宽：倾诉/深夜（deep-night）图片轮 `temp=1` + 24h 硬清理；
   无用户标识 → 零写入；不写任何 L2/L3 记忆。

红线：只写 tmp_path 下的临时库/临时上传目录（`FORTUNE_DB_PATH` /
`FORTUNE_UPLOADS_DIR` 注入），零 LLM 调用，不碰生产库。
"""
import os
import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from src.bot.handler import MessageHandler  # noqa: E402
from src.storage.session_dao import SessionDAO  # noqa: E402

JPG = "photo.jpg"
URL = f"https://yilichat.com/api/chat/uploads/{JPG}"


def _make_handler(tmp_path, deep_night=None):
    """真实 SessionDAO（临时库）+ 桩 CV 读数的最小 handler。"""
    h = object.__new__(MessageHandler)
    h.session_dao = SessionDAO(str(tmp_path / "sessions.sqlite"))
    h._deep_night = dict(deep_night or {})
    h.memory_system = Mock()
    h.chart_dao = None
    # CV 读数走桩：本测试只验落库/引用/清理链，不验 CV
    h._try_face_reading = lambda url, text, **k: None
    h._try_palm_reading = lambda url, text, **k: None
    h.fengshui_engine = Mock()
    h.mianxiang_engine = Mock()
    return h


@pytest.fixture
def allow_url(monkeypatch):
    """白名单放行（白名单本身的矩阵在 test_k33_upload_ssrf.py）。"""
    import src.bot.image_url_guard as guard
    monkeypatch.setattr(guard, "is_allowed_image_url", lambda url: True)


def _rows(db):
    """读 sessions（content 落库为密文 → 逐行解密还原文本，与清理反查同口径）。"""
    from src.storage.session_dao import _decrypt_or_plain
    con = sqlite3.connect(db)
    try:
        rows = con.execute(
            "SELECT user_id, role, content, intent, temp, temp_expire_at "
            "FROM sessions ORDER BY id").fetchall()
    finally:
        con.close()
    return [(uid, role, _decrypt_or_plain(content), intent, temp, expire)
            for uid, role, content, intent, temp, expire in rows]


def _db_of(h):
    return h.session_dao.db_path


# ================================================================
# 1. 图片轮落库 + 留下引用
# ================================================================

def test_image_turn_is_persisted_with_upload_url(tmp_path, allow_url):
    """该发生的发生：user 轮 + assistant 轮都落库，user 轮 content 带上传 URL。"""
    h = _make_handler(tmp_path)
    reply = h._handle_image(URL, "这张图怎么样", user_id="u_img")
    rows = _rows(_db_of(h))
    assert len(rows) == 2
    (uid_u, role_u, content_u, intent_u, _t, _e) = rows[0]
    (uid_a, role_a, content_a, intent_a, _t2, _e2) = rows[1]
    assert (uid_u, role_u) == ("u_img", "user")
    assert (uid_a, role_a) == ("u_img", "assistant")
    assert URL in content_u                 # 引用可被清理反查识别
    assert "（图片）" in content_u           # 与客户端 streamHost 同占位口径
    assert content_a == reply               # assistant 轮就是用户看到的回复
    assert intent_u == intent_a == "image"


def test_no_user_id_writes_nothing(tmp_path, allow_url):
    """不该发生的不发生：无用户标识 → 零写入（不建匿名共享身份）。"""
    h = _make_handler(tmp_path)
    h._handle_image(URL, "这张图怎么样", user_id="")
    assert _rows(_db_of(h)) == []
    # 缺省调用（既有 3 参形态）也不落库——保持改前行为
    h._handle_image(URL, "这张图怎么样")
    assert _rows(_db_of(h)) == []


def test_inner_handler_never_persists(tmp_path, allow_url):
    """分层守卫：`_handle_image_inner` 本身零落库（落库只由 `_handle_image` 收口）。"""
    h = _make_handler(tmp_path)
    h._handle_image_inner(URL, "这张图怎么样")
    assert _rows(_db_of(h)) == []


def test_referenced_upload_is_found_by_reference_index(tmp_path, allow_url,
                                                       monkeypatch):
    """k33 引用反查（清理的判据来源）能读到图片轮留下的引用。"""
    from src import main as main_mod
    h = _make_handler(tmp_path)
    h._handle_image(URL, "这张图怎么样", user_id="u_img")
    monkeypatch.setenv("FORTUNE_DB_PATH", str(_db_of(h)))
    referenced = main_mod._referenced_upload_names()
    assert referenced is not None and JPG in referenced


# ================================================================
# 2. 清理：被引用的不删，孤儿删
# ================================================================

def _uploads_dir(tmp_path, monkeypatch):
    d = tmp_path / "uploads"
    d.mkdir()
    monkeypatch.setenv("FORTUNE_UPLOADS_DIR", str(d))
    return d


def _touch_old(d, name, age_hours=100):
    p = d / name
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
    old = time.time() - age_hours * 3600
    os.utime(p, (old, old))
    return p


def test_referenced_upload_survives_ttl_via_real_image_turn(tmp_path, monkeypatch,
                                                            allow_url):
    """端到端：真跑图片轮 → 真跑清理 → 超 TTL 的被引用图**不删**。

    改前必失败：不落库 → 无引用 → 文件被当孤儿删除。
    """
    from src import main as main_mod
    d = _uploads_dir(tmp_path, monkeypatch)
    keep = _touch_old(d, JPG)
    orphan = _touch_old(d, "orphan.jpg")
    h = _make_handler(tmp_path)
    h._handle_image(URL, "这张图怎么样", user_id="u_img")
    monkeypatch.setenv("FORTUNE_DB_PATH", str(_db_of(h)))

    stats = main_mod.cleanup_chat_uploads(ttl_seconds=72 * 3600)
    assert keep.exists(), "被引用的图不得删除（历史图裂）"
    assert not orphan.exists(), "无引用孤儿必须删除"
    assert stats["removed"] == 1


def test_orphan_without_any_reference_is_removed(tmp_path, monkeypatch):
    """无任何引用的孤儿超 TTL 照删（清理能力未退化）。"""
    from src import main as main_mod
    d = _uploads_dir(tmp_path, monkeypatch)
    orphan = _touch_old(d, "lonely.jpg")
    fresh = _touch_old(d, "fresh.jpg", age_hours=1)
    main_mod.cleanup_chat_uploads(ttl_seconds=72 * 3600, referenced=set())
    assert not orphan.exists() and fresh.exists()


# ================================================================
# 3. 历史图能渲染（有路径即可）+ 已删图优雅降级
# ================================================================

def test_history_image_url_is_served_and_deleted_one_degrades(tmp_path,
                                                              monkeypatch):
    from starlette.testclient import TestClient
    from src.main import app
    d = _uploads_dir(tmp_path, monkeypatch)
    p = _touch_old(d, JPG, age_hours=0)
    client = TestClient(app)
    assert client.get(f"/api/chat/uploads/{JPG}").status_code == 200
    # 历史已删图：优雅降级（404，不抛异常/不 500）
    p.unlink()
    r = client.get(f"/api/chat/uploads/{JPG}")
    assert r.status_code == 404


# ================================================================
# 4. 隐私口径不放宽（倾诉/深夜 + 零记忆 + 无身份）
# ================================================================

def test_deep_night_image_turn_is_temp_not_relaxed(tmp_path, allow_url):
    """倾诉/深夜通道的既有约束原样生效：图片轮 temp=1 + 24h 过期。"""
    h = _make_handler(tmp_path, deep_night={"u_night": True})
    h._handle_image(URL, "这张图怎么样", user_id="u_night")
    rows = _rows(_db_of(h))
    assert len(rows) == 2
    for _uid, _role, _content, _intent, temp, expire_at in rows:
        assert temp == 1, "深夜图片轮必须与文本轮同口径打 temp 标记"
        assert expire_at, "temp 行必须有 24h 过期时间（cleanup_temp 兜底）"
    # 反向：非深夜用户 → 正常长期保留（temp=0）
    h2 = _make_handler(tmp_path / "day")
    (tmp_path / "day").mkdir(exist_ok=True)
    h2._handle_image(URL, "这张图怎么样", user_id="u_day")
    assert all(r[4] == 0 for r in _rows(_db_of(h2)))


def test_deep_night_image_rows_hard_deleted_after_24h(tmp_path, allow_url):
    """24h 硬清理对图片轮同样生效（倾诉消息不长期留存——约束未被放宽）。"""
    h = _make_handler(tmp_path, deep_night={"u_night": True})
    h._handle_image(URL, "这张图怎么样", user_id="u_night")
    assert len(_rows(_db_of(h))) == 2
    con = sqlite3.connect(_db_of(h))
    con.execute("UPDATE sessions SET temp_expire_at='2000-01-01T00:00:00'")
    con.commit()
    con.close()
    h.session_dao.cleanup_temp()
    assert _rows(_db_of(h)) == []


def test_image_turn_writes_no_long_term_memory(tmp_path, allow_url):
    """不写任何 L2/L3 记忆（记忆管线一行不碰——图片轮沿用改前"不入记忆"口径）。"""
    h = _make_handler(tmp_path)
    h._handle_image(URL, "这张图怎么样", user_id="u_img")
    assert h.memory_system.method_calls == []


def test_persist_failure_does_not_break_reply(tmp_path, allow_url):
    """落库异常不影响回复（优雅降级）。"""
    h = _make_handler(tmp_path)

    class _Boom:
        def add_message(self, *a, **kw):
            raise sqlite3.OperationalError("disk full")

    h.session_dao = _Boom()
    reply = h._handle_image(URL, "这张图怎么样", user_id="u_img")
    assert "图片" in reply and reply.strip()
