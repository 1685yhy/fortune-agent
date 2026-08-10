#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
工具循环 + 引用注册覆盖测试
（修复：有明确意图的问题也走 LLM 自主生成 + 工具循环 + 引用注册）
============================================================================

背景（docs/CONVERSATION_SYSTEM_DESIGN_V2.md v7，修复方案 B·引擎结果注入）：
  修复前：intent 非 None → 硬路由 _handle_*（不触发 <tool_call> 工具循环、
         不注册引用）；只有 intent=None 的自由对话走 _run_tool_loop。
  修复后：intent 非 None（除 xuetang/advisor/confidant 独立对话模式）→
         _handle_* 完成引擎计算 → 结果作为「引擎草稿」注入 system →
         LLM 以豆包式语气二次生成（_polish_with_engine_draft）→
         _run_tool_loop（润色中仍可输出 <tool_call> 补检索古籍/网络）。

C 组（hermetic · mock LLM，不依赖网络/真实 API）：
  C1  有意图（dream）也走 LLM 二次生成 + 引用注册：'梦见蛇'
      → 引擎草稿注入（LLM 二次生成被调用）→ 润色回复 + book 引用
  C2  工具循环触发：'古籍里怎么说梦见蛇'（intent=free_chat）
      → <tool_call>检索 → 真实 FAISS 执行 → book 引用出现 + 落库 tool_calls
  C3  排盘引擎结果注入 + 引用注册（type=engine）：'帮我排盘 1990年5月20日午时北京男'
  C4  信息收集不润色：'解梦'（无梦境内容）→ 原样返回、无额外 LLM 调用
  C5  无意图闲聊回归：'你好呀' → 正常回复、无引用
  C6  特殊模式回归：学堂 / AI 建议 / 心事树洞 不被引擎润色改动
  C7  流式：ChatStreamer（/api/chat/stream 编排器）done 事件带 citations

D 组（真实 LLM · 需要 API key，缺失自动跳过）：
  D1  '梦见蛇是什么意思' → 引用注册（book 解梦古籍）
  D2  '帮我排盘 1990年5月20日午时北京男' → 引用注册（type=engine）
  D3  '古籍里怎么说梦见蛇' → 工具循环触发 → 引用注册（book）

用法：
  .venv/bin/python scripts/test_tool_coverage.py            # C + D
  .venv/bin/python scripts/test_tool_coverage.py --group C  # 只跑 hermetic 组
退出码：0=全部通过；1=有失败（跑完全部再汇总）
"""
import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import List, Tuple

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from src.config import load_settings
from src.engines.bazi import BaziEngine
from src.engines.ziwei import ZiweiEngine
from src.engines.liuyao import LiuyaoEngine
from src.engines.fengshui import FengshuiEngine
from src.engines.mianxiang import MianxiangEngine
from src.engines.zeri import ZeriEngine
from src.engines.dream import DreamEngine
from src.engines.hehun import HehunEngine
from src.engines.qimen import QimenEngine
from src.engines.xingming import XingmingEngine
from src.rag.embedder import Embedder
from src.rag.retriever import Retriever
from src.llm.client import FortuneLLM
from src.bot.handler import MessageHandler
from src.storage.dao import UserDAO
from src.storage.member_dao import MemberDAO
from src.storage.session_dao import SessionDAO

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = ""):
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail[:200]}" if detail and not cond else ""))
    (PASS if cond else FAIL).append(name)


# ---------------------------------------------------------------------------
# 装配本地 handler（镜像 src/main.py lifespan，仅测试用）
# ---------------------------------------------------------------------------

def build_handler() -> Tuple[MessageHandler, str]:
    settings = load_settings()
    api_key = settings.claude_api_key or os.environ.get("ANTHROPIC_API_KEY") or ""
    db_path = str(settings.db_path)

    engine = BaziEngine()
    ziwei = ZiweiEngine()
    liuyao = LiuyaoEngine()
    fengshui = FengshuiEngine()
    mianxiang = MianxiangEngine()
    zeri = ZeriEngine()
    dream = DreamEngine()
    hehun = HehunEngine()
    qimen = QimenEngine()
    xingming = XingmingEngine()

    embedder = Embedder(model_name=settings.embedding_model)
    # 测试用真实向量库：vectordb_v2/fortune_books（27k 条，检索可出结果）
    retriever = Retriever(str(Path("/mnt/d/fortune-data/vectordb_v2")), embedder)
    retriever._collection_name = "fortune_books"

    dao = UserDAO(db_path)
    member_dao = MemberDAO(db_path)
    session_dao = SessionDAO(db_path)

    llm = FortuneLLM(api_key=api_key, model="deepseek-v4-flash",
                     deep_model="deepseek-v4-flash", provider="deepseek")

    handler = MessageHandler(
        engine, ziwei, liuyao, fengshui, mianxiang, zeri,
        retriever, llm, dao, dream_engine=dream,
        hehun_engine=hehun, qimen_engine=qimen, xingming_engine=xingming,
        session_dao=session_dao, member_dao=member_dao,
    )
    return handler, api_key


def ensure_paid(user_id: str, member_dao: MemberDAO):
    """把测试用户置为 basic 会员（含续期），避免免费 3 次/日额度限制影响测试。"""
    try:
        conn = member_dao._connect()
        conn.execute(
            """INSERT INTO memberships
               (user_id, plan, started_at, expires_at, queries_used, queries_limit, auto_renew)
               VALUES (?, 'basic', datetime('now'), datetime('now','+30 days'), 0, 500, 0)
               ON CONFLICT(user_id) DO UPDATE SET plan='basic', queries_limit=500,
                   started_at=datetime('now'), expires_at=datetime('now','+30 days'), auto_renew=0""",
            (user_id,),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Mock LLM：按消息内容路由的 fake completion（覆盖所有 LLM 入口）
# ---------------------------------------------------------------------------

def make_fake(cfg: dict):
    """cfg 各分支（按消息内容命中）：
      analysis_json   意图分析（MessageAnalyzer）
      polish          方案 B·引擎草稿润色（含「【引擎分析结果】」）
      tool_loop_reply 工具循环注入后的最终生成（含「[工具执行结果]」）
      tool_call_draft 自由对话首轮回复（含 <tool_call>，可选）
      default         其余 LLM 调用（解梦分析/八字分析等）
    返回 (fake, state)，state["n"] 记录调用次数。
    """
    state = {"n": 0}

    def fake(api_key, messages, **kw):
        state["n"] += 1
        text = " ".join(str(m.get("content", "")) for m in messages if isinstance(m, dict))
        if "【引擎分析结果】" in text:
            return cfg.get("polish", "好的，我帮你整理好了～")
        if "[工具执行结果]" in text:
            return cfg.get("tool_loop_reply", "好的～")
        if "message analyzer" in text:
            return cfg.get("analysis_json",
                           '{"needs_soothe": false, "soothe_text": "", '
                           '"emotion": "neutral", "intent": "free_chat", "is_sharing": false}')
        if "<tool_call>" in text:
            return cfg.get("tool_call_draft", cfg.get("default", "好的～"))
        if "开场白" in text:  # 秒回安抚（八字路径）
            return "命盘已排出【庚午 辛巳 乙酉 壬午】，日主乙木。正在深度推演中~ 🌟"
        if "还想了解" in text:  # 下文引导（八字路径）
            return "💬 还想了解：要不要看看下个月的整体运势？"
        return cfg.get("default", "好的，我帮你看看～")

    return fake, state


def install_fake(cfg: dict, handler) -> Tuple[object, dict]:
    """安装 fake 到所有 LLM 入口（src.llm.client + message_analyzer）。

    注意：不置空 handler.llm.api_key——_quick_flash/_run_tool_loop/
    _polish_with_engine_draft 都以 api_key 非空为前置守卫，置空会把流程
    短路成"无 LLM"降级路径，fake 就不会被调用。
    """
    import src.llm.client as llm_client_mod
    import src.engines.message_analyzer as analyzer_mod
    fake, state = make_fake(cfg)
    llm_client_mod.deepseek_anthropic_completion = fake
    analyzer_mod.deepseek_anthropic_completion = fake
    return fake, state


def restore_fake(handler):
    import src.llm.client as llm_client_mod
    import src.engines.message_analyzer as analyzer_mod
    llm_client_mod.deepseek_anthropic_completion = _original_completion
    analyzer_mod.deepseek_anthropic_completion = _original_completion


# 真实 completion 引用（恢复用）——导入一次
import src.llm.client as _llm_client_mod
_original_completion = _llm_client_mod.deepseek_anthropic_completion


# ---------------------------------------------------------------------------
# C 组：hermetic（mock LLM）
# ---------------------------------------------------------------------------

def group_c(handler: MessageHandler):
    print("\n===== C 组：hermetic（mock LLM，验证工具循环+引用注册覆盖） =====")

    # C1 有意图（dream）也走 LLM 二次生成 + 引用注册
    print("— C1 有意图（dream）→ 引擎草稿注入 + LLM 二次生成 + 引用注册")
    uid = "tc_c1_dream"
    ensure_paid(uid, handler.member_dao)
    fake, state = install_fake({
        "analysis_json": '{"needs_soothe": false, "soothe_text": "", '
                         '"emotion": "neutral", "intent": "dream", "is_sharing": false}',
        "default": "梦见蛇通常与变化、财运有关，不必过度担心。",
        "polish": "梦见蛇啦？古籍里说梦见蛇多与变化、财运相关[1]。"
                  "别太紧张，这可能预示着你最近会有一些新的变化～",
    }, handler)
    try:
        reply = handler.process("梦见蛇", uid)
        check("C1 回复经 LLM 二次生成（含润色标记）", "梦见蛇啦" in reply, reply[:120])
        check("C1 回复标注 [1] 引用", "[1]" in reply, reply[:120])
        citations = handler.pop_citations(uid)
        check("C1 引用已注册（book 解梦古籍）",
              any(c.get("type") == "book" for c in citations) and len(citations) >= 1,
              f"citations={citations}")
        check("C1 LLM 调用次数 = 意图分析 + 引擎解读 + 润色（≥3）",
              state["n"] >= 3, f"n={state['n']}")
    finally:
        restore_fake(handler)

    # C2 工具循环触发（检索）→ citations 出现
    print("— C2 自由对话输出 <tool_call>检索 → 工具执行 + 引用注册")
    uid = "tc_c2_search"
    ensure_paid(uid, handler.member_dao)
    fake, state = install_fake({
        "analysis_json": '{"needs_soothe": false, "soothe_text": "", '
                         '"emotion": "neutral", "intent": "free_chat", "is_sharing": false}',
        "default": "<tool_call>检索: 梦见蛇 解梦 征兆</tool_call>我查查古籍怎么说",
        "tool_loop_reply": "古籍里说梦见蛇主财运变动、也主贵人来访[1]。你怎么会梦到它呀？",
    }, handler)
    try:
        reply = handler.process("古籍里怎么说梦见蛇", uid)
        check("C2 工具结果注入后的最终回复", "古籍里说梦见蛇" in reply, reply[:120])
        check("C2 回复标注 [1] 引用", "[1]" in reply, reply[:120])
        citations = handler.pop_citations(uid)
        check("C2 引用已注册（book）",
              any(c.get("type") == "book" for c in citations) and len(citations) >= 1,
              f"citations={citations}")
        # 落库 tool_calls 证明检索工具真的被执行
        msgs = handler.session_dao.get_history(uid, limit=5)
        last = msgs[-1] if msgs else {}
        check("C2 本轮工具调用落库（检索）", "检索" in (last.get("tool_calls") or ""),
              f"tool_calls={last.get('tool_calls')}")
    finally:
        restore_fake(handler)

    # C3 排盘引擎结果注入 + 引用注册（type=engine）
    print("— C3 排盘：引擎结果注入 + 引用注册（type=engine）")
    import src.bot.handler as handler_mod

    class FakeAdvisor:
        """屏蔽 AdaptiveAdvisor（真实实现会发起 LLM 调用，hermetic 测试替换）。"""

        def generate(self, result, user_context="", api_key=""):
            return {"actions": [], "celebrity_match": {}, "daily_tip": "", "style_notes": ""}

    uid = "tc_c3_bazi"
    ensure_paid(uid, handler.member_dao)
    fake, state = install_fake({
        # 意图分析 fast path（出生日期正则）不消费调用；以下按出现顺序命中
        "default": "你的八字：庚午年辛巳月乙酉日壬午时，日主乙木，身强喜水木。"
                   "今年事业运总体向好，注意情绪管理。",
        "polish": "你的命盘排好啦！庚午 辛巳 乙酉 壬午，日主乙木[1]。"
                  "今年整体运程稳中有升，事业上有贵人相助的迹象～",
    }, handler)
    orig_advisor = handler_mod.AdaptiveAdvisor
    handler_mod.AdaptiveAdvisor = FakeAdvisor
    try:
        reply = handler.process("帮我排盘 1990年5月20日午时北京男", uid)
        check("C3 回复经 LLM 二次生成（含润色标记）", "你的命盘排好啦" in reply, reply[:120])
        check("C3 回复标注 [1] 引用", "[1]" in reply, reply[:120])
        citations = handler.pop_citations(uid)
        check("C3 引用已注册（type=engine 命盘）",
              any(c.get("type") == "engine" for c in citations) and len(citations) >= 1,
              f"citations={citations}")
        # 排盘路径 LLM 调用：秒回安抚+深度分析+图表标题+下文引导+润色 ≥ 5 次
        check("C3 润色 LLM 调用已发生（二次生成）", state["n"] >= 5, f"n={state['n']}")
    finally:
        handler_mod.AdaptiveAdvisor = orig_advisor
        restore_fake(handler)

    # C4 信息收集不润色（无引用 → 原样返回，无额外 LLM 调用）
    print("— C4 信息收集（'解梦' 无梦境内容）→ 不润色、原样返回")
    uid = "tc_c4_info"
    ensure_paid(uid, handler.member_dao)
    fake, state = install_fake({
        "analysis_json": '{"needs_soothe": false, "soothe_text": "", '
                         '"emotion": "neutral", "intent": "dream", "is_sharing": false}',
    }, handler)
    try:
        reply = handler.process("解梦", uid)
        check("C4 原样返回信息收集引导（未润色）",
              "请描述您的梦境" in reply and "梦见一条大蟒蛇" in reply, reply[:120])
        check("C4 无额外 LLM 调用（仅意图分析 1 次）", state["n"] == 1, f"n={state['n']}")
        check("C4 无引用注册", handler.pop_citations(uid) == [])
    finally:
        restore_fake(handler)

    # C5 无意图闲聊回归
    print("— C5 无意图闲聊回归")
    uid = "tc_c5_free"
    ensure_paid(uid, handler.member_dao)
    fake, state = install_fake({
        "analysis_json": '{"needs_soothe": false, "soothe_text": "", '
                         '"emotion": "neutral", "intent": "free_chat", "is_sharing": false}',
    }, handler)
    try:
        reply = handler.process("你好呀", uid)
        check("C5 闲聊正常回复", bool(reply and reply.strip()), reply[:120])
        check("C5 无引用注册", handler.pop_citations(uid) == [])
    finally:
        restore_fake(handler)

    # C6 特殊模式回归（xuetang/advisor/confidant 不被引擎润色改动）
    print("— C6 特殊模式回归（学堂/建议/心事树洞）")
    uid = "tc_c6_special"
    ensure_paid(uid, handler.member_dao)

    # C6a 学堂（关键字早退分支）
    fake, state = install_fake({}, handler)
    try:
        reply = handler.process("学堂 命理入门", uid)
        check("C6a 学堂正常返回", bool(reply and reply.strip()), reply[:120])
        check("C6a 学堂无引用注册", handler.pop_citations(uid) == [])
    finally:
        restore_fake(handler)

    # C6b AI 建议（关键字早退分支，无八字 → 信息引导）
    fake, state = install_fake({}, handler)
    try:
        reply = handler.process("我该怎么做", uid)
        check("C6b 建议正常返回（引导提供八字）",
              bool(reply) and ("出生" in reply or "八字" in reply or "建议" in reply),
              reply[:120])
        check("C6b 建议无引用注册", handler.pop_citations(uid) == [])
    finally:
        restore_fake(handler)

    # C6c 心事树洞（is_sharing → confidant 分支）。confidant 用原生 httpx
    # 直连 API（不走 deepseek_anthropic_completion），临时置空 key 防真实
    # 调用泄漏（置空后 confidant 降级 _free_chat → fake）。
    fake, state = install_fake({
        "analysis_json": '{"needs_soothe": true, "soothe_text": "我在呢，先别难过～", '
                         '"emotion": "sadness", "intent": "free_chat", "is_sharing": true}',
    }, handler)
    orig_key = handler.llm.api_key
    handler.llm.api_key = ""
    try:
        reply = handler.process("我最近特别难受，感觉什么都没意思", uid)
        check("C6c 心事树洞正常回复（共情式）",
              bool(reply and reply.strip()), reply[:120])
        check("C6c 树洞无引用注册", handler.pop_citations(uid) == [])
    finally:
        handler.llm.api_key = orig_key
        restore_fake(handler)

    # C7 流式：ChatStreamer（/api/chat/stream 编排器）done 事件带 citations
    print("— C7 流式：done 事件带 citations（ChatStreamer 全流程）")
    uid = "tc_c7_stream"
    ensure_paid(uid, handler.member_dao)
    fake, state = install_fake({
        "analysis_json": '{"needs_soothe": false, "soothe_text": "", '
                         '"emotion": "neutral", "intent": "dream", "is_sharing": false}',
        "default": "梦见蛇通常与变化、财运有关，不必过度担心。",
        "polish": "梦见蛇啦？古籍里说梦见蛇多与变化、财运相关[1]。"
                  "别太紧张，这可能预示着你最近会有一些新的变化～",
    }, handler)
    try:
        from src.api.chat_stream import ChatStreamer

        streamer = ChatStreamer(handler, handler.member_dao, dao=None,
                                sanitizer=None, auditor=None, validator=None)
        req = SimpleNamespace(message="梦见蛇", user_id=uid, voice_text="",
                              message_type="text", image_url="")
        request = SimpleNamespace(headers={}, client=None)
        auth = {"method": "jwt", "user_id": uid}

        async def _collect():
            out = []
            async for ev in streamer.events(req, request, auth):
                out.append(ev)
            return out

        events = asyncio.run(_collect())
        done = next((e for e in events if e.get("type") == "done"), None)
        chunks = [e for e in events if e.get("type") == "chunk"]
        check("C7 流式产出正文 chunk", len(chunks) >= 1, f"chunks={len(chunks)}")
        check("C7 done 事件携带 citations",
              done is not None and any(c.get("type") == "book" for c in done.get("citations", [])),
              f"done.citations={done.get('citations') if done else None}")
    finally:
        restore_fake(handler)


# ---------------------------------------------------------------------------
# D 组：真实 LLM E2E（需 API key）
# ---------------------------------------------------------------------------

def group_d(handler: MessageHandler, api_key: str):
    print("\n===== D 组：真实 LLM E2E =====")
    if not api_key:
        print("  [SKIP] 无 API key，D 组跳过")
        return

    # D1 解梦 E2E：有意图 → LLM 二次生成 + 引用注册
    print("— D1 解梦 E2E（intent=dream → 引用注册 book）")
    uid = "tc_d1_dream"
    ensure_paid(uid, handler.member_dao)
    t0 = time.time()
    reply = handler.process("梦见蛇是什么意思", uid)
    citations = handler.pop_citations(uid)
    check("D1 解梦回复非空", bool(reply and reply.strip()), reply[:120])
    check("D1 引用已注册（book 解梦古籍）",
          any(c.get("type") == "book" for c in citations), f"citations={citations}")
    print(f"    （{time.time()-t0:.1f}s）{reply[:60]}...")

    # D2 排盘 E2E：引擎结果注入 + 引用注册（type=engine）
    print("— D2 排盘 E2E（intent=bazi → 引用注册 type=engine）")
    uid = "tc_d2_bazi"
    ensure_paid(uid, handler.member_dao)
    t0 = time.time()
    reply = handler.process("帮我排盘 1990年5月20日午时北京男", uid)
    citations = handler.pop_citations(uid)
    ok_bazi = any(k in reply for k in ("庚午", "四柱", "日主", "命盘", "八字"))
    check("D2 排盘回复含命盘结果", ok_bazi, reply[:120])
    check("D2 引用已注册（type=engine 命盘）",
          any(c.get("type") == "engine" for c in citations), f"citations={citations}")
    print(f"    （{time.time()-t0:.1f}s）{reply[:60]}...")

    # D3 古籍检索 E2E：工具循环 → 引用注册（book）
    print("— D3 古籍检索 E2E（工具循环 → 引用注册 book）")
    uid = "tc_d3_search"
    ensure_paid(uid, handler.member_dao)
    t0 = time.time()
    reply = handler.process("古籍里怎么说梦见蛇", uid)
    citations = handler.pop_citations(uid)
    check("D3 回复非空", bool(reply and reply.strip()), reply[:120])
    check("D3 引用已注册（book 古籍）",
          any(c.get("type") == "book" for c in citations), f"citations={citations}")
    print(f"    （{time.time()-t0:.1f}s）{reply[:60]}...")


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="工具循环+引用注册覆盖测试（修复硬路由）")
    ap.add_argument("--group", choices=["C", "D", "ALL"], default="ALL")
    args = ap.parse_args()

    handler, api_key = build_handler()
    print(f"装配完成：api_key={'已配置' if api_key else '缺失'} | 向量库=vectordb_v2/fortune_books")

    if args.group in ("C", "ALL"):
        group_c(handler)
    if args.group in ("D", "ALL"):
        group_d(handler, api_key)

    print(f"\n===== 汇总：PASS {len(PASS)} | FAIL {len(FAIL)} =====")
    if FAIL:
        print("失败项：" + " | ".join(FAIL))
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
