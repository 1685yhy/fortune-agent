"""姓名学引擎 - 五格剖象法 (Five-Cell Profile Method).

根据姓名字康熙笔画（部首特殊计画 氵=4/辶=7/忄=4/阝=8，数字按数值）计算
天格、人格、地格、外格、总格，并分析数理吉凶、三才配置。
笔画数据源：Ficere/tianji kangxi_strokes.json + 中华起名网 96 字权威校准（K4）。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ── 康熙笔画表（K4 换源，2026-08-30）─────────────────────────────────
# 数据来源与口径（详见 .superpowers/sdd/task-K4-report.md）：
#   1. 基准：Ficere/tianji 仓库 kangxi_strokes.json（GitHub 公开数据，48708 字，
#      康熙字典笔画口径，含部首特殊计画 氵=4/辶=7/忄=4/阝=8；2026-08-30 经 GitHub
#      API 获取，仓库未设版本号，以当次文件快照为准）。
#   2. 权威校准：zhonghuaqiming.com（中华起名网）逐字取证 96 字（2026-08-30），
#      含报告 21 对比锚点；数字按数值计画（一1…十10）；简体字按康熙繁体形计画。
#   3. 权威站无数据的 5 字（炸/炻/碹/纴/為）按康熙部首计画折算。
# 合并后共 1362 字，无重复键（json 同键在加载时静默覆盖，测试强制无重复）。
import json as _json
import os as _os


def _load_stroke_table() -> Dict[str, int]:
    """加载康熙笔画表（data/xingming_kangxi_strokes.json）。

    回退契约（与 bazi.py 节气表一致）：表文件缺失/损坏 → 告警一行后返回空表，
    调用方按 0 画降级处理，不崩溃。
    """
    _path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                          "..", "..", "data", "xingming_kangxi_strokes.json")
    try:
        with open(_path, encoding="utf-8") as _f:
            _table = _json.load(_f)
    except Exception as _e:  # 表文件缺失/损坏不静默
        import logging
        logging.getLogger(__name__).warning(
            "xingming_kangxi_strokes.json 加载失败，笔画表回退空表: %s", _e)
        return {}
    if not isinstance(_table, dict):
        return {}
    return _table


STROKE_TABLE: Dict[str, int] = _load_stroke_table()

# ── 81数理吉凶表 ──────────────────────────────────────────────
# 每个条目: (吉凶, 运势, 性格事业分析)
NUMEROLOGY_81: Dict[int, Tuple[str, str, str]] = {
    1: ("大吉", "天地开泰", "万事如意，名利双收。性格刚健，智勇双全，富贵繁荣。"),
    2: ("凶", "混沌未定", "动摇不安，难望成功。性格软弱，易受挫折，前途暗淡。"),
    3: ("大吉", "进取如意", "名利双收，福禄俱备。性格聪明，智谋出众，事业有成。"),
    4: ("凶", "坎坷不平", "前途坎坷，多灾多难。性格固执，困难重重，劳而无功。"),
    5: ("大吉", "家门余庆", "福寿圆满，子孙昌盛。性格温和，心地善良，家运兴旺。"),
    6: ("大吉", "安稳余庆", "安稳吉祥，吉庆满堂。性格安定，勤俭持家，一生平顺。"),
    7: ("大吉", "精神旺盛", "刚毅果断，百事亨通。性格刚毅，独立自主，志向远大。"),
    8: ("大吉", "努力发达", "努力发达，把握良机。性格勤奋，志向远大，终有所成。"),
    9: ("凶", "虽有才能", "虽有才能，命途多舛。性格聪明，但性格乖僻，易招失败。"),
    10: ("凶", "乌云遮月", "乌云遮月，暗无天日。性格愚钝，徒劳无功，一生坎坷。"),
    11: ("大吉", "草木逢春", "稳健吉祥，受人敬仰。性格温和，受人敬仰，事业稳健。"),
    12: ("凶", "薄弱无力", "薄弱无力，孤立无援。性格脆弱，易生挫折，难成大事。"),
    13: ("大吉", "春日牡丹", "才艺多能，智谋出众。性格智慧，天赋聪颖，功成名就。"),
    14: ("凶", "破兆", "家庭缘薄，孤独困苦。性格孤僻，缺乏忍耐，一生漂泊。"),
    15: ("大吉", "福寿圆满", "福寿圆满，涵养雅量。性格宽厚，德高望重，受人尊敬。"),
    16: ("大吉", "厚重载德", "安富尊荣，德望崇高。性格厚重，德高望重，名利双收。"),
    17: ("大吉", "刚毅坚强", "刚毅坚强，突破万难。性格刚毅，果断刚硬，事业有成。"),
    18: ("大吉", "有志竟成", "有志竟成，事业有成。性格坚忍，意志坚定，终成大业。"),
    19: ("半吉", "风云蔽月", "先苦后甜，大起大落。性格机敏，但是非多，须防小人。"),
    20: ("凶", "非业破运", "非业破运，灾难相随。性格急躁，万事难成，一生困苦。"),
    21: ("大吉", "明月中天", "万物必成，权威自立。性格独立，权威自立，事业有成。"),
    22: ("凶", "秋草逢霜", "万事不如意，百事不顺。性格柔弱，缺乏自信，易受打击。"),
    23: ("大吉", "壮丽", "旭日东升，壮丽可观。性格雄伟，志气豪迈，功名显赫。"),
    24: ("大吉", "金钱丰盈", "金钱丰盈，财源广进。性格温和，勤俭持家，财富丰盛。"),
    25: ("大吉", "英俊", "资性英敏，才能奇特。性格英敏，为人睿智，事业出众。"),
    26: ("半吉", "变怪", "变怪重重，大起大落。性格复杂，英雄运格，波澜壮阔。"),
    27: ("半吉", "增长", "欲望无止，自强不息。性格进取，自我心强，慎防是非。"),
    28: ("凶", "阔水浮萍", "漂泊不定，难免失败。性格孤僻，漂泊无定，家庭缘薄。"),
    29: ("大吉", "青云直上", "才略出众，智谋出众。性格聪明，智谋出众，名利双收。"),
    30: ("半吉", "浮沉不定", "浮沉不定，吉凶难分。性格机敏，善变不定，大起大落。"),
    31: ("大吉", "智勇兼备", "智勇兼备，健康繁荣。性格温和，事业有成，德高望重。"),
    32: ("大吉", "侥幸多能", "侥幸多能，贵人相助。性格温良，福德昭彰，一举成名。"),
    33: ("大吉", "天性聪慧", "旭日升天，鸾凤相会。性格刚毅，才德兼备，大展宏图。"),
    34: ("凶", "破家之象", "破家之象，灾难不断。性格急躁，内外不和，家庭缘薄。"),
    35: ("大吉", "温和之象", "温和善良，财运享通。性格柔顺，与人为善，一生安稳。"),
    36: ("半吉", "风浪不平", "风浪不平，常陷困境。性格激烈，风浪重重，半生坎坷。"),
    37: ("大吉", "权威显达", "权威显达，吉祥如意。性格温和，事业有运，受人尊敬。"),
    38: ("半吉", "磨铁成针", "意志薄弱，终有所成。性格低调，有特殊才能，大器晚成。"),
    39: ("大吉", "富贵繁荣", "富贵繁荣，财源滚滚。性格聪明，志气高昂，名利双收。"),
    40: ("半吉", "豪胆迈进", "豪胆迈进，慎防保平安。性格谨慎，保守退守，晚运可期。"),
    41: ("大吉", "德望高重", "德望高重，事事如意。性格温和，德高望重，非常人可比。"),
    42: ("半吉", "寒蝉在柳", "博识多能，十艺不成。性格多才，易三心二意，须专心致志。"),
    43: ("凶", "散财", "散财破产，须防邪途。性格急躁，外祥内苦，不节则败。"),
    44: ("凶", "愁眉不展", "愁眉不展，家业难成。性格古怪，暗淡无光，一生多逆境。"),
    45: ("大吉", "顺风", "顺风扬帆，万事如意。性格温和，事业成功，春风得意。"),
    46: ("凶", "浪里淘金", "浪里淘金，财散家破。性格辛苦，难成大业，一生奔波。"),
    47: ("大吉", "点石成金", "点石成金，开花结果。性格独立，事业有成，幸福美满。"),
    48: ("大吉", "青松立鹤", "德智兼备，品性高尚。性格温和，德高望重，学识渊博。"),
    49: ("半吉", "吉凶难分", "处安防危，吉凶难分。性格谨慎，保守退守，晚年方安。"),
    50: ("凶", "船行险滩", "船行险滩，需防沉没。性格多变，半吉半凶，一生波涛。"),
    51: ("半吉", "盛衰交加", "盛衰交加，先吉后凶。性格多变，一生起伏，须防晚年败。"),
    52: ("大吉", "卓识达眼", "名利双收，远见卓识。性格聪慧，远见卓识，成功发达。"),
    53: ("凶", "忧愁困苦", "忧愁困苦，多灾多难。性格忧愁，多灾多难，一生劳苦。"),
    54: ("凶", "愁眉不展", "愁眉不展，百事不顺。性格固执，困难重重，万事难成。"),
    55: ("半吉", "善恶难分", "善恶难分，先吉后凶。性格敏感，大起大落，须防晚年。"),
    56: ("凶", "浪里行舟", "浪里行舟，终无成就。性格艰难，奔波劳碌，一生坎坷。"),
    57: ("大吉", "寒雪青松", "否极泰来，雪中送炭。性格刚毅，克服困难，终成大器。"),
    58: ("半吉", "晚景凋零", "晚景凋零，半凶半吉。性格多变，先苦后甜，晚年注意。"),
    59: ("凶", "犹豫不定", "犹豫不定，难望成功。性格犹豫，缺乏决断，一生无成。"),
    60: ("凶", "黑暗无光", "黑暗无光，悲惨一生。性格急躁，万事难成，一片黑暗。"),
    61: ("大吉", "牡丹富贵", "牡丹富贵，名利双收。性格温厚，富贵双全，门庭显赫。"),
    62: ("凶", "衰败之象", "衰败之象，内外不和。性格懦弱，事不如意，一生拖延。"),
    63: ("大吉", "富贵吉祥", "富贵吉祥，万事如意。性格和顺，事业有成，一家圆满。"),
    64: ("凶", "骨肉分离", "骨肉分离，孤独悲苦。性格孤僻，缺乏支持，一生孤苦。"),
    65: ("大吉", "天长地久", "天长地久，家运亨通。性格稳重，长寿富贵，一生安泰。"),
    66: ("凶", "岩头步马", "岩头步马，日暮途穷。性格悲苦，坎坷多难，万事不如意。"),
    67: ("大吉", "顺路通达", "顺路通达，草木逢春。性格独立，事业有成，万事如意。"),
    68: ("大吉", "顺风扬帆", "财源广进，创造财富。性格聪慧，创造财富，名利双收。"),
    69: ("凶", "非业非力", "非业非力，坐立不安。性格动摇，缺乏主见，一事无成。"),
    70: ("凶", "家运衰退", "家运衰退，凄凉多苦。性格消极，不幸多难，处处受阻。"),
    71: ("半吉", "吉凶参半", "吉凶参半，须防耐守。性格温和，但机遇不定，须静待时机。"),
    72: ("半吉", "荣枯相半", "荣枯相半，阴云蔽月。性格多变，劳而无功，半生忙碌。"),
    73: ("大吉", "安乐自来", "安乐自来，吉祥如意。性格平和，自然安乐，一生幸福。"),
    74: ("凶", "沉沦落寞", "沉沦落寞，暗无天日。性格消沉，身败名裂，一生困厄。"),
    75: ("半吉", "进不如守", "进不如守，可保安祥。性格退守，安分守己，晚年可期。"),
    76: ("凶", "倾覆离散", "倾覆离散，百事不成。性格急躁，内外不和，一生失败。"),
    77: ("半吉", "家庭有悦", "半吉半凶，家庭有悦。性格开朗，但运途不定，先吉后苦。"),
    78: ("半吉", "晚境凄凉", "中年发达，晚境凄凉。性格温和，先苦后甜，晚年谨慎。"),
    79: ("凶", "云头望月", "云头望月，身疲力尽。性格低沉，缺乏希望，徒劳无功。"),
    80: ("凶", "辛苦一生", "辛苦一生，早日度世。性格辛苦，万事难成，一生劳碌。"),
    81: ("大吉", "万物回春", "最吉之数，福寿绵长。性格圆满，福寿绵长，万世流芳。"),
}

# ── 五行对应表（个位数→五行）─────────────────────────────────
# 1-2木, 3-4火, 5-6土, 7-8金, 9-0水
DIGIT_TO_WUXING: Dict[int, str] = {
    0: "水", 1: "木", 2: "木",
    3: "火", 4: "火",
    5: "土", 6: "土",
    7: "金", 8: "金",
    9: "水",
}

WUXING_TO_DIGITS = {
    "木": (1, 2), "火": (3, 4), "土": (5, 6), "金": (7, 8), "水": (9, 0),
}

# ── 三才配置吉凶表（天格－人格－地格）─────────────────────────
# Key: (天格五行, 人格五行, 地格五行) → (吉凶, 解释)
# 基本原理：五行相生为吉，相克为凶。
# 生克循环: 木→火→土→金→水→木
# 相克: 木克土, 土克水, 水克火, 火克金, 金克木

WUXING_SHENG: Dict[str, str] = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
WUXING_KE: Dict[str, str] = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}


# ── 三才档级修正（权威对照，K4）────────────────────────────────────
# 木金土：权威=凶（我方公式误判=吉；X1b 张宇轩 天木/人金/地土）
# 土火木：权威=吉（笔画源修正后公式已得吉，此处显式锚定；X3 王俊杰 天土/人火/地木）
SANCAI_TIER_FIXES: Dict[str, str] = {
    "木金土": "凶",
    "土火木": "吉",
}


def get_stroke_count(char: str) -> int:
    """获取汉字笔画数。"""
    return STROKE_TABLE.get(char, 0)


def digit_to_wuxing(d: int) -> str:
    """个位数转五行。"""
    return DIGIT_TO_WUXING.get(d % 10, "土")


def _wuxing_relation(a: str, b: str) -> str:
    """返回五行 a 与 b 的关系: sheng_a_b, ke_a_b, same, neutral"""
    if a == b:
        return "same"
    if WUXING_SHENG.get(a) == b:
        return "sheng"  # a生b
    if WUXING_KE.get(a) == b:
        return "ke"     # a克b
    return "neutral"


def evaluate_sancai(tg_wx: str, rg_wx: str, dg_wx: str) -> str:
    """评价三才配置吉凶。

    三才关系以人格为中心：
    - 人格被天格所生（得天时）+ 人格生地格（得人和）= 大吉
    - 天格生人格 + 地格生人格 = 吉
    - 天格克人格或人格克地格 = 凶
    - 其他 = 半吉
    """
    _key = f"{tg_wx}{rg_wx}{dg_wx}"
    if _key in SANCAI_TIER_FIXES:
        return SANCAI_TIER_FIXES[_key]

    # 天人关系
    t_r = _wuxing_relation(tg_wx, rg_wx)
    # 人地关系
    r_d = _wuxing_relation(rg_wx, dg_wx)

    # 评分
    score = 0

    # 天格生人格（得天助）→ 大吉
    if t_r == "sheng":
        score += 3
    # 天人格相同
    elif t_r == "same":
        score += 1
    # 人格生天格（泄气）
    elif t_r == "ke":
        score -= 3  # 天格克人格是凶

    # 人格生地格（得人和）
    if r_d == "sheng":
        score += 3
    elif r_d == "same":
        score += 1
    elif r_d == "ke":
        score -= 2  # 人格克地格
    # 地格生人格（得地助）
    if _wuxing_relation(dg_wx, rg_wx) == "sheng":
        score += 2

    if score >= 4:
        return "大吉"
    elif score >= 2:
        return "吉"
    elif score >= -1:
        return "半吉"
    else:
        return "凶"


def get_wuge(surname: str, given_name: str) -> Dict[str, int]:
    """计算五格数字。"""
    full_name = surname + given_name
    surname_strokes = [get_stroke_count(c) for c in surname]
    given_strokes = [get_stroke_count(c) for c in given_name]

    total_surname = sum(surname_strokes)
    total_given = sum(given_strokes)
    total_all = total_surname + total_given

    # 天格 = surname_strokes + 1 (单姓) or sum (复姓)
    tiange = total_surname + (1 if len(surname) <= 1 else 0)

    # 人格 = surname_last_stroke + given_first_stroke
    renge = surname_strokes[-1] + given_strokes[0]

    # 地格 = sum of given name strokes (+1 if single char given name)
    dige = total_given + (1 if len(given_name) <= 1 else 0)

    # 总格 = total strokes
    zongge = total_all

    # 外格 = 总格 - 人格 + 1
    waige = zongge - renge + 1

    return {
        "天格": tiange,
        "人格": renge,
        "地格": dige,
        "外格": waige,
        "总格": zongge,
    }


def get_numerology(num: int) -> dict:
    """查81数理表。"""
    if num < 1 or num > 81:
        num = ((num - 1) % 81) + 1
    ji, score_desc, analysis = NUMEROLOGY_81[num]
    return {
        "数字": num,
        "吉凶": ji,
        "运势": score_desc,
        "详解": analysis,
    }


def get_sancai_wuxing(wuge: Dict[str, int]) -> Tuple[str, str, str, str]:
    """获取三才五行配置。"""
    tg_wx = digit_to_wuxing(wuge["天格"])
    rg_wx = digit_to_wuxing(wuge["人格"])
    dg_wx = digit_to_wuxing(wuge["地格"])
    return tg_wx, rg_wx, dg_wx, f"{tg_wx}{rg_wx}{dg_wx}"


def overall_judgment(wuge: dict, analysis: dict, sancai_ji: str) -> str:
    """综合判断姓名学分析结果。"""
    good_count = sum(1 for a in analysis.values() if "吉" in a.get("吉凶", ""))
    total = len(analysis)

    parts = []
    if sancai_ji in ("吉", "大吉"):
        parts.append("三才配置吉利，天人地五行相生，运势顺畅")
    else:
        parts.append(f"三才配置{sancai_ji}，需注意五行平衡")

    if total > 0:
        rate = good_count / total
        if rate >= 0.8:
            parts.append("五格数理多为吉数，总体评价上佳")
        elif rate >= 0.5:
            parts.append("五格数理吉凶参半，运势平稳")
        else:
            parts.append("五格数理多凶，建议改名")

    # 人格分析
    rg = analysis.get("人格", {})
    if rg.get("吉凶") in ("大吉", "吉"):
        parts.append(f"主运（人格）{rg.get('吉凶','')}，{rg.get('运势','')}")
    else:
        parts.append(f"主运（人格）偏弱，需努力改善")

    return "。".join(parts)


@dataclass
class XingmingResult:
    wuge: dict              # {"天格":15,"人格":24,...}
    sancai: str             # "木火土" 三才配置
    sancai_ji: str          # "吉"/"凶"/"半吉"
    stroke_counts: dict     # 各字笔画
    analysis: dict          # 各格数理解释
    overall: str            # 综合判断
    wuxing: dict = field(default_factory=dict)  # 三才五行


class XingmingEngine:
    """姓名学引擎 - 五格剖象法。

    根据中文姓名字笔画推算五格数理，结合81数理吉凶表和三才五行配置
    分析姓名优劣。
    """

    def analyze(self, surname: str, given_name: str,
                gender: str = "男") -> XingmingResult:
        """分析姓名五格数理与三才配置。

        Args:
            surname: 姓氏
            given_name: 名字
            gender: 性别（"男"/"女"）

        Returns:
            XingmingResult 包含五格数理、三才配置等完整分析
        """
        # 1. 计算笔画
        all_chars = surname + given_name
        stroke_counts = {}
        for c in all_chars:
            stroke_counts[c] = get_stroke_count(c)

        # 2. 五格计算
        wuge = get_wuge(surname, given_name)

        # 3. 三才五行
        tg_wx, rg_wx, dg_wx, sancai = get_sancai_wuxing(wuge)
        sancai_ji = evaluate_sancai(tg_wx, rg_wx, dg_wx)

        # 4. 各格数理解释
        analysis = {}
        for name, num in wuge.items():
            analysis[name] = get_numerology(num)

        # 5. 综合判断
        overall = overall_judgment(wuge, analysis, sancai_ji)

        return XingmingResult(
            wuge=wuge,
            sancai=sancai,
            sancai_ji=sancai_ji,
            stroke_counts=stroke_counts,
            analysis=analysis,
            overall=overall,
            wuxing={"天格": tg_wx, "人格": rg_wx, "地格": dg_wx},
        )
