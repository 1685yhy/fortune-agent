"""双人合盘聚合 API — POST /api/union（免费档）。

免费档（paid=false）：契合分/等级/三维得分条/一句话缘语/缘笺脱敏数据。
纯规则+模板管线，LLM 仅作可弃的缘语润色（2s 超时，失败即模板兜底）。
付费档（paid=true）由 Task 5 接入：深度报告四章（deep_report 支付校验+归档）。

隐私红线：
- 双方生辰只在本请求内存中使用（transient），免费档全程零落库；
- 缘笺数据/缘语一律脱敏（不含时辰/出生地/姓名）。
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.security.auth import require_user
from src.engines.hehun import HehunEngine
from src.engines.bazi import BaziEngine
from src.engines.union import run_union
from src.engines.yuan_quote import generate_yuan_quote
from .hehun import BaziInput, _resolve_pair
from .compatibility import _compute_match_score

logger = logging.getLogger(__name__)

router = APIRouter(tags=["union"])

_hehun_engine: Optional[HehunEngine] = None
_bazi_engine: Optional[BaziEngine] = None
_llm_ref = None
_retriever = None
_member_dao = None
_dao = None


def setup(hehun_engine, bazi_engine, llm=None, retriever=None, member_dao=None, dao=None):
    """在主应用生命周期中注入引擎/LLM/检索器/支付/归档依赖。"""
    global _hehun_engine, _bazi_engine, _llm_ref, _retriever, _member_dao, _dao
    _hehun_engine = hehun_engine
    _bazi_engine = bazi_engine
    _llm_ref = llm
    _retriever = retriever
    _member_dao = member_dao
    _dao = dao


class UnionRequest(BaseModel):
    """双人合盘请求：双契约（person_a/b 原生 + person1/2 小程序）+ 关系标签 + 付费标记。"""
    person_a: Optional[BaziInput] = None
    person_b: Optional[BaziInput] = None
    person1: Optional[BaziInput] = None
    person2: Optional[BaziInput] = None
    relation: str = ""   # 恋人/暧昧/夫妻/朋友/暗恋（空=不标注）
    paid: bool = False


def _make_polish_fn():
    """缘语润色：LLM 超时 2s；无 LLM/异常 → 调用方模板兜底（免费档 2 秒内保证）。"""
    if _llm_ref is None or not getattr(_llm_ref, "api_key", ""):
        return None
    def polish(text: str) -> str:
        r = _llm_ref._call_deepseek_model(
            f"把以下缘语润色为一句不超过24个字的中文缘语，只输出润色后的句子：{text}",
            _llm_ref.model, max_tokens=60, timeout=2.0)
        return (r.response or "").strip()
    return polish


@router.post("/api/union")
async def union_match(req: UnionRequest, uid: str = Depends(require_user)):
    """合盘聚合：免费档即时返回；付费档见 Task 5（_require_paid + 报告生成 + 归档）。"""
    if _hehun_engine is None or _bazi_engine is None:
        raise HTTPException(status_code=503, detail="Union service not ready")

    # 双契约解析（与 /api/hehun 相同校验，缺任一方 400）
    a, b = _resolve_pair(req)

    # 双方真实排盘（时辰/出生地缺失由契约层归一为默认+结果标注，不阻断）
    r1 = _bazi_engine.calculate(a.year, a.month, a.day, a.hour, a.minute, a.city, a.gender)
    r2 = _bazi_engine.calculate(b.year, b.month, b.day, b.hour, b.minute, b.city, b.gender)

    hehun_result = _hehun_engine.match(r1, r2)
    compat_match = _compute_match_score(r1, r2)
    union = run_union(hehun_result, compat_match, r1, r2, a, b,
                      relation=req.relation)

    quote = generate_yuan_quote(union["levelLabel"], union["features"],
                                relation=req.relation, cliffhanger=True,
                                polish_fn=_make_polish_fn())
    union["yuan_card"]["quote"] = quote["full"]
    union["yuan_card"]["quoteParts"] = quote

    return {
        "score": union["score"], "levelLabel": union["levelLabel"],
        "levelSublabel": union["levelSublabel"],
        "dimensions": union["dimensions"], "features": union["features"],
        "relation": union["relation"],
        "quote": quote["full"], "quoteParts": quote,
        "yuan_card": union["yuan_card"],
        "paywall": {"product": "deep_report", "price": 19.9,
                    "message": "解锁深度合盘报告：前世今生 / 相处模式 / 矛盾点与化解 / 契合详情"},
        "transient": True,
    }
