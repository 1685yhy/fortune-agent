"""多盘对比 API（P1-2）：POST /api/duipan — 同一生日不同时辰两盘对比。

契约：
    body {birthYear, birthMonth, birthDay, city, gender, hourA, hourB}
    （兼容原生 year/month/day；hourA/hourB 为时辰序号 0-11（子..亥，子=晚子时 23 点）
     或时钟小时 12-23）
    → {pan_a: 排盘全字段, pan_b: 排盘全字段, diff: 差异对比, summary: 规则摘要}

diff 结构（确定性规则，0 LLM）：
    hours / four_pillars(changed+changes) / day_master(same+note[晚子时说明]) /
    wuxing(counts 差 + strength) / yongshen(core_same) / geju /
    dayun(起运岁数 start_same/a_start/b_start + 序列 sequence_same) /
    shensha(added/removed) / same

隐私红线：生辰只用于内存排盘（duipan.compare_pans），不落库、不入日志、不写
DAO —— 与排盘/合婚接口同口径。全接口 require_user 鉴权。
错误：生辰缺失/越界/伪日期（如 2 月 30 日）→ 400；hourA/hourB 缺失或不在 0-23 →
400；未登录 → 401。
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..security.auth import require_user
from ..engines.duipan import compare_pans
from ..api.birth_contract import normalize_gender

router = APIRouter(tags=["duipan"])


# ── Pydantic 模型 ─────────────────────────────────────────────

class DuipanRequest(BaseModel):
    """多盘对比请求（双契约兼容：birthYear/birthMonth/birthDay 优先于 year/month/day）。

    - hourA/hourB：0-11 视为时辰序号（子..亥，子=晚子时 23 点），12-23 视为时钟小时
      （与排盘接口 normalize_hour 契约一致）
    """
    year: Optional[int] = None
    month: Optional[int] = None
    day: Optional[int] = None
    city: str = "北京"
    gender: str = "男"
    # 小程序字段（别名）
    birthYear: Optional[int] = None
    birthMonth: Optional[int] = None
    birthDay: Optional[int] = None
    hourA: Optional[int] = None
    hourB: Optional[int] = None


# ── 全局依赖注入 ──────────────────────────────────────────────

_engine = None


def setup(engine):
    """主应用生命周期注入 BaziEngine（同 paipan 模式）。"""
    global _engine
    _engine = engine


# ── API 端点 ──────────────────────────────────────────────────

@router.post("/api/duipan")
async def duipan(req: DuipanRequest, uid: str = Depends(require_user)):
    """多盘对比：同一生日不同时辰排两盘 → 差异对比 + 规则摘要。

    - 401：未登录（require_user 鉴权红线）
    - 400：生辰缺失 / 越界 / 伪日期；hourA/hourB 缺失或不在 0-23
    - 503：引擎未注入（服务未就绪）
    """
    if _engine is None:
        raise HTTPException(status_code=503, detail="Duipan service not ready")
    birth, hour_a, hour_b, city, gender = _resolve(req)
    return compare_pans(birth, hour_a, hour_b, city, gender, engine=_engine)


def _resolve(req: DuipanRequest):
    """解析并校验生辰与两时辰（复用排盘接口的 400 校验口径）。

    返回 (birth, hour_a, hour_b, city, gender)：birth 为 (年, 月, 日)，
    hour_a/hour_b 为原始输入（0-11 时辰序号 / 12-23 时钟小时，normalize_hour 在
    引擎层统一处理），gender 已归一化为 男/女。
    """
    year = req.birthYear if req.birthYear is not None else req.year
    month = req.birthMonth if req.birthMonth is not None else req.month
    day = req.birthDay if req.birthDay is not None else req.day
    if year is None or month is None or day is None:
        raise HTTPException(status_code=400, detail="出生年/月/日不能为空")
    year, month, day = int(year), int(month), int(day)
    # 越界生辰 → 400（与排盘接口同口径，非法值直通引擎会触发排盘异常 → 500）
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
    hour_a, hour_b = req.hourA, req.hourB
    for name, h in (("hourA", hour_a), ("hourB", hour_b)):
        if h is None:
            raise HTTPException(
                status_code=400, detail="%s 不能为空（时辰序号 0-11 或时钟小时 12-23）" % name)
        if isinstance(h, bool) or not isinstance(h, int) or not (0 <= h <= 23):
            raise HTTPException(
                status_code=400, detail="%s 须为 0-23 的整数（0-11 时辰序号 / 12-23 时钟小时）" % name)
    return (year, month, day), hour_a, hour_b, req.city, normalize_gender(req.gender)
