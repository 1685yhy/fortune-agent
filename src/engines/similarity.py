"""命例相似度匹配引擎 — 结构化特征比对。

【已停用 2026-08-09 · 用户确认方案 v5 选 A 彻底移除】
本文件保留仅供历史参考，不再被任何代码调用（handler.py 已删除引用）。
原因：未问"像谁"也输出命例/名人对照（用户实测差评点"和李连杰相似"），
且每次对话加载 44k 命例 / 950 名人有性能开销。

在 44k 命例库中找到与用户最相似的命盘，提供命例统计分析。
基于日主、月令、十神、神煞等结构化特征进行加权匹配。
"""

import sqlite3
import json
import math
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field


# ── 五行元素 ────────────────────────────────────────────────────

TIANGAN = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
DIZHI = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
TG_WUXING = {"甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
             "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水"}
DZ_WUXING = {"子": "水", "丑": "土", "寅": "木", "卯": "木", "辰": "土", "巳": "火",
             "午": "火", "未": "土", "申": "金", "酉": "金", "戌": "土", "亥": "水"}


@dataclass
class ChartFeatures:
    """命盘结构化特征"""
    id: int
    day_master: str          # 日主天干
    month_branch: str        # 月支（月令）
    year_pillar: str         # 年柱
    month_pillar: str        # 月柱
    day_pillar: str          # 日柱
    hour_pillar: str         # 时柱
    shishen: List[str]       # 十神
    shensha: List[str]       # 神煞
    dayun: List[str]         # 大运
    gender: str              # 性别
    qiyunsui: int            # 起运岁数
    day_wuxing: str          # 日主五行
    wuxing_balance: Dict[str, int]  # 五行统计


@dataclass
class SimilarCase:
    """相似命例"""
    chart_id: int
    score: float             # 相似度分数 0-1
    day_master: str
    month_pillar: str
    day_pillar: str
    shishen: List[str]
    shensha: List[str]
    dayun: List[str]
    gender: str
    qiyunsui: int


@dataclass
class SimilarityReport:
    """相似度分析报告"""
    user_chart: Dict
    top_matches: List[SimilarCase]
    total_cases: int
    avg_similarity: float
    # 统计分析
    day_master_distribution: Dict[str, int]  # 日主分布
    geju_patterns: Dict[str, int]            # 常见格局组合
    career_peak_range: Tuple[int, int]       # 事业高峰年龄段
    wealth_peak_range: Tuple[int, int]       # 财运高峰年龄段
    common_shensha: List[str]                # 共有的神煞
    insight_text: str                        # AI可读的洞察文本


class SimilarityEngine:
    """命例相似度匹配引擎"""

    # ── 权重配置 ─────────────────────────────────────────────
    WEIGHTS = {
        "day_master": 0.20,      # 日主相同
        "month_branch": 0.25,     # 月令相同（格局关键）
        "shishen": 0.25,          # 十神模式相似
        "shensha": 0.15,          # 神煞重叠
        "nayin_day": 0.08,        # 日柱纳音相同
        "wuxing_balance": 0.07,   # 五行平衡度相似
    }

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._features_cache: List[ChartFeatures] = []
        self._loaded = False

    def _load_features(self) -> List[ChartFeatures]:
        """从SQLite加载所有命盘的结构化特征"""
        if self._loaded and self._features_cache:
            return self._features_cache

        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("""
            SELECT id, day_master, day_pillar, month_pillar, year_pillar, hour_pillar,
                   shishen, shensha, dayun, gender, qiyunsui
            FROM charts
            WHERE day_master IS NOT NULL AND day_master != ''
            ORDER BY id
        """).fetchall()
        conn.close()

        features = []
        for row in rows:
            try:
                day_master = row[1] or ""
                day_pillar = row[2] or ""
                month_pillar = row[3] or ""
                year_pillar = row[4] or ""
                hour_pillar = row[5] or ""

                # 月支
                month_branch = month_pillar[1] if len(month_pillar) >= 2 else ""

                # 解析 JSON 字段
                shishen_list = _safe_json_parse(row[6])
                shensha_list = _safe_json_parse(row[7])
                dayun_list = _safe_json_parse(row[8])

                # 五行统计
                wb = _count_wuxing(year_pillar, month_pillar, day_pillar, hour_pillar)

                feat = ChartFeatures(
                    id=row[0],
                    day_master=day_master,
                    month_branch=month_branch,
                    year_pillar=year_pillar,
                    month_pillar=month_pillar,
                    day_pillar=day_pillar,
                    hour_pillar=hour_pillar,
                    shishen=shishen_list,
                    shensha=shensha_list,
                    dayun=dayun_list,
                    gender=row[9] or "未知",
                    qiyunsui=row[10] or 0,
                    day_wuxing=TG_WUXING.get(day_master, ""),
                    wuxing_balance=wb,
                )
                features.append(feat)
            except Exception:
                continue

        self._features_cache = features
        self._loaded = True
        return features

    def search(self, user_chart: Dict, top_k: int = 10) -> SimilarityReport:
        """搜索与用户命盘最相似的命例。

        Args:
            user_chart: 用户命盘信息，包含 day_master, bazi, shishen, shensha 等
            top_k: 返回最相似的 top_k 个命例

        Returns:
            SimilarityReport 包含匹配结果和统计分析
        """
        all_features = self._load_features()

        # 从用户命盘提取特征
        user_feat = self._extract_user_features(user_chart)

        # 计算每个命例的相似度
        scored = []
        for feat in all_features:
            score = self._compute_similarity(user_feat, feat)
            scored.append((score, feat))

        # 排序取 top_k
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:top_k]

        # 构建匹配结果
        matches = []
        for score, feat in top:
            matches.append(SimilarCase(
                chart_id=feat.id,
                score=round(score, 4),
                day_master=feat.day_master,
                month_pillar=feat.month_pillar,
                day_pillar=feat.day_pillar,
                shishen=feat.shishen,
                shensha=feat.shensha,
                dayun=feat.dayun,
                gender=feat.gender,
                qiyunsui=feat.qiyunsui,
            ))

        # 统计分析
        similarities = [s for s, _ in top]
        avg_sim = sum(similarities) / len(similarities) if similarities else 0

        # 日主分布
        dm_dist = {}
        for _, feat in top:
            dm_dist[feat.day_master] = dm_dist.get(feat.day_master, 0) + 1

        # 常见格局模式（月令+十神组合）
        geju_patterns = {}
        for _, feat in top:
            pattern = f"{feat.month_branch}月{feat.shishen[2] if len(feat.shishen) > 2 else ''}"
            geju_patterns[pattern] = geju_patterns.get(pattern, 0) + 1

        # 事业高峰：大运中出现官杀/印星的年龄段
        career_ages = []
        wealth_ages = []
        for _, feat in top:
            for i, dy in enumerate(feat.dayun[:8]):
                age = feat.qiyunsui + i * 10
                dy_str = str(dy)
                # 官杀运 → 事业
                if any(c in dy_str for c in ["官", "杀"]):
                    career_ages.append(age)
                # 财星运 → 财运
                if any(c in dy_str for c in ["财"]):
                    wealth_ages.append(age)

        career_peak = (min(career_ages), max(career_ages)) if career_ages else (25, 45)
        wealth_peak = (min(wealth_ages), max(wealth_ages)) if wealth_ages else (30, 50)

        # 共有神煞
        shensha_counter = {}
        for _, feat in top:
            for ss in feat.shensha:
                shensha_counter[ss] = shensha_counter.get(ss, 0) + 1
        common_ss = [ss for ss, c in sorted(shensha_counter.items(), key=lambda x: -x[1])[:5] if c >= 3]

        # 生成洞察文本
        insight = self._generate_insight(
            user_feat, matches, avg_sim, dm_dist,
            career_peak, wealth_peak, common_ss, len(all_features),
        )

        return SimilarityReport(
            user_chart=user_chart,
            top_matches=matches,
            total_cases=len(all_features),
            avg_similarity=round(avg_sim, 4),
            day_master_distribution=dm_dist,
            geju_patterns=geju_patterns,
            career_peak_range=career_peak,
            wealth_peak_range=wealth_peak,
            common_shensha=common_ss,
            insight_text=insight,
        )

    def _extract_user_features(self, chart: Dict) -> ChartFeatures:
        """从用户命盘字典提取特征"""
        bazi = chart.get("bazi", [])
        day_master = chart.get("day_master", "")
        shishen = _safe_json_parse(chart.get("shishen", "[]"))
        shensha = _safe_json_parse(chart.get("shensha", "[]"))
        dayun = chart.get("dayun", [])

        year_pillar = bazi[0] if len(bazi) > 0 else ""
        month_pillar = bazi[1] if len(bazi) > 1 else ""
        day_pillar = bazi[2] if len(bazi) > 2 else ""
        hour_pillar = bazi[3] if len(bazi) > 3 else ""

        month_branch = month_pillar[1] if len(month_pillar) >= 2 else ""
        gender = chart.get("gender", "未知")

        # 五行统计
        wb = _count_wuxing(year_pillar, month_pillar, day_pillar, hour_pillar)

        return ChartFeatures(
            id=0,
            day_master=day_master,
            month_branch=month_branch,
            year_pillar=year_pillar,
            month_pillar=month_pillar,
            day_pillar=day_pillar,
            hour_pillar=hour_pillar,
            shishen=shishen,
            shensha=shensha,
            dayun=[d[1] if isinstance(d, tuple) else str(d) for d in dayun],
            gender=gender,
            qiyunsui=0,
            day_wuxing=TG_WUXING.get(day_master, ""),
            wuxing_balance=wb,
        )

    def _compute_similarity(self, user: ChartFeatures, other: ChartFeatures) -> float:
        """计算两个命盘的结构化相似度（加权求和）"""
        score = 0.0

        # 1. 日主相同 (20%)
        if user.day_master == other.day_master:
            score += self.WEIGHTS["day_master"]

        # 2. 月令相同 (25%)
        if user.month_branch == other.month_branch:
            score += self.WEIGHTS["month_branch"]

        # 3. 十神模式相似 (25%) — Jaccard or positional match
        if user.shishen and other.shishen:
            shishen_sim = _jaccard_similarity(user.shishen, other.shishen)
            score += shishen_sim * self.WEIGHTS["shishen"]

        # 4. 神煞重叠 (15%)
        if user.shensha and other.shensha:
            ss_sim = _jaccard_similarity(user.shensha, other.shensha)
            score += ss_sim * self.WEIGHTS["shensha"]

        # 5. 日柱纳音相同 (8%) — 用日支五行近似
        if user.day_pillar and other.day_pillar:
            user_dz = user.day_pillar[1] if len(user.day_pillar) >= 2 else ""
            other_dz = other.day_pillar[1] if len(other.day_pillar) >= 2 else ""
            if user_dz and other_dz and DZ_WUXING.get(user_dz) == DZ_WUXING.get(other_dz):
                score += self.WEIGHTS["nayin_day"]

        # 6. 五行平衡相似 (7%) — 欧几里得距离
        if user.wuxing_balance and other.wuxing_balance:
            wx_sim = _wuxing_similarity(user.wuxing_balance, other.wuxing_balance)
            score += wx_sim * self.WEIGHTS["wuxing_balance"]

        return score

    def _generate_insight(
        self,
        user: ChartFeatures,
        matches: List[SimilarCase],
        avg_sim: float,
        dm_dist: Dict[str, int],
        career_peak: Tuple[int, int],
        wealth_peak: Tuple[int, int],
        common_shensha: List[str],
        total_cases: int,
    ) -> str:
        """生成可读的洞察文本"""
        lines = []

        dm = user.day_master
        dm_wx = user.day_wuxing

        lines.append(f"在 {total_cases:,} 个命例库中，与你最相似的 Top-{len(matches)} 命例平均相似度为 {avg_sim:.1%}。")

        # 日主分布
        dm_entries = sorted(dm_dist.items(), key=lambda x: -x[1])
        dm_str = "、".join(f"{k}日主({v}例)" for k, v in dm_entries[:3])
        lines.append(f"相似命例中日主分布：{dm_str}。")

        # 格局
        if user.month_branch:
            lines.append(f"你的月令为{user.month_branch}月（{DZ_WUXING.get(user.month_branch, '')}），"
                         f"相似命例中该月令占比约 {dm_dist.get(user.day_master, 0) / max(len(matches), 1):.0%}。")

        # 事业/财运高峰
        lines.append(f"相似命例统计：事业高峰期约在 {career_peak[0]}-{career_peak[1]} 岁，"
                     f"财运高峰期约在 {wealth_peak[0]}-{wealth_peak[1]} 岁。")

        # 神煞
        if common_shensha:
            lines.append(f"相似命例共有神煞：{'、'.join(common_shensha)}。")

        # 大运启示
        if matches:
            best = matches[0]
            lines.append(f"最相似命例（相似度 {best.score:.1%}）：{best.day_master}日主，"
                         f"{best.month_pillar}月，起运 {best.qiyunsui} 岁。")

        return "\n".join(lines)


# ── 辅助函数 ────────────────────────────────────────────────────

def _safe_json_parse(val) -> list:
    """安全解析 JSON 字符串/列表"""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def _jaccard_similarity(a: List[str], b: List[str]) -> float:
    """计算两个集合的 Jaccard 相似度"""
    set_a = set(a)
    set_b = set(b)
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def _count_wuxing(year: str, month: str, day: str, hour: str) -> Dict[str, int]:
    """统计四柱五行出现次数"""
    counts = {"木": 0, "火": 0, "土": 0, "金": 0, "水": 0}
    for pillar in [year, month, day, hour]:
        if len(pillar) >= 2:
            tg = pillar[0]
            dz = pillar[1]
            if tg in TG_WUXING:
                counts[TG_WUXING[tg]] += 1
            if dz in DZ_WUXING:
                counts[DZ_WUXING[dz]] += 1
    return counts


def _wuxing_similarity(a: Dict[str, int], b: Dict[str, int]) -> float:
    """计算五行平衡的余弦相似度"""
    elements = ["木", "火", "土", "金", "水"]
    vec_a = [a.get(e, 0) for e in elements]
    vec_b = [b.get(e, 0) for e in elements]

    dot = sum(x * y for x, y in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(x * x for x in vec_a))
    norm_b = math.sqrt(sum(x * x for x in vec_b))

    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
