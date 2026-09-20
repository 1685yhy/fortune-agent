"""k63-r2（D）：schema/结构文本泄漏守卫 —— 纯规则，零 LLM、零网络。

事故形态（2026-09-20 实跑实测）：`advisor_v2._call_llm` 挂 **GLM-4-Flash 降级链**
（`src/llm/client.py:glm_openai_completion`），小模型把 **prompt 里给模型看的
JSON schema 示例**原样当字段值回显 → 用户直接看到"对模型说的话"（样本见
`_REAL_GLM_ECHO`，逐字来自 `/dev/shm/k63_live_out.json` 的 unknown 档 serendipity）。

本文件锁四件事：
A 正例（**形态**清单，不是匹配某段 schema 原文）→ 必须被清洗（整段置空）
B 反例（合法表达）→ 必须**一字不动**（中文引号 / 全角括号 / `{}` / `[1]` 引用编号 /
  "30字以内"日常建议 / 真实实跑建议文案）
C 接线：`advisor_v2.generate()` 字段层真的挂了这道守卫；`advice` 被置空 → 整条摘除；
  全摘除 → 退回通用兜底（不出现空壳行、不把模板说明给用户）
D 切分：`scrub_turn`（词表：称谓/神煞）语义未被改动，与 schema 守卫互不干扰

运行：`TMPDIR=/dev/shm OMP_NUM_THREADS=4 nice -n 10 \
  /home/a/fortune-agent/.venv/bin/python -m pytest tests/test_k63_schema_echo_guard.py -q`
"""
import dataclasses
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from src.engines.advisor_v2 import AdaptiveAdvisor, LIFE_DOMAINS  # noqa: E402
from src.engines.bazi import BaziEngine  # noqa: E402
from src.utils.fact_guard import (guard_schema_echo,  # noqa: E402
                                  is_schema_echo, schema_echo_hits,
                                  scrub_schema_echo, scrub_turn)


# ============================================================
# A：正例 —— 形态清单（每条都是"给模型看的话"的**形态**，非某段原文）
# ============================================================

# 逐字实录（GLM-4-Flash 降级档，2026-09-20 实跑 unknown 档 serendipity）
_REAL_GLM_ECHO = (
    "你问的是[领域]，但你的命盘同时提示了其他重要信息。格式："
    "'顺便说一句（你可能没问但很重要）：' + 简要说明其他领域的好时机与需注意的风险"
    "（80字以内）。如果实在没有特别信息，输出空字符串。"
)

POSITIVE_FORMS = [
    # 实测样本（GLM-4-Flash 降级档真回显，逐字）
    ("real_glm_echo", _REAL_GLM_ECHO),
    # schema 各字段的 spec 文本（prompt 里给模型看的说明；换成别的 prompt 也仍是这个形态）
    ("spec_advice", "具体的行动建议，1-2句话，精炼有力，必须结合用户的八字数据给出个性化理由"),
    ("spec_timing", "最佳行动时间窗口，必须包含具体日期范围（如'2027年9月15日-10月15日'）"),
    ("spec_daily_tip", "一句今日小建议（30字以内）"),
    ("spec_style_notes", "一句话总结用户命格特点和建议（30字以内）"),
    ("spec_confidence", "high/medium/low"),
    # JSON 键形态回显
    ("json_key", '"advice": "具体的行动建议，1-2句话，精炼有力"'),
    ("json_key_serendipity", '"serendipity": "顺便说一句：..."'),
    # JSON 结构片段
    ("json_array_frag", 'actions: [{"category": "事业", "advice": "..."}]'),
    ("json_braces_only", '输出形如 [{"category": "x"}] 的数组'),
    # 中文占位符
    ("placeholder_cn", "针对[用户姓名]的命盘给出个性化建议"),
    ("placeholder_another", "在[时间段]内推进最合适"),
    # 取值域枚举（schema 的 enum 回显）
    ("enum_domain", "confidence 只能填 high/medium/low"),
    # 元输出指令（对模型说的话）
    ("meta_json_only", "请只输出JSON对象，不要markdown代码块标记，不要其他文字"),
    ("meta_empty_string", "如果实在没有特别信息，输出空字符串。"),
    # 弱信号 ×2（阈值化：单条弱信号不判，两条才判）
    ("weak_pair", "格式：按示例给出；控制在30字以内"),
    ("weak_pair_meta", "必须包含具体日期范围，且不超过20字"),
]


@pytest.mark.parametrize("case,text", POSITIVE_FORMS, ids=[c for c, _ in POSITIVE_FORMS])
def test_positive_forms_are_cleaned(case, text):
    """形态命中 → 判泄漏 + 整段置空（返回空串，并带回命中明细）。"""
    assert is_schema_echo(text) is True, case
    cleaned, hits = guard_schema_echo(text)
    assert cleaned == "", case          # 整段置空
    assert hits, case                   # 命中明细非空（可排障）


# ============================================================
# B：反例 —— 合法表达必须一字不动（防误杀，含真实实跑文案）
# ============================================================

NEGATIVE_FORMS = [
    # 真实实跑产物（glm-4-flash，2026-09-20；逐字，见 /dev/shm/k63_live_out.json）
    ("live_male_career",
     "考虑转向金融行业，特别是银行或证券领域，这些行业与金和水五行相契合，有利于事业发展。"),
    ("live_female_career",
     "在2027年9月15日至10月15日期间，考虑转换到与水木相关的行业，如教育、文化或医疗，"
     "这些行业能帮助你发挥潜力，同时减轻金火的压力。"),
    ("live_unknown_tips",
     "保持乐观，今天的你特别适合与人沟通。"),
    ("live_style_notes",
     "你的命格特点是偏财格，喜水木，忌金火土，适合从事与水木相关的行业，注重身心健康和人际关系。"),
    # 中文引号（U+201C/U+201D）——不是 ASCII 引号，不得命中 JSON 键形态
    ("cn_quotes", "你命里“食伤生财”，建议把“表达欲”变成作品，别憋着。"),
    ("cn_quotes_with_colon", "记住这句话：“赚钱靠金，守钱靠水”。"),
    # 合法花括号（自然语言里的分组，不是 JSON）
    ("brace_group", "建议给自己设个 {每天读书30分钟} 的小计划，别硬扛。"),
    ("brace_math", "火土占 {3/8} 的比例，需要金水来平衡。"),
    # “N字以内”出现在**日常建议**里（单条弱信号，不得单独定罪）
    ("daily_char_limit", "每天写30字以内的感恩日记，坚持一个月看看变化。"),
    # 引用编号 [1] / [n]（半角方括号 + 非中文，不命中占位符形态）
    ("cite_number", "古籍《渊海子平》[1] 有云：伤官见官，为祸百端。"),
    ("cite_letter", "参考命盘数据[n]，你的财星在年柱。"),
    # 全角括号【】不得与半角占位符混淆
    ("fullwidth_bracket", "【领域】相关建议已在上方列出，重点看事业与健康。"),
    # 含冒号/逗号的正常中文
    ("plain_colon", "记住一句话：赚钱靠金，守钱靠水。"),
    ("ganzhi", "你的四柱是 己卯 己巳 乙丑 辛巳，日主乙木偏弱，喜水木。"),
    # 单条弱信号的真实写法（阈值必须放行 —— 这些是**用户向**文案，不是规格）
    ("window_word_once", "2026年的关键时间窗口在立秋之后，建议提前布局。"),
    ("must_include_once", "签合同必须包含违约条款，别只盯着价格。"),
    ("as_quote_once", "比如‘先复盘再行动’这种节奏，比硬冲更稳。"),
    ("two_words_once", "把沟通压到1-2句话，别一次倒完所有情绪。"),
    ("not_over_once", "每天投入不超过30分钟，先跑通再优化。"),
    # 实跑同款 serendipity（含"顺便说一句（你可能没问但很重要）"——必须放行）
    ("live_serendipity", "顺便说一句（你可能没问但很重要）：近期财运和事业都有上升空间，"
                         "但同时也要注意身体健康。"),
]


@pytest.mark.parametrize("case,text", NEGATIVE_FORMS, ids=[c for c, _ in NEGATIVE_FORMS])
def test_negative_forms_untouched(case, text):
    """合法表达 → 不判泄漏，且清洗前后**逐字节相同**（防误杀）。"""
    assert is_schema_echo(text) is False, (case, schema_echo_hits(text))
    assert scrub_schema_echo(text) == text, case
    assert guard_schema_echo(text) == (text, []), case


def test_threshold_single_weak_signal_tolerated():
    """阈值语义显式锁：单条弱信号放行、第二条弱信号才判泄漏。"""
    one_weak = "建议按这个格式：先复盘再行动。"          # 仅 "格式：" 一条弱信号
    assert is_schema_echo(one_weak) is False
    assert scrub_schema_echo(one_weak) == one_weak
    two_weak = "建议按这个格式：输出示例：先复盘再行动。"  # 两条弱信号
    assert is_schema_echo(two_weak) is True
    assert scrub_schema_echo(two_weak) == ""


def test_one_strong_signal_is_enough():
    """强信号单条即判（结构/占位符/元指令不是自然中文文案形态）。"""
    for text in ('"timing": "最佳行动时间窗口"', "在[时间段]内推进", "请只输出JSON"):
        assert is_schema_echo(text) is True, text


def test_documented_boundary_two_weak_signals_in_normal_prose():
    """**已知边界（显式钉住，不隐藏）**：正常建议里**同时**出现两条弱信号 → 会被误判置空。

    这是阈值设计的代价（宁可对形态可疑的文本从严，也不放行模板说明）。
    行为与报告 §9.3-7 的披露**逐条对齐**；本用例存在的意义是防止未来有人
    "以为它不会误判"而在别处依赖精确率 100%。
    """
    text = "2026年的关键时间窗口在立秋之后，签合同必须包含违约条款。"
    assert is_schema_echo(text) is True          # 弱信号 2 条：时间窗口 + 必须包含
    assert scrub_schema_echo(text) == ""


# ============================================================
# C：接线 —— generate() 字段层
# ============================================================

@pytest.fixture(scope="module")
def golden():
    return BaziEngine().calculate(1999, 5, 13, 9, 0, "长春", "男")


def _advisor_with(canned: str, monkeypatch):
    adv = AdaptiveAdvisor()
    monkeypatch.setattr(adv, "_call_llm", lambda prompt, api_key: canned)
    return adv


def test_generate_cleans_echoed_fields(golden, monkeypatch):
    """GLM 回显形态（实录样本）进入 generate → 用户可见字段不再含模板说明。"""
    canned = json.dumps({
        "actions": [
            {"category": "事业", "advice": "考虑转向金融行业，与金水五行契合。",
             "timing": '"timing": "最佳行动时间窗口，必须包含具体日期范围"',
             "confidence": "high"},
            {"category": "财运", "advice": "具体的行动建议，1-2句话，精炼有力，"
                                        "必须结合用户的八字数据给出个性化理由",
             "timing": "2027年立春到大暑", "confidence": "medium"},
        ],
        "serendipity": _REAL_GLM_ECHO,
        "daily_tip": "保持乐观，每一天都是新的开始。",
        "style_notes": "你的命格喜水木，注意心脏和眼睛的健康。",
    }, ensure_ascii=False)
    adv = _advisor_with(canned, monkeypatch)
    out = adv.generate(golden, user_context="建议", api_key="k")
    # 只看**字段值**（序列化后的 dict 自带 "timing": 这类键名，不能拿 JSON 文本当判据）
    vals = "\n".join(
        [str(a.get(k) or "") for a in out["actions"]
         for k in ("advice", "timing", "concrete_steps", "success_metric")]
        + [str(out.get("serendipity") or ""), str(out.get("daily_tip") or ""),
           str(out.get("style_notes") or "")])
    # 模板说明全清（含占位符/元指令/JSON 键/字段名/规格）
    for frag in ("[领域]", "输出空字符串", '"timing":', '"advice":', "80字以内",
                 "具体的行动建议", "最佳行动时间窗口"):
        assert frag not in vals, frag
    assert out["serendipity"] == ""                      # 回显字段置空
    # 正常内容逐字保留（只清回显，不误杀）
    assert out["daily_tip"] == "保持乐观，每一天都是新的开始。"
    assert out["style_notes"] == "你的命格喜水木，注意心脏和眼睛的健康。"
    # 被回显污染的 advice 那条整体摘除；健康那条保留
    cats = [a["category"] for a in out["actions"]]
    assert "财运" not in cats and "事业" in cats
    assert out["actions"][0]["advice"] == "考虑转向金融行业，与金水五行契合。"
    assert out["actions"][0]["timing"] == ""             # 回显 timing 置空，条目仍在


def test_generate_all_actions_echoed_falls_back(golden, monkeypatch):
    """5 条 advice 全是回显 → 全部摘除后退回通用兜底（不出现空壳行/模板说明）。"""
    canned = json.dumps({
        "actions": [{"category": d,
                     "advice": "具体的行动建议，1-2句话，精炼有力，必须结合用户的八字数据",
                     "timing": "最佳行动时间窗口，必须包含具体日期范围",
                     "confidence": "high"} for d in LIFE_DOMAINS],
        "serendipity": "", "daily_tip": "", "style_notes": "",
    }, ensure_ascii=False)
    adv = _advisor_with(canned, monkeypatch)
    out = adv.generate(golden, user_context="建议", api_key="k")
    assert [a["category"] for a in out["actions"]] == LIFE_DOMAINS   # 5 领域仍在
    for a in out["actions"]:
        assert a["advice"].strip(), a                                  # 无空壳行
        assert "具体的行动建议" not in a["advice"]                       # 模板说明已清
        # 兜底文案 = FALLBACK_ADVICE 首条（与"LLM 不可用"同款）
        assert a["advice"] == f"{a['category']}方面的建议需要结合你的具体处境来分析。"
    assert "具体的行动建议" not in json.dumps(out, ensure_ascii=False)


def test_generate_normal_output_untouched(golden, monkeypatch):
    """正常输出（实跑同款文案）→ 字段逐字不变（接线不得引入误杀）。"""
    actions = [{"category": "事业",
                "advice": "考虑转向金融行业，特别是银行或证券领域，"
                          "这些行业与金和水五行相契合，有利于事业发展。",
                "timing": "2026年9月20日至2027年3月20日", "confidence": "high"}]
    canned = json.dumps({"actions": actions,
                         "serendipity": "顺便说一句（你可能没问但很重要）：近期财运和"
                                        "事业都有上升空间，但同时也要注意身体健康。",
                         "daily_tip": "今日小建议：保持心情舒畅，有助于提升运势。",
                         "style_notes": "命格特点：偏财格，喜水木，需注意金火土的调和。"},
                        ensure_ascii=False)
    adv = _advisor_with(canned, monkeypatch)
    out = adv.generate(golden, user_context="建议", api_key="k")
    assert out["actions"][0]["advice"] == actions[0]["advice"]
    assert out["actions"][0]["timing"] == actions[0]["timing"]
    assert out["serendipity"].startswith("顺便说一句")
    assert out["daily_tip"] == "今日小建议：保持心情舒畅，有助于提升运势。"
    assert out["style_notes"] == "命格特点：偏财格，喜水木，需注意金火土的调和。"


def test_generate_echo_path_is_advisor_only(golden, monkeypatch):
    """边界：只影响 advisor 字段层，不改解析层 —— 解析仍返回原 dict 结构。"""
    canned = json.dumps({"actions": [], "serendipity": _REAL_GLM_ECHO,
                         "daily_tip": "", "style_notes": ""}, ensure_ascii=False)
    adv = _advisor_with(canned, monkeypatch)
    raw = adv._parse_llm_output(canned)                  # 解析层：原文照旧
    assert raw["serendipity"] == _REAL_GLM_ECHO
    out = adv.generate(golden, user_context="建议", api_key="k")
    assert out["serendipity"] == ""                      # 呈现层：已清


# ============================================================
# D：切分 —— scrub_turn 语义未被改动，两者互不干扰
# ============================================================

def test_scrub_turn_semantics_unchanged():
    """k11-B 行为逐条仍在（本批未动 scrub_turn 的判据与返回值）。"""
    text = "姐妹，金融属金压力大，文昌贵人护你"
    cleaned = scrub_turn(text, "男", ["太极贵人", "金融"])
    assert "姐妹" not in cleaned and "文昌贵人" not in cleaned
    assert "金融属金压力大" in cleaned
    # 女命放行 + allow 空跳过（既有边界）
    assert scrub_turn("姐妹别怕", "女", []) == "姐妹别怕"
    assert scrub_turn("命带文昌贵人", "男", []) == "命带文昌贵人"


def test_two_guards_are_independent():
    """词表守卫不管形态、形态守卫不管词表（切分可分别排障）。"""
    # scrub_turn 不碰 schema 回显（证明 D 没被并进 scrub_turn）
    assert scrub_turn(_REAL_GLM_ECHO, "男", []) == _REAL_GLM_ECHO
    # schema 守卫不碰称谓词（证明 D 没顺手做称谓过滤）
    assert scrub_schema_echo("姐妹，听姐一句劝") == "姐妹，听姐一句劝"
    # 组合顺序（advisor 里的真实用法）：两个都生效
    combo = scrub_schema_echo(scrub_turn("姐妹，" + _REAL_GLM_ECHO, "男", []))
    assert combo == ""                      # 称谓去词 + schema 整段置空


def test_hits_are_classified_strong_weak():
    """命中明细区分强/弱（告警与排障用）。"""
    hits = schema_echo_hits(_REAL_GLM_ECHO)
    assert any(h.startswith("strong:") for h in hits)
    assert all(h.startswith(("strong:", "weak:")) for h in hits)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
