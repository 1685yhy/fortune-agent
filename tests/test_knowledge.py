"""Tests for 命理知识解析引擎 + API（问真知识库，L2-2）。

数据基准：data/knowledge/7jJ1_knowledge.json（问真 H5 原档）+ shishen_knowledge.json
（由 shishen_knowledge.js 转换，与 7jJ1 ShiShenTips 逐字段一致）+ tiangan_dizhi.json
（天干《滴天髓》十干论、地支属相/藏干/节气，7jJ1 仅含名表）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.engines import knowledge  # noqa: E402
from src.main import app  # noqa: E402
from src.security.auth import AuthHandler, JWTHandler, set_auth_handler  # noqa: E402

# 各分类关键名称锚点（问真排盘点文字可查的知识条目）
KEY_HITS = [
    ("shishen", "正财"),
    ("zhangsheng", "长生"),
    ("nayin", "海中金"),      # 甲子纳音
    ("shensha", "天乙贵人"),
    ("tiangan", "甲"),
    ("dizhi", "子"),
]

# 各分类期望条数（问真知识库完整性锚点）
EXPECTED_COUNTS = {
    "shishen": 10,      # 十神：正财/偏财/正印/偏印/食神/伤官/比肩/劫财/正官/七杀
    "zhangsheng": 12,   # 十二长生：长生…养
    "nayin": 30,        # 三十纳音
    "shensha": 59,      # 神煞（问真独有 59 条）
    "tiangan": 10,      # 十天干
    "dizhi": 12,        # 十二地支
}


# ---------------------------------------------------------------- 引擎命中
@pytest.mark.parametrize("category,name", KEY_HITS)
def test_get_knowledge_hits(category, name):
    """各分类关键名称命中：返回 {name, tip(长文本)}。"""
    item = knowledge.get_knowledge(category, name)
    assert item is not None, f"{category}/{name} 未命中"
    assert item["name"] == name
    assert isinstance(item.get("tip"), str) and len(item["tip"]) >= 10, \
        f"{category}/{name} tip 为空或过短"


def test_get_knowledge_extra_fields():
    """十神带 gujue/shishen；十二长生带 shijue/chafa（tip 取 chafa，gujue 取歌诀）。"""
    ss = knowledge.get_knowledge("shishen", "正财")
    assert ss["gujue"] and "克" in ss["gujue"]      # 日主克者为财星
    assert ss["shishen"] and "生官杀" in ss["shishen"]

    zs = knowledge.get_knowledge("zhangsheng", "长生")
    assert zs["tip"] == zs["chafa"]                  # 正文=查法说明
    assert zs["gujue"] == zs["shijue"]               # 歌诀
    assert "渊海子平" in zs["shijue"] or "长生" in zs["shijue"]

    ny = knowledge.get_knowledge("nayin", "海中金")
    assert ny.get("book1") and "海中" in ny["book1"]
    assert ny.get("book2") and ny.get("book3")

    ss2 = knowledge.get_knowledge("shensha", "天乙贵人")
    assert "三命通会" in ss2["gujue"]                # 贵人歌诀出处


def test_get_knowledge_exact_match_only():
    """精确匹配：子串不命中（问真点文字是按完整名称查）。"""
    assert knowledge.get_knowledge("shishen", "正") is None
    assert knowledge.get_knowledge("shishen", "财") is None
    assert knowledge.get_knowledge("nayin", "海中") is None


def test_get_knowledge_miss_returns_none():
    """未命中/非法分类/空参数 → None。"""
    assert knowledge.get_knowledge("shishen", "不存在的十神") is None
    assert knowledge.get_knowledge("bad_category", "正财") is None
    assert knowledge.get_knowledge("", "正财") is None
    assert knowledge.get_knowledge("shishen", "") is None
    assert knowledge.get_knowledge("  ", "  ") is None


# ---------------------------------------------------------------- 清单
def test_all_categories_counts():
    """各类别名称清单：条数符合问真知识库完整性锚点，且与期望一致。"""
    cats = knowledge.all_categories()
    assert set(cats.keys()) == set(EXPECTED_COUNTS.keys())
    for cat, count in EXPECTED_COUNTS.items():
        assert len(cats[cat]) == count, f"{cat} 条数 {len(cats[cat])} != {count}"
    # 名称去重且有序
    for cat, names in cats.items():
        assert len(names) == len(set(names)), f"{cat} 存在重复名称"


def test_knowledge_completeness_no_empty_tip():
    """知识库完整性：每类所有条目经规范化后均有非空 tip。"""
    cats = knowledge.all_categories()
    for cat, names in cats.items():
        for name in names:
            item = knowledge.get_knowledge(cat, name)
            assert item is not None and item["name"] == name
            assert isinstance(item.get("tip"), str) and item["tip"].strip(), \
                f"{cat}/{name} tip 为空"


def test_shishen_js_converted_json_consistent():
    """shishen_knowledge.js 转换的 JSON 与 7jJ1 ShiShenTips 逐字段一致（数据固化校验）。"""
    import json
    from pathlib import Path
    kb = Path(__file__).resolve().parents[1] / "data" / "knowledge"
    wenzhen = json.loads((kb / "7jJ1_knowledge.json").read_text(encoding="utf-8"))
    js_file = json.loads((kb / "shishen_knowledge.json").read_text(encoding="utf-8"))
    by_name = {x["name"]: x for x in wenzhen["ShiShenTips"]}
    assert len(js_file) == len(by_name) == 10
    for it in js_file:
        assert by_name[it["name"]] == it, f"{it['name']} 与 7jJ1 不一致"


# ---------------------------------------------------------------- 排盘集成
def test_bazi_knowledge_index():
    """排盘结果 knowledge_index：六类可点文字清单，全部可在知识库命中。"""
    from src.engines.bazi import BaziEngine
    r = BaziEngine().calculate(1999, 5, 13, 11, 25, "北京", "男")
    idx = r.knowledge_index
    assert set(idx.keys()) == {"shishen", "zhangsheng", "nayin",
                               "shensha", "tiangan", "dizhi"}
    # 四柱天干/地支全覆盖（去重保序，与索引契约一致）
    assert sorted(idx["tiangan"]) == sorted(set(p[0] for p in r.bazi))
    assert sorted(idx["dizhi"]) == sorted(set(p[1] for p in r.bazi))
    assert len(idx["tiangan"]) == len(set(idx["tiangan"]))   # 无重复
    # 契约：每类索引名必须能在本类知识库命中（日主位省略——日主天干在 tiangan 类）
    assert "日主" not in idx["shishen"]
    for cat, names in idx.items():
        for name in names:
            assert knowledge.get_knowledge(cat, name) is not None, \
                f"索引 {cat}/{name} 在知识库未命中"


# ---------------------------------------------------------------- API
def _client():
    set_auth_handler(AuthHandler())
    return TestClient(app)


def _token(uid="u_knowledge_1"):
    return JWTHandler("test-secret-key-32-bytes-long!!").create_token(uid)


def test_api_knowledge_ok():
    """GET /api/knowledge?category=shishen&name=正财 → 200 {name,tip,...}。"""
    client = _client()
    headers = {"Authorization": f"Bearer {_token()}"}
    r = client.get("/api/knowledge", params={"category": "shishen", "name": "正财"},
                   headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "正财"
    assert len(body["tip"]) > 20
    assert "gujue" in body

    r2 = client.get("/api/knowledge",
                    params={"category": "zhangsheng", "name": "长生"},
                    headers=headers)
    assert r2.status_code == 200
    assert r2.json()["gujue"]  # 歌诀


def test_api_knowledge_401_unauthorized():
    """未登录 → 401（require_user 鉴权红线）。"""
    client = _client()
    assert client.get("/api/knowledge",
                      params={"category": "shishen", "name": "正财"}).status_code == 401
    assert client.get("/api/knowledge/categories").status_code == 401


def test_api_knowledge_404_miss():
    """命中分类但名称不存在 → 404。"""
    client = _client()
    headers = {"Authorization": f"Bearer {_token()}"}
    r = client.get("/api/knowledge",
                   params={"category": "shishen", "name": "不存在的十神"},
                   headers=headers)
    assert r.status_code == 404
    assert "未找到" in r.json()["detail"]


def test_api_knowledge_400_bad_category():
    """非法分类 → 400。"""
    client = _client()
    headers = {"Authorization": f"Bearer {_token()}"}
    r = client.get("/api/knowledge",
                   params={"category": "vip", "name": "黄金会员"},
                   headers=headers)
    assert r.status_code == 400


def test_api_knowledge_categories():
    """GET /api/knowledge/categories → 六类清单+条数。"""
    client = _client()
    headers = {"Authorization": f"Bearer {_token()}"}
    r = client.get("/api/knowledge/categories", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["categories"].keys()) == set(EXPECTED_COUNTS.keys())
    assert body["counts"] == EXPECTED_COUNTS
