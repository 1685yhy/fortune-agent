"""名人命例库 API（L3-1，问真 VIP 同款门控）：2807 位历史人物命例 + 穷通宝鉴评注。

- 数据源：data/mingren/emperors_mingli.json（1.5MB，2807 条）
  {人名: {info(简介), info2(穷通宝鉴评注——徐乐吾曰等), source, flist([{name: 年份, data: 事件}...])}}
- GET /api/mingren?page=&size=&q= → 分页列表 + 姓名搜索（require_user）
  - 高级会员（plan ∈ pro/annual）→ 全量 2807 位；
  - 基础/免费用户（plan ∈ free/basic）→ 仅前 35 条（按库顺序），is_full=false；
  - 返回 {items, total(可见数), total_all(未门控匹配数), library_total, is_full}。
- GET /api/mingren/{name} → 详情（info/info2/flist + chart 命盘 + timeline 大运×大事对照）
  - 未解锁（免费/基础访问前 35 之外）→ 403 + {"code": "VIP_REQUIRED", ...}（前端弹开通引导）；
  - 未知人名 → 404。
  - chart/timeline（batch5 B5-3）：仅当 src/data/mingren_birth.json 载有该名人生辰
    且排盘引擎已注入时返回（时辰缺失 → 默认午时 + hour_note 标注；纯计算不落库）；
    无生辰名人只返回生平（has_chart=false）。
- 体验模式（EXPERIENCE_MODE）→ 全量免费（同 zeri/night/zhuanxiang 门控口径）。
- 数据只读加载一次进内存（模块级懒加载缓存）；门控服务端强制，不信任客户端。
"""
import calendar
import json
import logging
import re
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Path as FPath, Query

from src.config import is_experience_mode
from src.engines.bazi import BaziEngine, DIZHI, TIANGAN
from src.security.auth import require_user

from .birth_contract import normalize_gender, normalize_hour
from .paipan import CANG_GAN, LUNAR_MONTH_CN, SHENGXIAO, SHICHEN_NAME

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/mingren", tags=["mingren"])

# 免费/基础用户可见条数（按库顺序前 35，问真同款"免费试看 35 例"口径）
FREE_LIMIT = 35

# 高级会员档位：pro / annual 全量；free / basic 仅前 35 条
_FULL_PLANS = ("pro", "annual")

# 数据文件位置（项目根 data/mingren/），setup 可注入覆盖（测试用临时库）
_DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "mingren" / "emperors_mingli.json"

# 出生数据文件（batch5 B5-3：落 src/data/ 随代码部署——生产 rsync 排除 data/，
# 故不入 data/mingren/；原 1.5MB 命例库零改动）
_BIRTH_FILE = Path(__file__).resolve().parent.parent / "data" / "mingren_birth.json"

# 时辰缺失时的默认时辰（午时 12:00，与 birth_contract.normalize_hour(None) 口径一致）
_DEFAULT_HOUR = 12
# 时辰无考标注文案（时辰缺失 → 默认午时排盘并标注）
_HOUR_NOTE = "时辰无考，以午时推演"

# 时辰 → 北京时间 (hour, minute)。分钟 +20 是刻意选择（B5-3 数据核验结论）：
# 引擎按出生地真太阳时排盘（北京经度修正 -14.4 分 + 均时差），且修正后 hour>=23
# 按晚子时归次日。取每个时辰窗中段偏 +20 分，保证修正后仍落本时辰且不触发
# 晚子时跨日（如乾隆子时 00:20 → 真太阳时 00:1x，日柱仍为庚午，与清宫档案
# 「庚午日丙子时」逐字一致——若用 00:00 会修正到 23:5x 触发归日翻转导致日柱不同）。
_SHICHEN_TIME = {
    "子": (0, 20), "丑": (2, 20), "寅": (4, 20), "卯": (6, 20),
    "辰": (8, 20), "巳": (10, 20), "午": (12, 20), "未": (14, 20),
    "申": (16, 20), "酉": (18, 20), "戌": (20, 20), "亥": (22, 20),
}
_SHICHEN_RE = re.compile(r"^([子丑寅卯辰巳午未申酉戌亥])时?$")


def _parse_shichen(text):
    """「寅时」→ (4, 20) 北京时间；无/非法 → None（走默认午时）。"""
    if not text:
        return None
    m = _SHICHEN_RE.match(str(text).strip())
    if not m:
        return None
    return _SHICHEN_TIME[m.group(1)]

# 依赖注入（仿 zeri.py setup 模式）：主应用 lifespan 注入 MemberDAO；测试注入 stub
_member_dao = None
_data_path = None

# 数据懒加载缓存（2807 条 ≈ 1.5MB，进程内只读一次；dict 保序 = 库顺序）
_cache = None
_cache_lock = threading.Lock()

# 出生数据缓存（小文件，随 src/ 部署）+ 命盘计算缓存（确定性纯计算，按名缓存一次）
_birth_cache = None
_birth_path = None
_chart_cache: dict = {}
_bazi_engine: BaziEngine = None


def setup(member_dao=None, data_path=None, birth_path=None, engine=None):
    """在主应用生命周期中注入依赖。

    - member_dao：会员档位查询（门控）
    - data_path：命例库路径覆盖（测试用）
    - birth_path：出生数据路径覆盖（测试用；None 用默认 src/data/mingren_birth.json）
    - engine：BaziEngine 实例（main.py 注入同一引擎；未注入 → 详情不返回 chart，
      列表 has_chart=false，其余行为与注入前完全一致）
    """
    global _member_dao, _data_path, _birth_path, _bazi_engine, _cache, _birth_cache, _chart_cache
    _member_dao = member_dao
    _bazi_engine = engine
    if data_path is not None:
        _data_path = Path(data_path)
        _cache = None  # 换库时清缓存（测试用）
    if birth_path is not None:
        _birth_path = Path(birth_path)
        _birth_cache = None  # 换出生数据时清缓存（测试用）
        _chart_cache = {}  # 命盘缓存依赖出生数据，一并清


def _load_data() -> dict:
    """懒加载命例库（线程安全，只读一次）。"""
    global _cache
    if _cache is not None:
        return _cache
    path = _data_path or _DATA_FILE
    with _cache_lock:
        if _cache is not None:
            return _cache
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            logger.error("名人命例库缺失: %s", path)
            raise HTTPException(status_code=503, detail="命例库未就绪")
        except json.JSONDecodeError as e:
            logger.error("名人命例库损坏: %s (%s)", path, e)
            raise HTTPException(status_code=503, detail="命例库未就绪")
        if not isinstance(data, dict) or not data:
            logger.error("名人命例库为空: %s", path)
            raise HTTPException(status_code=503, detail="命例库未就绪")
        _cache = data
        return _cache


def _load_birth() -> dict:
    """懒加载出生数据（batch5 B5-3：{人名: {birth(公历), source, gender[, lunar]}}）。

    缺失/损坏 → {}（优雅降级：无命盘但有生平，不影响主库；不抛 503——
    出生数据是增强字段，主命例库仍可用）。
    """
    global _birth_cache
    if _birth_cache is not None:
        return _birth_cache
    path = _birth_path or _BIRTH_FILE
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.warning("名人出生数据缺失（命盘功能关闭）: %s", path)
        data = {}
    except json.JSONDecodeError as e:
        logger.warning("名人出生数据损坏（命盘功能关闭）: %s (%s)", path, e)
        data = {}
    _birth_cache = data if isinstance(data, dict) else {}
    return _birth_cache


def _parse_birth_text(text: str):
    """解析「YYYY年M月D日」（公历）→ (year, month, day)；非法 → None。

    为何不复用 handler._extract_bazi_info（handler.py:4361）：那是对话场景的
    模糊文本解析器（中文数字/农历/时辰/城市），且 year<1900 直接放弃——
    名人库以历代帝王为主（如朱元璋 1328、武则天 624），须精确解析 + 支持古历年份。
    """
    if not text:
        return None
    m = re.match(r"^(\d{3,4})年(\d{1,2})月(\d{1,2})日$", str(text).strip())
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (100 <= year <= 2100 and 1 <= month <= 12):
        return None
    if not (1 <= day <= calendar.monthrange(year, month)[1]):
        return None
    return year, month, day


def _serialize_chart(year: int, month: int, day: int, birth: dict) -> dict:
    """BaziEngine.calculate → 同构精简版 serialize_bazi（字段名与 paipan 全量版一致，
    前端可复用排盘页渲染；只含命盘卡所需字段，不含 liunian/xiaoyun 等大字段）。

    - 时辰字段（shichen，如「寅时」）→ 北京时间映射 _SHICHEN_TIME（真太阳时口径）；
      时辰缺失 → 默认午时（12:00）并标注 hour_note；gender 走 normalize_gender
    - 城市默认北京（引擎真太阳时口径，与排盘页默认一致）
    - 纯计算不落库（区别于 POST /api/paipan 的 chart_records 落库副作用）
    """
    engine = _bazi_engine
    if engine is None:
        return None
    sc = _parse_shichen(birth.get("shichen"))
    if sc:
        hour, minute = sc
        shichen_label = "%s时" % _SHICHEN_RE.match(str(birth["shichen"]).strip()).group(1)
    else:
        hour, minute = normalize_hour(birth.get("hour")), 0  # 缺失 → 12（午时）
        shichen_label = SHICHEN_NAME.get(hour, "")
    gender = normalize_gender(birth.get("gender"))
    r = engine.calculate(year, month, day, hour, minute, "北京", gender)

    day_gan = r.bazi[2][0]
    # 空亡（日柱定旬，旬头支 index=(zhi_idx-gan_idx)%12，空亡为其前两支）
    dg_idx = (DIZHI.index(r.bazi[2][1]) - TIANGAN.index(r.bazi[2][0])) % 12
    kong_pair = {DIZHI[(dg_idx - 2) % 12], DIZHI[(dg_idx - 1) % 12]}
    # 神煞归柱（shensha_detail.source 形如「日柱」「年月柱」「年月日时柱」）
    shensha_of_pillar = [[] for _ in range(4)]
    for item in r.shensha_detail or []:
        src = str(item.get("source") or "")
        for i, ch in enumerate("年月日时"):
            if ch in src:
                shensha_of_pillar[i].append(item.get("name"))
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
            "kong": gz[1] if gz[1] in kong_pair else "",
            "shensha": shensha_of_pillar[idx],
        }
        for idx, (name, gz, ss) in enumerate(
            zip(("年柱", "月柱", "日柱", "时柱"), r.bazi, r.shishen))
    ]

    # 大运展开：十神 + 年份（与 paipan.serialize_bazi 同算法：交运年表优先，
    # 兜底虚岁口径 person_year + sui - 1）
    jy_years = (r.jiaoyun.get("years") or []) if isinstance(r.jiaoyun, dict) else []
    jy_by_sui = {y.get("sui"): y.get("year") for y in jy_years}
    dayun = []
    for sui, gz in r.dayun:
        start_year = jy_by_sui.get(sui) or (year + sui - 1)
        next_year = jy_by_sui.get(sui + 10) or (year + sui + 10 - 1)
        dayun.append({
            "sui": sui,
            "end_sui": sui + 9,
            "ganzhi": gz,
            "shishen": engine._calc_shishen(day_gan, gz[0]),
            "start_year": start_year,
            "end_year": next_year - 1,
        })

    # 起运信息：出生后 X 年…起运 + 起运虚岁 + 起运公历年
    start_sui = dayun[0]["sui"] if dayun else 0
    start_year = jy_by_sui.get(start_sui) or (year + start_sui - 1)

    _lunar = r.lunar or {}
    meta = {
        "solar_text": "%d年%d月%d日" % (year, month, day),
        "shichen": shichen_label,
        "gankun": "乾造" if gender == "男" else "坤造",
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

    chart = {
        "bazi": list(r.bazi),
        "pillars": pillars,
        "day_master": r.day_master,
        "shishen": list(r.shishen),
        "nayin": list(r.nayin),
        "gender": gender,
        "wuxing": dict(r.wuxing or {}),
        "qiyun": {
            "desc": r.qiyun_desc,
            "detail": list(r.qiyun_detail or ()),
            "start_sui": start_sui,
            "start_year": start_year,
        },
        "dayun": dayun,
        "meta": meta,
    }
    if birth.get("shichen") is None and birth.get("hour") is None:
        chart["hour_note"] = _HOUR_NOTE
    return chart


def _chart_of(name: str) -> dict:
    """按名取命盘（确定性纯计算，进程内缓存一次；失败/无出生数据 → None）。"""
    if _bazi_engine is None:
        return None  # 引擎未注入（测试/未接线）→ 恒无命盘，不受缓存残留影响
    if name in _chart_cache:
        return _chart_cache[name]
    birth = _load_birth().get(name)
    if not birth or _bazi_engine is None:
        _chart_cache[name] = None
        return None
    ymd = _parse_birth_text(birth.get("birth") or "")
    if not ymd:
        logger.warning("名人出生日期无法解析（命盘跳过）: %s = %r", name, birth.get("birth"))
        _chart_cache[name] = None
        return None
    try:
        chart = _serialize_chart(ymd[0], ymd[1], ymd[2], birth)
    except Exception as e:
        logger.warning("名人排盘失败（命盘跳过）: %s (%s)", name, e)
        _chart_cache[name] = None
        return None
    _chart_cache[name] = chart
    return chart


def _build_timeline(item: dict, chart: dict) -> list:
    """大运×大事对照：flist 事件年份 → 所在大运分组。

    算法：事件年份落在哪步大运的 [start_year, end_year] 区间 → 归该大运；
    起运前的年份（早于首步大运 start_year）→ 「未起运」组；
    晚于末步大运 end_year（超 110+ 虚岁，极罕见）→ 「命后」组。
    事件年份解析：flist name 为「YYYY年」或裸「YYYY」（库内两种并存，
    如嘉靖『1542』）；非四位数年份（「公元前551年」「1644后」等）不参与对照。
    """
    events = []  # (year, text)
    for f in item.get("flist") or []:
        m = re.match(r"^(\d{4})年?$", str(f.get("name", "")).strip())
        if not m:
            continue
        events.append((int(m.group(1)), str(f.get("data") or "").strip()))

    dayun = chart.get("dayun") or []
    groups = []
    if not events or not dayun:
        return groups
    first, last = dayun[0], dayun[-1]

    def _group(status, label, dayun_info, evs):
        if evs:
            groups.append({
                "status": status,
                "label": label,
                "dayun": dayun_info,
                "events": [{"year": y, "event": t} for y, t in evs],
            })

    before = [(y, t) for y, t in events if y < first["start_year"]]
    _group("before", "未起运", None, before)
    for d in dayun:
        inside = [(y, t) for y, t in events if d["start_year"] <= y <= d["end_year"]]
        _group("dayun", None, {
            "ganzhi": d["ganzhi"],
            "shishen": d["shishen"],
            "start_year": d["start_year"],
            "end_year": d["end_year"],
        }, inside)
    after = [(y, t) for y, t in events if y > last["end_year"]]
    _group("after", "大运之后", None, after)
    return groups


def _plan_of(uid: str) -> str:
    """取用户 plan（会员过期由 member_dao 归零为 free）；注入缺失/异常一律按 free。"""
    if _member_dao is None:
        return "free"
    try:
        m = _member_dao.get_membership(uid) or {}
        return (m.get("plan") or "free") or "free"
    except Exception:
        logger.warning("mingren 会员查询异常 uid=%s", uid)
        return "free"


def is_full_user(uid: str) -> bool:
    """高级会员（pro/annual）→ 全量；体验模式全量免费；其余 35 条。"""
    if is_experience_mode():
        return True
    return _plan_of(uid) in _FULL_PLANS


def _gate(uid: str):
    """门控前置：会员服务缺失时 503（不误放行），同 zhuanxiang._require_vip。"""
    if is_experience_mode():
        return
    if _member_dao is None:
        raise HTTPException(status_code=503, detail="会员服务未就绪")


def _visible_names(uid: str) -> list:
    """当前用户可见人名列表（全量 or 前 35，按库顺序）。"""
    names = list(_load_data().keys())
    if is_full_user(uid):
        return names
    return names[:FREE_LIMIT]


def _summary(item: dict) -> dict:
    """列表条目（全量字段随行下发，前端自行截断展示）。"""
    return {
        "name": item.get("_name"),
        "info": item.get("info") or "",
        "info2": item.get("info2") or "",
        "has_info2": bool(item.get("info2") and item["info2"] != "-"),
        # 有出生数据即标「有命盘」（详情页才有命盘卡；无需排盘，纯字典查询）
        "has_chart": item.get("_name") in _load_birth(),
    }


@router.get("")
async def list_mingren(
    page: int = Query(1, ge=1, description="页码，从 1 起"),
    size: int = Query(20, ge=1, le=100, description="每页条数 1-100"),
    q: str = Query("", description="姓名搜索（子串匹配）"),
    uid: str = Depends(require_user),
):
    """命例列表：分页 + 姓名搜索；免费/基础用户仅返回前 35 条（is_full=false）。"""
    _gate(uid)
    data = _load_data()
    full = is_full_user(uid)

    # 先门控（可见名单）→ 再搜索 → 再分页：免费用户任何搜索都只在前 35 条内命中
    visible = _visible_names(uid)
    query = (q or "").strip()
    if query:
        matched_visible = [n for n in visible if query in n]
        matched_all = [n for n in data if query in n]
    else:
        matched_visible = visible
        matched_all = list(data.keys())

    total = len(matched_visible)
    start = (page - 1) * size
    items = []
    for name in matched_visible[start:start + size]:
        item = dict(data[name])
        item["_name"] = name
        items.append(_summary(item))

    return {
        "items": items,
        "page": page,
        "size": size,
        "total": total,             # 当前用户可见的匹配总数（≤ FREE_LIMIT 或全量）
        "total_all": len(matched_all),  # 未门控的全库匹配数（免费用户引导卡用）
        "library_total": len(data),     # 库总条数（2807）
        "is_full": full,                # 是否全量解锁
    }


@router.get("/{name}")
async def get_mingren_detail(
    name: str = FPath(..., description="人名"),
    uid: str = Depends(require_user),
):
    """命例详情：简介 + 命理评注（穷通宝鉴）+ 生平大事时间线。

    未解锁（免费/基础访问前 35 之外）→ 403 + VIP_REQUIRED（前端弹开通引导）。
    """
    _gate(uid)
    data = _load_data()
    if name not in data:
        raise HTTPException(status_code=404, detail="未找到该命例")
    if name not in _visible_names(uid):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "VIP_REQUIRED",
                "message": "该命例为高级会员专属内容，开通高级会员即可解锁全部 %d 位名人命例（免费版可试看前 %d 位）" % (len(data), FREE_LIMIT),
            },
        )
    item = data[name]
    resp = {
        "name": name,
        "info": item.get("info") or "",
        "info2": item.get("info2") or "",
        "source": item.get("source") or "",
        "has_info2": bool(item.get("info2") and item["info2"] != "-"),
        "flist": item.get("flist") or [],
        "has_chart": False,
    }
    # batch5 B5-3：有出生数据且引擎就绪 → 命盘卡 + 大运×大事对照（纯计算不落库；
    # chart 随详情接口既有门控——未解锁用户在此前已 403，不单独设第二道门控）
    chart = _chart_of(name)
    if chart:
        resp["has_chart"] = True
        resp["chart"] = chart
        resp["timeline"] = _build_timeline(item, chart)
        birth = _load_birth().get(name) or {}
        resp["birth_text"] = birth.get("birth") or ""
        _sources = birth.get("sources") or ([birth["source"]] if birth.get("source") else [])
        resp["birth_source"] = "；".join(str(s) for s in _sources) or ""
    return resp
