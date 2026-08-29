"""排盘结果 API（L4）：POST /api/paipan — 全字段八字排盘结果。

契约（复用 hehun BaziInput 口径）：
    body {birthYear, birthMonth, birthDay, birthHour(0-11 时辰序号 或 12-23
          时钟小时), gender('male'|'female'|'男'|'女'), city}
    → BaziResult 全字段序列化 JSON：
      基础：bazi / pillars(四柱盘面含藏干十神纳音长生) / day_master / shishen /
            nayin / gender / wuxing
      L1：jiaoyun(交运 page_text 等) / siling(司令) / siling_detail / qiyun_desc /
          起运分解 qiyun_detail / dayun(带十神+年份) / xiaoyun(小运 110 条) /
          dyshensha(每步大运神煞 [[干支,[名...]],...]) / liunian_full(30 年，
          批1 每项带 shensha 流年神煞 + rel 与原局关系 + dayun 所在大运及大运vs流年关系) /
          liunian_rel / liuyue / liushi / dayun_rel / ganzhi_rel
      L2：wuxing_energy(counts/wangshuai/changsheng/strength/yongshen) /
          chenggu(weight_text/jieci/分项) / shensha_detail(59 种 name/source/luck) /
          knowledge_index(六类可点文字)
    meta：命主信息头（公历/农历/生肖/时辰名，供前端渲染；随整体结果 AES 密文落库）

历史回看（batch3 B3-4，用户问题 #10）：
    GET /api/paipan/history — 只显示自己的脱敏摘要（生辰摘要/四柱/日主/一句话结论/
        排盘时间，limit 默认 50）；表单排盘无问题输入，「当时问的」用排盘摘要合成。
    GET /api/paipan/history/{id} — 归属校验（id + user_id 双条件）后返回完整盘面
        （重看 0 重跑：直接回读落库结果，不重新计算、不再落新记录）。
    两者均为只读查询（不建表不写库）；从未排过盘（表未创建）→ 空列表。

隐私红线：生辰 AES 密文落库 chart_records（与档案同加密口径、按 uid 归属隔离），
不入日志、不写其他 DAO。全接口 require_user 鉴权。
错误：生辰缺失/越界/伪日期（如 2 月 30 日）→ 400；未登录 → 401。
"""
import json
import logging
import sqlite3
from fastapi import APIRouter, Depends, HTTPException

from ..engines.bazi import BaziEngine, BaziResult
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
# 地支 → 五行（历史结论「月令旺衰」用：wangshuai 键为五行）
DIZHI_WUXING = {"子": "水", "丑": "土", "寅": "木", "卯": "木", "辰": "土", "巳": "火",
                "午": "火", "未": "土", "申": "金", "酉": "金", "戌": "土", "亥": "水"}
# 农历月名（含「冬月」「腊月」，与称骨表口径一致）
LUNAR_MONTH_CN = ["正月", "二月", "三月", "四月", "五月", "六月",
                  "七月", "八月", "九月", "十月", "冬月", "腊月"]
# 时钟小时 → 时辰名，全 24 小时覆盖（按时辰起点映射：子23-1/丑1-3/寅3-5/
# 卯5-7/辰7-9/巳9-11/午11-13/未13-15/申15-17/酉17-19/戌19-21/亥21-23；
# 子时含晚子时 23 点口径，与 BaziEngine 晚子时处理一致）
SHICHEN_NAME = {
    23: "子时", 0: "子时", 1: "丑时", 2: "丑时",
    3: "寅时", 4: "寅时", 5: "卯时", 6: "卯时",
    7: "辰时", 8: "辰时", 9: "巳时", 10: "巳时",
    11: "午时", 12: "午时", 13: "未时", 14: "未时",
    15: "申时", 16: "申时", 17: "酉时", 18: "酉时",
    19: "戌时", 20: "戌时", 21: "亥时", 22: "亥时",
}


# ── 全局依赖注入 ──────────────────────────────────────────────

_bazi_engine: BaziEngine = None
_db_path: str = None


def setup(engine: BaziEngine):
    """主应用生命周期注入 BaziEngine（同 hehun/union 模式）。"""
    global _bazi_engine
    _bazi_engine = engine


def setup_db(path: str):
    """注入 chart_records 落库 db 路径（主应用同一 fortune.db，同 setup(engine) 模式）。

    未注入 → 不落库（只读路径下表单排盘零副作用；测试注入 tmp 路径隔离）。
    """
    global _db_path
    _db_path = path


# ── API 端点 ──────────────────────────────────────────────────

@router.post("/api/paipan")
async def paipan(req: BaziInput, uid: str = Depends(require_user)):
    """排盘：生辰 → BaziResult 全字段 JSON（结果 AES 密文落库 chart_records）。

    - 401：未登录（require_user 鉴权红线）
    - 400：生辰缺失 / 越界（年 1900-2100、月 1-12、日 1-31）/ 伪日期（2 月 30 日等）
    - 503：引擎未注入（服务未就绪）
    """
    if _bazi_engine is None:
        raise HTTPException(status_code=503, detail="Paipan service not ready")
    person = _resolve_person(req)
    result = _bazi_engine.calculate(
        person.year, person.month, person.day,
        person.hour, person.minute, person.city, person.gender,
        person.daylightSaving, person.lateChildHour)
    body = serialize_bazi(result, _bazi_engine, person)
    # 排盘结果落库 chart_records（重看 0 重跑；本人排盘，命主档案不强制绑定）
    if _db_path:
        try:
            from src.storage.chart_dao import ChartDAO
            ChartDAO(_db_path).save_chart(
                uid, None,
                {"year": person.year, "month": person.month, "day": person.day,
                 "hour": person.hour, "minute": person.minute,
                 "city": person.city, "gender": person.gender,
                 "calendar": "solar"},
                body)
        except Exception as e:
            logger.warning("paipan 落库失败 uid=%s: %s", uid, e)  # 不阻塞主流程
    return body


# ── 历史回看（batch3 B3-4）──────────────────────────────────────

@router.get("/api/paipan/history")
async def paipan_history(limit: int = 50, uid: str = Depends(require_user)):
    """排盘历史（只显示自己的）：脱敏摘要列表。

    - 401：未登录（require_user 鉴权红线）
    - 503：db 未注入（服务未就绪）
    - 每项为脱敏摘要：生辰摘要/四柱/日主/一句话结论/排盘时间；绝不回全量盘面
      （bazi_json 仅在详情接口归属校验后返回）
    - 只读查询：不建表不写库；chart_records 表尚未创建（从未排过盘）→ 空列表
    """
    if _db_path is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    limit = max(1, min(int(limit), 50))
    return {"records": _read_chart_history(_db_path, uid, limit)}


@router.get("/api/paipan/history/{record_id}")
async def paipan_history_detail(record_id: int, uid: str = Depends(require_user)):
    """排盘历史详情（归属校验：只能回看自己的）：完整盘面。

    重看 0 重跑：直接回读落库的 bazi_json，不重新计算、不再落新记录。
    - 401：未登录；404：记录不存在或非本人（id + user_id 双条件，不泄露存在性）
    """
    if _db_path is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    from src.storage.models import connect as db_connect
    from src.storage.dao import _decrypt_or_plain

    conn = db_connect(_db_path)
    try:
        try:
            row = conn.execute(
                "SELECT id, person_id, birth_enc, bazi_enc, created_at "
                "FROM chart_records WHERE id=? AND user_id=?",
                (record_id, uid)).fetchone()
        except sqlite3.OperationalError:
            row = None
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="记录不存在")

    def _load(enc: str) -> dict:
        try:
            return json.loads(_decrypt_or_plain(enc) or "{}")
        except (ValueError, TypeError):
            return {}

    return {
        "id": row[0],
        "person_id": row[1],
        "birth": _load(row[2]),
        "chart": _load(row[3]),
        "created_at": row[4],
    }


def _read_chart_history(db_path: str, uid: str, limit: int) -> list:
    """读 chart_records 脱敏摘要列表（只读查询：不建表不写库）。

    与 POST 落库同表（ChartDAO 口径）；表尚未创建 → 返回 []（从未排过盘）。
    """
    from src.storage.models import connect as db_connect
    from src.storage.dao import _decrypt_or_plain

    conn = db_connect(db_path)
    try:
        try:
            rows = conn.execute(
                "SELECT id, person_id, birth_enc, bazi_enc, created_at "
                "FROM chart_records WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (uid, limit)).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()

    def _load(enc: str) -> dict:
        try:
            return json.loads(_decrypt_or_plain(enc) or "{}")
        except (ValueError, TypeError):
            return {}

    return [_history_item(r[0], r[1], _load(r[2]), _load(r[3]), r[4])
            for r in rows]


def _history_item(record_id: int, person_id, birth: dict, bazi: dict,
                  created_at: str) -> dict:
    """单条脱敏摘要：生辰摘要 + 四柱 + 日主 + 一句话结论 + 排盘时间。

    用户视角（一眼认出「这是我上次排的」）：谁（person_id 供前端解析姓名）+
    什么时候排的（created_at）+ 当时问的（排盘摘要）+ 一句话结论（日主/格局/月令旺衰）。
    隐私：生辰只随本人记录返回（接口已按 uid 归属隔离）；全量盘面不进列表。
    """
    bazi_list = bazi.get("bazi") or []
    gender = birth.get("gender") or bazi.get("gender") or ""
    gender_cn = "女" if str(gender) in ("female", "女") else "男"

    # 「当时问的」：表单排盘无问题输入，用排盘摘要合成（与 union 自动归档文案同思路；
    # 聊天路径的真实问题在 consultations 表，chart_records 未冗余存问题列，不迁移）
    hour = birth.get("hour")
    shichen = (SHICHEN_NAME.get(int(hour), "") if isinstance(hour, (int, float))
               and not isinstance(hour, bool) else "")
    parts = ["八字排盘", f"{gender_cn}命"]
    y, m, d = birth.get("year"), birth.get("month"), birth.get("day")
    if y and m and d:
        parts.append(f"{int(y)}年{int(m)}月{int(d)}日")
    if shichen:
        parts.append(shichen)
    if birth.get("city"):
        parts.append(str(birth["city"]))

    # 一句话结论：日主 + 格局（聊天路径已存 geju）或 月令旺衰+强弱+用神（表单路径）
    dm = bazi.get("day_master") or ""
    concl = f"日主{dm}" if dm else "八字排盘"
    geju = bazi.get("geju") or ""
    if geju:
        concl += f" · {geju}"
    else:
        we = bazi.get("wuxing_energy") or {}
        month_zhi = ""
        if len(bazi_list) > 1:
            cell = bazi_list[1]
            if isinstance(cell, str) and len(cell) >= 2:
                month_zhi = cell[1]
            elif isinstance(cell, list) and len(cell) >= 2:
                month_zhi = str(cell[1])
        wang = (we.get("wangshuai") or {}).get(DIZHI_WUXING.get(month_zhi, ""))
        if month_zhi and wang:
            concl += f" · {month_zhi}月{DIZHI_WUXING.get(month_zhi, '')}{wang}"
        if we.get("strength"):
            concl += f" · {we['strength']}"
        if we.get("yongshen"):
            concl += f" · {we['yongshen']}"

    return {
        "id": record_id,
        "person_id": person_id,
        "birth": birth,            # 归属校验后仅本人可见（用户识别用）
        "bazi": bazi_list,
        "day_master": dm,
        "summary": " · ".join(parts),
        "conclusion": concl,
        "created_at": created_at,
    }


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

    # 称骨：原样透传引擎结果（weight_text/liang/qian/jieci/parts）。
    # parts 分项由引擎按晚子时归日后的同一农历日计算（与总重同源同口径，
    # 分项合计恒等于总重）——不再在此用原始日期重算，避免分项≠总重矛盾。
    chenggu = dict(r.chenggu or {})

    # 命主信息头（农历干支月日 + 生肖 + 时辰名 + 公历文本）。
    # lunar 农历信息用引擎归一化口径（r.lunar，晚子时/真太阳时已归日），
    # 保证 meta 的月/日文本与四柱（日柱=次日）自洽。
    _lunar = r.lunar or {}
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
            "month_text": LUNAR_MONTH_CN[_lunar["month"] - 1]
                          if _lunar.get("month") else "",
            "day_text": _lunar.get("day_text", ""),
        },
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
        "qiyun_sui_desc": r.qiyun_sui_desc,  # G5：问真实岁「X岁X个月起运」显示串
        "qiyun_detail": list(r.qiyun_detail or ()),
        "dayun": dayun,
        # 小运（110 条干支，起运前逐年）与每步大运神煞 [[干支, [神煞...]], ...]（P2-2 补全）
        "xiaoyun": list(r.xiaoyun),
        "dyshensha": r.dyshensha,
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
        # 注：taiyuan / minggong / shenggong / kongwang 等宫位字段引擎已算出，
        # 但本接口暂未序列化（前端排盘页当前不展示）——后续扩展时在此追加。
    }
