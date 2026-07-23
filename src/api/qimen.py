"""奇门遁甲 API — 时家转盘奇门排盘 + 用神分析.

提供 RESTful POST /api/qimen 接口，接收日期时间和问题，
返回完整九宫排盘和 AI 用神分析。
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["qimen"])

# ── Pydantic 模型 ─────────────────────────────────────────────────


class QimenRequest(BaseModel):
    """奇门遁甲请求"""
    year: int
    month: int
    day: int
    hour: int = 12
    minute: int = 0
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


# ── 全局依赖注入 ──────────────────────────────────────────────────

_qimen_engine = None
_retriever = None
_llm = None


def setup(qimen_engine, retriever=None, llm=None):
    """在主应用生命周期中注入引擎、检索器和 LLM 实例。"""
    global _qimen_engine, _retriever, _llm
    _qimen_engine = qimen_engine
    _retriever = retriever
    _llm = llm


# ── API 端点 ─────────────────────────────────────────────────────


@router.post("/api/qimen", response_model=QimenResponse)
async def qimen_analysis(req: QimenRequest):
    """奇门遁甲排盘分析。

    接收日期时间和问题，返回完整九宫排盘和 AI 用神分析。
    """
    if _qimen_engine is None:
        raise HTTPException(status_code=503, detail="Qimen service not ready")

    # 1. 排盘
    result = _qimen_engine.calculate(
        req.year, req.month, req.day,
        req.hour, req.minute, req.city,
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
    )
