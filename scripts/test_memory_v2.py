#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
记忆架构 v4（L1/L2/L3）+ 数据资产字段 验证（方案《CONVERSATION_SYSTEM_DESIGN_V2》§5/§7）

Hermetic（无网络，全部 LLM 调用注入 mock）：

L1 滚动窗口（§5.3）：
  1. 20 轮对话 → 动态窗口轮数正确（预算内尽量多）
  2. 关键事实（八字）在 50 轮后仍在（保底，不进压缩）

L2 增量摘要（§5.4）：
  3. 60 轮长会话 → 触发摘要 → <summary> 含要点 + <memories> 持久事实
  4. 二次触发摘要收敛（摘要的摘要，多级收敛不膨胀）
  5. 摘要失败降级：保留最近 3 轮 + 截断（degraded）
  6. 摘要落库（session_summaries 加密存取）往返

L3 长期记忆（§5.5）：
  7. 写入 profile/event → TTL 过期剔除
  8. 按需召回返回相关记忆（关键词匹配）
  9. 冲突覆盖：同 type+subject 新信息覆盖旧（八字更新）
 10. 隐私：查看/删除/清空；注销级 clear_all 删文件

数据资产字段（§7.2/§7.3）：
 11. 对话后 intent/emotion/tool_calls/retrieval_hit/model/安全标记落库正确
 12. 工具命中/未命中/未使用 三种 retrieval_hit 状态
 13. L3 事件捕获（工作/感情关键陈述）→ event 条目
 14. 导出脚本 JSONL 格式正确 + 脱敏（无 openid/个人信息）

用法：
  .venv/bin/python scripts/test_memory_v2.py
退出码：0=全部通过；1=有失败
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from src.engines.message_analyzer import MessageAnalysis
from src.bot.handler import MessageHandler, _assemble_context, estimate_tokens
from src.bot.memory_compactor import MemoryCompactor
from src.bot.tool_calls import ToolResult
from src.memory.user_memory import UserMemory
from src.storage.dao import UserDAO
from src.storage.member_dao import MemberDAO
from src.storage.session_dao import SessionDAO
from src.llm.client import FortuneLLM

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = ""):
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail[:200]}" if detail and not cond else ""))
    (PASS if cond else FAIL).append(name)


# ---------------------------------------------------------------------------
# 装配：临时 DB + 可 mock 的 handler（镜像 test_ai_native.build_handler）
# ---------------------------------------------------------------------------

_TMP = tempfile.mkdtemp(prefix="mem_v2_")
TMP_DB = os.path.join(_TMP, "fortune.db")


def build_handler() -> MessageHandler:
    dao = UserDAO(TMP_DB)
    member_dao = MemberDAO(TMP_DB)
    session_dao = SessionDAO(TMP_DB)
    llm = FortuneLLM(api_key="test-key", model="deepseek-flash",
                     deep_model="deepseek-flash", provider="deepseek")
    handler = MessageHandler(
        None, None, None, None, None, None, None, llm, dao,
        dream_engine=None, hehun_engine=None, qimen_engine=None,
        xingming_engine=None, session_dao=session_dao, member_dao=member_dao,
    )
    return handler


def ensure_paid(uid: str, member_dao: MemberDAO):
    try:
        conn = member_dao._connect()
        conn.execute(
            """INSERT INTO memberships
               (user_id, plan, started_at, expires_at, queries_used, queries_limit, auto_renew)
               VALUES (?, 'basic', datetime('now'), datetime('now','+30 days'), 0, 500, 0)
               ON CONFLICT(user_id) DO UPDATE SET plan='basic', queries_limit=500,
                   started_at=datetime('now'), expires_at=datetime('now','+30 days'), auto_renew=0""",
            (uid,),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# L1 滚动窗口
# ---------------------------------------------------------------------------

def test_l1_window(handler: MessageHandler):
    print("\n===== L1 滚动窗口（§5.3） =====")
    # 20 轮（40 条消息），每条约 40 字 → 估算 token 每条约 28
    history = [
        {"role": "user" if i % 2 == 0 else "assistant",
         "content": f"第{i}轮：用户咨询事业运与感情运，助手结合八字给出命理分析回复。"}
        for i in range(40)
    ]
    # 1) 预算内尽量多：默认窗口 16384 → 全部 40 条保留
    msgs = _assemble_context(history, profile="", summary="", current="当前消息")
    kept = [m for m in msgs if m["role"] in ("user", "assistant")]
    check("L1 20轮全部在预算内保留（40条）", len(kept) == 40, f"len={len(kept)}")
    check("L1 时间正序（旧→新）",
          kept[0]["content"] == history[0]["content"]
          and kept[-1]["content"] == history[-1]["content"])

    # 2) 小预算：窗口 2000 / 输出预留 1024 → 预算 = 2000-1024-600(检索预留)
    budget = 2000 - 1024 - 600
    msgs2 = _assemble_context(history, profile="", summary="", current="",
                              window_limit=2000, output_reserve=1024)
    kept2 = [m for m in msgs2 if m["role"] in ("user", "assistant")]
    # 复算期望保留条数（与实现同款贪心：从最新往回填）
    expected = 0
    used = 0
    for m in reversed(history):
        t = estimate_tokens(m["content"])
        if used + t > budget and expected > 0:
            break
        used += t
        expected += 1
    check("L1 小预算下保留条数与预算计算一致",
          len(kept2) == expected, f"got={len(kept2)} expected={expected}")
    check("L1 截断时当前消息仍保留（保底）",
          kept2 and kept2[-1]["content"] == history[-1]["content"])

    # 3) 关键事实保底：50 轮后八字仍在（原文，不进压缩）
    hist50 = [
        {"role": "user" if i % 2 == 0 else "assistant",
         "content": f"第{i}轮：用户聊家常，助手自然回应闲聊内容。"}
        for i in range(100)
    ]
    key_facts = ("用户八字：庚午 辛巳 乙酉 壬午（已保存，别再问出生信息）",)
    msgs3 = _assemble_context(hist50, profile="", summary="", current="",
                              key_facts=key_facts, window_limit=2000,
                              output_reserve=1024)
    kept3 = [m for m in msgs3 if m["role"] in ("user", "assistant")]
    check("L1 50轮后历史被截断（预算受限）", len(kept3) < len(hist50),
          f"kept={len(kept3)} total={len(hist50)}")
    sys_facts = [m["content"] for m in msgs3 if m["role"] == "system"]
    check("L1 关键事实（八字）50轮后仍在（保底原文）",
          any("庚午" in c and "别再问出生信息" in c for c in sys_facts),
          str(sys_facts)[:200])

    # 4) handler 侧：_collect_key_facts 汇集 dao 八字 + L3 is_key 条目
    uid = "mem_l1_facts"
    handler.dao.save_user_bazi(uid, {
        "year": 1990, "month": 5, "day": 20, "hour": 11,
        "city": "北京", "gender": "男",
        "bazi": ["庚午", "辛巳", "乙酉", "壬午"],
    })
    handler.memory_system.add_entry(uid, "event", "用户正在找工作，想换行业",
                                    subject="找工作", is_key=True,
                                    ttl_days=90, source="event_capture")
    facts = handler._collect_key_facts(uid)
    check("L1 handler 关键事实含八字", any("庚午" in f for f in facts), str(facts)[:200])
    check("L1 handler 关键事实含 L3 标记条目（找工作）",
          any("找工作" in f for f in facts), str(facts)[:200])

    # 5) 画像/摘要注入顺序：system 前置（关键事实 → 画像 → 摘要）
    msgs4 = _assemble_context(history[:6], profile="画像P", summary="摘要S",
                              current="", key_facts=("事实F",))
    sys4 = [m for m in msgs4 if m["role"] == "system"]
    check("L1 组装顺序：关键事实→画像→摘要→历史",
          len(sys4) == 3 and "事实F" in sys4[0]["content"]
          and "画像P" in sys4[1]["content"] and "摘要S" in sys4[2]["content"],
          str(sys4)[:200])


# ---------------------------------------------------------------------------
# L2 增量摘要
# ---------------------------------------------------------------------------

def test_l2_compactor(handler: MessageHandler):
    print("\n===== L2 增量摘要（§5.4） =====")

    scripted = {
        "first": (
            "<summary>\n对话要点：用户咨询事业运与感情运，两次使用排盘工具；"
            "用户状态平稳但为工作焦虑。\n关键结论：明年事业有转机。\n"
            "未完事项：想了解下半年财运。\n</summary>\n<memories>\n"
            "profile|用户八字已排盘：庚午 辛巳 乙酉 壬午（日主乙木）|1.0|\n"
            "event|用户正在找工作|0.9|90\n"
            "preference|用户偏好简短回答|0.8|\n</memories>"
        ),
        "second": (
            "<summary>\n对话要点：要点A 要点B（含上次摘要，已合并收敛）。\n"
            "未完事项：下半年财运。\n</summary>\n<memories>\n"
            "profile|用户八字已排盘：庚午 辛巳 乙酉 壬午（日主乙木）|1.0|\n"
            "event|用户正在找工作|0.9|90\n</memories>"
        ),
    }
    calls = {"n": 0}

    def fake_llm(api_key, messages, **kw):
        calls["n"] += 1
        prompt = messages[-1]["content"]
        # 提示词含滚动摘要标签（此前已积累的摘要）→ 返回"摘要的摘要"（收敛）
        if "已积累" in prompt:
            return scripted["second"]
        return scripted["first"]

    compactor = MemoryCompactor(api_key="fake", model="deepseek-flash",
                                window_limit=1000, trigger_ratio=0.7,
                                chunk_ratio=0.25, llm_fn=fake_llm)

    # 1) 触发判断：60 轮（120 条）→ 超阈值；短会话不触发
    msgs = [
        {"role": "user" if i % 2 == 0 else "assistant",
         "content": f"第{i}轮：用户咨询事业运，助手给出命理分析回复。"}
        for i in range(120)
    ]
    check("L2 60轮长会话触发压缩", compactor.should_compact(msgs))
    check("L2 短会话不触发",
          not compactor.should_compact(msgs[:6]))

    # 2) 首次压缩：摘要含要点 + memories 持久事实 + 压缩率 90%+
    res = compactor.compact(msgs)
    check("L2 拆分：旧消息被压缩、尾部原样保留",
          res.old_count > 0 and res.recent_count > 0,
          f"old={res.old_count} recent={res.recent_count} blocks={res.blocks}")
    check("L2 <summary> 含对话要点", "要点" in res.summary_text, res.summary_text[:150])
    check("L2 <summary> 含未完事项", "未完事项" in res.summary_text, res.summary_text[:150])
    mem_ev = [m for m in res.memories if m["type"] == "event"]
    mem_prof = [m for m in res.memories if m["type"] == "profile"]
    check("L2 <memories> 提取 event（找工作，TTL 90天）",
          any("找工作" in m["content"] and m["ttl_days"] == 90 for m in mem_ev),
          str(mem_ev)[:200])
    check("L2 <memories> 提取 profile（八字）", any("八字" in m["content"] for m in mem_prof))
    check("L2 压缩率 ≥ 90%", res.compression_pct >= 0.90,
          f"pct={res.compression_pct}")
    check("L2 无降级标记", not res.degraded)

    # 3) 二次触发收敛：新摘要包含旧摘要要点，且不膨胀
    res2 = compactor.compact(
        msgs, prev_summary=res.summary_text,
        prev_memories=[m["content"] for m in res.memories])
    check("L2 二次压缩收敛（含旧摘要要点）",
          "要点A" in res2.summary_text and "要点B" in res2.summary_text,
          res2.summary_text[:200])
    check("L2 二次摘要不膨胀", len(res2.summary_text) <= len(res.summary_text) + 200,
          f"{len(res2.summary_text)} vs {len(res.summary_text)}")

    # 4) 降级：LLM 全失败 → 保留最近 3 轮 + 截断
    def failing_llm(api_key, messages, **kw):
        raise RuntimeError("upstream down")

    compactor2 = MemoryCompactor(api_key="fake", window_limit=1000,
                                 chunk_ratio=0.25, llm_fn=failing_llm)
    res3 = compactor2.compact(msgs)
    check("L2 失败降级标记 degraded", res3.degraded)
    check("L2 降级保留最近 3 轮内容（截断占位）",
          "降级" in res3.summary_text and "第" in res3.summary_text,
          res3.summary_text[:200])

    # 5) 摘要落库往返（session_summaries 加密存取）
    uid = "mem_l2_sum"
    handler.session_dao.save_summary(uid, res.summary_text, res.memories,
                                     model="deepseek-flash",
                                     message_count=120, token_count=res.total_tokens)
    got = handler.session_dao.get_summary(uid)
    check("L2 摘要落库往返一致",
          got and got["summary"] == res.summary_text
          and len(got["memories"]) == len(res.memories),
          str(got)[:200] if got else "None")
    # 库内为密文（内容加密落库）
    conn = handler.session_dao._connect()
    raw = conn.execute(
        "SELECT summary FROM session_summaries WHERE user_id=?", (uid,)).fetchone()
    conn.close()
    check("L2 摘要加密落库（密文非明文）",
          raw and raw[0] and raw[0] != res.summary_text and ":" in raw[0],
          str(raw)[:80] if raw else "None")
    handler.session_dao.clear_summary(uid)
    check("L2 摘要可清除", handler.session_dao.get_summary(uid) is None)

    # 6) handler._maybe_compact：真实长会话触发（注入 scripted LLM）
    import src.llm.client as llm_mod
    orig = llm_mod.deepseek_anthropic_completion
    compactor.trigger_ratio = 0.1  # 触发放松：仅测试路径
    handler.compactor = compactor
    llm_mod.deepseek_anthropic_completion = fake_llm
    try:
        uid2 = "mem_l2_trigger"
        for i in range(60):
            handler.session_dao.add_message(
                uid2, "user", f"第{i}轮：用户咨询事业运，看看什么时候能升职。")
            handler.session_dao.add_message(
                uid2, "assistant", f"第{i}轮回复：结合你的命局，明年事业有转机。")
        summary_text = handler._maybe_compact(uid2)
        check("L2 handler 触发压缩并返回摘要", bool(summary_text) and "要点" in summary_text,
              summary_text[:150])
        check("L2 handler 压缩后 <memories> 转入 L3",
              any("找工作" in e["content"] for e in
                  handler.memory_system.list_entries(uid2)),
              str(handler.memory_system.list_entries(uid2))[:200])
        # 摘要已存库 → 再次触发读取 prev（收敛路径）
        handler._maybe_compact(uid2)
        got2 = handler.session_dao.get_summary(uid2)
        check("L2 二次触发读取旧摘要（摘要的摘要）", got2 is not None and got2["summary"], "")
    finally:
        llm_mod.deepseek_anthropic_completion = orig
        handler.compactor = None


# ---------------------------------------------------------------------------
# L3 长期记忆
# ---------------------------------------------------------------------------

def test_l3_memory():
    print("\n===== L3 长期记忆（§5.5） =====")
    mem_dir = os.path.join(_TMP, "memory")
    mem = UserMemory(base_dir=mem_dir)
    uid = "mem_l3_user"

    # 1) 写入 profile/event/preference/topic
    mem.add_entry(uid, "profile", "用户八字已排盘：庚午 辛巳 乙酉 壬午（日主乙木）",
                  subject="bazi", is_key=True, source="bazi_analysis")
    mem.add_entry(uid, "event", "用户正在找工作", subject="找工作",
                  ttl_days=90, is_key=True, source="event_capture")
    mem.add_entry(uid, "preference", "用户偏好简短回答", subject="length")
    mem.add_entry(uid, "topic", "关注话题：事业（已聊 1 次）", subject="career")
    entries = mem.list_entries(uid)
    check("L3 四种类型写入成功", len(entries) == 4 and
          {e["type"] for e in entries} == {"profile", "event", "preference", "topic"},
          str([e["type"] for e in entries]))

    # 2) TTL 过期剔除（老事件：30 天前 → 已过期）
    old = "2025-01-01T00:00:00"
    mem.add_entry(uid, "event", "用户当时在找工作（过期事件）", subject="旧找工作",
                  ttl_days=30, created_at=old)
    entries = mem.list_entries(uid)
    check("L3 TTL 过期条目被剔除",
          not any(e["subject"] == "旧找工作" for e in entries),
          str([e["subject"] for e in entries]))
    check("L3 未过期条目保留（90天事件）",
          any(e["subject"] == "找工作" for e in entries))

    # 3) 冲突覆盖：同 type+subject → 新信息覆盖旧（八字更新）
    mem.add_entry(uid, "profile",
                  "用户八字已更新：甲子 乙丑 丙寅 丁卯（日主丙火）",
                  subject="bazi", is_key=True, source="bazi_analysis")
    profs = [e for e in mem.list_entries(uid, "profile") if e["subject"] == "bazi"]
    check("L3 冲突覆盖：同主题仅保留最新一条", len(profs) == 1, str(profs)[:200])
    check("L3 覆盖后内容为新八字", "甲子" in profs[0]["content"] and "庚午" not in profs[0]["content"],
          profs[0]["content"][:150])

    # 4) 按需召回（关键词匹配，embedder=None 纯关键词）
    rels = mem.get_relevant_memories(uid, "我最近在找工作，要不要跳槽？", top_k=5)
    check("L3 按需召回：工作话题返回 event 条目",
          any("找工作" in r["content"] for r in rels), str(rels)[:250])
    rels2 = mem.get_relevant_memories(uid, "帮我看看我的八字怎么样", top_k=5)
    check("L3 按需召回：八字话题返回 profile 条目",
          any("八字" in r["content"] for r in rels2), str(rels2)[:250])
    rels3 = mem.get_relevant_memories(uid, "今天天气不错呢", top_k=5)
    check("L3 无关查询不召回（或得分最低）", not rels3 or rels3[0]["score"] <= 1.0, "")

    # 5) 关键事实保底
    facts = mem.get_key_facts(uid)
    check("L3 get_key_facts 返回 is_key 条目",
          any("八字" in f for f in facts) and any("找工作" in f for f in facts),
          str(facts)[:200])

    # 6) 隐私：查看/删除/清空/注销
    n_before = len(mem.list_entries(uid))
    removed = mem.delete_entry(uid, subject="找工作")
    check("L3 按 subject 删除", removed == 1 and
          not any(e["subject"] == "找工作" for e in mem.list_entries(uid)))
    eid = mem.list_entries(uid, "preference")[0]["id"]
    removed = mem.delete_entry(uid, entry_id=eid)
    check("L3 按 entry_id 删除", removed == 1)
    n_clear = mem.clear_entries(uid)
    check("L3 清空条目", n_clear == n_before - 2 and mem.list_entries(uid) == [])
    mem.add_entry(uid, "preference", "测试残留", subject="x")
    check("L3 注销级 clear_all 删除记忆文件", mem.clear_all(uid) == 1 and
          not mem.has_memory(uid))

    # 7) 与旧接口兼容（remember/recall/get_context 不受影响）
    mem.remember(uid, "last_topic", "career")
    check("L3 旧接口兼容", mem.recall(uid, "last_topic") == "career" and
          mem.get_context(uid).get("last_topic") == "career")


# ---------------------------------------------------------------------------
# 阶段 2：数据资产字段
# ---------------------------------------------------------------------------

def test_data_fields(handler: MessageHandler):
    print("\n===== 阶段 2：数据资产字段（§7.2） =====")
    import src.llm.client as llm_mod
    uid = "mem_d_fields"
    ensure_paid(uid, handler.member_dao)

    # 固定分析结果：自由对话 + happy
    fixed_analysis = MessageAnalysis(needs_soothe=False, soothe_text="",
                                     emotion_label="happy", intent=None,
                                     is_sharing=False)
    orig_analyze = handler._analyze_message
    orig_exec = handler._execute_tool_call
    orig_completion = llm_mod.deepseek_anthropic_completion

    script = [
        "<tool_call>检索: 梦见蛇 征兆</tool_call>我查查古籍里怎么说",
        "古籍里说蛇主财，结合你的情况来看是吉兆，别太担心。",
    ]
    counter = {"n": 0}

    def fake_completion(api_key, messages, **kw):
        i = counter["n"]
        counter["n"] += 1
        return script[min(i, len(script) - 1)]

    handler._analyze_message = lambda msg, user_id="": fixed_analysis
    handler._execute_tool_call = lambda name, params, user_id: ToolResult(
        "检索", True, "古籍检索结果（Top 5）：1.【周公解梦】\"梦见蛇主财\"（相关度 0.92）")
    llm_mod.deepseek_anthropic_completion = fake_completion
    try:
        # 1) 工具命中：retrieval_hit=hit + tool_calls 落库
        counter["n"] = 0
        reply = handler.process("梦见蛇是什么预兆", uid)
        rows = handler.session_dao.get_history(uid, limit=10)
        usr = [r for r in rows if r["role"] == "user"][-1]
        asst = [r for r in rows if r["role"] == "assistant"][-1]
        check("D1 用户消息 emotion 落库", usr["emotion"] == "happy", usr["emotion"] or "None")
        check("D1 用户消息 model 落库", bool(usr["model"]), usr["model"] or "None")
        check("D1 assistant retrieval_hit=hit", asst["retrieval_hit"] == "hit",
              asst["retrieval_hit"] or "None")
        tc = json.loads(asst["tool_calls"] or "[]")
        check("D1 tool_calls JSON 含 检索/参数/命中",
              tc and tc[0]["type"] == "检索" and "梦见蛇" in tc[0]["params"]
              and tc[0]["hit"] is True, str(tc)[:200])
        check("D1 assistant model 落库", bool(asst["model"]), asst["model"] or "None")
        check("D1 回复正常（工具结果注入）", "古籍里说蛇主财" in reply, reply[:100])

        # 2) 工具未命中：retrieval_hit=miss
        uid2 = "mem_d_miss"
        ensure_paid(uid2, handler.member_dao)
        handler._execute_tool_call = lambda name, params, user_id: ToolResult(
            "检索", False, "本次未命中古籍库。", needs_info=True)
        counter["n"] = 0
        handler.process("梦见蛇是什么预兆", uid2)
        rows = handler.session_dao.get_history(uid2, limit=10)
        asst2 = [r for r in rows if r["role"] == "assistant"][-1]
        check("D2 检索未命中 → retrieval_hit=miss", asst2["retrieval_hit"] == "miss",
              asst2["retrieval_hit"] or "None")

        # 3) 无工具：retrieval_hit=unused
        uid3 = "mem_d_unused"
        ensure_paid(uid3, handler.member_dao)
        counter["n"] = 0
        script2 = ["今天天气不错，随便聊聊。"]
        handler._execute_tool_call = lambda name, params, user_id: ToolResult("?", False, "")
        llm_mod.deepseek_anthropic_completion = (lambda api_key, messages, **kw: script2[0])
        handler.process("随便聊聊", uid3)
        rows = handler.session_dao.get_history(uid3, limit=10)
        asst3 = [r for r in rows if r["role"] == "assistant"][-1]
        check("D3 无工具 → retrieval_hit=unused", asst3["retrieval_hit"] == "unused",
              asst3["retrieval_hit"] or "None")

        # 4) 安全标记：自伤信号留痕
        uid4 = "mem_d_safety"
        ensure_paid(uid4, handler.member_dao)
        counter["n"] = 0
        llm_mod.deepseek_anthropic_completion = fake_completion
        handler.process("我想自杀，感觉活着没意思", uid4)
        rows = handler.session_dao.get_history(uid4, limit=10)
        usr4 = [r for r in rows if r["role"] == "user"][-1]
        asst4 = [r for r in rows if r["role"] == "assistant"][-1]
        check("D4 自伤信号 → safety_flag=self_harm_referral",
              usr4["safety_flag"] == "self_harm_referral"
              and asst4["safety_flag"] == "self_harm_referral",
              f"{usr4['safety_flag']} / {asst4['safety_flag']}")

        # 5) L3 事件捕获：工作/感情关键陈述
        uid5 = "mem_d_event"
        ensure_paid(uid5, handler.member_dao)
        counter["n"] = 0
        handler.process("我正在找工作，最近很焦虑，帮我看看", uid5)
        evs = handler.memory_system.list_entries(uid5)
        check("D5 关键陈述（找工作）→ L3 event 条目",
              any(e["type"] == "event" and "找工作" in e["content"] and e["is_key"]
                  for e in evs), str(evs)[:250])

        # 6) 八字排盘 → L3 profile 关键事实（直连方法，含 dao 持久化）
        uid6 = "mem_d_bazi"
        handler._persist_l3_bazi(uid6, {
            "year": 1990, "month": 5, "day": 20, "gender": "男",
            "bazi": ["庚午", "辛巳", "乙酉", "壬午"], "day_master": "乙木",
        })
        facts = handler.memory_system.list_entries(uid6, "profile")
        check("D6 八字 → L3 profile is_key 条目",
              any(e["subject"] == "bazi" and e["is_key"] and "庚午" in e["content"]
                  for e in facts), str(facts)[:250])

        # 7) intent 落库：排盘意图走真实 intent 路径（_analyze_message 仍为 fixed）
        uid7 = "mem_d_intent"
        ensure_paid(uid7, handler.member_dao)
        fixed_bazi = MessageAnalysis(needs_soothe=False, soothe_text="",
                                     emotion_label="neutral", intent="bazi")
        handler._analyze_message = lambda msg, user_id="": fixed_bazi
        counter["n"] = 0
        handler.process("帮我看看运势", uid7)
        rows = handler.session_dao.get_history(uid7, limit=10)
        asst7 = [r for r in rows if r["role"] == "assistant"][-1]
        check("D7 intent 落库（bazi）", asst7["intent"] == "bazi", asst7["intent"] or "None")
    finally:
        handler._analyze_message = orig_analyze
        handler._execute_tool_call = orig_exec
        llm_mod.deepseek_anthropic_completion = orig_completion


def test_export_scripts():
    print("\n===== 阶段 2：训练集导出（§7.3/§7.4） =====")
    import export_training_data as exp

    # 种子数据：好评咨询 + 会话
    conn = exp._connect(TMP_DB)
    conn.execute(
        """INSERT INTO consultations (user_id, question, intent, chart_data, analysis, feedback)
           VALUES ('mem_l3_user', '帮我看看事业运', 'bazi', '', '事业运势分析：明年有升职机会，注意与上级沟通。', 'positive')""")
    conn.execute(
        """INSERT INTO sessions (user_id, role, content, intent, emotion, retrieval_hit, model)
           VALUES ('mem_l3_user', 'user', '你好，我的openid是oabc1234567890defghijklmn，想算感情', 'free_chat', 'neutral', 'unused', 'deepseek-flash')""")
    conn.execute(
        """INSERT INTO sessions (user_id, role, content, intent, emotion, retrieval_hit, model)
           VALUES ('mem_l3_user', 'assistant', '你好呀，感情的事咱们慢慢聊，电话13712345678别告诉别人。', 'free_chat', 'neutral', 'unused', 'deepseek-flash')""")
    conn.commit()
    conn.close()

    out1 = os.path.join(_TMP, "train_consult.jsonl")
    rc = exp.main(["--db", TMP_DB, "--out", out1, "--source", "consultations", "--limit", "100"])
    lines = Path(out1).read_text(encoding="utf-8").splitlines()
    check("E1 导出脚本退出码 0", rc == 0)
    check("E1 consultations 好评导出非空", len(lines) >= 1, f"lines={len(lines)}")
    rec = json.loads(lines[0])
    check("E1 JSONL 格式：messages role/content 对",
          rec["messages"][0]["role"] == "user"
          and rec["messages"][1]["role"] == "assistant"
          and "事业" in rec["messages"][0]["content"]
          and "升职" in rec["messages"][1]["content"],
          json.dumps(rec, ensure_ascii=False)[:250])
    check("E1 meta 带 intent/feedback", rec["meta"].get("intent") == "bazi"
          and rec["meta"].get("feedback") == "positive")

    out2 = os.path.join(_TMP, "train_sessions.jsonl")
    rc = exp.main(["--db", TMP_DB, "--out", out2, "--source", "sessions", "--limit", "100"])
    lines2 = Path(out2).read_text(encoding="utf-8").splitlines()
    check("E2 sessions 导出非空", len(lines2) >= 1, f"lines={len(lines2)}")
    # 找到含 openid 的原始对话行（脱敏后应变为 [openid] 标记）
    rec2 = None
    for line in lines2:
        if "[openid]" in line:
            rec2 = json.loads(line)
            break
    check("E2 含 openid 的对话行存在", rec2 is not None, "")
    full = json.dumps(rec2, ensure_ascii=False)
    check("E2 脱敏：无原始 openid", "oabc1234567890" not in full
          and "openid" not in rec2, full[:200])
    check("E2 脱敏：手机号打码", "[手机号]" in full, full[:200])
    check("E2 不导出 user_id", "user_id" not in rec2, "")

    # 分析脚本可运行（JSON 输出）
    import analyze_conversations as ana
    data = ana.analyze(TMP_DB, "")
    check("E3 分析脚本：概览统计", data["overview"]["messages"] >= 4
          and data["overview"]["users"] >= 1, json.dumps(data["overview"], ensure_ascii=False)[:200])
    check("E3 分析脚本：情绪分布含 neutral",
          any(d["key"] == "neutral" for d in data["emotion_distribution"]),
          str(data["emotion_distribution"])[:200])
    check("E3 分析脚本：检索命中分布含 unused",
          any(d["key"] == "unused" for d in data["retrieval_hit_distribution"]))
    check("E3 分析脚本：反馈联动按意图含 bazi",
          any(d["intent"] == "bazi" and d["positive_rate"] == 100.0
              for d in data["feedback_by_intent"]),
          str(data["feedback_by_intent"])[:200])


def main():
    t0 = time.time()
    handler = build_handler()
    test_l1_window(handler)
    test_l2_compactor(handler)
    test_l3_memory()
    test_data_fields(handler)
    test_export_scripts()

    print(f"\n===== 结果：{len(PASS)} 通过 / {len(FAIL)} 失败"
          f"（{time.time() - t0:.1f}s） =====")
    if FAIL:
        for name in FAIL:
            print(f"  [FAIL] {name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
