"""AI 取名 API —— 免费 5 名 + 五维评分 + 深度报告(付费) + 名笺收藏。

设计(方案-AI取名.md,权威):
- POST /api/ming/generate {surname, gender, style_chips, custom_expectation, mode, 生辰选填}
  → 免费 5 名: 前 3 名完整(综合分+五维+点评+出处(RAG 有则示)+五行补益标签),
    第 4、5 名只给分数(locked,不露推理——解锁线)
- POST /api/ming/report {surname, gender, given, mode, current_name, 生辰}
  → 付费深度报告(契合度矩阵/改名对比/备选 15/名笺落款); 两档定价(方案权威):
    宝宝版 ming_report ¥19.9(契合度+备选 15), 成人版 ming_report_pro ¥29.9(含改名对比);
    边界服务端强制(_require_paid, EXPERIENCE_MODE 全免费, 会员免费, pro 档覆盖宝宝档)
- 成人免费现名诊断: 免费生成(成人场景)响应带 current_name_issues(五格/五行/读音 简评,≤5 条);
  完整改名对比只在付费报告 rename_compare
- POST /api/ming/save + GET /api/ming/saved: 名笺收藏(轻量表,生辰零落库)
- 免费生成日额度 3 次(ming_quota; 会员/体验模式不限; 防爬防刷)

隐私红线:
- 生辰只在请求内存中用于排盘,不落库;收藏/归档只写脱敏摘要。
- 出处只来自 RAG 命中,绝不编造。
"""
import logging
import os
import random
import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.config import is_experience_mode
from src.security.admin import is_admin_user  # k37 S1：超管豁免判据（单一事实源）
from src.security.auth import require_user
from src.api.birth_contract import normalize_gender

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ming"])

# 免费档每日生成次数(方案: 登录后 3 次/日; 环境变量可调,测试用)
FREE_DAILY_LIMIT = int(os.getenv("MING_DAILY_LIMIT", "3"))
_CJK = re.compile(r"^[一-龥]+$")

# 依赖注入(仿 union.py): 由主应用 lifespan 注入; 测试可替换
_llm_ref = None        # FortuneLLM(提供 api_key; 无则规则兜底)
_retriever = None      # 古籍 RAG(276 万块; 无则不出处)
_member_dao = None     # 支付/会员
_dao = None            # 咨询归档(可选)
_bazi_engine = None    # 排盘引擎
_ming_dao = None


def setup(llm=None, retriever=None, member_dao=None, dao=None,
          bazi_engine=None, ming_dao=None):
    """在主应用生命周期中注入依赖。"""
    global _llm_ref, _retriever, _member_dao, _dao, _bazi_engine, _ming_dao
    _llm_ref = llm
    _retriever = retriever
    _member_dao = member_dao
    _dao = dao
    _bazi_engine = bazi_engine
    _ming_dao = ming_dao


def _bazi() -> Optional[object]:
    if _bazi_engine is not None:
        return _bazi_engine
    try:
        from src.engines.bazi import BaziEngine
        return BaziEngine()
    except Exception:
        return None


def _mdao() -> object:
    global _ming_dao
    if _ming_dao is None:
        from src.storage.dao import get_conn
        from src.storage.ming_dao import MingDAO
        _ming_dao = MingDAO(get_conn())
    return _ming_dao


def _api_key() -> str:
    if _llm_ref is not None and getattr(_llm_ref, "api_key", ""):
        return _llm_ref.api_key
    return os.getenv("DEEPSEEK_API_KEY") or os.getenv("ANTHROPIC_API_KEY") or ""


def _bj_day() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


# 降级模式 rng 种子去重: 同日多次生成(含重新生成)必须结果不同。
# 种子 = uid:day:当日已用次数(配额计数,持久) : 进程内递增序号(覆盖体验/会员等不耗配额的路径)。
_gen_seq: dict = {}


def _daily_seed(uid: str, salt: str = "") -> str:
    """构造当日生成种子: 混入当日已用次数与递增序号, 同日同名请求结果不同。"""
    day = _bj_day()
    key = f"{uid}:{day}"
    n = _gen_seq.get(key, 0) + 1
    _gen_seq[key] = n
    used = ""
    try:
        row = _mdao().conn.execute(
            "SELECT cnt FROM ming_quota WHERE user_id=? AND day=?", (uid, day)).fetchone()
        if row:
            used = f":q{row[0]}"
    except Exception:
        pass
    return f"{key}:{n}{used}" + (f":{salt}" if salt else "")


def _require_paid(uid: str, tier: str = "ming_report"):
    """付费边界(服务端强制,不信任客户端):
    - 体验模式(EXPERIENCE_MODE) → 全免费
    - 两档定价: 宝宝档 ming_report(¥19.9, 契合度+备选 15) /
      成人档 ming_report_pro(¥29.9, 含改名对比); pro 档覆盖宝宝档, 反之不覆盖
      (宝宝档兼容 deep_report 旧通道)
    - 会员(plan != free) → 深度报告免费
    - 其余 → 403(提示购买对应档位)
    """
    if is_experience_mode():
        return
    if _member_dao is None:
        raise HTTPException(status_code=503, detail="支付服务未就绪")
    allowed = (["ming_report_pro"] if tier == "ming_report_pro"
               else ["ming_report", "ming_report_pro", "deep_report"])
    try:
        for pid in allowed:
            if _member_dao.get_user_purchase(uid, pid) is not None:
                return
        m = _member_dao.get_membership(uid) or {}
        if (m.get("plan") or "free") != "free":
            return
    except Exception as exc:
        logger.warning("ming 付费校验异常: %s", exc)
        raise HTTPException(status_code=503, detail="支付校验失败,请稍后再试")
    if tier == "ming_report_pro":
        raise HTTPException(
            status_code=403,
            detail="成人改名报告需解锁专属档（¥29.9，ming_report_pro 通道，含改名对比），会员深度报告免费")
    raise HTTPException(
        status_code=403,
        detail="请先解锁名笺深度报告（¥19.9，ming_report 通道），会员深度报告免费")


def _check_quota(uid: str):
    """免费生成日额度(3 次/日); 体验模式/已购/会员/超管(ADMIN_IDS)不限。"""
    if is_experience_mode():
        return
    if _member_dao is not None:
        try:
            for pid in ("ming_report", "ming_report_pro"):
                if _member_dao.get_user_purchase(uid, pid) is not None:
                    return
            m = _member_dao.get_membership(uid) or {}
            if (m.get("plan") or "free") != "free":
                return
        except Exception:
            pass
    try:
        remaining = _mdao().consume_quota(uid, _bj_day(), FREE_DAILY_LIMIT)
        # k37 S1：超管豁免（ADMIN_IDS 白名单，判据单一事实源 src/security/admin.py）。
        # 与 k36 在 handler._check_quota 的口径一致：豁免只加在「额度门」这一处
        # ——超过上限也不 429；上行 consume_quota 仍照常计数（不额外分支）。
        if remaining < 0 and not is_admin_user(uid):
            raise HTTPException(
                status_code=429,
                detail=f"今日免费生成次数已用完（{FREE_DAILY_LIMIT} 次/日），明日再来，或解锁深度报告不限次")
    except HTTPException:
        raise
    except Exception:
        pass  # 额度表不可用不阻断(宁缺毋滥原则的反向: 不因额度故障拒绝服务)


# ── 请求模型(双契约兼容: 原生 + 小程序字段) ─────────────────────

class MingRequest(BaseModel):
    surname: str = Field(min_length=1, max_length=4)
    gender: str = "男"
    style_chips: List[str] = Field(default_factory=list)
    custom_expectation: str = Field("", max_length=60)
    mode: str = "baby"            # baby 宝宝取名 | adult 成人改名
    current_name: str = Field("", max_length=8)   # 成人改名现名
    # 生辰(选填; 填了八字契合更准)
    birthYear: Optional[int] = None
    birthMonth: Optional[int] = None
    birthDay: Optional[int] = None
    birthHour: Optional[int] = None
    year: Optional[int] = None
    month: Optional[int] = None
    day: Optional[int] = None
    hour: Optional[int] = None
    # 免费额度豁免(供已购用户跳过, 服务端二次确认, 客户端不可信)
    paid: bool = False


class MingReportRequest(MingRequest):
    given: str = Field(min_length=1, max_length=2)


class MingSaveRequest(BaseModel):
    surname: str = Field(min_length=1, max_length=4)
    given: str = Field(min_length=1, max_length=2)
    gender: str = "男"
    score: int = Field(0, ge=0, le=100)
    style_note: str = Field("", max_length=30)


class MingDeleteRequest(BaseModel):
    surname: str = Field(min_length=1, max_length=4)
    given: str = Field(min_length=1, max_length=2)


def _validate_surname(surname: str) -> str:
    surname = surname.strip()
    if not surname or not _CJK.match(surname):
        raise HTTPException(status_code=400, detail="姓氏需为 1-4 个汉字")
    return surname


def _resolve_birth(req) -> Optional[object]:
    """生辰解析(原生 year/month/day 与小字生 birthYear 双契约)。缺省按子时兜底。"""
    y = req.year if req.year is not None else req.birthYear
    mth = req.month if req.month is not None else req.birthMonth
    d = req.day if req.day is not None else req.birthDay
    h = req.hour if req.hour is not None else req.birthHour
    present = [v is not None for v in (y, mth, d)]
    if not any(present):
        return None
    if not all(present):
        raise HTTPException(status_code=400, detail="出生日期需完整填写（年月日）")
    if not (1900 <= y <= 2100 and 1 <= mth <= 12 and 1 <= d <= 31):
        raise HTTPException(status_code=400, detail="出生日期不合法")
    if h is None:
        h = 0  # 时辰选填, 兜底子时并注明
    elif h < 0 or h > 23:
        raise HTTPException(status_code=400, detail="出生时辰不合法（0-23 点）")
    engine = _bazi()
    if engine is None:
        return None
    try:
        result = engine.calculate(y, mth, d, h, 0, "北京", normalize_gender(req.gender))
        result._hour_note = "未填时辰，按子时计，仅供五行参考" if (
            req.hour is None and req.birthHour is None) else ""
        return result
    except Exception as exc:
        logger.warning("ming 排盘失败(%s), 无八字维度", exc)
        return None


def _style_note(req: MingRequest) -> str:
    if req.custom_expectation.strip():
        return req.custom_expectation.strip()
    if req.style_chips:
        return "、".join(req.style_chips)
    return "随心"


def _item_for(surname: str, given: str, gender: str, styles: List[str],
              yongshen: str, counts: Optional[dict], ping: str,
              retriever) -> dict:
    """组装单名卡片(完整字段)。截断由调用方在排序后按名次执行。"""
    from src.engines.ming import score_name, lookup_source
    scored = score_name(surname, given, gender, styles, yongshen, counts)
    item = {
        "given": given,
        "full": surname + given,
        "score": scored["total"],
        "level": scored["level"],
        "dims": scored["dims"],
        "ping": ping,
        "buyi": scored["buyi"],
        "locked": False,
    }
    src = lookup_source(retriever, given)
    if src:
        item["src"] = src
    return item


@router.post("/api/ming/generate")
async def ming_generate(req: MingRequest, uid: str = Depends(require_user)):
    """免费 5 名生成 + 五维评分。"""
    from src.engines.ming import generate_names, STYLE_CHIPS
    surname = _validate_surname(req.surname)
    gender = normalize_gender(req.gender)
    styles = [s for s in req.style_chips if s in STYLE_CHIPS]
    mode = "adult" if req.mode == "adult" else "baby"
    if mode == "adult" and not req.current_name.strip():
        raise HTTPException(status_code=400, detail="成人改名需填写现名")
    if req.current_name and not _CJK.match(req.current_name):
        raise HTTPException(status_code=400, detail="现名需为汉字")

    _check_quota(uid)

    bazi_result = _resolve_birth(req)
    yongshen = (bazi_result.yongshen if bazi_result else "") or ""
    counts = bazi_result.wuxing if bazi_result else None

    wuxing_text = ""
    if yongshen:
        from src.engines.ming import parse_yongshen
        _use, helpful = parse_yongshen(yongshen)
        wuxing_text = (f"八字五行分布: {counts}; 用神为{_use},"
                       f"名字宜多用{'、'.join(helpful)}之五行,避与过剩五行同气")
    elif counts:
        wuxing_text = f"八字五行分布: {counts}; 名字五行宜均衡,可补缺失五行"
    else:
        wuxing_text = "名字五行宜均衡,无特别偏好"

    gender_abbr = "m" if gender == "男" else "f"
    names = generate_names(
        surname, gender_abbr, styles, req.custom_expectation.strip(),
        wuxing_text, api_key=_api_key(),
        rng=random.Random(_daily_seed(uid)),
        gender=gender, yongshen=yongshen, wuxing_counts=counts)

    if len(names) < 5:
        raise HTTPException(status_code=503, detail="生成失败，请稍后重试")

    items = [_item_for(surname, n["given"], gender, styles, yongshen, counts,
                       n.get("ping", ""), _retriever)
             for n in names[:5]]
    # 名次排序: 综合分 > 五行契合度 > 数理吉凶(方案第五节)
    items.sort(key=lambda it: (-it["score"], -it["dims"]["五行"], -it["dims"]["数理"]))
    # 解锁线: 第 4、5 名只给分数,不露深度解析
    for i, it in enumerate(items):
        it["locked"] = i >= 3
        if it["locked"]:
            it.pop("ping", None)
            it.pop("buyi", None)
            it.pop("src", None)

    resp = {
        "names": items,
        "style_note": _style_note(req),
        "mode": mode,
        "gender": gender,
        "quota_remaining": None,  # 前端不需要
    }
    # 成人免费现名诊断(方案免费档): 现名问题清单,规则生成,≤5 条;
    # 完整改名对比只在付费报告 rename_compare 中
    if mode == "adult":
        from src.engines.ming import diagnose_current_name
        resp["current_name_issues"] = diagnose_current_name(
            surname, req.current_name.strip(), gender, yongshen, counts)
    return resp


@router.post("/api/ming/report")
async def ming_report(req: MingReportRequest, uid: str = Depends(require_user)):
    """付费深度报告(契合度矩阵 / 改名对比 / 备选 15 / 名笺落款)。"""
    surname = _validate_surname(req.surname)
    given = req.given.strip()
    if not given or not _CJK.match(given) or len(given) > 2:
        raise HTTPException(status_code=400, detail="名字需为 1-2 个汉字")
    gender = normalize_gender(req.gender)
    mode = "adult" if req.mode == "adult" else "baby"
    if mode == "adult" and not req.current_name.strip():
        raise HTTPException(status_code=400, detail="成人改名需填写现名")
    current_name = req.current_name.strip() if mode == "adult" else ""
    if current_name and not _CJK.match(current_name):
        raise HTTPException(status_code=400, detail="现名需为汉字")
    # 两档定价: 宝宝→ming_report(¥19.9); 成人→ming_report_pro(¥29.9, 含改名对比)
    _require_paid(uid, "ming_report_pro" if mode == "adult" else "ming_report")

    from src.engines.ming import STYLE_CHIPS, build_report
    bazi_result = _resolve_birth(req)
    report = build_report(
        surname, given, gender, mode, current_name,
        [s for s in req.style_chips if s in STYLE_CHIPS],
        req.custom_expectation.strip(), bazi_result=bazi_result,
        retriever=_retriever,
        rng=random.Random(_daily_seed(uid, salt=f"report:{given}")),
        api_key=_api_key())

    # 归档(隐私红线: 只写脱敏摘要,不含生辰)
    if _dao is not None:
        try:
            _dao.save_consultation(
                uid,
                f"AI取名：{surname}{given} 综合{report['name_analysis']['total']}分",
                chart_result={"type": "ming", "name": surname + given,
                              "score": report["name_analysis"]["total"],
                              "mode": mode},
                analysis=f"AI取名深度报告：{surname}{given}（脱敏摘要，无生辰）",
                intent="ming")
        except Exception as exc:
            logger.warning("ming 报告归档失败: %s", exc)

    return {"report": report}


@router.post("/api/ming/save")
async def ming_save(req: MingSaveRequest, uid: str = Depends(require_user)):
    """收藏名笺(幂等, 已收藏提示不报错)。"""
    surname = _validate_surname(req.surname)
    given = req.given.strip()
    if not given or not _CJK.match(given) or len(given) > 2:
        raise HTTPException(status_code=400, detail="名字需为 1-2 个汉字")
    saved, already = _mdao().save(
        uid, surname, given, normalize_gender(req.gender), req.score,
        req.style_note.strip())
    return {"saved": saved, "already": already}


@router.get("/api/ming/saved")
async def ming_saved(uid: str = Depends(require_user)):
    """已收藏名笺列表(最新在前)。"""
    return {"items": _mdao().list_saved(uid)}


@router.delete("/api/ming/delete")
async def ming_delete(req: MingDeleteRequest, uid: str = Depends(require_user)):
    """取消收藏名笺(幂等: 不存在返回 deleted=False, 不报错)。"""
    surname = _validate_surname(req.surname)
    given = req.given.strip()
    if not given or not _CJK.match(given) or len(given) > 2:
        raise HTTPException(status_code=400, detail="名字需为 1-2 个汉字")
    deleted = _mdao().delete_saved(uid, surname, given)
    return {"deleted": deleted}
