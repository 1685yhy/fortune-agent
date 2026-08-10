"""调度逻辑测试:预生成缓存 + 按偏好时间批量发送(mock 发送通道)"""
import os, sys, tempfile, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest.mock as mock
from src.storage.jian_dao import JianPrefDAO
import src.main as main_mod

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

fd, path = tempfile.mkstemp(suffix='.db'); os.close(fd)
dao = JianPrefDAO(sqlite3.connect(path))
# 注:brief 原始测试数据未含 mp_openid,导致发送路径必然走 skipped;
# 补上 openid 才能真实走到 send_template 下发路径(实现要求无 openid 跳过)。
dao.upsert_pref("u1", {"jian_enabled": 1, "jian_time": "07:30", "night_enabled": 0, "night_time": "", "bound_status": "bound", "mp_openid": "o_u1"})
dao.upsert_pref("u2", {"jian_enabled": 1, "jian_time": "08:00", "night_enabled": 0, "night_time": "", "bound_status": "bound", "mp_openid": "o_u2"})

# 1. 预生成缓存
with mock.patch("src.engines.jian_quote.generate_daily_quote", return_value={"quote": "金句", "book": "穷通宝鉴"}):
    content = main_mod._precompute_jian_for("2026-08-11")
    check("预生成含宜忌", "suitable" in content and "quote" in content)

# 2. 按 07:30 只推 u1
# 注:本块会以"今天"真实调用 _precompute_jian_for → generate_daily_quote,
# 真实管线含 bge-m3 + FAISS + LLM 核对(网络/耗时),继续 mock 金句管线保证测试封闭。
sent = []
with mock.patch("src.services.wechat_mp.send_template",
                side_effect=lambda oid, tpl, data, url="": sent.append(oid) or {}), \
     mock.patch("src.services.wechat_mp.mp_ready", return_value=True), \
     mock.patch("src.engines.jian_quote.generate_daily_quote",
                return_value={"quote": "金句", "book": "穷通宝鉴"}):
    stats = main_mod._send_jian_batch(dao, "07:30", "jian")
    check("7:30 只推 u1", sent == ["o_u1"] and stats["pushed"] == 1)

print(f"\nALL PASS ({ok})")
