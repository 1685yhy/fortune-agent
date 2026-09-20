# -*- coding: utf-8 -*-
"""k61 r2 ②：**显式 opt-in** 的真实 LLM 端到端冒烟（免费 glm-4-flash）。

## 为什么需要它

k61 P2 把 `test_response_under_5_seconds` 的**远端**延迟断言收口成了本地管线断言
（原判据测的是远端 LLM 响应延迟，实测 17.2–29.2s，5 秒阈值站不住）。
那条用例当时**歪打正着**地也是全仓唯一守着「端到端能在 5 秒内返回」这个
**产品级承诺**的东西 —— 收口后这个承诺就没人管了。

本文件把它补回来，但**不放进常规门禁**：
- 真实 LLM 调用必然慢且抖动（远端延迟不是我们不变量），放进常规门禁 =
  重复 k57 的假红；
- 所以**默认 skip**，只在显式要求时跑，并把耗时**打出来**供人工判读。

## 触发方式（默认不跑）

    K61_E2E=1 TMPDIR=/dev/shm /home/a/fortune-run/.venv/bin/python3 \\
        -m pytest tests/test_k61_e2e_smoke.py -q -s
    # 或按标记筛选（同样需要 K61_E2E=1 打开开关）：
    K61_E2E=1 ... -m pytest tests/ -m e2e -q -s

**部署后手工跑一次**（在部署目录，`.env` 提供 ZHIPU_API_KEY）：

    cd <部署目录> && K61_E2E=1 TMPDIR=/dev/shm ./.venv/bin/python3 \\
        -m pytest tests/test_k61_e2e_smoke.py -q -s

## 诚实前提（务必知道）

本冒烟走的是**免费 glm-4-flash**，所以它测的是「端到端链路能返回 + 该链路的耗时」，
**不是**生产 DeepSeek 链路的延迟。生产延迟**无法**在测试里测 —— k61 红线禁止测试
访问 DeepSeek。产品级 5 秒承诺的最终判读需要**人工**在部署环境看线上数据
（或另开批次的非门禁探针），本文件的数字只作**参考基线**。
"""
import os
import time

import pytest

# 生产入口（uvicorn src.main:app）必然 `import src.config` —— 导入即
# `load_env_file(".env")`。本文件若**单独跑**（`pytest tests/test_k61_e2e_smoke.py`），
# 其 import 链（engines/llm client）**不含** src.config → `.env` 不会加载 →
# ZHIPU_API_KEY 取不到 → 冒烟恒 skip。显式导入 = 与生产同一加载路径，
# 单独跑也能拿到部署 `.env` 的免费 GLM key。
import src.config  # noqa: F401

from src.engines.advisor_v2 import AdaptiveAdvisor
from src.engines.bazi import BaziEngine
from src.llm.client import GLM_DEFAULT_MODEL

#: 产品级承诺（人工判读用，不在此断言 —— 见模块 docstring 的诚实前提）
PRODUCT_SLO_SECONDS = 5.0

#: 挂死保护：真实调用必须在这个上限内返回（远超任何正常远端延迟）
HANG_GUARD_SECONDS = 300.0


def _e2e_enabled() -> bool:
    return os.environ.get("K61_E2E", "").strip().lower() in ("1", "true", "yes", "on")


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not _e2e_enabled(),
        reason="真实 LLM 端到端冒烟：默认 skip，需 K61_E2E=1 显式触发（见模块 docstring）",
    ),
]


def _report(label: str, elapsed: float, extra: str = "") -> None:
    verdict = "HIT" if elapsed < PRODUCT_SLO_SECONDS else "MISS"
    print(f"\n[k61 e2e] {label} route={GLM_DEFAULT_MODEL} elapsed={elapsed:.2f}s "
          f"product_slo({PRODUCT_SLO_SECONDS:.0f}s)={verdict} {extra}".rstrip())


def test_e2e_llm_chain_alive():
    """冒烟 1：统一 LLM 层能真的拿到一段回复（链路活着，分钟级挂死保护）。"""
    from src.llm.client import glm_openai_completion
    key = os.environ.get("ZHIPU_API_KEY", "").strip()
    if not key:
        pytest.skip("缺少 ZHIPU_API_KEY（免费 glm-4-flash）")

    start = time.time()
    out = glm_openai_completion(
        key, [{"role": "user", "content": "只回复两个字：收到"}],
        model=GLM_DEFAULT_MODEL, max_tokens=16, temperature=0.1, timeout=60.0,
    )
    elapsed = time.time() - start
    _report("unified-client ping", elapsed, f"reply={out[:12]!r}")
    assert out.strip(), "统一层返回空文本（链路有问题）"
    assert elapsed < HANG_GUARD_SECONDS


def test_e2e_advisor_generate_reports_latency(glm_route):
    """冒烟 2：`AdaptiveAdvisor.generate()` 真实端到端 + **打印耗时**。

    断言只保「结构正确 + 没挂死」；耗时是**报告项**，不做门禁断言
    （远端延迟非不变量，压成硬墙钟就是 k57 的假红）。
    """
    advisor = AdaptiveAdvisor()
    bazi = BaziEngine().calculate(1990, 5, 20, 15, 0, "北京", "男")

    start = time.time()
    result = advisor.generate(
        bazi, user_context="最近工作很忙，想了解事业运势", api_key=glm_route)
    elapsed = time.time() - start

    _report("advisor.generate end-to-end", elapsed,
            f"actions={len(result.get('actions', []))} "
            f"serendipity={bool(result.get('serendipity'))}")
    assert len(result.get("actions", [])) == 5, "端到端应产出 5 个生活领域建议"
    assert elapsed < HANG_GUARD_SECONDS


# ══════════════════════════════════════════════════════════════════
# r4 ②（控制方裁决 = 方案 B）：真 provider 的**准确率**测量 —— 只报数，不设阈值
# ══════════════════════════════════════════════════════════════════
#
# 为什么在这里而不是门禁里：0.70 那个阈值是**在 DeepSeek 上校准**的，而本批按红线
# 只能跑免费 glm-4-flash —— 实测免费档够不到该阈值（见下面的报数）。把"按 provider
# 分档"当修法 = 实质放宽判据（控制方不批）；"保持 0.70 让它红" = 制造噪音（也不批）。
# 于是：**判别力**留在门禁（`tests/test_mood_detector.py` 的
# `TestAccuracySampleHasDiscriminativePower`：死端点必须 < 0.70 + 分层样本必须
# 覆盖三类），**真实准确率**在这里**测量并报数**，供人工判读降级档质量。
#
# 报数必须同时给出 **provider** 与 **样本量**（控制方硬要求），并保留分层样本。

#: 分层样本每类取几条（与门禁内 `representative_sample()` 同一口径：每类前 N 条）
E2E_PER_CLASS = 4


def _stratified_sample():
    """分层样本：gentle/analyst/sassy 各取前 4 条（确定性、可复现）。"""
    import collections

    from test_mood_detector import LABELED_TEST_CASES
    by = collections.defaultdict(list)
    for case in LABELED_TEST_CASES:
        by[case[1]].append(case)
    sample = []
    for cls in ("gentle", "analyst", "sassy"):
        sample.extend(by[cls][:E2E_PER_CLASS])
    return sample


@pytest.mark.e2e
def test_e2e_mood_accuracy_reports_only(glm_route):
    """真 provider 的情绪/人设判定准确率 —— **只报数**（provider + 样本量 + 分层明细）。

    不设通过阈值：阈值是 provider 相关的，而本批只能跑免费档（见模块顶部说明）。
    """
    from src.engines.mood_detector import MoodDetector

    sample = _stratified_sample()
    detector = MoodDetector(api_key=glm_route)
    import collections
    per = collections.defaultdict(lambda: [0, 0])
    rows = []
    for msg, expected, _ in sample:
        got = detector.detect(msg).mood
        per[expected][1] += 1
        if got == expected:
            per[expected][0] += 1
        rows.append(f"{expected}->{got}")

    total = len(sample)
    correct = sum(v[0] for v in per.values())
    provider = f"zhipu/{GLM_DEFAULT_MODEL}"
    print(f"\n[k61 e2e] mood accuracy provider={provider} n={total} "
          f"score={correct}/{total}={correct / total:.3f}")
    for cls in ("gentle", "analyst", "sassy"):
        c, n = per[cls]
        print(f"[k61 e2e]   {cls:8s} {c}/{n}")
    print(f"[k61 e2e]   detail: {' '.join(rows)}")

    # 只断言"测完了"（每条都真的被判过一次），不断言分数 —— 分数是报告项。
    assert len(rows) == total
    assert all(m in ("sassy", "analyst", "gentle") for m in rows_to_moods(rows))
    assert 0.0 <= correct / total <= 1.0


def rows_to_moods(rows):
    """从 `exp->got` 明细里取出 got 列（自检用）。"""
    return [r.split("->", 1)[1] for r in rows]
