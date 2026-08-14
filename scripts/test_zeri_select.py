"""择吉日 select_lucky_days 验证脚本（多日扫描 + 三层评分 + Top3）。

验证项:
1. 无八字 → 个人分默认 24（满分 30 折算 80%）
2. 有八字喜用神水: 水日比和 +5(20) / 金日生水 +10(25) / 无关日 15
3. 周末偏好开/关 → practical +10/0
4. exclude_dates 去重
5. 合格 <3 → suggest_wider=True, cards 如实返回 1-2 个
6. 旧 select() 接口回归（对话链路在用, 不破坏）
7. 确定性抽查: 2026-08 一整个月搬家扫描, 人工核对 Top3
   是否在黄历宜入宅/移徙且非破/闭（无 user_bazi → 冲宅主不判定, 属预期）

用法（服务同款 .venv）:
    cd /mnt/e/fortune-agent && .venv/bin/python3 scripts/test_zeri_select.py
退出码: 0 = 全部通过; 1 = 有失败项
"""
import os
import sys

# 保证从项目根目录 import src.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_PASS = 0
_FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {name}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def main():
    from src.engines.zeri import ZeriEngine, LuckyDayCard

    eng = ZeriEngine()

    # ------------------------------------------------------------------
    # 1. 无八字 → 个人分默认 24
    # ------------------------------------------------------------------
    print("== 1. 无八字默认分 24 ==")
    res = eng.select_lucky_days("搬家", "2026-08-08", "2026-08-18")
    print(f"  搬家 2026-08-08~18 cards: {[c.date for c in res['cards']]}")
    check("无八字 → 所有卡片 personal_score=24",
          bool(res["cards"]) and all(c.personal_score == 24 for c in res["cards"]),
          f"scores={[c.personal_score for c in res['cards']]}")

    # ------------------------------------------------------------------
    # 2. 有八字喜用神水: 比和/生扶/无关
    # ------------------------------------------------------------------
    print("== 2. 有八字喜用神水 ==")
    ub = {"yongshen": "水", "shengxiao": "鼠"}
    res = eng.select_lucky_days("搬家", "2026-08-08", "2026-08-18", user_bazi=ub)
    by_date = {c.date: c for c in res["cards"]}
    # 08-16 壬戌日(壬=水)比和 → 20; 08-10 丙辰日(丙=火)无关 → 15
    check("水日比和 +5 → 20", by_date["2026-08-16"].personal_score == 20,
          f"got {by_date['2026-08-16'].personal_score}")
    check("火日无关 → 15", by_date["2026-08-10"].personal_score == 15,
          f"got {by_date['2026-08-10'].personal_score}")
    p = eng._personal_score("庚", {"yongshen": "水"})
    check("金日生水 +10 → 25(喜用神相合)", p == (25, "喜用神相合"), f"got {p}")

    # ------------------------------------------------------------------
    # 3. 周末偏好开/关
    # ------------------------------------------------------------------
    print("== 3. 周末偏好 ==")
    on = eng.select_lucky_days("搬家", "2026-08-08", "2026-08-18", prefer_weekend=True)
    off = eng.select_lucky_days("搬家", "2026-08-08", "2026-08-18", prefer_weekend=False)
    on_by = {c.date: c for c in on["cards"]}
    off_by = {c.date: c for c in off["cards"]}
    # 08-16 周日 满日
    check("开 → 08-16 practical=10", on_by["2026-08-16"].practical_score == 10,
          f"got {on_by['2026-08-16'].practical_score}")
    check("关 → 08-16 practical=0", off_by["2026-08-16"].practical_score == 0,
          f"got {off_by['2026-08-16'].practical_score}")
    check("平日 08-10 无加分", on_by["2026-08-10"].practical_score == 0,
          f"got {on_by['2026-08-10'].practical_score}")

    # ------------------------------------------------------------------
    # 4. exclude_dates 去重
    # ------------------------------------------------------------------
    print("== 4. exclude_dates ==")
    res = eng.select_lucky_days(
        "搬家", "2026-08-08", "2026-08-18", exclude_dates=["2026-08-16"])
    check("排除 08-16 后不在 cards 中",
          all(c.date != "2026-08-16" for c in res["cards"]),
          f"cards={[c.date for c in res['cards']]}")
    check("未排除日 08-10 仍在", any(c.date == "2026-08-10" for c in res["cards"]),
          f"cards={[c.date for c in res['cards']]}")

    # ------------------------------------------------------------------
    # 5. suggest_wider 兜底: 出行 08-28~31 全为空亡日
    # ------------------------------------------------------------------
    print("== 5. suggest_wider ==")
    res = eng.select_lucky_days("出行", "2026-08-28", "2026-08-31")
    check("空亡窗口 → 0 卡 + suggest_wider + reason",
          res["cards"] == [] and res["suggest_wider"] is True and res["reason"],
          f"cards={res['cards']} suggest={res['suggest_wider']} reason={res['reason']}")
    # 窗口不足 3 天时如实返回 1-2 个（08-08~18 搬家仅 08-10/08-16 两日合格）
    res2 = eng.select_lucky_days("搬家", "2026-08-08", "2026-08-18", prefer_weekend=True)
    check("合格=2 → suggest_wider=True 且如实返回2张",
          res2["suggest_wider"] is True and len(res2["cards"]) == 2,
          f"cards={[c.date for c in res2['cards']]}")

    # ------------------------------------------------------------------
    # 6. 旧 select() 接口回归
    # ------------------------------------------------------------------
    print("== 6. 旧 select() 回归 ==")
    r1 = eng.select(2026, 8, 10, purpose="搬家")
    r2 = eng.select(2026, 8, 10, purpose="搬家")
    check("select 确定性", r1.jianchu == r2.jianchu and r1.yi == r2.yi and r1.ji == r2.ji,
          f"jc1={r1.jianchu} jc2={r2.jianchu}")
    check("2026-08-10 建除=成", r1.jianchu == "成", f"got {r1.jianchu}")
    check("字段完整", r1.ershibaxiu in
          ["角", "亢", "氐", "房", "心", "尾", "箕", "斗", "牛", "女", "虚", "危",
           "室", "壁", "奎", "娄", "胃", "昴", "毕", "觜", "参", "井", "鬼", "柳",
           "星", "张", "翼", "轸"] and "冲" in r1.chong,
          f"xiu={r1.ershibaxiu} chong={r1.chong}")

    # ------------------------------------------------------------------
    # 7. 确定性抽查: 2026-08 一整个月搬家扫描（人工核对）
    # ------------------------------------------------------------------
    print("== 7. 2026-08 搬家全月扫描（人工核对） ==")
    aug = eng.select_lucky_days("搬家", "2026-08-01", "2026-08-31", prefer_weekend=True)
    print(f"  scanned={aug['scanned']} 合格卡数={len(aug['cards'])} "
          f"suggest_wider={aug['suggest_wider']}")
    jc_of = {}
    for d in range(1, 32):
        jc_of[f"2026-08-{d:02d}"] = eng.select(2026, 8, d).jianchu
    for c in aug["cards"]:
        print(f"  {c.date} {c.lunar_text} 建除={jc_of[c.date]} 总分{c.total} "
              f"[场景{c.scene_score}/个人{c.personal_score}/实用{c.practical_score}] "
              f"理由={c.reason_source}")
        print(f"     宜={c.yi}")
        print(f"     忌={c.ji}")
        print(f"     吉时={c.jishi} 喜神={c.xi_fangwei} 财神={c.cai_fangwei}")

    check("scanned=31", aug["scanned"] == 31, f"got {aug['scanned']}")
    check("Top3 ≤ 3 张", len(aug["cards"]) <= 3, f"got {len(aug['cards'])}")
    check("合格=3 → suggest_wider=False", aug["suggest_wider"] is False,
          f"cards={[c.date for c in aug['cards']]}")
    # 人工核对记录（2026-08 已人工核对）:
    #   08-16 农历七月初四 壬戌日 星期日 满日 —— 黄历宜【入宅/移徙/安床】✓
    #   08-10 农历六月廿八 丙辰日 星期一 成日 —— 黄历宜【入宅/移徙/安床】✓
    #   08-26 农历七月十四 壬申日 星期三 建日 —— 黄历宜【入宅/移徙】✓
    #   三日建除均为满/成/建, 非破/闭日 ✓; 无 user_bazi → 冲宅主生肖不判定（预期, 不误伤）
    #   08-15/08-19/08-25/08-30 为三娘煞日, 但搬家场景不含三娘煞规则, 未排除属预期
    for c in aug["cards"]:
        assert jc_of[c.date] not in ("破", "闭"), f"{c.date} 不应为破/闭日"
        assert any(k in "".join(c.yi) for k in ("入宅", "移徙")), \
            f"{c.date} 黄历宜应含入宅/移徙"
    check("Top3 均宜入宅/移徙 且非破/闭", True)

    print()
    print(f"结果: {_PASS} passed, {_FAIL} failed")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
