# tests/test_engine_run_comparison.py
import json
import os

from src.engine.run_comparison import run_comparison


class FakeLLM:
    def analyze(self, chart_data, references, user_question,
                use_pro=False, extra_system_prompt=None, stream_cb=None):
        return type("AR", (), {"response": "综合解读", "tokens_used": 10, "model": "fake"})()


def test_run_comparison_fake_llm_all_cases():
    # 真实本地检索前置：指向古籍库 fortune_books_v2（bge-m3 已本地缓存、chroma 在本机）。
    # 强制离线——本机无外网，不带 HF_HUB_OFFLINE 时 import sentence_transformers 会卡联网；
    # 强制 CPU——本机 GPU 被并发进程占用，embedder 走 CUDA 会 OOM 崩溃（torch 在举证时才惰性导入，此处设置生效）。
    os.environ["EMBEDDING_COLLECTION"] = "fortune_books_v2"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    cases = json.load(open("src/engine/cases/comparison_cases.json", encoding="utf-8"))
    runs = run_comparison(cases, use_real_llm=False)
    assert len(runs) == 5
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
