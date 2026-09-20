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

## 集成分支（batch2-k61）变更登记：准确率测量**及其种子语料**随死模块一并移除

- **移除**：原 r4 ② 的 `test_e2e_mood_accuracy_reports_only`（+ 其助手
  `_stratified_sample` / `rows_to_moods` / `E2E_PER_CLASS`）。
  它 `from src.engines.mood_detector import MoodDetector`（**产品模块**），
  而该模块已被 k62（`2a67833`）按用户拍板作为死代码删除 → 该用例永远
  `ModuleNotFoundError`（实测：`No module named 'src.engines.mood_detector'`）。
  ⚠️ **该能力（情绪/人设判定）随模块一起移除 —— 它是死模块，"情绪/人设判定"
  从未接入产品（`src/` 下 0 引用、无生产调用点）。这里不是"丢了一个在用的能力"，
  而是"删掉了一个从未被用过的能力的测试"**。如将来真要做该能力，需**重新立项**
  （k62 守卫 `tests/test_k62_deadcode_removal_guard.py` 会拦住模块复活）。
- **数据一并移除**：原文件内的 `LABELED_TEST_CASES`（54 条分层种子语料）**已整体删除**
  —— 模块删了，它的数据跟着走。原集成分支曾把它**就地内联**为"存档"保留
  （为免从被删的 `tests/test_mood_detector.py` import 而碎掉），后经复核**改判为删除**。

## ⚠️ 给将来重建该能力的人：**不要复用旧标签，请重新设计标注**

删掉那 54 条**不等于**"只是丢了一份草稿" —— 复核时查出**三条可复现的一手证据**，
说明它**不是**一份可复用的标注资产（这也是"该能力当年为什么没接进产品"的一部分解释）：

1. **出处文件自身元数据从未与内容对齐**：同一文件 **4 处写「50 条」**
   （`# Acceptance Test: 50 Test Cases…`、`# 50 labeled test cases…`、类 docstring
   `Verify MoodDetector accuracy on 50…`、断言消息 `All 50 test cases should have…`），
   而实际恒为 **54**（断言写死 54）。
   ⇒ 「声明与事实不符」这个形态**在它的源头就存在** —— 与本次修复撞上的
   「k61 r8 写 24 条 vs 事实源 54 条」**同源**。它不是一份被精心维护的资产。
2. **部分期望编码的是已废止的口径（= 已知为错的样本）**：
   `你好 / 在吗 / 好的谢谢 / 早上好 / 明白了` 被期望成 `sassy` —— 那是把**中性问候**
   绑到「3 档人设」的**语气约定**上，而这套人设**已被用户拍板拆除**（k62/k63）。
   ⇒ 这份"标注"里**有已知为错的样本**，所以它不是"未验证"，是**部分错误**。
   ⚠️ 一个**部分标签已知为错**的数据集，即便标成"草稿"，仍会诱使后来者拿它当起点；
   而"草稿"这个限定**依赖未来的读者去读这段 docstring** —— 那是脆弱的保障。
3. **内部一致性从未被验证**：k61 在该语料上的实测（`tests/test_mood_detector.py:518`）
   为全 54 条 **0.556**，其中 `sassy` **1/20**。20 条只中 1 条，既可能是模型不行、
   也可能是**标签与自然读法不一致** —— 而**没有任何记录**做过标注者一致性复核
   来排除后者。

**⇒ 重建该能力时，正确做法是重新设计标注**（含**标注者一致性复核**与口径评审），
**而不是复用这份源头就有「声明与事实不符」、且部分标签编码已废止约定的草稿**。
（本段是该能力的**唯一**遗留登记；对应的防复活守卫仍在
`tests/test_k62_deadcode_removal_guard.py`，会拦住 `mood_detector` 模块复活。）
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
