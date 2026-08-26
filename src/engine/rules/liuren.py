# src/engine/rules/liuren.py
"""六壬确定性规则库 v1：九宗门判定 / 旬空落传落课 / 贵人顺逆 / 三传五行。

口径（与《六壬大全》对照，审计注明）：
  - 九宗门判定次序：伏吟/返吟盘型优先；常盘先看四课上下克
    （一上克下=元首课、一下贼上=重审课；同向多克取与日干比者为
    比用课，俱比/俱不比/上下互克=涉害课，涉害深浅未实现用孟仲季
    简便法并记降级）；无克依次：干支同宫=八专课（不取遥克）→
    遥克（蒿矢=上神克日、弹射=日克上神，单候选不论比）→
    干上神==日支（课2=课3实三课）=别责课 → 昴星课
    （阳日取地盘酉上神=虎视、阴日取天盘酉之下神=冬蛇掩目）
  - 旬空：日柱所在旬空亡二字；落三传称"空亡入传"，四课上神落空
    称"空亡落课"
  - 贵人顺逆：贵人落在地盘阳支（子寅辰午申戌）顺行、阴支逆行
  - 三传五行：天盘支纳五行（子水丑土寅木卯木辰土巳火午火未土申金酉金戌土亥水）

只做确定性规则/查表（可验证），不产解释性断语（吉凶归 LLM 综合层）。
输入 chart 为 LiurenEngine().calculate(...).to_dict()，也可传部分字段的最小 dict。
"""
from __future__ import annotations

# ---- 常量（与 src/engines/liuren.py 同口径，独立复刻为判定层，防漂移由
#      测试 test_classify_consistent_with_engine 交叉验证）----

ZHI_WUXING = {"子": "水", "丑": "土", "寅": "木", "卯": "木", "辰": "土",
              "巳": "火", "午": "火", "未": "土", "申": "金", "酉": "金",
              "戌": "土", "亥": "水"}
GAN_WUXING = {"甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
              "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水"}
WUXING_KE = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}
YANG_ZHI = ("子", "寅", "辰", "午", "申", "戌")
YANG_GAN = ("甲", "丙", "戊", "庚", "壬")
GAN_JIGONG = {"甲": "寅", "乙": "辰", "丙": "巳", "丁": "未", "戊": "巳",
              "己": "未", "庚": "申", "辛": "戌", "壬": "亥", "癸": "丑"}
MENG_ZHI = ("寅", "申", "巳", "亥")
ZHONG_ZHI = ("子", "午", "卯", "酉")


def _wuxing_of(char: str) -> str:
    """干支单字取五行（干用干表、支用支表）。"""
    if char in GAN_WUXING:
        return GAN_WUXING[char]
    return ZHI_WUXING[char]


def zhi_wuxing(zhi: str) -> str:
    """地支五行（子水丑土寅木卯木辰土巳火午火未土申金酉金戌土亥水）。"""
    return ZHI_WUXING[zhi]


def sanchuan_wuxing(sanchuan: list) -> list:
    """三传五行（天盘支纳五行）。"""
    return [zhi_wuxing(c) for c in sanchuan]


def kongwang_in_sanchuan(sanchuan: list, xunkong: list) -> bool:
    """旬空是否落三传（空亡入传）。"""
    return any(c in xunkong for c in sanchuan)


def kongwang_in_sipan(sipan: list, xunkong: list) -> list:
    """四课上神落入旬空者（空亡落课，去重）。

    sipan 每课为 {"位置": 课名, "干支": 上神+下神}，取每课上神（干支首字）。
    """
    seen = []
    for ke in sipan:
        upper = ke["干支"][0]
        if upper in xunkong and upper not in seen:
            seen.append(upper)
    return seen


def guiren_direction(palace_zhi: str) -> str:
    """贵人顺逆：落地盘阳支（子寅辰午申戌）顺行、阴支逆行。"""
    return "顺行" if palace_zhi in YANG_ZHI else "逆行"


def classify_zongmen(chart: dict) -> tuple:
    """九宗门判定（独立于引擎的判定层）。

    依据 chart 的排盘事实（盘型/四课克情/日干日支/干上神支上神）判定
    (宗门大类, 课名)。发用取用细节（三传）由引擎负责，此处只做课名分类。

    Returns:
        (宗门, 课名)，如 ("贼克", "重审课") / ("涉害", "涉害课")。
    """
    raw = chart.get("raw_data", {})
    pan_type = raw.get("盘型", "常")
    ke_qing = raw.get("四课克情", [])
    day_gan = chart.get("day_gan", "")
    day_zhi = chart.get("day_zhi", "")
    if not ke_qing or not day_gan:
        raise ValueError("chart 缺四课克情/日干，无法判定宗门")
    s1 = ke_qing[0]["上"]  # 干上神
    s3 = ke_qing[2]["上"]  # 支上神

    if pan_type == "伏吟":
        # 伏吟：课1（干上神vs日干）有克 → 不虞；无克阳日自任、阴日自信
        if ke_qing[0]["克"] != "无克":
            return "伏吟", "不虞"
        return "伏吟", ("自任" if day_gan in YANG_GAN else "自信")

    if pan_type == "返吟":
        # 返吟：有克 → 无依；无克（丁丑/己丑/辛丑/丁未/己未/辛未六日）→ 井栏格
        has_ke = any(k["克"] != "无克" for k in ke_qing)
        return "返吟", ("无依" if has_ke else "井栏格")

    # ---- 常盘：先看四课上下克 ----
    down = [k["上"] for k in ke_qing if k["克"] == "下贼上"]
    up = [k["上"] for k in ke_qing if k["克"] == "上克下"]
    total = len(down) + len(up)

    if total == 1:
        return "贼克", ("重审课" if down else "元首课")
    if total >= 2:
        if down and up:
            return "涉害", "涉害课"  # 上下互克 → 涉害
        group = down if down else up
        matched = [c for c in group if (c in YANG_ZHI) == (day_gan in YANG_GAN)]
        if len(matched) == 1:
            return "比用", "比用课"
        return "涉害", "涉害课"  # 俱比/俱不比 → 涉害

    # ---- 无克 ----
    if GAN_JIGONG[day_gan] == day_zhi:
        return "八专", "八专课"  # 干支同宫，不取遥克
    # 遥克（蒿矢=上神克日优先，弹射=日克上神）
    uppers = [k["上"] for k in ke_qing]
    she_ke_day = [u for u in uppers
                  if WUXING_KE[_wuxing_of(u)] == GAN_WUXING[day_gan]]
    day_ke_she = [u for u in uppers
                  if WUXING_KE[GAN_WUXING[day_gan]] == _wuxing_of(u)]
    if she_ke_day or day_ke_she:
        return "遥克", ("蒿矢课" if she_ke_day else "弹射课")
    # 别责：干上神==日支（课2=课3实三课）
    if s1 == day_zhi:
        return "别责", "别责课"
    # 昴星
    return "昴星", ("虎视" if day_gan in YANG_GAN else "冬蛇掩目")


def analyze(chart: dict) -> list:
    """确定性要点（供推演链步骤段）：宗门课名/旬空落传落课/贵人顺逆/三传五行。"""
    sanchuan = chart.get("sanchuan", [])
    xunkong = chart.get("xunkong", [])
    sipan = chart.get("sipan", [])
    raw = chart.get("raw_data", {})
    zongmen, kename = classify_zongmen(chart)

    points = [f"三传{sanchuan}为{kename}（{zongmen}）"]
    if kongwang_in_sanchuan(sanchuan, xunkong):
        points.append(f"旬空{xunkong}入三传")
    else:
        points.append(f"旬空{xunkong}不入三传")
    kong_ke = kongwang_in_sipan(sipan, xunkong)
    if kong_ke:
        points.append(f"空亡落四课上神:{kong_ke}")
    points.append(f"三传五行:{sanchuan_wuxing(sanchuan)}")
    points.append(f"贵人{raw.get('guiren_shen', '')}{raw.get('guiren_direction', '')}")
    return points


def evaluate(chart: dict) -> dict:
    """确定性要点字典（供跑分断言，考卷 expected 直接对其键值断言）。"""
    sanchuan = chart.get("sanchuan", [])
    xunkong = chart.get("xunkong", [])
    sipan = chart.get("sipan", [])
    raw = chart.get("raw_data", {})
    zongmen, kename = classify_zongmen(chart)
    return {
        "要点": analyze(chart),
        "三传": sanchuan,
        "宗门": zongmen,
        "课名": kename,
        "三传五行": sanchuan_wuxing(sanchuan),
        "旬空": xunkong,
        "空亡入传": kongwang_in_sanchuan(sanchuan, xunkong),
        "空亡落四课": kongwang_in_sipan(sipan, xunkong),
        "贵人": raw.get("guiren_shen", ""),
        "贵人顺逆": raw.get("guiren_direction", ""),
    }
