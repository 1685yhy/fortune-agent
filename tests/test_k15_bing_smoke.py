# -*- coding: utf-8 -*-
"""k15 真 Bing 冒烟（env 开关门控，默认 skip；跑一次给真实证据，不天天跑）。

门控（同时满足才实跑，否则 skip）：
  1. RUN_BING_SMOKE=1（显式开启——冒烟有真实网络成本与结果波动）
  2. web_search_available()=True（cn.bing.com 可达；Bing 免费通道无 key，
     无需任何 API key；失败 = 网络不可达/被墙 → skip 并注明原因）
冒烟断言：search_web("易宝支付", limit=5) 返回结构化结果非空——每条含
title/url/text/site_name 四键、url 为 http(s) 且去重后 ≥1 条。对应
k11b 评测挂行 T104/T105（entity_qa_search 行）的联网前提验证：
T104「易宝支付」检索关键词真实可用性 + 来源尾注域名素材来自该结果集。

运行（一次冒烟取证）：cd /mnt/e/fae-k15 && \
  RUN_BING_SMOKE=1 OMP_NUM_THREADS=4 \
  /home/a/fortune-agent/.venv/bin/python -m pytest tests/test_k15_bing_smoke.py \
  -q -p no:cacheprovider -k smoke
"""
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import pytest  # noqa: E402


def _smoke_enabled() -> tuple:
    """返回 (是否实跑, 跳过原因)。零副作用探测：env 开关 + 可达性探测。"""
    if os.environ.get("RUN_BING_SMOKE") != "1":
        return False, "未设置 RUN_BING_SMOKE=1（冒烟 env 门控，默认 skip）"
    try:
        from src.rag import web_search as ws
    except Exception as e:  # noqa: BLE001
        return False, f"模块导入失败: {type(e).__name__}: {e}"
    try:
        if not ws.web_search_available(force=False):
            return False, "web_search_available()=False（cn.bing.com 不可达）"
    except Exception as e:  # noqa: BLE001
        return False, f"可达性探测异常: {type(e).__name__}: {e}"
    return True, ""


def test_real_bing_smoke_structured_results():
    """真 Bing 检索：返回结构化结果非空（T104 挂行关键词「易宝支付」）。"""
    run, why = _smoke_enabled()
    if not run:
        pytest.skip(why)
    from src.rag import web_search as ws
    results = ws.search_web("易宝支付", limit=5)
    assert isinstance(results, list) and results, \
        "search_web 返回空（Bing 无结果或通道异常）"
    seen = set()
    for r in results:
        assert isinstance(r, dict), r
        for k in ("title", "url", "text", "site_name"):
            assert k in r, (k, r)
        assert str(r["url"] or "").startswith("http"), r
        assert str(r["title"] or "").strip(), r
        seen.add(str(r["url"]))
    assert len(seen) >= 1
