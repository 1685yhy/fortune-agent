"""枕边灯语管线测试:锚点提取(分类级) + 独白生成 + 红线校验 + 兜底 + TTS(mock)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest.mock as mock
from src.engines.night_soliloquy import (
    build_soliloquy, extract_anchors, _validate_soliloquy,
    _fallback_soliloquy, synth_lamp_audio)

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

# 1. 锚点提取:分类级(不含原文词)
anchors = extract_anchors("用户今天聊了面试和加班,说最近睡不踏实")
check("锚点分类级", set(anchors) <= {"事业", "感情", "健康", "财运"} and "事业" in anchors)
check("空摘要无锚点", extract_anchors("") == [])

# 2. 兜底模板结构
tpl = _fallback_soliloquy("2026-08-12", {"day_ganzhi": "庚午", "suitable": ["早睡", "静心"],
                                          "unsuitable": ["熬夜"], "quote": "火气偏旺,宜静"})
check("兜底含灯还亮着", "灯还亮着" in tpl)
check("兜底含明日宜忌", "庚午" in tpl)
check("兜底落款", tpl.rstrip().endswith("灯下的人"))

# 3. 红线校验:长度/开场/落款
check("合格独白通过",
      _validate_soliloquy("灯还亮着。" + "今天的事,我记得。" * 15 + "晚安。灯下的人"))
check("超长拒绝", not _validate_soliloquy("灯还亮着。" + "好" * 300 + "晚安。灯下的人"))
check("无落款拒绝", not _validate_soliloquy("灯还亮着。" + "今天的事。" * 10))

class FakeSDAO:
    """最小会话 DAO 桩:可配摘要与当日历史。"""
    def __init__(self, summary="", today=True):
        self.summary = summary; self.today = today
    def get_summary(self, user_id):
        return {"summary": self.summary} if self.summary else None
    def get_history(self, user_id, limit=50):
        return [{"created_at": "2026-08-12 10:00:00"}] if self.today else []

def fake_llm(api_key, messages, **kw):
    return ("灯还亮着。\n今天你聊了工作的事,我记得。\n"
            "我知道你今天很累,辛苦了,先把灯点着。\n"
            "不用急着把话说完,我就在这里陪着你。\n"
            "明日庚午日,火气偏旺,宜早睡,心火自平。\n"
            "你只管睡。天大的事,等太阳升起来再说。\n晚安。灯下的人")

# 4. 有摘要+锚点 → LLM 独白成功(结构完整)
with mock.patch("src.engines.night_soliloquy._default_llm", side_effect=fake_llm):
    r = build_soliloquy("u1", "2026-08-12", FakeSDAO("用户聊了面试"), whisper=True)
    check("独白生成成功", r["fallback"] is False and r["text"].startswith("灯还亮着")
          and len(r["text"]) >= 100 and r["text"].rstrip().endswith("灯下的人"))
    check("独白含锚点", "事业" in r["anchors"])

    # 5. 今日无对话 → 兜底(宁缺毋滥,不编造记忆)
    r2 = build_soliloquy("u2", "2026-08-12", FakeSDAO("", today=False), whisper=True)
    check("无对话兜底", r2["fallback"] is True and "灯还亮着" in r2["text"])

    # 6. 私语关闭 → 兜底(纯金句+宜忌模板)
    r3 = build_soliloquy("u3", "2026-08-12", FakeSDAO("用户聊了面试"), whisper=False)
    check("私语关→兜底", r3["fallback"] is True)

# 7. LLM 异常 → 兜底
with mock.patch("src.engines.night_soliloquy._default_llm", side_effect=Exception("boom")):
    r4 = build_soliloquy("u4", "2026-08-12", FakeSDAO("用户聊了面试"), whisper=True)
    check("LLM 失败兜底", r4["fallback"] is True)

# 8. TTS:成功拼完整 URL;失败返回空串
fake_ok = mock.Mock(); fake_ok.status_code = 200
fake_ok.json = lambda: {"audio_url": "/audio/abc.mp3", "duration_ms": 70000}
fake_bad = mock.Mock(); fake_bad.status_code = 502
with mock.patch("httpx.post", return_value=fake_ok):
    check("TTS 成功", synth_lamp_audio("灯还亮着") == "http://127.0.0.1:8768/audio/abc.mp3")
with mock.patch("httpx.post", return_value=fake_bad):
    check("TTS 失败空串", synth_lamp_audio("灯还亮着") == "")

print(f"\nALL PASS ({ok})")
