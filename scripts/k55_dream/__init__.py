"""k55 批次：解梦语料扩充（多源爬虫 + 清洗入库 + 规则层统计）。

模块划分：
- crawl_lib   : 合规抓取基础库（robots 门禁 / 限速 / 重试 / 断点续爬 / 统一输出）
- adapters.*  : 各站适配器（列表页 + 详情页解析，统一输出 {title, content, source}）
- stats_elements : 语料元素词频统计（规则层依据）
- build_rules    : Top-N 统计结果 → 规则层模式条目
- clean_dedupe   : 跨站去重 / 噪音清洗 / 编码修复 / 最短长度过滤
"""
