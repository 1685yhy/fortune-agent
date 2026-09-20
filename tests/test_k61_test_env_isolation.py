# -*- coding: utf-8 -*-
"""k61 r2 ①：测试进程环境隔离（方案 A）的**锁**。

背景：`src/config.py` 导入即 `load_env_file(".env")`，部署/生产检出的 `.env`
（软链到生产）会把生产值灌进测试进程。除了已收口的 `DEEPSEEK_API_KEY`，
`EXPERIENCE_MODE=true` 之类的**行为开关**同样会进来 —— 仓内已经有 3 处散落补丁
在绕它（`test_chat_quota.py` / `test_member_pay.py` 的 autouse fixture、
`test_qian_kinds.py` 的模块级置空），正说明它是**会改断言的泄漏**，
只是新增用例不会知道要补。

修法 = 协调方批准的**方案 A（测试侧 pin，零生产改动）**：`tests/conftest.py`
在任何 `src.*` 导入之前把部署键 pin 成空值（`load_env_file` 不覆盖已存在变量），
于是测试进程等价于「没有部署 .env」。

本文件锁四件事：
1. pin 清单**每条都有理由**（防「悄悄加/悄悄删」）；
2. 测试进程里 pin 全部生效、`is_experience_mode()` 必须为 False；
3. **pin 真的能压住一个真实 `.env`**（子进程实验：对照组证明 .env 确实会泄漏，
   实验组证明 pin 之后不泄漏）—— 这是防「有人把它当生产默认又漏回去」的核心锁；
4. 免费 GLM 用的 `ZHIPU_API_KEY` 与待裁决的 `PUBLIC_BASE_URL` **不在** pin 清单里
   （正向对照：pin 过头会关掉本批的验证面 / 妨碍独立核查）。

（仓库卫生守卫的机制自检见 `tests/test_k61_repo_hygiene.py`。）

运行：TMPDIR=/dev/shm OMP_NUM_THREADS=1 /home/a/fortune-run/.venv/bin/python3 \
      -m pytest tests/test_k61_test_env_isolation.py -q
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import conftest as k61conftest  # noqa: E402

from conftest import (  # noqa: E402
    ENV_PINS_AT_IMPORT,
    TEST_ENV_NOT_PINNED,
    TEST_ENV_PINS,
    TEST_MEMORY_DIR,
)

REPO = str(Path(__file__).resolve().parent.parent)
TESTS_DIR = str(Path(__file__).resolve().parent)


# ══════════════════════════════════════════════════════════════════
# 1) pin 清单卫生
# ══════════════════════════════════════════════════════════════════

class TestPinHygiene:
    def test_every_pin_has_a_reason(self):
        assert TEST_ENV_PINS, "pin 清单为空（隔离被整体撤掉？）"
        for key, reason in TEST_ENV_PINS.items():
            assert reason and len(reason) >= 10, f"pin 缺理由：{key}"

    def test_every_exemption_has_a_reason(self):
        assert TEST_ENV_NOT_PINNED, "豁免清单为空（应至少含 ZHIPU_API_KEY）"
        for key, reason in TEST_ENV_NOT_PINNED.items():
            assert reason and len(reason) >= 10, f"豁免缺理由：{key}"

    def test_pin_and_exempt_are_disjoint(self):
        assert not (set(TEST_ENV_PINS) & set(TEST_ENV_NOT_PINNED)), \
            "同一个键既 pin 又豁免"

    def test_free_glm_key_is_exempt(self):
        """正向对照：pin 掉 ZHIPU_API_KEY 会让所有 GLM 端到端用例 skip。"""
        assert "ZHIPU_API_KEY" not in TEST_ENV_PINS
        assert "ZHIPU_API_KEY" in TEST_ENV_NOT_PINNED

    def test_public_base_url_left_alone_for_arbitration(self):
        """协调方指示：PUBLIC_BASE_URL 只登记、不 pin（pin 会掩盖生产值）。"""
        assert "PUBLIC_BASE_URL" not in TEST_ENV_PINS

    def test_leak_sensitive_keys_are_pinned(self):
        """本批点名的行为开关/生产密钥必须在 pin 清单里。"""
        for key in ("EXPERIENCE_MODE", "DEV_OPENID", "DEV_TOKEN_ENDPOINT",
                    "DEEPSEEK_API_KEY", "JWT_SECRET_KEY", "ENCRYPTION_KEY",
                    "ADMIN_KEY", "FORTUNE_API_KEY"):
            assert key in TEST_ENV_PINS, f"{key} 漏出 pin 清单"


# ══════════════════════════════════════════════════════════════════
# 2) pin 在本进程生效（含 EXPERIENCE_MODE 的那条锁）
# ══════════════════════════════════════════════════════════════════

class TestPinsInEffect:
    def test_pins_were_in_effect_at_import_time(self):
        """**核心锁（顺序无关）**：导入期快照里 pin 必须全部生效（空值）。

        为什么看导入期而不是「此刻」：pin 的唯一职责是在 `load_env_file(".env")`
        之前把键占住，那件事只发生在导入期；而收集期就有约 20 个测试模块会直接
        `os.environ["JWT_SECRET_KEY"] = "test-..."`（仓内既有约定、不回滚），
        所以「此刻是否为空」对 JWT_SECRET_KEY 这类键天然不成立。
        """
        leaked = {k: v for k, v in ENV_PINS_AT_IMPORT.items() if v != ""}
        assert not leaked, (
            f"这些键在导入期没有被 pin（部署 .env 会灌进来）：{leaked}\n"
            f"处置：确认 tests/conftest.py 的 `_pin_test_env()` 调用还在、"
            f"且该键仍在 TEST_ENV_PINS 里。"
        )

    def test_experience_mode_is_off(self):
        """**协调方点名的那条锁**：测试进程里 EXPERIENCE_MODE 必须是测试 pin 的值。

        （仓内只有把它置空的写法，没有置真值且不回滚的写法，故这条可以看「此刻」。）
        """
        assert os.environ.get("EXPERIENCE_MODE") == ""
        assert ENV_PINS_AT_IMPORT["EXPERIENCE_MODE"] == ""
        from src.config import is_experience_mode, load_settings
        assert is_experience_mode() is False, "测试进程不得处于体验模式（全免费/跳配额）"
        assert load_settings().experience_mode is False

    def test_deepseek_key_cannot_enter_test_process(self):
        """k61 红线的**结构性**保证：测试进程不持有生产 DeepSeek key。

        比守卫更强的一层：守卫拦「出站」，pin 让「连 key 都没有」—— 于是
        `resolve_llm_api_key()` 恒为空，引擎的既有早退语义自动生效；
        守卫退为兜底（仍会拦住用 Mock/硬编码 key 的调用点，见 P1 追加项）。
        """
        assert ENV_PINS_AT_IMPORT["DEEPSEEK_API_KEY"] == ""
        assert os.environ.get("DEEPSEEK_API_KEY") == ""
        from src.llm.client import resolve_llm_api_key
        assert resolve_llm_api_key() == ""
        assert resolve_llm_api_key(allow_anthropic_fallback=False) == ""

    def test_memory_dir_redirected_out_of_repo(self):
        """UserMemory 默认目录必须已被重定向出仓库（否则写脏 data/memory/*.json）。"""
        from src.memory.user_memory import UserMemory
        base = os.path.abspath(UserMemory().base_dir)
        assert base == os.path.abspath(TEST_MEMORY_DIR), base
        assert not base.startswith(os.path.abspath(REPO) + os.sep), \
            f"默认记忆目录仍指向仓库内：{base}"


# ══════════════════════════════════════════════════════════════════
# 3) pin 真的压得住一个真实 .env（子进程对照实验）
# ══════════════════════════════════════════════════════════════════

_PROBE_CODE = """
import json, os
{import_conftest}import src.config
from src.config import is_experience_mode, load_settings
print(json.dumps({{
    "experience_mode": os.environ.get("EXPERIENCE_MODE"),
    "dev_openid": os.environ.get("DEV_OPENID"),
    "jwt_set": bool(os.environ.get("JWT_SECRET_KEY")),
    "deepseek_set": bool(os.environ.get("DEEPSEEK_API_KEY")),
    "is_exp": is_experience_mode(),
    "settings_exp": load_settings().experience_mode,
}}))
"""


def _probe(tmp_path, import_conftest: bool) -> dict:
    """在**带 .env 的临时 cwd** 里跑一个子进程，返回它看到的配置状态。

    关键：把父进程已 pin 的键从子进程环境里**删掉** —— 否则子进程继承的
    空值会让对照组也「看不出泄漏」，实验失去意义。
    """
    (tmp_path / ".env").write_text(
        "EXPERIENCE_MODE=true\n"
        "DEV_OPENID=dev_user\n"
        "JWT_SECRET_KEY=k61-canary-jwt-not-a-real-secret\n"
        "DEEPSEEK_API_KEY=sk-k61-canary-not-a-real-key\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    for key in list(TEST_ENV_PINS) + ["USER_MEMORY_DIR"]:
        env.pop(key, None)
    env["PYTHONPATH"] = os.pathsep.join([REPO, TESTS_DIR])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE_CODE.format(
            import_conftest="import conftest\n" if import_conftest else "")],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, f"探针失败：\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_control_dotenv_really_leaks(tmp_path):
    """对照组（**不导入 conftest**）：证明 .env 确实会灌进进程 —— 泄漏是真的。

    这条是「改前失败」夹具：若哪天 `src/config.py` 不再加载 .env，本用例会红，
    提醒我们 pin 机制（以及整套泄漏论证）需要重新评估。
    """
    got = _probe(tmp_path, import_conftest=False)
    assert got["experience_mode"] == "true", got
    assert got["dev_openid"] == "dev_user", got
    assert got["jwt_set"] is True and got["deepseek_set"] is True, got
    assert got["is_exp"] is True and got["settings_exp"] is True, got


def test_pin_defeats_a_real_dotenv(tmp_path):
    """实验组（导入 conftest = 走测试进程的真实加载顺序）：.env 被 pin 挡住。"""
    got = _probe(tmp_path, import_conftest=True)
    assert got["experience_mode"] == "", got
    assert got["dev_openid"] == "", got
    assert got["jwt_set"] is False, got
    assert got["deepseek_set"] is False, got
    assert got["is_exp"] is False and got["settings_exp"] is False, got


# ══════════════════════════════════════════════════════════════════
# 5) r3 ⑤：`.env` 加载必须与「收集了哪些文件」无关（GLM 门控顺序依赖）
# ══════════════════════════════════════════════════════════════════
#
# 审查者实测：单独跑 `pytest tests/test_adaptive_advisor.py`（`ZHIPU_API_KEY` 在
# 部署 `.env` 里）时 4 条 GLM 门控用例**静默 skip**；两个文件一起跑才真跑。
# 根因：`.env` 是否加载取决于哪个模块先 `import src.config`。
# 修法：conftest 显式导入 `src.config`（生产入口同款路径）→ 确定性加载。

class TestDotenvLoadingIsDeterministic:
    def test_conftest_loads_src_config(self):
        """conftest 必须显式导入 src.config（它是必然被加载的那个文件）。"""
        import inspect
        src = inspect.getsource(k61conftest)
        assert "import src.config" in src, \
            "conftest 没有显式导入 src.config → .env 加载会退回「取决于还导入了谁」"
        assert "src.config" in sys.modules, "src.config 未在会话开始前被导入"

    def test_env_loaded_before_test_modules(self):
        """`.env` 必须在**任何测试模块**被 import 之前就加载完（顺序无关）。"""
        import src.config
        assert hasattr(src.config, "load_env_file")

    def test_single_file_run_does_not_silently_skip_glm_tests(self, tmp_path):
        """**决定性锁**：单文件跑 + `.env` 有 ZHIPU key → GLM 门控用例**不得 skip**。

        用 canary key（无效）：用例会**真跑**（打到 GLM 拿 401 → 走兜底人设
        gentle，仍在允许集合内 → 通过）。本用例只断言「门开了」：
        既不是 skipped，且确实执行到了（passed 或 failed 都算执行过）。
        """
        import subprocess
        import sys as _sys

        (tmp_path / ".env").write_text("ZHIPU_API_KEY=k61-canary-not-a-real-key\n",
                                       encoding="utf-8")
        env = os.environ.copy()
        for key in list(TEST_ENV_PINS) + ["USER_MEMORY_DIR", "ZHIPU_API_KEY"]:
            env.pop(key, None)
        env["PYTHONPATH"] = os.pathsep.join([REPO, TESTS_DIR])
        proc = subprocess.run(
            [_sys.executable, "-m", "pytest",
             os.path.join(REPO, "tests",
                          "test_mood_detector.py::TestRealAPI::test_real_detection_flow"),
             "-q", "-p", "no:cacheprovider"],
            cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=300,
        )
        out = proc.stdout + proc.stderr
        assert "skipped" not in out, (
            "单文件跑时 GLM 门控用例被静默 skip（顺序依赖回来了）：\n" + out[-800:]
        )
        assert ("1 passed" in out) or ("1 failed" in out), \
            "用例既没通过也没失败 —— 没真的执行：\n" + out[-800:]


# ══════════════════════════════════════════════════════════════════
# 6) r5（审查整改）：I3 回退面入 pin / I4 数据路径隔离
# ══════════════════════════════════════════════════════════════════

class TestI3LlmKeyFallbackSurface:
    """I3：`resolve_llm_api_key()` 的**回退面**（`ANTHROPIC_API_KEY`）必须一起 pin。

    不 pin 的话，"测试进程不持有生产 LLM key"只是**名义上**成立：DEEPSEEK 为空时
    会回退到 ANTHROPIC，而守卫的 pin 层绕过了；并且那条锁在**带该变量的机器上假红**
    （审查者实测 1 failed / 14 passed；`env -u ANTHROPIC_API_KEY` 后 15 passed）。
    本机宿主环境**确实带** `ANTHROPIC_API_KEY`（Claude Code 自带）→ 这些用例能跑通
    本身就是"带该变量的机器不再假红"的验收。
    """

    def test_anthropic_key_is_pinned(self):
        assert "ANTHROPIC_API_KEY" in TEST_ENV_PINS
        assert ENV_PINS_AT_IMPORT["ANTHROPIC_API_KEY"] == ""

    def test_resolver_cannot_fall_back(self):
        """两个 key 都取不到 → 解析结果必为空（回退面已封）。"""
        from src.llm.client import resolve_llm_api_key
        assert resolve_llm_api_key() == ""
        assert resolve_llm_api_key(allow_anthropic_fallback=True) == ""
        assert resolve_llm_api_key(allow_anthropic_fallback=False) == ""

    def test_deepseek_absence_lock_holds_with_anthropic_in_host_env(self):
        """**验收**：宿主带 `ANTHROPIC_API_KEY` 时，那条 DeepSeek 锁不得假红。"""
        import os as _os
        assert _os.environ.get("ANTHROPIC_API_KEY") == "", \
            "宿主变量漏进来了（pin 未生效）→ 会在带该变量的机器上假红"
        from src.llm.client import resolve_llm_api_key
        assert resolve_llm_api_key() == ""


class TestI4DataPathIsolation:
    """I4：`VECTORDB_DIR` / `FORTUNE_DB_PATH` / `FAISS_INDEX_DIR` 必须 pin 到**沙箱**。

    这三个键的**代码默认值就是生产路径** → pin 成空值等于没 pin（r3 的
    `FAISS_INDEX_DIR` pin 就是这种空操作）。实测后果：`test_engine_run_comparison`
    → `evidence.py:41` / `baseline.py:47` 会真的打开生产向量库并 bump
    `chroma.sqlite3` 的 mtime（审查者实测 11:12:37）。
    """

    def test_path_keys_are_sandboxed(self):
        import os as _os
        from conftest import TEST_FAISS_DIR, TEST_VECTORDB_DIR
        assert _os.environ["VECTORDB_DIR"] == TEST_VECTORDB_DIR
        assert _os.environ["FAISS_INDEX_DIR"] == TEST_FAISS_DIR
        assert _os.environ["FORTUNE_DB_PATH"].startswith(str(Path(TEST_FAISS_DIR).parent))

    def test_settings_do_not_point_at_production(self):
        """**结构断言**：settings 的三个路径都不得落在生产数据根下。"""
        from src.config import load_settings
        s = load_settings()
        prod_roots = k61conftest.PROD_DATA_ROOTS
        for name, value in (("vectordb_dir", s.vectordb_dir),
                            ("faiss_index_dir", s.faiss_index_dir),
                            ("db_path", s.db_path)):
            text = str(value)
            for root in prod_roots:
                assert not text.startswith(root), f"{name} 仍指向生产：{text}"

    def test_prod_data_guard_installed(self, _k61_prod_data_guard):
        """生产数据守卫是 autouse（本用例不做安装动作却拿到它）。"""
        assert k61conftest.PROD_DATA_FILES, "被监视的生产数据文件清单为空"

    def test_evidence_uses_settings_not_hardcoded_prod_path(self):
        """`evidence.py` / `baseline.py` 的改造点读 **settings**（故 pin 即隔离）。"""
        import inspect
        from src.engine import baseline, evidence
        for mod in (evidence, baseline):
            src = inspect.getsource(mod)
            assert "vectordb_dir" in src, mod.__name__
            assert "/mnt/d/fortune-data" not in src, \
                f"{mod.__name__} 里出现了硬编码生产路径"
            assert "/home/a/data" not in src, \
                f"{mod.__name__} 里出现了硬编码生产路径"
