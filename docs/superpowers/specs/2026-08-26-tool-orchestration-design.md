# 前置编排层优化设计：统一能力注册表 + 结构化工单 + 分诊路由

> 日期：2026-08-26 ｜ 状态：待评审 ｜ PM 已拍板方向（方案一：分批全建，质量优先、速度随后）

## 背景与问题

PM 反馈（F1/F2 系列问题的共性根子）：AI 收到输入后，对"什么时候调用哪个工具、什么时候该动大模型、怎么返回值"决策不稳——工具选不对、参数靠猜、已给信息不累积。对照豆包/元宝/行业通用做法调研（见 `.superpowers/sdd/research-tool-orch-20260826.md`），差距有三：

1. **工具协议落后**：现在 AI 调工具靠"回答里写 `<tool_call>工具名: 参数</tool_call>` 文字标签 + 正则解析"（src/bot/tool_calls.py:24-31、handler.py:1438）——无参数 schema、无校验、无超时重试。豆包类产品用结构化 tool_call + 工具注册表（name/description/parameters）+ 标准结果回喂。
2. **两套描述脱节**：意图列表（COMBINED_PROMPT 14 类）与 TOOL_REGISTRY（tool_calls.py:80-117，8 工具）互不关联，新增能力改两处，AI 没有统一的"能力说明书"。
3. **调用链冗余 + 无分诊体系**：一条排盘请求 4-6 次 LLM 调用、端到端 20~40s（intent 0.9s + instant 1.3s + main_analysis 8~10s + polish 1.4~4.8s + tool_loop 0~11s，日志实测 2026-08-26）。快路径已有雏形（存量直读 0s、重看盘、ResponseCache、预生成）但零散，未成"分诊路由"体系（行业标准：常见事务走快通道 <1s 不惊动 AI，疑难走慢通道，快转慢兜底）。

## 设计总览

分两批独立上线，每批独立验证、零回归护栏：

- **批次 1（质量根子）**：统一能力注册表 + 结构化工单协议（JSON 工单 + 参数校验 + 超时重试 + 标准结果回喂）
- **批次 2（速度）**：分诊路由（快慢通道 + 兜底链）+ LLM 调用压缩（并行/合并 + 硬上限）+ 语义缓存

计算层（排盘引擎 BaziEngine/择吉/古籍金句）、数据层（DAO/加密/鉴权）、存储结构**全部不动**。

---

## 批次 1：统一能力注册表 + 结构化工单协议

### 1.1 统一能力注册表（新文件 src/bot/capability_registry.py）

把意图描述与工具描述合并为一份能力说明书。条目结构：

```python
@dataclass(frozen=True)
class Capability:
    cap_id: str            # 如 "bazi_chart", "web_search", "record_lookup", "quote_rag", "dream", "fengshui", "zeri", "calendar"
    name: str              # 短名，LLM 可见
    description: str       # 干什么用 + 何时用 + 何时不用（决策说明书）
    params_schema: dict    # JSON Schema（必填/类型/枚举/范围）
    executor: Callable     # 现有处理函数引用（不改实现）
    timeout_s: float       # 单次执行超时，默认 8.0
    retries: int           # 失败重试次数，默认 1
    requires: list[str]    # 前置条件（如 bazi_chart requires 年/月/日/时）
```

要点：
- 条目内容**逐条来自现有代码**：TOOL_REGISTRY 的 8 个工具的 desc 原文迁移 + 意图分发表（handler.py:2758-2774）的意图描述补全——产出 `CAPABILITIES` 注册表；现有 TOOL_REGISTRY 保留为薄壳（`build_tool_list()` 从注册表投影，不改其消费方签名，回归零冲击）
- 新增能力 = 加一条记录，一处维护
- 导出 `CAPABILITY_DESCRIPTIONS`（给 LLM 的说明书文本，intent 提示词与 tool 提示词共用）

### 1.2 结构化工单协议（替换文本标签协议）

**输出格式**（LLM 在回复末尾输出独立代码块）：

```
<tool_calls>
[{"tool": "web_search", "params": {"query": "..."}}, ...]
</tool_calls>
```

- 解析：优先 JSON 解析（`tool_calls.py` 内新增 `parse_tool_calls(text)`）；解析失败按现有正则兜底（兼容老格式一个过渡期）；两者都失败 → 视为无工具调用
- **参数校验**：`validate_params(cap_id, params)` 按 params_schema 校验；非法 → 该工单标记 `{"ok": false, "error": "参数不合法: ..."}` 喂回 LLM，不计重试
- **执行**：超时（timeout_s）+ 重试（retries）；结果统一包装：

```python
{"ok": True, "data": <执行结果>, "tool": "web_search"}
{"ok": False, "error": "超时(8s)", "tool": "web_search"}
```

- **回喂**：以 system 消息注入上一轮消息后，LLM 可换工具、修参数或直接给最终回答；循环上限沿用 MAX_TOOL_ITERATIONS=2（不放开）
- **失败兜底**：重试耗尽 → 直接告诉 LLM"该工具不可用，请基于已有信息回答"，不再循环

### 1.3 意图与工具一体化提示

- intent 识别提示词（message_analyzer.py）的意图清单改由 `CAPABILITY_DESCRIPTIONS` 生成（同一份说明书，两端引用同一来源）
- 工具调用提示词（tool_loop 的 system 注入）同样引用注册表——消除"两套描述"漂移

### 1.4 批次 1 测试（新文件 tests/test_capability_registry.py）

1. 注册表完整性：CAPABILITIES 覆盖原 TOOL_REGISTRY 全部 8 工具 + 意图分发表（handler.py:2758-2774）全部意图分支（数量以代码为准，无遗漏、无孤儿）
2. 工单解析：合法 JSON 工单/多工单/非法 JSON 兜底正则/两者皆失败→无工具
3. 参数校验：缺必填/类型错/枚举外 → ok:false 不执行
4. 超时重试：桩 executor 超时 → 重试 1 次 → 失败兜底文案
5. 结果回喂格式：统一包装 ok/data/error
6. 回归：test_bot.py 基线逐名一致（22 failed / 43 passed 与 F1 基线相同）、test_card_mark.py、test_partial_birth.py 全绿

---

## 批次 2：分诊路由 + LLM 调用压缩 + 语义缓存

### 2.1 分诊路由层（handler.py 新增 `_route_request`，入口在 process 早期）

决策顺序（纯代码，零 LLM）：

```
1. 快通道候选（按优先级）：
   a. 缓存命中（ResponseCache 升级版，见 2.3）→ 直接回
   b. 存量直读（record_query.direct_query，已有）→ 直接回
   c. 重看盘（_try_reuse_chart，已有）→ 直接回
   d. 标准答案库（新增 common_answers.py：常见问法→固定精答，见 2.2）
   e. 意图明确 + 档案齐 + 无需要检索的 → 引擎直算 + 模板出稿（跳过 intent/instant/polish 部分环节）
2. 兜底：快通道全未命中或结果不满足 → 转慢通道（现状全链路，含 tool_loop）
3. 降级链沿用：downgraded → lite 链（不动）
```

判定输入：意图（可先走规则预判：BIRTH_DATE_PATTERN 等零 LLM 判 bazi 已有）、用户档案有无、会话是否命中快件缓存、消息是否为常见问法。**任何不确定都转慢通道**（宁慢勿错）。

### 2.2 标准答案库（新增 src/bot/common_answers.py）

- 收集高频常见问题（产品侧给 20~30 条起步：什么是八字/算命准吗/怎么看我的盘/服务收费等）→ 人工精写固定答案（含品牌口径）
- 匹配：关键词规则 + 可选 embedding 相似度；命中 → 快通道直出（保留问候/上下文组装，零 LLM）
- 未命中 → 慢通道，不影响现有行为

### 2.3 LLM 调用压缩

- **main_analysis 与 polish 并行化**：polish 依赖 main_analysis 输出（引擎结果注入）——改为 polish 提示词直接吞引擎原始结果 + 一次性出稿（合并为 1 次调用）；或保留 2 次但流式并行出稿（首批选前者：合并，改动最小）
- **instant 回复**：非流式场景并行隐藏（已有）；流式场景保留（即时反馈体验）
- **硬上限**：每次请求主链 LLM 调用 ≤4 次（intent 1 + main 1 + tool_loop ≤2），超限强制出稿（用已得内容，不再补调用）；instant 为并行隐藏调用（flash），不计入主链上限
- **语义缓存**：现有 ResponseCache（src/utils/cache.py LRU 500 条 1h）升级：
  - 键 = 意图 + 会话画像哈希 + 语义向量（bge-m3 已有）相似度 > 0.40 视为命中
  - **仅对通用知识类启用**（意图 ∈ {通用问答/古籍金句/常见问题}），排盘/择吉/个性化绝对实时算（强个性化场景行业共识不缓存）
  - TTL 沿用 1h；不碰加密存储、不落库

### 2.4 批次 2 测试

1. 路由决策表：构造各场景（缓存命中/直读/重看盘/标准答案/意图明确档案齐/全未命中）断言走对通道
2. 快转慢兜底：快通道条件成立但结果异常 → 转慢通道正常出稿
3. 调用计数：mock LLM client 断言一次排盘请求 LLM 调用 ≤4 次
4. 语义缓存：相似问法命中（>0.40）、个性化意图不命中、TTL 过期失效
5. 回归：test_bot.py 基线逐名一致、其余测试全绿

---

## 红线（两批共同）

- 不动鉴权/归属/加密/存储结构/DAO 表结构（api_security_redline 全适用）
- 计算层（BaziEngine/zeri/jian_quote）零改动；`_extract_bazi_info` 等既有提取器行为不变
- 测试基线：test_bot.py 失败集逐名一致（22 failed / 43 passed，与 F1 基线相同）；test_card_mark.py（28 条）、test_record_query.py、test_partial_birth.py（38 条）全绿
- 批次独立交付：批次 1 完成验证后先上线运行稳定，再开批次 2
- 快通道宁慢勿错：任何不确定一律转慢通道
- 语义缓存不碰个性化结果；缓存键含用户隔离

## 验收标准

- 批次 1：选工具零"文本标签残留"（tool_calls.py:18-23 的老修复点可移除兜底验证）；参数非法不执行；超时/失败有兜底文案；意图与工具说明书同源
- 批次 2：普通排盘端到端 ≤15s（现 20~40s）；快通道场景 ≤2s；单请求 LLM 调用 ≤4 次；通用问答语义缓存命中返回 <1s

## 待拍板/观察项（不影响开工）

- 产品侧收集标准答案库 20~30 条内容（批次 2 前置依赖，PM/运营提供或我先按现有知识库起草）
- 语义缓存假阳性观察：上线后两周内统计命中后用户追问率
- 工具超时对慢网络（用户端）无影响——工具超时只发生在服务端执行，不涉及用户网络
