"""晨笺订阅偏好 DAO 测试"""
import os, sys, tempfile, sqlite3, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.storage.jian_dao import JianPrefDAO

def make_dao():
    fd, path = tempfile.mkstemp(suffix='.db'); os.close(fd)
    # check_same_thread=False 与生产一致(dao.get_conn),并发测试需跨线程复用连接
    return JianPrefDAO(sqlite3.connect(path, check_same_thread=False)), path

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

# 6. 并发 upsert 不丢字段(写锁保护读-改-写窗口;无锁时两线程各写自己快照会互相覆盖)
dao_c, _ = make_dao()
barrier = threading.Barrier(2)
def w1():
    barrier.wait()
    for _ in range(50):
        dao_c.upsert_pref("u_conc", {"jian_time": "08:00"})
def w2():
    barrier.wait()
    for _ in range(50):
        dao_c.upsert_pref("u_conc", {"night_time": "22:00"})
t1, t2 = threading.Thread(target=w1), threading.Thread(target=w2)
t1.start(); t2.start(); t1.join(); t2.join()
p = dao_c.get_pref("u_conc")
check("并发 upsert 合并字段", p and p["jian_time"] == "08:00" and p["night_time"] == "22:00")

# 7. 连续失败计数(P1): 默认 0,bump +1,reset 归零
dao.upsert_pref("u5", {"jian_enabled": 1, "jian_time": "07:30", "bound_status": "bound"})
check("fail_count 默认 0", dao.get_pref("u5")["fail_count"] == 0)
dao.bump_fail("u5"); dao.bump_fail("u5"); dao.bump_fail("u5")
check("bump 3 次 fail_count=3", dao.get_pref("u5")["fail_count"] == 3)
dao.reset_fail("u5")
check("reset 归零", dao.get_pref("u5")["fail_count"] == 0)
# bump/reset 不影响其他字段
p5 = dao.get_pref("u5")
check("计数不影响绑定态", p5["bound_status"] == "bound" and p5["jian_enabled"] == 1)

# 8. 老库迁移: 缺 fail_count 列的既有表自动 ALTER 补齐(默认 0)
fd2, path2 = tempfile.mkstemp(suffix='.db'); os.close(fd2)
legacy = sqlite3.connect(path2)
legacy.execute("""CREATE TABLE jian_prefs (
    user_id TEXT PRIMARY KEY, jian_enabled INTEGER DEFAULT 0, jian_time TEXT DEFAULT '07:30',
    night_enabled INTEGER DEFAULT 0, night_time TEXT DEFAULT '23:00',
    bound_status TEXT DEFAULT 'unbound', mp_openid TEXT DEFAULT '', updated_at REAL)""")
legacy.execute("INSERT INTO jian_prefs (user_id, jian_enabled) VALUES ('legacy1', 1)")
legacy.commit()
dao_legacy = JianPrefDAO(legacy)
p_legacy = dao_legacy.get_pref("legacy1")
check("老库迁移补 fail_count", p_legacy and p_legacy["fail_count"] == 0)
dao_legacy.bump_fail("legacy1")
check("迁移后 bump 可用", dao_legacy.get_pref("legacy1")["fail_count"] == 1)

print(f"\nALL PASS ({ok})")
