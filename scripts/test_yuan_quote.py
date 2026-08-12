"""缘语管线测试：确定性/等级/关系口吻/红线校验/LLM兜底/悬念半句"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.engines.yuan_quote import generate_yuan_quote, _validate

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

FEATS = ["五行互补", "双天乙贵人"]
# 1. 确定性：同输入同输出
q1 = generate_yuan_quote("天作之合", FEATS, relation="恋人", cliffhanger=True)
q2 = generate_yuan_quote("天作之合", FEATS, relation="恋人", cliffhanger=True)
check("同输入结果确定", q1 == q2)

# 2. 等级映射：主句来自对应等级模板库
q3 = generate_yuan_quote("情投意合", FEATS, relation="恋人")
check("等级模板库", "情投意合" not in q3["main"] and len(q3["main"]) > 0)

# 3. 关系口吻：暗恋 vs 夫妻 后缀不同
q4 = generate_yuan_quote("天作之合", FEATS, relation="暗恋")
q5 = generate_yuan_quote("天作之合", FEATS, relation="夫妻")
check("关系口吻差异", q4["suffix"] != "" and q5["suffix"] != "" and q4["suffix"] != q5["suffix"])

# 4. 红线：LLM 返回绝对断言 → 拒绝，保留模板原句
q6 = generate_yuan_quote("天作之合", FEATS, relation="恋人", polish_fn=lambda t: "你们必成良缘，注定在一起")
check("绝对断言被拒", q6["main"] in ["金玉相逢，良缘可期", "双星交辉，缘分天成", "天时地利，恰逢其人"])

# 5. 兜底：polish_fn 抛异常 → 模板原句
def boom(t):
    raise RuntimeError("llm down")
q7 = generate_yuan_quote("天作之合", FEATS, relation="恋人", polish_fn=boom)
check("LLM失败模板兜底", len(q7["main"]) > 0)

# 6. 合法润色被采用
q8 = generate_yuan_quote("天作之合", FEATS, relation="恋人", polish_fn=lambda t: "愿你们两心相印，岁岁年年")
check("合法润色采用", q8["main"] == "愿你们两心相印，岁岁年年")

# 7. 悬念半句：免费档 cliffhanger=True 时 full 以省略号结尾
q9 = generate_yuan_quote("天作之合", FEATS, relation="恋人", cliffhanger=True)
q10 = generate_yuan_quote("天作之合", FEATS, relation="恋人", cliffhanger=False)
check("悬念半句", q9["cliffhanger"].endswith("……") and q10["cliffhanger"] == "")

# 8. 红线校验函数：主句长度 ≤24
check("主句不超24字", all(len(q["main"]) <= 24 for q in (q1, q3, q6, q7, q8, q9)))
check("红线词拒绝", not _validate("你们必成，一定在一起") and _validate("金玉相逢，良缘可期"))

print(f"\nALL PASS ({ok})")
