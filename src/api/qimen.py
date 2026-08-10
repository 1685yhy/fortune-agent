"""奇门遁甲 API — 时家转盘奇门排盘 + 用神分析.

提供 RESTful POST /api/qimen 接口，接收日期时间和问题，
返回完整九宫排盘和 AI 用神分析。
"""

from typing import Optional

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..security.auth import require_user
from ..services.narrative import NarrativeService
from ..api.birth_contract import parse_birth_str

router = APIRouter(tags=["qimen"])

# ── Pydantic 模型 ─────────────────────────────────────────────────


class QimenRequest(BaseModel):
    """奇门遁甲请求（双契约兼容）。

    - 后端原生契约: year/month/day/hour/minute/city/question
    - 小程序契约（qimen.js）: date('YYYY-MM-DD')/time('HH:MM')/city/question
      —— date/time 提供时优先于 year/month/day/hour/minute
    """
    year: Optional[int] = None
    month: Optional[int] = None
    day: Optional[int] = None
    hour: int = 12
    minute: int = 0
    date: Optional[str] = None
    time: Optional[str] = None
    city: str = "北京"
    question: str = ""


class PalaceInfo(BaseModel):
    """单宫位信息"""
    palace: str
    bashen: str
    jiuxing: str
    bamen: str
    tianpan: str
    dipan: str


class QimenResponse(BaseModel):
    """奇门排盘响应"""
    dun_type: str
    ju_number: int
    zhifu_star: str
    zhishi_door: str
    solar_term: str
    yuan: str
    bazi: str
    palaces: list[PalaceInfo]
    analysis: str = ""
    narrative: str = ""


# ── 全局依赖注入 ──────────────────────────────────────────────────

_qimen_engine = None
_retriever = None
_llm = None
_narrative: Optional[NarrativeService] = None


def setup(qimen_engine, retriever=None, llm=None, narrative: NarrativeService = None):
    """在主应用生命周期中注入引擎、检索器和 LLM 实例。"""
    global _qimen_engine, _retriever, _llm, _narrative
    _qimen_engine = qimen_engine
    _retriever = retriever
    _llm = llm
    _narrative = narrative


# ── API 端点 ─────────────────────────────────────────────────────


@router.post("/api/qimen", response_model=QimenResponse)
async def qimen_analysis(req: QimenRequest, uid: str = Depends(require_user)):
    """奇门遁甲排盘分析。

    接收日期时间和问题，返回完整九宫排盘和 AI 用神分析。
    安全修复：必须登录（生辰/问题为敏感数据）。
    """
    if _qimen_engine is None:
        raise HTTPException(status_code=503, detail="Qimen service not ready")

    year, month, day, hour, minute = _resolve_datetime(req)

    # 1. 排盘
    result = _qimen_engine.calculate(
        year, month, day,
        hour, minute, req.city,
    )

    # 2. 构建宫位信息 (洛书九宫顺序)
    lo_shu_order = ["巽", "离", "坤", "震", "中", "兑", "艮", "坎", "乾"]
    palaces = []
    for name in lo_shu_order:
        palaces.append(PalaceInfo(
            palace=f"{name}宫",
            bashen=result.bashen.get(name, ""),
            jiuxing=result.jiuxing.get(name, ""),
            bamen=result.bamen.get(name, ""),
            tianpan=result.tianpan.get(name, ""),
            dipan=result.dipan.get(name, ""),
        ))

    # 3. 格式化命盘字符串用于 LLM 分析
    chart_str = _qimen_engine.print_chart(result)
    bazi_raw = result.raw_data.get("bazi", [])

    # 4. RAG 检索 + LLM 分析
    analysis = ""
    if _llm:
        try:
            refs = []
            if _retriever:
                search_query = f"奇门遁甲 {req.question}" if req.question else "奇门遁甲运筹"
                refs = _retriever.search(search_query, category="qimen", top_k=15)
            llm_result = _llm.analyze(chart_str, refs, req.question or "奇门遁甲运筹")
            analysis = llm_result.response
        except Exception as e:
            analysis = f"AI 分析暂时不可用：{str(e)[:100]}"

    # 5. LLM narrative layer
    narrative_text = ""
    if _narrative:
        try:
            chart_dict = {
                "dun_type": result.dun_type,
                "ju_number": result.ju_number,
                "zhifu_star": result.zhifu_star,
                "zhishi_door": result.zhishi_door,
                "solar_term": result.raw_data.get("solar_term", ""),
                "yuan": result.raw_data.get("yuan", ""),
                "bazi": " ".join(bazi_raw) if isinstance(bazi_raw, list) else str(bazi_raw),
                "palaces": [{
                    "palace": f"{name}宫",
                    "bashen": result.bashen.get(name, ""),
                    "jiuxing": result.jiuxing.get(name, ""),
                    "bamen": result.bamen.get(name, ""),
                    "tianpan": result.tianpan.get(name, ""),
                    "dipan": result.dipan.get(name, ""),
                } for name in lo_shu_order],
            }
            narrative_text = _narrative.qimen(chart_dict, req.question or "奇门遁甲运筹")
        except Exception:
            pass

    return QimenResponse(
        dun_type=result.dun_type,
        ju_number=result.ju_number,
        zhifu_star=result.zhifu_star,
        zhishi_door=result.zhishi_door,
        solar_term=result.raw_data.get("solar_term", ""),
        yuan=result.raw_data.get("yuan", ""),
        bazi=" ".join(bazi_raw) if isinstance(bazi_raw, list) else str(bazi_raw),
        palaces=palaces,
        analysis=analysis,
        narrative=narrative_text,
    )


# ── 契约解析辅助 ──────────────────────────────────────────────────

def _resolve_datetime(req: QimenRequest):
    """解析起局时间：小程序契约(date/time 字符串)优先，原生字段回落。"""
    year, month, day, hour, minute = req.year, req.month, req.day, req.hour, req.minute
    if req.date:
        parsed = parse_birth_str(req.date)
        if parsed is None:
            raise HTTPException(status_code=400, detail="date 格式应为 YYYY-MM-DD")
        year, month, day = parsed
    if req.time:
        m = re.match(r"^(\d{1,2}):(\d{2})$", str(req.time).strip())
        if not m:
            raise HTTPException(status_code=400, detail="time 格式应为 HH:MM")
        hour, minute = int(m.group(1)), int(m.group(2))
    if year is None or month is None or day is None:
        raise HTTPException(status_code=400, detail="year/month/day 或 date 必填")
    if not (1 <= month <= 12 and 1 <= day <= 31 and 0 <= hour <= 23 and 0 <= minute <= 59):
        raise HTTPException(status_code=400, detail="日期时间数值越界")
    return year, month, day, hour, minute
