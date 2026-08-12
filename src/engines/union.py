"""合盘聚合引擎：双引擎归一打分 + 特征提取 + 等级 + 脱敏缘笺数据。

评分（双引擎真实排盘打分，禁止伪随机/假结果）：
    合婚引擎（五行互补40 + 生肖25 + 日柱35，0-100）× 0.6
  + 合盘引擎（日主生克/夫妻宫六合/天乙贵人/五行互补/纳音，0-100）× 0.4
等级：≥90 天作之合 / ≥80 情投意合 / ≥70 相得益彰 / ≥60 和而不同 / <60 细水长流。

隐私红线：本模块产出的缘笺数据一律脱敏——只含 日柱+年月日+生肖，
不含时辰/出生地/姓名；双色印章仅用 地支+生肖，不涉日期。
"""
import logging

from src.engines.hehun import WUXING_KE

logger = logging.getLogger(__name__)

# 与 src/api/love.py SCORE_LEVELS 保持一致（等级称谓复用）
SCORE_LEVELS = [
    {"min": 90, "label": "天作之合", "sublabel": "Perfect Match"},
    {"min": 80, "label": "情投意合", "sublabel": "Soul Connection"},
    {"min": 70, "label": "相得益彰", "sublabel": "Complementary"},
    {"min": 60, "label": "和而不同", "sublabel": "Harmony in Diversity"},
    {"min": 0, "label": "细水长流", "sublabel": "Gentle Flow"},
]

_HEHUN_W = 0.6   # 合婚引擎权重
_COMPAT_W = 0.4  # 合盘引擎权重


def normalize_score(hehun_score: int, compat_score: int) -> int:
    """双引擎归一 0-100（越界输入收敛）。"""
    return max(0, min(100, round(_HEHUN_W * int(hehun_score) + _COMPAT_W * int(compat_score))))


def get_level(score: int) -> dict:
    for level in SCORE_LEVELS:
        if score >= level["min"]:
            return level
    return SCORE_LEVELS[-1]


def _nayin_wuxing(bazi) -> str:
    """日柱纳音的五行（"天上火"→"火"）；无纳音返回空串。"""
    nayin = getattr(bazi, "nayin", []) or []
    if len(nayin) < 3 or not nayin[2]:
        return ""
    return str(nayin[2])[-1]


def extract_features(hehun_result, compat_match, bazi1, bazi2) -> list:
    """特征词：生肖关系/日支关系/日干关系/日主五行关系/夫妻宫六合/双天乙贵人/五行互补/纳音相克。"""
    features = []
    sd = hehun_result.shengxiao_detail
    rel = (sd or {}).get("relation", "")
    if rel:
        features.append(rel)
    rd = hehun_result.rizhu_detail
    zr = (rd or {}).get("ri_zhi_relation", "")
    if zr:
        features.append(zr)
    gr = (rd or {}).get("ri_gan_relation", "")
    if gr:
        features.append(f"日干{gr}")
    dm = (hehun_result.bazi_match or {}).get("day_master_relation", "")
    if dm:
        features.append(dm)
    if compat_match.get("combo_bonus", 0) > 0:
        features.append("夫妻宫六合")
    if compat_match.get("shensha_bonus", 0) > 0:
        features.append("双天乙贵人")
    if compat_match.get("complement_bonus", 0) > 0:
        features.append("五行互补")
    n1, n2 = _nayin_wuxing(bazi1), _nayin_wuxing(bazi2)
    if n1 and n2 and (WUXING_KE.get(n1) == n2 or WUXING_KE.get(n2) == n1):
        features.append("纳音相克")
    return list(dict.fromkeys(features))


def desensitize_birth(year, month, day, day_ganzhi: str, shengxiao: str) -> str:
    """脱敏显示串：'日柱 · YYYY年M月D日 属X'（无时辰/出生地/姓名）。"""
    return f"{day_ganzhi} · {year}年{month}月{day}日 属{shengxiao}"


def build_yuan_card(score: int, level_label: str, quote: dict,
                    birth_a: str, birth_b: str, seal_char: str) -> dict:
    """缘笺数据（全部脱敏）：双人生辰显示串/契合分/等级/缘语/双色印章字。"""
    return {
        "birthA": birth_a, "birthB": birth_b,
        "score": score, "levelLabel": level_label,
        "quote": (quote or {}).get("full", ""), "quoteParts": quote or {},
        "sealChar": seal_char, "disclaimer": "签文只作心意，不作断言",
    }


def run_union(hehun_result, compat_match, bazi1, bazi2, person_a, person_b,
              relation: str = "", quote: dict = None) -> dict:
    """聚合：评分/等级/三维得分条/特征/缘笺数据/报告素材明细。

    person_a/person_b：含 year/month/day 的入参对象（仅取年月日做脱敏显示）。
    """
    score = normalize_score(hehun_result.score, compat_match.get("final_score", 0))
    level = get_level(score)
    sd = hehun_result.shengxiao_detail
    rd = hehun_result.rizhu_detail

    dimensions = {
        "wuxing": {"score": int(hehun_result.wuxing_score), "max": 40},
        "shengxiao": {"score": int(hehun_result.shengxiao_score), "max": 25,
                      "relation": (sd or {}).get("relation", "")},
        "rizhu": {"score": int(hehun_result.rizhu_score), "max": 35,
                  "relation": (rd or {}).get("ri_zhi_relation", "")},
    }
    features = extract_features(hehun_result, compat_match, bazi1, bazi2)

    birth_a = desensitize_birth(person_a.year, person_a.month, person_a.day,
                                "".join(bazi1.bazi[2]), (sd or {}).get("shengxiao1", ""))
    birth_b = desensitize_birth(person_b.year, person_b.month, person_b.day,
                                "".join(bazi2.bazi[2]), (sd or {}).get("shengxiao2", ""))
    year_zhi_b = bazi2.bazi[0][1] if bazi2.bazi and len(bazi2.bazi[0]) > 1 else ""
    seal_char = f"{year_zhi_b}{(sd or {}).get('shengxiao2', '')}" if year_zhi_b else "缘"

    return {
        "score": score, "levelLabel": level["label"], "levelSublabel": level["sublabel"],
        "dimensions": dimensions, "features": features, "relation": relation,
        "yuan_card": build_yuan_card(score, level["label"], quote or {},
                                     birth_a, birth_b, seal_char),
        # 报告素材（付费档第四章使用）：双引擎明细，仅内存传递
        "raw": {
            "hehun_wuxing": hehun_result.bazi_match,
            "hehun_shengxiao": hehun_result.shengxiao_detail,
            "hehun_rizhu": hehun_result.rizhu_detail,
            "compat": {k: compat_match.get(k) for k in
                       ("base_score", "wuxing_relation", "relation_desc",
                        "complement_bonus", "shensha_bonus", "combo_bonus", "final_score")},
        },
        "transient": True,
    }
