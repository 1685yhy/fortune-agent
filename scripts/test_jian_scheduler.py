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

# 3. 失败链(P1): 连续 3 次发送失败 → bound_status=invalid,自动退出推送名单
# 注:u1 同在 07:30 组也会连续失败并最终失效,属预期;u2 在 08:00 组不受影响。
dao.upsert_pref("u3", {"jian_enabled": 1, "jian_time": "07:30", "night_enabled": 0,
                       "night_time": "", "bound_status": "bound", "mp_openid": "o_u3"})
with mock.patch("src.services.wechat_mp.send_template",
                side_effect=RuntimeError("模拟模板消息通道故障")), \
     mock.patch("src.services.wechat_mp.mp_ready", return_value=True):
    main_mod._send_jian_batch(dao, "07:30", "jian")
    check("失败1次 fail_count=1", dao.get_pref("u3")["fail_count"] == 1)
    main_mod._send_jian_batch(dao, "07:30", "jian")
    check("失败2次 fail_count=2", dao.get_pref("u3")["fail_count"] == 2)
    main_mod._send_jian_batch(dao, "07:30", "jian")
    check("失败3次 bound_status=invalid", dao.get_pref("u3")["bound_status"] == "invalid")
r3 = dao.list_enabled_at("07:30", "jian")
check("失效后不在推送名单", "u3" not in r3 and "u1" not in r3)

# 4. 成功后 fail_count 清零(重新绑定为 bound 后恢复推送)
dao.upsert_pref("u3", {"bound_status": "bound"})
sent2 = []
with mock.patch("src.services.wechat_mp.send_template",
                side_effect=lambda oid, tpl, data, url="": sent2.append(oid) or {}), \
     mock.patch("src.services.wechat_mp.mp_ready", return_value=True), \
     mock.patch("src.engines.jian_quote.generate_daily_quote",
                return_value={"quote": "金句", "book": "穷通宝鉴"}):
    stats2 = main_mod._send_jian_batch(dao, "07:30", "jian")
check("恢复后只推 u3", sent2 == ["o_u3"] and stats2["pushed"] == 1)
check("成功清零 fail_count", dao.get_pref("u3")["fail_count"] == 0)

# 5. 晚安按日生成(P1): thing1 含当日干支,thing2 含"明日宜/忌"与当日宜忌内容
dao.upsert_pref("u4", {"jian_enabled": 0, "jian_time": "", "night_enabled": 1,
                       "night_time": "23:00", "bound_status": "bound", "mp_openid": "o_u4"})
night_sent = []
fake_night_content = {"day_ganzhi": "庚申", "suitable": ["出行"], "unsuitable": ["借贷"]}
with mock.patch("src.services.wechat_mp.send_template",
                side_effect=lambda oid, tpl, data, url="": night_sent.append((oid, data)) or {}), \
     mock.patch("src.services.wechat_mp.mp_ready", return_value=True), \
     mock.patch("src.main._precompute_jian_for", return_value=fake_night_content):
    stats_n = main_mod._send_jian_batch(dao, "23:00", "night")
    check("23:00 晚安只推 u4", night_sent and night_sent[0][0] == "o_u4" and stats_n["pushed"] == 1)
    night_data = night_sent[0][1]
    check("晚安 thing1 含干支", "庚申" in night_data["thing1"]["value"] and "夜深了" in night_data["thing1"]["value"])
    check("晚安 thing1 ≤20字", len(night_data["thing1"]["value"]) <= 20)
    check("晚安 thing2 用当日宜忌", "明日宜出行" in night_data["thing2"]["value"] and "忌借贷" in night_data["thing2"]["value"])
    check("晚安 thing2 ≤20字", len(night_data["thing2"]["value"]) <= 20)

print(f"\nALL PASS ({ok})")
