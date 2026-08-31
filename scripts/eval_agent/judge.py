#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L3 质量判卷层（E4）：免费 glm-4-flash 判卷 + 五维 rubric + JSON 三级兜底 + 30 条校准样本落盘。

用法：
  python3 scripts/eval_agent/judge.py                        # 默认 30 条校准抽样（P0/P1/P2 各 10 覆盖 10 域）
  python3 scripts/eval_agent/judge.py --tasks T001,T019      # 指定任务
  python3 scripts/eval_agent/judge.py --category fortune     # 指定域
  python3 scripts/eval_agent/judge.py --all                  # 全量 100 条
  python3 scripts/eval_agent/judge.py --out <dir>            # 结果目录（默认 data/eval/results/l3-<时间戳>/）

退出码：0 = 跑完且零判卷失败 / 1 = 有任务判卷失败（judge_error）或跳过（主链未产出回复）
        / 2 = 评估集校验失败或前置失败（key 缺失）。
本批不设分数阈值门禁（L3 门禁在 E6 统一运行器接线），输出真实基线数字，
不达标也是基线——校准优先。

L3 测什么（spec §5.3）：回复文案质量五维——accuracy 30% / completeness 25% /
personalization 20% / actionability 15% / citation_quality 10%，每维 0-10
分段锚定描述，加权汇总（temperature=0.3）。边界：只评回复质量（文案层），
不评预测准不准——预测类内容的正误评估不在本层范围（引擎层已用权威锚点
覆盖计算正确性）。

判卷模型：免费 glm-4-flash（复用 src/llm/client.py glm_openai_completion，
OpenAI 兼容端点）。key 只读环境变量 ZHIPU_API_KEY，零落盘。

JSON 三级兜底（策略逐项等价 src/eval/scorer.py LLMScorer._parse_scores，
L261-340；差异仅在于显式 parse_level 追踪 + 每维 judge_error 标注）：
  1. 直接 json.loads（无代码围栏）
  2. 剥离 markdown 代码围栏（```json / ```）后 json.loads
  3. 花括号滑动截断匹配 / 正则提取五维 score
  三级全失败 → 该维 0 分 + judge_error 标注。

红线（违反即返工）：
- 判卷模型固定免费 glm-4-flash；本批任何路径不得调用生产模型（不提供
  生产模型路由；主链路由复用 l1_eval._init_runtime 的 glm 路由；
  grep 生产模型名应零命中——本文件不初始化任何生产模型）
- src/bot/tool_calls.py 零改动（拦截在测试侧 interceptor.py）
- 隔离库方案：复用 l1_eval 骨架——复制真实库 → 每任务临时库，
  原始库零写入；USER_MEMORY_DIR/CHARTS_DIR 重定向临时目录
- key 只读环境变量（ZHIPU_API_KEY），零落盘；零新增依赖
- data/eval/agent_tasks.jsonl 只读（唯一事实源）；data/memory/、
  src/engine/out/ 零触碰

模块导入零副作用：src 导入（除 src.eval.scorer 纯常量模块）与环境变量
全部在函数内懒执行——本模块 import 亦无副作用，便于单测直接 import
断言纯函数。
"""
import argparse
import concurrent.futures
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_HERE = Path(__file__).resolve().parent
for _p in (_REPO, _HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import l1_eval  # noqa: E402  — 复用骨架：隔离库/种子注入/主链装配/落盘/退出码
from interceptor import ToolCallRecorder  # noqa: E402

# rubric 五维权威对齐（spec §5.3：「rubric 沿用现有 scorer.py 五维结构」）：
# 权重、维度名、0-10 分数段全部复用 src/eval/scorer.py（纯常量模块，零副作用）。
# 权重必须和为 1.0（单测断言）。
from src.eval.scorer import DIMENSION_WEIGHTS, SCORING_RUBRIC  # noqa: E402

TASKS_PATH = l1_eval.TASKS_PATH
VALIDATOR = l1_eval.VALIDATOR
RESULTS_ROOT = l1_eval.RESULTS_ROOT
TASK_TIMEOUT = l1_eval.TASK_TIMEOUT

DIMS = ("accuracy", "completeness", "personalization", "actionability",
        "citation_quality")

# 判卷模型配置（temperature=0.3 低温度保一致性；max_tokens 1024 与 scorer 同档）
JUDGE_MODEL = "glm-4-flash"  # 镜像 src/llm/client.py GLM_DEFAULT_MODEL（懒加载处再校验）
JUDGE_TEMPERATURE = 0.3
JUDGE_MAX_TOKENS = 1024

# 校准抽样规则（确定性可复现）：每级先按域字母序各取最小 id 一条（尽域覆盖），
# 余量按 id 升序补齐到 10 条。P0 仅存在于 8 域 → 覆盖 8 域 + 补齐 2；
# P1 覆盖全部 10 域；P2 覆盖 7 域 + 补齐 3。
CALIBRATION_PER_SEVERITY = 10


# ================================================================
# 判卷提示词（system：rubric + 三档校准锚点 + 输出格式）
# ================================================================

# 三档校准锚点（brief 契约：「每维 0-10 分段锚定描述，如 8-10=准确且无错误、
# 4-7=部分准确有遗漏、0-3=错误/离题」）——与 scorer.py 五档详述并存，
# 五档是细粒度标准、三档是快速锚定，两者不冲突（3 档边界与 5 档区间对齐）。
THREE_BAND_ANCHORS = """## 三档校准锚点（速查，与上表五档区间对齐）
- 8-10：准确且无错误
- 4-7：部分准确有遗漏
- 0-3：错误/离题"""

# 校准修正锚点（E6 落地——事实源 docs/superpowers/eval/2026-08-31-l3-calibration.md
# §rubric 修正建议四类，逐条落实；E4 校准 90% 一致率的 15 处偏差全部对应该四类，
# 修正后重跑 30 条校准样本预期一致率 ≥95%）
CALIBRATION_FIX_ANCHORS = """## 校准修正锚点（E6 校准报告四类修正，判卷时必须执行）
1. 引导类：缺信息时正确引导建档/收集信息是**正确行为**（符合产品设计），
   按引导完整度评分——所需信息项+示例齐全 → accuracy/completeness 8-10；
   部分信息 → 4-7；**引导方向错误**（用户闲聊却引导建档/测算）→ accuracy ≤3。
2. 内容错域/空壳：回复内容域与用户请求域**不符**（如请求六爻摇卦却输出
   奇门遁甲内容/其他命理域术语）→ accuracy ≤4；**空壳回复**（卡片内无实质
   内容、仅一句应付话，未给出用户要的结果）→ accuracy/completeness ≤3。
3. 事实核查：排盘四柱/流年干支等可对照推算的**计算事实**，必须逐柱核查——
   用户提供出生信息时可对照推算验证各柱，或利用回复中已给出的其他柱
   推算/验证时柱；发现任何干支/柱位错误 → accuracy ≤4。
4. 跳档稳定：同类型回复（如同为建档引导、同为查档回复）分数必须一致，
   **分差 ≤1**——同质量回复不得因表达差异大幅跳档。"""

JUDGE_SYSTEM_PROMPT = """你是一位严格的命理回复质量判卷专家（L3 判卷层）。

你只评【AI 回复】的文案质量，不评判预测准不准——预测类内容的正误评估不在本层范围（引擎层已用权威锚点覆盖计算正确性）。你要评估的是：回复文字本身的准确性、完整性、个性化、可操作性与引证质量。

重要规则：
1. 分数必须为 0-10 之间的整数或半整数（如 7.5）
2. 每维必须给出具体的评分理由（中文）
3. 保持一致性——相同质量的回复应得到相同的分数
4. 回复为空或完全无关 → 所有维度给 0 分
5. 基于回复的实际质量评分，不要因为回复风格（如语气）而加减分
6. 只依据给定材料（对话轮次、真实回复、用户上下文、任务锚点）评分，不脑补

{scoring_rubric}

{three_band_anchors}

{calibration_fix_anchors}

## Output Format

你必须以 JSON 格式输出评分结果，不要包含其他内容：

```json
{{
  "accuracy": {{
    "score": 7.0,
    "justification": "..."
  }},
  "completeness": ...
}}
```

每个 dimension 包含 score（0-10 的浮点数）和 justification（中文评分理由）。
"""


def build_judge_system_prompt() -> str:
    """判卷 system 提示词 = scorer.py 五档 rubric + 三档校准锚点 +
    校准修正锚点（E6 四类）+ 输出契约。"""
    return JUDGE_SYSTEM_PROMPT.format(
        scoring_rubric=SCORING_RUBRIC, three_band_anchors=THREE_BAND_ANCHORS,
        calibration_fix_anchors=CALIBRATION_FIX_ANCHORS)


def setup_summary(task: dict) -> str:
    """种子信息摘要（供个性化维度判卷：档案/收藏/签/择吉/盘记录等）。"""
    s = task.get("setup") or {}
    parts = []
    for key in ("persons", "favorites", "qian_saves", "zeri_plans",
                "chart_records", "membership", "chat_quota"):
        v = s.get(key)
        if v:
            parts.append(f"{key}={json.dumps(v, ensure_ascii=False)}")
    return "；".join(parts)


def build_judge_user_prompt(task: dict, replies: list) -> str:
    """判卷 user 提示词：任务锚点 + 用户上下文 + 逐轮（用户问话 + 完整真实回复）。"""
    turns = task.get("turns") or []
    lines = ["请对以下命理问答的【AI 回复】进行五维质量判卷。", ""]
    lines.append(f"【任务】{task.get('id', '?')}（域：{task.get('category', '?')}｜"
                 f"级别：{task.get('severity', '?')}）")
    lines.append(f"【任务标题】{task.get('title', '')}")
    hint = task.get("judge_hint") or ""
    if hint:
        lines.append(f"【任务锚点】{hint}")
    ctx = setup_summary(task)
    if ctx:
        lines.append(f"【用户上下文】{ctx}")
    lines.append("【对话轮次与真实回复】")
    for i, (turn, reply) in enumerate(zip(turns, replies), 1):
        lines.append(f"轮{i}")
        lines.append(f"用户: {(turn.get('text') or '').strip()}")
        lines.append(f"AI: {reply}")
    lines.append("")
    lines.append("【判卷边界】只评回复文案质量，不评预测准不准。")
    return "\n".join(lines)


# ================================================================
# 判卷模型调用（免费 glm-4-flash；懒加载 src.llm.client——模块导入零副作用）
# ================================================================

def _call_glm(api_key: str, messages: list, model: str, max_tokens: int,
              temperature: float, timeout: float) -> str:
    """薄封装 glm_openai_completion（懒加载；失败抛异常由上层兜底）。"""
    from src.llm.client import GLM_DEFAULT_MODEL, glm_openai_completion
    assert model == GLM_DEFAULT_MODEL, \
        f"判卷模型必须为免费 {GLM_DEFAULT_MODEL}（当前 {model}）"
    return glm_openai_completion(
        api_key, messages, model=model, max_tokens=max_tokens,
        temperature=temperature, timeout=timeout)


def call_judge_model(system_prompt: str, user_prompt: str, api_key: str) -> str:
    """调 glm-4-flash 判卷，返回原始文本（解析由三级兜底负责）。"""
    return _call_glm(
        api_key,
        [{"role": "system", "content": system_prompt},
         {"role": "user", "content": user_prompt}],
        model=JUDGE_MODEL, max_tokens=JUDGE_MAX_TOKENS,
        temperature=JUDGE_TEMPERATURE, timeout=45.0)


# ================================================================
# JSON 三级兜底解析（策略逐项等价 scorer.py:261-340；差异：parse_level/
# 每维 judge_error 显式追踪）
# ================================================================

_STRICT_DIM = re.compile(
    r'"{dim}"\s*:\s*\{{.*?"score"\s*:\s*([\d.]+).*?"justification"\s*:\s*"(.+?)"\s*\}}',
    re.DOTALL)
_BROAD_SCORE = re.compile(r'"{dim}"[^}}]*?"score"\s*:\s*([\d.]+)', re.DOTALL)
_BROAD_JUST = re.compile(r'"{dim}"[^}}]*?"justification"\s*:\s*"([^"]+)"', re.DOTALL)


def _strip_code_fence(raw: str) -> str:
    """剥离 markdown 代码围栏（```json / ``` 两类），与 scorer.py 同策略。"""
    s = raw.strip()
    if "```json" in s:
        s = s.split("```json")[1].split("```")[0].strip()
    elif "```" in s:
        s = s.split("```")[1].split("```")[0].strip()
    return s


def _clamp(score: float) -> float:
    return max(0.0, min(10.0, score))


def _build_from_dict(data: dict) -> dict:
    """解析出的 dict → 五维分（缺失维 → 0 分 + 该维 judge_error）。"""
    out = {}
    for dim in DIMS:
        entry = data.get(dim)
        if not isinstance(entry, dict):
            out[dim] = {"score": 0.0, "judge_error": True,
                        "justification": "判卷 JSON 缺失该维，按 0 分兜底并标注 judge_error"}
            continue
        try:
            score = float(entry.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        just = str(entry.get("justification", "") or "")
        out[dim] = {"score": _clamp(score), "judge_error": False,
                    "justification": just}
    return out


def _regex_extract(raw: str) -> dict:
    """正则提取五维分（scorer.py 策略 3 同款：严格结构 / 宽松 score+justification）。"""
    out = {}
    for dim in DIMS:
        m = _STRICT_DIM.pattern.replace("{dim}", dim)
        mm = re.search(m, raw, re.DOTALL)
        score, just = 0.0, "Extracted via regex fallback"
        if mm:
            score = float(mm.group(1))
            just = mm.group(2)[:300]
        else:
            m2 = _BROAD_SCORE.pattern.replace("{dim}", dim)
            if re.search(m2, raw, re.DOTALL):
                score = float(re.search(m2, raw, re.DOTALL).group(1))
            jm = _BROAD_JUST.pattern.replace("{dim}", dim)
            if re.search(jm, raw, re.DOTALL):
                just = re.search(jm, raw, re.DOTALL).group(1)[:300]
        out[dim] = {"score": _clamp(score), "judge_error": False,
                    "justification": just}
    return out


def parse_scores(raw: str) -> dict:
    """三级兜底解析判卷原始输出。

    返回 {"dims": {dim: {"score", "justification", "judge_error"}},
          "parse_level": 0-3, "judge_error": bool}
    - level 0：raw 为空（判卷模型返回空）
    - level 1：直接 JSON
    - level 2：剥离 markdown 代码围栏后 JSON
    - level 3：花括号滑动截断 / 正则提取
    三级全失败 → 每维 0 分 + judge_error（按 brief：仍失败返回该维 0 分并标注）。
    """
    raw = raw or ""
    if not raw.strip():
        dims = {d: {"score": 0.0, "judge_error": True,
                    "justification": "判卷模型返回空，按 0 分兜底并标注 judge_error"}
                for d in DIMS}
        return {"dims": dims, "parse_level": 0, "judge_error": True}

    fenced = "```" in raw
    json_str = _strip_code_fence(raw)

    # 第 1/2 级：直接 json.loads
    try:
        data = json.loads(json_str)
        if isinstance(data, dict):
            dims = _build_from_dict(data)
            err = any(v["judge_error"] for v in dims.values())
            return {"dims": dims, "parse_level": 2 if fenced else 1,
                    "judge_error": err}
    except json.JSONDecodeError:
        pass

    # 第 3 级 a：花括号滑动截断（scorer.py 策略 2）
    brace_start = json_str.find("{")
    if brace_start >= 0:
        for end_offset in range(len(json_str), brace_start, -1):
            candidate = json_str[brace_start:end_offset]
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                dims = _build_from_dict(data)
                err = any(v["judge_error"] for v in dims.values())
                return {"dims": dims, "parse_level": 3, "judge_error": err}

    # 第 3 级 b：正则提取（scorer.py 策略 3）
    dims = _regex_extract(raw)
    if any(v["score"] > 0 for v in dims.values()):
        return {"dims": dims, "parse_level": 3, "judge_error": False}
    for v in dims.values():
        v["judge_error"] = True
        v["justification"] = "三级兜底全失败（正则未提取到任何分数），按 0 分兜底并标注 judge_error"
    return {"dims": dims, "parse_level": 3, "judge_error": True}


# ================================================================
# 加权汇总与分组统计（纯函数，供单测直接 import）
# ================================================================

def weighted_overall(dims: dict) -> float:
    """五维加权平均 = Σ(维度分 × 权重)，保留两位。"""
    return round(sum(dims[d]["score"] * DIMENSION_WEIGHTS[d] for d in DIMS), 2)


def _avg(vals: list) -> float:
    return round(sum(vals) / len(vals), 3) if vals else None


def group_metrics(results: list) -> dict:
    """L3 指标：加权平均（已判卷 / 含 judge_error 按 0 两种口径）+ P0 平均 +
    分域平均 + 五维平均。judge_error/skipped 任务显式列出，不混入正常均值
    （真实数字就是基线，不美化；两种口径都留档）。"""
    judged = [r for r in results if not r.get("skipped") and not r["judge_error"]]
    judged_plain = [r for r in results if not r.get("skipped")]  # judge_error 按 0 分计
    per_dim = {d: _avg([r["dims"][d]["score"] for r in judged]) for d in DIMS}
    by_cat = {}
    for r in judged:
        by_cat.setdefault(r["category"], []).append(r["overall"])
    cat_avg = {k: _avg(v) for k, v in sorted(by_cat.items())}
    p0 = [r for r in judged if r["severity"] == "P0"]
    return {
        "total": len(results),
        "judged": len(judged),
        "skipped": [r["id"] for r in results if r.get("skipped")],
        "judge_error": [r["id"] for r in results
                        if not r.get("skipped") and r["judge_error"]],
        "weighted_avg": _avg([r["overall"] for r in judged]),
        "weighted_avg_incl_errors": _avg([r["overall"] for r in judged_plain]),
        "p0_avg": _avg([r["overall"] for r in p0]),
        "p0_count": len(p0),
        "per_dimension": per_dim,
        "by_category": cat_avg,
    }


# ================================================================
# 判卷单任务（纯逻辑；判卷模型调用经 call_judge_model 注入点可 mock）
# ================================================================

def judge_task(task: dict, replies: list, api_key: str) -> dict:
    """对单任务的逐轮真实回复做 L3 判卷。

    返回：{id/category/severity/title/turns/replies/dims/overall/parse_level/
           judge_error/judge_error_reason/skipped/skip_reason}
    - 无回复（主链跑失败）→ skipped 标注（验收契约：无回复任务标注跳过）
    - 判卷模型调用失败 → 全维 0 + judge_error + reason
    - JSON 解析三级兜底（parse_scores）
    """
    replies = [r or "" for r in replies]
    if not replies or all(not r.strip() for r in replies):
        return {"id": task["id"], "category": task["category"],
                "severity": task["severity"], "title": task["title"],
                "skipped": True, "skip_reason": "无回复（主链跑失败），跳过判卷",
                "dims": {}, "overall": 0.0, "parse_level": 0,
                "judge_error": False, "judge_error_reason": "",
                "turns": task.get("turns") or [], "replies": replies}
    system_prompt = build_judge_system_prompt()
    user_prompt = build_judge_user_prompt(task, replies)
    try:
        raw = call_judge_model(system_prompt, user_prompt, api_key)
    except Exception as e:  # noqa: BLE001 — 判卷失败要显式标注而非静默
        reason = f"{type(e).__name__}: {str(e)[:200]}"
        dims = {d: {"score": 0.0, "judge_error": True,
                    "justification": f"判卷模型调用失败: {reason}"}
                for d in DIMS}
        return {"id": task["id"], "category": task["category"],
                "severity": task["severity"], "title": task["title"],
                "skipped": False, "skip_reason": "",
                "dims": dims, "overall": 0.0, "parse_level": 0,
                "judge_error": True, "judge_error_reason": reason,
                "turns": task.get("turns") or [], "replies": replies}
    parsed = parse_scores(raw)
    dims = parsed["dims"]
    return {"id": task["id"], "category": task["category"],
            "severity": task["severity"], "title": task["title"],
            "skipped": False, "skip_reason": "",
            "dims": dims, "overall": weighted_overall(dims),
            "parse_level": parsed["parse_level"],
            "judge_error": parsed["judge_error"],
            "judge_error_reason": "" if not parsed["judge_error"]
            else "解析三级兜底后仍有维度缺失或全部为 0",
            "turns": task.get("turns") or [], "replies": replies}


# ================================================================
# 校准抽样（确定性可复现）
# ================================================================

def select_calibration_ids(tasks: list) -> list:
    """30 条校准抽样：P0/P1/P2 各 10 条，覆盖 10 域。

    规则（确定性，无随机）：每级先按域字母序各取最小任务 id 一条
    （尽域覆盖），余量按 id 升序补齐。P0 仅存在于 8 域（paipan/fortune/
    zeri/hehun/xingming/qian/chat/edge），P1 覆盖全部 10 域，P2 覆盖
    7 域（zeri/xingming/qian/liuyao/ziwei/chat/edge）——域覆盖上限即
    该级实际域数，单测断言分布。
    """
    ids = []
    for sev in ("P0", "P1", "P2"):
        pool = [t for t in tasks if t["severity"] == sev]
        by_dom = {}
        for t in pool:
            by_dom.setdefault(t["category"], []).append(t["id"])
        picked = [min(v) for v in (by_dom[d] for d in sorted(by_dom))]
        rest = sorted(t["id"] for t in pool if t["id"] not in picked)
        ids += picked + rest[:CALIBRATION_PER_SEVERITY - len(picked)]
    return ids[:CALIBRATION_PER_SEVERITY * 3]


# ================================================================
# 主链回复收集（复用 l1_eval 骨架：隔离库/种子注入/主链装配/拦截；
# 与 l2_eval._run_one_task 同款执行路径，唯一差异：保留完整回复文本——
# L3 判卷输入需要完整回复，L2 骨架 2000/4000 字符截断不满足）
# ================================================================

def align_temp_schema(db_path: str) -> str:
    """临时库 schema 对齐 shim（judge.py 本地，生产库/骨架零改动）。

    骨架种子 DDL（l1_eval._SEED_TABLE_DDL）按新 schema 声明 qian_saves.kind
    （默认 'original'），但真实库 qian_saves 尚无该列——CREATE IF NOT EXISTS
    不补列，骨架种子 INSERT 报 "no column named kind"（E2/E3 骨架遗留，
    L3 校准首测暴露：T051/T053/T057 三任务种子注入失败）。此处对临时库
    补列，默认值与骨架种子语义完全一致（种子恒写 kind='original'），
    使骨架种子代码原样可跑。返回错误串或 None。
    """
    try:
        con = sqlite3.connect(db_path)
        try:
            cols = [r[1] for r in con.execute("PRAGMA table_info(qian_saves)")]
            if cols and "kind" not in cols:
                con.execute("ALTER TABLE qian_saves ADD COLUMN kind TEXT "
                            "NOT NULL DEFAULT 'original'")
                con.commit()
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {str(e)[:120]}"
    return None


def collect_replies(task: dict, R: dict) -> dict:
    """黑盒跑单任务收集逐轮完整回复（临时库 → schema 对齐 → 种子 → 逐轮 process）。"""
    tid = task["id"]
    user_id = f"eval_{tid}"
    out = {"skipped": False, "skip_reason": "", "replies": [],
           "full_reply": "", "exception": None, "elapsed": 0.0}
    tdir = Path(tempfile.mkdtemp(prefix=f"{tid}-", dir=str(R["tmp_root"])))
    try:
        db_path = l1_eval.seed_db_copy(R["settings"].db_path, tdir)
    except Exception as e:  # noqa: BLE001
        out["skipped"] = True
        out["skip_reason"] = f"隔离库复制失败: {type(e).__name__}: {str(e)[:120]}"
        return out
    align_err = align_temp_schema(str(db_path))
    if align_err is not None:
        out["skipped"] = True
        out["skip_reason"] = f"临时库 schema 对齐失败: {align_err}"
        return out
    seed_err = l1_eval.seed_task_setup(str(db_path), user_id,
                                       task.get("setup") or {})
    if seed_err is not None:
        out["skipped"] = True
        out["skip_reason"] = f"种子注入失败: {seed_err}"
        return out

    recorder = ToolCallRecorder()
    t0 = time.monotonic()
    try:
        with recorder:
            recorder.install()  # 与 L2 同口径（记录不改行为）
            handler = R["build_handler"](db_path, "glm")

            def _run_chain():
                replies = []
                for turn in task["turns"]:
                    recorder.begin_turn()
                    text = (turn.get("text") or "").strip()
                    replies.append(
                        handler.process(text, user_id,
                                        session_id=f"eval-{tid}") or "")
                return replies

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                replies = ex.submit(_run_chain).result(timeout=TASK_TIMEOUT)
    except concurrent.futures.TimeoutError:
        replies = []
        out["exception"] = f"任务超时（>{TASK_TIMEOUT}s）"
    except Exception as e:  # noqa: BLE001 — 主链异常也要落报告
        replies = []
        out["exception"] = f"{type(e).__name__}: {str(e)[:300]}"
    finally:
        out["elapsed"] = time.monotonic() - t0

    out["replies"] = replies
    out["full_reply"] = "\n".join(replies)
    out["actual_calls"] = recorder.flat_calls()
    return out


# ================================================================
# 运行期：跑任务集 → 判卷 → 落盘（meta.json + results.json + report.md
# + calibration_samples.json）
# ================================================================

def run_eval(tasks: list, out_dir: Path, calibration: bool = False,
             keep_tmp: bool = False) -> dict:
    """跑指定任务集并落盘报告。返回 {"meta", "metrics", "results"}。

    calibration=True 时额外落盘 calibration_samples.json（30 条校准样本，
    含任务 id + turns + 完整真实回复 + judge 五维分 + 加权分，供主会话
    人工标注与一致率计算——本批只落盘样本，标注由主会话负责）。
    """
    R = l1_eval._init_runtime("glm")
    if not keep_tmp:
        l1_eval.atexit_register_cleanup(R["tmp_root"])
    glm_key = os.environ.get("ZHIPU_API_KEY", "").strip()
    results = []
    for task in tasks:
        c = collect_replies(task, R)
        base = {"id": task["id"], "category": task["category"],
                "severity": task["severity"], "title": task["title"],
                "turns": task.get("turns") or []}
        if c["skipped"]:
            results.append({**base, "skipped": True,
                            "skip_reason": c["skip_reason"],
                            "replies": [], "dims": {}, "overall": 0.0,
                            "parse_level": 0, "judge_error": False,
                            "judge_error_reason": "",
                            "elapsed": c["elapsed"]})
            print(f"[SKIP] {task['id']} {task['title']} —— {c['skip_reason']}")
            continue
        j = judge_task(task, c["replies"], glm_key)
        results.append({**j, "elapsed": c["elapsed"]})
        if j["judge_error"]:
            print(f"[JUDGE_ERR] {task['id']} {task['category']} {task['severity']} "
                  f"{task['title']} 判卷失败（L{j['parse_level']}）: "
                  f"{j['judge_error_reason'][:120]}")
        else:
            print(f"[JUDGED] {task['id']} {task['category']} {task['severity']} "
                  f"{task['title']} ({c['elapsed']:.1f}s) 加权 {j['overall']} "
                  f"解析L{j['parse_level']}")

    metrics = group_metrics(results)
    meta = {
        "layer": "L3",
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "judge_model": JUDGE_MODEL,
        "judge_temperature": JUDGE_TEMPERATURE,
        "judge_max_tokens": JUDGE_MAX_TOKENS,
        "chain_model_route": "glm",
        "repo_version": l1_eval._git_head(),
        "scope": [t["id"] for t in tasks],
        "eval_set": "data/eval/agent_tasks.jsonl (E1, 唯一事实源，只读)",
        "calibration": calibration,
        "calibration_rule": ("P0/P1/P2 各 10 条：每级先按域字母序各取最小 id 一条"
                             "（尽域覆盖），余量按 id 升序补齐；确定性可复现"),
        "rubric_source": "src/eval/scorer.py DIMENSION_WEIGHTS + SCORING_RUBRIC"
                         "（权重/维度名/分数段权威对齐，spec §5.3）",
        "fallback": "JSON 三级兜底（直接解析/剥离代码围栏/花括号+正则提取），"
                    "全失败 → 该维 0 分 + judge_error",
        "gate": "本批无阈值门禁（spec §7：L3 门禁在 E6 统一运行器接线；"
                "参考阈值 P0 ≥8、全量 ≥7.5，首测基线如实留档）",
        "metrics": metrics,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(
        render_report_md(meta, metrics, results), encoding="utf-8")
    if calibration:
        (out_dir / "calibration_samples.json").write_text(
            json.dumps(render_calibration_samples(results),
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n校准样本已落盘（{len(results)} 条）: "
              f"{out_dir / 'calibration_samples.json'}")
    print(f"\n结果已写入: {out_dir}")
    return {"meta": meta, "metrics": metrics, "results": results}


def render_calibration_samples(results: list) -> list:
    """校准样本（brief 契约：任务 id + turns + 完整真实回复 + judge 五维分 + 加权分）。"""
    out = []
    for r in results:
        out.append({
            "id": r["id"], "category": r["category"], "severity": r["severity"],
            "title": r["title"], "turns": r.get("turns") or [],
            "replies": r.get("replies") or [],
            "dims": r.get("dims") or {},
            "overall": r.get("overall") or 0.0,
            "parse_level": r.get("parse_level"),
            "judge_error": r.get("judge_error", False),
            "skipped": r.get("skipped", False),
        })
    return out


def render_report_md(meta: dict, metrics: dict, results: list) -> str:
    L = []
    L.append("# L3 质量判卷报告（E4）\n")
    L.append(f"- 运行时间: {meta['run_at']}")
    L.append(f"- 判卷模型: {meta['judge_model']} "
             f"(temperature={meta['judge_temperature']}, "
             f"max_tokens={meta['judge_max_tokens']}) | "
             f"主链路由: {meta['chain_model_route']} | 仓库版本: {meta['repo_version']}")
    L.append(f"- 任务范围: {len(meta['scope'])} 条"
             + (f"（30 条校准抽样：P0/P1/P2 各 10 覆盖 10 域）"
                if meta.get("calibration") else ""))
    L.append(f"- rubric: {meta['rubric_source']}")
    L.append(f"- 门禁: {meta['gate']}\n")
    L.append("## 指标\n")
    L.append("| 指标 | 值 | 参考阈值（spec §5.3，E6 接线） |")
    L.append("|---|---|---|")
    L.append(f"| 加权平均（已判卷 {metrics['judged']} 条） | "
             f"{metrics['weighted_avg'] if metrics['weighted_avg'] is not None else '—'} "
             f"| 全量 ≥7.5 |")
    L.append(f"| 加权平均（含 judge_error 按 0 计） | "
             f"{metrics['weighted_avg_incl_errors'] if metrics['weighted_avg_incl_errors'] is not None else '—'} | — |")
    L.append(f"| P0 平均（{metrics['p0_count']} 条） | "
             f"{metrics['p0_avg'] if metrics['p0_avg'] is not None else '—'} | ≥8 |")
    L.append(f"\n首测基线如实留档：真实数字即基线，不达标也不美化——校准优先"
             f"（人工标注对比一致率 ≥75% 才启用，spec §5.3）。\n")
    L.append("### 五维平均（已判卷）\n")
    L.append("| 维度 | 平均分 | 权重 |")
    L.append("|---|---|---|")
    for d in DIMS:
        v = metrics["per_dimension"][d]
        L.append(f"| {d} | {v if v is not None else '—'} | "
                 f"{DIMENSION_WEIGHTS[d]:.0%} |")
    L.append("\n### 分域平均（已判卷）\n")
    L.append("| 域 | 平均加权分 | 任务数 |")
    L.append("|---|---|---|")
    cat_count = {}
    for r in results:
        if not r.get("skipped") and not r["judge_error"]:
            cat_count[r["category"]] = cat_count.get(r["category"], 0) + 1
    for k, v in metrics["by_category"].items():
        L.append(f"| {k} | {v if v is not None else '—'} | {cat_count.get(k, 0)} |")
    L.append("")
    if metrics["skipped"]:
        L.append(f"## 跳过清单（{len(metrics['skipped'])} 条，无回复/主链跑失败）\n")
        for r in results:
            if r.get("skipped"):
                L.append(f"- {r['id']} {r['title']}: {r['skip_reason']}")
        L.append("")
    if metrics["judge_error"]:
        L.append(f"## 判卷失败清单（{len(metrics['judge_error'])} 条，judge_error）\n")
        for r in results:
            if not r.get("skipped") and r["judge_error"]:
                L.append(f"- {r['id']} {r['title']}: "
                         f"{r['judge_error_reason'] or '解析三级兜底后维度缺失'}")
        L.append("")
    L.append("## 逐任务明细\n")
    L.append("| id | 域 | 级别 | 加权分 | accuracy | completeness | personalization | actionability | citation_quality | 解析 | 状态 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        if r.get("skipped"):
            L.append(f"| {r['id']} | {r['category']} | {r['severity']} | — | — | — | "
                     f"— | — | — | — | SKIP: {r['skip_reason'][:40]} |")
            continue
        dims = r["dims"]
        status = "JUDGE_ERR" if r["judge_error"] else "OK"
        L.append(f"| {r['id']} | {r['category']} | {r['severity']} | {r['overall']} "
                 f"| {dims['accuracy']['score']} | {dims['completeness']['score']} "
                 f"| {dims['personalization']['score']} | {dims['actionability']['score']} "
                 f"| {dims['citation_quality']['score']} | L{r['parse_level']} | {status} |")
    L.append("")
    return "\n".join(L)


# ================================================================
# CLI
# ================================================================

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="L3 质量判卷（E4）：跑主链收集真实回复 → 免费 glm-4-flash "
                    "五维判卷 → 三级兜底 → 报告；默认 30 条校准抽样")
    ap.add_argument("--tasks", default="",
                    help="只跑指定任务 id（逗号分隔，如 T001,T019）")
    ap.add_argument("--category", default="",
                    help="只跑指定域（paipan/fortune/zeri/hehun/xingming/"
                         "qian/liuyao/ziwei/chat/edge）")
    ap.add_argument("--all", action="store_true", help="跑全量 100 条")
    ap.add_argument("--out", default="",
                    help="结果目录（默认 data/eval/results/l3-<时间戳>/）")
    ap.add_argument("--keep-tmp", action="store_true",
                    help="保留临时隔离目录（默认退出清理）")
    args = ap.parse_args(argv)

    # 1) 评估集校验（唯一事实源，只读；失败退出码 2）
    if not TASKS_PATH.exists():
        print(f"FATAL: 评估集不存在: {TASKS_PATH}", file=sys.stderr)
        return 2
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR), str(TASKS_PATH)],
        capture_output=True, text=True, timeout=120)
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        print(f"FATAL: 评估集校验失败（validate_tasks 退出码 {proc.returncode}）",
              file=sys.stderr)
        return 2

    tasks = [json.loads(line) for line in TASKS_PATH.read_text(encoding="utf-8")
             .splitlines() if line.strip()]
    calibration_ids = select_calibration_ids(tasks)
    if args.tasks:
        want = {x.strip() for x in args.tasks.split(",") if x.strip()}
        unknown = sorted(want - {t["id"] for t in tasks})
        if unknown:
            print(f"FATAL: 未知任务 id: {unknown}", file=sys.stderr)
            return 2
        ids = [t["id"] for t in tasks if t["id"] in want]
    elif args.category:
        if args.category not in l1_eval.CATEGORIES:
            print(f"FATAL: 未知域: {args.category}（合法: "
                  f"{','.join(l1_eval.CATEGORIES)}）", file=sys.stderr)
            return 2
        ids = [t["id"] for t in tasks if t["category"] == args.category]
    elif args.all:
        ids = [t["id"] for t in tasks]
    else:
        ids = calibration_ids
    selected = [t for t in tasks if t["id"] in ids]
    if not selected:
        print("FATAL: 无任务可跑", file=sys.stderr)
        return 2
    calibration = ids == calibration_ids

    out_dir = (Path(args.out) if args.out
               else RESULTS_ROOT / f"l3-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        run_eval(selected, out_dir, calibration=calibration,
                 keep_tmp=args.keep_tmp)
    except RuntimeError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: 运行器错误: {type(e).__name__}: {str(e)[:300]}",
              file=sys.stderr)
        return 2

    m = (out_dir / "meta.json")
    if m.exists():
        meta = json.loads(m.read_text(encoding="utf-8"))
        mm = meta["metrics"]
        print(f"\n指标: 已判卷 {mm['judged']} | 加权平均 "
              f"{mm['weighted_avg'] if mm['weighted_avg'] is not None else '—'} "
              f"| P0 平均 {mm['p0_avg'] if mm['p0_avg'] is not None else '—'}")
        if mm["skipped"]:
            print(f"跳过 {len(mm['skipped'])} 条: {mm['skipped']}")
        if mm["judge_error"]:
            print(f"判卷失败 {len(mm['judge_error'])} 条: {mm['judge_error']}")
        if meta.get("calibration"):
            print("校准样本: calibration_samples.json 已落盘（供主会话人工标注）")
        return 0 if not (mm["judge_error"] or mm["skipped"]) else 1
    print("FATAL: 结果未落盘", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
