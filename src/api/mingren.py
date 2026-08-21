"""名人命例库 API（L3-1，问真 VIP 同款门控）：2807 位历史人物命例 + 穷通宝鉴评注。

- 数据源：data/mingren/emperors_mingli.json（1.5MB，2807 条）
  {人名: {info(简介), info2(穷通宝鉴评注——徐乐吾曰等), source, flist([{name: 年份, data: 事件}...])}}
- GET /api/mingren?page=&size=&q= → 分页列表 + 姓名搜索（require_user）
  - 高级会员（plan ∈ pro/annual）→ 全量 2807 位；
  - 基础/免费用户（plan ∈ free/basic）→ 仅前 35 条（按库顺序），is_full=false；
  - 返回 {items, total(可见数), total_all(未门控匹配数), library_total, is_full}。
- GET /api/mingren/{name} → 详情（info/info2/flist）
  - 未解锁（免费/基础访问前 35 之外）→ 403 + {"code": "VIP_REQUIRED", ...}（前端弹开通引导）；
  - 未知人名 → 404。
- 体验模式（EXPERIENCE_MODE）→ 全量免费（同 zeri/night/zhuanxiang 门控口径）。
- 数据只读加载一次进内存（模块级懒加载缓存）；门控服务端强制，不信任客户端。
"""
import json
import logging
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Path as FPath, Query

from src.config import is_experience_mode
from src.security.auth import require_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/mingren", tags=["mingren"])

# 免费/基础用户可见条数（按库顺序前 35，问真同款"免费试看 35 例"口径）
FREE_LIMIT = 35

# 高级会员档位：pro / annual 全量；free / basic 仅前 35 条
_FULL_PLANS = ("pro", "annual")

# 数据文件位置（项目根 data/mingren/），setup 可注入覆盖（测试用临时库）
_DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "mingren" / "emperors_mingli.json"

# 依赖注入（仿 zeri.py setup 模式）：主应用 lifespan 注入 MemberDAO；测试注入 stub
_member_dao = None
_data_path = None

# 数据懒加载缓存（2807 条 ≈ 1.5MB，进程内只读一次；dict 保序 = 库顺序）
_cache = None
_cache_lock = threading.Lock()


def setup(member_dao=None, data_path=None):
    """在主应用生命周期中注入依赖。"""
    global _member_dao, _data_path
    _member_dao = member_dao
    if data_path is not None:
        global _cache
        _data_path = Path(data_path)
        _cache = None  # 换库时清缓存（测试用）


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
    return {
        "name": name,
        "info": item.get("info") or "",
        "info2": item.get("info2") or "",
        "source": item.get("source") or "",
        "has_info2": bool(item.get("info2") and item["info2"] != "-"),
        "flist": item.get("flist") or [],
    }
