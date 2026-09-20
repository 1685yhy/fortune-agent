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

## 集成分支（batch2-k61）变更登记：准确率测量随死模块一并移除，种子语料**存档**

- **移除**：原 r4 ② 的 `test_e2e_mood_accuracy_reports_only`（+ 其助手
  `_stratified_sample` / `rows_to_moods` / `E2E_PER_CLASS`）。
  它 `from src.engines.mood_detector import MoodDetector`（**产品模块**），
  而该模块已被 k62（`2a67833`）按用户拍板作为死代码删除 → 该用例永远
  `ModuleNotFoundError`（实测：`No module named 'src.engines.mood_detector'`）。
  ⚠️ **该能力（情绪/人设判定）随模块一起移除 —— 它是死模块，"情绪/人设判定"
  从未接入产品（`src/` 下 0 引用、无生产调用点）。这里不是"丢了一个在用的能力"，
  而是"删掉了一个从未被用过的能力的测试"**。如将来真要做该能力，需**重新立项**
  （k62 守卫 `tests/test_k62_deadcode_removal_guard.py` 会拦住模块复活）。
- **存档（注意框定）**：`LABELED_TEST_CASES`（54 条）**就地内联**在本文件（下方），
  不再从被删的 `tests/test_mood_detector.py` import。它是
  **为一个从未接入产品的能力所准备的、已标注的种子语料，因该能力移除而存档**；
  **唯一消费者已随被测模块一起删除，本文件里没有任何东西在用它**
  （下面没有任何测试读这个常量 —— 不要把它读成"还有个能力在用它"）。
  存档理由：**标注数据不可再生**，且它已被显式标注 + 有锁，不会像死模块那样误导人。
  ⚠️ **存档质量提示**：这是**手写的期望集，不是经过验证的标注语料** ——
  出处文件自身元数据在 4 处写「50 条」而实际 54 条（自相矛盾，从未对齐）；
  其中「你好 / 在吗 / 早上好 / 好的谢谢 / 明白了」期望 `sassy` 属**已废止的口径**
  （把中性问候绑到 3 档人设的语气约定上，与 k62/k63 拆掉的人设同源）。
  将来若真要重建该能力，**应把它当草稿起点复核，而不是当基准真值**。
  条数取证：k61 r8 §8.3 曾写「24 条」，与事实源不符 —— 该常量在
  `main` / `ea110c3` / `2a67833^` / `k61-registered-r2` **每个含它的 ref 上都是 54 条**
  （k61 自己 `tests/test_mood_detector.py:518` 亦写「全 54 条 0.556」）。
  集成分支按**事实源 54 条**内联；样本语义逐条逐序不变（证据与锁见
  `tests/test_k61_e2e_seed_corpus.py`）。
- **移除触发条件（登记项，防止成为永久陈列品）**：**若该能力（情绪/人设判定）
  在后续规划中不再重建，则此存档与其锁一并移除**（即删掉下方
  `LABELED_TEST_CASES` 常量 + `tests/test_k61_e2e_seed_corpus.py`）。
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
# 【存档】分层种子语料（就地内联，**不得改回 import**）
# ══════════════════════════════════════════════════════════════════
#
# ⚠️ **这是存档，不是活资产**：它为一个**从未接入产品**的能力（情绪/人设判定，
# `src/engines/mood_detector.py`，已由 k62 `2a67833` 按用户拍板作为死代码拆除）
# 而准备；**它唯一的消费者（原 `test_e2e_mood_accuracy_reports_only`）已随该模块
# 一起删除 —— 本文件里没有任何测试读这个常量**，别把它读成"还有个能力在用它"。
#
# 出处：`tests/test_mood_detector.py`（该文件已随死模块被 k62 `2a67833` 删除）
# 的 `LABELED_TEST_CASES`，**逐字**搬来，含原始分组注释与顺序 —— 顺序是语义的一部分：
# 分层抽样取「每类前 N 条」，换序即换样本。
#
# 格式：(message, expected_mood, category)
#
# ⚠️ 质量提示：这是**手写期望集，不是经验证的标注语料**（出处文件自身元数据 4 处写
# 「50 条」而实际 54 条；且把「你好/在吗/早上好」这类中性问候期望成 `sassy`，
# 属已废止的人设口径）。重建该能力时应把它当草稿起点复核，**不当基准真值**。
#
# 存档理由 = 标注数据不可再生 + 已被显式标注，不会像死模块那样误导人。
# 存续由 `tests/test_k61_e2e_seed_corpus.py` 在**常规门禁**里锁住形状与抽样
# （该锁在常规门禁里跑，不随 `K61_E2E` 跳过）。
# **移除触发条件**：若该能力在后续规划中不再重建，则本存档与其锁一并移除。
LABELED_TEST_CASES = [
    # ── Anxiety / Worry / Fear -> gentle ──
    ("我好焦虑，不知道该怎么办", "gentle", "anxiety"),
    ("最近压力好大，晚上睡不着", "gentle", "anxiety"),
    ("我害怕这次考试会考砸", "gentle", "fear"),
    ("担心老公的身体，他最近总说累", "gentle", "worry"),
    ("很紧张，明天要去面试了", "gentle", "anxiety"),
    ("最近总是很烦躁，看什么都不顺眼", "gentle", "anxiety"),
    ("我好害怕失去这份工作", "gentle", "fear"),
    ("总觉得心里不踏实", "gentle", "anxiety"),
    ("每天都在担心孩子的成绩", "gentle", "worry"),
    ("最近工作特别累，想辞职了", "gentle", "exhaustion"),

    # ── Data / Analysis / Numbers -> analyst ──
    ("帮我分析一下明年的财运走势", "analyst", "analysis"),
    ("从命理角度分析我适合什么职业", "analyst", "analysis"),
    ("我的八字里木旺不旺？和金的关系是什么", "analyst", "data"),
    ("给我一个数据分析，我什么时候能升职", "analyst", "data"),
    ("这个投资方案成功率有多少", "analyst", "analysis"),
    ("比较一下申月和酉月对我的影响", "analyst", "analysis"),
    ("用数据分析一下我今年的事业运势", "analyst", "data"),
    ("从概率角度分析我该不该跳槽", "analyst", "analysis"),
    ("做一个详细的流年分析报告", "analyst", "analysis"),
    ("帮我看看这个合婚配对的结果", "analyst", "analysis"),
    ("今年有几个重要时间节点需要关注", "analyst", "analysis"),
    ("用统计学角度看看我的财运", "analyst", "data"),
    ("这个八字格局有什么特点", "analyst", "analysis"),
    ("从五行角度分析一下我的体质", "analyst", "analysis"),
    ("我的八字里哪些元素比较强", "analyst", "data"),

    # ── Humor / Casual / Joking -> sassy ──
    ("哈哈哈大师我的桃花运来了吗", "sassy", "humor"),
    ("今天心情超好，感觉要发财了", "sassy", "joy"),
    ("笑死，测了好几个八字都说我会发财", "sassy", "humor"),
    ("哎呀今天被夸了，开心死了", "sassy", "joy"),
    ("哈哈哈上次你说的话真的太准了", "sassy", "humor"),
    ("我是不是命里带财啊？开个玩笑哈哈", "sassy", "humor"),
    ("今天运气也太好了吧", "sassy", "joy"),
    ("来给我算算啥时候能暴富", "sassy", "casual"),
    ("哈哈刚买彩票就让我来算一卦", "sassy", "humor"),
    ("今天天气真好，心情也跟着好了", "sassy", "joy"),
    ("帮我看看我是不是天选之子", "sassy", "humor"),
    ("大师我今天捡到钱了！", "sassy", "joy"),
    ("最近运气爆棚啊，来算算能不能持续", "sassy", "joy"),
    ("哈哈我感觉我要走上人生巅峰了", "sassy", "humor"),
    ("我上辈子是不是拯救了银河系", "sassy", "humor"),

    # ── Anger / Frustration -> gentle (de-escalate) ──
    ("我真的很生气，感觉被坑了", "gentle", "anger"),
    ("太让人火大了，这什么破事", "gentle", "anger"),
    ("烦死了，每天都遇到倒霉事", "gentle", "frustration"),
    ("我对这个结果非常不满意", "gentle", "anger"),
    ("忍了很久了，这次真的受不了", "gentle", "frustration"),

    # ── Confusion / Uncertainty -> analyst (clarify) ──
    ("好纠结要不要换工作，帮我想想", "analyst", "confusion"),
    ("不知道该怎么选择，给点建议", "analyst", "confusion"),
    ("我很迷茫，不知道未来的方向", "gentle", "confusion"),
    ("想不通为什么总是遇到这种事", "gentle", "confusion"),

    # ── Neutral / Simple Greeting -> sassy ──
    ("你好", "sassy", "neutral"),
    ("在吗", "sassy", "neutral"),
    ("好的谢谢", "sassy", "neutral"),
    ("早上好", "sassy", "neutral"),
    ("明白了", "sassy", "neutral"),
]
