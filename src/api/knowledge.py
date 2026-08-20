"""命理知识解析 API（L2-2）：排盘结果点十神/十二长生/纳音/神煞/天干/地支查看知识解析。

- GET /api/knowledge?category=shishen&name=正财 → {name, tip, gujue?, ...}
- GET /api/knowledge/categories → {category: [名称...]}（前端渲染可点文字用）
- 全接口 require_user 鉴权（红线：业务路由只认 JWT，与平台其他接口一致）
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

from src.engines import knowledge as knowledge_engine
from src.security.auth import require_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("")
async def get_knowledge(category: str, name: str,
                        uid: str = Depends(require_user)):
    """查知识条目：category ∈ shishen/zhangsheng/nayin/shensha/tiangan/dizhi。

    - 分类非法 → 400；分类合法但名称未命中 → 404。
    """
    cat = (category or "").strip()
    key = (name or "").strip()
    if cat not in knowledge_engine.all_categories():
        raise HTTPException(status_code=400, detail="未知知识分类")
    item = knowledge_engine.get_knowledge(cat, key)
    if item is None:
        raise HTTPException(status_code=404, detail="未找到该知识条目")
    return item


@router.get("/categories")
async def knowledge_categories(uid: str = Depends(require_user)):
    """各类别名称清单（含条数），前端据此渲染可点文字。"""
    cats = knowledge_engine.all_categories()
    return {"categories": cats,
            "counts": {c: len(names) for c, names in cats.items()}}
