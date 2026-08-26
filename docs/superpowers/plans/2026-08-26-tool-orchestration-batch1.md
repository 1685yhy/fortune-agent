# 批次 1：统一能力注册表 + 结构化工单协议 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把"文字标签调工具"升级为豆包式"能力注册表 + 结构化 JSON 工单 + 参数校验 + 超时重试 + 标准结果回喂"，意图清单与工具清单同源。

**Architecture:** 新增 `src/bot/capability_registry.py` 作为唯一能力事实源（7 工具 + 15 意图）；`tool_calls.py` 的 `parse_tool_calls` 升级为"JSON 工单优先、正则兜底"双协议；`handler.py` 执行层从注册表分派、统一超时重试、回喂标准 JSON 结果；`message_analyzer.py` 意图枚举行由注册表生成（同源）。现有执行器（`_tool_*`）实现零改动——结构化参数经 `serialize_params` 序列化回文本桥接。

**Tech Stack:** Python 3.12.3，jsonschema 4.26.0（已在 venv，无新依赖），pytest，httpx。

## Global Constraints

以下红线来自 spec（docs/superpowers/specs/2026-08-26-tool-orchestration-design.md），所有任务隐含遵守：

- **不动**鉴权/归属/加密/存储结构/DAO 表结构；计算层（BaziEngine/zeri/jian_quote）零改动；`_extract_bazi_info` 等既有提取器行为不变
- **工具执行器实现零改动**：`_tool_bazi/_tool_search/_tool_web_search/_tool_dream/_tool_fengshui/_tool_zeri/_tool_query_records` 函数体不碰，只改调用方式
- **MAX_TOOL_ITERATIONS=2 不放开**；降级链路（downgraded）禁用工具循环的防御性门控保留
- **文本标签兼容期保留**：`<tool_call>工具名: 参数</tool_call>` 与 `TOOL:` 前缀的正则兜底必须保留，只增不减
- **数量以代码为准**：TOOL_REGISTRY 实际 **7 个工具**（排盘/检索/搜索/解梦/风水/择日/查记录），handler_map 实际 **15 个意图**（bazi/ziwei/liuyao/fengshui/mianxiang/zeri/qimen/xingming/hehun/dream/calendar/hourly/xuetang/advisor/career）——不写死数字，以运行时断言为准
- **测试基线**：`1191 passed / 0 failed / 7 skipped / 0 errors`（2026-08-26 固化，`.superpowers/sdd/baseline-20260826.md`）。通过集不减少、失败集不新增。复跑命令：
  ```bash
  cd /mnt/e/fortune-agent-deploy && timeout 1200 /home/a/fortune-agent/.venv/bin/python -m pytest tests/ -q -p no:cacheprovider --continue-on-collection-errors 2>&1 | tail -5
  ```
  全量挂起（WSL2 磁盘 I/O）时按文件跑：`for f in tests/test_*.py; do timeout 150 /home/a/fortune-agent/.venv/bin/python -m pytest "$f" -q -p no:cacheprovider 2>&1 | tail -2; done`
- **生产部署红线**：rsync 禁 `--delete`；pkill 必须单独一条命令（防自杀）；启动必须显式 `cd /home/a/fortune-run`；冷启动 ~140s 不能早判死
- **测试纪律**：无 conftest.py；测试不触碰真实 DB（tmp_path/:memory:）、网络全 mock；真实网络调用只允许在 scripts/ 验证脚本里
- **枚举集不动**：`_parse_response` 的 valid set（15 个含 xuetang 不含 hourly）、COMBINED_PROMPT 枚举行（14 个）、handler_map（15 个含 hourly）三者保持现状，注册表条目为其快照

---

### Task 1: 验证 deepseek Anthropic 兼容端点是否支持原生 tool_use（决定性前置）

**为什么是第一步**：PM 拍板"如果验证下来 deepseek 原生支持 tool_use，就直接升级成和豆包完全一样的原生方式"。本任务用真实 API 实测端点对 `tools` 参数的响应，结论写入 `.superpowers/sdd/tool-use-verification-20260826.md`，决定 Task 3B（原生增强）是否执行。**无论结论如何，主线 Task 2-7 照常执行**（应用层 JSON 工单是豆包机制的可落地版，原生 tool_use 是加分项）。

**Files:**
- Create: `scripts/verify_deepseek_tool_use.py`
- Create: `.superpowers/sdd/tool-use-verification-20260826.md`（结论文档，脚本自动写 + implementer 手动补结论）

**Interfaces:**
- Consumes: `src/llm/client.py` 的 `ANTHROPIC_MESSAGES_URL`、`_anthropic_headers(api_key)`、`_anthropic_model_name(model)`（只读复用，不改 client）
- Produces: 结论文档（RESULT 三选一：`SUPPORTED_NATIVE_TOOL_USE` / `IGNORED_TOOLS` / `NOT_SUPPORTED`），驱动 Task 3B 的开关

- [ ] **Step 1: 写验证脚本**

```python
#!/usr/bin/env python3
"""验证 deepseek Anthropic 兼容端点是否支持原生 tool_use（批次 1 Task 1）。

用法: FORTUNE_API_KEY=<key> python scripts/verify_deepseek_tool_use.py [model]
默认模型与生产一致: deepseek-v4-flash；也可传 deepseek-chat / deepseek-reasoner。
退出码: 0=原生支持 1=不支持/被忽略 2=缺 key 3=网络/协议错误 4=未知
"""
import json
import os
import sys

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from src.llm.client import (  # noqa: E402
    ANTHROPIC_MESSAGES_URL, _anthropic_headers, _anthropic_model_name,
)

TOOLS = [{
    "name": "web_search",
    "description": "网络搜索，输入关键词获取最新信息",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "搜索关键词"}},
        "required": ["query"],
    },
}]


def main() -> int:
    model = sys.argv[1] if len(sys.argv) > 1 else "deepseek-v4-flash"
    api_key = os.environ.get("FORTUNE_API_KEY")
    if not api_key:
        print("FATAL: 需要 FORTUNE_API_KEY 环境变量")
        return 2
    payload = {
        "model": _anthropic_model_name(model),
        "max_tokens": 300,
        "temperature": 0.3,
        "tools": TOOLS,
        "tool_choice": {"type": "auto"},
        "messages": [{"role": "user",
                      "content": "帮我查一下：今天北京天气怎么样？请用搜索工具。"}],
    }
    try:
        resp = httpx.post(ANTHROPIC_MESSAGES_URL,
                          headers=_anthropic_headers(api_key),
                          json=payload, timeout=90.0)
    except Exception as e:  # noqa: BLE001
        print(f"RESULT=ERROR 请求异常: {e}")
        return 3
    print(f"HTTP {resp.status_code}")
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        print("RESULT=ERROR 非 JSON 响应:", resp.text[:300])
        return 3
    if resp.status_code != 200:
        print("RESULT=NOT_SUPPORTED_OR_ERROR")
        print(json.dumps(data, ensure_ascii=False)[:500])
        return 1
    stop = data.get("stop_reason", "")
    content = data.get("content", [])
    tool_blocks = [b for b in content
                   if isinstance(b, dict) and b.get("type") == "tool_use"]
    text_blocks = [b for b in content
                   if isinstance(b, dict) and b.get("type") == "text"]
    print(f"stop_reason={stop}")
    print(f"tool_use_blocks={len(tool_blocks)} text_blocks={len(text_blocks)}")
    if tool_blocks:
        print("RESULT=SUPPORTED_NATIVE_TOOL_USE")
        print(json.dumps(tool_blocks[0], ensure_ascii=False)[:400])
        return 0
    if text_blocks:
        print("RESULT=IGNORED_TOOLS (纯文本，tools 被忽略)")
        print(json.dumps(text_blocks[0], ensure_ascii=False)[:300])
        return 1
    print("RESULT=UNKNOWN")
    print(json.dumps(data, ensure_ascii=False)[:500])
    return 4


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 跑验证（真实 API，三个模型全测）**

```bash
cd /mnt/e/fortune-agent-deploy && FORTUNE_API_KEY=dd2b3b17feae1dc0093cde4a45e8d3a2 /home/a/fortune-agent/.venv/bin/python scripts/verify_deepseek_tool_use.py deepseek-v4-flash
FORTUNE_API_KEY=dd2b3b17feae1dc0093cde4a45e8d3a2 /home/a/fortune-agent/.venv/bin/python scripts/verify_deepseek_tool_use.py deepseek-chat
FORTUNE_API_KEY=dd2b3b17feae1dc0093cde4a45e8d3a2 /home/a/fortune-agent/.venv/bin/python scripts/verify_deepseek_tool_use.py deepseek-reasoner
```

Expected: 每行输出 `RESULT=...` 三选一。任何 HTTP 非 200 或异常都如实记录，不重试、不改参（结论按事实写）。

- [ ] **Step 3: 写结论文档 `.superpowers/sdd/tool-use-verification-20260826.md`**

内容：三个模型的 RESPONSE 原文摘录（status/stop_reason/content 前 300 字）+ 结论：
- `SUPPORTED_NATIVE_TOOL_USE`（任一模型）→ 结论写"**执行 Task 3B**：主线完成后追加原生 tool_use 增强"，并注明哪个模型支持
- 否则 → 结论写"**不执行 Task 3B**：应用层 JSON 工单为准（主线）"
- 无论结论，补一句："原生支持与否不影响批次 1 主线验收"

- [ ] **Step 4: 提交**

```bash
cd /mnt/e/fortune-agent-deploy && git add scripts/verify_deepseek_tool_use.py .superpowers/sdd/tool-use-verification-20260826.md && git commit -m "feat: verify deepseek native tool_use support (batch1 task1)"
```

---

### Task 2: 统一能力注册表 `capability_registry.py`

**Files:**
- Create: `src/bot/capability_registry.py`
- Modify: `src/bot/tool_calls.py:80-117`（TOOL_REGISTRY 改为从注册表投影）
- Test: `tests/test_capability_registry.py`

**Interfaces:**
- Consumes: 无（纯新模块；不 import handler/tool_calls，避免循环依赖）
- Produces:
  - `Capability`（frozen dataclass）：`cap_id/name/description/params_schema/executor/timeout_s/retries/requires/cap_type`
  - `CAPABILITIES: list[Capability]`（22 条：7 tool + 15 intent）
  - `TOOL_NAME_BY_ID: dict[str, str]`（英文 id → 中文名，如 "web_search" → "搜索"）
  - `CAPABILITY_BY_NAME: dict[str, Capability]`（中文名 → 条目）
  - `CAPABILITY_BY_ID: dict[str, Capability]`
  - `validate_params(cap_id: str, params: dict) -> Optional[str]`（jsonschema 校验，None=合法）
  - `build_intent_enum_line() -> str`（枚举行文本，必须 == COMBINED_PROMPT 现枚举原文）
  - `build_tool_description() -> str`（工具说明书文本，Task 5 用）
  - `bind_executors(mapping: dict[str, Callable]) -> None`（handler 侧注入执行器引用，防循环 import）

- [ ] **Step 1: 写失败测试（注册表完整性）**

`tests/test_capability_registry.py`：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /mnt/e/fortune-agent-deploy && /home/a/fortune-agent/.venv/bin/python -m pytest tests/test_capability_registry.py -q -p no:cacheprovider
```

Expected: FAIL（`ModuleNotFoundError: No module named 'src.bot.capability_registry'`）。

- [ ] **Step 3: 实现 `src/bot/capability_registry.py`**

```python
"""统一能力注册表（批次 1，spec 1.1）：唯一能力事实源。

两类能力：
- cap_type="tool"：可被 <tool_calls> 工单调用的工具（7 个），executor 由
  handler 启动时 bind_executors() 注入（防循环 import）
- cap_type="intent"：意图分发分支（15 个），executor 指向 _handle_*

设计要点：
- TOOL_REGISTRY（tool_calls.py）从此文件投影，消费方签名不变
- validate_params 用 jsonschema 校验结构化工单参数
- build_intent_enum_line() 与 COMBINED_PROMPT 现枚举原文必须一致（Task 5 同源改造的锚点）
"""
from dataclasses import dataclass, field
from typing import Callable, Optional

import jsonschema

# 7 个工具的 params_schema：全部单文本键（现有执行器吃自然语言文本），
# 结构化工单经 serialize_params 序列化回文本桥接（spec 红线：执行器零改动）
_TOOL_PARAMS_SCHEMAS = {
    "bazi_chart": {"type": "object",
                   "properties": {"text": {"type": "string",
                                            "description": "出生信息自然语言描述，如：1990年5月20日 午时 北京 男"}},
                   "required": ["text"]},
    "quote_rag": {"type": "object",
                  "properties": {"query": {"type": "string", "description": "搜索关键词"}},
                  "required": ["query"]},
    "web_search": {"type": "object",
                   "properties": {"query": {"type": "string", "description": "搜索关键词"}},
                   "required": ["query"]},
    "dream": {"type": "object",
              "properties": {"text": {"type": "string", "description": "梦境描述"}},
              "required": ["text"]},
    "fengshui": {"type": "object",
                 "properties": {"text": {"type": "string", "description": "房屋坐向/布局描述，如：坐北朝南"}},
                 "required": ["text"]},
    "zeri": {"type": "object",
             "properties": {"text": {"type": "string",
                                      "description": "场景+时间范围，如：下个月搬家"}},
             "required": ["text"]},
    "record_lookup": {"type": "object",
                      "properties": {"query": {"type": "string",
                                               "description": "想查的用户本人数据描述，如：我的档案"}},
                      "required": ["query"]},
}


@dataclass(frozen=True)
class Capability:
    cap_id: str                 # 如 "web_search"
    name: str                   # 中文短名，LLM 可见（"搜索"）
    description: str            # 干什么用 + 何时用（决策说明书）
    params_schema: dict         # JSON Schema（tool 类有；intent 类为 {}）
    executor: Optional[Callable] = None   # handler 启动时 bind_executors 注入
    timeout_s: float = 8.0      # 单次执行超时（tool 类）
    retries: int = 1            # 失败重试次数（tool 类）
    requires: str = ""          # 前置条件说明（tool 类）
    cap_type: str = "tool"      # "tool" | "intent"


# ---- tool 类（7 个）：desc/requires 原文迁移自 tool_calls.py TOOL_REGISTRY ----
_TOOL_CAPS = [
    Capability("bazi_chart", "排盘",
               "输入出生信息（年月日时、地点、性别）的自然语言描述，输出四柱十神大运流年",
               _TOOL_PARAMS_SCHEMAS["bazi_chart"], requires="出生年月日时、出生地点、性别"),
    Capability("quote_rag", "检索",
               "输入搜索关键词，输出古籍原文 Top5。只引用与用户问题直接相关的内容，不相关忽略",
               _TOOL_PARAMS_SCHEMAS["quote_rag"], requires="搜索关键词"),
    Capability("web_search", "搜索",
               "输入关键词，输出网络搜索结果（带来源 URL，Top 3-5，补充最新/社会信息）",
               _TOOL_PARAMS_SCHEMAS["web_search"], requires="搜索关键词"),
    Capability("dream", "解梦",
               "输入梦境描述，输出象征分析+古籍匹配",
               _TOOL_PARAMS_SCHEMAS["dream"], requires="梦境描述"),
    Capability("fengshui", "风水",
               "输入房屋坐向/布局描述，输出吉凶判断+化解建议",
               _TOOL_PARAMS_SCHEMAS["fengshui"], requires="房屋坐向（如：坐北朝南）"),
    Capability("zeri", "择日",
               "输入场景+时间范围，输出3个推荐吉日（宜忌/吉时/方位/一句理由）。"
               "场景支持：嫁娶/搬家/开业/晋升/出行/提车/签约；时间可写下个月、下周、具体日期",
               _TOOL_PARAMS_SCHEMAS["zeri"], requires="场景（嫁娶/搬家/开业/晋升/出行/提车/签约）+ 时间范围（下个月/下周/具体日期）"),
    Capability("record_lookup", "查记录",
               "查用户自己的存量数据（档案/解梦/历史对话/签/名笺/灯语/择吉/晨笺/收藏/会员）。"
               "输入想查的内容描述，如'我的档案''以前解过什么梦'",
               _TOOL_PARAMS_SCHEMAS["record_lookup"], requires="用户本人数据"),
]

# ---- intent 类（15 个）：handler_map（handler.py:2758-2774）全量快照 ----
_INTENT_CAPS = [
    Capability("bazi", "八字", "八字排盘：出生信息+命理分析", {}, cap_type="intent"),
    Capability("ziwei", "紫微", "紫微斗数排盘分析", {}, cap_type="intent"),
    Capability("liuyao", "六爻", "易经六爻占卜", {}, cap_type="intent"),
    Capability("fengshui", "风水", "风水分析", {}, cap_type="intent"),
    Capability("mianxiang", "面相", "面相分析", {}, cap_type="intent"),
    Capability("zeri", "择日", "择吉日", {}, cap_type="intent"),
    Capability("qimen", "奇门", "奇门遁甲分析", {}, cap_type="intent"),
    Capability("xingming", "姓名", "姓名学分析", {}, cap_type="intent"),
    Capability("hehun", "合婚", "双人合盘/合婚分析", {}, cap_type="intent"),
    Capability("dream", "解梦", "梦境解析", {}, cap_type="intent"),
    Capability("calendar", "黄历", "今日运势/今日宜忌/黄历查询", {}, cap_type="intent"),
    Capability("hourly", "时辰", "时辰/每日时段分析", {}, cap_type="intent"),
    Capability("xuetang", "学堂", "学堂/教育命理分析", {}, cap_type="intent"),
    Capability("advisor", "顾问", "生活建议/怎么办类咨询", {}, cap_type="intent"),
    Capability("career", "事业", "事业适配：职业/公司/行业选择类问题", {}, cap_type="intent"),
]

CAPABILITIES: list = _TOOL_CAPS + _INTENT_CAPS

TOOL_NAME_BY_ID: dict = {c.cap_id: c.name for c in _TOOL_CAPS}
CAPABILITY_BY_NAME: dict = {c.name: c for c in CAPABILITIES}
CAPABILITY_BY_ID: dict = {c.cap_id: c for c in CAPABILITIES}

# COMBINED_PROMPT 现枚举原文（14 个，无 xuetang/hourly，含 free_chat）——顺序不可改
INTENT_ENUM_ORDER = ["bazi", "ziwei", "liuyao", "fengshui", "zeri", "mianxiang",
                     "qimen", "xingming", "hehun", "dream", "calendar",
                     "advisor", "career", "free_chat"]

_executors: dict = {}


def bind_executors(mapping: dict) -> None:
    """handler 侧注入执行器（tool 类 lambda + intent 类 _handle_*），防循环 import。"""
    _executors.update(mapping)
    for c in CAPABILITIES:
        c.__dict__["executor"] = _executors.get(c.cap_id) or c.executor


def validate_params(cap_id: str, params: dict) -> Optional[str]:
    """按 params_schema 校验结构化工单参数。返回 None=合法，否则错误信息串。"""
    cap = CAPABILITY_BY_ID.get(cap_id)
    if cap is None:
        return f"未知能力「{cap_id}」"
    if not cap.params_schema:
        return None
    try:
        jsonschema.validate(instance=params, schema=cap.params_schema)
        return None
    except jsonschema.ValidationError as e:
        return f"参数不合法: {e.message}"


def build_intent_enum_line() -> str:
    """意图枚举行（Task 5 同源改造锚点）：生成结果必须与 COMBINED_PROMPT 原文一致。"""
    return "Classify into EXACTLY ONE: " + ", ".join(INTENT_ENUM_ORDER)


def build_tool_description() -> str:
    """工具说明书文本（给 LLM 选工具的决策说明书，Task 5 注入 tool_loop）。"""
    lines = ["【可用工具清单】（需要时按 JSON 工单调用，不需要就不调用）"]
    for c in _TOOL_CAPS:
        lines.append(f"- {c.name}（{c.cap_id}）：{c.description}。参数：{c.requires}。"
                     f"超时{c.timeout_s:.0f}s，失败自动重试{c.retries}次")
    return "\n".join(lines)
```

- [ ] **Step 4: TOOL_REGISTRY 改为投影（`tool_calls.py:80-117` 整段替换）**

原 `TOOL_REGISTRY = {...}` 7 条 dict 字面量删除，替换为：

```python
# 工具注册表：从 capability_registry 投影（唯一事实源，消费方签名不变）。
# name → {key, desc, requires}；key 保留历史值（bazi/search/web/dream/fengshui/zeri/records）。
from src.bot.capability_registry import CAPABILITIES, TOOL_NAME_BY_ID  # noqa: E402

_TOOL_KEYS = {
    "排盘": "bazi", "检索": "search", "搜索": "web", "解梦": "dream",
    "风水": "fengshui", "择日": "zeri", "查记录": "records",
}
TOOL_REGISTRY = {
    c.name: {"key": _TOOL_KEYS[c.name], "desc": c.description,
             "requires": c.requires}
    for c in CAPABILITIES if c.cap_type == "tool"
}
```

（顶部 import 区加 `import` 语句前先确认无循环：tool_calls.py 不 import handler，capability_registry 不 import tool_calls，安全。）

- [ ] **Step 5: 跑测试确认通过**

```bash
cd /mnt/e/fortune-agent-deploy && /home/a/fortune-agent/.venv/bin/python -m pytest tests/test_capability_registry.py -v -p no:cacheprovider
```

Expected: 5 passed。

- [ ] **Step 6: 跑现有 tool_calls 相关测试确认投影无回归**

```bash
cd /mnt/e/fortune-agent-deploy && /home/a/fortune-agent/.venv/bin/python -m pytest tests/test_tool_calls.py tests/test_bot.py -q -p no:cacheprovider 2>&1 | tail -3
```

Expected: 无新增失败（数量以实际为准，不得少于运行前）。

- [ ] **Step 7: 提交**

```bash
cd /mnt/e/fortune-agent-deploy && git add src/bot/capability_registry.py src/bot/tool_calls.py tests/test_capability_registry.py && git commit -m "feat: unified capability registry (batch1 task2)"
```

---

### Task 3: 结构化工单协议（JSON 工单解析 + 参数序列化桥接）

**Files:**
- Modify: `src/bot/tool_calls.py`（新增 JSON 工单解析 + serialize_params；`parse_tool_calls` 升级为 JSON 优先正则兜底；`ToolCall` 加 `params_obj`）
- Test: `tests/test_capability_registry.py`（追加工单解析用例）

**Interfaces:**
- Consumes: Task 2 的 `TOOL_NAME_BY_ID`、`CAPABILITY_BY_ID`
- Produces:
  - `serialize_params(params: dict) -> str`（结构化工单参数 → 执行器文本）
  - `ToolCall(name, params: str = "", params_obj: Optional[dict] = None)`
  - `parse_tool_calls(text) -> List[ToolCall]`（行为升级但返回类型不变；JSON 工单 → params_obj 有值；正则兜底 → params 有值）

- [ ] **Step 1: 写失败测试（追加到 tests/test_capability_registry.py）**

```python
def test_json_workorder_parse():
    """JSON 工单块解析：合法工单 → ToolCall 带 params_obj。"""
    from src.bot.tool_calls import ToolCall, parse_tool_calls
    calls = parse_tool_calls(
        '好的，我来查。<tool_calls>'
        '[{"tool": "web_search", "params": {"query": "北京天气"}}, '
        '{"tool": "bazi_chart", "params": {"text": "1990年5月20日 北京 男"}}]'
        '</tool_calls>'
    )
    assert [c.name for c in calls] == ["搜索", "排盘"]
    assert calls[0].params_obj == {"query": "北京天气"}
    assert calls[1].params_obj == {"text": "1990年5月20日 北京 男"}


def test_json_workorder_bad_json_falls_back():
    """非法 JSON 工单块 → 正则兜底（旧协议仍工作）。"""
    from src.bot.tool_calls import parse_tool_calls
    calls = parse_tool_calls("<tool_calls>这不是JSON</tool_calls>\n<tool_call>搜索: 北京天气</tool_call>")
    assert [c.name for c in calls] == ["搜索"]
    assert calls[0].params_obj is None
    assert calls[0].params == "北京天气"


def test_json_workorder_unknown_tool_skipped():
    """工单里未知工具 → 跳过不执行。"""
    from src.bot.tool_calls import parse_tool_calls
    calls = parse_tool_calls(
        '<tool_calls>[{"tool": "no_such_tool", "params": {"q": "x"}}]</tool_calls>')
    assert calls == []


def test_serialize_params():
    """结构化参数 → 执行器文本：单键直接取值，多键 k: v 拼接。"""
    from src.bot.tool_calls import serialize_params
    assert serialize_params({"query": "北京天气"}) == "北京天气"
    assert serialize_params({"text": "1990年5月20日"}) == "1990年5月20日"
    assert serialize_params({"query": 123}) == "123"
    assert serialize_params({"a": "1", "b": "2"}) == "a: 1\nb: 2"
    assert serialize_params({}) == ""


def test_no_tool_call_no_workorder():
    """无工单无标签 → 空列表（不误判）。"""
    from src.bot.tool_calls import parse_tool_calls
    assert parse_tool_calls("今天天气不错") == []
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /mnt/e/fortune-agent-deploy && /home/a/fortune-agent/.venv/bin/python -m pytest tests/test_capability_registry.py -q -p no:cacheprovider
```

Expected: FAIL（`serialize_params` 不存在 / `parse_tool_calls` 不解析 JSON 工单）。

- [ ] **Step 3: 实现（tool_calls.py）**

文件头 docstring 上方加 `import json`（已有 re/dataclass/typing）：

```python
import json
from typing import List, Optional
```

`ToolCall` 改造（保持 `params: str` 字段兼容旧消费方）：

```python
@dataclass
class ToolCall:
    """一条解析出的工具调用。

    - 结构化工单（JSON 工单块）：params_obj 有值，params 为空串，执行前需校验+序列化
    - 文本标签（正则兜底）：params 有值，params_obj 为 None（旧行为，不校验直接执行）
    """
    name: str
    params: str = ""
    params_obj: Optional[dict] = None
```

新增常量与函数（放在 `TOOL_CALL_RE` 之后）：

```python
# 结构化工单块：<tool_calls>[{"tool": "...", "params": {...}}]</tool_calls>
_TOOL_CALLS_BLOCK_RE = re.compile(r'<tool_calls>(.*?)</tool_calls>', re.S)


def _parse_json_workorder(text: str) -> List[ToolCall]:
    """解析 JSON 工单块（JSON 优先，失败返回 [] 由正则兜底）。

    tool 字段接受英文 cap_id（如 "web_search"）或中文名（如 "搜索"），
    统一归一为注册表中文名（与文本标签路径的 ToolCall.name 一致）。
    """
    m = _TOOL_CALLS_BLOCK_RE.search(text)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    calls = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("tool", "")).strip()
        name = _TOOL_SYNONYMS.get(name, name)
        name = TOOL_NAME_BY_ID.get(name, name)  # 英文 cap_id → 中文名
        if name not in TOOL_REGISTRY:
            continue  # 未知工具：不执行（strip 路径仍会移除）
        params = item.get("params", {})
        if isinstance(params, str):
            params = {"text": params}
        elif not isinstance(params, dict):
            params = {}
        calls.append(ToolCall(name=name, params_obj=params))
    return calls


def serialize_params(params: dict) -> str:
    """结构化工单参数 → 执行器文本桥接（执行器全部吃自然语言文本）。

    单键直接取值；多键 "k: v" 换行拼接（通用兜底，够用即可）。
    """
    if not params:
        return ""
    if len(params) == 1:
        v = next(iter(params.values()))
        if isinstance(v, (str, int, float)):
            return str(v).strip()
    return "\n".join(f"{k}: {v}" for k, v in params.items())
```

`parse_tool_calls` 头部插入 JSON 优先分支（原正则逻辑保留为兜底）：

```python
def parse_tool_calls(text: str) -> List[ToolCall]:
    """解析回复中的工具调用。JSON 工单优先，正则文本标签兜底。

    - JSON 工单：<tool_calls>[{"tool": "web_search", "params": {"query": "..."}}]</tool_calls>
    - 文本标签（兼容期保留）：<tool_call>搜索: 关键词</tool_call> / TOOL: 关键词
    - 两者都失败/都没有 → 返回空列表，调用方静默降级
    """
    if not text:
        return []
    calls = _parse_json_workorder(text)
    if calls:
        return calls
    calls = []
    for m in TOOL_CALL_RE.finditer(text):
        name = (m.group("name1") or m.group("name2") or "").strip()
        name = _TOOL_SYNONYMS.get(name, name)
        params = (m.group("params") or "").strip()
        if name not in TOOL_REGISTRY:
            continue  # 未知工具：不执行（strip 路径仍会移除）
        calls.append(ToolCall(name=name, params=params))
    return calls
```

`strip_tool_calls` 补一层工单块清理（残留 `<tool_calls>` 块不得留给用户可见文本）：

```python
def strip_tool_calls(text: str) -> str:
    """去掉回复中的工具调用标记，保留其余文字（用户可见部分）。

    兜底三层：JSON 工单块 → 文本标签/截断残留 → 裸标签符。
    """
    if not text:
        return text
    s = _TOOL_CALLS_BLOCK_RE.sub("", text)
    s = TOOL_CALL_RE.sub("", s)
    s = _TOOL_RESIDUE_RE.sub("", s)
    return s.strip()
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd /mnt/e/fortune-agent-deploy && /home/a/fortune-agent/.venv/bin/python -m pytest tests/test_capability_registry.py tests/test_tool_calls.py -v -p no:cacheprovider
```

Expected: 全部 passed（新增 6 条 + 既有 tool_calls 测试无回归）。

- [ ] **Step 5: 提交**

```bash
cd /mnt/e/fortune-agent-deploy && git add src/bot/tool_calls.py tests/test_capability_registry.py && git commit -m "feat: structured JSON workorder protocol (batch1 task3)"
```

---

### Task 3B（条件任务）：deepseek 原生 tool_use 增强

**仅当 Task 1 结论为 `SUPPORTED_NATIVE_TOOL_USE` 时执行**；否则跳过本任务并在结论文档标注"3B 未执行"。

**Files:**
- Modify: `src/llm/client.py`（`deepseek_anthropic_completion` 加 `tools`/`tool_choice` 透传）
- Modify: `src/bot/handler.py`（`_run_tool_loop` 原生 tool_use 循环：解析 `content` 中 `tool_use` blocks → 执行 → `role="user"` 带 `tool_result` 回传）
- Test: `tests/test_capability_registry.py`（追加原生循环解析单测，mock client）

**Interfaces:**
- Consumes: Task 1 验证结论、Task 2 注册表、Task 3 工单解析（原生失败时兜底 JSON 工单）
- Produces: `client.py` 的 `deepseek_anthropic_completion(..., tools=None, tool_choice=None)` 可选参数；handler 原生循环优先、JSON 工单兜底

- [ ] **Step 1: client.py 透传 tools/tool_choice**

`deepseek_anthropic_completion` 签名追加 `tools: Optional[list] = None, tool_choice: Optional[dict] = None`，`_anthropic_payload` 签名同步追加，payload 中 `if tools: payload["tools"] = tools`、`if tool_choice: payload["tool_choice"] = tool_choice`。不传时行为与现状完全一致。

- [ ] **Step 2: 失败测试**

```python
def test_native_tool_use_loop(mocker):
    """原生 tool_use 块 → 执行 → tool_result 回传 → 继续出稿。"""
    from src.bot.handler import FortuneBot  # noqa: F401  # 仅验证解析函数
    from src.bot.tool_calls import parse_native_tool_use_blocks
    blocks = parse_native_tool_use_blocks([{
        "type": "tool_use", "id": "tu_1", "name": "web_search",
        "input": {"query": "北京天气"},
    }])
    assert blocks == [{"id": "tu_1", "tool": "搜索", "params_obj": {"query": "北京天气"}}]
```

- [ ] **Step 3: tool_calls.py 加 `parse_native_tool_use_blocks(content: list) -> list`**

```python
def parse_native_tool_use_blocks(content: list) -> List[ToolCall]:
    """解析 Anthropic 协议原生 tool_use 块（content 列表）。

    name 接受英文工具名或中文名，统一归一为注册表中文名；
    若模型输出的工具名不在注册表 → 跳过不执行（程序确认原则）。
    """
    calls = []
    for b in content:
        if not isinstance(b, dict) or b.get("type") != "tool_use":
            continue
        name = str(b.get("name", "")).strip()
        name = TOOL_NAME_BY_ID.get(name, name)
        if name not in TOOL_REGISTRY:
            continue
        params = b.get("input") or {}
        if isinstance(params, str):
            params = {"text": params}
        elif not isinstance(params, dict):
            params = {}
        calls.append(ToolCall(name=name, params_obj=params,
                              tool_use_id=str(b.get("id", ""))))
    return calls
```

（`ToolCall` 追加 `tool_use_id: str = ""` 字段。）

- [ ] **Step 4: handler.py `_run_tool_loop` 原生优先**

LLM 调用改传 `tools=...`（由注册表生成，`build_tool_schema_list()` 新增于 capability_registry.py，与 `build_tool_description` 同源）。`new_reply` 若含原生 `tool_use` 块（client 返回结构需同步——`deepseek_anthropic_completion` 返回纯文本，原生块解析需 client 返回完整 JSON：新增 `deepseek_anthropic_messages(..., tools=..., tool_choice=...)` 返回完整响应 dict 供本循环使用），执行后以 `{"role": "user", "content": [{"type": "tool_result", "tool_use_id": ..., "content": ...}]}` 回传。**原生路径任何一步异常 → 立即降级走 JSON 工单路径**（现有逻辑保留，双协议并存一个过渡期）。

- [ ] **Step 5: 测试 + 提交**

```bash
cd /mnt/e/fortune-agent-deploy && /home/a/fortune-agent/.venv/bin/python -m pytest tests/test_capability_registry.py tests/test_tool_calls.py -q -p no:cacheprovider
git add src/llm/client.py src/bot/tool_calls.py src/bot/handler.py src/bot/capability_registry.py tests/test_capability_registry.py
git commit -m "feat: native tool_use loop for deepseek (batch1 task3b)"
```

---

### Task 4: 执行层改造（注册表分派 + 超时重试 + JSON 回喂）

**Files:**
- Modify: `src/bot/handler.py`
  - `__init__`：`bind_executors` 注入 7 tool lambda + 15 intent handler
  - `_execute_tool_call`（1438-1457）：改注册表分派 + 校验 + 超时重试
  - `_run_tool_loop`（1162-1177）：结果回喂改标准 JSON 包装
  - `process` 内 `handler_map`（2758-2774）：改为注册表 intent 投影
- Test: `tests/test_capability_registry.py`（追加执行层用例）

**Interfaces:**
- Consumes: Task 2 的 `CAPABILITY_BY_NAME/validate_params/bind_executors`，Task 3 的 `serialize_params`
- Produces: `_run_with_timeout(cap, params, user_id, user_question) -> ToolResult`（实例方法）

- [ ] **Step 1: 写失败测试**

```python
def test_execute_invalid_params_not_executed(mocker):
    """参数非法 → 不执行 executor，回 ok:false 参数不合法。"""
    from src.bot.handler import FortuneBot
    bot = mocker.Mock(spec=FortuneBot)
    # 用真实注册表逻辑但隔离 bot 实例方法
    from src.bot.capability_registry import validate_params, CAPABILITY_BY_ID
    assert validate_params("web_search", {}) is not None
    assert CAPABILITY_BY_ID["web_search"].cap_type == "tool"


def test_result_json_wrapper():
    """回喂格式：统一 {"tool","ok","data"/"error"} JSON。"""
    from src.bot.tool_calls import ToolResult
    from src.bot.handler import format_tool_results_json
    ok = ToolResult("搜索", True, "北京：晴 25℃")
    err = ToolResult("排盘", False, "缺少出生信息，请询问", needs_info=True)
    out = format_tool_results_json([ok, err])
    assert '"ok": true' in out and '"data": "北京：晴 25℃"' in out
    assert '"ok": false' in out and '"needs_info": true' in out
```

- [ ] **Step 2: 跑测试确认失败**

Expected: FAIL（`format_tool_results_json` 不存在）。

- [ ] **Step 3: 实现 `format_tool_results_json`（handler.py 模块级函数，放 _run_tool_loop 前）**

```python
def format_tool_results_json(results: list) -> str:
    """工具结果统一 JSON 包装（spec 1.2）：{"tool","ok","data"/"error","needs_info"}。

    以 JSON 行序列化注入 system，LLM 结构化消化（豆包式标准结果回喂）。
    """
    lines = []
    for r in results:
        body = {"tool": r.name, "ok": bool(r.ok)}
        if r.ok:
            body["data"] = r.text
        else:
            body["error"] = r.text
            if r.needs_info:
                body["needs_info"] = True
        lines.append(json.dumps(body, ensure_ascii=False))
    return "\n".join(lines)
```

（handler.py 顶部若未 import json，确认已有——工具日志 `json.dumps` 已在用，必已导入。）

- [ ] **Step 4: `__init__` 注入 executor（handler.py `__init__` 末尾）**

```python
        # 批次 1（spec 1.1）：统一能力注册表注入执行器（lambda 统一签名
        # (params, user_id="", user_question="") -> ToolResult，执行器实现零改动）
        from src.bot.capability_registry import bind_executors
        bind_executors({
            "bazi_chart": lambda p, uid="", uq="": self._tool_bazi(p, uid),
            "quote_rag": lambda p, uid="", uq="": self._tool_search(
                p, user_id=uid, user_question=uq),
            "web_search": lambda p, uid="", uq="": self._tool_web_search(p, user_id=uid),
            "dream": lambda p, uid="", uq="": self._tool_dream(p, uid),
            "fengshui": lambda p, uid="", uq="": self._tool_fengshui(p),
            "zeri": lambda p, uid="", uq="": self._tool_zeri(p, uid),
            "record_lookup": lambda p, uid="", uq="": self._tool_query_records(p, uid),
            "bazi": self._handle_bazi, "ziwei": self._handle_ziwei,
            "liuyao": self._handle_liuyao, "fengshui": self._handle_fengshui,
            "mianxiang": self._handle_mianxiang, "zeri": self._handle_zeri,
            "qimen": self._handle_qimen, "xingming": self._handle_xingming,
            "hehun": self._handle_hehun, "dream": self._handle_dream,
            "calendar": self._handle_calendar, "hourly": self._handle_hourly,
            "xuetang": self._handle_xuetang, "advisor": self._handle_advisor,
            "career": self._handle_career,
        })
```

（各 `_handle_*` 方法签名以 handler.py 实际为准：`(self, msg, user_id, stream_cb=..., session_id=...)`，bind_executors 的 intent 值直接传方法引用，handler_map 投影后调用方式不变。）

- [ ] **Step 5: `_execute_tool_call` 改注册表分派（1438-1457 整段替换）**

```python
    def _execute_tool_call(self, name: str, params: str, user_id: str,
                           user_question: str = "") -> ToolResult:
        """执行单个工具调用，返回可注入对话的结果文本。

        批次 1（spec 1.2）：注册表分派 + 参数校验 + 超时重试。
        - params_obj 有值（结构化工单）：先校验（非法不执行）→ 序列化回文本
        - params 有值（文本标签兜底）：不校验直接执行（旧行为，兼容期）
        - 超时/异常按 retries 重试，耗尽 → 兜底文案
        """
        cap = CAPABILITY_BY_NAME.get(name)
        if cap is None or cap.executor is None:
            return ToolResult(name, False, f"未知工具「{name}」，请直接和用户正常聊天。")
        if isinstance(params, dict):
            err = validate_params(cap.cap_id, params)
            if err:
                return ToolResult(name, False, err)  # 参数不合法：不执行、不计重试
            params = serialize_params(params)
        return self._run_with_timeout(cap, params, user_id, user_question)
```

新增 `_run_with_timeout`（放 `_execute_tool_call` 之后）：

```python
    def _run_with_timeout(self, cap, params: str, user_id: str,
                          user_question: str = "") -> ToolResult:
        """带超时重试执行注册表 executor（线程池包装，不阻塞事件循环）。"""
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
        for attempt in range(max(1, cap.retries + 1)):
            try:
                with ThreadPoolExecutor(max_workers=1) as ex:
                    fut = ex.submit(cap.executor, params, user_id=user_id,
                                    user_question=user_question)
                    return fut.result(timeout=cap.timeout_s)
            except FutTimeout:
                continue  # 超时 → 重试（最后一次循环走失败兜底）
            except Exception:  # noqa: BLE001 — 工具异常重试后兜底
                continue
        return ToolResult(
            cap.name, False,
            f"「{cap.name}」执行超时/异常（已重试{cap.retries}次），"
            "请基于已有信息继续回答，或明确告知用户该能力暂不可用。")
```

（handler.py 顶部 import 加 `from src.bot.capability_registry import CAPABILITY_BY_NAME, CAPABILITIES, validate_params` 和 `from src.bot.tool_calls import serialize_params`。）

- [ ] **Step 6: `_run_tool_loop` 工具调用参数传递改 params_obj（1132-1142 段）**

原调用（`r = self._execute_tool_call(c.name, c.params, user_id, user_question=msg)`）替换为——结构化工单的 dict 参数必须传进执行层，否则校验/序列化永远不会触发：

```python
                r = self._execute_tool_call(
                    c.name,
                    c.params_obj if c.params_obj is not None else c.params,
                    user_id, user_question=msg)
```

- [ ] **Step 7: `_run_tool_loop` 回喂改 JSON 包装（原 1162-1177 段）**

原 `results_text = "\n\n".join(f"【工具：{r.name}】\n{r.text}" ...)` 替换为：

```python
            results_text = format_tool_results_json(results)
```

（system 注入指令文本"不要再次输出 <tool_call> 标签"改为"不要再输出任何工具调用标记（如确有新工具需要，按 <tool_calls> JSON 工单格式输出）"——其余指令文本原样保留。）

- [ ] **Step 8: `process` 的 handler_map 改注册表投影（原 2758-2774 段）**

原 15 行 dict 字面量删除，替换为（`CAPABILITIES` 已在 Step 5 顶部 import）：

```python
        # Step 2: 路由到对应处理器（批次 1：从能力注册表投影，单一事实源）
        handler_map = {c.cap_id: c.executor for c in CAPABILITIES
                       if c.cap_type == "intent" and c.executor}
```

（`handler_map.get(analysis.intent)` 调用方式不变。若任一 intent 未绑定 executor（__init__ 未执行场景），`c.executor` 为 None 会被过滤，行为等同原"未开放"分支。）

- [ ] **Step 9: 测试 + 回归**

```bash
cd /mnt/e/fortune-agent-deploy && /home/a/fortune-agent/.venv/bin/python -m pytest tests/test_capability_registry.py tests/test_tool_calls.py -v -p no:cacheprovider
/home/a/fortune-agent/.venv/bin/python -m pytest tests/test_bot.py -q -p no:cacheprovider 2>&1 | tail -3
```

Expected: 新增用例全过；test_bot.py 失败集与基线一致（不得新增）。

- [ ] **Step 10: 提交**

```bash
cd /mnt/e/fortune-agent-deploy && git add src/bot/handler.py && git commit -m "feat: registry dispatch + timeout retry + JSON result feed (batch1 task4)"
```

---

### Task 5: 意图与工具一体化提示（同源改造）

**Files:**
- Modify: `src/engines/message_analyzer.py`（COMBINED_PROMPT 枚举行同源）
- Modify: `src/bot/handler.py`（`_tool_loop_analysis_hint` 搜索引导改 JSON 工单格式；`_run_tool_loop` system 注入工具说明书）
- Test: `tests/test_capability_registry.py`（追加一致性用例）

**Interfaces:**
- Consumes: Task 2 的 `build_intent_enum_line()`、`build_tool_description()`；Task 3 的 JSON 工单格式
- Produces: 无新接口（纯内部改造）

- [ ] **Step 1: 写失败测试**

```python
def test_combined_prompt_enum_same_source():
    """COMBINED_PROMPT 枚举行由注册表生成，且与原文一致、无占位符残留。"""
    from src.engines.message_analyzer import COMBINED_PROMPT
    assert "__INTENT_ENUM__" not in COMBINED_PROMPT
    assert ("Classify into EXACTLY ONE: bazi, ziwei, liuyao, fengshui, zeri, "
            "mianxiang, qimen, xingming, hehun, dream, calendar, advisor, "
            "career, free_chat") in COMBINED_PROMPT


def test_tool_description_built():
    """工具说明书生成：7 工具齐、含 cap_id 与超时参数。"""
    from src.bot.capability_registry import build_tool_description
    d = build_tool_description()
    for cid in ("bazi_chart", "web_search", "quote_rag", "dream",
                "fengshui", "zeri", "record_lookup"):
        assert cid in d
    assert "8s" in d and "重试1次" in d
```

- [ ] **Step 2: 跑测试确认失败**

Expected: FAIL（`__INTENT_ENUM__` 未出现——现有 prompt 是字面量，测试要求同源标记先失败；工具说明书断言 8s 也失败）。

- [ ] **Step 3: message_analyzer.py 枚举行同源**

COMBINED_PROMPT 第 53 行原文替换为占位符 + 模块级 replace（避免 f-string 化整段转义大括号的回归风险）：

```python
from src.bot.capability_registry import build_intent_enum_line  # 文件头 import 区
```

COMBINED_PROMPT 中：

```
## 2. Intent Classification
Classify into EXACTLY ONE: __INTENT_ENUM__
```

（原 "bazi, ziwei, liuyao, ... free_chat" 一行替换为 `__INTENT_ENUM__`。）

COMBINED_PROMPT 定义之后追加：

```python
COMBINED_PROMPT = COMBINED_PROMPT.replace("__INTENT_ENUM__", build_intent_enum_line())
```

**注意**：handler.py 等别处也可能 import 此模块——替换在模块加载时完成，行为与原先完全一致（生成结果 == 原文，由测试锚定）。

- [ ] **Step 4: handler.py 搜索引导改 JSON 工单（_tool_loop_analysis_hint 1062-1067 段）**

原 `"请先输出一次 <tool_call>搜索: 具体关键词</tool_call> 获取实时信息，..."` 替换为：

```python
                    hints.append(
                        "【实时信息】此问题依赖实时信息（公司/行业/时事/最新数据）。"
                        "请先输出一次 <tool_calls>[{\"tool\": \"web_search\", "
                        "\"params\": {\"query\": \"具体关键词\"}}]</tool_calls> "
                        "获取实时信息，再基于搜索结果继续回答，不要凭记忆编造行业现状数据；"
                        "若搜索不可用，则明确告知用户"
                        "「实时信息暂不可用，以下按命理知识分析」。"
                    )
```

- [ ] **Step 5: `_run_tool_loop` system 注入工具说明书（1151-1155 段 system 消息）**

首条 system 内容末尾追加：

```python
                "你是易理明灯，请基于工具执行结果继续自然地完成你的回复。"
                "回答纪律：直接专业作答，禁止油滑/套近乎开场白；"
                "严格紧扣用户问题，用户没问的（名人相似、旁支话题）不得主动展开。"
                + self._tool_loop_analysis_hint(analysis)
                + "\n\n" + build_tool_description()
```

（handler.py 顶部 import `build_tool_description`。）

- [ ] **Step 6: 测试 + 回归**

```bash
cd /mnt/e/fortune-agent-deploy && /home/a/fortune-agent/.venv/bin/python -m pytest tests/test_capability_registry.py tests/test_message_analyzer.py -v -p no:cacheprovider 2>&1 | tail -5
/home/a/fortune-agent/.venv/bin/python -m pytest tests/test_bot.py -q -p no:cacheprovider 2>&1 | tail -3
```

Expected: 新增用例全过；message_analyzer/bot 既有测试失败集不变。

- [ ] **Step 7: 提交**

```bash
cd /mnt/e/fortune-agent-deploy && git add src/engines/message_analyzer.py src/bot/handler.py tests/test_capability_registry.py && git commit -m "feat: single-source intent/tool descriptions (batch1 task5)"
```

---

### Task 6: 全量回归 + 基线对照

**Files:** 无代码改动，纯验证。

- [ ] **Step 1: 全量跑基线命令**

```bash
cd /mnt/e/fortune-agent-deploy && timeout 1200 /home/a/fortune-agent/.venv/bin/python -m pytest tests/ -q -p no:cacheprovider --continue-on-collection-errors 2>&1 | tail -5
```

Expected: `1191 passed / 0 failed / 7 skipped / 0 errors`（新增的 test_capability_registry.py 用例计在新 passed 之上，总数 = 1191 + 新增条数；**通过集不减少、失败集不新增**）。
挂起时按文件跑 for 循环（见 Global Constraints）。

- [ ] **Step 2: 失败集逐名对照**

任何新增失败 → 定位根因修复后才可继续；flaky 判定按基线规则（单独重跑 3 次全过才可标环境 flaky）。

- [ ] **Step 3: 提交（若 Step 1/2 有修复）**

```bash
git add -A && git commit -m "fix: batch1 regression fixes"
```

---

### Task 7: 生产灰度上线 + 复测

**Files:** 无代码改动，纯部署验证（红线流程）。

- [ ] **Step 1: rsync 到生产（禁 --delete）**

```bash
cd /mnt/e/fortune-agent-deploy && rsync -a --exclude='.git' --exclude='.venv' --exclude='__pycache__' --exclude='.superpowers' --exclude='docs' ./ /home/a/fortune-run/
```

- [ ] **Step 2: 停旧进程（pkill 必须单独一条命令，防自杀）**

```bash
pkill -f "fortune-run"
```

- [ ] **Step 3: 启动（必须显式 cd /home/a/fortune-run）**

```bash
cd /home/a/fortune-run && nohup /home/a/fortune-agent/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8767 > start.log 2>&1 &
```

（启动命令以生产实际入口为准——检查 /home/a/fortune-run 现有启动方式后复刻。）冷启动 ~140s：**等待 150s 后才判定，不早判死**。

- [ ] **Step 4: 健康检查 + 复测**

```bash
sleep 150 && curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8767/health
# 旧协议文本标签（兼容期必须仍工作）
/tmp/chat_test.sh "dd2b3b17feae1dc0093cde4a45e8d3a2" "帮我查一下我的档案里有什么" "batch1-t1" "probe_user_xyz"
# 排盘主链（F2 回归）
/tmp/chat_test.sh "dd2b3b17feae1dc0093cde4a45e8d3a2" "1999年5月13日 10:55 北京 男 帮我排个盘" "batch1-t2" "qa_v2_user"
# 需要搜索的行业问题（工具链）
/tmp/chat_test.sh "dd2b3b17feae1dc0093cde4a45e8d3a2" "教育行业现在怎么样？适合我发展吗？" "batch1-t3" "probe_user_xyz"
```

Expected: health 200；三条复测均正常出稿、无 `<tool_call>`/`<tool_calls>` 标签残留、排盘正确。

- [ ] **Step 5: 台账记录 + 提交**

`.superpowers/sdd/progress.md` 追加：

```
## 批次 1（2026-08-26）
- Task 1-7 完成（commits ...）；基线对照通过（1191+X / 0 / 7 / 0）
- Task 1 结论：<SUPPORTED_NATIVE_TOOL_USE / NOT_SUPPORTED>；3B <执行/未执行>
- 生产部署验证：健康检查 200，三条复测通过
- 验收：选工具零标签残留、参数非法不执行、超时失败有兜底、意图与工具说明书同源
```

```bash
cd /mnt/e/fortune-agent-deploy && git add .superpowers/sdd/progress.md && git commit -m "docs: batch1 deployment verification ledger"
```
