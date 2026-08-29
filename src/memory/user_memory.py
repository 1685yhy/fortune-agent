"""用户记忆系统 - 跨会话持久化用户记忆。

存储为 JSON 文件：data/memory/{user_id}.json

传统字段（兼容保留）：
- bazi_info: 八字信息（年、月、日、时、分、城市、性别）
- last_topic: 最近一次对话主题
- concerns: 用户关心的主题列表
- mood_history: 最近的情感状态记录
- consultation_count: 总咨询次数
- topic_counts: 主题出现次数（AI 原生 Phase 2：重复话题检测，方案 5.5）
- last_visit: 最近访问时间（活跃度，方案 6.1）

L3 长期记忆（方案 §5.5，v4 结构化）：
- entries: 结构化记忆条目列表，每条：
  {id, type: profile|preference|topic|event, subject, content, confidence,
   created_at, ttl_days, is_key, source, hit_count}
- 类型语义：profile 画像（八字/性别等，长期）；preference 偏好（长期）；
  topic 关注主题（计数）；event 关键事件（如"正在找工作"，默认 90 天过期）
- TTL：过期条目读取时自动剔除（运行时过期，原始对话仍全量留存）
- 冲突覆盖：同 type + 同 subject 的新信息覆盖旧条目（状态变化理解）
- 按需召回：get_relevant_memories 用关键词匹配 + 可选 embedding 相似度
- 隐私：list_entries / delete_entry / clear_all（用户可查看、删除）
"""

import json
import os
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple


# 时辰（地支）序列：index = (hour + 1) // 2 % 12
_SHICHEN = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]


def _hour_to_shichen(hour) -> Optional[str]:
    """24 小时制 → 时辰（如 7 → 辰；23/0 → 子）。非法值返回 None。"""
    try:
        h = int(hour)
    except (TypeError, ValueError):
        return None
    if h < 0 or h > 23:
        return None
    return _SHICHEN[(h + 1) // 2 % 12]


def format_birth_line(bazi: Optional[dict]) -> str:
    """由原始出生字段生成画像行：「出生:1990年8月20日 辰时 北京 男(来自用户档案)」。

    输入为含 year/month/day/hour/minute/city/gender 的 dict（可缺键）；
    字段缺失部分不写；全部缺失返回空串（无档案不增加任何内容）。
    """
    if not bazi:
        return ""
    parts = []
    y, m, d = bazi.get("year"), bazi.get("month"), bazi.get("day")
    if y and m and d:
        parts.append(f"{y}年{m}月{d}日")
    hour = bazi.get("hour")
    if hour not in (None, ""):
        shichen = _hour_to_shichen(hour)
        if shichen:
            parts.append(f"{shichen}时")
    city = bazi.get("city")
    if city:
        parts.append(str(city))
    gender = bazi.get("gender")
    if gender and str(gender) not in ("unknown", "None"):
        parts.append(str(gender))
    if not parts:
        return ""
    return "出生:" + " ".join(parts) + "(来自用户档案)"


class UserMemory:
    """Persistent user memory across sessions.

    每个用户的记忆存储为单独的 JSON 文件。
    支持记住、回忆、获取上下文和忘记功能。
    """

    _DEFAULT_FIELDS = {
        "bazi_info": None,
        "last_topic": "",
        "concerns": [],
        "mood_history": [],
        "consultation_count": 0,
        "topic_counts": {},
        "last_visit": "",
    }

    # 主题英文键 → 中文名（画像摘要用）
    TOPIC_CN = {"wealth": "财运", "love": "感情", "career": "事业",
                "health": "健康", "growth": "个人成长"}
    MOOD_CN = {"heartbreak": "感情困扰", "sadness": "低落", "anxiety": "焦虑",
               "confusion": "迷茫", "anger": "烦躁", "happy": "不错", "neutral": "平稳"}

    def __init__(self, base_dir: Optional[str] = None):
        """初始化用户记忆系统。

        Args:
            base_dir: 记忆文件存储目录，默认 data/memory
            （环境变量 USER_MEMORY_DIR 可覆盖默认目录，测试隔离用）
        """
        if base_dir is None:
            env_dir = os.environ.get("USER_MEMORY_DIR", "").strip()
            if env_dir:
                base_dir = env_dir
            else:
                base_dir = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "data", "memory",
                )
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    # ------------------------------------------------------------
    # 路径与 I/O
    # ------------------------------------------------------------

    def _path(self, user_id: str) -> str:
        """获取用户记忆文件的路径。"""
        # Sanitize user_id to prevent directory traversal
        safe_id = user_id.replace("/", "_").replace("\\", "_").replace("..", "_")
        return os.path.join(self.base_dir, f"{safe_id}.json")

    def _load(self, user_id: str) -> Dict[str, Any]:
        """从磁盘加载用户记忆。"""
        path = self._path(user_id)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def _save(self, user_id: str, data: Dict[str, Any]) -> None:
        """将用户记忆写入磁盘。"""
        data["_updated_at"] = datetime.now().isoformat()
        path = self._path(user_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------
    # 核心 API
    # ------------------------------------------------------------

    def remember(self, user_id: str, key: str, value: Any) -> None:
        """记住一个键值对。

        Args:
            user_id: 用户 ID
            key: 键名
            value: 值（必须是 JSON 可序列化类型）
        """
        data = self._load(user_id)
        data[key] = value
        self._save(user_id, data)

    def recall(self, user_id: str, key: str) -> Optional[Any]:
        """回忆一个键的值。

        Args:
            user_id: 用户 ID
            key: 键名

        Returns:
            存储的值，如果键不存在返回 None
        """
        data = self._load(user_id)
        return data.get(key)

    def get_context(self, user_id: str) -> Dict[str, Any]:
        """获取用户的所有记忆。

        Args:
            user_id: 用户 ID

        Returns:
            包含所有记忆字段的字典
        """
        data = self._load(user_id)
        # Ensure default fields exist for convenient access
        for field, default in self._DEFAULT_FIELDS.items():
            if field not in data:
                data[field] = default
        return data

    def forget(self, user_id: str, key: str) -> None:
        """忘记一个键。

        Args:
            user_id: 用户 ID
            key: 要删除的键名
        """
        data = self._load(user_id)
        data.pop(key, None)
        self._save(user_id, data)

    # ------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------

    def has_memory(self, user_id: str) -> bool:
        """检查用户是否有记忆数据。"""
        return os.path.exists(self._path(user_id))

    def save_bazi_info(self, user_id: str, bazi_info: Dict[str, Any],
                       subject: str = "self",
                       force_gender: bool = False) -> dict:
        """保存用户的八字信息（阶段 5 关键字段保护，方案 v5）。

        - 字段级合并：只更新新提供的非空字段，未提供的保留旧值
          （防排盘缺字段时把已存信息覆盖成默认值）
        - gender 保护：已有值且新值 unknown/None → 不覆盖；
          已有值且新值冲突（女 vs 男）→ 不覆盖并返回 conflict 标记
          （handler 综合回复时向用户确认）
        - force_gender=True（G1-C2，仅用户明示性别纠正路径）：男↔女冲突时
          强制覆写为新性别且不返回 conflict 标记（画像层与权威档案
          users.bazi_info+persons 同步）；默认 False 时拒绝覆写行为不变。
          unknown/None 新值保护不受 force 影响（仍不覆盖）。
        - subject=other（帮他人排盘）→ 不写入本人画像（facts.subject 隔离）

        Args:
            user_id: 用户 ID
            bazi_info: 包含 year, month, day, hour, minute, city, gender 等字段
            subject: "self" 本人 / "other" 帮他人排盘
            force_gender: 仅用户明示性别纠正时传 True，强制覆写冲突性别

        Returns:
            dict: 冲突标记 {"conflict": True, "conflict_field": "gender"}；无冲突返回 {}
        """
        data = self._load(user_id)
        if subject and subject != "self":
            # 帮他人排盘：不污染本人画像（阶段 5 subject 隔离）
            return {}
        old = data.get("bazi_info") or {}
        merged = dict(old)
        conflict: Dict[str, Any] = {}
        for k, v in (bazi_info or {}).items():
            if v is None or str(v).strip() == "":
                continue  # 非空才更新
            if k == "gender":
                old_g = str(old.get("gender") or "")
                new_g = str(v)
                if old_g and old_g not in ("unknown", "None"):
                    if new_g in ("unknown", "None"):
                        continue  # 新值 unknown 不覆盖已有值
                    if new_g != old_g and not force_gender:
                        # 已有值且新值冲突（女 vs 男）：不覆盖，返回冲突标记
                        conflict = {"conflict": True, "conflict_field": "gender"}
                        continue
            merged[k] = v
        data["bazi_info"] = merged
        data["consultation_count"] = data.get("consultation_count", 0) + 1
        self._save(user_id, data)
        return conflict

    def add_mood_record(self, user_id: str, mood: str, topic: str = "") -> None:
        """添加一条情绪记录。

        Args:
            user_id: 用户 ID
            mood: 情绪标签（如 "anxiety", "sadness", "happy"）
            topic: 当前讨论主题
        """
        data = self._load(user_id)
        mood_history: List[dict] = data.get("mood_history", [])
        mood_history.append({
            "mood": mood,
            "topic": topic,
            "timestamp": datetime.now().isoformat(),
        })
        # Keep only last 20 records
        if len(mood_history) > 20:
            mood_history = mood_history[-20:]
        data["mood_history"] = mood_history
        data["last_topic"] = topic or data.get("last_topic", "")
        self._save(user_id, data)

    def add_concern(self, user_id: str, concern: str) -> None:
        """添加一个用户关心的主题。

        Args:
            user_id: 用户 ID
            concern: 关心的话题（如 "事业", "感情", "健康"）
        """
        data = self._load(user_id)
        concerns: List[str] = data.get("concerns", [])
        if concern not in concerns:
            concerns.append(concern)
            # Keep only last 10 unique concerns
            if len(concerns) > 10:
                concerns = concerns[-10:]
        data["concerns"] = concerns
        self._save(user_id, data)

    # ------------------------------------------------------------
    # L3 长期记忆（方案 §5.5）— 结构化条目 / TTL / 冲突覆盖 / 按需召回
    # ------------------------------------------------------------

    ENTRY_TYPES = ("profile", "preference", "topic", "event")
    ENTRY_TYPE_CN = {"profile": "画像", "preference": "偏好",
                     "topic": "主题", "event": "事件"}
    # 默认 TTL（天）：event 短时效；profile/preference/topic 长期
    DEFAULT_TTL_DAYS = {"profile": None, "preference": None,
                        "topic": None, "event": 90}
    # 召回关键词过滤的常见停用字
    _STOP_CHARS = set("的了是在我你他她它们和与就都有没也去来看说想问什么怎样"

                      "一下一个这那吧啊呢吗些还于为被把给向从到中上小大")

    def list_entries(self, user_id: str, entry_type: Optional[str] = None,
                     prune: bool = True) -> List[dict]:
        """列出用户的结构化记忆条目（按创建时间新→旧）。

        Args:
            user_id: 用户 ID
            entry_type: 过滤类型（profile/preference/topic/event），None = 全部
            prune: 是否先剔除 TTL 过期条目（默认 True）

        Returns:
            list of dicts: [{id, type, subject, content, confidence, created_at,
                             ttl_days, is_key, source, hit_count}, ...]
        """
        data = self._load(user_id)
        if prune:
            self._prune(data)
            self._save(user_id, data)
        entries = data.get("entries", [])
        if entry_type:
            entries = [e for e in entries if e.get("type") == entry_type]
        return sorted(entries, key=lambda e: e.get("created_at", ""), reverse=True)

    def add_entry(self, user_id: str, entry_type: str, content: str,
                  subject: str = "", confidence: float = 1.0,
                  ttl_days: Optional[int] = None, is_key: bool = False,
                  source: str = "", created_at: Optional[str] = None,
                  entry_id: Optional[str] = None) -> dict:
        """写入一条结构化记忆（L3，方案 §5.5）。

        - 冲突覆盖：同 type + 同 subject（非空）的新条目替换旧条目
          （如八字更新、状态变化），不机械堆叠
        - TTL：ttl_days 为 None 表示长期保留；默认按类型取 DEFAULT_TTL_DAYS
        - topic 类型：同 subject 重复写入时计数 +1（hit_count），内容更新为最新次数

        Args:
            user_id: 用户 ID
            entry_type: profile / preference / topic / event
            content: 记忆内容（自然语言文本）
            subject: 主题键（冲突覆盖与召回用；如 "bazi"、"找工作"）
            confidence: 置信度 0-1
            ttl_days: 有效期（天），None = 长期
            is_key: 是否关键事实（八字/用户明确陈述的工作/感情状态等，
                    组装上下文时无论多旧都保留，方案 §5.3 关键事实保底）
            source: 来源（l2_memories / bazi_analysis / event_capture / manual）
            created_at: 创建时间（ISO），默认现在（测试可注入旧时间验证 TTL）
            entry_id: 显式 ID（默认自动生成）
        """
        if entry_type not in self.ENTRY_TYPES:
            raise ValueError(f"未知记忆类型: {entry_type}，应为 {self.ENTRY_TYPES}")
        data = self._load(user_id)
        self._prune(data)
        entries: List[dict] = data.get("entries", [])
        now = created_at or datetime.now().isoformat()
        if ttl_days is None:
            ttl_days = self.DEFAULT_TTL_DAYS.get(entry_type)

        new_entry = {
            "id": entry_id or uuid.uuid4().hex[:12],
            "type": entry_type,
            "subject": subject or "",
            "content": content,
            "confidence": max(0.0, min(1.0, float(confidence))),
            "created_at": now,
            "ttl_days": ttl_days,
            "is_key": bool(is_key),
            "source": source or "",
            "hit_count": 1,
        }

        # 冲突覆盖：同 type + 同 subject → 替换旧条目（topic 类型则计数累加）
        replaced = False
        for i, e in enumerate(entries):
            if e.get("type") == entry_type and (subject and e.get("subject") == subject):
                if entry_type == "topic":
                    e["hit_count"] = e.get("hit_count", 1) + 1
                    e["content"] = self._format_topic_content(subject, e["hit_count"])
                    e["created_at"] = now  # 最近提及时间
                    replaced = True
                    new_entry = e
                else:
                    entries[i] = new_entry
                    replaced = True
                break
        if not replaced:
            entries.append(new_entry)
        data["entries"] = entries
        self._save(user_id, data)
        return new_entry

    def _format_topic_content(self, subject: str, count: int) -> str:
        """topic 条目的计数内容格式。"""
        return f"关注话题：{subject}（已聊 {count} 次）"

    def _prune(self, data: Dict[str, Any]) -> int:
        """剔除 TTL 过期的记忆条目，返回剔除数量。"""
        entries = data.get("entries", [])
        if not entries:
            return 0
        now = datetime.now()
        kept = []
        removed = 0
        for e in entries:
            ttl = e.get("ttl_days")
            if ttl is None:
                kept.append(e)
                continue
            try:
                created = datetime.fromisoformat(e.get("created_at", ""))
            except (ValueError, TypeError):
                created = now
            if created + timedelta(days=int(ttl)) < now:
                removed += 1
            else:
                kept.append(e)
        data["entries"] = kept
        return removed

    def delete_entry(self, user_id: str, entry_id: str = "",
                     subject: str = "", entry_type: str = "") -> int:
        """删除记忆条目（隐私：用户可删除，方案 §5.5）。

        Args:
            user_id: 用户 ID
            entry_id: 条目 ID（优先）
            subject: 主题键（entry_id 为空时按 subject 删除）
            entry_type: 与 subject 组合精确匹配

        Returns:
            删除的条目数
        """
        data = self._load(user_id)
        entries: List[dict] = data.get("entries", [])
        if not entries:
            return 0
        kept = []
        removed = 0
        for e in entries:
            match = False
            if entry_id and e.get("id") == entry_id:
                match = True
            elif subject and e.get("subject") == subject:
                if not entry_type or e.get("type") == entry_type:
                    match = True
            if match:
                removed += 1
            else:
                kept.append(e)
        if removed:
            data["entries"] = kept
            self._save(user_id, data)
        return removed

    def clear_entries(self, user_id: str) -> int:
        """清空该用户全部结构化记忆条目，返回删除数量。"""
        data = self._load(user_id)
        entries = data.get("entries", [])
        n = len(entries)
        if n:
            data["entries"] = []
            self._save(user_id, data)
        return n

    def clear_all(self, user_id: str) -> int:
        """删除该用户整个记忆文件（隐私删除/注销，PIPL 第 47 条）。"""
        path = self._path(user_id)
        if os.path.exists(path):
            try:
                os.remove(path)
                return 1
            except OSError:
                return 0
        return 0

    def get_key_facts(self, user_id: str) -> List[str]:
        """获取标记为关键事实的记忆原文（L1 关键事实保底用，方案 §5.3）。

        返回内容列表；命中 TTL 过期的条目先剔除。
        """
        out = []
        for e in self.list_entries(user_id):
            if e.get("is_key") and e.get("content"):
                out.append(e["content"])
        return out

    def get_relevant_memories(self, user_id: str, query: str,
                              top_k: int = 5, embedder: Any = None,
                              threshold: float = 0.05) -> List[dict]:
        """按需召回：当前问题与哪些记忆相关（方案 §5.5）。

        记忆量小，不建索引：
        1. 关键词匹配为主——query 与条目 subject/content 的字面重合度
           （subject 精确命中加分；排除常见停用字）
        2. 可选 embedding 增强——传入 bge-m3 embedder（现有模型）时，
           计算 query 与条目内容的余弦相似度，加权合并排序

        Args:
            user_id: 用户 ID
            query: 当前问题文本
            top_k: 返回条数上限
            embedder: 可选，具备 encode_single(text)->np.ndarray 的 bge-m3 封装
            threshold: 最低得分过滤

        Returns:
            list of dicts: [{entry 字段..., score}]
        """
        if not query:
            return []
        entries = self.list_entries(user_id)
        if not entries:
            return []

        query_terms = self._keyword_terms(query)
        # embedding 增强（可选，失败静默降级为纯关键词）
        emb_query = None
        emb_sims: Dict[str, float] = {}
        if embedder is not None:
            try:
                emb_query = embedder.encode_single(query)
                if emb_query is not None:
                    import numpy as np
                    texts = [f"{e.get('subject','')} {e.get('content','')}" for e in entries]
                    mats = embedder.encode(texts)
                    if mats is not None:
                        q = np.asarray(emb_query, dtype=np.float64).reshape(1, -1)
                        m = np.asarray(mats, dtype=np.float64)
                        dots = (m @ q.T).reshape(-1)
                        norms = np.linalg.norm(m, axis=1) * np.linalg.norm(q)
                        sims = np.divide(dots, norms, out=np.zeros_like(dots),
                                         where=norms > 0)
                        # 归一化到 [0,1]
                        emb_sims = {e["id"]: float((s + 1.0) / 2.0)
                                    for s, e in zip(sims, entries)}
            except Exception:
                emb_sims = {}

        scored = []
        for e in entries:
            kw = self._keyword_score(query_terms, e)
            sim = emb_sims.get(e["id"])
            if sim is not None:
                score = 0.6 * kw + 0.4 * sim
            else:
                score = kw
            if score > 0:
                scored.append((score, e))

        scored.sort(key=lambda x: -x[0])
        out = []
        for score, e in scored[:top_k]:
            if score < threshold:
                continue
            item = dict(e)
            item["score"] = round(score, 4)
            out.append(item)
        return out

    def _keyword_terms(self, text: str) -> List[str]:
        """抽取查询关键词：2+ 字连续子串（跳过停用字打头/结尾）与 2+ 字母词。"""
        text = text.strip()
        if not text:
            return []
        terms = set()
        # 拉丁字母/数字词（如 "openai"、"2026"）
        for m in re.finditer(r"[A-Za-z0-9_]{2,}", text):
            terms.add(m.group(0).lower())
        # 中文：所有 2~4 字滑窗（滤掉含停用字开头的窗口，保留真实关键词重合度）
        cjk = [c for c in text if ord(c) > 0x2E80]
        for size in (4, 3, 2):
            for i in range(len(cjk) - size + 1):
                w = "".join(cjk[i:i + size])
                if w[0] not in self._STOP_CHARS and w[-1] not in self._STOP_CHARS:
                    terms.add(w)
        return list(terms)

    def _keyword_score(self, terms: List[str], entry: dict) -> float:
        """关键词匹配得分：命中术语数 / 术语总数（subject 命中加权）。"""
        if not terms:
            return 0.0
        hay_subject = entry.get("subject", "") or ""
        hay_content = entry.get("content", "") or ""
        hit = 0
        for t in terms:
            if t in hay_subject:
                hit += 2
            elif t in hay_content:
                hit += 1
        return hit / (len(terms) * 2.0)

    # ------------------------------------------------------------
    # P2 记忆升级（方案 v5 四层中 L2 事实条目 + 演化链）— 加密存储
    # ------------------------------------------------------------
    # 存储：记忆 JSON 文件内 fact_entries_enc / topic_evolution_enc 字段，
    # AES-256-GCM 整段加密（DataEncryptor，与 dao.py 同款密钥体系），
    # 文件其余字段保持明文（与既有记忆文件格式兼容，旧文件可正常读）。

    # L2 事实条目类型（方案 v5：profile 画像 / preference 偏好 / event 事件 / fact 一般事实）
    FACT_ENTRY_TYPES = ("profile", "preference", "event", "fact")
    # 默认 TTL（天）：方案 §三「条目默认 180 天，超期降权清理」
    FACT_DEFAULT_TTL_DAYS = 180

    def _load_enc_json(self, user_id: str, key: str) -> list:
        """读取加密 JSON 数组字段（无/解密失败 → []）。"""
        raw = self._load(user_id).get(key)
        if not raw:
            return []
        from src.security.encryption import DataEncryptor
        try:
            dec = DataEncryptor().decrypt(raw)
            if dec is None:
                return []
            data = json.loads(dec)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save_enc_json(self, user_id: str, key: str, items: list) -> None:
        """加密写入 JSON 数组字段。"""
        from src.security.encryption import DataEncryptor
        enc = DataEncryptor().encrypt(json.dumps(items, ensure_ascii=False))
        data = self._load(user_id)
        data[key] = enc
        self._save(user_id, data)

    def add_fact_entry(self, user_id: str, entry_type: str, content: str,
                       subject: str = "self", confidence: float = 1.0,
                       ttl_days: Optional[int] = None,
                       source_msg: str = "") -> dict:
        """写入一条 L2 原子事实条目（System1 每轮提取，方案 v5 阶段 5）。

        - 去重：同 type + 同 content 不重复加，仅刷新 updated_at/confidence
        - 出生信息走 persons 表，不通过此方法入库（handler 层过滤）
        - TTL：默认 180 天（方案 §三），过期条目读取时剔除

        Args:
            user_id: 用户 ID
            entry_type: profile / preference / event / fact
            content: 原子事实文本（如"性别：女"、"公司：字节跳动"）
            subject: self（本人）/ other（帮他人）
            confidence: 置信度 0-1
            ttl_days: 有效期（天），None 时按默认 180
            source_msg: 来源消息（原文片段，溯源用）

        Returns:
            dict: 条目（新建或已存在刷新后的）
        """
        if entry_type not in self.FACT_ENTRY_TYPES:
            entry_type = "fact"
        content = (content or "").strip()
        if not content:
            return {}
        if ttl_days is None:
            ttl_days = self.FACT_DEFAULT_TTL_DAYS
        now = datetime.now().isoformat()
        items = self._load_enc_json(user_id, "fact_entries_enc")
        # 去重：同 type + 同 content → 只刷新 updated_at（方案：同内容不重复加）
        for e in items:
            if e.get("type") == entry_type and e.get("content") == content:
                e["updated_at"] = now
                e["confidence"] = max(0.0, min(1.0, float(confidence)))
                if source_msg:
                    e["source_msg"] = source_msg[:500]
                self._save_enc_json(user_id, "fact_entries_enc", items)
                return e
        entry = {
            "id": uuid.uuid4().hex[:12],
            "type": entry_type,
            "subject": subject or "self",
            "content": content,
            "confidence": max(0.0, min(1.0, float(confidence))),
            "ttl_days": ttl_days,
            "created_at": now,
            "updated_at": now,
            "source_msg": (source_msg or "")[:500],
        }
        items.append(entry)
        self._save_enc_json(user_id, "fact_entries_enc", items)
        return entry

    def _prune_fact_entries(self, items: list) -> int:
        """剔除 TTL 过期的事实条目，返回剔除数量。"""
        if not items:
            return 0
        now = datetime.now()
        kept, removed = [], 0
        for e in items:
            ttl = e.get("ttl_days")
            if ttl is None:
                kept.append(e)
                continue
            try:
                created = datetime.fromisoformat(e.get("created_at", ""))
            except (ValueError, TypeError):
                created = now
            if created + timedelta(days=int(ttl)) < now:
                removed += 1
            else:
                kept.append(e)
        items[:] = kept
        return removed

    def list_fact_entries(self, user_id: str, entry_type: Optional[str] = None,
                          prune: bool = True) -> list:
        """列出 L2 事实条目（新→旧）；prune 时先剔除 TTL 过期条目。"""
        items = self._load_enc_json(user_id, "fact_entries_enc")
        if prune and self._prune_fact_entries(items):
            self._save_enc_json(user_id, "fact_entries_enc", items)
        if entry_type:
            items = [e for e in items if e.get("type") == entry_type]
        return sorted(items, key=lambda e: e.get("created_at", ""), reverse=True)

    def delete_fact_entry(self, user_id: str, fact_id: str) -> int:
        """删除单条 L2 事实条目（「明灯记得你」逐条删除）。"""
        if not fact_id:
            return 0
        items = self._load_enc_json(user_id, "fact_entries_enc")
        kept, removed = [], 0
        for e in items:
            if e.get("id") == fact_id:
                removed += 1
            else:
                kept.append(e)
        if removed:
            self._save_enc_json(user_id, "fact_entries_enc", kept)
        return removed

    def clear_fact_entries(self, user_id: str) -> int:
        """清空全部 L2 事实条目（「明灯记得你」一键清空；不动 persons/生辰）。"""
        items = self._load_enc_json(user_id, "fact_entries_enc")
        n = len(items)
        if n:
            self._save_enc_json(user_id, "fact_entries_enc", [])
        return n

    def count_fact_entries(self, user_id: str) -> int:
        """事实条目总数（管理 API 展示）。"""
        return len(self._load_enc_json(user_id, "fact_entries_enc"))

    # ------------------------------------------------------------
    # 演化链（方案 §三：同一主题多次咨询，结论+反馈串链）
    # ------------------------------------------------------------

    def add_evolution(self, user_id: str, topic: str, stance: str = "",
                      quote: str = "") -> None:
        """把一次咨询的结论摘要 append 到该 topic 的时间线（cap 5 条）。

        timeline 条目: {ts, stance, quote}；topic 无则新建。
        """
        if not topic:
            return
        quote = (quote or "").strip()[:160]
        if not quote and not stance:
            return
        data = self._load(user_id)
        raw = data.get("topic_evolution_enc")
        from src.security.encryption import DataEncryptor
        evos = {}
        if raw:
            try:
                dec = DataEncryptor().decrypt(raw)
                if dec:
                    parsed = json.loads(dec)
                    evos = parsed if isinstance(parsed, dict) else {}
            except Exception:
                evos = {}
        timeline = evos.setdefault(topic, [])
        timeline.append({
            "ts": datetime.now().isoformat(),
            "stance": (stance or "")[:80],
            "quote": quote,
        })
        if len(timeline) > 5:  # cap 5 条，保留最近
            timeline[:] = timeline[-5:]
        data["topic_evolution_enc"] = DataEncryptor().encrypt(
            json.dumps(evos, ensure_ascii=False))
        self._save(user_id, data)

    def get_evolutions(self, user_id: str) -> dict:
        """全部话题演化链: {topic: {count, last_at, timeline: [{ts, stance, quote}]}}。"""
        data = self._load(user_id)
        raw = data.get("topic_evolution_enc")
        if not raw:
            return {}
        from src.security.encryption import DataEncryptor
        try:
            dec = DataEncryptor().decrypt(raw)
            if not dec:
                return {}
            evos = json.loads(dec)
            if not isinstance(evos, dict):
                return {}
        except Exception:
            return {}
        out = {}
        for topic, timeline in evos.items():
            if not isinstance(timeline, list) or not timeline:
                continue
            out[topic] = {
                "count": len(timeline),
                "last_at": timeline[-1].get("ts", ""),
                "timeline": timeline,
            }
        return out

    def get_evolution(self, user_id: str, topic: str) -> list:
        """某话题的时间线（无则 []）。"""
        evos = self.get_evolutions(user_id)
        return (evos.get(topic) or {}).get("timeline", [])

    def format_evolution_hint(self, user_id: str, topic: str) -> str:
        """生成"过往咨询演变"提示（时间线 ≥2 条才注入）。

        返回如：
        【过往咨询演变】用户就「事业」咨询过多次：
        2026-06-03：…结论摘要…
        2026-08-09：…结论摘要…
        请结合其过往咨询的演变（立场/关注点变化）给出建议，
        可自然带出"您上次…这次…"，不机械罗列。
        """
        timeline = self.get_evolution(user_id, topic)
        if len(timeline) < 2:
            return ""
        cn = self.TOPIC_CN.get(topic, topic)
        lines = [f"【过往咨询演变】用户就「{cn}」咨询过多次："]
        for t in timeline[-5:]:
            ts = str(t.get("ts", ""))[:7]  # YYYY-MM
            quote = str(t.get("quote", "")).strip()
            if quote:
                lines.append(f"- {ts}：{quote[:80]}")
        lines.append("请结合其过往咨询的演变（立场/关注点变化）给出建议，"
                     "可自然带出「您上次…这次…」，不机械罗列。")
        return "\n".join(lines)

    # ------------------------------------------------------------
    # AI 原生（Phase 2）— 主题次数 / 活跃度 / 画像摘要（方案 2.2 / 5.5 / 6.1）
    # ------------------------------------------------------------

    def record_topic(self, user_id: str, topic: str) -> None:
        """记录一次主题对话（同一主题计数 +1），并更新 last_topic / last_visit。"""
        if not topic:
            return
        data = self._load(user_id)
        counts: Dict[str, int] = data.get("topic_counts") or {}
        counts[topic] = counts.get(topic, 0) + 1
        data["topic_counts"] = counts
        data["last_topic"] = topic
        data["last_visit"] = datetime.now().isoformat()
        self._save(user_id, data)

    def get_topic_count(self, user_id: str, topic: str) -> int:
        """获取某主题的累计出现次数（0 = 未聊过）。"""
        data = self._load(user_id)
        return (data.get("topic_counts") or {}).get(topic, 0)

    def get_recent_topics(self, user_id: str, limit: int = 3) -> List[str]:
        """按出现次数取最近关注的主题（中文名）。"""
        data = self._load(user_id)
        counts: Dict[str, int] = data.get("topic_counts") or {}
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
        return [self.TOPIC_CN.get(k, k) for k, _ in top]

    def get_profile_summary(self, user_id: str, max_topics: int = 3) -> str:
        """生成紧凑的用户画像摘要（供 LLM 生成个性化问候/上下文注入）。

        包含：八字（若已排盘）、上次主题、上次情绪、常聊主题（含次数）、来访次数。
        无记忆时返回空字符串。
        """
        data = self._load(user_id)
        if not data:
            return ""
        parts = []
        bazi = data.get("bazi_info")
        if bazi and bazi.get("bazi"):
            parts.append(f"八字已排盘: {' '.join(str(x) for x in bazi['bazi'][:4])}"
                         f"（日主 {bazi.get('day_master', '?')}）")
        # Task 1 排盘档案打通：有原始出生字段 → 补出生行（字段缺失部分不写）
        birth_line = format_birth_line(bazi)
        if birth_line:
            parts.append(birth_line)
        last_topic = data.get("last_topic", "")
        if last_topic:
            parts.append(f"上次主题: {self.TOPIC_CN.get(last_topic, last_topic)}")
        last_mood = self.get_last_mood(user_id)
        if last_mood:
            parts.append(f"上次情绪: {self.MOOD_CN.get(last_mood, last_mood)}")
        counts: Dict[str, int] = data.get("topic_counts") or {}
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:max_topics]
        if top:
            parts.append("常聊主题: " + "、".join(
                f"{self.TOPIC_CN.get(k, k)}({v}次)" for k, v in top))
        if data.get("last_visit"):
            parts.append("上次来访: " + data["last_visit"][:10])
        count = data.get("consultation_count", 0)
        if count:
            parts.append(f"累计咨询: {count}次")
        return "；".join(parts)

    def get_last_mood(self, user_id: str) -> Optional[str]:
        """获取用户最近一次情绪状态。

        Returns:
            最近的情绪标签，如果没有记录返回 None
        """
        data = self._load(user_id)
        mood_history = data.get("mood_history", [])
        if mood_history:
            return mood_history[-1].get("mood")
        return None

    def get_mood_summary(self, user_id: str) -> str:
        """生成情绪摘要。

        Returns:
            中文摘要字符串，如 "最近情绪波动较大" 或 "状态平稳"
        """
        data = self._load(user_id)
        mood_history = data.get("mood_history", [])
        if not mood_history:
            return ""

        # Count mood types
        mood_counts: Dict[str, int] = {}
        for record in mood_history[-10:]:  # Last 10 entries
            mood = record.get("mood", "")
            if mood:
                mood_counts[mood] = mood_counts.get(mood, 0) + 1

        if not mood_counts:
            return ""

        # Find dominant mood
        dominant = max(mood_counts, key=mood_counts.get)

        mood_labels = {
            "heartbreak": "感情困扰",
            "sadness": "低落",
            "anxiety": "焦虑",
            "confusion": "迷茫",
            "anger": "烦躁",
            "happy": "不错",
            "neutral": "平稳",
        }

        label = mood_labels.get(dominant, dominant)
        return f"最近情绪{label}"

    def get_greeting(self, user_id: str) -> str:
        """生成个性化的欢迎语。

        根据用户记忆生成合适的问候语。
        如果用户没有记忆，返回空字符串。

        Returns:
            个性化问候语，如 "上次聊到你的感情问题，最近好点了吗？"
        """
        data = self._load(user_id)
        if not data:
            return ""

        last_topic = data.get("last_topic", "")
        last_mood = self.get_last_mood(user_id)
        concerns = data.get("concerns", [])
        consultation_count = data.get("consultation_count", 0)

        if consultation_count == 0:
            return ""

        # Mood-aware greeting
        if last_mood in ("heartbreak", "sadness", "anxiety", "confusion", "anger"):
            return "最近感觉好点了吗？"

        # Topic-aware greeting
        if last_topic:
            topic_greetings = {
                "事业": "工作上有新进展吗？",
                "工作": "工作上有新进展吗？",
                "感情": "感情方面最近怎么样？",
                "健康": "身体好点了吗？",
                "财运": "财运方面有什么新变化吗？",
            }
            for keyword, greeting in topic_greetings.items():
                if keyword in last_topic:
                    return greeting

            # Generic topic reference
            return f"上次聊到你的{last_topic}，最近有变化吗？"

        # Last resort: generic greeting
        return "今天想聊点什么？"
