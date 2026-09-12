"""对话额度判定与消费（L5-1：对话额度体系 + 降级链路，成本控制核心）。

规则：
- 会员（plan != free）/体验模式：不限、不计数、不降级（正常对话完全不变）。
- 免费用户：每日 15 条（CHAT_DAILY_LIMIT）。前 15 条正常；第 16 条起降级
  ——不 429 硬断，返回 downgraded=True → 调用方切换 LLM（GLM-4-Flash +
  精简 prompt）。
- 消费点：/api/chat 与 /api/chat/stream 请求进入时（鉴权后、LLM 调用前）。
- 任何异常：放行不降级（宁可正常也不误伤正常对话）。

时间口径：北京时间自然日（YYYY-MM-DD），与 ming_quota 一致。
"""
import logging
from datetime import datetime, timedelta, timezone

from src.config import is_experience_mode
from src.security.admin import is_admin_user

logger = logging.getLogger(__name__)

CHAT_DAILY_LIMIT = 15  # 免费用户每日对话条数（成本控制核心参数）


def _bj_day() -> str:
    """北京时间自然日（与 ming_quota 同口径）。"""
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


def chat_quota_status(member_dao, chat_quota_dao, user_id: str) -> dict:
    """查询对话额度状态（GET /api/user/chat-quota 用）。

    返回 {used, limit, downgraded, is_member}：
    - 会员/体验模式：used=0, limit=None, downgraded=False, is_member=True
    - 超管（ADMIN_IDS 白名单，k36 A28）：used=0, limit=None, downgraded=False,
      is_member=False（不是会员，只是豁免；limit=None 表示"无限"，与会员同款表示法）
    - 免费用户：used=今日已用条数, limit=15, downgraded=used>=15, is_member=False
    """
    if is_admin_user(user_id):
        # 超管豁免：不限额、不计次（与会员/体验模式同款：不进消费分支）。
        # 判据 = 已验证身份（/api/chat 的 user_id 取自 JWT sub）；白名单为空 → 恒不命中。
        return {"used": 0, "limit": None, "downgraded": False, "is_member": False}

    is_member = False
    try:
        membership = member_dao.get_membership(user_id) if member_dao else None
        is_member = bool(membership and membership.get("plan", "free") != "free")
    except Exception:
        logger.warning("chat quota member check failed: user=%s", user_id)
    if is_member or is_experience_mode():
        return {"used": 0, "limit": None, "downgraded": False, "is_member": is_member}

    used = 0
    if chat_quota_dao is not None:
        try:
            used = chat_quota_dao.get_count(user_id, _bj_day())
        except Exception:
            logger.warning("chat quota query failed: user=%s", user_id)
    return {
        "used": used,
        "limit": CHAT_DAILY_LIMIT,
        "downgraded": used >= CHAT_DAILY_LIMIT,
        "is_member": False,
    }


def try_consume_chat_quota(member_dao, chat_quota_dao, user_id: str) -> dict:
    """对话请求进入时消费额度（鉴权后、LLM 调用前）。

    返回同 chat_quota_status：
    - 免费且未超限：cnt+1，downgraded=False（正常链路）；
    - 免费且已超限（第 16 条起）：不计数，downgraded=True（降级链路）；
    - 会员/体验模式：不计数不降级。
    内部异常一律吞掉并放行（保守：不因额度系统故障破坏正常对话）。
    """
    status = chat_quota_status(member_dao, chat_quota_dao, user_id)
    if (status["is_member"] or is_experience_mode() or status["downgraded"]
            or is_admin_user(user_id)):
        return status  # 会员/体验/超管不计数；已超限不再累计
    if chat_quota_dao is not None:
        try:
            chat_quota_dao.consume(user_id, _bj_day(), CHAT_DAILY_LIMIT)
            status["used"] = chat_quota_dao.get_count(user_id, _bj_day())
        except Exception:
            logger.warning("chat quota consume failed: user=%s", user_id)
    return status
