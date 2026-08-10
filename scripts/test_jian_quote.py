"""古籍金句管线测试(规则部分纯逻辑;RAG/LLM 部分 mock)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest.mock as mock
from src.engines.jian_quote import generate_daily_quote, _filter_candidates, _reset_cache

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

# 1. 筛选规则:长度/书名/无书名剔除
cands = [
    {"text": "申月金旺,逢土生扶,反为有用之才", "book": "穷通宝鉴"},
    {"text": "太短", "book": "滴天髓"},
    {"text": "x" * 60, "book": "三命通会"},  # 超长
    {"text": "无名句", "book": ""},            # 无书名
]
good = _filter_candidates(cands)
check("长度与书名筛选", len(good) == 1 and good[0]["book"] == "穷通宝鉴")

# 2. 30 天不重复
_reset_cache()
fake_r = {"text": "申月金旺,逢土生扶,反为有用之才", "book": "穷通宝鉴"}
with mock.patch("src.engines.jian_quote._retrieve_candidates", return_value=[fake_r, fake_r]):
    r1 = generate_daily_quote("2026-08-10", "庚申")
    r2 = generate_daily_quote("2026-08-11", "辛酉")
    check("首日有金句", r1 is not None and r1["book"] == "穷通宝鉴")
    check("次日不重复", r2 is not None and r2["quote"] != r1["quote"] or r2 is None)

# 3. 无合格候选 → None(宁缺毋滥)
with mock.patch("src.engines.jian_quote._retrieve_candidates", return_value=[]):
    r3 = generate_daily_quote("2026-08-12", "壬戌")
    check("空候选返回 None", r3 is None)

print(f"\nALL PASS ({ok})")
