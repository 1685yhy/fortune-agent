"""专项论断 API（L2-5，问真 VIP 同款）：论财 / 论事业 / 论健康。

设计：
- POST /api/zhuanxiang {type: cai|shiye|jiankang, birth: {year,month,day,
  hour,minute,city,gender}} → 排盘 + 纯规则结构化论断（不调 LLM）。
  双契约兼容：birth 支持原生字段（year/...）与小程序的 birthYear/... 字段
  （同 hehun.py 的 BaziInput 口径）。
- 高级会员门控（复用 zeri.py/night.py 的会员判定模式）：
  - 体验模式（EXPERIENCE_MODE）→ 全免费；
  - 会员（plan != free）→ 放行；
  - 免费用户 → 403 + {"code": "VIP_REQUIRED", "message": ...}
    （前端据此弹开通会员引导，仿 ming/union 的付费墙文案风格）。
- 生辰只在请求内存中用于排盘，不落库（隐私红线，同 ming.py）。
"""
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.config import is_experience_mode
from src.security.auth import require_user
from src.api.birth_contract import normalize_gender, normalize_hour

logger = logging.getLogger(__name__)
router = APIRouter(tags=["zhuanxiang"])

TYPES = ("cai", "shiye", "jiankang")
TYPE_LABEL = {"cai": "论财", "shiye": "论事业", "jiankang": "论健康"}

# 依赖注入（仿 zeri.py setup 模式）：由主应用 lifespan 注入；测试注入 stub
_member_dao = None     # MemberDAO 引用
_bazi_engine = None    # BaziEngine 引用


def setup(member_dao=None, bazi_engine=None):
    """在主应用生命周期中注入依赖。"""
    global _member_dao, _bazi_engine
    _member_dao = member_dao
    _bazi_engine = bazi_engine


def _engine():
    """BaziEngine 引用：注入优先，缺失时懒加载。"""
    if _bazi_engine is not None:
        return _bazi_engine
    try:
        from src.engines.bazi import BaziEngine
        return BaziEngine()
    except Exception:
        return None


def is_member(uid: str) -> bool:
    """会员判定：plan != free 即会员；未注入/异常一律按非会员处理（仿 night.py/zeri.py）。"""
    if _member_dao is None:
        return False
    try:
        m = _member_dao.get_membership(uid) or {}
        return (m.get("plan") or "free") != "free"
    except Exception:
        return False


def _require_vip(uid: str):
    """高级会员门控（服务端强制，不信任客户端）：
    体验模式 / 会员放行；免费用户 403 + VIP_REQUIRED（前端弹开通引导）。"""
    if is_experience_mode():
        return
    if _member_dao is None:
        raise HTTPException(status_code=503, detail="会员服务未就绪")
    if is_member(uid):
        return
    raise HTTPException(
        status_code=403,
        detail={
            "code": "VIP_REQUIRED",
            "message": "论财/论事业/论健康为高级会员专属论断，开通会员即可解锁全部分析（当前免费版仅基础排盘）",
        },
    )


# ── 请求模型（双契约兼容：原生 + 小程序字段，同 hehun.py）─────────

class BirthInput(BaseModel):
    """单人生辰信息（双契约兼容）。

    - 后端原生契约: year/month/day/hour(时钟小时)/minute/city/gender
    - 小程序契约: birthYear/birthMonth/birthDay/birthHour(0-11 时辰序号)
      /gender('male'|'female')/city —— 两者都填时小程序字段优先
    """
    year: Optional[int] = None
    month: Optional[int] = None
    day: Optional[int] = None
    hour: int = 0
    minute: int = 0
    city: str = "北京"
    gender: str = "男"
    birthYear: Optional[int] = None
    birthMonth: Optional[int] = None
    birthDay: Optional[int] = None
    birthHour: Optional[int] = None


class ZhuanxiangRequest(BaseModel):
    type: str
    birth: BirthInput


def _resolve_birth(birth: BirthInput) -> BirthInput:
    """把小程序字段(birthYear...)或原生字段(year...)统一为引擎入参
    （同 hehun.py 的校验口径：越界/伪日期 → 400）。"""
    year = birth.birthYear if birth.birthYear is not None else birth.year
    month = birth.birthMonth if birth.birthMonth is not None else birth.month
    day = birth.birthDay if birth.birthDay is not None else birth.day
    if year is None or month is None or day is None:
        raise HTTPException(status_code=400, detail="出生年/月/日不能为空")
    year, month, day = int(year), int(month), int(day)
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
    hour = birth.birthHour if birth.birthHour is not None else birth.hour
    return BirthInput(
        year=year, month=month, day=day,
        # k19：minute 恒为随附分钟、不参与 hour 判定（旧契约 idx+偏置保留；
        # 本接口暂无钟表档调用方 → 不传 clock_signal）
        hour=normalize_hour(hour), minute=birth.minute,
        city=birth.city, gender=normalize_gender(birth.gender),
    )


# ── 端点 ─────────────────────────────────────────────────────────

@router.post("/api/zhuanxiang")
def zhuanxiang(req: ZhuanxiangRequest, uid: str = Depends(require_user)):
    """专项论断：论财 / 论事业 / 论健康（高级会员专属，规则引擎生成）。

    先门控后排盘（非会员直接 403，不白烧排盘）——付费墙语义同 union.py
    _require_paid；论断为纯规则确定性输出（LLM 润色为后续可选增强）。
    """
    ztype = (req.type or "").strip()
    if ztype not in TYPES:
        raise HTTPException(status_code=400, detail="type 须为 cai / shiye / jiankang")
    _require_vip(uid)

    eng = _engine()
    if eng is None:
        raise HTTPException(status_code=503, detail="排盘服务未就绪")
    b = _resolve_birth(req.birth)
    try:
        result = eng.calculate(b.year, b.month, b.day, b.hour, b.minute, b.city, b.gender)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="排盘失败：%s" % str(e))
    except Exception as e:
        logger.exception("专项论断排盘失败 uid=%s type=%s", uid, ztype)
        raise HTTPException(status_code=500, detail="排盘服务异常，请稍后再试")

    if ztype == "cai":
        from src.engines.zhuanxiang import lun_cai
        analysis = lun_cai(result)
    elif ztype == "shiye":
        from src.engines.zhuanxiang import lun_shiye
        analysis = lun_shiye(result)
    else:
        from src.engines.zhuanxiang import lun_jiankang
        analysis = lun_jiankang(result)

    return {
        "type": ztype,
        "type_label": TYPE_LABEL[ztype],
        "bazi": result.bazi,
        "day_master": result.day_master,
        "result": analysis,
    }
