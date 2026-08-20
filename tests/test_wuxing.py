"""五行能量引擎测试（L2-3）：统计 / 月令旺衰 / 十二长生 / 日主强弱 / BaziResult 集成。

锚点：闫海洋盘 1999-05-13 11:25 北京 男 → 己卯 己巳 乙丑 壬午。
  - 五行统计（天干 + 地支本气）：土3 木2 火2 水1 金0
  - 巳月旺衰：木'休'、火'旺'、土'相'、金'死'、水'囚'
  - 乙日长生：年支卯=临官、月支巳=沐浴、日支丑=衰、时支午=长生
  - 日主强弱（得令0 + 得地2 + 得势-2 = 0）→ 偏弱

回归：changsheng_state 与问真校准过的 bazi_formatter.get_changsheng 全量交叉验证
（10 干 × 12 支）；tests/test_bazi_qz_full.py 200 案例另行跑（星运/自坐即此表口径）。
"""
import json
import os

import pytest

from src.engines.wuxing import (wuxing_counts, month_wangshuai,
                                changsheng_state, day_master_strength)
from src.engines.bazi import BaziEngine
from src.engines.bazi_formatter import CHANG_SHENG, CS_NAMES, get_changsheng

DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "wuxing_tables.json")
ENGINE = BaziEngine()

# 锚点：闫海洋盘
ANCHOR_BAZI = ["己卯", "己巳", "乙丑", "壬午"]
ANCHOR_BIRTH = (1999, 5, 13, 11, 25, "北京", "男")


# ───────────────────────── 1. 表完整性（data 资产） ─────────────────────────

def _load_raw():
    with open(DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_table_completeness_changsheng():
    """changsheng：行0 = 12 状态名；行1~10 = 甲乙丙丁戊己庚辛壬癸（天干序）各 12 支。"""
    raw = _load_raw()["changsheng"]
    assert len(raw) == 11, "1 行状态名 + 10 行天干序列"
    assert raw[0] == CS_NAMES, "状态名行与 bazi_formatter 一致"
    assert all(len(row) == 12 for row in raw[1:])
    # 行序 = 天干序 甲乙丙丁戊己庚辛壬癸，且与问真校准的 formatter 表逐行一致
    stems = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
    for i, stem in enumerate(stems):
        assert raw[i + 1] == CHANG_SHENG[stem], "行%d 应为 %s 的长生序列" % (i + 1, stem)


def test_table_completeness_wangshuai():
    """wangshuai：行0 = 列头 [金木水火土]；行1~5 = 金/木/水/火/土月（行序即月令五行序）各 5 列。"""
    raw = _load_raw()["wuxing_wangshuai"]
    assert len(raw) == 6, "1 行列头 + 5 行月令"
    assert raw[0] == ["金", "木", "水", "火", "土"]
    assert all(len(row) == 5 for row in raw[1:])
    states = {"旺", "相", "休", "囚", "死"}
    for row in raw[1:]:
        assert set(row) == states and len(set(row)) == 5, "每行恰含 旺相休囚死 各一"
    # 标准旺衰值（月令五行 → 目标五行 [金木水火土]）
    assert raw[1] == ["旺", "死", "相", "囚", "休"], "金月：金旺木死水相火囚土休"
    assert raw[2] == ["囚", "旺", "休", "相", "死"], "木月：金囚木旺水休火相土死"
    assert raw[3] == ["休", "相", "旺", "死", "囚"], "水月：金休木相水旺火死土囚"
    assert raw[4] == ["死", "休", "囚", "旺", "相"], "火月：金死木休水囚火旺土相"
    assert raw[5] == ["相", "囚", "死", "休", "旺"], "土月：金相木囚水死火休土旺"


# ───────────────────────── 2. 十二长生 ─────────────────────────

def test_changsheng_align_formatter():
    """与问真校准的 formatter（200 案例星运/自坐口径）全量交叉验证：10 干 × 12 支。"""
    branches = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
    for gan in "甲乙丙丁戊己庚辛壬癸":
        for zhi in branches:
            assert changsheng_state(gan, zhi) == get_changsheng(gan, zhi), (gan, zhi)


def test_changsheng_anchor():
    """锚点：乙日四支长生状态（乙长生在午逆行：午巳辰卯寅丑子亥戌酉申未）。"""
    assert changsheng_state("乙", "丑") == "衰"
    assert changsheng_state("乙", "卯") == "临官"
    assert changsheng_state("乙", "巳") == "沐浴"
    assert changsheng_state("乙", "午") == "长生"
    # 标准点：甲亥=长生（甲长生在亥，顺行）、甲子=沐浴、甲午=死、
    # 庚巳=长生、癸卯=长生（癸长生在卯，逆行）、丙寅=长生
    assert changsheng_state("甲", "亥") == "长生"
    assert changsheng_state("甲", "子") == "沐浴"
    assert changsheng_state("甲", "午") == "死"
    assert changsheng_state("庚", "巳") == "长生"
    assert changsheng_state("癸", "卯") == "长生"
    assert changsheng_state("丙", "寅") == "长生"
    assert changsheng_state("丁", "酉") == "长生"  # 阴火同阴土，长生在酉


def test_changsheng_unknown():
    """未知天干/地支 → "?"（与 get_changsheng 行为一致，不抛异常）。"""
    assert changsheng_state("?", "子") == "?"
    assert changsheng_state("甲", "?" ) == "?"


# ───────────────────────── 3. 月令旺衰 ─────────────────────────

def test_wangshuai_anchor():
    """锚点：巳月（火月）各五行旺衰：火旺 土相 木休 金死 水囚。"""
    assert month_wangshuai("巳", "木") == "休"
    assert month_wangshuai("巳", "火") == "旺"
    assert month_wangshuai("巳", "土") == "相"
    assert month_wangshuai("巳", "金") == "死"
    assert month_wangshuai("巳", "水") == "囚"


def test_wangshuai_standard():
    """四时旺衰标准值（子平常规）：木月木旺、金月金旺、水月水旺、土月土旺；土月丑亦旺。"""
    assert month_wangshuai("寅", "木") == "旺"   # 木月
    assert month_wangshuai("寅", "火") == "相"
    assert month_wangshuai("寅", "土") == "死"
    assert month_wangshuai("酉", "金") == "旺"   # 金月
    assert month_wangshuai("酉", "火") == "囚"
    assert month_wangshuai("子", "水") == "旺"   # 水月
    assert month_wangshuai("辰", "土") == "旺"   # 土月
    assert month_wangshuai("丑", "土") == "旺"   # 丑月亦土月
    assert month_wangshuai("午", "火") == "旺"   # 火月


def test_wangshuai_invalid():
    """非法输入抛 ValueError（编程错误显式暴露）。"""
    with pytest.raises(ValueError):
        month_wangshuai("X", "木")
    with pytest.raises(ValueError):
        month_wangshuai("巳", "石")


# ───────────────────────── 4. 五行统计 ─────────────────────────

def test_counts_anchor():
    """锚点：己卯 己巳 乙丑 壬午 → 土3 木2 火2 水1 金0。
    （天干 己土 己土 乙木 壬水 + 地支本气 卯木 巳火 丑土 午火）"""
    assert wuxing_counts(ANCHOR_BAZI) == {"金": 0, "木": 2, "水": 1, "火": 2, "土": 3}
    assert sum(wuxing_counts(ANCHOR_BAZI).values()) == 8


def test_counts_missing_zero():
    """五行缺失 = 0（五键恒在）：全木火局无金水土。"""
    counts = wuxing_counts(["甲寅", "丙寅", "甲午", "丙寅"])
    assert counts == {"金": 0, "木": 5, "水": 0, "火": 3, "土": 0}
    assert counts["金"] == 0


def test_counts_empty():
    assert wuxing_counts([]) == {"金": 0, "木": 0, "水": 0, "火": 0, "土": 0}


# ───────────────────────── 5. 日主强弱 ─────────────────────────

def test_strength_anchor():
    """锚点：乙木日主 己卯己巳乙丑壬午 → 偏弱。
    规则核算：得令（巳月木'休'）=0；得地（卯临官+午长生）=2；
    得势 扶=木2+水1×0.5=2.5 vs 克泄耗=金0+火2+土3=5，差-2.5→-2；
    总分 0+2-2=0 ∈ [-1.5,1.5) → 偏弱。"""
    counts = wuxing_counts(ANCHOR_BAZI)
    assert day_master_strength(ANCHOR_BAZI, counts) == "偏弱"


def test_strength_strong_weak():
    """极端校准：身强盘 → 旺；身弱盘 → 弱。"""
    strong = ["甲寅", "丙寅", "甲子", "甲子"]
    # 得令（寅月木旺）+2；得地（寅临官×2）+2；得势 扶=木5+水2×0.5=6 vs 克泄耗=金0+火1+土0=1 → +2
    assert day_master_strength(strong, wuxing_counts(strong)) == "旺"
    weak = ["癸巳", "丁巳", "癸丑", "丙辰"]
    # 得令（巳月水'囚'）-1；得地（丑冠带）+1；得势 扶=水1 vs 克泄耗=土2+火4=6 → -2
    assert day_master_strength(weak, wuxing_counts(weak)) == "弱"


def test_strength_invalid():
    with pytest.raises(ValueError):
        day_master_strength(["己卯"], {})


# ───────────────────────── 6. BaziResult 集成 ─────────────────────────

def test_engine_integration_anchor():
    """整盘集成：排盘 → wuxing_energy 五字段齐全且与手算一致。"""
    r = ENGINE.calculate(*ANCHOR_BIRTH)
    assert r.bazi == ANCHOR_BAZI
    we = r.wuxing_energy
    assert we["counts"] == {"金": 0, "木": 2, "水": 1, "火": 2, "土": 3}
    assert we["counts"] == r.wuxing  # 与既有 wuxing 字段同口径
    assert we["wangshuai"] == {"金": "死", "木": "休", "水": "囚", "火": "旺", "土": "相"}
    assert we["changsheng"] == {"年": "临官", "月": "沐浴", "日": "衰", "时": "长生"}
    assert we["strength"] == "偏弱"
    assert we["yongshen"] == r.yongshen
    assert "用神" in we["yongshen"]  # 沿用现有 _calc_yongshen 判定


def test_engine_integration_other():
    """另一盘抽查：辛丑 庚子 壬寅 辛亥（壬日主，子月水旺；壬在亥=临官）。"""
    r = ENGINE.calculate(1962, 1, 4, 21, 50, "", "男")  # 无城市不修正
    assert r.bazi == ["辛丑", "庚子", "壬寅", "辛亥"]
    we = r.wuxing_energy
    assert we["changsheng"] == {"年": "衰", "月": "帝旺", "日": "病", "时": "临官"}
    # 壬长生在申、临官在亥、帝旺在子——按表：壬 申酉戌亥子丑… 申=长生 亥=临官 子=帝旺
    assert changsheng_state("壬", "子") == "帝旺"
    assert changsheng_state("壬", "亥") == "临官"
    assert we["wangshuai"]["水"] == "旺"     # 子月水旺
    assert we["wangshuai"]["金"] == "休"     # 子月金休
    assert we["counts"] == r.wuxing


# ───────────────────────── 7. 回退路径（L2-4 顺带） ─────────────────────────

class _FakeOS:
    """把 _load_tables 的表文件路径指到不存在文件。

    只替换 wuxing 模块的 _os 引用（该模块仅 _load_tables 用 _os），
    不碰全局 os.path，避免影响 pytest 自身机制。
    """
    class path:
        @staticmethod
        def abspath(p):
            return "/nonexistent_fortune_dir/wuxing.py"

        @staticmethod
        def dirname(p):
            return "/nonexistent_fortune_dir"

        @staticmethod
        def join(*parts):
            return "/nonexistent_fortune_dir/wuxing_tables.json"


def test_fallback_embedded_tables_equal_json(monkeypatch):
    """回退契约（L2-4 审查遗留 I1）：表文件缺失 → 回退内嵌表，且与 JSON 全等。"""
    from src.engines import wuxing as wx_mod
    monkeypatch.setattr(wx_mod, "_os", _FakeOS)
    monkeypatch.setattr(wx_mod, "_TABLES", None)  # 清缓存强制走加载路径
    names, cs_by_gan, ws_by_month = wx_mod._load_tables()
    raw = _load_raw()
    # 回退表与 data/wuxing_tables.json 全等
    assert names == raw["changsheng"][0]
    assert cs_by_gan == {g: raw["changsheng"][i + 1]
                         for i, g in enumerate("甲乙丙丁戊己庚辛壬癸")}
    assert ws_by_month == {w: raw["wuxing_wangshuai"][i + 1]
                           for i, w in enumerate(["金", "木", "水", "火", "土"])}
    # 与内嵌表逐项全等（回退即内嵌表本身）
    assert cs_by_gan == wx_mod._CHANGSHENG_BY_GAN
    assert ws_by_month == wx_mod._WANGSHUAI_BY_MONTH
    # 回退后引擎照常工作（不抛异常、口径不变）
    assert wx_mod.changsheng_state("乙", "卯") == "临官"
    assert wx_mod.month_wangshuai("巳", "火") == "旺"
