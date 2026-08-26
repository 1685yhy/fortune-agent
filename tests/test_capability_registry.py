"""批次 1：统一能力注册表测试（spec 1.4 第 1 条 + 一致性）。"""
import sys

sys.path.insert(0, "src")

from src.bot import capability_registry as reg  # noqa: E402
from src.bot.tool_calls import TOOL_REGISTRY  # noqa: E402


def test_tool_coverage():
    """7 个工具全注册：排盘/检索/搜索/解梦/风水/择日/查记录。"""
    names = {c.name for c in reg.CAPABILITIES if c.cap_type == "tool"}
    assert names == {"排盘", "检索", "搜索", "解梦", "风水", "择日", "查记录"}


def test_tool_registry_projection():
    """TOOL_REGISTRY 从注册表投影：7 键，desc/requires 与注册表一致。"""
    assert set(TOOL_REGISTRY) == {"排盘", "检索", "搜索", "解梦", "风水", "择日", "查记录"}
    for name, cap in reg.CAPABILITY_BY_NAME.items():
        if cap.cap_type == "tool":
            assert TOOL_REGISTRY[name]["desc"] == cap.description
            assert TOOL_REGISTRY[name]["requires"] == cap.requires


def test_intent_coverage():
    """15 个意图全注册（handler_map 全量，含 hourly/xuetang）。"""
    ids = {c.cap_id for c in reg.CAPABILITIES if c.cap_type == "intent"}
    assert ids == {"bazi", "ziwei", "liuyao", "fengshui", "mianxiang", "zeri",
                   "qimen", "xingming", "hehun", "dream", "calendar",
                   "hourly", "xuetang", "advisor", "career"}


def test_intent_enum_line_unchanged():
    """枚举行与 COMBINED_PROMPT 现原文一字不差（14 个，无 xuetang/hourly）。"""
    assert reg.build_intent_enum_line() == (
        "Classify into EXACTLY ONE: bazi, ziwei, liuyao, fengshui, zeri, "
        "mianxiang, qimen, xingming, hehun, dream, calendar, advisor, career, "
        "free_chat"
    )


def test_validate_params():
    """参数校验：缺必填/类型错 → 错误串；合法 → None。"""
    err = reg.validate_params("web_search", {"query": "北京天气"})
    assert err is None
    err = reg.validate_params("web_search", {})
    assert err is not None and "query" in err
    err = reg.validate_params("web_search", {"query": 123})
    assert err is not None
    assert reg.validate_params("no_such_cap", {"query": "x"}) is not None


def test_tool_intent_name_collision():
    """同名 cap_id（fengshui/zeri/dream）：tool 条目优先于 intent 条目（工单校验走 tool）。

    注：_TOOL_CAPS(7) + _INTENT_CAPS(15) = 22 项列表，但 fengshui/zeri/dream 的
    cap_id 与中文名在两类中完全相同，去重后唯一键为 19（修复前后一致）。
    """
    assert reg.CAPABILITY_BY_ID["fengshui"].cap_type == "tool"
    assert reg.CAPABILITY_BY_ID["zeri"].cap_type == "tool"
    assert reg.CAPABILITY_BY_ID["dream"].cap_type == "tool"
    assert reg.CAPABILITY_BY_NAME["风水"].cap_type == "tool"
    assert reg.CAPABILITY_BY_NAME["择日"].cap_type == "tool"
    assert reg.CAPABILITY_BY_NAME["解梦"].cap_type == "tool"
    assert len(reg.CAPABILITY_BY_ID) == 19
    assert len(reg.CAPABILITY_BY_NAME) == 19
    # 回归点：intent 覆盖 tool 时，tool 必填校验静默失效（validate_params 返回 None）
    assert reg.validate_params("fengshui", {}) is not None
    assert reg.validate_params("zeri", {}) is not None
    assert reg.validate_params("dream", {}) is not None
