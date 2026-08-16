# src/engine/rules/qimen.py
"""奇门遁甲确定性规则库 v1：八门/九星五行属性、三吉门、阴阳遁、八神序。

口径：烟波钓叟歌（《奇门遁甲秘笈大全》所收）。
只做确定性查表/排盘事实，不产解释性断语（吉凶解释归 LLM 综合层）；
排盘事实（值符星/值使门/八门九星分布）来自 QimenEngine，本库仅断言其存在与格式。
"""
from __future__ import annotations

from lunar_python import Solar

# ---- 八门五行属性（按八门本宫八卦五行）----
# 口诀：开乾金、休坎水、生艮土、伤震木、杜巽木、景离火、死坤土、惊兑金
MEN_WUXING = {
    "开": "金",   # 开门居乾宫，乾为金
    "休": "水",   # 休门居坎宫，坎为水
    "生": "土",   # 生门居艮宫，艮为土
    "伤": "木",   # 伤门居震宫，震为木
    "杜": "木",   # 杜门居巽宫，巽为木
    "景": "火",   # 景门居离宫，离为火
    "死": "土",   # 死门居坤宫，坤为土
    "惊": "金",   # 惊门居兑宫，兑为金
}

# ---- 九星五行属性（按九星本宫八卦五行；天禽居中宫，寄坤二宫属土）----
STAR_WUXING = {
    "天蓬": "水",  # 天蓬星居坎宫，属水
    "天任": "土",  # 天任星居艮宫，属土
    "天冲": "木",  # 天冲星居震宫，属木
    "天辅": "木",  # 天辅星居巽宫，属木
    "天英": "火",  # 天英星居离宫，属火
    "天芮": "土",  # 天芮星居坤宫，属土
    "天柱": "金",  # 天柱星居兑宫，属金
    "天心": "金",  # 天心星居乾宫，属金
    "天禽": "土",  # 天禽星居中宫，寄坤二宫，属土
}

# ---- 三吉门：休、生、开 ----
SAN_JI_MEN = {"休", "生", "开"}

# ---- 八神固定顺序：值符→螣蛇→太阴→六合→白虎→玄武→九地→九天 ----
BA_SHEN_ORDER = ["值符", "螣蛇", "太阴", "六合", "白虎", "玄武", "九地", "九天"]

# ---- 阴阳遁节气（以节气为界：冬至后阳遁、夏至后阴遁）----
YANG_DUN_TERMS = ("冬至", "小寒", "大寒", "立春", "雨水", "惊蛰",
                  "春分", "清明", "谷雨", "立夏", "小满", "芒种")
YIN_DUN_TERMS = ("夏至", "小暑", "大暑", "立秋", "处暑", "白露",
                 "秋分", "寒露", "霜降", "立冬", "小雪", "大雪")


def men_attribute(men: str) -> str:
    """八门五行属性查表（烟波钓叟歌八门落宫五行）。"""
    if men not in MEN_WUXING:
        raise ValueError(f"非法八门: {men}")
    return MEN_WUXING[men]


def star_attribute(star: str) -> str:
    """九星五行属性查表（九星落宫五行，天禽寄坤二宫属土）。"""
    if star not in STAR_WUXING:
        raise ValueError(f"非法九星: {star}")
    return STAR_WUXING[star]


def san_ji_men(men: str) -> bool:
    """三吉门判定：休、生、开为三吉门，其余为凶门。"""
    if men not in MEN_WUXING:
        raise ValueError(f"非法八门: {men}")
    return men in SAN_JI_MEN


def ba_shen_order() -> list[str]:
    """八神固定顺序：值符→螣蛇→太阴→六合→白虎→玄武→九地→九天。"""
    return list(BA_SHEN_ORDER)


def dun_style_by_term(term: str) -> str:
    """按节气名判定阴阳遁：冬至后（冬至→芒种）阳遁、夏至后（夏至→大雪）阴遁。"""
    if term in YANG_DUN_TERMS:
        return "阳遁"
    if term in YIN_DUN_TERMS:
        return "阴遁"
    raise ValueError(f"未知节气: {term}")


def _resolve_solar_term(year: int, month: int, day: int,
                        hour: int, minute: int = 0) -> str:
    """取公历时刻所处节气名（lunar-python，独立于 QimenEngine 的查表实现）。"""
    solar = Solar.fromYmdHms(year, month, day, hour, minute, 0)
    lunar = solar.getLunar()
    current = lunar.getCurrentJieQi()
    if current is not None:
        return current.getName()
    prev = lunar.getPrevJieQi()
    return prev.getName() if prev is not None else "冬至"


def dun_style(year: int, month: int, day: int,
              hour: int, minute: int = 0) -> str:
    """阴阳遁判定（独立查表实现）：冬至后阳遁、夏至后阴遁，以节气为界。

    结果可与 QimenEngine.calculate(...).dun_type 交叉验证。
    """
    return dun_style_by_term(_resolve_solar_term(year, month, day, hour, minute))


def _field(result, key: str, default=None):
    """兼容 QimenResult 对象与 dict 两种输入（规则库独立于排盘引擎）。"""
    if isinstance(result, dict):
        return result.get(key, default)
    return getattr(result, key, default)


def analyze(result) -> list[str]:
    """确定性要点列表：遁局/局数/值符星/值使门/三吉门/八门九星五行/八神序。

    只列排盘事实与查表结论，不做吉凶解释（解释归 LLM 综合层）。
    """
    points = []
    dun = _field(result, "dun_type")
    ju = _field(result, "ju_number")
    if dun:
        points.append(f"遁局：{dun}{ju or ''}局")
    zf = _field(result, "zhifu_star")
    zs = _field(result, "zhishi_door")
    if zf:
        points.append(f"值符星：{zf}（{star_attribute(zf)}）")
    if zs:
        points.append(f"值使门：{zs}（{men_attribute(zs)}）")
    bamen = _field(result, "bamen", {}) or {}
    jiuxing = _field(result, "jiuxing", {}) or {}
    doors = set(bamen.values()) if isinstance(bamen, dict) else set()
    stars = set(jiuxing.values()) if isinstance(jiuxing, dict) else set()
    san_ji = [m for m in ("休", "生", "开") if m in doors]
    points.append(f"三吉门：{'、'.join(san_ji) if san_ji else '无'}")
    if doors:
        points.append(f"八门五行：{'、'.join(f'{m}{men_attribute(m)}' for m in sorted(doors))}")
    if stars:
        points.append(f"九星五行：{'、'.join(f'{s}{star_attribute(s)}' for s in sorted(stars))}")
    points.append(f"八神序：{'、'.join(BA_SHEN_ORDER)}")
    return points


def evaluate(result) -> dict:
    """结构化确定性事实（供跑分断言）：遁局/局数/值符星/值使门/八门九星属性/三吉门/八神序。"""
    bamen = _field(result, "bamen", {}) or {}
    jiuxing = _field(result, "jiuxing", {}) or {}
    doors = bamen.values() if isinstance(bamen, dict) else ()
    stars = jiuxing.values() if isinstance(jiuxing, dict) else ()
    san_ji = [m for m in ("休", "生", "开") if m in set(doors)]
    return {
        "遁局": _field(result, "dun_type", ""),
        "局数": _field(result, "ju_number", 0),
        "值符星": _field(result, "zhifu_star", ""),
        "值使门": _field(result, "zhishi_door", ""),
        "八门属性": {m: men_attribute(m) for m in doors if m},
        "九星属性": {s: star_attribute(s) for s in stars if s},
        "三吉门": san_ji,
        "八神序": list(BA_SHEN_ORDER),
    }
