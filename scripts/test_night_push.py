"""晚安推送深夜版测试:深夜文案 + 入口落地页(复用晨笺 _send_jian_batch night 分支)"""
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
dao.upsert_pref("u1", {"night_enabled": 1, "night_time": "23:00", "jian_enabled": 0,
                       "bound_status": "bound", "mp_openid": "oU1"})

sent = []
def fake_send(oid, tpl, data, url=""):
    sent.append({"oid": oid, "tpl": tpl, "data": data, "url": url}); return {}

with mock.patch("src.services.wechat_mp.send_template", side_effect=fake_send), \
     mock.patch("src.services.wechat_mp.mp_ready", return_value=True), \
     mock.patch("src.main._precompute_jian_for", return_value={
         "date": "2026-08-12", "day_ganzhi": "庚午",
         "suitable": ["早睡", "静心"], "unsuitable": ["熬夜"], "quote": "火气偏旺"}):
    stats = main_mod._send_jian_batch(dao, "23:00", "night")
    check("推送 u1", stats["pushed"] == 1 and sent[0]["oid"] == "oU1")
    d = sent[0]["data"]
    check("深夜版标题", d["thing1"]["value"] == "明灯 · 夜话")
    check("深夜陪伴承诺", d["thing4"]["value"] == "今夜说的话,天亮就忘")
    check("灯语宜忌", d["thing3"]["value"].startswith("明日宜"))
    check("落地页 entry=night", sent[0]["url"].endswith("pages/chat/chat?entry=night"))

print(f"\nALL PASS ({ok})")
