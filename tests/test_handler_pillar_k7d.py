"""k7d: D2 四柱提取器对 polish 稿系统性误判 → 回退引擎稿 → 双稿 修复回归。

实证（2026-09-05 重放③ + 真实 API 实验，非推测）：
- polish 稿 P 结构把「大运：3岁戊辰，13岁丁卯，23岁丙寅，33岁乙丑，43岁甲子」
  摘要行放在【分析解读】完整四柱声明（己卯年、己巳月、乙丑日、辛巳时）之前；
- 旧 _pillar_claims_conflict 取「文本序前 4 个干支」比对存档 → 前 4 = 大运四连
  [戊辰,丁卯,丙寅,乙丑] ≠ 存档 [己卯,己巳,乙丑,辛巳] → 系统性误判冲突
  → D2 回退引擎稿 → 用户实时流（polish 稿）与收尾整卡（引擎稿）异文
  = 18:47 双稿形态（引擎稿开头即四柱、从不冲突，故回退目标恒为引擎稿）。

修复语义：回复全文存在「与存档同序同值的完整四柱四连组」（任意位置）
→ 声明正确，不冲突；断言 ≥4 个干支却无存档序四连 → 仍判冲突（D2 原价值）。
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JWT_SECRET_KEY"] = "test-secret-key-32-bytes-long!!"

import pytest  # noqa: E402

from src.bot.handler import MessageHandler  # noqa: E402

CHART_BAZI = ["己卯", "己巳", "乙丑", "辛巳"]  # 18:47 重放实证已存盘四柱

# k7d 实证 polish 稿 P 形态：大运摘要行（先）→ 解读干支 → 完整四柱声明（后）。
# 大运行文本序前 4 干支 = [戊辰,丁卯,丙寅,乙丑]（铁证一打点复现），
# 存档四柱四连 [己卯,己巳,乙丑,辛巳] 出现在【分析解读】中 —— 修复前必误判冲突。
POLISH_DRAFT = (
    "【命盘总览】日主乙木生于巳月，火旺木相，财星透干，食伤生财，"
    "格局清秀，中年财运会稳步走高，整体走势稳中有进。\n\n"
    "【大运走势】大运：3岁戊辰，13岁丁卯，23岁丙寅，33岁乙丑，43岁甲子。"
    "青年运丁卯木运助身，学业顺遂；中年丙寅火运生财，事业渐入佳境；"
    "33岁后乙丑运湿土培木，财库渐实，宜把握机遇。\n\n"
    "【分析解读】您生于己卯年、己巳月、乙丑日、辛巳时，日主乙木坐丑土财库，"
    "月令巳火伤官生财，时柱辛巳七杀透干制身，格局身弱财旺，喜水木印比帮扶。"
    "今年丙午流年，伤官生财有力，财星得地，秋天金气当令，好在流年有午火帮身，"
    "整体也是机遇与挑战并存的一年，逢水木流年运势更佳。"
)

# 引擎稿 E 形态：开头即完整四柱声明，大运干支在其后。
ENGINE_DRAFT = (
    "己卯 己巳 乙丑 辛巳，日主乙木，生于巳月。大运：3岁戊辰，13岁丁卯，"
    "23岁丙寅，33岁乙丑，43岁甲子。今年财运稳步上升，注意秋季波动。"
)


def _handler_with_chart(tmp_path, bazi=None):
    from src.storage.chart_dao import ChartDAO
    h = object.__new__(MessageHandler)
    h.chart_dao = ChartDAO(str(tmp_path / "c.db"))
    h.chart_dao.save_chart("u1", 1,
        {"year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
         "city": "北京", "gender": "男"},
        {"bazi": bazi or CHART_BAZI, "day_master": "乙木",
         "dayun": [["0", "甲申"]], "liunian": {"2026": "丙午"},
         "shensha": [], "geju": "伤官格", "yongshen": "水"})
    return h


# ─────────────────────────── k7d 误判复现（本批实证核心）───────────────────────────

def test_polish_draft_no_false_conflict(tmp_path):
    """polish 稿形态（大运摘要行先于完整四柱声明）→ 不判冲突（修复前必挂）。

    前置打点（照铁证一）：回复文本序前 4 干支 = 大运四连 [戊辰,丁卯,丙寅,乙丑]
    ≠ 存档 [己卯,己巳,乙丑,辛巳] → 旧实现「前 4 比对」必误判 True。
    """
    h = _handler_with_chart(tmp_path)
    found = h._GANZHI_RE.findall(POLISH_DRAFT)
    print(f"[k7d打点] 存档四柱={CHART_BAZI}")
    print(f"[k7d打点] polish稿 text序前4干支={found[:4]}  "
          f"（大运摘要四连；≠存档 → 旧实现必误判冲突=True）")
    print(f"[k7d打点] 全文干支数={len(found)}，含存档序四连组="
          f"{any(list(found[i:i+4]) == CHART_BAZI for i in range(len(found)-3))}")
    assert list(found[:4]) != CHART_BAZI  # 结构前提：前 4 确是大运四连（铁证一形态）
    assert h._pillar_claims_conflict(POLISH_DRAFT, CHART_BAZI) is False


def test_polish_draft_no_fallback_to_engine(tmp_path):
    """终审层回归：polish 稿形态回复不得再被回退成引擎稿（18:47 双稿直接形态）。"""
    h = _handler_with_chart(tmp_path)
    out, blocked = h._enforce_pillar_integrity(POLISH_DRAFT, ENGINE_DRAFT, "u1")
    assert out == POLISH_DRAFT       # 不回退（修复前会被换成 ENGINE_DRAFT）
    assert blocked is False          # 不误禁缓存


# ─────────────────────────── 真冲突仍拦（D2 原价值保留）───────────────────────────

def test_true_conflict_still_detected(tmp_path):
    """LLM 答了别家的四柱（≥4 干支、全文无存档序四连）→ 仍判冲突。"""
    h = _handler_with_chart(tmp_path)
    reply = "今年财运整体平稳…你的四柱：庚午 甲申 乙丑 丙子，日主乙木…"
    assert h._pillar_claims_conflict(reply, CHART_BAZI) is True
    # 终审层：真冲突 + 有引擎原稿 → 回退原稿（D2 防护不变）
    out, blocked = h._enforce_pillar_integrity(reply, ENGINE_DRAFT, "u1")
    assert out == ENGINE_DRAFT
    assert blocked is False


def test_true_conflict_extra_ganzhi_still_detected(tmp_path):
    """干支更多但依旧无存档序四连（解读段落干支乱序）→ 仍判冲突。"""
    h = _handler_with_chart(tmp_path)
    reply = ("命盘：丙午 甲午 壬戌 癸卯 大运：壬辰 辛卯 庚寅 己丑 戊子，"
             "今年财运起伏大，需谨慎…")
    assert h._pillar_claims_conflict(reply, CHART_BAZI) is True


# ─────────────────────────── 引擎稿形态（四柱在前）不误伤 ───────────────────────────

def test_engine_draft_shape_never_conflicts(tmp_path):
    """引擎稿开头即四柱 → 不冲突（与旧行为一致，回归不误伤）。"""
    h = _handler_with_chart(tmp_path)
    found = h._GANZHI_RE.findall(ENGINE_DRAFT)
    print(f"[k7d打点] 引擎稿 text序前4干支={found[:4]}（=存档 → 新旧实现均不冲突）")
    assert h._pillar_claims_conflict(ENGINE_DRAFT, CHART_BAZI) is False


# ─────────────────────────── 干支 <4 早退（非排盘类回复）───────────────────────────

def test_fewer_than_four_ganzhi_early_exit(tmp_path):
    """无干支 / 仅 2 个干支（非排盘类回复）→ 不判冲突。"""
    h = _handler_with_chart(tmp_path)
    assert h._pillar_claims_conflict("宜穿蓝色，今天适合出行", CHART_BAZI) is False
    assert h._pillar_claims_conflict(
        "今年丙午流年运势尚可，注意保持作息规律。", CHART_BAZI) is False
