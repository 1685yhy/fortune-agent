"""合婚引擎 (Marriage Matching) - 五行互补 + 生肖 + 日柱分析。

结合两人的八字命盘进行婚姻匹配度评估，包含五行互补、生肖相合、
日柱关系三项核心分析。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from src.engines.bazi import TIANGAN, DIZHI, WUXING_TG, WUXING_DZ, SHENG_XU, SHENG_XU_MAP


# ── 生肖六合、三合、六冲、六害 ──────────────────────────────────
# 生肖顺序: 鼠牛虎兔龙蛇马羊猴鸡狗猪 (0-11)
# 对应地支: 子丑寅卯辰巳午未申酉戌亥 (0-11)
SHENGXIAO_NAMES = ["鼠", "牛", "虎", "兔", "龙", "蛇", "马", "羊", "猴", "鸡", "狗", "猪"]

# 六合 (最佳配对) - 子丑合, 寅亥合, 卯戌合, 辰酉合, 巳申合, 午未合
LIUHE_PAIRS = {(0, 1), (2, 11), (3, 10), (4, 9), (5, 8), (6, 7)}
LIUHE_MAP: Dict[int, int] = {0: 1, 1: 0, 2: 11, 11: 2, 3: 10, 10: 3, 4: 9, 9: 4, 5: 8, 8: 5, 6: 7, 7: 6}

# 三合 (次佳配对)
# 申子辰 (猴鼠龙), 亥卯未 (猪兔羊), 寅午戌 (虎马狗), 巳酉丑 (蛇鸡牛)
SANHE_GROUPS = [{0, 4, 8}, {2, 6, 10}, {3, 7, 11}, {1, 5, 9}]

# 六冲 (最差配对)
# 子午冲, 丑未冲, 寅申冲, 卯酉冲, 辰戌冲, 巳亥冲
LIUCHONG_PAIRS = {(0, 6), (6, 0), (1, 7), (7, 1), (2, 8), (8, 2), (3, 9), (9, 3), (4, 10), (10, 4), (5, 11), (11, 5)}

# 六害
# 子未害, 丑午害, 寅巳害, 卯辰害, 申亥害, 酉戌害
LIUHAI_PAIRS = {(0, 7), (7, 0), (1, 6), (6, 1), (2, 5), (5, 2), (3, 4), (4, 3), (8, 11), (11, 8), (9, 10), (10, 9)}

# ── 日支（地支）关系 ────────────────────────────────────────────
# 地支六合
DIZHI_LIUHE: Dict[str, str] = {
    "子": "丑", "丑": "子",
    "寅": "亥", "亥": "寅",
    "卯": "戌", "戌": "卯",
    "辰": "酉", "酉": "辰",
    "巳": "申", "申": "巳",
    "午": "未", "未": "午",
}

# 地支三合
DIZHI_SANHE: Dict[str, List[str]] = {
    "申": ["子", "辰"], "子": ["申", "辰"], "辰": ["申", "子"],
    "亥": ["卯", "未"], "卯": ["亥", "未"], "未": ["亥", "卯"],
    "寅": ["午", "戌"], "午": ["寅", "戌"], "戌": ["寅", "午"],
    "巳": ["酉", "丑"], "酉": ["巳", "丑"], "丑": ["巳", "酉"],
}

# 地支六冲
DIZHI_LIUCHONG: Dict[str, str] = {
    "子": "午", "午": "子",
    "丑": "未", "未": "丑",
    "寅": "申", "申": "寅",
    "卯": "酉", "酉": "卯",
    "辰": "戌", "戌": "辰",
    "巳": "亥", "亥": "巳",
}

# 地支六害
DIZHI_LIUHAI: Dict[str, str] = {
    "子": "未", "未": "子",
    "丑": "午", "午": "丑",
    "寅": "巳", "巳": "寅",
    "卯": "辰", "辰": "卯",
    "申": "亥", "亥": "申",
    "酉": "戌", "戌": "酉",
}

# 地支相刑
DIZHI_XIANGXING: Dict[str, List[str]] = {
    "寅": ["巳", "申"], "巳": ["寅", "申"], "申": ["寅", "巳"],  # 无恩之刑
    "丑": ["未", "戌"], "未": ["丑", "戌"], "戌": ["丑", "未"],  # 持势之刑
    "子": ["卯"], "卯": ["子"],                                   # 无礼之刑
    "辰": ["辰"], "午": ["午"], "酉": ["酉"], "亥": ["亥"],       # 自刑
}

# ── 五行生克循环 ────────────────────────────────────────────────
WUXING_CYCLE = ["木", "火", "土", "金", "水"]
WUXING_SHENG = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
WUXING_KE = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}


@dataclass
class HehunResult:
    score: int                  # 0-100 综合评分
    bazi_match: dict            # 五行互补分析
    shengxiao: str              # 生肖相合/相冲描述
    shengxiao_detail: dict      # 生肖详细分析
    rizhu: str                  # 日柱关系描述
    rizhu_detail: dict          # 日柱详细分析
    wuxing_score: int = 0       # 五行互补得分
    shengxiao_score: int = 0    # 生肖得分
    rizhu_score: int = 0        # 日柱得分
    advice: str = ""            # 综合建议


class HehunEngine:
    """合婚引擎。

    通过比较两人八字命盘，从五行互补、生肖相合、日柱关系三个维度
    评估婚姻匹配度，给出综合评分和建议。
    """

    def match(self, bazi1: "BaziResult", bazi2: "BaziResult") -> HehunResult:
        """合婚匹配分析。

        Args:
            bazi1: 第一人八字结果
            bazi2: 第二人八字结果

        Returns:
            HehunResult 包含匹配评分和分析
        """
        # 1. 五行互补分析 (40分)
        wuxing_match = self._analyze_wuxing(bazi1, bazi2)
        wuxing_score = wuxing_match["score"]

        # 2. 生肖分析 (25分)
        shengxiao_match = self._analyze_shengxiao(bazi1, bazi2)
        shengxiao_score = shengxiao_match["score"]

        # 3. 日柱分析 (35分)
        rizhu_match = self._analyze_rizhu(bazi1, bazi2)
        rizhu_score = rizhu_match["score"]

        # 4. 综合评分
        total_score = wuxing_score + shengxiao_score + rizhu_score
        total_score = min(100, max(0, total_score))

        # 5. 综合建议
        advice = self._generate_advice(total_score, wuxing_match, shengxiao_match, rizhu_match)

        return HehunResult(
            score=total_score,
            bazi_match=wuxing_match,
            shengxiao=shengxiao_match["description"],
            shengxiao_detail=shengxiao_match,
            rizhu=rizhu_match["description"],
            rizhu_detail=rizhu_match,
            wuxing_score=wuxing_score,
            shengxiao_score=shengxiao_score,
            rizhu_score=rizhu_score,
            advice=advice,
        )

    def _analyze_wuxing(self, bazi1, bazi2) -> dict:
        """五行互补分析（满分40分）。

        分析两人八字五行分布，评估互补程度：
        - 各自缺少的五行是否互补
        - 五行分布是否和谐
        - 日主五行关系
        """
        w1 = bazi1.wuxing
        w2 = bazi2.wuxing

        # 找出各自的弱项（少于2的）
        weak1 = {k for k, v in w1.items() if v <= 1}
        weak2 = {k for k, v in w2.items() if v <= 1}

        # 互补检查：弱1在强2中 ≥ 2
        complement_count = 0
        complement_details = []
        for wx in weak1:
            if w2.get(wx, 0) >= 2:
                complement_count += 1
                complement_details.append(f"{wx}: 一人缺{'{'}补{'}'}，二人{wx}旺")
        for wx in weak2:
            if w1.get(wx, 0) >= 2 and wx not in weak1:
                complement_count += 1
                complement_details.append(f"{wx}: 二人缺{'{'}补{'}'}，一人{wx}旺")

        # 日主关系
        day_master1 = bazi1.day_master[-1]  # 五行
        day_master2 = bazi2.day_master[-1]

        if WUXING_SHENG.get(day_master1) == day_master2:
            dm_relation = "相生（吉）"  # 1生2
            dm_score = 8
        elif WUXING_SHENG.get(day_master2) == day_master1:
            dm_relation = "相生（吉）"  # 2生1
            dm_score = 8
        elif day_master1 == day_master2:
            dm_relation = "比和（中）"  # 相同
            dm_score = 5
        elif WUXING_KE.get(day_master1) == day_master2:
            dm_relation = "相克（凶）"  # 1克2
            dm_score = 2
        else:
            dm_relation = "相克（凶）"  # 2克1
            dm_score = 2

        # 总分计算
        # 互补: 每个补点 10 分，最多 30 分
        comp_score = min(complement_count * 10, 30)
        score = comp_score + dm_score

        # 强弱描述
        w1_desc = ", ".join(f"{k}:{v}" for k, v in sorted(w1.items()))
        w2_desc = ", ".join(f"{k}:{v}" for k, v in sorted(w2.items()))

        if complement_count >= 2:
            complement_desc = "五行互补性强，互相弥补不足"
        elif complement_count >= 1:
            complement_desc = "有五行互补，但可进一步加强"
        else:
            complement_desc = "五行互补性一般，需注意调和"

        return {
            "score": min(score, 40),
            "wuxing_1": w1_desc,
            "wuxing_2": w2_desc,
            "day_master_1": bazi1.day_master,
            "day_master_2": bazi2.day_master,
            "day_master_relation": dm_relation,
            "complement_count": complement_count,
            "complement_details": complement_details,
            "complement_desc": complement_desc,
            "score_breakdown": {
                "互补得分": comp_score,
                "日主关系得分": dm_score,
            },
        }

    def _analyze_shengxiao(self, bazi1, bazi2) -> dict:
        """生肖分析（满分25分）。

        分析两人生肖（年支）关系：
        - 六合/三合 = 最佳
        - 相合但非六合三合 = 中等
        - 六冲/六害/相刑 = 差
        """
        # 提取年支（生肖）
        year_zhi1 = bazi1.bazi[0][1] if len(bazi1.bazi[0]) > 1 else ""
        year_zhi2 = bazi2.bazi[0][1] if len(bazi2.bazi[0]) > 1 else ""

        if not year_zhi1 or not year_zhi2:
            return {
                "score": 15,
                "description": "无法确定生肖信息，取中等分",
                "zhi1": year_zhi1,
                "zhi2": year_zhi2,
                "shengxiao1": "",
                "shengxiao2": "",
                "relation": "未知",
            }

        idx1 = DIZHI.index(year_zhi1) if year_zhi1 in DIZHI else -1
        idx2 = DIZHI.index(year_zhi2) if year_zhi2 in DIZHI else -1

        shengxiao1 = SHENGXIAO_NAMES[idx1] if 0 <= idx1 < 12 else ""
        shengxiao2 = SHENGXIAO_NAMES[idx2] if 0 <= idx2 < 12 else ""

        # 检查关系
        if idx1 >= 0 and idx2 >= 0:
            pair = (idx1, idx2)
            if pair in LIUHE_PAIRS or pair in {(j, i) for i, j in LIUHE_PAIRS}:
                relation = "六合（上等婚配）"
                score_base = 25
            elif any(idx1 in g and idx2 in g for g in SANHE_GROUPS):
                relation = "三合（上等婚配）"
                score_base = 23
            elif pair in LIUCHONG_PAIRS:
                relation = "六冲（忌配）"
                score_base = 8
            elif pair in LIUHAI_PAIRS:
                relation = "六害（不利）"
                score_base = 12
            else:
                # 普通相合
                relation = "普通（中等）"
                score_base = 16
        else:
            relation = "未知"
            score_base = 15

        description = f"生肖{SHENGXIAO_NAMES[idx1] if 0 <= idx1 < 12 else '?'}与{SHENGXIAO_NAMES[idx2] if 0 <= idx2 < 12 else '?'}：{relation}"

        return {
            "score": score_base,
            "description": description,
            "zhi1": year_zhi1,
            "zhi2": year_zhi2,
            "shengxiao1": shengxiao1,
            "shengxiao2": shengxiao2,
            "relation": relation,
        }

    def _analyze_rizhu(self, bazi1, bazi2) -> dict:
        """日柱分析（满分35分）。

        分析两人日柱关系：
        - 日支六合/三合 = 上吉
        - 日支相生 = 中吉
        - 日支相同 = 平和
        - 日支六冲/六害/相刑 = 凶
        """
        if len(bazi1.bazi) < 3 or len(bazi2.bazi) < 3:
            return self._default_rizhu()

        rizhu1 = bazi1.bazi[2]
        rizhu2 = bazi2.bazi[2]

        ri_gan1 = rizhu1[0]
        ri_zhi1 = rizhu1[1]
        ri_gan2 = rizhu2[0]
        ri_zhi2 = rizhu2[1]

        # 日天干五行关系
        gan1_wx = WUXING_TG.get(ri_gan1, "")
        gan2_wx = WUXING_TG.get(ri_gan2, "")

        if gan1_wx and gan2_wx:
            if WUXING_SHENG.get(gan1_wx) == gan2_wx or WUXING_SHENG.get(gan2_wx) == gan1_wx:
                gan_relation = "相生"
                gan_score = 8
            elif gan1_wx == gan2_wx:
                gan_relation = "比和"
                gan_score = 5
            elif WUXING_KE.get(gan1_wx) == gan2_wx or WUXING_KE.get(gan2_wx) == gan1_wx:
                gan_relation = "相克"
                gan_score = 2
            else:
                gan_relation = "平和"
                gan_score = 4
        else:
            gan_relation = "未知"
            gan_score = 4

        # 日支关系
        if ri_zhi1 and ri_zhi2:
            if DIZHI_LIUHE.get(ri_zhi1) == ri_zhi2:
                zhi_relation = "六合（天赐良缘）"
                zhi_score = 20
            elif DIZHI_LIUCHONG.get(ri_zhi1) == ri_zhi2:
                zhi_relation = "六冲（婚配大忌）"
                zhi_score = 3
            elif DIZHI_LIUHAI.get(ri_zhi1) == ri_zhi2:
                zhi_relation = "六害（关系紧张）"
                zhi_score = 5
            elif ri_zhi1 in DIZHI_SANHE and ri_zhi2 in DIZHI_SANHE.get(ri_zhi1, []):
                zhi_relation = "三合（美满佳配）"
                zhi_score = 18
            elif ri_zhi1 in DIZHI_XIANGXING and ri_zhi2 in DIZHI_XIANGXING.get(ri_zhi1, []):
                zhi_relation = "相刑（易生矛盾）"
                zhi_score = 7
            elif ri_zhi1 == ri_zhi2:
                zhi_relation = "相同（性格相似）"
                zhi_score = 10
            else:
                zhi_relation = "平和（无特殊关系）"
                zhi_score = 12
        else:
            zhi_relation = "未知"
            zhi_score = 10

        score = min(gan_score + zhi_score, 35)

        # 日柱合婚短语
        if zhi_score >= 18:
            description = f"日支{zhi_relation}，日干{gan_relation}，天缘甚佳"
        elif zhi_score <= 5:
            description = f"日支{zhi_relation}，日干{gan_relation}，需多加磨合"
        else:
            description = f"日支{zhi_relation}，日干{gan_relation}"

        return {
            "score": score,
            "description": description,
            "rizhu_1": rizhu1,
            "rizhu_2": rizhu2,
            "ri_gan_1": ri_gan1,
            "ri_gan_2": ri_gan2,
            "ri_zhi_1": ri_zhi1,
            "ri_zhi_2": ri_zhi2,
            "ri_gan_relation": gan_relation,
            "ri_zhi_relation": zhi_relation,
            "rizhi_score": zhi_score,
            "rigan_score": gan_score,
        }

    def _default_rizhu(self) -> dict:
        """默认日柱分析。"""
        return {
            "score": 18,
            "description": "无法获取完整日柱信息",
            "rizhu_1": "",
            "rizhu_2": "",
            "ri_gan_1": "",
            "ri_gan_2": "",
            "ri_zhi_1": "",
            "ri_zhi_2": "",
            "ri_gan_relation": "未知",
            "ri_zhi_relation": "未知",
            "rizhi_score": 10,
            "rigan_score": 5,
        }

    def _generate_advice(self, total_score: int, wuxing: dict,
                         shengxiao: dict, rizhu: dict) -> str:
        """生成综合建议。"""
        parts = []

        if total_score >= 80:
            parts.append("天作之合！双方八字匹配度极高，五行互补，生肖和合，日柱相生，是天赐良缘。")
        elif total_score >= 65:
            parts.append("上等婚配。双方八字较为匹配，互补性良好，虽有少许冲突但可通过沟通化解。")
        elif total_score >= 50:
            parts.append("中等婚配。双方各有优劣，需要相互包容理解，在相处中培养默契。")
        elif total_score >= 35:
            parts.append("中等偏下。双方存在较多冲突点，建议充分了解对方后再做决定，多磨合。")
        else:
            parts.append("八字冲克较多，婚姻需谨慎。建议双方多沟通，理性分析后再做决定。")

        # 针对弱项的建议
        weaknesses = []
        if wuxing.get("score", 40) < 20:
            weaknesses.append("五行互补不足")
        if shengxiao.get("score", 25) < 15:
            weaknesses.append(f"生肖{shengxiao.get('relation','不睦')}")
        if rizhu.get("score", 35) < 18:
            weaknesses.append(f"日柱{rizhu.get('ri_zhi_relation','不睦')}")

        if weaknesses:
            parts.append(f"注意事项：{'；'.join(weaknesses)}。建议通过风水布局、命名调整等方式化解。")

        if total_score < 50:
            parts.append("建议咨询专业命理师做进一步详细分析。")

        return "\n".join(parts)
