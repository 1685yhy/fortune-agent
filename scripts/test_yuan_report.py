"""深度报告管线测试：四章结构/古籍引用/红线/规则兜底/契合详情直出"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.engines.yuan_report import build_report, TRUST_STATEMENT

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

class FakeBazi:
    def __init__(self, bazi, nayin):
        self.bazi = bazi
        self.nayin = nayin

b1 = FakeBazi(bazi=[["甲", "子"], ["丙", "寅"], ["戊", "午"], ["庚", "申"]], nayin=["海中金", "炉中火", "天上火", "石榴木"])
b2 = FakeBazi(bazi=[["乙", "丑"], ["丁", "卯"], ["己", "酉"], ["辛", "亥"]], nayin=["海中金", "炉中火", "大林木", "平地木"])

union = {
    "score": 84, "levelLabel": "情投意合",
    "dimensions": {"wuxing": {"score": 35, "max": 40}, "shengxiao": {"score": 25, "max": 25, "relation": "六合（上等婚配）"},
                   "rizhu": {"score": 17, "max": 35, "relation": "平和（无特殊关系）"}},
    "features": ["六合（上等婚配）", "双天乙贵人", "纳音相克", "五行相克"],
    "relation": "恋人",
    "raw": {
        "hehun_wuxing": {"complement_desc": "五行互补性强", "score_breakdown": {"互补得分": 30, "日主关系得分": 5}},
        "hehun_shengxiao": {"description": "生肖鼠与牛：六合（上等婚配）", "score": 25},
        "hehun_rizhu": {"description": "日支平和，日干比和", "score": 17},
        "compat": {"base_score": 80, "wuxing_relation": "比和", "relation_desc": "金金比和",
                   "complement_bonus": 10, "shensha_bonus": 5, "combo_bonus": 0, "final_score": 95},
    },
    "transient": True,
}

# 1. 无 RAG 无 LLM → 四章完整（全规则兜底）
rep = build_report(union, b1, b2, retriever=None, llm=None)
check("四章齐全", [c["title"] for c in rep["chapters"]] == ["前世今生", "相处模式", "矛盾点与化解", "契合详情"])
check("落款存在", rep["full_text"].endswith(TRUST_STATEMENT))
check("前世今生规则兜底", len(rep["chapters"][0]["content"]) > 30)
check("无引用时 citations 空", rep["citations"] == [])

# 2. 古籍 RAG 引用进入前世今生（书名可见）
class FakeRetriever:
    def search(self, query, category="", top_k=10):
        return [type("H", (), {"text": "申月金旺，逢土生扶，反为有用之才", "source": "穷通宝鉴"})()]
rep2 = build_report(union, b1, b2, retriever=FakeRetriever(), llm=None)
check("引用带书名", rep2["citations"] and rep2["citations"][0]["book"] == "穷通宝鉴")
check("前世今生引原典", "穷通宝鉴" in rep2["chapters"][0]["content"])

# 3. LLM 叙事正常 → 采用 LLM 文本
class FakeLLM:
    api_key = "test"
    model = "deepseek-chat"
    def _call_deepseek_model(self, prompt, model, max_tokens=700, timeout=30):
        return type("R", (), {"response": "前世的一盏灯，化作今生人群里的一眼认出，熟悉感不请自来。"})()
rep3 = build_report(union, b1, b2, retriever=None, llm=FakeLLM())
check("LLM 叙事采用", "前世" in rep3["chapters"][0]["content"])

# 4. LLM 越红线（绝对断言）→ 规则兜底
class BadLLM(FakeLLM):
    def _call_deepseek_model(self, prompt, model, max_tokens=700, timeout=30):
        return type("R", (), {"response": "你们注定离婚，必成怨偶。"})()
rep4 = build_report(union, b1, b2, retriever=None, llm=BadLLM())
check("红线断言被拒回退", "注定" not in rep4["chapters"][0]["content"] and "必离婚" not in rep4["full_text"])

# 5. 矛盾点逐条配化解建议（六合不配建议，纳音相克/五行相克配）
rep5 = build_report(union, b1, b2, retriever=None, llm=None)
ch3 = rep5["chapters"][2]["content"]
check("纳音相克化解", "纳音相克" in ch3)
check("五行相克化解", "相克" in ch3)
check("化解不做承诺", "化灾" not in ch3 and "改运" not in ch3 and "必" not in ch3)

# 6. 契合详情直出（无 LLM）：分数明细可对照
ch4 = rep5["chapters"][3]["content"]
check("契合详情含明细", "40" in ch4 and "25" in ch4 and "35" in ch4 and "情投意合" in ch4)
check("契合详情含双引擎", "合婚引擎" in ch4 and "合盘引擎" in ch4)

# 7. 全文不泄隐私（无出生地/姓名/时辰）
check("全文脱敏", "上海" not in rep5["full_text"] and "北京" not in rep5["full_text"])

print(f"\nALL PASS ({ok})")
