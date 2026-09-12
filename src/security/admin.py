"""超管白名单（`ADMIN_IDS`）——判定的单一事实源（k36 A28 最小可用面）。

语义（本批定义，产品未定义完整权限面前只做最小面）：

- `ADMIN_IDS`：逗号分隔的 user_id 列表，**只从环境变量读**（`.env` 由 config 在
  导入时载入 os.environ，启动后即固定）。
- **fail-closed**：未配置 / 为空 / 只有空白或逗号 → 零超管，任何路径都不放行。
- **判据唯一**：只有「已验证 JWT 的 sub ∈ ADMIN_IDS」（精确匹配，大小写敏感）
  才能认定为超管。请求体/查询参数/请求头里自称 admin、JWT 的 role claim、
  API key 一律**不作判据**（调用方必须传入已由 `JWTHandler.verify_token`
  校验过的 `sub`，不得传客户端可自填的 user_id）。
- **不打印具体 user_id**：对外只暴露数量（`admin_whitelist_summary()`）；
  具体标识只进审计通道（`src/security/audit.py`，安全要求：超管动作可追溯）。

消费点（口径一致，改动一处即全生效）：
- 运维端点：`src/security/auth.py::require_admin`（ADMIN_KEY 之外的第二条放行路径）
- 每日 chat 额度：`src/services/chat_quota.py`（/api/chat 与 /api/chat/stream 共用）
- 引擎/工具额度：`src/bot/handler.py::_check_quota`（memberships.queries_limit 门）
"""
import os

ADMIN_IDS_ENV = "ADMIN_IDS"


def admin_ids() -> frozenset:
    """当前生效的超管 user_id 集合（每次都读环境变量，便于测试注入与运行期一致）。"""
    raw = os.getenv(ADMIN_IDS_ENV, "") or ""
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


def is_admin_user(user_id) -> bool:
    """user_id 是否超管。空值/未配置 → False（fail-closed）。"""
    if not user_id:
        return False
    return str(user_id) in admin_ids()


def admin_whitelist_summary() -> str:
    """启动日志用文案：「超管白名单：已配置 N 个」/「超管白名单：未配置」。

    只暴露数量，绝不包含具体 user_id（安全要求）。
    """
    count = len(admin_ids())
    return f"已配置 {count} 个" if count else "未配置"
