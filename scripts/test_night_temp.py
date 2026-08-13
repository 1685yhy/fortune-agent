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

print(f"\nALL PASS ({ok})")
