"""合婚配对 API — 五行互补 + 生肖 + 日柱分析.

提供 RESTful POST /api/hehun 接口，接收双方八字信息，返回合婚分析结果。
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..security.auth import require_user
from ..services.narrative import NarrativeService
from ..api.birth_contract import normalize_gender, normalize_hour

router = APIRouter(tags=["hehun"])

# ── Pydantic 模型 ─────────────────────────────────────────────────

class BaziInput(BaseModel):
    """单人生辰信息（双契约兼容）。

    - 后端原生契约: year/month/day/hour(时钟小时)/minute/city/gender
    - 小程序契约（hehun.js）: birthYear/birthMonth/birthDay/birthHour(0-11
      时辰序号)/gender('male'|'female')/city —— 两者都填时小程序字段优先
    """
    year: Optional[int] = None
    month: Optional[int] = None
    day: Optional[int] = None
    hour: int = 0
    minute: int = 0
    city: str = "北京"
    gender: str = "男"
    # G5 能力开关（默认关=现行为零变化，旧请求零影响）：
    daylightSaving: bool = False   # 夏令时：1986-1991 区间内出生时间减 1 小时再排盘
    lateChildHour: bool = False    # 早晚子时专业档（问真 yzs=1）：23:00-24:00 日柱按当天
    # R2-4 真太阳时开关（2026-09-03 产品裁决反转默认：所有排盘统一默认开、
    # 前端开关允许用户关闭——paipan/hehun/union/zhuanxiang 同源 BaziInput
    # 一并生效；R2-1 曾默认关对齐问真 App 默认态）。字段 R2-1 已加
    # （镜像 daylightSaving/lateChildHour 模式，接口只增不减，本次仅翻默认值）：
    # True=按出生地经度+均时差修正后排盘（默认，产品口径）；
    # False=北京时间直排=问真默认态口径（用户关闭后一致性成立）。
    solarTime: bool = True         # 真太阳时：按出生地经度+均时差修正后再排盘
    # 小程序字段（别名）
    birthYear: Optional[int] = None
    birthMonth: Optional[int] = None
    birthDay: Optional[int] = None
    birthHour: Optional[int] = None
    # k19：birthHour 为钟表时钟小时（0-23）的显式声明——配合 birthMinute
    # 0-59；True 时 0-11 不再按时辰序号映射（10:55 / 10 点整场景）。旧调用
    # 方不传 → False → 0-11 时辰序号语义不变（零行为变化）。
    birthClock: bool = False


class HehunRequest(BaseModel):
    """合婚请求：双方生辰（person_a/person_b 原生契约，person1/person2 小程序契约）"""
    person_a: Optional[BaziInput] = None
    person_b: Optional[BaziInput] = None
    person1: Optional[BaziInput] = None
    person2: Optional[BaziInput] = None


class HehunResponse(BaseModel):
    """合婚匹配结果"""
    total_score: int
    score: int = 0  # 小程序 hehun.js 读 result.score（与 total_score 相同）
    wuxing: dict
    shengxiao: dict
    rizhu: dict
    advice: list[str]
    summary: str
    narrative: str = ""


# ── 全局依赖注入 ──────────────────────────────────────────────────

_hehun_engine = None
_bazi_engine = None
_narrative: Optional[NarrativeService] = None


def setup(hehun_engine, bazi_engine, narrative: NarrativeService = None):
    """在主应用生命周期中注入引擎实例。"""
    global _hehun_engine, _bazi_engine, _narrative
    _hehun_engine = hehun_engine
    _bazi_engine = bazi_engine
    _narrative = narrative


# ── API 端点 ─────────────────────────────────────────────────────

@router.post("/api/hehun", response_model=HehunResponse)
async def hehun_match(req: HehunRequest, uid: str = Depends(require_user)):
    """合婚匹配计算。

    安全修复：必须登录（生辰信息为敏感数据）。

    接收双方八字信息，引擎计算五行互补、生肖配对、日柱关系，
    返回综合评分和详细分析。
    """
    if _hehun_engine is None or _bazi_engine is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Hehun service not ready")

    a, b = _resolve_pair(req)

    # 排盘
    r1 = _bazi_engine.calculate(a.year, a.month, a.day, a.hour, a.minute, a.city, a.gender,
                                a.daylightSaving, a.lateChildHour, a.solarTime)
    r2 = _bazi_engine.calculate(b.year, b.month, b.day, b.hour, b.minute, b.city, b.gender,
                                b.daylightSaving, b.lateChildHour, b.solarTime)

    # 合婚匹配
    result = _hehun_engine.match(r1, r2)

    # 构建结构化结果字典
    result_dict = {
        "total_score": result.score,
        "wuxing": {
            "score": result.bazi_match.get("score", 0),
            "detail": result.bazi_match.get("complement_desc", ""),
            "complement": result.bazi_match.get("complement_details", []),
            "deficiency": "",
        },
        "shengxiao": {
            "type": result.shengxiao,
            "score": result.shengxiao_score,
            "relation": result.shengxiao_detail.get("relation", ""),
            "shengxiao_1": result.shengxiao_detail.get("shengxiao1", ""),
            "shengxiao_2": result.shengxiao_detail.get("shengxiao2", ""),
        },
        "rizhu": {
            "score": result.rizhu_score,
            "detail": result.rizhu,
            "rizhi_relation": result.rizhu_detail.get("ri_zhi_relation", ""),
            "rigan_relation": result.rizhu_detail.get("ri_gan_relation", ""),
        },
    }

    # LLM narrative
    narrative_text = ""
    if _narrative:
        try:
            narrative_text = _narrative.hehun(result_dict)
        except Exception:
            pass

    return HehunResponse(
        total_score=result_dict["total_score"],
        score=result_dict["total_score"],
        wuxing=result_dict["wuxing"],
        shengxiao=result_dict["shengxiao"],
        rizhu=result_dict["rizhu"],
        advice=result.advice.split('\n'),
        summary=f"综合评分：{result.score}/100。{result.bazi_match.get('complement_desc', '')}。{result.shengxiao}。{result.rizhu}。",
        narrative=narrative_text,
    )


# ── 契约解析辅助 ──────────────────────────────────────────────────

def _resolve_person(data: Optional[BaziInput]) -> BaziInput:
    """把小程序字段(birthYear...)或原生字段(year...)统一为引擎入参。

    - birthYear/birthMonth/birthDay 优先，缺省回落 year/month/day；
    - birthHour 为 0-11 时辰序号 → 时钟小时（normalize_hour 处理）；
    - gender 'male'/'female' → '男'/'女'。
    """
    if data is None:
        raise HTTPException(status_code=400, detail="缺少生辰信息（person_a/person_b 或 person1/person2）")
    year = data.birthYear if data.birthYear is not None else data.year
    month = data.birthMonth if data.birthMonth is not None else data.month
    day = data.birthDay if data.birthDay is not None else data.day
    if year is None or month is None or day is None:
        raise HTTPException(status_code=400, detail="出生年/月/日不能为空")
    year, month, day = int(year), int(month), int(day)
    # 越界生辰 → 400（此前漏检直通引擎，非法值触发排盘异常 → 500）
    if not (1900 <= year <= 2100):
        raise HTTPException(status_code=400, detail="出生年份须在 1900-2100 之间")
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="出生月份须在 1-12 之间")
    if not (1 <= day <= 31):
        raise HTTPException(status_code=400, detail="出生日期须在 1-31 之间")
    try:
        date(year, month, day)  # 兜底真实日历（如 2 月 30 日）→ 400
    except ValueError:
        raise HTTPException(status_code=400, detail="出生日期无效")
    hour = data.birthHour if data.birthHour is not None else data.hour
    return BaziInput(
        year=int(year), month=int(month), day=int(day),
        # k19：birthClock 显式声明 = 钟表时间 → 0-11 时钟小时直通（10:55）；
        # minute 恒为随附分钟、不参与 hour 判定（旧契约 idx+偏置 语义保留）
        hour=normalize_hour(hour, data.birthClock),
        minute=data.minute,
        city=data.city, gender=normalize_gender(data.gender),
        daylightSaving=data.daylightSaving, lateChildHour=data.lateChildHour,
        solarTime=data.solarTime,
    )


def _resolve_pair(req: HehunRequest):
    """解析双方生辰：小程序契约(person1/person2)与原生契约(person_a/person_b)
    二选一，混用或缺失返回 400。"""
    if (req.person1 is not None or req.person2 is not None):
        if req.person1 is None or req.person2 is None:
            raise HTTPException(status_code=400, detail="person1 与 person2 必须同时提供")
        return _resolve_person(req.person1), _resolve_person(req.person2)
    if req.person_a is None or req.person_b is None:
        raise HTTPException(status_code=400, detail="person_a 与 person_b 必须同时提供")
    return _resolve_person(req.person_a), _resolve_person(req.person_b)
