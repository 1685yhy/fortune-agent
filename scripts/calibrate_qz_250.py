"""问真八字全字段批量校准脚本（250 案例，降速防封号）。

按 PM/协调员要求：
- 250 案例（原 500 减半），固定随机种子可复现
- 每请求 sleep 1-2s，每 50 案例暂停 30s
- 429/403/验证码响应立即停止
- 主对比 city 传空（排盘核心逻辑对比，问真 API 不接收城市）；
  真太阳时锚点见 tests/test_bazi_solar_time.py（长春锚点已固化）

对比字段：四柱8字 / 十神4 / 藏干 / 藏干十神 / 纳音4 / 空亡(per柱+日柱旬) /
自坐 / 星运 / 大运前8 / 起运虚岁 / 起运分解(qiyunarr) / 胎元 / 命宫 / 身宫(含纳音) / 神煞(交集)

用法：cd /mnt/e/fortune-agent-deploy && /home/a/fortune-run/.venv/bin/python scripts/calibrate_qz_250.py
输出：data/calibrate_qz_250.json（逐案例对比明细）+ 控制台汇总
"""
import json
import random
import sys
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lunar_python import Lunar, Solar

from src.engines.bazi import BaziEngine
from src.engines.bazi_formatter import (
    BRANCH_HIDDEN, CHANG_SHENG, CS_NAMES, XUN_KONG, NAYIN_TABLE, _get_shishen,
    get_changsheng, compute_cg_shishen,
)
from src.engines.shensha import SHENSHA_TABLE_NAMES, SHENSHA_LUCK

API = "https://bzapi3.iwzbz.com/getbasebz8.php?d={d}&s=0&s={g}&today=2026-8-19"
OUT = Path(__file__).resolve().parent.parent / "data" / "calibrate_qz_250.json"
N_CASES = 250
SLEEP = (1.0, 2.0)
PAUSE_EVERY = 50
PAUSE_SECS = 30
RETRIES = 2

# 各年立春等节气的常见公历日期（用于节气前后案例生成，±1 日偏差可接受）
JIE_DAYS = {
    1: (5, 6), 2: (4,), 3: (5, 6), 4: (4, 5), 5: (5, 6), 6: (5, 6),
    7: (7,), 8: (7, 8), 9: (7, 8), 10: (8,), 11: (7,), 12: (6, 7),
}
HOURS = [0, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23]


def gen_cases(n: int, seed: int = 20260819) -> list:
    """生成 n 个多样化案例：1940-2025、四季、节气前后、闰月、各时辰。"""
    rng = random.Random(seed)
    cases = []
    while len(cases) < n:
        y = rng.randint(1940, 2025)
        roll = rng.random()
        if roll < 0.35:
            # 节气前后 ±2 日（立春/立夏/立秋/立冬等 12 节）
            m = rng.randint(1, 12)
            day = rng.choice(JIE_DAYS[m]) + rng.randint(-2, 2)
            if day < 1:
                m, day = m - 1, 28
            elif day > 28:
                m, day = m % 12 + 1, rng.randint(1, 3)
            d = max(1, min(28, day))
        else:
            m = rng.randint(1, 12)
            d = rng.randint(1, 28)
        # 闰月日期（约 15%）：对随机年找闰月并取月中日期
        if roll >= 0.85:
            try:
                for leap_m in range(1, 13):
                    ld = Lunar.fromYmd(y, leap_m, 15, 12, 0, 0)
                    if "闰" in ld.getMonthInChinese():
                        s = ld.getSolar()
                        y2, m2, d2 = s.getYear(), s.getMonth(), s.getDay()
                        h2 = rng.choice(HOURS)
                        cases.append((y2, m2, d2, h2, rng.randint(0, 59), rng.choice("男女")))
                        break
                else:
                    continue
            except Exception:
                pass
            else:
                continue
        h = rng.choice(HOURS)
        mi = rng.randint(0, 59)
        cases.append((y, m, d, h, mi, rng.choice("男女")))
    return cases


def fetch(d: str, g: int) -> dict:
    """调问真 API（重试 2 次；429/403/验证码立即抛 AbortError 中止全程）。"""
    url = API.format(d=d, g=g)
    last = None
    for attempt in range(RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=20).read()
            if b"captcha" in raw.lower() or "验证码" in raw.decode("utf-8", "ignore"):
                raise AbortError("captcha detected")
            return json.loads(raw)
        except AbortError:
            raise
        except Exception as ex:
            code = getattr(ex, "code", None)
            if code in (429, 403):
                raise AbortError(f"HTTP {code}: {ex}")
            last = ex
            time.sleep(2.0)
    raise last


class AbortError(Exception):
    pass


def main():
    cases = gen_cases(N_CASES)
    print(f"生成 {len(cases)} 案例，开始校准（sleep {SLEEP[0]}-{SLEEP[1]}s，每 {PAUSE_EVERY} 例暂停 {PAUSE_SECS}s）")
    engine = BaziEngine()
    results = []
    aborted = False

    for i, (y, m, d, h, mi, gender) in enumerate(cases):
        d_str = f"{y:04d}-{m:02d}-{d:02d}%20{h:02d}:{mi:02d}"
        g = 1 if gender == "男" else 0
        try:
            wz = fetch(d_str, g)
        except AbortError as ex:
            print(f"\n!! 已中止（{ex}），保存已收集 {len(results)} 案例")
            aborted = True
            break
        except Exception as ex:
            print(f"!! 案例 {y}-{m}-{d} {h}:{mi} 获取失败跳过: {ex}")
            continue

        r = engine.calculate(y, m, d, h, mi, "", gender)
        wz_p = [wz["bz"][str(i_)] + wz["bz"][str(i_ + 1)] for i_ in range(0, 8, 2)]
        day_gan = r.bazi[2][0]

        # ---------- 各字段对比 ----------
        cmp = {
            "四柱": (list(r.bazi), wz_p),
            "十神": (list(r.shishen), list(wz.get("ss", []))),
            "藏干": ([BRANCH_HIDDEN.get(p[1], ["?"]) for p in r.bazi], list(wz.get("cg", []))),
            "藏干十神": ([compute_cg_shishen(day_gan, BRANCH_HIDDEN.get(p[1], ["?"])) for p in r.bazi], list(wz.get("cgss", []))),
            "纳音": (list(r.nayin), list(wz.get("ny", []))),
            "空亡": (list(r.kongwang), list(wz.get("kw", []))),
            "日旬空亡": (r.kongwang_day, wz.get("kongwang", "")),
            "自坐": ([get_changsheng(p[0], p[1]) for p in r.bazi], list(wz.get("zz", []))),
            "星运": ([get_changsheng(day_gan, p[1]) for p in r.bazi], list(wz.get("xy", []))),
            "大运8": ([g_ for _, g_ in r.dayun[:8]], list(wz.get("dayun", []))[:8]),
            "起运虚岁": (r.dayun[0][0] if r.dayun else None, wz.get("qiyunsui")),
            "起运分解": (list(r.qiyun_detail), list(wz.get("qiyunarr", []))[:5]),
            "胎元": (r.taiyuan, wz.get("taiyuan", "")),
            "命宫": (r.minggong, wz.get("minggong", "")),
            "身宫": (r.shenggong, wz.get("shenggong", "")),
            "胎元纳音": (r.taiyuan_nayin, wz.get("taiyuan_nayin", "")),
            "命宫纳音": (r.minggong_nayin, wz.get("minggong_nayin", "")),
            "身宫纳音": (r.shenggong_nayin, wz.get("shenggong_nayin", "")),
        }
        # ---------- 神煞（交集口径） ----------
        qz_ss = [n for lst in wz.get("szshensha", []) for n in lst if n]
        qz_known = {n for n in qz_ss if n in SHENSHA_TABLE_NAMES or n in SHENSHA_LUCK}
        ours_known = {n for n in r.shensha if n in SHENSHA_TABLE_NAMES or n in SHENSHA_LUCK}
        over_report = sorted(ours_known - qz_known)   # 我们报但问真没有（同词表内）
        miss_report = sorted(qz_known - ours_known)   # 问真有我们漏（同词表内）
        missing_kind = sorted({n for n in qz_ss if n not in SHENSHA_TABLE_NAMES and n not in SHENSHA_LUCK})

        failed = {k: {"ours": o, "qz": t} for k, (o, t) in cmp.items() if o != t}
        if over_report:
            failed["神煞超报"] = {"ours": over_report, "qz": "同词表内问真无"}
        if miss_report:
            failed["神煞漏报"] = {"ours": "同词表内我们无", "qz": miss_report}

        results.append({
            "case": f"{y}-{m:02d}-{d:02d} {h:02d}:{mi:02d} {gender}",
            "ok": len(failed) == 0,
            "n_fields": len(cmp),
            "failed": failed,
            "missing_kind": missing_kind,
            "ours_shensha": list(r.shensha),
            "qz_shensha": qz_ss,
        })
        if (i + 1) % 10 == 0:
            ok = sum(1 for x in results if x["ok"])
            print(f"  进度 {i+1}/{len(cases)}: 完全一致 {ok}/{len(results)}")
        time.sleep(random.uniform(*SLEEP))
        if (i + 1) % PAUSE_EVERY == 0:
            print(f"  --- 暂停 {PAUSE_SECS}s 防限频 ---")
            time.sleep(PAUSE_SECS)

    # ---------- 汇总 ----------
    valid = [x for x in results]
    ok_n = sum(1 for x in valid if x["ok"])
    print(f"\n{'='*70}")
    print(f"问真校准汇总: {len(valid)} 有效案例（{len(cases)-len(valid)} 获取失败跳过）")
    print(f"完全一致: {ok_n}/{len(valid)} ({ok_n/max(1,len(valid))*100:.1f}%)")
    if aborted:
        print("!! 注意: 因限频/风控提前中止")

    # 按字段统计
    field_stats = {}
    for x in valid:
        for k in x["failed"]:
            field_stats[k] = field_stats.get(k, 0) + 1
    if field_stats:
        print(f"\n差异字段分布 (前15):")
        for k, c in sorted(field_stats.items(), key=lambda kv: -kv[1])[:15]:
            print(f"  {k}: {c}/{len(valid)} ({c/max(1,len(valid))*100:.1f}%)")

    # 缺失神煞种类清单
    all_missing = {}
    for x in valid:
        for n in x.get("missing_kind", []):
            all_missing[n] = all_missing.get(n, 0) + 1
    if all_missing:
        print(f"\n问真独有神煞（我们未建，不判失败，记录清单）:")
        for n, c in sorted(all_missing.items(), key=lambda kv: -kv[1]):
            print(f"  {n}: {c} 案例")

    # 失败样例
    bad = [x for x in valid if not x["ok"]]
    if bad:
        print(f"\n失败样例（前 8）:")
        for x in bad[:8]:
            print(f"  {x['case']}:")
            for k, v in x["failed"].items():
                print(f"    {k}: ours={v['ours']} qz={v['qz']}")

    OUT.write_text(json.dumps({"summary": {
        "total": len(valid), "ok": ok_n, "aborted": aborted,
        "field_stats": field_stats, "missing_kind": all_missing,
    }, "results": valid}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n明细已保存: {OUT}")


if __name__ == "__main__":
    main()
