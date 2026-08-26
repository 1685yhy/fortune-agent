# src/engine/extract/extract_tiandisui.py
"""滴天髓阐微 命例提取：识别竖排四柱块 + 捕获后续断语，输出考卷 jsonl。

用法: python -m src.engine.extract.extract_tiandisui <txt路径> <输出jsonl路径>

排版适配说明（2026-08-15 对照原文实测确认）：
1. 四柱竖排为 2 字一行（年/月/日/时各一行），但"时柱+次例年柱"常合并成
   4 字一行（如 "丙子丙申"），偶有 6 字残行（如 L6375 "壬辰乙未丙申"，
   藏真实时柱 + 补足开头），且每段末尾会跟 6~8 个干支（六十甲子顺排/逆排补足）。
   故凡纯干支字符行一律按 2 字 token 切分（2/4/6/8 字 = 1~4 个 token），
   合法干支对并入当前 token 流；chunk 非法者（如 L6586 "午辰"）跳过而不切断
   token 流（切断会让其后补足干支冒充新命例）。以"连续干支 token 流"为单位切段，
   每 4 个 token 为一组四柱，取段首第一组为命例四柱（段内其余为补足干支）。
2. 断语紧跟整段之后，收集至下一段开始，或章名 / 原注 / 任氏曰 为止；
   章诀短行与残缺行（如 "戊圾"）剔除。
3. 四柱行序校验（五虎遁定月柱 + 五鼠遁定时柱）写入 audit：
   校验失败者视为誊录误，quality=rejected；audit 注明具体不合之柱与应为值。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PILL_LINE = re.compile(r"^[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]$")
PILL_TEXT = re.compile(r"^[甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥]{2,}$")
CHAPTER_RE = re.compile(r"^[一二三四五六七八九十]+、")

STEMS = "甲乙丙丁戊己庚辛壬癸"
BRANCHES = "子丑寅卯辰巳午未申酉戌亥"
# 五虎遁：年干 -> 寅月天干；五鼠遁：日干 -> 子时天干
HU = {"甲": "丙", "己": "丙", "乙": "戊", "庚": "戊", "丙": "庚", "辛": "庚",
      "丁": "壬", "壬": "壬", "戊": "甲", "癸": "甲"}
SHU = {"甲": "甲", "己": "甲", "乙": "丙", "庚": "丙", "丙": "戊", "辛": "戊",
       "丁": "庚", "壬": "庚", "戊": "壬", "癸": "壬"}
# 阶段1 可跑的十神格名（与 Task4 geju.py 输出一一对应）
GEJU_NAMES = ("正官格", "七杀格", "正印格", "偏印格", "食神格", "伤官格",
              "正财格", "偏财格", "建禄格", "月刃格")
JIE_MONTH = {"孟春": "寅", "仲春": "卯", "季春": "辰", "孟夏": "巳", "仲夏": "午",
             "季夏": "未", "孟秋": "申", "仲秋": "酉", "季秋": "戌", "孟冬": "亥",
             "仲冬": "子", "季冬": "丑"}
AUDIT_BASE = "2026-08-15 脚本提取；geju 仅取断语明示；未明示者为 reference 待阶段2"

DAY_RE = re.compile(r"(?:日干|日元|日主)([甲乙丙丁戊己庚辛壬癸])")
DAY_PILL_RE = re.compile(r"([甲乙丙丁戊己庚辛壬癸])[子丑寅卯辰巳午未申酉戌亥]?日元")
DAY_BORN_RE = re.compile(r"([甲乙丙丁戊己庚辛壬癸])(?:木|金|水|火|土)?生于")
MONTH_BORN_RE = re.compile(r"生于(?:([子丑寅卯辰巳午未申酉戌亥])月|(孟|仲|季)(春|夏|秋|冬))")


def _month_pillar(year_stem: str, month_branch: str) -> str:
    """五虎遁：年干+月支 -> 月柱天干。"""
    start = HU[year_stem]
    idx = (STEMS.index(start) + ((BRANCHES.index(month_branch) - 2) % 12)) % 10
    return STEMS[idx]


def _hour_pillar(day_stem: str, hour_branch: str) -> str:
    """五鼠遁：日干+时支 -> 时柱天干。"""
    start = SHU[day_stem]
    idx = (STEMS.index(start) + BRANCHES.index(hour_branch)) % 10
    return STEMS[idx]


def _pills_valid(pills: list[str]) -> bool:
    """四柱行序校验：月柱合五虎遁、时柱合五鼠遁，否则为誊录误。"""
    return (pills[1][0] == _month_pillar(pills[0][0], pills[1][1])
            and pills[3][0] == _hour_pillar(pills[2][0], pills[3][1]))


def _chunk_pills(s: str) -> tuple[list[str], list[str]]:
    """纯干支字符行按 2 字切块，返回 (合法干支对, 非法chunk)。

    行可为 2/4/6/8 字（1~4 个 token，8 字行=两个柱）。非法 chunk
    （如 L6586 "午辰庚戌" 中的 "午辰"）由调用方跳过且不切断 token 流：
    切断会让其后补足干支冒充新命例（曾致 tds_0429/tds_0444 假条目）。
    """
    valid: list[str] = []
    invalid: list[str] = []
    for i in range(0, len(s), 2):
        c = s[i:i + 2]
        (valid if PILL_LINE.match(c) else invalid).append(c)
    return valid, invalid


def _is_stop(s: str) -> bool:
    """断语收集终止行：章名 / 大节 / 原注 / 任氏曰（其后为理论，非本命例断语）。"""
    if CHAPTER_RE.match(s):
        return True
    if s in ("通神论", "六亲论"):
        return True
    if s.startswith(("任氏曰", "原注")):
        return True
    return False


def _is_garbage(s: str) -> bool:
    """非断语行：章诀短行 / 残缺干支串（如"戊圾"；整行无合法干支对的纯干支残行）。"""
    if re.fullmatch(r"[甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥]{4,}", s):
        return True
    if len(s) <= 24 and not any(k in s for k in ("造", "日元", "日干", "日主", "生于")):
        return True
    return False


def _find_geju(prose: str) -> str:
    """断语明示的十神格名（可跑 Task4 的 10 格），未明示返回空串。

    只在断语前 60 字内查找：断语开头先述本命例格局，
    后半多为泛论（如"羊刃局…伤官格，清则谦和"），不可作本命例 expected。
    """
    head = prose[:60]
    for name in GEJU_NAMES:
        if name in head:
            return name
    return ""


def _day_mismatch(pills: list[str], prose: str) -> bool:
    """断语明示日干（X日元/日干X/X五行生于…）与日柱不符则 True。

    "X五行生于"仅取断语前 60 字（开头述日主；后文述他干，如"夫甲木生于季春"）。
    """
    got = ""
    head = prose[:60]
    for rx in (DAY_RE, DAY_PILL_RE):
        m = rx.search(prose)
        if m and m.group(1):
            got = m.group(1)
            break
    else:
        m = DAY_BORN_RE.search(head)
        if m and m.group(1):
            got = m.group(1)
    return bool(got) and got != pills[2][0]


def _month_mismatch(pills: list[str], prose: str) -> bool:
    """断语明示月支（生于X月/孟仲季X）与月柱不符则 True（取断语前 60 字）。"""
    m = MONTH_BORN_RE.search(prose[:60])
    if not m:
        return False
    want = m.group(1) or JIE_MONTH.get(m.group(2) + m.group(3), "")
    return bool(want) and want != pills[1][1]


def extract(path: str) -> list[dict]:
    """滴天髓阐微 txt -> 命例考卷 list[dict]（Case schema 同构）。"""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    cases: list[dict] = []
    pills: list[str] = []             # 当前段的连续干支 token
    run_start = 0                     # 段首行号
    run_end = 0                       # 段末行号（最后一个干支行）
    run_dropped: tuple[int, list[str]] | None = None  # 本段被跳过的残行 (行号, chunks)
    pending: dict | None = None       # 断语收集中的 case（尚未 finalize）
    pending_prose: list[tuple[int, str]] = []  # (行号, 断语行)
    collecting = False

    def close_pending() -> None:
        """收尾当前 case：定 prose / expected / quality / audit。"""
        nonlocal pending, pending_prose, collecting
        if pending is None:
            return
        pills4 = pending["pills"]
        prose = "".join(t for _, t in pending_prose).strip()[:600]
        valid = _pills_valid(pills4)
        day_bad = _day_mismatch(pills4, prose)
        month_bad = _month_mismatch(pills4, prose)
        geju = "" if (day_bad or month_bad) else _find_geju(prose)
        expected = {"geju": geju} if geju else {}
        last = pending_prose[-1][0] if pending_prose else pending["_run_end"]
        audit = AUDIT_BASE
        if not valid:
            fails = []
            mstem = _month_pillar(pills4[0][0], pills4[1][1])
            if pills4[1][0] != mstem:
                fails.append(f"月柱{pills4[1]}不合五虎遁(应为{mstem}{pills4[1][1]})")
            hstem = _hour_pillar(pills4[2][0], pills4[3][1])
            if pills4[3][0] != hstem:
                fails.append(f"时柱{pills4[3]}不合五鼠遁(应为{hstem}{pills4[3][1]})")
            audit += "；四柱校验失败(" + "、".join(fails) + ",疑似誊录误)"
        else:
            audit += "；四柱合法(五虎遁+五鼠遁)"
        if pending.get("_dropped"):
            dline, dchunks = pending["_dropped"]
            audit += f"；原文本段L{dline}有残行干支({''.join(dchunks)})已跳过"
        if day_bad:
            audit += f"；断语日干与日柱不符(日柱{pills4[2]})"
        if month_bad:
            audit += f"；断语月支与月柱不符(月柱{pills4[1]})"
        if not valid:
            quality = "rejected"
        elif expected:
            quality = "unit"
        else:
            quality = "reference"
        pending.update({
            "source_lines": f"{pending['_run_start']}-{last}",
            "expected": expected,
            "prose": prose,
            "quality": quality,
            "audit": audit,
        })
        pending.pop("_run_start")
        pending.pop("_run_end")
        pending.pop("_dropped", None)
        cases.append(pending)
        pending = None
        pending_prose = []
        collecting = False

    def close_run() -> None:
        """段结束：满四柱则开新 case；不足则其后断语并入上一 case。"""
        nonlocal pills, collecting, pending, run_dropped
        if not pills:
            return
        if len(pills) >= 4:
            close_pending()
            pending = {
                "_run_start": run_start,
                "_run_end": run_end,
                "_dropped": run_dropped,
                "id": "",
                "source": "滴天髓阐微.txt",
                "source_lines": "",
                "pills": pills[:4],
                "gender": "",
                "expected": {},
                "prose": "",
                "quality": "",
                "audit": "",
            }
            collecting = True
        else:
            collecting = True
        pills = []
        run_dropped = None

    for i, line in enumerate(lines, 1):
        s = line.strip()
        if not s:
            continue
        if PILL_TEXT.match(s) and len(s) % 2 == 0:
            valid, invalid = _chunk_pills(s)
            if valid:
                if not pills:
                    run_start = i
                pills.extend(valid)
                run_end = i
                if invalid and run_dropped is None:
                    run_dropped = (i, invalid)
                continue
            # 纯干支行但无合法 chunk：落入 OTHER 分支（close_run + 垃圾剔除）
        # OTHER 行：先收段，再尝试收集断语
        if pills:
            close_run()
        if collecting and pending is not None:
            if _is_stop(s):
                collecting = False
            elif not _is_garbage(s):
                pending_prose.append((i, s))
    close_run()
    close_pending()

    for idx, c in enumerate(cases, 1):
        c["id"] = f"tds_{idx:04d}"
    return cases


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    cases = extract(src)
    with open(out, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"提取 {len(cases)} 条 -> {out}")
