"""倾诉临时通道测试:temp 会话落库+24h 清理 + handler deepNight 跳过记忆管线+语气层注入"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest.mock as mock
from datetime import datetime, timedelta
from src.storage.session_dao import SessionDAO

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

fd, path = tempfile.mkstemp(suffix='.db'); os.close(fd)
sdao = SessionDAO(path)

# 1. temp 消息落库 + 带过期时间
now = datetime.utcnow()
sdao.add_message("u1", "user", "今天加班到十一点,回来屋里黑着", temp=True)
sdao.add_message("u1", "assistant", "辛苦了。", temp=True)
sdao.add_message("u1", "user", "普通消息")
h = sdao.get_history("u1", limit=10)
check("temp 标记落库",
      sum(1 for m in h if m.get("temp") == 1) == 2
      and sum(1 for m in h if m.get("temp") == 0) == 1)
check("temp 带过期时间", all(m.get("temp_expire_at") for m in h if m.get("temp") == 1))

# 2. 24h 硬清理:未过期不清,过期删除
check("未过期不清理", sdao.cleanup_temp((now + timedelta(hours=10)).isoformat()) == 0)
check("24h 后清理 temp", sdao.cleanup_temp((now + timedelta(hours=25)).isoformat()) == 2)
check("temp 已清空", all(m.get("temp") != 1 for m in sdao.get_history("u1", 10)))

# 3. handler:deepNight 跳过 L2/L3/演化链,消息 temp 落库,语气层注入
import src.bot.handler as handler_mod
from src.bot.handler import MessageHandler
from src.bot.night_persona import NIGHT_TONE_HINT

class FakeLLM:
    api_key = ""
    model = "deepseek-v4-flash"
class FakeDAO:
    db_path = path

with mock.patch.object(handler_mod, "MemberDAO", return_value=None), \
     mock.patch.object(handler_mod, "PreferenceDAO", return_value=None), \
     mock.patch("src.bot.handler.UserMemory"):
    h = MessageHandler(None, None, None, None, None, None, None,
                       FakeLLM(), FakeDAO(), session_dao=sdao)
h._deep_night = {}

analysis = mock.Mock(intent=None, emotion_label=None, needs_soothe=False,
                     soothe_text="", is_sharing=False, facts={})
calls = {"persist": 0, "capture": 0, "evolution": 0}
def spy_persist(uid, msg, facts): calls["persist"] += 1
def spy_capture(uid, msg): calls["capture"] += 1
def spy_evo(uid, topic, reply): calls["evolution"] += 1

captured = {}
def spy_free_chat(msg, user_id, emotion_label=None, extra_hint="", stream_cb=None):
    captured["hint"] = extra_hint or ""
    return "深夜回复"

with mock.patch.object(h, "_analyze_message", return_value=analysis), \
     mock.patch.object(h, "_persist_facts_entries", side_effect=spy_persist), \
     mock.patch.object(h, "_capture_key_event", side_effect=spy_capture), \
     mock.patch.object(h, "_record_evolution", side_effect=spy_evo), \
     mock.patch.object(h, "_free_chat", side_effect=spy_free_chat), \
     mock.patch.object(h, "_run_tool_loop", return_value="深夜回复"), \
     mock.patch.object(h, "_get_welcome_back", return_value=""), \
     mock.patch.object(h, "_consume_quota"), \
     mock.patch("src.bot.handler.is_cacheable", return_value=False):
    h.process("今天加班到十一点,有点想哭", "u2", deep_night=True)
    check("deepNight 跳过 L2 事实", calls["persist"] == 0)
    check("deepNight 跳过事件捕捉", calls["capture"] == 0)
    check("deepNight 跳过演化链", calls["evolution"] == 0)
    check("deepNight 注入深夜语气层", "深夜陪伴模式" in captured["hint"])
    check("deepNight 消息 temp 落库",
          all(m["temp"] == 1 for m in sdao.get_history("u2", 10)))

    # 4. 白天:记忆管线照常,消息非 temp,无深夜语气层
    h.process("今天面试顺利吗", "u3", deep_night=False)
    check("白天记忆管线照常", calls["persist"] == 1 and calls["capture"] == 1)
    check("白天消息非 temp", all(m["temp"] == 0 for m in sdao.get_history("u3", 10)))
    check("白天无深夜语气层", "深夜陪伴模式" not in captured["hint"])
    check("语气层常量存在", len(NIGHT_TONE_HINT) > 50)

# ═══ 终审 #1:temp 泄漏进 L2/L3(隐私红线 P0) ═══

# 5. DAO 层 get_history temp 过滤参数
sdao.add_message("u4", "user", "夜里说的悄悄话:我怕黑", temp=True)
sdao.add_message("u4", "user", "白天的正常消息")
h_all = sdao.get_history("u4", limit=10)
h_clean = sdao.get_history("u4", limit=10, temp=False)
h_temp = sdao.get_history("u4", limit=10, temp=True)
check("get_history 默认含 temp", any(m.get("temp") == 1 for m in h_all))
check("get_history temp=False 排除 temp 倾诉",
      all(m.get("temp") == 0 for m in h_clean)
      and not any("我怕黑" in m["content"] for m in h_clean))
check("get_history temp=True 仅 temp", len(h_temp) == 1 and h_temp[0]["temp"] == 1)

# 6. 压缩路径(_maybe_compact)排除 temp:前夜倾诉 + 白天消息 → 摘要只见白天
class FakeCompactor:
    window_limit = 1024
    trigger_ratio = 0.5
    compact_calls = 0
    inputs = []
    def should_compact(self, messages):
        return True
    def compact(self, messages, prev_summary="", prev_memories=None):
        FakeCompactor.compact_calls += 1
        FakeCompactor.inputs = [m["content"] for m in messages]
        return mock.Mock(summary_text="L2摘要(仅白天)", memories=[],
                         old_count=len(messages), compression_pct=50.0,
                         degraded=False, total_tokens=100)

sdao.add_message("u5", "user", "前夜倾诉:我爱上了一个不该爱的人", temp=True)
for i in range(22):
    sdao.add_message("u5", "user", f"白天日常消息第{i}条:今天天气不错,该吃点什么好呢")
with mock.patch.object(h, "compactor", FakeCompactor()):
    s = h._maybe_compact("u5")
check("压缩触发(白天消息足量)", s == "L2摘要(仅白天)")
check("压缩输入仅白天消息(22条,无 temp 倾诉)",
      len(FakeCompactor.inputs) == 22
      and not any("前夜倾诉" in c for c in FakeCompactor.inputs))

# 7. 仅 temp 消息(深夜纯倾诉)不触发压缩 → 摘要不被污染
sdao.add_message("u6", "user", "只有夜里的话,没有白天的话", temp=True)
with mock.patch.object(h, "compactor", FakeCompactor()):
    s2 = h._maybe_compact("u6")
check("仅 temp 不触发压缩", s2 == "" and FakeCompactor.compact_calls == 1)

# 8. deepNight 跳过缓存读/写(终审应修:夜里回复不命中/不写入白天缓存)
cc = {"get": 0, "set": 0}
def fake_cache_get(msg, uid):
    cc["get"] += 1
    return None
def fake_cache_set(msg, reply, uid):
    cc["set"] += 1
with mock.patch.object(h, "cache") as fake_cache, \
     mock.patch.object(h, "_analyze_message", return_value=analysis), \
     mock.patch.object(h, "_free_chat", side_effect=spy_free_chat), \
     mock.patch.object(h, "_run_tool_loop", return_value="深夜回复"), \
     mock.patch.object(h, "_get_welcome_back", return_value=""), \
     mock.patch.object(h, "_consume_quota"), \
     mock.patch("src.bot.handler.is_cacheable", return_value=True):
    fake_cache.get.side_effect = fake_cache_get
    fake_cache.set.side_effect = fake_cache_set
    h.process("要记得我吗", "u7", deep_night=True)
check("deepNight 跳过缓存读/写", cc["get"] == 0 and cc["set"] == 0)

# 9. 终审 #4:NIGHT_TONE_HINT 含"要帮我记住吗"话术
check("提示词含'要帮你记住吗'", "要帮你记住吗" in NIGHT_TONE_HINT)

print(f"\nALL PASS ({ok})")
