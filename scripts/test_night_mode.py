"""深夜时段判定引擎测试:三档预设 + 跨日 + 边界(纯逻辑,无网络)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime
from src.engines.night_mode import is_night_mode, night_window

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

def t(h, m=0):
    return datetime(2026, 8, 12, h, m)

# 标准档 21:00-01:00
check("标准 21:00 亮灯", is_night_mode(t(21, 0)))
check("标准 20:59 未亮", not is_night_mode(t(20, 59)))
check("标准 23:59 深夜", is_night_mode(t(23, 59)))
check("标准 00:30 深夜(跨日)", is_night_mode(t(0, 30)))
check("标准 01:00 熄灯", not is_night_mode(t(1, 0)))

# 早睡党 20:00-23:00(不跨日)
check("早睡 20:00 亮灯", is_night_mode(t(20, 0), "early"))
check("早睡 23:30 熄灯", not is_night_mode(t(23, 30), "early"))
check("早睡 00:30 不跨日", not is_night_mode(t(0, 30), "early"))

# 夜猫子 22:00-02:00
check("夜猫 01:30 深夜", is_night_mode(t(1, 30), "night"))
check("夜猫 02:00 熄灯", not is_night_mode(t(2, 0), "night"))

# 白天 / 未知档位回退
check("白天 15:00 全档熄灯",
      not is_night_mode(t(15, 0)) and not is_night_mode(t(15, 0), "night"))
check("未知档位回退标准", is_night_mode(t(22, 0), "???") and night_window("???") == (21, 1))

print(f"\nALL PASS ({ok})")
