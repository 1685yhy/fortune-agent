"""k39-S2 · T015：起运实岁串**上**排盘卡（分支 k39-product）。

背景：起运实岁串（qiyun_sui_desc，"X岁X个月起运"）已由引擎算准（G5 对齐问真
99.6%），但排盘结果卡 `format_compact_card` 未展示 → 评测任务 T015 的
judge_hint（问真锚点李明 1990-01-01 23:40 男 北京 →「8岁6个月起运」）在卡面上
无从落地。本批把引擎既有字段纯确定性渲染上卡：0 LLM、不新造文案、不动起运算法。

覆盖：
① 该发生的发生：G5 锚点（李明）卡片含「8岁6个月起运」，且含「起运」字样。
② 不该发生的不发生：qiyun_sui_desc 为空时卡片无任何起运行（优雅降级）。
③ 回归护栏：既有 📅 大运 行格式不变、⭐ 神煞 行仍可定位、四柱表不受影响。

运行：cd /home/a/k39-wt && OMP_NUM_THREADS=1 python3 -m pytest \
  tests/test_k39_qiyun_card.py -x -q
"""
import dataclasses
import re
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from src.engines.bazi import BaziEngine  # noqa: E402
from src.engines.bazi_formatter import format_compact_card  # noqa: E402


@pytest.fixture(scope="module")
def engine():
    return BaziEngine()


@pytest.fixture(scope="module")
def anchor_result(engine):
    """G5 问真锚点：1990-01-01 23:40 北京 男（李明）→ 8岁6个月起运。

    与 tests/test_bazi_g5_wenzhen_align.py ANCHORS 同口径：显式 solar_time=True。
    """
    return engine.calculate(1990, 1, 1, 23, 40, "北京", "男", solar_time=True)


# ═══════════════════════ ① 该发生的发生 ═══════════════════════

class TestQiyunOnCard:
    def test_anchor_card_contains_qiyun_sui_desc(self, anchor_result):
        """G5 锚点命盘：卡片含问真实岁串「8岁6个月起运」（T015 judge_hint 锚点）。"""
        assert anchor_result.qiyun_sui_desc == "8岁6个月起运"
        card = format_compact_card(anchor_result)
        assert "起运" in card
        assert "8岁6个月起运" in card

    def test_anchor_card_qiyun_line_wording_uses_engine_field(self, anchor_result):
        """不新造文案：起运行 = 既有字段原文（⏳ 起运：{qiyun_sui_desc}）。"""
        card = format_compact_card(anchor_result)
        qy_lines = [l for l in card.splitlines() if l.startswith("⏳")]
        assert len(qy_lines) == 1
        assert qy_lines[0] == f"⏳ 起运：{anchor_result.qiyun_sui_desc}"

    def test_qiyun_line_sits_after_dayun_before_shensha(self, anchor_result):
        """位置：紧贴 📅 大运 行之后、⭐ 神煞 行之前（阅读顺序：大运 → 起运 → 神煞）。"""
        card = format_compact_card(anchor_result)
        lines = card.splitlines()
        idx = {p: next(i for i, l in enumerate(lines) if l.startswith(p))
               for p in ("📅 大运", "⏳ 起运", "⭐ 神煞")}
        assert idx["📅 大运"] < idx["⏳ 起运"] < idx["⭐ 神煞"]

    def test_card_with_birth_info_also_has_qiyun(self, anchor_result):
        """带 birth_info（真实主链调用形态）同样上卡。"""
        card = format_compact_card(anchor_result, {
            "year": 1990, "month": 1, "day": 1, "hour": 23, "minute": 40,
            "city": "北京", "gender": "男",
        })
        assert "8岁6个月起运" in card


# ═══════════════════════ ② 不该发生的不发生 ═══════════════════════

class TestQiyunGracefulDegradation:
    def test_empty_qiyun_sui_desc_omits_line(self, anchor_result):
        """字段为空（老数据/降级路径）→ 卡片无 ⏳ 起运 行，不是空壳行。"""
        degraded = dataclasses.replace(anchor_result, qiyun_sui_desc="")
        assert degraded.qiyun_sui_desc == ""
        card = format_compact_card(degraded)
        assert not any(l.startswith("⏳") for l in card.splitlines())
        assert "起运" not in card

    def test_missing_attribute_omits_line(self, anchor_result):
        """对象缺 qiyun_sui_desc 属性（旧序列化对象）→ getattr 兜底，同样不崩不上行。

        这里用同字段集的最小替身，保留卡片其它行所需属性。
        """
        class _NoQiyunField:
            def __init__(self, r):
                for f in ("bazi", "day_master", "geju", "yongshen", "wuxing",
                          "dayun", "shensha"):
                    setattr(self, f, getattr(r, f))

        card = format_compact_card(_NoQiyunField(anchor_result))
        assert not any(l.startswith("⏳") for l in card.splitlines())
        assert "起运" not in card
        assert "📅 大运：" in card  # 其它行不受影响


# ═══════════════════════ ③ 回归护栏（既有行零破坏） ═══════════════════════

class TestExistingRowsRegression:
    def test_dayun_row_present_and_format_unchanged(self, anchor_result):
        """📅 大运 行仍在，格式不变：{岁}岁{干支} 以 → 连接（新增行不得挤动它）。"""
        card = format_compact_card(anchor_result)
        dy_line = next(l for l in card.splitlines() if l.startswith("📅 大运："))
        expected = "📅 大运：" + " → ".join(
            f"{a}岁{g}" for a, g in anchor_result.dayun[:4])
        assert dy_line == expected
        assert re.fullmatch(r"📅 大运：\d+岁\S+( → \d+岁\S+){0,3}", dy_line)

    def test_shensha_row_still_locatable(self, anchor_result):
        """⭐ 神煞 行按既有前缀可定位（起运行不得破坏该查找）。"""
        card = format_compact_card(anchor_result)
        ss_line = next(l for l in card.splitlines() if l.startswith("⭐ 神煞"))
        assert ss_line == f"⭐ 神煞：{'、'.join(str(s) for s in anchor_result.shensha)}"

    def test_four_pillars_table_and_tail_unchanged(self, anchor_result):
        """四柱表与卡片尾部提示行零变化（起运行只做加法）。"""
        card = format_compact_card(anchor_result)
        lines = card.splitlines()
        # 契约：紧凑卡按「天干/地支」两行横向排四柱（非逐柱"己巳"连写）
        gan_row = next(l for l in lines if l.startswith("天干"))
        zhi_row = next(l for l in lines if l.startswith("地支"))
        for p in anchor_result.bazi:
            assert p[0] in gan_row and p[1] in zhi_row
        assert "藏干" in card and "纳音" in card
        assert "💡 回复「详细排盘」查看完整命盘" in card
