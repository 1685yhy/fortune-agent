# 阶段2 门禁报告
e2e 考卷 25/25 全过

- 举证冒烟: 真实检索 0 条
- LLM冒烟: 跳过(无 DEEPSEEK_API_KEY)


## 举证冒烟 0 条根因留证（如实记录，不凑绿）

门禁脚本按简报逐字执行，真实输出为"举证冒烟: 真实检索 0 条"。已排查定位根因：

1. **默认 collection 指向空库**：`src/rag/retriever.py:56` 未设 `EMBEDDING_COLLECTION` 时
   取默认 `fortune_books`（chroma 计数 0 条）；真实数据 27115 条全在 `fortune_books_v2`。
2. **category 名不精确匹配**：`src/engine/evidence.py:47` `gather()` 硬编码
   `category="bazi"`，而库内命例类别实际为 `bazi_case`（4934 条），chroma where
   精确匹配失败 → 向量检索与 BM25 双路均 0 命中。
3. **对照验证（链路本身可用）**：`EMBEDDING_COLLECTION=fortune_books_v2` +
   `category="bazi_case"` 重跑 `EvidenceProvider().gather` → 真实检索 5 条，
   score 0.67+（0.6753/0.6752/0.6734），内容与推演要点"伤官见官"精准相关；
   bge-m3 模型加载正常（391 weights）。

结论：引擎证据层链路功能正常，0 条源于检索侧默认配置与库实际（collection 名/category 名）
不一致。此为需上游裁决的问题（evidence.py 默认参数或库端配置），非引擎回归 FAIL，
按硬闸原则如实留证，不改代码凑绿。

## 修复记录（2026-08-15 阶段2裁决）

默认配置缺陷已修（裁决修复提交）：

1. **默认分类对齐库内**：`evidence.py` 默认 `category` 由 `"bazi"` 改为 `"bazi_case"`
   （`fortune_books_v2` 全量精确实测：`bazi_case`=4934 条，`bazi`=0 条），
   `gather(category=...)` 保持可覆盖。
2. **空库守卫**：`_get_retriever()` 在构造真实 Retriever 前检查 `EMBEDDING_COLLECTION`，
   未显式设置（默认指向空库 `fortune_books`，count=0）时抛 `RuntimeError` 并给出
   可操作指引（`fortune_books_v2`，27115 条古籍库）；`compose_report` 的 try/except
   降级 [] 不受影响，直接调用方得到明确报错。
3. **coverage 步数修正**：`deduction_steps` 7→6，注明"第 7 步'大运流年.engine'仅
   engine_result 非空时条件追加"（pills-only 链实际 6 步）。
