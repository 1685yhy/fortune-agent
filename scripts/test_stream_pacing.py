#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Task 2 等待时长优化（chat UX 修复）— hermetic 验证

覆盖：
  T1 阶段计时日志：一轮 bazi 排盘产生 5 处 [timing] 日志
     （stage=intent / instant / main_analysis / polish / tool_loop，
      格式统一 "[timing] stage=<name> duration=<sec>s"，可 grep 量化）
  T2 并行化：意图分析与秒回安抚并行发出（threading.Barrier 证明并发）；
     秒回 LLM 调用仍先于主分析发出；预生成秒回内容进入最终回复（语义不变）
  T3 前端看门狗：CHUNK_GAP_TIMEOUT_S 60→90（三版 streamHost.js 一致）；
     回退静默化（流式失败无输出 → 静默回退 /api/chat，不再要求 gotData；
     仅回退失败才显示「网络开小差了」+ 重试钮）；重试功能保留
  T4 后端重试保留（client.py 空/过短重试一次）；无出生信息的消息
     不触发秒回预生成（不浪费 flash 调用）
  T5（Fix1）排盘意图门控：含出生日期但意图非排盘（如职业/自由聊天）→
     不提交预生成（不产生被丢弃的 calculate+flash 调用）
  T6（Fix2a）慢预生成（instant 2s）：主线程 0.5s 有限等待超时 → 回退同步
     flash（共 2 次，属预期回退路径）；内容仍前置；instant<main 时序仍成立；
     等待被限制在 0.5s（未等完整 2s worker）
  T7（Fix2b）慢预生成但 0.5s 内就绪（instant 0.3s）：消费在途结果，
     不产生第二次 flash 调用（mock 计数）
  T8（Fix2c+Fix3）worker 异常（engine.calculate 在 worker 线程抛异常）：
     回退触发、无崩溃、异常打日志（预生成失败）

用法：
  .venv/bin/python3 scripts/test_stream_pacing.py
退出码：0=全部通过；1=有失败
"""
import hashlib
import logging
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

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
from src.llm.client import FortuneLLM
from src.bot.handler import MessageHandler
from src.storage.dao import UserDAO
from src.storage.session_dao import SessionDAO

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = ""):
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail[:200]}" if detail and not cond else ""))
    (PASS if cond else FAIL).append(name)


# ---------------------------------------------------------------------------
# 脚本化 mock（断网运行）：按 prompt 内容分派返回
# ---------------------------------------------------------------------------

INSTANT_TEXT = "命盘亮点开场白：你的日主乙木春生，才华内秀～ 🌟"
POLISH_TEXT = ("这是润色后的最终回复文本。" + INSTANT_TEXT +
               "\n完整保留了四柱庚午辛巳乙酉壬午的核心数据。")

INTENT_JSON_BAZI = ('{"needs_soothe": false, "soothe_text": "", "emotion": "neutral", '
                    '"intent": "bazi", "is_sharing": false, "secondary_needs": [], '
                    '"facts": {}, "missing_info": [], "needs_search": false}')
INTENT_JSON_FREE = ('{"needs_soothe": false, "soothe_text": "", "emotion": "neutral", '
                    '"intent": "free_chat", "is_sharing": false, "secondary_needs": [], '
                    '"facts": {}, "missing_info": [], "needs_search": false}')


def make_dispatcher(barrier=None, calls=None, intent_json=INTENT_JSON_BAZI,
                    instant_delay=0.0):
    """deepseek_anthropic_completion 的 mock 分派器。

    barrier/呼叫: T2 并发性验证——秒回安抚两路（worker 预生成 flash + 主线程
    回退 flash）都进入 barrier.wait，串行实现会触发 BrokenBarrierError
    （calls["barrier_broken"]=True）。注：纯生日陈述的意图分析走 MessageAnalyzer
    fast path（无 LLM 调用），故意图分支不再参与 barrier。
    instant_delay: 秒回安抚分支返回前 sleep 秒数（模拟慢 LLM；Fix2 新增用例）。
    注：worker 异常路径（T8）不用 flash mock 抛异常——flash 异常会被
    _quick_flash 吞掉并走模板兜底（worker 仍返回非空），无法触发同步回退；
    真实 worker 失败面是 engine.calculate 抛异常，T8 直接 mock 该层。
    calls: {"intent"/"main": monotonic 时间戳, "instant_count": n,
            "instant_times": [...], "barrier_broken": bool}
    """
    def fake_completion(api_key, messages, **kw):
        first = messages[0].get("content", "") if messages else ""
        first = str(first)
        last = str(messages[-1].get("content", "")) if messages else ""
        # 意图分析（MessageAnalyzer）
        if first.startswith("You are a message analyzer"):
            if calls is not None:
                calls["intent"] = time.monotonic()
            return intent_json
        # 行动建议（advisor_v2，并行线程）
        if first.startswith("你是一个精通子平八字的AI命理顾问"):
            return '{"actions": [], "daily_tip": "", "style_notes": ""}'
        # 润色（system 含"润色成一段"）
        if "润色成一段" in first:
            return POLISH_TEXT
        # 工具循环（system 含"基于工具执行结果"）
        if "基于工具执行结果" in first:
            return "这是工具注入后的最终分析文本，长度超过十五个字符。"
        # 秒回安抚（_quick_flash，user prompt 含"命盘排出来了"）
        if "命盘排出来了" in last:
            if calls is not None:
                calls["instant_count"] = calls.get("instant_count", 0) + 1
                calls.setdefault("instant_times", []).append(time.monotonic())
            if instant_delay:
                time.sleep(instant_delay)
            if barrier is not None:
                try:
                    barrier.wait(timeout=8)
                except Exception:
                    if calls is not None:
                        calls["barrier_broken"] = True
            return INSTANT_TEXT
        # 欢迎回来（_quick_flash）
        if "回头客" in last:
            return "欢迎回来～"
        # 下文引导（_quick_flash）
        if "下文引导" in last:
            return "💬 还想了解：要不要帮你看看下个月的整体运势？"
        # 主分析（llm.analyze：user 消息以 USER_CONTEXT_TEMPLATE 开头）
        if last.lstrip().startswith("## 用户排盘信息"):
            if calls is not None:
                calls["main"] = time.monotonic()
            return ("主分析结果：庚午辛巳乙酉壬午，日主乙木，木火通明，才华横溢。"
                    "长度超过十五个字符。")
        # 其余（自由对话等）
        return "通用兜底回复文本，长度足够超过十五个字符。"
    return fake_completion


class CaptureHandler(logging.Handler):
    """捕获 src.bot.handler logger 的 INFO 日志（计时行断言用）。"""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.lines = []

    def emit(self, record):
        try:
            self.lines.append(record.getMessage())
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 装配 hermetic handler（真实引擎 + 临时 DB + stub 检索 + mock LLM）
# ---------------------------------------------------------------------------

class StubRetriever:
    def search(self, query, top_k=5, category="", **kw):
        return []


class StubEmbedder:
    """引用校验用的 embedder 替身（避免测试环境加载 bge-m3 模型，hermetic）。"""

    def encode_single(self, text):
        import numpy as np
        return np.array([1.0, 0.0, 0.0])


def stub_citation_embedder():
    """让 verify_citations 复用模块级单例（跳过模型加载，断网可跑）。"""
    import src.rag.citation as citation_mod
    citation_mod._verifier_embedder = StubEmbedder()


def build_handler(tmp: str) -> MessageHandler:
    db_path = os.path.join(tmp, "test.db")
    os.environ["USER_MEMORY_DIR"] = os.path.join(tmp, "memory")
    handler = MessageHandler(
        BaziEngine(), ZiweiEngine(), LiuyaoEngine(), FengshuiEngine(),
        MianxiangEngine(), ZeriEngine(), StubRetriever(),
        FortuneLLM(api_key="test-key", model="deepseek-flash",
                   deep_model="deepseek-flash", provider="deepseek"),
        UserDAO(db_path),
        dream_engine=DreamEngine(), hehun_engine=HehunEngine(),
        qimen_engine=QimenEngine(), xingming_engine=XingmingEngine(),
        session_dao=SessionDAO(db_path),
    )
    handler.member_dao = None  # 不限额度
    return handler


# Fix1 门控：预生成只对「纯生日陈述」（MessageAnalyzer fast path 判 bazi）触发，
# 含意图词（事业/适合…）的消息不再提交预生成 → 测试消息必须是纯生日陈述
BAZI_MSG = "1990年5月20日午时北京男"
TIMING_RE = re.compile(r"^\[timing\] stage=(\S+) duration=(\d+\.\d+)s$")


# ---------------------------------------------------------------------------
# T1 阶段计时日志
# ---------------------------------------------------------------------------

def t1_timing_logs(handler: MessageHandler):
    print("\n— T1 阶段计时日志（5 处 [timing] 行）")
    import src.llm.client as llm_mod
    orig = llm_mod.deepseek_anthropic_completion
    cap = CaptureHandler()
    hlog = logging.getLogger("src.bot.handler")
    prev_level = hlog.level
    hlog.setLevel(logging.INFO)  # 默认 effective level 为 WARNING，INFO 计时行不会发出
    hlog.addHandler(cap)
    try:
        llm_mod.deepseek_anthropic_completion = make_dispatcher(calls={})
        uid = "t1_user"
        reply = handler.process(BAZI_MSG, uid)
    finally:
        llm_mod.deepseek_anthropic_completion = orig
        hlog.removeHandler(cap)
        hlog.setLevel(prev_level)  # 还原测试前的日志级别（Fix5）

    check("T1 回复非空", bool(reply and reply.strip()), reply[:80])
    timing = {}
    for line in cap.lines:
        m = TIMING_RE.match(line)
        if m:
            timing[m.group(1)] = float(m.group(2))
    for stage in ("intent", "instant", "main_analysis", "polish", "tool_loop"):
        check(f"T1 stage={stage} 计时日志存在且格式统一",
              stage in timing and timing[stage] >= 0,
              f"timing={ {k: v for k, v in timing.items()} }")
    check("T1 计时格式可 grep（[timing] stage=xxx duration=Ns）",
          any("[timing] stage=" in l for l in cap.lines))


# ---------------------------------------------------------------------------
# T2 并行化（barrier 证明并发）
# 注：Fix1 门控后，纯生日陈述的意图分析走 MessageAnalyzer fast path（无 LLM
# 调用），可并行的两路变为「worker 预生成 flash」与「主线程回退 flash」——
# barrier 证明这两路 flash 同时在途（预生成未就绪时回退不与 worker 串行）。
# ---------------------------------------------------------------------------

def t2_parallel(handler: MessageHandler):
    print("\n— T2 预生成 worker flash 与主线程回退 flash 并发（barrier）")
    import src.llm.client as llm_mod
    orig = llm_mod.deepseek_anthropic_completion
    barrier = threading.Barrier(2)
    calls = {"barrier_broken": False}
    try:
        llm_mod.deepseek_anthropic_completion = make_dispatcher(
            barrier=barrier, calls=calls)
        uid = "t2_user"
        t0 = time.monotonic()
        reply = handler.process(BAZI_MSG, uid)
        wall = time.monotonic() - t0
    finally:
        llm_mod.deepseek_anthropic_completion = orig

    times = calls.get("instant_times", [])
    main = calls.get("main")
    check("T2 秒回安抚预生成调用发生（worker flash）",
          calls.get("instant_count", 0) >= 1,
          f"calls={ {k: v for k, v in calls.items() if k != 'barrier_broken'} }")
    check("T2 预生成未就绪 → 主线程回退 flash 也触发（共 2 次）",
          calls.get("instant_count", 0) == 2,
          f"calls={ {k: v for k, v in calls.items() if k != 'barrier_broken'} }")
    check("T2 两路 flash 并发（barrier 未超时）", calls.get("barrier_broken") is False,
          "串行实现会让先到的一路 barrier.wait 超时")
    check("T2 秒回 LLM 调用先于主分析发出",
          len(times) >= 1 and main is not None and times[0] < main,
          f"instant_times={times} main={main}")
    check("T2 预生成秒回内容进入最终回复（语义不变）",
          INSTANT_TEXT in reply, reply[:120])
    check("T2 意图走 fast path（纯生日陈述无意图 LLM 调用）",
          calls.get("intent") is None, f"calls={calls}")
    print(f"      [info] T2 墙钟 {wall:.2f}s（含 0.5s 有限等待 + 两路 flash）")


# ---------------------------------------------------------------------------
# T3 前端看门狗 + 回退静默化（三版 streamHost.js 字符串断言）
# ---------------------------------------------------------------------------

STREAMHOST_PATHS = [
    PROJECT_DIR / "miniprogram" / "utils" / "streamHost.js",
    PROJECT_DIR / "miniprogram_simple" / "utils" / "streamHost.js",
    PROJECT_DIR / "miniprogram_fusion" / "utils" / "streamHost.js",
]


def t3_streamhost():
    print("\n— T3 前端看门狗 90s + 回退静默化（三版一致）")
    hashes = []
    for p in STREAMHOST_PATHS:
        check(f"T3 文件存在 {p.name}", p.is_file(), str(p))
        if not p.is_file():
            continue
        src = p.read_text(encoding="utf-8")
        hashes.append(hashlib.md5(src.encode()).hexdigest())
        check(f"T3 CHUNK_GAP_TIMEOUT_S=90（{p.parent.parent.name}）",
              re.search(r"CHUNK_GAP_TIMEOUT_S\s*=\s*90\b", src) is not None)
        check(f"T3 看门狗提示为 90 秒（{p.parent.parent.name}）",
              "90 秒无新内容" in src)
        check(f"T3 回退条件不再要求 gotData（{p.parent.parent.name}）",
              "!hasPartial && !this.fallbackStarted" in src
              and "!hasPartial && !this.gotData" not in src)
        check(f"T3 回退失败才显示网络开小差了（{p.parent.parent.name}）",
              "网络开小差了，再试一次？" in src and "notify('生成失败" in src)
        check(f"T3 手动重试保留（{p.parent.parent.name}）",
              "retry(msgId" in src)
    if len(hashes) == 3:
        check("T3 三版 streamHost.js byte-identical",
              hashes[0] == hashes[1] == hashes[2],
              f"{hashes}")


# ---------------------------------------------------------------------------
# T4 后端重试保留 + 无出生信息不触发预生成
# ---------------------------------------------------------------------------

def t4_retry_and_no_pregen(handler: MessageHandler):
    print("\n— T4 后端空/过短重试保留；无出生信息不触发秒回预生成")
    client_src = (PROJECT_DIR / "src" / "llm" / "client.py").read_text(encoding="utf-8")
    check("T4 client.py 空/过短回复重试一次保留",
          "重试一次" in client_src and "# Retry once if reply is empty" in client_src)

    import src.llm.client as llm_mod
    orig = llm_mod.deepseek_anthropic_completion
    calls = {}
    try:
        llm_mod.deepseek_anthropic_completion = make_dispatcher(
            calls=calls, intent_json=INTENT_JSON_FREE)
        reply = handler.process("今天天气不错", "t4_user")
    finally:
        llm_mod.deepseek_anthropic_completion = orig
    check("T4 自由聊天回复非空", bool(reply and reply.strip()), reply[:80])
    check("T4 无出生信息 → 不触发秒回预生成（不浪费 flash）",
          calls.get("instant_count", 0) == 0, f"calls={calls}")


# ---------------------------------------------------------------------------
# T5（Fix1）排盘意图门控：含出生日期但意图非排盘 → 不提交预生成
# ---------------------------------------------------------------------------

def t5_intent_gate_pregen(handler: MessageHandler):
    print("\n— T5（Fix1）排盘意图门控：非排盘意图不触发预生成")
    import src.llm.client as llm_mod
    orig = llm_mod.deepseek_anthropic_completion
    calls = {}
    try:
        llm_mod.deepseek_anthropic_completion = make_dispatcher(
            calls=calls, intent_json=INTENT_JSON_FREE)
        # 含完整出生日期 + 意图提示词（适合/工作）→ fast path 判非 bazi，
        # 修复前会浪费一次 worker 排盘+flash（结果被丢弃）
        reply = handler.process("1990年5月20日出生 我适合做什么工作呢", "t5_user")
    finally:
        llm_mod.deepseek_anthropic_completion = orig
    check("T5 含出生日期+意图词 → 走 LLM 意图分析（自由聊天）",
          calls.get("intent") is not None, f"calls={calls}")
    check("T5 自由聊天回复非空", bool(reply and reply.strip()), reply[:80])
    check("T5 非排盘意图 → 不触发预生成（0 次秒回 flash，不再有丢弃调用）",
          calls.get("instant_count", 0) == 0, f"calls={calls}")


# ---------------------------------------------------------------------------
# T6（Fix2a）慢预生成：主线程 0.5s 有限等待超时 → 回退同步 flash
# ---------------------------------------------------------------------------

def t6_slow_instant_fallback(handler: MessageHandler):
    print("\n— T6（Fix2a）慢预生成（instant 2s）：0.5s 超时 → 回退同步 flash")
    import src.llm.client as llm_mod
    orig = llm_mod.deepseek_anthropic_completion
    calls = {}
    t0 = time.monotonic()
    try:
        llm_mod.deepseek_anthropic_completion = make_dispatcher(
            calls=calls, instant_delay=2.0)
        reply = handler.process(BAZI_MSG, "t6_user")
    finally:
        llm_mod.deepseek_anthropic_completion = orig
    wall = time.monotonic() - t0
    times = calls.get("instant_times", [])
    main = calls.get("main")
    check("T6 回退路径触发（同步 flash 被调，共 2 次：worker + 回退）",
          calls.get("instant_count", 0) == 2, f"calls={calls}")
    check("T6 回退秒回内容仍前置（语义不变）",
          INSTANT_TEXT in reply, reply[:120])
    check("T6 秒回 LLM 调用仍先于主分析发出",
          len(times) >= 1 and main is not None and times[0] < main,
          f"instant_times={times} main={main}")
    check("T6 主线程等待被限制在 0.5s（未等完整 2s worker）",
          len(times) >= 1 and main is not None and (main - times[0]) < 3.0,
          f"worker_flash_start→main="
          f"{round(main - times[0], 2) if main is not None and times else '?'}s")
    print(f"      [info] T6 墙钟 {wall:.2f}s（两段 2s 慢 flash + 0.5s 有限等待）")


# ---------------------------------------------------------------------------
# T7（Fix2b）慢预生成但 0.5s 内就绪 → 消费在途结果，不产生第二次 flash
# ---------------------------------------------------------------------------

def t7_inflight_consumed(handler: MessageHandler):
    print("\n— T7（Fix2b）慢预生成（instant 0.3s）：0.5s 内就绪 → 消费在途结果")
    import src.llm.client as llm_mod
    orig = llm_mod.deepseek_anthropic_completion
    calls = {}
    t0 = time.monotonic()
    try:
        llm_mod.deepseek_anthropic_completion = make_dispatcher(
            calls=calls, instant_delay=0.3)
        reply = handler.process(BAZI_MSG, "t7_user")
    finally:
        llm_mod.deepseek_anthropic_completion = orig
    wall = time.monotonic() - t0
    check("T7 在途结果被消费（仅 1 次 flash，不产生第二次调用）",
          calls.get("instant_count", 0) == 1, f"calls={calls}")
    check("T7 预生成秒回内容进入最终回复",
          INSTANT_TEXT in reply, reply[:120])
    print(f"      [info] T7 墙钟 {wall:.2f}s（等待被 0.3s 就绪结果截短）")


# ---------------------------------------------------------------------------
# T8（Fix2c+Fix3）worker 异常：回退触发、无崩溃、异常打日志
# ---------------------------------------------------------------------------

def t8_worker_exception_fallback(handler: MessageHandler):
    print("\n— T8（Fix2c）worker 异常路径：回退触发、无崩溃（Fix3 告警日志）")
    import src.llm.client as llm_mod
    orig = llm_mod.deepseek_anthropic_completion
    orig_calc = handler.engine.calculate

    def flaky_calculate(*args, **kw):
        if threading.current_thread().name.startswith("pregen"):
            raise ValueError("worker calculate boom")
        return orig_calc(*args, **kw)

    cap = CaptureHandler()
    hlog = logging.getLogger("src.bot.handler")
    prev_level = hlog.level
    hlog.setLevel(logging.INFO)
    hlog.addHandler(cap)
    calls = {}
    try:
        llm_mod.deepseek_anthropic_completion = make_dispatcher(calls=calls)
        handler.engine.calculate = flaky_calculate
        reply = handler.process(BAZI_MSG, "t8_user")
    finally:
        handler.engine.calculate = orig_calc
        llm_mod.deepseek_anthropic_completion = orig
        hlog.removeHandler(cap)
        hlog.setLevel(prev_level)
    check("T8 worker 异常 → 无崩溃（回复非空）",
          bool(reply and reply.strip()), reply[:80])
    check("T8 回退触发（主线程同步 flash 被调，仅 1 次）",
          calls.get("instant_count", 0) == 1, f"calls={calls}")
    check("T8 回退秒回内容进入最终回复", INSTANT_TEXT in reply, reply[:120])
    check("T8 worker 异常已打日志（Fix3「预生成失败」）",
          any("预生成失败" in l for l in cap.lines), f"logs={cap.lines[:3]}")


def main():
    print("Task 2 等待时长优化验证（hermetic，断网可跑）")
    stub_citation_embedder()
    tmp = tempfile.mkdtemp(prefix="fortune_pacing_test_")
    handler = build_handler(tmp)
    t1_timing_logs(handler)
    t2_parallel(handler)
    t3_streamhost()
    t4_retry_and_no_pregen(handler)
    t5_intent_gate_pregen(handler)
    t6_slow_instant_fallback(handler)
    t7_inflight_consumed(handler)
    t8_worker_exception_fallback(handler)

    print(f"\n===== 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 =====")
    if FAIL:
        print("失败项:")
        for f in FAIL:
            print(f"  ✗ {f}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
