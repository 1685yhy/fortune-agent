# tests/test_engine_run_comparison.py
import json
import os

from src.engine.run_comparison import run_comparison


class FakeLLM:
    def analyze(self, chart_data, references, user_question,
                use_pro=False, extra_system_prompt=None, stream_cb=None):
        return type("AR", (), {"response": "综合解读", "tokens_used": 10, "model": "fake"})()


def test_run_comparison_fake_llm_all_cases(tmp_path):
    # k61 r2（仓库卫生）：存档落到 tmp —— 此前 run_comparison 把输出路径硬编码在
    # 函数体内，本用例会直接覆盖仓库里**被 git 跟踪**的
    # `src/engine/out/comparison_runs.jsonl`（那个文件同时是
    # tests/test_engine_build_report.py 的输入夹具，被覆盖会连带改变它读到的内容）。
    # 断言对象一直是返回的 runs，与落盘位置无关。
    out_path = tmp_path / "comparison_runs.jsonl"
    # 真实本地检索前置：指向古籍库 fortune_books_v2（bge-m3 已本地缓存、chroma 在本机）。
    # 强制离线——本机无外网，不带 HF_HUB_OFFLINE 时 import sentence_transformers 会卡联网；
    # 强制 CPU——本机 GPU 被并发进程占用，embedder 走 CUDA 会 OOM 崩溃（torch 在举证时才惰性导入，此处设置生效）。
    os.environ["EMBEDDING_COLLECTION"] = "fortune_books_v2"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    cases = json.load(open("src/engine/cases/comparison_cases.json", encoding="utf-8"))
    runs = run_comparison(cases, use_real_llm=False, out_path=str(out_path))
    assert len(runs) == 5
    # r2 追加：存档确实落在注入路径（而不是仓库里的默认路径）
    assert out_path.exists() and out_path.stat().st_size > 0
    for r in runs:
        # pills-only 案例（tds_0001）无 birth，基线如实降级为 {}（见基线降级断言）；
        # 有出生信息的案例基线必须产出分析
        if r["baseline"]:
            assert r["baseline"]["analysis"]
        assert r["engine"]["analysis"]
        assert r["engine"]["chain_text"] and "第1步" in r["engine"]["chain_text"]
        assert "note" in r
    pills_run = [r for r in runs if r["id"] == "tds_0001"][0]
    assert pills_run["baseline"] == {}
    assert "不适用" in pills_run["baseline_note"]
    mingli_runs = [r for r in runs if r["id"] != "tds_0001"]
    assert len(mingli_runs) == 4
    assert all(r["baseline_note"] == "OK" for r in mingli_runs)
