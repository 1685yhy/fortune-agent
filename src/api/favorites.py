"""收藏 API（Task 9：缺口②——收藏后端化）。全接口 require_user 鉴权 + 归属强制。

- POST   /api/favorites          收藏一条（{type, ref_id, summary}；UNIQUE 幂等）
- DELETE /api/favorites?type=&ref_id=   取消收藏（幂等）
- GET    /api/favorites          收藏列表（最新在前，默认 50 条）
- POST   /api/favorites/import   本地存量导入（{items:[{type,ref_id,summary}]}，
                                 逐条 add，UNIQUE 天然防重，返回导入条数）

安全约定（与 user.py 同款）：
- user_id 一律取 JWT sub（uid），body/query 传参被忽略（防覆写他人收藏）；
- type 白名单 chat/jian/qian/ming/lamp，白名单外 400；
- summary strip 后截 100 字；
- DAO 未装配（setup 未注入）→ 503，绝不假 200（fail-closed）。
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.security.auth import require_user
from src.storage.favorite_dao import FavoriteDAO

router = APIRouter(prefix="/api/favorites", tags=["favorites"])

# type 白名单：chat(对话)/jian(晨笺)/qian(灵签)/ming(名笺)/lamp(灯语)
ALLOWED_TYPES = ("chat", "jian", "qian", "ming", "lamp")
SUMMARY_MAX = 100
REF_ID_MAX = 128
IMPORT_MAX = 500  # 单次导入上限（防超大 body；本地存量通常 < 100 条）

_dao: Optional[FavoriteDAO] = None


def setup(dao: FavoriteDAO):
    """在主应用生命周期中注入 DAO（仿 zeri.py/qian.py setup 模式）。"""
    global _dao
    _dao = dao


def _validate(type_: str, ref_id: str, summary: str = "") -> str:
    """统一校验：type 白名单 / ref_id 必填截长 / summary 截 100 字。返回清洗后 summary。"""
    if type_ not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail=f"type 非法：仅支持 {'/'.join(ALLOWED_TYPES)}")
    if not ref_id or not str(ref_id).strip():
        raise HTTPException(status_code=400, detail="ref_id 不能为空")
    return (summary or "").strip()[:SUMMARY_MAX]


class FavoriteBody(BaseModel):
    type: str = ""
    ref_id: str = ""
    summary: str = ""


class ImportItem(BaseModel):
    type: str = ""
    ref_id: str = ""
    summary: str = ""


class ImportBody(BaseModel):
    items: list[ImportItem] = Field(default_factory=list)


@router.post("")
def add_favorite(body: FavoriteBody, uid: str = Depends(require_user)):
    """收藏一条。UNIQUE(user_id,type,ref_id) 幂等：重复收藏 → already=true 提示不报错。"""
    if _dao is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    summary = _validate(body.type, body.ref_id, body.summary)
    row_id = _dao.add(uid, body.type, str(body.ref_id).strip()[:REF_ID_MAX], summary)
    return {"success": True, "already": row_id == 0}


@router.delete("")
def remove_favorite(type: str = "", ref_id: str = "", uid: str = Depends(require_user)):
    """取消收藏（归属强制：只删自己的）。不存在 → deleted=false 不报错。"""
    if _dao is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    _validate(type, ref_id)
    existed = _dao.has(uid, type, str(ref_id).strip()[:REF_ID_MAX])
    _dao.remove(uid, type, str(ref_id).strip()[:REF_ID_MAX])
    return {"success": True, "deleted": existed}


@router.get("")
def list_favorites(limit: int = 50, uid: str = Depends(require_user)):
    """收藏列表（最新在前，limit 上限 200）。"""
    if _dao is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    limit = max(1, min(int(limit), 200))
    items = _dao.list_favorites(uid, limit=limit)
    return {"items": items, "count": len(items)}


@router.post("/import")
def import_favorites(body: ImportBody, uid: str = Depends(require_user)):
    """本地存量导入（小程序 wx storage 的 kept 收藏首启迁移）。

    逐条 add（UNIQUE 天然幂等防重），重复导入不产生重复行；返回本次新增条数。
    imported 标记列在 DAO 层置 1（本地清标记是前端行为，后端只需幂等）。
    """
    if _dao is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    items = body.items or []
    if len(items) > IMPORT_MAX:
        raise HTTPException(status_code=400, detail=f"单次导入不能超过 {IMPORT_MAX} 条")
    imported = 0
    for it in items:
        summary = _validate(it.type, it.ref_id, it.summary)
        row_id = _dao.add(uid, it.type, str(it.ref_id).strip()[:REF_ID_MAX], summary, imported=1)
        if row_id:
            imported += 1
    return {"success": True, "imported": imported}
