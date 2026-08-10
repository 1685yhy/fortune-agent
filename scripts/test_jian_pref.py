"""晨笺订阅偏好 DAO 测试"""
import os, sys, tempfile, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.storage.jian_dao import JianPrefDAO

def make_dao():
    fd, path = tempfile.mkstemp(suffix='.db'); os.close(fd)
    return JianPrefDAO(sqlite3.connect(path)), path

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

# 1. 建表+默认无数据
dao, path = make_dao()
check("初始无偏好", dao.get_pref("u1") is None)

# 2. upsert 插入
dao.upsert_pref("u1", {"jian_enabled": 1, "jian_time": "07:30",
                       "night_enabled": 1, "night_time": "23:00", "bound_status": "bound"})
p = dao.get_pref("u1")
check("插入成功", p and p["jian_time"] == "07:30" and p["jian_enabled"] == 1)

# 3. upsert 部分更新(只改时间不丢其他字段)
dao.upsert_pref("u1", {"jian_time": "08:00"})
p = dao.get_pref("u1")
check("部分更新保留", p["jian_time"] == "08:00" and p["night_time"] == "23:00")

# 4. list_enabled_at 按时刻筛选
dao.upsert_pref("u1", {"jian_time": "07:30"})  # 步骤3把u1改为08:00,此处先改回,确保其属于7:30组
dao.upsert_pref("u2", {"jian_enabled": 1, "jian_time": "07:30", "night_enabled": 0, "night_time": "", "bound_status": "bound"})
dao.upsert_pref("u3", {"jian_enabled": 0, "jian_time": "07:30", "night_enabled": 0, "night_time": "", "bound_status": "bound"})
r = dao.list_enabled_at("07:30", "jian")
check("7:30 晨笺组", "u1" in r and "u2" in r and "u3" not in r)
r2 = dao.list_enabled_at("23:00", "night")
check("23:00 晚安组", "u1" in r2)

# 5. 未绑定不可推送
dao.upsert_pref("u4", {"jian_enabled": 1, "jian_time": "07:30", "bound_status": "unbound"})
r3 = dao.list_enabled_at("07:30", "jian")
check("未绑定排除", "u4" not in r3)

print(f"\nALL PASS ({ok})")
