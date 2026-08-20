"""排盘结果 API（L4）：POST /api/paipan — 全字段八字排盘结果。

契约（复用 hehun BaziInput 口径）：
    body {birthYear, birthMonth, birthDay, birthHour(0-11 时辰序号 或 12-23
          时钟小时), gender('male'|'female'|'男'|'女'), city}
    → BaziResult 全字段序列化 JSON：
      基础：bazi / pillars(四柱盘面含藏干十神纳音长生) / day_master / shishen /
            nayin / gender / wuxing
      L1：jiaoyun(交运 page_text 等) / siling(司令) / siling_detail / qiyun_desc /
          起运分解 qiyun_detail / dayun(带十神+年份) / liunian_full(30 年) /
          liunian_rel / liuyue / liushi / dayun_rel / ganzhi_rel
      L2：wuxing_energy(counts/wangshuai/changsheng/strength/yongshen) /
          chenggu(weight_text/jieci/分项) / shensha_detail(59 种 name/source/luck) /
          knowledge_index(六类可点文字)
    meta：命主信息头（公历/农历/生肖/时辰名，供前端渲染，不落库）

隐私红线：生辰只用于内存排盘（engine.calculate），不落库、不入日志、不写
DAO —— 与合婚接口同口径。全接口 require_user 鉴权。
错误：生辰缺失/越界/伪日期（如 2 月 30 日）→ 400；未登录 → 401。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from lunar_python import Solar

from ..engines.bazi import BaziEngine, BaziResult
from ..engines import chenggu as chenggu_mod
from ..security.auth import require_user
from ..api.birth_contract import normalize_gender, normalize_hour
from ..api.hehun import BaziInput, _resolve_person

logger = logging.getLogger(__name__)
router = APIRouter(tags=["paipan"])

# ── 序列化用静态表 ─────────────────────────────────────────────

# 地支藏干（本气/中气/余气序，问真口径，与 _calc_geju 藏干表一致）
CANG_GAN = {
    "子": "癸", "丑": "己癸辛", "寅": "甲丙戊", "卯": "乙", "辰": "戊乙癸",
    "巳": "丙戊庚", "午": "丁己", "未": "己丁乙", "申": "庚壬戊", "酉": "辛",
    "戌": "戊辛丁", "亥": "壬甲",
}
# 地支 → 生肖（命主信息头）
SHENGXIAO = {"子": "鼠", "丑": "牛", "寅": "虎", "卯": "兔", "辰": "龙", "巳": "蛇",
             "午": "马", "未": "羊", "申": "猴", "酉": "鸡", "戌": "狗", "亥": "猪"}
# 农历月名（含「冬月」「腊月」，与称骨表口径一致）
LUNAR_MONTH_CN = ["正月", "二月", "三月", "四月", "五月", "六月",
                  "七月", "八月", "九月", "十月", "冬月", "腊月"]
# 时钟小时 → 时辰名（子时按晚子时 23 点口径，与 BaziEngine 晚子时处理一致）
SHICHEN_NAME = {23: "子时", 0: "子时", 1: "丑时", 3: "寅时", 5: "卯时", 7: "辰时",
                9: "巳时", 11: "午时", 13: "未时", 15: "申时", 17: "酉时",
                19: "戌时", 21: "亥时"}


# ── 全局依赖注入 ──────────────────────────────────────────────

_bazi_engine: BaziEngine = None


def setup(engine: BaziEngine):
    """主应用生命周期注入 BaziEngine（同 hehun/union 模式）。"""
    global _bazi_engine
    _bazi_engine = engine


# ── API 端点 ──────────────────────────────────────────────────

@router.post("/api/paipan")
async def paipan(req: BaziInput, uid: str = Depends(require_user)):
    """排盘：生辰 → BaziResult 全字段 JSON（内存排盘，生辰不落库）。

    - 401：未登录（require_user 鉴权红线）
    - 400：生辰缺失 / 越界（年 1900-2100、月 1-12、日 1-31）/ 伪日期（2 月 30 日等）
    - 503：引擎未注入（服务未就绪）
    """
    if _bazi_engine is None:
        raise HTTPException(status_code=503, detail="Paipan service not ready")
    person = _resolve_person(req)
    result = _bazi_engine.calculate(
        person.year, person.month, person.day,
        person.hour, person.minute, person.city, person.gender)
    return serialize_bazi(result, _bazi_engine, person)


# ── BaziResult 全字段序列化 ────────────────────────────────────

def serialize_bazi(r: BaziResult, engine: BaziEngine,
                   person: BaziInput) -> dict:
    """BaziResult → JSON 兼容 dict（全字段 + 前端盘面所需派生数据）。

    - dayun 由 (sui, ganzhi) 元组展开为 {sui, end_sui, ganzhi, shishen,
      start_year, end_year}（十神/年份由引擎与交运年表派生）
    - 四柱 pillars 派生藏干（含十神）、十二长生行运、纳音、日主高亮
    - chenggu 增补分项骨重（年/月/日/时，问真称骨表）
    - meta 为命主信息头：公历文本/农历干支+月日/生肖/时辰名
    其余字段原样透传（tuple 已转 list，无 datetime 类型，天然 JSON 兼容）。
    """
    day_gan = r.bazi[2][0]
    pillars = [
        {
            "name": name,
            "ganzhi": gz,
            "gan": gz[0],
            "zhi": gz[1],
            "shishen": ss,
            "is_day": idx == 2,
            "canggan": [{"gan": g, "shishen": engine._calc_shishen(day_gan, g)}
                        for g in CANG_GAN.get(gz[1], "")],
            "nayin": r.nayin[idx] if idx < len(r.nayin) else "",
            "xingyun": (r.wuxing_energy.get("changsheng", {})
                        .get(("年月日时")[idx], "")),
        }
        for idx, (name, gz, ss) in enumerate(
            zip(("年柱", "月柱", "日柱", "时柱"), r.bazi, r.shishen))
    ]

    # 大运展开：十神（大运天干 vs 日主）+ 年份（交运年表优先，兜底虚岁口径）
    jy_years = (r.jiaoyun.get("years") or []) if isinstance(r.jiaoyun, dict) else []
    jy_by_sui = {y.get("sui"): y.get("year") for y in jy_years}
    dayun = []
    for i, (sui, gz) in enumerate(r.dayun):
        start_year = jy_by_sui.get(sui) or (person.year + sui - 1)
        next_year = jy_by_sui.get(sui + 10) or (person.year + sui + 10 - 1)
        dayun.append({
            "sui": sui,
            "end_sui": sui + 9,
            "ganzhi": gz,
            "shishen": engine._calc_shishen(day_gan, gz[0]),
            "start_year": start_year,
            "end_year": next_year - 1,
        })

    # 称骨分项（问真称骨表：年按 60 甲子序、月按农历月、日按农历日、时按时支）
    chenggu = dict(r.chenggu or {})
    if chenggu:
        try:
            lunar = Solar.fromYmdHms(
                person.year, person.month, person.day,
                person.hour, person.minute, 0).getLunar()
            # 晚子时归日（与引擎口径一致，保证与排盘自洽）
            l_month = abs(lunar.getMonth())
            l_day = lunar.getDay()
            tables = chenggu_mod._load_tables()
            parts_qian = [
                int(round(tables["fn"][chenggu_mod._year_index(r.bazi[0])] * 10)),
                int(round(tables["cn"][l_month - 1] * 10)),
                int(round(tables["ln"][l_day - 1] * 10)),
                int(round(tables["un"][chenggu_mod._hour_index(r.bazi[3])] * 10)),
            ]
            labels = ["年 %s" % r.bazi[0], "月 %s" % LUNAR_MONTH_CN[l_month - 1],
                      "日 %s" % ("初%s" % chenggu_mod.RN[l_day - 1]
                                 if l_day <= 10 else lunar.getDayInChinese()),
                      "时 %s" % r.bazi[3][1]]
            # 分项骨重可不足 1 两（如 7 钱）：bone_weight_text 要求两≥1，这里手动格式化
            def _part_weight(qian: int) -> str:
                liang, q = divmod(qian, 10)
                text = "%s两" % chenggu_mod.RN[liang - 1] if liang else ""
                if q:
                    text += "%s钱" % chenggu_mod.RN[q - 1]
                return text

            chenggu["parts"] = [{
                "label": labels[i],
                "weight": _part_weight(parts_qian[i]),
            } for i in range(4)]
        except Exception as _e:  # 分项为展示增强，失败不阻塞主结果
            logger.warning("称骨分项计算失败，忽略: %s", _e)

    # 命主信息头（农历干支月日 + 生肖 + 时辰名 + 公历文本）
    lunar = None
    try:
        lunar = Solar.fromYmdHms(
            person.year, person.month, person.day,
            person.hour, person.minute, 0).getLunar()
    except Exception:
        lunar = None
    meta = {
        "solar_text": "%d年%d月%d日 %02d:%02d"
                      % (person.year, person.month, person.day,
                         person.hour, person.minute),
        "shichen": SHICHEN_NAME.get(person.hour, ""),
        "zodiac": SHENGXIAO.get(r.bazi[0][1], ""),
        "lunar": {
            "year_ganzhi": r.bazi[0],
            "month_ganzhi": r.bazi[1],
            "day_ganzhi": r.bazi[2],
            "month_text": LUNAR_MONTH_CN[abs(lunar.getMonth()) - 1] if lunar else "",
            "day_text": lunar.getDayInChinese() if lunar else "",
        } if lunar else {},
    }

    return {
        # 基础
        "bazi": list(r.bazi),
        "pillars": pillars,
        "day_master": r.day_master,
        "shishen": list(r.shishen),
        "nayin": list(r.nayin),
        "gender": r.gender,
        "wuxing": dict(r.wuxing or {}),
        # L1 运势
        "jiaoyun": r.jiaoyun,
        "siling": r.siling,
        "siling_detail": r.siling_detail,
        "qiyun_desc": r.qiyun_desc,
        "qiyun_detail": list(r.qiyun_detail or ()),
        "dayun": dayun,
        "dayun_rel": r.dayun_rel,
        "liunian_full": r.liunian_full,
        "liunian_rel": r.liunian_rel,
        "liuyue": r.liuyue,
        "liushi": r.liushi,
        "ganzhi_rel": r.ganzhi_rel,
        # L2 深度
        "wuxing_energy": r.wuxing_energy,
        "chenggu": chenggu,
        "shensha": list(r.shensha),
        "shensha_detail": r.shensha_detail,
        "knowledge_index": r.knowledge_index,
        # 元信息
        "meta": meta,
    }
