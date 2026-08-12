"""合盘聚合引擎测试：双引擎归一/等级边界/特征提取/缘笺脱敏"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.engines.hehun import HehunEngine
from src.engines.union import (normalize_score, get_level, extract_features,
                               desensitize_birth, run_union)
from src.api.compatibility import _compute_match_score

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

class FakeBazi:
    def __init__(self, bazi, day_master, wuxing, shensha, nayin):
        self.bazi = bazi
        self.day_master = day_master
        self.wuxing = wuxing
        self.shensha = shensha
        self.nayin = nayin

class FakePerson:
    def __init__(self, year, month, day):
        self.year = year; self.month = month; self.day = day

# 子×丑 → 生肖六合；双方皆带天乙贵人；纳音 天上火×大林木（木生火，不相克）
fake1 = FakeBazi(bazi=[["甲","子"],["丙","寅"],["戊","午"],["庚","申"]],
                 day_master="戊土", wuxing={"木":2,"火":2,"土":1,"金":2,"水":1},
                 shensha=["天乙贵人"], nayin=["海中金","炉中火","天上火","石榴木"])
fake2 = FakeBazi(bazi=[["乙","丑"],["丁","卯"],["己","酉"],["辛","亥"]],
                 day_master="己土", wuxing={"木":1,"火":1,"土":2,"金":2,"水":2},
                 shensha=["天乙贵人"], nayin=["海中金","炉中火","大林木","平地木"])

# 1. 双引擎归一
check("归一 100×100", normalize_score(100, 100) == 100)
check("归一 50×50", normalize_score(50, 50) == 50)
check("归一 40×60", normalize_score(40, 60) == 48)
check("归一越界收敛", 0 <= normalize_score(-5, 999) <= 100)

# 2. 等级边界（与 love.py SCORE_LEVELS 一致）
check("90 天作之合", get_level(90)["label"] == "天作之合")
check("89 情投意合", get_level(89)["label"] == "情投意合")
check("60 和而不同", get_level(60)["label"] == "和而不同")
check("59 细水长流", get_level(59)["label"] == "细水长流")

# 3. 特征提取（真实引擎跑 fake 命盘）
hr = HehunEngine().match(fake1, fake2)
cm = _compute_match_score(fake1, fake2)
feats = extract_features(hr, cm, fake1, fake2)
check("生肖六合特征", any("六合" in f for f in feats))
check("双天乙贵人特征", "双天乙贵人" in feats)
check("特征去重保序", len(feats) == len(set(feats)))

# 4. 脱敏显示串：无时辰/无出生地/无姓名，格式固定
birth_a = desensitize_birth(1990, 5, 20, "戊午", "鼠")
check("脱敏格式", re.match(r"^[一-鿿]{2} · \d{4}年\d{1,2}月\d{1,2}日 属[一-鿿]$", birth_a) is not None)
check("脱敏不含时辰", "时" not in birth_a and "23" not in birth_a)

# 5. run_union 聚合：结构完整
u = run_union(hr, cm, fake1, fake2, FakePerson(1990, 5, 20), FakePerson(1992, 8, 15),
              relation="恋人", quote={"full": "金玉相逢，良缘可期"})
check("评分范围", 0 <= u["score"] <= 100 and u["levelLabel"] in ("天作之合", "情投意合", "相得益彰", "和而不同", "细水长流"))
check("三维得分条", u["dimensions"]["wuxing"]["max"] == 40 and u["dimensions"]["shengxiao"]["max"] == 25
      and u["dimensions"]["rizhu"]["max"] == 35)
check("transient 标记", u.get("transient") is True)
check("缘笺数据完整", u["yuan_card"]["sealChar"] == "丑牛" and u["yuan_card"]["disclaimer"] == "签文只作心意，不作断言")
check("缘笺生辰脱敏", "时" not in u["yuan_card"]["birthA"] and "上海" not in u["yuan_card"]["birthB"])
check("报告素材明细", "compat" in u["raw"] and "hehun_wuxing" in u["raw"])

print(f"\nALL PASS ({ok})")
