#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Task 3 思考步骤渐进展示（chat UX 修复）— hermetic 验证

背景（根因）：后端曾在开工前的一个事件循环里把 INTENT_THINKING_STEPS 预置
步骤全部打光（for s in steps: stream_cb("thinking", ...)），随后各 _do_* 又在
真实工作里程碑补发——前端因此一次性看到全部思考步骤。本任务把预置步骤迁移到
各 _do_*/_handle_* 的真实工作里程碑处（复用 _emit_stream_event 现有调用点），
保证至少 1 条起始步骤在开工时发出、之后每完成一步真实工作推进一步。

覆盖：
  T1 bazi（4 步）：thinking 事件恰为
     [正在排盘…, 正在查阅古籍…, 正在推演五行流年…, 正在整理行动建议…]，
     首条先于 engine.calculate（开工时发出），且与真实工作点交错：
     t0 < calculate 开始 < t1 < 检索开始 < t2 < LLM 分析开始 < t3；
     相邻事件间隔 ≥ 5ms（不再同一毫秒打光）；无 <tool_call> 时条数 = 4。
  T2 ziwei（3 步，原无里程碑事件）：同上模式——
     [我在排紫微斗数盘…, 正在查阅古籍…, 逐宫推演十二宫…] 跨真实工作点分布。
  T3 源码级：handler.py 不再存在预置循环打光（for s in steps 已删）与
     INTENT_THINKING_STEPS 字典；各意图里程碑文案均已出现在 handler.py。
  T4 前端三版（miniprogram/simple/fusion）：chat 四文件 md5 一致；
     streamHost._onDone 自动收起（thinkCollapsed: true）；chat.wxml 单步渲染
     （当前 doing 步 + 已完成 N 步计数）；chat.js 派生 thinkDone/thinkDoing/
     thinkLabel；node --check 语法通过。

用法：
  .venv/bin/python3 scripts/test_stream_steps.py
退出码：0=全部通过；1=有失败
"""
import hashlib
import logging
import os
import subprocess
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
INTENT_JSON_ZIWEI = ('{"needs_soothe": false, "soothe_text": "", "emotion": "neutral", '
                     '"intent": "ziwei", "is_sharing": false, "secondary_needs": [], '
                     '"facts": {}, "missing_info": [], "needs_search": false}')


def make_dispatcher(calls=None, intent_json=INTENT_JSON_BAZI, analyze_delay=0.02):
    """deepseek_anthropic_completion 的 mock 分派器（Task 2 风格）。

    analyze_delay: 主分析（llm.analyze）分支返回前 sleep 秒数——模拟真实
    LLM 分析耗时，让「分析前」与「分析后」的 thinking 事件时间戳分离。
    calls: {"instant_count": n, "main_analyze": bool}
    """
    def fake_completion(api_key, messages, **kw):
        first = messages[0].get("content", "") if messages else ""
        first = str(first)
        last = str(messages[-1].get("content", "")) if messages else ""
        # 意图分析（MessageAnalyzer）
        if first.startswith("You are a message analyzer"):
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
                calls["main_analyze"] = True
            if analyze_delay:
                time.sleep(analyze_delay)
            return ("主分析结果：庚午辛巳乙酉壬午，日主乙木，木火通明，才华横溢。"
                    "长度超过十五个字符。")
        # 其余（自由对话 / ziwei 等引擎分析 prompt）
        return "通用兜底回复文本，长度足够超过十五个字符。"
    return fake_completion


# ---------------------------------------------------------------------------
# 事件捕获 + hermetic handler 装配（复用 Task 2 测试基建）
# ---------------------------------------------------------------------------

class CaptureStream:
    """记录 stream_cb 收到的全部事件：(monotonic 秒, 类型, 文本)。"""

    def __init__(self):
        self.events = []

    def __call__(self, evt_type, payload):
        self.events.append((time.monotonic(), evt_type,
                            (payload or {}).get("text", "")))

    def thinking(self):
        return [(t, text) for t, evt, text in self.events if evt == "thinking"]


class StubRetriever:
    def search(self, query, top_k=5, category="", **kw):
        return []


class StubEmbedder:
    """引用校验用的 embedder 替身（避免加载 bge-m3 模型，hermetic）。"""

    def encode_single(self, text):
        import numpy as np
        return np.array([1.0, 0.0, 0.0])


def stub_citation_embedder():
    import src.rag.citation as citation_mod
    citation_mod._verifier_embedder = StubEmbedder()


def build_handler(tmp: str) -> MessageHandler:
    db_path = os.path.join(tmp, "test.db")
    os.environ["USER_MEMORY_DIR"] = os.path.join(tmp, "memory")
    handler = MessageHandler(
        BaziEngine(), ZiweiEngine(), LiuyaoEngine(), FengshuiEngine(),
        MianxiangEngine(), ZeriEngine(), StubRetriever(),
        FortuneLLM(api_key="test-key", model="deepseek-v4-flash",
                   deep_model="deepseek-v4-flash", provider="deepseek"),
        UserDAO(db_path),
        dream_engine=DreamEngine(), hehun_engine=HehunEngine(),
        qimen_engine=QimenEngine(), xingming_engine=XingmingEngine(),
        session_dao=SessionDAO(db_path),
    )
    handler.member_dao = None  # 不限额度
    return handler


def wrap_with_hook(orig, calls, key, delay=0.02, guard_pregen=True):
    """包一层真实方法：记录开始时刻 + 小 sleep 模拟耗时。

    guard_pregen: 跳过预生成 worker 线程的调用（其线程名以 pregen 开头，
    Task 2 后台并行，主流程之外，不参与本测试的时间轴断言）。
    """
    def wrapped(*args, **kw):
        if guard_pregen and threading.current_thread().name.startswith("pregen"):
            return orig(*args, **kw)
        calls[key] = time.monotonic()
        if delay:
            time.sleep(delay)
        return orig(*args, **kw)
    return wrapped


# ---------------------------------------------------------------------------
# T1 bazi：4 步跨真实工作点（首条开工即发、交错分布、条数正确）
# ---------------------------------------------------------------------------

BAZI_MSG = "1990年5月20日午时北京男"
# 注：须含意图提示词（如"好吗"）绕过 MessageAnalyzer fast path——
# 纯生日陈述会被 fast path 直接判 bazi，只有含意图词才走（mock）LLM 分类
ZHUANPAN_MSG = "1990年5月20日午时北京男 帮我排紫微斗数好吗"

BAZI_STEPS = ["正在排盘…", "正在查阅古籍…", "正在推演五行流年…", "正在整理行动建议…"]
ZIWEI_STEPS = ["我在排紫微斗数盘…", "正在查阅古籍…", "逐宫推演十二宫…"]

# 各意图里程碑文案（源码级断言：预置文案已迁移到真实里程碑处）
INTENT_STEP_TEXTS = {
    "bazi": BAZI_STEPS,
    "ziwei": ZIWEI_STEPS,
    "liuyao": ["我在起卦…", "正在查阅古籍…", "推演卦象变化…"],
    "qimen": ["我在起奇门局…", "正在查阅古籍…", "推演九宫格局…"],
    "fengshui": ["我在勘察风水格局…", "正在查阅古籍…", "结合五行方位分析…"],
    "mianxiang": ["我在端详你的面相…", "正在查阅古籍…", "结合五宫五行分析…"],
    "zeri": ["我在翻黄历择吉…", "正在查阅古籍…", "比对吉凶宜忌…"],
    "hehun": ["我在比对两人命盘…", "正在查阅古籍…", "推演五行互补…"],
    "xingming": ["我在拆解姓名笔画五行…", "正在查阅古籍…", "推演三才配置…"],
    "dream": ["正在分析梦境象征…", "正在整理解梦要点…"],
    "calendar": ["我在查今日星象…"],
    "hourly": ["我在推演时辰运势…"],
}


def t1_bazi_steps(handler: MessageHandler):
    print("\n— T1 bazi 思考步骤跨真实工作点分布（4 步，mock 引擎/检索/LLM 各耗 20ms）")
    import src.llm.client as llm_mod
    orig_llm = llm_mod.deepseek_anthropic_completion
    orig_calc = handler.engine.calculate
    orig_search = handler.retriever.search
    orig_analyze = handler.llm.analyze
    calls = {}
    cap = CaptureStream()
    try:
        llm_mod.deepseek_anthropic_completion = make_dispatcher(calls=calls)
        handler.engine.calculate = wrap_with_hook(orig_calc, calls, "calc_start")
        handler.retriever.search = wrap_with_hook(orig_search, calls, "search_start")
        handler.llm.analyze = wrap_with_hook(orig_analyze, calls, "analyze_start")
        reply = handler.process(BAZI_MSG, "t1_user", stream_cb=cap)
    finally:
        handler.engine.calculate = orig_calc
        handler.retriever.search = orig_search
        handler.llm.analyze = orig_analyze
        llm_mod.deepseek_anthropic_completion = orig_llm

    check("T1 回复非空", bool(reply and reply.strip()), reply[:80])
    steps = cap.thinking()
    texts = [s[1] for s in steps]
    check("T1 思考条数正确（bazi = 4，无工具调用注入）", len(steps) == 4,
          f"texts={texts}")
    check("T1 步骤文案顺序正确（排盘→古籍→流年→建议）",
          texts == BAZI_STEPS, f"texts={texts}")
    t0 = steps[0][0]
    t1 = steps[1][0]
    t2 = steps[2][0]
    t3 = steps[3][0]
    check("T1 首条在开工时发出（先于 engine.calculate）",
          t0 < calls.get("calc_start", 0), f"t0={t0:.3f} calc={calls.get('calc_start'):.3f}")
    check("T1 步骤与真实工作点交错（t0<排盘<t1<检索<t2<分析<t3）",
          t0 < calls["calc_start"] < t1 < calls["search_start"] < t2 < calls["analyze_start"] < t3,
          f"t={[round(x, 3) for x in (t0, calls['calc_start'], t1, calls['search_start'], t2, calls['analyze_start'], t3)]}")
    gaps = [b - a for a, b in zip([s[0] for s in steps], [s[0] for s in steps][1:])]
    check("T1 相邻步骤间隔 ≥ 5ms（非同一毫秒打光）",
          len(gaps) == 3 and all(g >= 0.005 for g in gaps),
          f"gaps={[round(g, 3) for g in gaps]}")


# ---------------------------------------------------------------------------
# T2 ziwei：原无里程碑事件 → 补发 3 步，同样跨真实工作点
# ---------------------------------------------------------------------------

def t2_ziwei_steps(handler: MessageHandler):
    print("\n— T2 ziwei 思考步骤跨真实工作点分布（3 步，原无里程碑事件）")
    import src.llm.client as llm_mod
    orig_llm = llm_mod.deepseek_anthropic_completion
    orig_calc = handler.ziwei_engine.calculate
    orig_search = handler.retriever.search
    orig_analyze = handler.llm.analyze
    calls = {}
    cap = CaptureStream()
    try:
        llm_mod.deepseek_anthropic_completion = make_dispatcher(
            calls=calls, intent_json=INTENT_JSON_ZIWEI)
        handler.ziwei_engine.calculate = wrap_with_hook(orig_calc, calls, "calc_start")
        handler.retriever.search = wrap_with_hook(orig_search, calls, "search_start")
        handler.llm.analyze = wrap_with_hook(orig_analyze, calls, "analyze_start")
        reply = handler.process(ZHUANPAN_MSG, "t2_user", stream_cb=cap)
    finally:
        handler.ziwei_engine.calculate = orig_calc
        handler.retriever.search = orig_search
        handler.llm.analyze = orig_analyze
        llm_mod.deepseek_anthropic_completion = orig_llm

    check("T2 回复非空", bool(reply and reply.strip()), reply[:80])
    steps = cap.thinking()
    texts = [s[1] for s in steps]
    check("T2 思考条数正确（ziwei = 3）", len(steps) == 3, f"texts={texts}")
    check("T2 步骤文案顺序正确（排盘→古籍→十二宫）",
          texts == ZIWEI_STEPS, f"texts={texts}")
    if len(steps) < 3:
        return  # 条数不对时跳过时序断言（避免 IndexError 掩盖失败详情）
    t0, t1, t2 = steps[0][0], steps[1][0], steps[2][0]
    check("T2 首条在开工时发出（先于 ziwei_engine.calculate）",
          t0 < calls.get("calc_start", 0),
          f"t0={t0:.3f} calc={calls.get('calc_start', 0.0):.3f}")
    check("T2 步骤与真实工作点交错（t0<排盘<t1<检索<t2<分析）",
          t0 < calls["calc_start"] < t1 < calls["search_start"] < t2 < calls["analyze_start"],
          f"t={[round(x, 3) for x in (t0, calls['calc_start'], t1, calls['search_start'], t2, calls['analyze_start'])]}")
    gaps = [b - a for a, b in zip([s[0] for s in steps], [s[0] for s in steps][1:])]
    check("T2 相邻步骤间隔 ≥ 5ms（非同一毫秒打光）",
          len(gaps) == 2 and all(g >= 0.005 for g in gaps),
          f"gaps={[round(g, 3) for g in gaps]}")


# ---------------------------------------------------------------------------
# T3 源码级：预置循环打光已移除，里程碑文案已就位
# ---------------------------------------------------------------------------

def t3_source_checks():
    print("\n— T3 handler.py 源码级检查（无预置循环打光 + 里程碑文案就位）")
    src = (PROJECT_DIR / "src" / "bot" / "handler.py").read_text(encoding="utf-8")
    check("T3 预置循环打光已删除（for s in steps 不存在）",
          "for s in steps" not in src)
    check("T3 INTENT_THINKING_STEPS 预置字典已迁移（字典定义不再存在）",
          "INTENT_THINKING_STEPS = {" not in src)
    for intent, texts in INTENT_STEP_TEXTS.items():
        for t in texts:
            check(f"T3 {intent} 里程碑文案在 handler.py：{t}", t in src)


# ---------------------------------------------------------------------------
# T4 前端三版：byte-identical + 新渲染逻辑 + node --check
# ---------------------------------------------------------------------------

FRONTEND_FILES = {
    "streamHost.js": ["utils/streamHost.js"],
    "chat.wxml": ["pages/chat/chat.wxml"],
    "chat.wxss": ["pages/chat/chat.wxss"],
    "chat.js": ["pages/chat/chat.js"],
}
VERSIONS = ["miniprogram", "miniprogram_simple", "miniprogram_fusion"]


def t4_frontend():
    print("\n— T4 前端三版一致 + 单步渲染/完成收起逻辑 + 语法检查")
    contents = {}
    hashes = {}
    for name, rels in FRONTEND_FILES.items():
        contents[name] = []
        hashes[name] = []
        for ver in VERSIONS:
            p = PROJECT_DIR / ver / rels[0]
            check(f"T4 {name} 存在（{ver}）", p.is_file(), str(p))
            if not p.is_file():
                continue
            txt = p.read_text(encoding="utf-8")
            contents[name].append(txt)
            hashes[name].append(hashlib.md5(txt.encode()).hexdigest())
    for name in FRONTEND_FILES:
        if len(hashes[name]) == 3:
            check(f"T4 {name} 三版 byte-identical",
                  hashes[name][0] == hashes[name][1] == hashes[name][2],
                  f"{hashes[name]}")

    # streamHost：_onDone 自动收起（thinkCollapsed: true）
    sh = contents["streamHost.js"]
    check("T4 streamHost._onDone 完成自动收起（thinkCollapsed: true）",
          all("thinkCollapsed: true" in s for s in sh))
    check("T4 streamHost 思考数组状态语义保留（done/doing）",
          all("state: 'done'" in s and "state: 'doing'" in s for s in sh))

    # chat.wxml：单步渲染（当前 doing 步）+ 已完成 N 步计数 + 完成收起标签
    wxml = contents["chat.wxml"]
    check("T4 chat.wxml 单步渲染（item.thinkDoing 当前步）",
          all("item.thinkDoing" in s for s in wxml))
    check("T4 chat.wxml 已完成计数行（已完成 N 步）",
          all("已完成" in s and "item.thinkDone" in s for s in wxml))
    # chat.js：镜像派生 thinkDone/thinkDoing/thinkLabel + 折叠交互保留
    chatjs = contents["chat.js"]
    check("T4 chat.wxml 收起标签用 thinkLabel（文案在 chat.js 镜像派生）",
          all("item.thinkLabel" in s for s in wxml))
    check("T4 chat.js 收起标签文案（思考完成 ✓ 已生成回复）",
          all("思考完成 ✓ 已生成回复" in s for s in chatjs))
    check("T4 chat.js 镜像派生 thinkLabel/thinkDone/thinkDoing",
          all("thinkLabel" in s and "thinkDone" in s and "thinkDoing" in s for s in chatjs))
    check("T4 chat.js 折叠交互保留（toggleThink + patchMessage）",
          all("toggleThink" in s and "patchMessage" in s for s in chatjs))

    # node --check 语法校验（6 个 JS 文件）
    try:
        subprocess.run(["node", "--version"], capture_output=True, timeout=10)
        has_node = True
    except Exception:
        has_node = False
    if not has_node:
        print("      [info] node 不可用，跳过 node --check（改用 Python 解析级断言）")
    else:
        for ver in VERSIONS:
            for rel in ("utils/streamHost.js", "pages/chat/chat.js"):
                p = PROJECT_DIR / ver / rel
                r = subprocess.run(["node", "--check", str(p)],
                                   capture_output=True, timeout=30)
                check(f"T4 node --check {ver}/{rel}",
                      r.returncode == 0, r.stderr.decode()[:200])


def main():
    print("Task 3 思考步骤渐进展示验证（hermetic，断网可跑）")
    stub_citation_embedder()
    tmp = tempfile.mkdtemp(prefix="fortune_steps_test_")
    handler = build_handler(tmp)
    t1_bazi_steps(handler)
    t2_ziwei_steps(handler)
    t3_source_checks()
    t4_frontend()

    print(f"\n===== 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 =====")
    if FAIL:
        print("失败项:")
        for f in FAIL:
            print(f"  ✗ {f}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
