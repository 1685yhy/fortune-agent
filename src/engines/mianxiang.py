"""面相/手相引擎 - 五行面相分析 (Five Element Face Reading)."""
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ============================================================
# 基础数据
# ============================================================

# 五行脸型判定
FACE_TYPE_RULES = {
    "金形": {"keywords": ["方脸", "方形", "国字脸", "骨骼分明", "棱角"], "element": "金", "desc": "面方、骨骼分明、轮廓刚硬"},
    "木形": {"keywords": ["长脸", "瘦长", "狭长", "窄长", "长方形"], "element": "木", "desc": "面长、身材瘦高、清秀"},
    "水形": {"keywords": ["圆脸", "圆形", "丰满", "肉感", "圆润"], "element": "水", "desc": "面圆、丰满、皮肤润泽"},
    "火形": {"keywords": ["尖脸", "心形脸", "倒三角", "上宽下尖", "瓜子脸"], "element": "火", "desc": "上宽下尖、发际尖、下巴尖"},
    "土形": {"keywords": ["厚实", "敦厚", "方圆", "宽阔", "厚重", "方正", "国泰"], "element": "土", "desc": "面方厚、体敦实、鼻准丰隆"},
}

# 三停分析规则
THREE_ZONES_RULES = {
    "上停": {"range": "发际至眉毛", "domain": "早年运(1-30岁)", "keywords_good": ["饱满", "宽阔", "丰隆", "高", "宽", "圆润"],
             "keywords_bad": ["窄", "低", "凹陷", "短", "狭窄", "塌"],
             "default": "适中"},
    "中停": {"range": "眉毛至鼻尖", "domain": "中年运(31-50岁)", "keywords_good": ["端正", "挺直", "高挺", "匀称", "丰隆"],
             "keywords_bad": ["歪", "塌", "扁", "短", "露骨"],
             "default": "适中"},
    "下停": {"range": "鼻尖至下巴", "domain": "晚年运(51岁以后)", "keywords_good": ["饱满", "圆润", "丰厚", "方", "宽"],
             "keywords_bad": ["尖", "窄", "薄", "短", "后缩"],
             "default": "适中"},
}

# 五岳分析规则
FIVE_MOUNTAINS_RULES = {
    "南岳(额头)": {"position": "额头", "keyword_good": ["饱满", "高耸", "宽阔", "丰隆", "明润"],
                   "keyword_bad": ["窄", "低", "凹陷", "短", "暗淡"],
                   "default": "适中"},
    "北岳(下巴)": {"position": "下巴", "keyword_good": ["饱满", "圆润", "丰厚", "方", "朝拱"],
                   "keyword_bad": ["尖", "窄", "薄", "短", "后缩"],
                   "default": "适中"},
    "东岳(左颧)": {"position": "左颧", "keyword_good": ["高耸", "饱满", "有肉", "相拱"],
                   "keyword_bad": ["低", "塌", "无肉", "露骨", "横突"],
                   "default": "适中"},
    "西岳(右颧)": {"position": "右颧", "keyword_good": ["高耸", "饱满", "有肉", "相拱"],
                   "keyword_bad": ["低", "塌", "无肉", "露骨", "横突"],
                   "default": "适中"},
    "中岳(鼻子)": {"position": "鼻子", "keyword_good": ["端正", "高挺", "丰隆", "有肉", "挺直"],
                   "keyword_bad": ["歪", "塌", "扁", "露骨", "尖削"],
                   "default": "适中"},
}

# 五官分析规则
FIVE_FEATURES_RULES = {
    "眉(保寿官)": {"keywords": ["眉", "眉毛"], "good": "清秀弯长", "bad": "粗短杂乱", "default": "适中"},
    "眼(监察官)": {"keywords": ["眼", "眼睛", "目"], "good": "黑白分明有神", "bad": "昏浊无神", "default": "适中"},
    "耳(采听官)": {"keywords": ["耳", "耳朵"], "good": "厚实贴脑", "bad": "薄小招风", "default": "适中"},
    "鼻(审辨官)": {"keywords": ["鼻", "鼻子"], "good": "高挺端正", "bad": "歪斜露骨", "default": "适中"},
    "口(出纳官)": {"keywords": ["口", "嘴", "唇", "嘴巴"], "good": "唇红齿白", "bad": "唇薄歪斜", "default": "适中"},
}

# 五官具体分析词典
FIVE_FEATURES_ANALYSIS = {
    "眉(保寿官)": {
        "浓": "眉毛浓密，个性刚强",
        "淡": "眉毛清淡，性情温和",
        "长": "眉毛长，长寿之相",
        "短": "眉毛短，性情急躁",
        "粗": "眉粗有力，胆大",
        "细": "眉细清秀，心细",
        "弯": "眉弯如月，温和善良",
        "顺": "眉顺不乱，思路清晰",
        "连": "连眉，心胸狭窄",
        "散": "眉散，财不聚",
    },
    "眼(监察官)": {
        "大": "眼睛大，开朗外向",
        "小": "眼睛小，心思细腻",
        "长": "眼形长，稳重有威",
        "圆": "眼圆，机灵活泼",
        "有神": "目光有神，精力充沛",
        "无神": "目光无神，精力不足",
        "深邃": "眼神深邃，富有智慧",
        "清澈": "眼神清澈，心地光明",
        "三角": "三角眼，精明算计",
        "下垂": "眼尾下垂，亲和",
    },
    "耳(采听官)": {
        "大": "耳朵大，福气深厚",
        "小": "耳朵小，机警灵活",
        "厚": "耳厚有肉，福寿绵长",
        "薄": "耳薄，福薄",
        "高": "耳高于眉，聪明",
        "低": "耳低，踏实",
        "贴": "贴脑耳，稳重可靠",
        "招风": "招风耳，有创意",
    },
    "鼻(审辨官)": {
        "高": "鼻梁高，自信有魄力",
        "挺": "鼻梁挺直，正直",
        "大": "鼻头大，慷慨",
        "小": "鼻头小，节俭",
        "丰隆": "鼻头丰隆，财运佳",
        "尖削": "鼻尖削，精明",
        "露孔": "鼻孔外露，散财",
        "端正": "鼻端正，为人正直",
        "歪": "鼻歪，心术不正",
        "鹰钩": "鹰钩鼻，城府深",
    },
    "口(出纳官)": {
        "大": "嘴大，心胸开阔",
        "小": "嘴小，谨慎细心",
        "厚": "嘴唇厚，忠厚老实",
        "薄": "嘴唇薄，口才好",
        "红": "唇红，身体健康",
        "紫": "唇紫，体寒",
        "正": "嘴正，言行一致",
        "歪": "嘴歪，言不由衷",
        "方": "口方，有威仪",
        "尖": "口尖，刻薄",
    },
}

# 整体判定权重
ZONE_WEIGHTS = {"上停": 0.3, "中停": 0.4, "下停": 0.3}
MOUNTAIN_WEIGHTS = {"南岳(额头)": 0.2, "北岳(下巴)": 0.2, "东岳(左颧)": 0.1, "西岳(右颧)": 0.1, "中岳(鼻子)": 0.4}


@dataclass
class MianxiangResult:
    """面相分析结果"""
    face_type: str                   # 金形/木形/水形/火形/土形
    three_zones: Dict[str, str]      # 三停: {"上停":"饱满","中停":"适中","下停":"略短"}
    five_mountains: Dict[str, str]   # 五岳: {"南岳(额头)":"高耸","中岳(鼻子)":"端正",...}
    features: Dict[str, str]         # 五官: {"眉(保寿官)":"清秀弯长","眼(监察官)":"有神",...}
    overall: str                     # 综合判断
    raw_data: dict = field(default_factory=dict)


class MianxiangEngine:
    """面相引擎 - 基于五行面相理论分析面部特征"""

    def analyze(
        self,
        description: str = "",
        features: Optional[Dict[str, str]] = None,
    ) -> MianxiangResult:
        """面相分析

        Args:
            description: 面部描述文本（中文）
            features: 结构化特征字典（可选），可包含:
                - face_type: 直接指定脸型 "金形"/"木形"/"水形"/"火形"/"土形"
                - forehead: 额头描述
                - nose: 鼻子描述
                - chin: 下巴描述
                - left_cheekbone: 左颧描述
                - right_cheekbone: 右颧描述
                - eyebrows: 眉描述
                - eyes: 眼描述
                - ears: 耳描述
                - mouth: 口描述
        """
        if features is None:
            features = {}

        text = description

        # 1. 脸型判定
        face_type = self._determine_face_type(text, features)

        # 2. 三停分析
        three_zones = self._analyze_three_zones(text, features)

        # 3. 五岳分析
        five_mountains = self._analyze_five_mountains(text, features)

        # 4. 五官分析
        five_features = self._analyze_five_features(text, features)

        # 5. 综合判断
        overall = self._generate_overall(face_type, three_zones, five_mountains, five_features)

        return MianxiangResult(
            face_type=face_type,
            three_zones=three_zones,
            five_mountains=five_mountains,
            features=five_features,
            overall=overall,
            raw_data={
                "description": description,
                "features": features,
            },
        )

    # ---- 脸型判定 ----

    def _determine_face_type(self, text: str, features: Dict[str, str]) -> str:
        """根据描述或特征判定脸型"""
        # 优先使用直接指定
        ft = features.get("face_type", "")
        if ft in FACE_TYPE_RULES:
            return ft

        # 从描述中匹配关键词（按关键词长度降序匹配，避免短词误匹配）
        if text:
            for ftype, rule in sorted(
                FACE_TYPE_RULES.items(),
                key=lambda x: max(len(kw) for kw in x[1]["keywords"]),
                reverse=True,
            ):
                for kw in sorted(rule["keywords"], key=len, reverse=True):
                    if kw in text:
                        return ftype

        # 从特征值中扫描
        for key, val in features.items():
            for ftype, rule in sorted(
                FACE_TYPE_RULES.items(),
                key=lambda x: max(len(kw) for kw in x[1]["keywords"]),
                reverse=True,
            ):
                for kw in sorted(rule["keywords"], key=len, reverse=True):
                    if kw in val:
                        return ftype

        return "土形"  # default

    # ---- 三停分析 ----

    def _analyze_three_zones(self, text: str, features: Dict[str, str]) -> Dict[str, str]:
        """分析三停（上中下三停）"""
        result = {}
        zone_map = {
            "上停": ["额头", "额", "上停", "发际"],
            "中停": ["鼻子", "鼻", "颧骨", "中停"],
            "下停": ["下巴", "下停", "颔", "地阁"],
        }

        zone_keys = {
            "上停": "forehead",
            "中停": "nose",
            "下停": "chin",
        }

        for zone, zkeywords in zone_map.items():
            rule = THREE_ZONES_RULES[zone]
            found = self._evaluate_from_text_and_features(
                text, features, zkeywords, zone_keys[zone],
                rule["keywords_good"], rule["keywords_bad"], rule["default"],
            )
            result[zone] = found

        return result

    # ---- 五岳分析 ----

    def _analyze_five_mountains(self, text: str, features: Dict[str, str]) -> Dict[str, str]:
        """分析五岳"""
        result = {}
        mountain_keywords = {
            "南岳(额头)": (["额头", "额"], "forehead"),
            "北岳(下巴)": (["下巴", "地阁"], "chin"),
            "东岳(左颧)": (["左颧", "左脸颊", "左颊"], "left_cheekbone"),
            "西岳(右颧)": (["右颧", "右脸颊", "右颊"], "right_cheekbone"),
            "中岳(鼻子)": (["鼻子", "鼻"], "nose"),
        }

        for mt_name, (mt_keywords, feat_key) in mountain_keywords.items():
            rule = FIVE_MOUNTAINS_RULES[mt_name]
            val = self._evaluate_from_text_and_features(
                text, features, mt_keywords, feat_key,
                rule["keyword_good"], rule["keyword_bad"], rule["default"],
            )
            result[mt_name] = val

        return result

    # ---- 五官分析 ----

    def _analyze_five_features(self, text: str, features: Dict[str, str]) -> Dict[str, str]:
        """分析五官（眉、眼、耳、鼻、口）"""
        result = {}
        feat_keywords = {
            "眉(保寿官)": (["眉", "眉毛"], "eyebrows"),
            "眼(监察官)": (["眼", "眼睛"], "eyes"),
            "耳(采听官)": (["耳", "耳朵"], "ears"),
            "鼻(审辨官)": (["鼻", "鼻子"], "nose"),
            "口(出纳官)": (["口", "嘴", "唇", "嘴巴"], "mouth"),
        }

        for feat_name, (kw_list, feat_key) in feat_keywords.items():
            analysis = self._analyze_single_feature(text, features, kw_list, feat_key, feat_name)
            result[feat_name] = analysis

        return result

    def _analyze_single_feature(
        self, text: str, features: Dict[str, str],
        kw_list: List[str], feat_key: str, feat_name: str,
    ) -> str:
        """分析单一五官"""
        # 先从 features dict 取
        feat_val = features.get(feat_key, "")

        # 从描述中匹配关键词
        search_text = text + " " + feat_val
        detail_dict = FIVE_FEATURES_ANALYSIS.get(feat_name, {})

        found_details = []
        for detail_kw, detail_desc in detail_dict.items():
            if detail_kw in search_text:
                found_details.append(detail_desc)

        if found_details:
            return "；".join(found_details[:3])

        # 从描述中判断好坏
        if feat_val:
            rule = FIVE_FEATURES_RULES[feat_name]
            if any(g in feat_val for g in rule["good"][:2]):
                return rule["good"]
            if any(b in feat_val for b in rule["bad"][:2]):
                return rule["bad"]

        # 从文本中判断
        if text:
            rule = FIVE_FEATURES_RULES[feat_name]
            # 检查是否提到了该部位
            if any(kw in text for kw in kw_list):
                if any(g in text for g in ["好", "美", "佳", "端正", "有神", "清秀"]):
                    return rule["good"]
                if any(b in text for b in ["差", "不好", "丑", "歪", "斜"]):
                    return rule["bad"]

        return FIVE_FEATURES_RULES[feat_name]["default"]

    # ---- 辅助方法 ----

    def _evaluate_from_text_and_features(
        self, text: str, features: Dict[str, str],
        text_keywords: List[str], feature_key: str,
        good_keywords: List[str], bad_keywords: List[str],
        default: str,
    ) -> str:
        """从文本和特征中评估某个部位"""
        # 按长度降序排列关键词，优先匹配更具体的词
        good_sorted = sorted(good_keywords, key=len, reverse=True)
        bad_sorted = sorted(bad_keywords, key=len, reverse=True)

        # 先从 features dict 取指定特征
        feat_val = features.get(feature_key, "")

        # 检查特征值
        if feat_val:
            for g in good_sorted:
                if g in feat_val:
                    return self._describe_positive(g)
            for b in bad_sorted:
                if b in feat_val:
                    return self._describe_negative(b)

        # 从描述文本匹配
        if text:
            for tk in text_keywords:
                if tk in text:
                    for g in good_sorted:
                        if g in text:
                            return self._describe_positive(g)
                    for b in bad_sorted:
                        if b in text:
                            return self._describe_negative(b)

        return default

    @staticmethod
    def _describe_positive(kw: str) -> str:
        """正面描述的标准化"""
        mapping = {
            "饱满": "饱满丰隆", "宽阔": "宽阔饱满", "丰隆": "丰隆有势",
            "高": "高耸有势", "高耸": "高耸有势", "挺直": "端正挺直",
            "端正": "端正有型", "圆润": "圆润饱满", "丰厚": "丰厚有肉",
            "宽": "宽阔适宜", "有肉": "丰润有肉", "朝拱": "朝拱有情",
            "相拱": "相拱有情", "圆": "圆润饱满", "方": "方正有威",
            "明润": "明润有泽",
        }
        return mapping.get(kw, f"{kw}")

    @staticmethod
    def _describe_negative(kw: str) -> str:
        """负面描述的标准化"""
        mapping = {
            "窄": "偏窄", "低": "偏低", "凹陷": "凹陷不足",
            "短": "略短", "塌": "塌陷", "尖": "偏尖",
            "薄": "偏薄", "歪": "不正", "露骨": "露骨",
            "后缩": "后缩", "横突": "横突", "暗淡": "暗淡无光",
            "狭窄": "狭窄", "扁": "扁平",
        }
        return mapping.get(kw, f"{kw}")

    # ---- 综合判断 ----

    def _generate_overall(
        self, face_type: str,
        three_zones: Dict[str, str],
        five_mountains: Dict[str, str],
        features: Dict[str, str],
    ) -> str:
        """生成综合判断"""
        parts = []

        # 脸型说明
        face_desc = FACE_TYPE_RULES.get(face_type, {})
        parts.append(f"脸型为{face_type}：{face_desc.get('desc', '')}。")

        # 三停评语
        zone_verdicts = []
        for zone, status in three_zones.items():
            zone_verdicts.append(f"{zone}{status}")
        parts.append("三停方面：" + "，".join(zone_verdicts) + "。")

        # 五岳评语
        mt_verdicts = []
        for mt_name, status in five_mountains.items():
            mt_verdicts.append(f"{mt_name.split('(')[0]}{status}")
        parts.append("五岳方面：" + "，".join(mt_verdicts) + "。")

        # 五官评语
        feat_verdicts = []
        for feat_name, analysis in features.items():
            short_name = feat_name.split("(")[0]
            feat_verdicts.append(f"{short_name}{analysis}")
        parts.append("五官方面：" + "；".join(feat_verdicts) + "。")

        # 综合结论
        total_score = 0.0
        # 三停评分
        zone_scores = {"上停": {"饱满丰隆": 9, "宽阔饱满": 9, "方正有威": 8,
                                 "丰隆有势": 8, "高耸有势": 8, "端正有型": 7,
                                 "圆润饱满": 7, "宽阔适宜": 7, "适中": 6,
                                 "偏窄": 4, "偏低": 4, "略短": 4, "偏低": 4,
                                 "凹陷不足": 3, "狭窄": 3, "扁平": 3},
                       "中停": {"端正挺直": 9, "端正有型": 8, "丰隆有势": 8,
                                 "高耸有势": 8, "宽阔饱满": 7, "圆润饱满": 7,
                                 "适中": 6, "偏窄": 4, "偏低": 4, "扁平": 4,
                                 "凹陷不足": 3, "不正": 2, "塌陷": 2},
                       "下停": {"饱满丰隆": 9, "圆润饱满": 8, "丰厚有肉": 8,
                                 "方正有威": 8, "宽阔饱满": 7, "宽阔适宜": 7,
                                 "适中": 6, "偏尖": 4, "偏窄": 4, "偏薄": 4,
                                 "略短": 4, "后缩": 3}}

        for zone_name, status in three_zones.items():
            s_map = zone_scores.get(zone_name, {})
            score = s_map.get(status, 6)
            total_score += score * ZONE_WEIGHTS.get(zone_name, 0.33)

        # 五岳评分
        mt_scores = {"高耸有势": 9, "饱满丰隆": 9, "宽阔饱满": 8, "端正有型": 8,
                      "丰隆有势": 8, "圆润饱满": 8, "丰润有肉": 7, "端正挺直": 8,
                      "明润有泽": 7, "适中": 6, "朝拱有情": 8, "相拱有情": 7,
                      "方正有威": 7, "宽阔适宜": 6, "偏窄": 4, "偏低": 4,
                      "凹陷不足": 3, "塌陷": 2, "露骨": 3, "不正": 2,
                      "暗淡无光": 3, "后缩": 3, "扁平": 4, "略短": 4,
                      "偏尖": 4, "狭窄": 3, "偏薄": 4, "横突": 3}

        for mt_name, status in five_mountains.items():
            score = mt_scores.get(status, 6)
            total_score += score * MOUNTAIN_WEIGHTS.get(mt_name, 0.2)

        # 总分归一化为 1-10
        normalized_score = min(10, max(1, round(total_score)))

        if normalized_score >= 8:
            verdict = "面相上佳。三停匀称、五岳朝拱、五官端正，为富贵福寿之相。"
        elif normalized_score >= 6:
            verdict = "面相中上。整体格局不错，个别部位可适当调整，运势平稳向上。"
        elif normalized_score >= 4:
            verdict = "面相中等。虽有不足，但通过修身养性可改善运势。"
        else:
            verdict = "面相有待改善。某些部位需要加强，建议多行善积德以改运。"

        parts.append(f"综合评分为{normalized_score}/10，{verdict}")

        return "\n".join(parts)
