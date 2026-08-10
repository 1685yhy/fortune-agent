"""轻私语管线测试:分类模板+红线(不点名事件/人物/原话)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest.mock as mock
from src.engines.jian_private import generate_private_line, _CATEGORY_TEMPLATES

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

# 1. 有事业类记忆 → 事业模板
fake_mem = [{"type": "topic", "subject": "事业", "content": "最近在找工作"}]
with mock.patch("src.engines.jian_private._recall", return_value=fake_mem):
    line = generate_private_line("u1")
    check("事业模板", "事业" in line and "找工作" not in line)  # 不点破具体事件

# 2. 无记忆 → 通用句
with mock.patch("src.engines.jian_private._recall", return_value=[]):
    line2 = generate_private_line("u2")
    check("无记忆通用句", len(line2) > 0)

# 3. 红线:任何输出不包含具体事件词
with mock.patch("src.engines.jian_private._recall", return_value=[{"type": "event", "subject": "换工作", "content": "2026年3月跳槽到字节"}]):
    line3 = generate_private_line("u3")
    check("红线过滤事件词", "字节" not in line3 and "跳槽" not in line3 and "3月" not in line3)

print(f"\nALL PASS ({ok})")
