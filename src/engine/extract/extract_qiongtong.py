"""穷通宝鉴 120 格提取：10 日主章 × 12 月段 → 查表 json。

用法: python -m src.engine.extract.extract_qiongtong <txt路径> <输出json路径>

语料结构（2026-08-15 探查 /mnt/d/fortune-data/books/bazi/穷通宝鉴.txt，1366 行）：
- 章节头三形态（与简报骨架的简单 "甲木" 行不同）：
  * 论X：`论丙火`/`论丁火`/`论戊土`/`论己土`/`论辛金`/`论壬水`/`论癸水`；
  * 三X季头：`三春甲木`/`三春丙火：`/`三夏己土：` 等（部分带全角冒号）；
  * 三X总论：`三春甲木总论`/`三秋甲木总论`/`三春乙木总论`/`三夏乙木总论`
    共 4 处，其下段落为前言总述，整体丢弃。
- 月标记：正月~十月（主流），十一月/十二月（主流冬月写法），
  冬月/腊月（少量），合并形 `正二月`/`五六月`/`八九月`/`十一二月`
  （一行同述多个月，内容复制到所属各月）。
- 庚金章无任何总头，直接以 `正月庚金：`…`十二月庚金：` 起段；
  戊/己/辛/壬/癸 部分月份段同理 → 行首 `X月日主` 即切换日主章。
- 己土章夏/秋/冬无逐月段，只有季段总述（`三夏己土：`/`三秋己土：`/
  `三冬己土：` + 总述段落）→ 补给该季各月格（巳午未/申酉戌/子丑）。
- 乙丑/丁丑 本版无 `十二月` 段（乙冬最后一个"十一月乙木"段之后的
  未标记冬段、丁冬的 `三冬丁火，甲木为尊…` 总括行）→ 以"冬尾"规则
  取该章最后一个十一月段之后的非例盘内容补给，文字均出自原文。
- 例盘行（`时日月年` 表头、纯干支 4-8 字、干支+标点短行）为命例旁注，
  过滤不录入；`论木`/`论金`/`论土`/`秋月之水：`/`冬月之金：` 等
  五行前言段同样丢弃。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

GANS = "甲乙丙丁戊己庚辛壬癸"
GZ = set("甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥")
MONTHS = ["寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥", "子", "丑"]
SEASON_OF = {"寅": "春", "卯": "春", "辰": "春", "巳": "夏", "午": "夏", "未": "夏",
             "申": "秋", "酉": "秋", "戌": "秋", "亥": "冬", "子": "冬", "丑": "冬"}
SEASON_MONTHS = {"春": ["寅", "卯", "辰"], "夏": ["巳", "午", "未"],
                 "秋": ["申", "酉", "戌"], "冬": ["亥", "子", "丑"]}

MONTH_MAP = {"正月": "寅", "二月": "卯", "三月": "辰", "四月": "巳", "五月": "午",
             "六月": "未", "七月": "申", "八月": "酉", "九月": "戌", "十月": "亥",
             "十一月": "子", "冬月": "子", "十二月": "丑", "腊月": "丑"}
MERGED = {"正二月": ["寅", "卯"], "五六月": ["午", "未"],
          "八九月": ["酉", "戌"], "十一二月": ["子", "丑"]}

# 正则按行首锚定；合并月形排在最前（如 "十一二月" 须先于 "十一月"）。
CHAP_RE = re.compile(r"^论[甲乙丙丁戊己庚辛壬癸][木火土金水]$")
ZONGLUN_RE = re.compile(r"^三[春夏秋冬][甲乙丙丁戊己庚辛壬癸][木火土金水]?总论$")
SEASON_RE = re.compile(r"^三(?P<season>[春夏秋冬])(?P<gan>[甲乙丙丁戊己庚辛壬癸])[木火土金水]?[：:]?$")
MONTH_RE = re.compile(
    r"^(?P<mm>正二月|五六月|八九月|十一二月|十一月|十二月|冬月|腊月|"
    r"正月|二月|三月|四月|五月|六月|七月|八月|九月|十月)"
    r"(?P<gan>[甲乙丙丁戊己庚辛壬癸])?"
)
PREAMBLE_HEAD_RE = re.compile(r"^[春夏秋冬]月之[木火土金水][：:]?$")
PREAMBLE_LUN_RE = re.compile(r"^论[木火土金水]$")


def is_chart_line(s: str) -> bool:
    """例盘旁注行：`时日月年` 表头行 / 纯干支 3~8 字行 / 干支+标点短行。"""
    if "时日月年" in s:
        return True
    t = "".join(c for c in s if not c.isspace())
    for ch in "，,、．。：:；;":
        t = t.replace(ch, "")
    return 3 <= len(t) <= 8 and all(c in GZ for c in t)


def extract_qiongtong(path: str) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    lines = text.splitlines()

    table: dict[str, dict[str, str]] = {g: {} for g in GANS}
    season_paras: dict[str, dict[str, str]] = {g: {} for g in GANS}  # 季段总述（补给源）
    merged_paras: dict[str, dict[str, list]] = {g: {} for g in GANS}  # 合并月段（补给源）
    chapter_lines: dict[str, list[int]] = {g: [] for g in GANS}
    chapter_end: dict[str, int] = {}
    win_markers: dict[str, int] = {}  # 每章最后一个 十一月/冬月 段行号（冬尾补给）

    cur_gan: str | None = None
    cur_months: list[str] = []
    buf: list[str] = []
    season_buf: list[str] = []
    season_of_buf: str | None = None
    merged_season: str | None = None  # 当前缓冲是否合并月段（记录补给源用）

    def flush() -> None:
        nonlocal buf, merged_season
        if cur_gan and cur_months and buf:
            txt = "".join(buf).strip()
            for m in cur_months:  # 同段可属多个月（合并形）
                prev = table[cur_gan].get(m, "")
                table[cur_gan][m] = prev + txt  # 追加而非覆盖，保全文段
            if merged_season:
                merged_paras[cur_gan].setdefault(merged_season, []).append(
                    (cur_months[-1], txt))
        buf = []
        merged_season = None

    def flush_season() -> None:
        nonlocal season_buf, season_of_buf
        if cur_gan and season_of_buf and season_buf:
            season_paras[cur_gan].setdefault(season_of_buf,
                                             "".join(season_buf).strip())
        season_buf = []
        season_of_buf = None

    def switch_gan(gan: str, idx: int) -> None:
        nonlocal cur_gan
        if cur_gan and cur_gan != gan:
            chapter_end[cur_gan] = idx
        if gan != cur_gan:
            cur_gan = gan
            chapter_lines[gan].append(idx)

    for idx, raw in enumerate(lines):
        s = raw.strip()
        if not s:
            continue
        m_ch = CHAP_RE.match(s)
        if m_ch:
            flush(); flush_season()
            switch_gan(s[1], idx)
            cur_months = []
            continue
        m_zl = ZONGLUN_RE.match(s)
        if m_zl:
            flush(); flush_season()
            cur_months = []
            continue
        m_sn = SEASON_RE.match(s)
        if m_sn:
            flush(); flush_season()
            switch_gan(m_sn.group("gan"), idx)
            cur_months = []
            season_of_buf = m_sn.group("season")
            continue
        m_mo = MONTH_RE.match(s)
        if m_mo:
            mm = m_mo.group("mm")
            gan = m_mo.group("gan")
            if mm in MERGED or gan:  # 排除 "冬月之金：" 等前言行（单月必须带日主）
                flush(); flush_season()
                if mm in MERGED:
                    cur_months = list(MERGED[mm])
                    merged_season = SEASON_OF[MERGED[mm][0]]
                    if gan:
                        switch_gan(gan, idx)
                else:
                    cur_months = [MONTH_MAP[mm]]
                    if mm in ("十一月", "冬月"):
                        win_markers[cur_gan] = idx
                    switch_gan(gan, idx)
                buf = [s]
                continue
        if PREAMBLE_HEAD_RE.match(s) or PREAMBLE_LUN_RE.match(s):
            flush(); flush_season()
            cur_months = []
            continue
        if is_chart_line(s):
            continue
        # 正文行
        if season_of_buf is not None and not cur_months:
            season_buf.append(s)
        elif cur_months:
            buf.append(s)
        # 其余（总论段/前言段）丢弃
    flush(); flush_season()

    # ---------- 补给：空格必须全部填上，文字一律出自原文 ----------
    for gan in GANS:
        for m in MONTHS:
            if table[gan].get(m):
                continue
            # 1) 季段总述（如 三冬己土 → 子丑）
            sp = season_paras.get(gan, {}).get(SEASON_OF[m])
            if sp:
                table[gan][m] = sp
                continue
            # 2) 合并月段：该季合并形之后的空月（如 八九月丁火 → 戌）
            fills = merged_paras.get(gan, {}).get(SEASON_OF[m], [])
            for last_named, txt in fills:
                if SEASON_MONTHS[SEASON_OF[m]].index(last_named) < \
                        SEASON_MONTHS[SEASON_OF[m]].index(m) and not table[gan].get(m):
                    table[gan][m] = txt
        # 3) 冬尾：丑 无十二月段时，取本章最后一个十一月段之后的非例盘内容
        if not table[gan].get("丑"):
            last = win_markers.get(gan)
            if last is not None:
                tail: list[str] = []
                for idx2 in range(last + 1, chapter_end.get(gan, len(lines))):
                    s2 = lines[idx2].strip()
                    if not s2 or is_chart_line(s2):
                        continue
                    if (CHAP_RE.match(s2) or ZONGLUN_RE.match(s2)
                            or SEASON_RE.match(s2) or PREAMBLE_HEAD_RE.match(s2)
                            or PREAMBLE_LUN_RE.match(s2)):
                        break
                    tail.append(s2)
                if tail:
                    table[gan]["丑"] = "".join(tail).strip()
    return table


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    table = extract_qiongtong(src)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(table, f, ensure_ascii=False, indent=1)
    empty = [(g, m) for g, ms in table.items() for m, t in ms.items() if not t]
    print(f"120 格完成，空格 {len(empty)} 个: {empty[:10]}")
