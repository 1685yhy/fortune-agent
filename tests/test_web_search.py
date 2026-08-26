# -*- coding: utf-8 -*-
"""C-3 搜索质量：长 query 关键词精简策略（Task B3 条目 17，web_search.py 首次改动）。

背景：长中文 query（如「2026年教育行业政策 最新动向 双减 职业教育」）整段
提交 Bing 常返回垃圾结果——堆叠修饰词稀释相关性。修复：_simplify_query 纯
规则精简（剥请求前缀/问句尾巴、切段、移除修饰性堆叠段、保留前 2-3 内容段），
_search_bing 用精简后的 query 构造请求。

红线（批次 2）：web_search.py 首次触碰必须有单测 + 真实 API 冒烟
（冒烟见 /tmp/b3_web_search_smoke.py，glm-4-flash 免费模型，key 只从环境变量读）。
"""
import sys
from datetime import date
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import src.rag.web_search as ws  # noqa: E402


# ---------------------------------------------------------------- 精简函数


def test_simplify_long_stacked_chinese_query():
    """台账原句：「2026年教育行业政策 最新动向 双减 职业教育」→ 去堆叠、留 3 内容段。

    用历史年份（2020）——当前年份剥离是独立规则（见年份专项测试），
    此处聚焦堆叠段剥离。
    """
    out = ws._simplify_query("2020年教育行业政策 最新动向 双减 职业教育")
    assert out == "2020年教育行业政策 双减 职业教育"
    assert "最新动向" not in out


def test_simplify_removes_stop_segments():
    """修饰性堆叠段整段剥离（最新/最近/怎么样/情况…），内容段保留。"""
    assert ws._simplify_query("2024年属猴运势 最新 怎么样") == "2024年属猴运势"
    assert ws._simplify_query("苏州 天气 情况 今天") == "苏州 天气"
    assert ws._simplify_query("双减政策 最新消息 有哪些") == "双减政策"


def test_simplify_short_query_unchanged():
    """短 query（≤1 段）原样返回——精简只针对长句堆叠场景。"""
    assert ws._simplify_query("北京天气") == "北京天气"
    assert ws._simplify_query("1990年5月20日午时") == "1990年5月20日午时"


def test_simplify_empty_and_none():
    """空/None → 空串（不崩，调用方按原逻辑处理）。"""
    assert ws._simplify_query("") == ""
    assert ws._simplify_query(None) == ""
    assert ws._simplify_query("   ") == ""


def test_simplify_strips_request_prefix():
    """句首请求/语气前缀剥离：请问/帮我查…（前缀后单段 → 内容段原样）。"""
    assert ws._simplify_query("请问1998年教育行业政策") == "1998年教育行业政策"
    assert ws._simplify_query("帮我查一下2020年高考分数线") == "2020年高考分数线"
    assert ws._simplify_query("帮我搜 2020年高考分数线") == "2020年高考分数线"


def test_simplify_strips_question_tail():
    """句尾问句尾巴剥离（…是什么/…怎么样/…怎么办），内容主体保留。"""
    assert ws._simplify_query("1998年教育行业政策是什么") == "1998年教育行业政策"
    assert ws._simplify_query("属猴的人今年运势怎么样") == "属猴的人今年运势"
    assert ws._simplify_query("八字不合怎么办") == "八字不合"


def test_simplify_all_stop_segments_fallback_keeps_first():
    """极端：全段均为修饰段 → 保留第一段（绝不为空，宁多不空）。"""
    out = ws._simplify_query("最新动向 怎么样 情况")
    assert out  # 非空即可，保第一段


def test_simplify_max_keep():
    """max_keep=2 → 只留前 2 个内容段（brief：长 query 降为 2-3 关键词）。"""
    assert ws._simplify_query(
        "2020年教育行业政策 双减 职业教育", max_keep=2) == "2020年教育行业政策 双减"


def test_simplify_glued_sentence_with_tail():
    """无空格长句（LLM 常见输出形态）：剥问句尾巴后成单段 → 内容主体保留。

    历史年份（2020）排除年份规则干扰，聚焦「剥尾巴后单段原样返回」。
    """
    out = ws._simplify_query("2020年教育行业的最新动向是什么")
    assert out == "2020年教育行业"


# ------------------------------------------------- 当前年份专题块专项
# 2026-08-27 真实抓取复现：Bing 对含当前年份的 query 返回「年份专题」垃圾块
# （百科/日历/节假日…），实质忽略其他检索词——'2026年教育行业政策' 的 top3
# 全是 2026 年百科/节假日通知/日历表。策略：当前年份前缀/整段剥离。


def test_simplify_current_year_prefix_stripped():
    """粘连形态（LLM 实际输出）：「2026年教育行业政策 最新动向 …」→ 年份前缀剥离。"""
    cur = f"{date.today().year}年"
    out = ws._simplify_query(f"{cur}教育行业政策 最新动向 双减政策 职业教育 影响")
    assert out == "教育行业政策 双减政策 职业教育"
    assert cur not in out


def test_simplify_current_year_seg_dropped():
    """空格分隔形态：「2026年 属猴 运势」→ 年份段剥离。"""
    cur = f"{date.today().year}年"
    assert ws._simplify_query(f"{cur} 属猴 运势") == "属猴 运势"


def test_simplify_trailing_current_year_seg_dropped():
    """年份在句尾：「教育政策 2026年 最新」→ 年份段仍剥离。"""
    cur = str(date.today().year)
    assert ws._simplify_query(f"教育政策 {cur}年 最新") == "教育政策"


def test_simplify_historical_year_kept():
    """历史年份（1999年…）无年份专题块问题且是真实内容词 → 保留。"""
    assert ws._simplify_query("1999年 诺贝尔 文学奖") == "1999年 诺贝尔 文学奖"


def test_simplify_year_only_query_kept():
    """纯年份 query（「2026年」）→ 保留（本就查询年份本身，剥离会变空 query）。"""
    assert ws._simplify_query(f"{date.today().year}年") == f"{date.today().year}年"


# ------------------------------------------------- 人工智能 → AI 同义改写
# 2026-08-27 冒烟复现：Bing CN 对含「人工智能」的 query 返回「人工」词典释义
# 垃圾块（百科/读音/组词），引擎侧分词缺陷——任何长度精简都绕不开；同义
# 改写为「AI」实测绕过（'AI 就业形势' 返回真实 AI 行业结果）。


def test_simplify_ai_synonym_rewrite_multi_seg():
    """多段 query：「人工智能 就业形势」→「AI 就业形势」（绕过 Bing『人工』缺陷）。"""
    assert ws._simplify_query("人工智能 就业形势") == "AI 就业形势"
    assert ws._simplify_query("人工智能行业 就业 建议") == "AI行业 就业 建议"


def test_simplify_ai_synonym_kept_for_single_seg():
    """单段「人工智能」→ 原样保留（query 主题即该词本身，不改写）。"""
    assert ws._simplify_query("人工智能") == "人工智能"


# ------------------------------------------------------------ 请求构造集成


def test_search_bing_sends_simplified_query(monkeypatch):
    """_search_bing 请求 URL 的 q 参数必须用精简后的 query（无堆叠修饰段）。"""
    import httpx

    captured = {}

    class _Resp:
        status_code = 200
        text = "<html><body><ol><li class=\"b_algo\"><h2><a href=\"https://example.com/a\">教育行业政策报告</a></h2><p>2026年教育行业政策摘要</p></li></ol></body></html>"

        def raise_for_status(self):
            return None

    def fake_get(url, **kw):
        captured["url"] = url
        return _Resp()

    monkeypatch.setattr(httpx, "get", fake_get)
    results = ws._search_bing("2026年教育行业政策 最新动向 双减 职业教育")
    assert captured["url"].startswith(ws.BING_SEARCH_URL + "?q=")
    from urllib.parse import quote, unquote
    q = unquote(captured["url"].split("?q=", 1)[1].split("&", 1)[0])
    assert "最新动向" not in q
    assert "教育行业政策" in q
    assert "双减" in q and "职业教育" in q
    assert results and results[0]["title"] == "教育行业政策报告"


def test_search_bing_short_query_unchanged(monkeypatch):
    """短 query 原样提交（精简不动单段 query）。"""
    import httpx

    captured = {}

    class _Resp:
        status_code = 200
        text = "<html><body></body></html>"

        def raise_for_status(self):
            return None

    def fake_get(url, **kw):
        captured["url"] = url
        return _Resp()

    monkeypatch.setattr(httpx, "get", fake_get)
    ws._search_bing("北京天气")
    from urllib.parse import unquote
    assert "北京天气" in unquote(captured["url"])
