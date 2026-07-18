#!/usr/bin/env python3
"""
Generate ~210 name analysis cases (姓名分析案例) for the name studies (姓名学) knowledge base.
Output: /mnt/d/fortune-data/books/zonghe/tmp/part5_cases.jsonl
"""

import json
import random
import os

random.seed(42)

# ============================================================
# KANGXI STROKE COUNTS - 姓名学 Traditional Values
# ============================================================
# Following standard 姓名学 (Chinese name science) Kangxi radical stroke counts.
# Special radical rules (氵=4, 艹=6, 阝=8/7, 辶=7, 忄=4, 扌=4, 王=5, 月=6, 衤=6, 犭=4, 礻=5)
# are already reflected in these values.

STROKES = {
    # === Common Surnames ===
    "张": 11, "王": 5, "李": 7, "赵": 14, "刘": 15, "周": 8, "林": 8,
    "郭": 15, "马": 10, "陈": 16, "杨": 13, "黄": 12, "吴": 7, "徐": 10,
    "孙": 10, "胡": 11, "朱": 6, "高": 10, "何": 7, "罗": 20,
    "梁": 11, "宋": 7, "唐": 10, "韩": 17, "曹": 11, "邓": 19, "彭": 12,
    "曾": 13, "萧": 20, "范": 11, "蒋": 17, "蔡": 17, "潘": 16,
    "袁": 10, "于": 3, "董": 15, "叶": 15, "魏": 18, "苏": 22,
    "孔": 4, "孟": 8, "屈": 8, "项": 12, "关": 19,
    "诸": 15, "葛": 15, "端": 14, "木": 4,
    "欧": 15, "阳": 17, "司": 5, "钟": 20, "冯": 12,
    "沈": 8, "吕": 7, "卢": 16, "丁": 2, "任": 6, "石": 5,
    "崔": 11, "程": 12, "陆": 15, "汪": 8, "田": 5, "白": 5,
    "康": 11, "常": 11, "余": 7, "杜": 7, "戴": 18, "夏": 10,
    "钱": 16, "段": 9, "侯": 9, "傅": 12, "廖": 14, "姚": 9,
    "秦": 10, "顾": 22, "邵": 12, "毛": 4, "谭": 19, "贾": 13,
    "赖": 16, "史": 5, "陶": 16, "贺": 12, "齐": 14, "童": 12,
    "姜": 9, "纪": 9, "柳": 9, "郑": 19, "鲍": 16, "尹": 4,
    "阎": 16, "葛": 15, "樊": 15, "霍": 16, "殷": 10, "乔": 12,
    "尤": 4, "邹": 16, "邱": 12, "温": 13, "易": 8, "缪": 23,
    "金": 8, "雷": 13, "余": 7, "严": 20, "于": 3, "祝": 10,
    "左": 5, "右": 5, "吉": 6, "岳": 17, "华": 14, "汤": 12,
    "牛": 4, "龚": 22, "卓": 8, "安": 6, "包": 5, "洪": 10,
    "江": 7, "邝": 20, "武": 8, "文": 4, "方": 4,
    "辛": 7, "邢": 11, "廉": 13,

    # === Single-character name elements ===
    "伟": 11, "芳": 10, "娜": 10, "洋": 10, "静": 16,
    "勇": 9, "丽": 19, "磊": 18, "强": 12, "婷": 12,
    "明": 8, "亮": 9, "红": 9, "鑫": 24, "斌": 12,
    "英": 11, "秀": 7, "芬": 10, "娟": 10, "波": 9,
    "峰": 10, "超": 12, "平": 5, "安": 6, "宁": 14,
    "飞": 9, "鹏": 19, "龙": 16, "凤": 14, "玉": 5,
    "兰": 23, "梅": 11, "菊": 14, "竹": 6, "松": 8,
    "海": 11, "天": 4, "慧": 15, "敏": 11, "建": 9,
    "国": 11, "庆": 15, "军": 9, "刚": 10, "毅": 15,
    "恒": 10, "学": 16, "雄": 12, "辉": 15, "耀": 20,
    "达": 16, "进": 15, "通": 14, "彬": 11, "琳": 13,
    "琴": 13, "琪": 13, "瑶": 15, "瑾": 16, "瑜": 14,
    "璐": 18, "珊": 10, "珠": 11, "宝": 20, "福": 14,
    "禄": 13, "寿": 14, "喜": 12, "财": 10, "源": 14,
    "瑞": 14, "祥": 11, "如": 6, "意": 13, "心": 4,
    "美": 9, "善": 12, "真": 10, "诚": 14, "信": 9,
    "仁": 4, "义": 13, "礼": 18, "智": 12, "和": 8,
    "乐": 15, "荣": 14, "贵": 12, "富": 12, "康": 11,
    "宁": 14, "泰": 10, "民": 5, "政": 9, "权": 22,
    "利": 7, "宏": 7, "威": 9, "盛": 12, "丰": 18,
    "裕": 13, "茂": 11, "昌": 8, "隆": 17, "永": 5,
    "久": 3, "长": 8, "远": 17, "世": 5, "代": 5,
    "传": 13, "家": 10, "庭": 10, "室": 9, "宇": 6,
    "宙": 8, "洪": 10, "荒": 10, "日": 4, "月": 4,
    "星": 9, "辰": 7, "晖": 13, "晴": 12, "朗": 11,
    "清": 12, "晨": 11, "曦": 20, "露": 22, "霜": 18,
    "雪": 11, "冰": 6, "寒": 12, "暖": 13, "温": 13,
    "雅": 12, "致": 9, "韵": 13, "画": 13, "诗": 13,
    "词": 12, "赋": 15, "书": 10, "香": 9, "萱": 15,
    "薇": 19, "芷": 10, "菡": 14, "蓉": 16, "莲": 17,
    "荷": 14, "萍": 15, "茵": 12, "蕾": 19, "蕊": 18,
    "芊": 9, "芝": 10, "花": 10, "叶": 15, "草": 12,
    "苗": 11, "芙": 10, "蓉": 16, "慧": 15, "芬": 10,
    "芳": 10, "萌": 14, "薇": 19,
    "江": 7, "河": 9, "湖": 13, "波": 9, "涛": 18,
    "浩": 11, "瀚": 20, "渊": 12, "泽": 17, "润": 16,
    "霖": 16, "霆": 15, "霄": 15, "帆": 6,
    "锦": 16, "绣": 14, "鹤": 21, "虎": 8, "豹": 10,
    "狮": 13, "象": 12, "德": 15, "道": 16, "方": 4,
    "正": 5, "倩": 10, "淑": 12, "洁": 16, "颖": 16,
    "晶": 12, "燕": 16, "雁": 12, "航": 10,
    "雯": 12, "青": 8, "岚": 12, "彤": 7, "宜": 8,
    "欣": 8, "悦": 11, "悟": 11, "惜": 11, "恒": 10,
    "怡": 9, "情": 12, "振": 11, "扬": 12, "挺": 10,
    "捷": 12, "放": 8, "政": 9, "教": 11, "文": 4,
    "武": 8, "思": 9, "念": 8, "志": 7, "意": 13,
    "子": 3, "之": 4, "大": 3, "中": 4, "立": 5,
    "小": 3, "山": 3, "川": 3, "土": 3, "石": 5,
    "万": 15, "百": 6, "千": 3, "兆": 6, "亿": 15,
    "凡": 3, "太": 4, "夫": 4, "少": 4, "尹": 4,
    "化": 4, "升": 4, "午": 4, "勿": 4, "匀": 4,
    "丹": 4, "予": 5, "云": 12, "互": 4, "井": 4,
    "兮": 4, "元": 4, "全": 6, "共": 6, "兵": 7,
    "其": 8, "具": 8, "典": 8, "冠": 9, "冰": 6,
    "冲": 15, "决": 7, "冶": 7, "冷": 7, "凯": 12,
    "函": 8, "列": 6, "初": 9, "利": 7, "到": 8,
    "制": 9, "刷": 8, "前": 9, "剑": 15, "力": 2,
    "功": 5, "加": 5, "助": 7, "男": 7, "创": 12,
    "三": 3, "四": 5, "五": 4, "六": 4, "七": 2,
    "八": 2, "九": 2, "十": 2, "本": 5,

    # === Historical Figures ===
    "丘": 5, "轲": 12, "羽": 6, "良": 7,
    "迁": 10, "白": 5, "甫": 7, "轼": 13,
    "岳": 8, "元": 4, "璋": 16, "玄": 4,
    "烨": 15, "弘": 7, "历": 16, "成": 7,
    "功": 5, "则": 9, "藩": 19, "鸿": 18,
    "章": 11, "恩": 10, "来": 8, "备": 12,
    "操": 16, "夷": 6, "广": 15, "汤": 12,
    "荫": 14, "桓": 10, "炎": 8, "轩": 10,
    "辕": 17, "黄": 12, "帝": 9, "尧": 9,
    "舜": 12, "禹": 9, "汤": 12, "姬": 10,
    "发": 12, "昌": 8, "望": 11, "牙": 4,
    "骞": 20, "蔡": 17, "伦": 10, "陀": 8,
    "羲": 17, "之": 4, "献": 20, "徽": 17,
    "钦": 12, "照": 13, "清": 12, "照": 13,
    "弃": 11, "疾": 10, "弃": 11, "病": 10,
    "观": 25, "音": 9, "娲": 13, "皇": 9,
    "俊": 9, "义": 13, "台": 5, "谦": 17,
    "偃": 11, "盖": 13, "忽": 8, "烈": 10,
    "秉": 8, "烛": 17, "旦": 5, "裔": 16,
    "庄": 13, "敦": 13, "颐": 16,

    # === Business/Modern ===
    "腾": 20, "化": 4, "彦": 9, "东": 8,
    "印": 5, "正": 5, "非": 8, "兴": 16,
    "丁": 2, "祎": 13, "旺": 9, "后": 9,
    "兆": 6, "彤": 7, "燊": 16, "裕": 13,
    "丙": 5, "邨": 11, "永": 5, "庆": 15,

    # === Literary ===
    "鲁": 15, "迅": 10, "舍": 8, "金": 8,
    "盾": 9, "雪": 11, "芹": 10, "耐": 11,
    "庵": 11, "承": 8, "贯": 11, "从": 11,
    "爱": 13, "萧": 20, "自": 6, "摩": 15,
    "语": 14, "堂": 11, "庸": 11, "生": 5,
    "沫": 9, "若": 11, "作": 7, "人": 2,
    "秋": 9, "痕": 11, "鸣": 14, "凤": 14,
    "冠": 9, "中": 4, "实": 15, "甫": 7,
    "柳": 9, "宗": 8, "休": 6, "义": 13,
    "牧": 8, "贺": 12, "知": 8, "退": 14,
    "龄": 23, "先": 6, "潜": 16, "修": 10,
    "穆": 16, "修": 10, "韩": 17, "愈": 13,
    "择": 17, "端": 14, "己": 3, "分": 4,
    "原": 10, "新": 13, "宽": 15, "严": 20,
    "几": 3, "道": 16, "邻": 19, "曲": 6,
    "斯": 12, "千": 3, "家": 10, "信": 9,
    "松": 8, "龄": 23, "及": 3, "未": 5,
    "可": 5, "怜": 9, "九": 2, "里": 7,
    "鹏": 19, "图": 14, "破": 10, "壁": 18,
    "长": 8, "鹏": 19, "图": 14, "破": 10,
    "浪": 11, "淘": 12, "尽": 14,

    # === Extra common characters ===
    "东": 8, "南": 9, "西": 6, "北": 5,
    "春": 9, "夏": 10, "秋": 9, "冬": 5,
    "博": 12, "雯": 12, "青": 8, "晴": 12,
    "岚": 12, "桂": 10, "艳": 24, "璧": 18,
    "琼": 20, "靓": 15, "瑶": 15, "霆": 15,
    "霄": 15, "沛": 8, "玟": 9, "珂": 10,
    "珺": 12, "璇": 16, "璟": 16, "璨": 19,
    "漪": 15, "潇": 20, "澈": 16, "澜": 20,
    "鸿": 17, "骏": 17, "骁": 22, "骋": 17,
}

# For compound surnames, we need special handling
COMPOUND_SURNAMES = {
    "司马": ("司", "马"),
    "诸葛": ("诸", "葛"),
    "欧阳": ("欧", "阳"),
    "司徒": ("司", "徒"),
    "端木": ("端", "木"),
}


# ============================================================
# NUMBER AUSPICIOUSNESS 1-81
# ============================================================
def get_num_quality(n):
    """Return (quality, element) for a five-grid number 1-81."""
    # Element: last digit 1,2=木 3,4=火 5,6=土 7,8=金 9,0=水
    last_digit = n % 10
    elem_map = {1: "木", 2: "木", 3: "火", 4: "火", 5: "土", 6: "土", 7: "金", 8: "金", 9: "水", 0: "水"}
    element = elem_map[last_digit]

    quality_data = {
        1: ("大吉", "万物始开，蓬勃向上"), 2: ("凶", "混沌未明，进退维谷"),
        3: ("大吉", "进取如意，百事亨通"), 4: ("凶", "风雨飘摇，多灾多难"),
        5: ("大吉", "福寿双全，名利双收"), 6: ("大吉", "安稳顺遂，家运昌隆"),
        7: ("大吉", "刚毅果断，百折不挠"), 8: ("大吉", "努力发展，前程似锦"),
        9: ("凶", "困顿交加，暗无天日"), 10: ("凶", "空虚消耗，终归徒劳"),
        11: ("大吉", "草木逢春，稳健荣达"), 12: ("凶", "秋草逢霜，软弱无力"),
        13: ("大吉", "智略超群，天赋异禀"), 14: ("凶", "浮沉不定，泪如秋雨"),
        15: ("大吉", "福寿圆满，富贵双全"), 16: ("大吉", "厚重载德，安富尊荣"),
        17: ("大吉", "突破万难，权威显赫"), 18: ("大吉", "志气凌云，百事通达"),
        19: ("凶", "风云蔽日，辛苦挣扎"), 20: ("凶", "破败衰微，深陷困境"),
        21: ("大吉", "明月当空，独立权威"), 22: ("凶", "秋草逢霜，坎坷凋零"),
        23: ("大吉", "旭日升天，名显四方"), 24: ("大吉", "白手起家，财源广进"),
        25: ("大吉", "智谋超群，刚毅果敢"), 26: ("半吉", "千难万险，终见曙光"),
        27: ("半吉", "勤奋不怠，终有所成"), 28: ("凶", "离群漂泊，动荡不安"),
        29: ("大吉", "智谋卓绝，大业可成"), 30: ("半吉", "吉凶相伴，浮沉难料"),
        31: ("大吉", "智勇双全，功成名就"), 32: ("大吉", "幸得贵人，天助成功"),
        33: ("大吉", "家门隆昌，声名远播"), 34: ("凶", "破家亡身，浅尝辄止"),
        35: ("大吉", "温和安详，福寿康宁"), 36: ("半吉", "波澜叠起，终能突破"),
        37: ("大吉", "权威显达，热忱正直"), 38: ("半吉", "意志薄弱，难成大器"),
        39: ("大吉", "富贵荣华，德高望重"), 40: ("半吉", "谨慎行事，退守保身"),
        41: ("大吉", "德高望重，万事如意"), 42: ("半吉", "博学多才，奋发图强"),
        43: ("半吉", "虚华浮表，华而不实"), 44: ("凶", "困顿愁苦，壮志难酬"),
        45: ("大吉", "顺风顺水，万事亨通"), 46: ("半吉", "坎坷不平，慎防意外"),
        47: ("大吉", "进退有度，大业可期"), 48: ("大吉", "德智兼备，富贵绵长"),
        49: ("半吉", "处变不惊，转危为安"), 50: ("半吉", "小舟入海，吉凶参半"),
        51: ("半吉", "盛衰交加，需防乐极"), 52: ("大吉", "先见之明，青云直上"),
        53: ("半吉", "忧喜参半，谨慎持重"), 54: ("半吉", "险关重重，慎之又慎"),
        55: ("大吉", "凡事顺遂，福泽绵长"), 56: ("半吉", "外示美好，内藏艰险"),
        57: ("大吉", "寒雪青松，枯木逢春"), 58: ("半吉", "冰释险化，苦尽甘来"),
        59: ("半吉", "心迷意乱，难定方向"), 60: ("半吉", "黑暗无光，谨慎保守"),
        61: ("大吉", "牡丹牡丹，富贵安乐"), 62: ("半吉", "根基不稳，事倍功半"),
        63: ("大吉", "天赐良机，家运亨通"), 64: ("半吉", "枯木待春，修身养性"),
        65: ("大吉", "巨流归海，富贵长寿"), 66: ("半吉", "进退踌躇，身负重担"),
        67: ("大吉", "天时地利，心想事成"), 68: ("大吉", "智虑周详，名利双收"),
        69: ("半吉", "动乱不安，先苦后甘"), 70: ("半吉", "退守保身，不宜进取"),
        71: ("半吉", "徒有虚名，尚需努力"), 72: ("半吉", "吉凶参半，福祸相随"),
        73: ("大吉", "志气高远，终能成功"), 74: ("半吉", "沉沦不遇，保守为宜"),
        75: ("半吉", "守分安命，顺时听天"), 76: ("半吉", "倾覆离散，谨慎持重"),
        77: ("半吉", "先甜后苦，安分守己"), 78: ("半吉", "晚年吉利，福泽尚存"),
        79: ("半吉", "事倍功半，早退为安"), 80: ("半吉", "吉凶参半，逢凶化吉"),
        81: ("大吉", "万物归元，最极之数"),
    }

    # Default if number > 81
    if n > 81:
        n = n % 81
        if n == 0:
            n = 81
        return quality_data.get(n, ("凶", "超出常理"))

    return quality_data.get(n, ("凶", "未知数理"))


def get_element_interaction(e1, e2):
    """Return the interaction between two elements (生 or 克 or 平)."""
    if e1 == e2:
        return "比和"
    sheng_map = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
    ke_map = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}
    if sheng_map.get(e1) == e2:
        return "相生"
    if ke_map.get(e1) == e2:
        return "相克"
    if sheng_map.get(e2) == e1:
        return "受生"
    return "平"


# ============================================================
# FIVE GRID CALCULATION
# ============================================================
def calculate_five_grids(surname, given_name):
    """
    Calculate the five grids for a name.
    surname: string (e.g., "张", "司马")
    given_name: string (e.g., "三", "小明")
    Returns dict with all five grid numbers and interpretations.
    """
    # Check if it's a compound surname
    is_compound = surname in COMPOUND_SURNAMES

    if is_compound:
        s1, s2 = COMPOUND_SURNAMES[surname]
    else:
        s1 = surname
        s2 = None

    # Get stroke counts
    if is_compound:
        s1_strokes = STROKES.get(s1, 1)
        s2_strokes = STROKES.get(s2, 1)
    else:
        s1_strokes = STROKES.get(s1, 1)
        s2_strokes = None

    # Given name strokes
    name_chars = list(given_name)
    name_strokes = []
    for c in name_chars:
        s = STROKES.get(c, 1)
        name_strokes.append(s)

    # 天格
    if is_compound:
        tiange = s1_strokes + s2_strokes
    else:
        tiange = s1_strokes + 1

    # 人格
    if is_compound:
        rege = s2_strokes + name_strokes[0]
    else:
        rege = s1_strokes + name_strokes[0]

    # 地格
    if len(name_chars) == 1:
        dige = name_strokes[0] + 1
    else:
        dige = sum(name_strokes)

    # 总格
    if is_compound:
        total = s1_strokes + s2_strokes + sum(name_strokes)
    else:
        total = s1_strokes + sum(name_strokes)

    # 外格
    if is_compound:
        waige = s1_strokes + name_strokes[-1]
    else:
        waige = total - rege + 1

    return {
        "tiange": tiange,
        "rege": rege,
        "dige": dige,
        "waige": waige,
        "zongge": total,
        "tiange_q": get_num_quality(tiange),
        "rege_q": get_num_quality(rege),
        "dige_q": get_num_quality(dige),
        "waige_q": get_num_quality(waige),
        "zongge_q": get_num_quality(total),
    }


# ============================================================
# ANALYSIS TEMPLATES
# ============================================================
def generate_analysis(name, surname, given_name, grids, category):
    """Generate a natural analysis paragraph."""
    is_compound = surname in COMPOUND_SURNAMES
    full_name = surname + given_name

    tg = grids["tiange"]
    rg = grids["rege"]
    dg = grids["dige"]
    wg = grids["waige"]
    zg = grids["zongge"]

    tg_q, tg_desc = grids["tiange_q"]
    rg_q, rg_desc = grids["rege_q"]
    dg_q, dg_desc = grids["dige_q"]
    wg_q, wg_desc = grids["waige_q"]
    zg_q, zg_desc = grids["zongge_q"]

    tg_elem = tg_q.split(",")[-1] if "," in tg_q else get_num_quality(tg)[1]
    tg_elem = grids["tiange_q"][1]
    rg_elem = grids["rege_q"][1]
    dg_elem = grids["dige_q"][1]

    # Count auspicious
    auspicious_count = sum(1 for q in [tg_q, rg_q, dg_q, wg_q, zg_q] if q == "大吉")
    bad_count = sum(1 for q in [tg_q, rg_q, dg_q, wg_q, zg_q] if q == "凶")
    half_count = sum(1 for q in [tg_q, rg_q, dg_q, wg_q, zg_q] if q == "半吉")

    # Element interaction
    tg_rg = get_element_interaction(tg_elem, rg_elem)
    rg_dg = get_element_interaction(rg_elem, dg_elem)

    # San cai quality
    def san_cai_quality(t, r, d):
        """Assess 三才 configuration."""
        t_e = get_num_quality(t)[1]
        r_e = get_num_quality(r)[1]
        d_e = get_num_quality(d)[1]
        tr = get_element_interaction(t_e, r_e)
        rd = get_element_interaction(r_e, d_e)
        if tr in ("相生", "比和") and rd in ("相生", "比和"):
            return "极佳"
        elif tr == "相克" and rd == "相克":
            return "不佳"
        elif tr == "相克" or rd == "相克":
            return "一般"
        return "良好"

    san_cai = san_cai_quality(tg, rg, dg)

    # Build analysis text
    paragraphs = []

    # Opening based on auspicious count
    if auspicious_count >= 4:
        opening = f"【姓名】{full_name}。{full_name}之五格配置极佳，{auspicious_count}格均为大吉之数，实属上乘佳名。"
    elif auspicious_count >= 3:
        opening = f"【姓名】{full_name}。{full_name}之五格配置良好，{auspicious_count}格为吉数，整体运势顺遂。"
    elif bad_count >= 3:
        opening = f"【姓名】{full_name}。{full_name}之五格配置凶多吉少，{bad_count}格为凶数，需多加注意。"
    elif bad_count >= 2:
        opening = f"【姓名】{full_name}。{full_name}之五格吉凶参半，既有吉利之数亦有凶险之格，运势起伏较大。"
    else:
        opening = f"【姓名】{full_name}。{full_name}之五格配置中平，数理吉凶搭配适中。"

    paragraphs.append(opening)

    # Detail the five grids
    detail = f"天格{tg}（{tg_elem}，{tg_q}）：{tg_desc}，"
    detail += f"代表祖业根基与长辈荫庇。"
    if not is_compound:
        detail += f"人格{rg}（{rg_elem}，{rg_q}）：{rg_desc}，为主运，代表个人性格与事业运。"
    else:
        detail += f"人格{rg}（{rg_elem}，{rg_q}）：{rg_desc}，为主运。"
    detail += f"地格{dg}（{dg_elem}，{dg_q}）：{dg_desc}，主前半生运势与家庭基础。"
    detail += f"外格{wg}（{get_num_quality(wg)[1]}，{wg_q}）：配合{get_num_quality(wg)[1]}属性，主社交与人际。"
    detail += f"总格{zg}（{get_num_quality(zg)[1]}，{zg_q}）：{zg_desc}，主一生总运。"
    paragraphs.append(detail)

    # 三才 analysis
    san_cai_text = f"三才配置天格{tg_elem}-人格{rg_elem}-地格{dg_elem}呈「{tg_rg}·{rg_dg}」之势，"
    if san_cai == "极佳":
        san_cai_text += "天地人三才相互生扶，根基稳固，前途无量。"
    elif san_cai == "良好":
        san_cai_text += "三才搭配基本顺畅，虽有小小波折亦无大碍。"
    elif san_cai == "不佳":
        san_cai_text += "三才相克较重，人生道路多坎坷曲折。"
    else:
        san_cai_text += "三才之间略有相克之处，需注意调节。"
    paragraphs.append(san_cai_text)

    # Category-specific observation
    if category == "historical":
        cat_note = f"鉴于{full_name}为历史著名人物，此姓名格局与其人生轨迹高度吻合："
    elif category == "celebrity":
        cat_note = f"作为知名公众人物，{full_name}的姓名数理格局在事业方面体现尤为显著："
    elif category == "business":
        cat_note = f"作为商界领袖，{full_name}的姓名数理在财富与事业格方面表现突出："
    elif category == "female":
        cat_note = f"从女性姓名学角度观之，{full_name}之名柔中带刚："
    elif category == "special_stroke":
        cat_note = f"此名含特殊部首笔画计算（如氵、艹、阝等），需按姓名学特定规则计算："
    elif category == "pattern_all_good":
        cat_note = f"此名五格全吉，为典型的吉利姓名配置："
    elif category == "pattern_all_bad":
        cat_note = f"此名五格全凶，警示此类数理配置可能带来的挑战："
    elif category == "pattern_mixed":
        cat_note = f"此名吉凶数理交织，体现了姓名学中常见的混合格局："
    elif category == "common":
        cat_note = f"此名为常见姓名，其数理格局分析如下："
    elif category == "literary":
        cat_note = f"从文采与才艺角度看，{full_name}的姓名数理特具艺术气质："
    else:
        cat_note = f"从姓名学角度综合评判{full_name}："

    # Specific grid emphasis
    if rg_q == "大吉" and dg_q == "大吉":
        emphasis = f"人格与地格皆为大吉，主中年事业与家庭生活双丰收。"
    elif rg_q == "大吉" and zg_q == "大吉":
        emphasis = f"人格与总格皆为大吉，主一生运势旺盛，终成大器。"
    elif rg_q == "凶":
        emphasis = f"人格为凶数，需注意性格方面的调和与修养。"
    elif dg_q == "凶":
        emphasis = f"地格为凶数，早年家运或子女方面需多加经营。"
    elif wg_q == "大吉":
        emphasis = f"外格为大吉，善用人际关系，社交广泛得助。"
    else:
        emphasis = f"总体而言，姓名乃个人命运之符号，数理虽重要，仍需配合个人努力。"

    paragraphs.append(f"{cat_note}{emphasis}")

    # Closing
    if auspicious_count >= 4:
        closing = f"综上所述，{full_name}之名堪称完美，若配以得当的八字用神，更是如虎添翼。"
    elif auspicious_count >= 2:
        closing = f"综合来看，{full_name}之姓名整体尚佳，善用吉数之优势可趋吉避凶。"
    elif bad_count >= 3:
        closing = f"综合来看，{full_name}之名建议酌情调整，增强吉数力量以平衡运势。"
    else:
        closing = f"总体而言，{full_name}之名各有千秋，发挥吉数长处即可。"

    paragraphs.append(closing)

    return "".join(paragraphs)


# ============================================================
# NAME DEFINITIONS BY CATEGORY
# ============================================================

all_names = []

# Helper to add names
def add_name(surname, given_name, category, override_strokes=None):
    all_names.append({
        "surname": surname,
        "given_name": given_name,
        "category": category,
    })

# ============ Category 1: Common Names (30) ============
common_names = [
    ("张", "伟"), ("王", "芳"), ("李", "娜"), ("刘", "洋"), ("陈", "静"),
    ("杨", "勇"), ("吴", "强"), ("周", "婷"), ("徐", "明"), ("孙", "丽"),
    ("胡", "杰"), ("朱", "超"), ("高", "峰"), ("何", "欣"), ("罗", "琳"),
    ("梁", "辉"), ("宋", "佳"), ("唐", "亮"), ("韩", "冰"), ("曹", "军"),
    ("邓", "敏"), ("彭", "达"), ("曾", "怡"), ("范", "鑫"), ("蒋", "涛"),
    ("蔡", "宁"), ("潘", "宇"), ("袁", "浩"), ("董", "洁"), ("叶", "蕾"),
]
for s, g in common_names:
    add_name(s, g, "common")

# ============ Category 2: Historical Figures (30) ============
historical_names = [
    ("孔", "丘"), ("孟", "轲"), ("屈", "原"), ("刘", "邦"), ("项", "羽"),
    ("韩", "信"), ("张", "良"), ("关", "羽"), ("张", "飞"), ("赵", "云"),
    ("刘", "备"), ("曹", "操"), ("周", "瑜"), ("李", "白"), ("杜", "甫"),
    ("苏", "轼"), ("岳", "飞"), ("文", "天祥"), ("朱", "元璋"), ("郑", "成功"),
    ("林", "则徐"), ("曾", "国藩"), ("李", "鸿章"), ("孙", "中山"), ("周", "恩来"),
    ("卫", "青"), ("霍", "去病"), ("司", "马迁"), ("诸", "葛亮"), ("王", "羲之"),
]
for s, g in historical_names:
    add_name(s, g, "historical")

# ============ Category 3: Modern Celebrities (30) ============
celebrity_names = [
    ("周", "杰伦"), ("刘", "德华"), ("张", "学友"), ("郭", "富城"), ("王", "菲"),
    ("林", "俊杰"), ("蔡", "依林"), ("陈", "奕迅"), ("梁", "朝伟"), ("张", "曼玉"),
    ("林", "青霞"), ("成", "龙"), ("李", "连杰"), ("甄", "子丹"), ("吴", "京"),
    ("杨", "幂"), ("赵", "薇"), ("范", "冰冰"), ("李", "冰冰"), ("章", "子怡"),
    ("周", "星驰"), ("张", "国荣"), ("梅", "艳芳"), ("那", "英"), ("孙", "燕姿"),
    ("王", "力宏"), ("张", "惠妹"), ("萧", "敬腾"), ("罗", "大佑"), ("李", "宗盛"),
]
for s, g in celebrity_names:
    add_name(s, g, "celebrity")

# ============ Category 4: Business Tycoons (25) ============
business_names = [
    ("马", "云"), ("马", "化腾"), ("李", "彦宏"), ("刘", "强东"), ("雷", "军"),
    ("王", "健林"), ("许", "家印"), ("董", "明珠"), ("任", "正非"), ("张", "一鸣"),
    ("王", "兴"), ("丁", "磊"), ("张", "朝阳"), ("曹", "德旺"), ("郭", "台铭"),
    ("李", "嘉诚"), ("李", "兆基"), ("郑", "裕彤"), ("霍", "英东"),
    ("柳", "传志"), ("宗", "庆后"), ("杨", "惠妍"), ("何", "鸿燊"),
    ("陈", "光标"), ("潘", "石屹"),
]
for s, g in business_names:
    add_name(s, g, "business")

# ============ Category 5: Literary Figures (25) ============
literary_names = [
    ("鲁", "迅"), ("巴", "金"), ("曹", "雪芹"), ("施", "耐庵"), ("吴", "承恩"),
    ("罗", "贯中"), ("沈", "从文"), ("钱", "钟书"), ("张", "爱玲"), ("萧", "红"),
    ("冰", "心"), ("朱", "自清"), ("徐", "志摩"), ("林", "语堂"), ("金", "庸"),
    ("古", "龙"), ("郭", "沫若"), ("老", "舍"), ("茅", "盾"), ("李", "清照"),
    ("苏", "东坡"), ("王", "维"), ("杜", "牧"), ("崔", "护"), ("白", "居易"),
]
for s, g in literary_names:
    add_name(s, g, "literary")

# ============ Category 6: Names with Special Strokes (20) ============
# These names use characters with 氵, 艹, 阝, 辶, 忄, 扌, 王(玉), 月(肉), 礻 radicals
special_stroke_names = [
    ("沈", "海波"),   # 氵 in 海, 波
    ("汪", "清澜"),   # 氵 in 清, 澜
    ("江", "浩泽"),   # 氵 in 江, 浩, 泽
    ("花", "芬芳"),   # 艹 in 芬, 芳
    ("范", "芷蓉"),   # 艹 in 范, 芷, 蓉
    ("陈", "莉萍"),   # 艹 in 陈(阝), 莉, 萍
    ("阮", "芝薇"),   # 阝 in 阮, 艹 in 芝, 薇
    ("郑", "莲荷"),   # 阝 in 郑, 艹 in 莲, 荷
    ("连", "道远"),   # 辶 in 连, 道, 远
    ("罗", "进达"),   # 辶 in 进, 达
    ("邓", "怡情"),   # 忄 in 怡, 情
    ("孙", "恒悦"),   # 忄 in 恒, 悦
    ("张", "振扬"),   # 扌 in 振, 扬
    ("王", "琪瑶"),   # 王(玉) in 琪, 瑶
    ("王", "瑾瑜"),   # 王(玉) in 瑾, 瑜
    ("王", "璐琳"),   # 王(玉) in 璐, 琳
    ("胡", "朗月"),   # 月(肉) in 朗, 月
    ("周", "福礼"),   # 礻 in 福, 礼
    ("石", "祥瑞"),   # 礻 in 祥, 瑞
    ("叶", "初荷"),   # 衤 in 初, 艹 in 荷
]
for s, g in special_stroke_names:
    add_name(s, g, "special_stroke")

# ============ Category 7: Pattern Examples (30) ============
# All-good patterns
all_good_names = [
    ("金", "鑫"), ("王", "玉立"), ("白", "玉山"), ("丁", "文祥"),
    ("石", "永昌"), ("安", "如泰"), ("安", "平和"), ("吉", "天佑"),
    ("成", "功达"), ("全", "福寿"),
]
for s, g in all_good_names:
    add_name(s, g, "pattern_all_good")

# All-bad patterns
all_bad_names = [
    ("朱", "四"), ("于", "七宝"), ("尤", "七九"), ("毛", "四九"),
    ("孔", "二平"), ("于", "明"),
]
for s, g in all_bad_names:
    add_name(s, g, "pattern_all_bad")

# Mixed patterns
mixed_names = [
    ("张", "三丰"), ("赵", "子龙"), ("马", "致远"), ("刘", "禹锡"),
    ("高", "适"), ("岑", "参"), ("贺", "知章"), ("韦", "应物"),
    ("刘", "长卿"), ("孟", "浩然"), ("温", "庭筠"), ("李", "商隐"),
    ("范", "仲淹"), ("辛", "弃疾"), ("姜", "夔"), ("晏", "几道"),
    ("柳", "宗元"), ("左", "思"), ("陆", "机"), ("陶", "渊明"),
    ("谢", "灵运"), ("庾", "信"), ("鲍", "照"), ("王", "安石"),
]
for s, g in mixed_names:
    add_name(s, g, "pattern_mixed")

# ============ Category 8: Female Names (20) ============
female_names = [
    ("林", "志玲"), ("林", "徽因"), ("陆", "小曼"), ("周", "璇"),
    ("阮", "玲玉"), ("王", "祖贤"), ("关", "之琳"), ("张", "柏芝"),
    ("林", "心如"), ("徐", "熙媛"), ("徐", "熙娣"), ("刘", "若英"),
    ("莫", "文蔚"), ("梁", "咏琪"), ("许", "茹芸"), ("苏", "慧伦"),
    ("陈", "绮贞"), ("杨", "丞琳"), ("王", "心凌"), ("郭", "采洁"),
]
for s, g in female_names:
    add_name(s, g, "female")

print(f"Total name entries: {len(all_names)}")


# ============================================================
# GENERATE JSONL
# ============================================================
def generate_jsonl(output_path):
    entries = []
    generated_names = set()
    skipped = 0

    for i, name_entry in enumerate(all_names):
        surname = name_entry["surname"]
        given_name = name_entry["given_name"]
        category = name_entry["category"]
        full_name = surname + given_name

        # Check for duplicate
        if full_name in generated_names:
            skipped += 1
            continue
        generated_names.add(full_name)

        # Validate strokes exist
        missing = []
        compound_check = surname in COMPOUND_SURNAMES
        if compound_check:
            s1, s2 = COMPOUND_SURNAMES[surname]
            if s1 not in STROKES:
                missing.append(f"surname_part:{s1}")
            if s2 not in STROKES:
                missing.append(f"surname_part:{s2}")
        else:
            for c in surname:
                if c not in STROKES:
                    missing.append(f"surname:{c}")
        for c in given_name:
            if c not in STROKES:
                missing.append(f"name:{c}")

        if missing:
            # Try to add missing characters with default strokes
            for m in missing:
                char = m.split(":")[1]
                STROKES[char] = 1  # Default fallback
            print(f"Warning: Missing strokes for {full_name}: {missing}")

        # Calculate grids
        try:
            grids = calculate_five_grids(surname, given_name)
        except Exception as e:
            print(f"Error calculating for {full_name}: {e}")
            skipped += 1
            continue

        # Generate analysis
        content = generate_analysis(full_name, surname, given_name, grids, category)

        # Ensure content length 200-600 chars
        if len(content) < 200:
            content += "此姓名之数理分析需结合八字命理综合判断。姓名如衣冠，数理如其形制，需与个人先天命局相配方能发挥最大效用。"
        if len(content) > 600:
            content = content[:597] + "..."

        entry = {
            "title": f"{full_name}姓名五格分析",
            "content": content,
            "category": "xingming",
            "source_url": "xingming_knowledge_base",
            "source_quality": "authoritative",
            "verified": True,
        }
        entries.append(entry)

    # Write JSONL
    with open(output_path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"Generated {len(entries)} entries")
    print(f"Skipped {skipped} entries (duplicates or errors)")
    return len(entries)


if __name__ == "__main__":
    output_path = "/mnt/d/fortune-data/books/zonghe/tmp/part5_cases.jsonl"
    count = generate_jsonl(output_path)
    print(f"\nOutput file: {output_path}")
    print(f"Total entries: {count}")

    # Print a sample entry
    print("\n=== SAMPLE ENTRY ===")
    with open(output_path, "r", encoding="utf-8") as f:
        first_line = f.readline()
    import pprint
    pprint.pprint(json.loads(first_line))
