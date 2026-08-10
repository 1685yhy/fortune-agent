#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M9 话术引擎评测系统 — 四层评测 + 动态题库
==========================================

话术引擎上线前量化验收 / 上线后每周追踪。

评测四层：
  L1 情绪回应    — 接住度 0-5（先回应情绪信号）+ 温度 0-5（有人情味不机械）
  L2 语言质量    — 口语度 0-5 + 比喻/金句 0-5 + 截图友好 0-5（短句分点可截图）
  L3 专业质量    — 术语翻译率 0-5 + 证据感 0-5 + 不绝对 0-5（无绝对预测/吓人）
  L4 真实对话抽检— 对真实用户对话（日志导出/内置真实语料）按同一评分表抽检

总分（20 制加权）：L1×2 + L2×1.5 + L3×1.5（各层取层内维度均值，原始满分 25，
归一化到 20 制）。达标线：总分(20制) ≥ 16 且 L1 ≥ 4（共情是底线）。

用法：
  python scripts/tone_eval.py                        # 评测全部样本（默认 target=api）
  python scripts/tone_eval.py --target local         # 直接调 handler.process
  python scripts/tone_eval.py --limit 5 --category 失眠
  python scripts/tone_eval.py --list                 # 查看当前题库
  python scripts/tone_eval.py --add-sample "今天好累" --category 受挫 --intensity 0.6
  python scripts/tone_eval.py --export 报告.md       # 导出评测报告
  python scripts/tone_eval.py --l4                   # L4 真实对话抽检

被测引擎：
  --target api   调用 http://127.0.0.1:8767/api/chat  POST {message, user_id}
  --target local 本地装配 MessageHandler，直接 handler.process(message, user_id)

评卷 LLM：
  DeepSeek API（key 取 settings.claude_api_key，可用 --eval-api-key 或
  环境变量 DEEPSEEK_API_KEY 覆盖），对每份回复按评分标准打分（JSON 输出）。

独立脚本：不修改 src/ 下任何文件。
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

# 让 local 模式能 import src.*（脚本位于 scripts/ 下）
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

EVAL_SET_FILE = PROJECT_DIR / "data" / "eval" / "eval_set.json"
REAL_DIALOGUES_FILE = PROJECT_DIR / "data" / "eval" / "real_dialogues.jsonl"
RESULTS_DIR = PROJECT_DIR / "data" / "eval" / "results"

DEFAULT_API_URL = "http://127.0.0.1:8767/api/chat"
DEEPSEEK_ENDPOINT = "https://api.deepseek.com/v1/chat/completions"
DEFAULT_EVAL_MODEL = "deepseek-v4-flash"

# 达标线：总分(20制) ≥ 16 且 L1(情绪回应) ≥ 4
PASS_TOTAL_20 = 16.0
PASS_L1 = 4.0

# 每维满分
DIM_MAX = 5.0
# 加权：L1×2 + L2×1.5 + L3×1.5（原始满分 25，归一化到 20 制）
WEIGHT_L1, WEIGHT_L2, WEIGHT_L3 = 2.0, 1.5, 1.5
RAW_MAX = WEIGHT_L1 * DIM_MAX + WEIGHT_L2 * DIM_MAX + WEIGHT_L3 * DIM_MAX  # 25.0

# ---------------------------------------------------------------------------
# 评分维度定义（评卷 LLM 提示词直接引用）
# ---------------------------------------------------------------------------
DIMENSIONS: List[Dict[str, str]] = [
    {"key": "l1_receive", "layer": "L1", "name": "接住度",
     "desc": "是否先回应情绪信号再接内容",
     "high": "5=先稳稳接住情绪再展开分析，甚至给出具体安抚动作；4=先回应情绪再讲内容",
     "mid": "2=只简单提一句情绪，主要精力在讲道理/分析",
     "low": "0=无视情绪信号，直接开算、说教、下判断"},
    {"key": "l1_warmth", "layer": "L1", "name": "温度",
     "desc": "是否有人情味、不机械",
     "high": "5=温暖自然像朋友，不油腻不套路；4=明显有人情味",
     "mid": "2=礼貌但平淡，客服腔",
     "low": "0=机械冰冷，模板味，像系统提示"},
    {"key": "l2_colloquial", "layer": "L2", "name": "口语度",
     "desc": "像人说话，不端着",
     "high": "5=鲜活口语像真人聊天；4=自然口语",
     "mid": "2=中规中矩，书面味偏重",
     "low": "0=官腔/书面语/AI 说明文腔"},
    {"key": "l2_metaphor", "layer": "L2", "name": "比喻/金句",
     "desc": "是否有贴切比喻或可分享的金句",
     "high": "5=有可转发金句或非常贴切的比喻；4=有亮眼比喻",
     "mid": "2=偶有一点亮点，总体平铺直叙",
     "low": "0=全程平铺直叙，无任何亮点"},
    {"key": "l2_screenshot", "layer": "L2", "name": "截图友好",
     "desc": "短句分点，适合截图转发",
     "high": "5=短句+分点+适度符号，天然适合截图；4=短句分点清晰",
     "mid": "2=长段落，勉强可读",
     "low": "0=大段文字堆砌，无断句无重点"},
    {"key": "l3_terms", "layer": "L3", "name": "术语翻译",
     "desc": "出现术语是否用大白话解释",
     "high": "5=术语都自然翻译成大白话；4=关键术语必配白话解释",
     "mid": "2=用了术语，偶尔才解释",
     "low": "0=术语堆砌，不给解释"},
    {"key": "l3_evidence", "layer": "L3", "name": "证据感",
     "desc": "是否有古籍/知识引用，不空口断言",
     "high": "5=引用准确且自然融入（如\"《周易》云\"），或明确\"传统命理认为\"；4=有出处感的知识引用",
     "mid": "2=偶有知识点，多数为泛泛而谈",
     "low": "0=空口断言、拍脑袋结论",
     "note": "纯闲聊/情绪样本不需要引用：不强行塞引用给 4-5 分基准，强塞引用反而降分"},
    {"key": "l3_no_absolutes", "layer": "L3", "name": "不绝对",
     "desc": "无绝对预测、不吓人",
     "high": "5=留有余地，有\"仅供参考/最终决定在你\"意识，不吓人；4=语气留有分寸",
     "mid": "2=语气偏绝对，或轻微制造焦虑",
     "low": "0=铁口直断（\"你必离婚\"\"大凶\"）或恐吓式言论"},
]

LAYER_DIMS = {"L1": ["l1_receive", "l1_warmth"],
              "L2": ["l2_colloquial", "l2_metaphor", "l2_screenshot"],
              "L3": ["l3_terms", "l3_evidence", "l3_no_absolutes"]}
LAYER_WEIGHTS = {"L1": WEIGHT_L1, "L2": WEIGHT_L2, "L3": WEIGHT_L3}

# ---------------------------------------------------------------------------
# 种子题库（首次运行 / 文件缺失时自动写入）
# ---------------------------------------------------------------------------
SEED_SAMPLES: List[Dict[str, Any]] = [
    # ---- 失眠（情绪场景） ----
    {"id": "insomnia_01", "category": "失眠", "domain": None,
     "text": "最近老是失眠，凌晨两三点醒了就睡不着，脑子里全是第二天的事，翻来覆去到天亮。我是不是身体有什么毛病啊？",
     "intensity": 0.7, "expect_terms": []},
    {"id": "insomnia_02", "category": "失眠", "domain": None,
     "text": "又失眠了，连续第五天了。白天困得要死晚上精神得很，烦死了。",
     "intensity": 0.6, "expect_terms": []},
    {"id": "insomnia_03", "category": "失眠", "domain": None,
     "text": "我睡不着，越想睡越睡不着，是不是我命里带什么煞？还是卧室风水有问题？",
     "intensity": 0.65, "expect_terms": ["风水", "煞"]},
    # ---- 分手（情绪场景） ----
    {"id": "breakup_01", "category": "分手", "domain": None,
     "text": "他昨天跟我说分手了，谈了三年，说走就走。我现在吃不下睡不着，觉得自己什么都做不好……",
     "intensity": 0.9, "expect_terms": []},
    {"id": "breakup_02", "category": "分手", "domain": None,
     "text": "分手三个月了还是走不出来，看到以前的东西就哭。我是不是很没用？",
     "intensity": 0.85, "expect_terms": []},
    {"id": "breakup_03", "category": "分手", "domain": None,
     "text": "我把他微信删了，今天他又来加我。我到底要不要通过？好烦。",
     "intensity": 0.6, "expect_terms": []},
    # ---- 受挫（情绪场景） ----
    {"id": "setback_01", "category": "受挫", "domain": None,
     "text": "面试又被拒了，这是今年第五次了。是不是我八字就不适合这份工作？要不要认命？",
     "intensity": 0.75, "expect_terms": ["八字"]},
    {"id": "setback_02", "category": "受挫", "domain": None,
     "text": "努力了一年，业绩还是垫底，领导天天给我脸色看。我感觉自己就是个废物。",
     "intensity": 0.8, "expect_terms": []},
    {"id": "setback_03", "category": "受挫", "domain": None,
     "text": "考研二战又没过，差两分。爸妈嘴上不说，我知道他们失望。我该继续吗？",
     "intensity": 0.85, "expect_terms": []},
    # ---- 分享（情绪场景） ----
    {"id": "share_01", "category": "分享", "domain": None,
     "text": "今天升职了！！终于熬出头了，请允许我开心一下哈哈哈哈",
     "intensity": 0.3, "expect_terms": []},
    {"id": "share_02", "category": "分享", "domain": None,
     "text": "跟你说个好消息，我昨天买彩票中了500块，虽然不多但就是开心！",
     "intensity": 0.25, "expect_terms": []},
    # ---- 生日（情绪场景） ----
    {"id": "birthday_01", "category": "生日", "domain": None,
     "text": "今天是我生日，一个人在外面，没人记得。唉。",
     "intensity": 0.7, "expect_terms": []},
    {"id": "birthday_02", "category": "生日", "domain": None,
     "text": "明天生日，还是本命年，总觉得要倒霉一年，有什么办法避一避吗？",
     "intensity": 0.5, "expect_terms": ["本命年"]},
    # ---- 专业问题 · 八字 ----
    {"id": "prof_bazi_01", "category": "专业问题", "domain": "八字",
     "text": "师傅，我1993年农历二月十四出生，男，帮我看看我什么时候能遇到正缘？",
     "intensity": 0.3, "expect_terms": ["正缘"]},
    {"id": "prof_bazi_02", "category": "专业问题", "domain": "八字",
     "text": "我日主是戊土，网上说我是财多身弱，容易破财留不住钱，真的假的？",
     "intensity": 0.4, "expect_terms": ["财多身弱"]},
    # ---- 专业问题 · 紫微 ----
    {"id": "prof_ziwei_01", "category": "专业问题", "domain": "紫微",
     "text": "我紫微盘迁移宫有天同星，是不是代表我适合去外地发展？",
     "intensity": 0.3, "expect_terms": ["天同", "迁移宫"]},
    {"id": "prof_ziwei_02", "category": "专业问题", "domain": "紫微",
     "text": "帮我看看我的紫微命盘，子女宫好像没有主星，是不是我这辈子没孩子命？",
     "intensity": 0.5, "expect_terms": ["紫微"]},
    # ---- 专业问题 · 解梦 ----
    {"id": "prof_dream_01", "category": "专业问题", "domain": "解梦",
     "text": "昨晚梦见自己掉牙齿，一颗一颗掉光，还流血，直接吓醒了。这梦什么意思啊？",
     "intensity": 0.55, "expect_terms": ["掉牙", "周公解梦"]},
    {"id": "prof_dream_02", "category": "专业问题", "domain": "解梦",
     "text": "梦见被蛇追，怎么跑都跑不动，是什么意思？我最近在找工作，有关系吗？",
     "intensity": 0.45, "expect_terms": ["解梦"]},
    # ---- 闲聊 ----
    {"id": "chat_01", "category": "闲聊", "domain": None,
     "text": "在吗？今天天气真好，适合干点啥？",
     "intensity": 0.1, "expect_terms": []},
    {"id": "chat_02", "category": "闲聊", "domain": None,
     "text": "你到底是算命大师还是AI？能告诉我实话吗",
     "intensity": 0.15, "expect_terms": []},
    {"id": "chat_03", "category": "闲聊", "domain": None,
     "text": "最近歌荒了，给我推荐首歌呗",
     "intensity": 0.1, "expect_terms": []},
]

# L4 真实对话抽检种子（真实用户输入，取自 golden_200 真实用户语料库；
# reply 字段留空 = 运行时由被测引擎生成后评分，也可填入真实记录做离线抽检）
REAL_SEED: List[Dict[str, Any]] = [
    {"id": "real_01", "category": "解梦", "domain": "解梦", "source": "golden_200/dream_L1_047",
     "text": "我91年的羊，连续好几个晚上梦见自己从悬崖掉下去，每次都在半空中挣扎醒来，太难受了，这是什么意思？",
     "intensity": 0.7, "expect_terms": [], "reply": None},
    {"id": "real_02", "category": "解梦", "domain": "解梦", "source": "golden_200/dream_L1_023",
     "text": "我1995年生，女，总梦到在考试，要么迟到要么交白卷，醒来心慌，是不是运势不好？",
     "intensity": 0.65, "expect_terms": [], "reply": None},
    {"id": "real_03", "category": "解梦", "domain": "解梦", "source": "golden_200/dream_L1_051",
     "text": "我82年属狗，梦见自己在一个坟地里走不出去，四周都是墓碑，阴森森的，醒来一身冷汗，这梦是什么意思？",
     "intensity": 0.8, "expect_terms": [], "reply": None},
    {"id": "real_04", "category": "专业问题", "domain": "八字", "source": "golden_200/bazi_L1_093",
     "text": "属马女，1990年，在单位五年没动了，明年能不能有升职的机会？",
     "intensity": 0.45, "expect_terms": [], "reply": None},
    {"id": "real_05", "category": "专业问题", "domain": "八字", "source": "golden_200/bazi_L1_010",
     "text": "师傅，我孩子2016年属猴的，身子骨弱，要怎么调理？",
     "intensity": 0.5, "expect_terms": [], "reply": None},
    {"id": "real_06", "category": "受挫", "domain": "八字", "source": "golden_200/bazi_L1_085",
     "text": "2000年属龙，女生，最近工作老是被领导骂，事业不顺，什么时候能好转？",
     "intensity": 0.75, "expect_terms": [], "reply": None},
    {"id": "real_07", "category": "受挫", "domain": "八字", "source": "golden_200/bazi_L1_096",
     "text": "师傅，我1969年属鸡，男，这两年身体毛病没断过，小问题不断，是哪年冲的吗？",
     "intensity": 0.55, "expect_terms": [], "reply": None},
    {"id": "real_08", "category": "闲聊", "domain": "八字", "source": "golden_200/bazi_L1_115",
     "text": "我八字里比肩多，是不是朋友多？",
     "intensity": 0.15, "expect_terms": [], "reply": None},
]


# ---------------------------------------------------------------------------
# 题库读写
# ---------------------------------------------------------------------------
def ensure_eval_set() -> Path:
    """题库文件不存在时写入种子题库；返回文件路径。"""
    EVAL_SET_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not EVAL_SET_FILE.exists():
        write_eval_set(SEED_SAMPLES)
        print(f"[init] 已创建种子题库: {EVAL_SET_FILE}（{len(SEED_SAMPLES)} 条样本）")
    return EVAL_SET_FILE


def load_eval_set() -> List[Dict[str, Any]]:
    ensure_eval_set()
    data = json.loads(EVAL_SET_FILE.read_text(encoding="utf-8"))
    samples = data.get("samples", data) if isinstance(data, dict) else data
    return samples


def write_eval_set(samples: List[Dict[str, Any]]) -> None:
    payload = {
        "version": 1,
        "description": "M9 话术引擎四层评测题库（L1 情绪回应 / L2 语言质量 / L3 专业质量 / L4 真实对话抽检）",
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sample_count": len(samples),
        "samples": samples,
    }
    EVAL_SET_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def add_sample(text: str, category: str, intensity: float,
               domain: Optional[str] = None,
               expect_terms: Optional[List[str]] = None) -> Dict[str, Any]:
    """动态题库：向 eval_set.json 追加一条样本并保存。"""
    samples = load_eval_set()
    prefix = category if category else "sample"
    seq = 1
    ids = {s.get("id") for s in samples}
    while f"{prefix}_{seq:02d}" in ids:
        seq += 1
    sample = {
        "id": f"{prefix}_{seq:02d}",
        "category": category,
        "domain": domain,
        "text": text,
        "intensity": float(intensity),
        "expect_terms": expect_terms or [],
    }
    samples.append(sample)
    write_eval_set(samples)
    return sample


# ---------------------------------------------------------------------------
# 被测引擎
# ---------------------------------------------------------------------------
class Engine:
    name = "base"

    def generate(self, text: str, user_id: str) -> str:
        raise NotImplementedError


class APIEngine(Engine):
    """--target api：POST http://127.0.0.1:8767/api/chat  {message, user_id}"""

    name = "api"

    def __init__(self, api_url: str = DEFAULT_API_URL, timeout: float = 180.0):
        self.api_url = api_url
        self.timeout = timeout

    def generate(self, text: str, user_id: str) -> str:
        resp = httpx.post(
            self.api_url,
            json={"message": text, "user_id": user_id},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        reply = data.get("reply")
        if not reply:
            raise RuntimeError(f"API 返回无 reply 字段: {str(data)[:200]}")
        return reply


class LocalEngine(Engine):
    """--target local：本地装配 MessageHandler，直接 handler.process(message, user_id)

    装配逻辑镜像 src/main.py 的 lifespan（各组件失败仅告警不阻断），
    需要完整本地环境（引擎/向量库/DB）。"""

    name = "local"

    def __init__(self, api_key: Optional[str] = None):
        self._handler = None
        self._api_key = api_key

    def _build(self):
        import logging
        logging.basicConfig(level=logging.WARNING)

        from src.config import load_settings
        from src.engines.bazi import BaziEngine
        from src.engines.ziwei import ZiweiEngine
        from src.engines.liuyao import LiuyaoEngine
        from src.engines.fengshui import FengshuiEngine
        from src.engines.mianxiang import MianxiangEngine
        from src.engines.zeri import ZeriEngine
        from src.engines.dream import DreamEngine
        from src.engines.hehun import HehunEngine
        from src.engines.qimen import QimenEngine
        from src.engines.xingming import XingmingEngine
        from src.rag.embedder import Embedder
        from src.rag.retriever import Retriever
        from src.llm.client import FortuneLLM
        from src.bot.handler import MessageHandler
        from src.storage.dao import UserDAO
        from src.storage.member_dao import MemberDAO
        from src.storage.session_dao import SessionDAO

        settings = load_settings()
        api_key = self._api_key or settings.claude_api_key

        engine = BaziEngine()
        ziwei = ZiweiEngine()
        liuyao = LiuyaoEngine()
        fengshui = FengshuiEngine()
        mianxiang = MianxiangEngine()
        zeri = ZeriEngine()
        dream = DreamEngine()
        hehun = HehunEngine()
        qimen = QimenEngine()
        xingming = XingmingEngine()

        embedder = Embedder(model_name=settings.embedding_model)
        # 本仓库为 ChromaDB 检索器（search 用 query_texts 自动嵌入，无需加载模型）
        # 评测使用有数据的真实向量库：vectordb_v2/fortune_books
        retriever = Retriever("/mnt/d/fortune-data/vectordb_v2", embedder)
        retriever._collection_name = "fortune_books"

        dao = None
        try:
            dao = UserDAO(str(settings.db_path))
        except Exception as e:
            print(f"[local] 警告: UserDAO 初始化失败({e})，将用 None 代替")

        session_dao = None
        member_dao = None
        if dao:
            try:
                session_dao = SessionDAO(str(settings.db_path))
                member_dao = MemberDAO(str(settings.db_path))
            except Exception as e:
                print(f"[local] 警告: SessionDAO/MemberDAO 初始化失败({e})")

        llm = FortuneLLM(api_key=api_key, model="deepseek-v4-flash",
                         deep_model="deepseek-v4-flash", provider="deepseek")

        self._handler = MessageHandler(
            engine, ziwei, liuyao, fengshui, mianxiang, zeri,
            retriever, llm, dao, dream_engine=dream,
            hehun_engine=hehun, qimen_engine=qimen, xingming_engine=xingming,
            session_dao=session_dao, member_dao=member_dao,
        )
        # 评测用户提升额度（含续期，避免过期 downgrade 回免费 3 次/日）
        if member_dao:
            try:
                conn = member_dao._connect()
                conn.execute(
                    """INSERT INTO memberships
                       (user_id, plan, started_at, expires_at, queries_used, queries_limit, auto_renew)
                       VALUES ('eval_m9', 'basic', datetime('now'), datetime('now','+30 days'), 0, 500, 0)
                       ON CONFLICT(user_id) DO UPDATE SET plan='basic', queries_limit=500,
                           started_at=datetime('now'), expires_at=datetime('now','+30 days'), auto_renew=0""",
                )
                conn.commit()
                conn.close()
            except Exception:
                pass
        print(f"[local] MessageHandler 装配完成 (api_key={'已配置' if api_key else '缺失'})")
        return self._handler

    def generate(self, text: str, user_id: str) -> str:
        if self._handler is None:
            self._build()
        return self._handler.process(text, user_id)


def make_engine(target: str, api_url: str, api_key: Optional[str]) -> Engine:
    if target == "api":
        return APIEngine(api_url=api_url)
    if target == "local":
        return LocalEngine(api_key=api_key)
    raise ValueError(f"未知 target: {target}")


# ---------------------------------------------------------------------------
# 评卷 LLM（DeepSeek）
# ---------------------------------------------------------------------------
class Judge:
    """DeepSeek 评卷器：对一份回复按 8 个维度打分（JSON 输出）。"""

    def __init__(self, api_key: str, model: str = DEFAULT_EVAL_MODEL):
        self.api_key = api_key
        self.model = model

    # ---- 提示词 ----
    @staticmethod
    def _rubric_block() -> str:
        lines = []
        for d in DIMENSIONS:
            lines.append(f"- {d['name']}({d['key']}, 0-5): {d['desc']}")
            lines.append(f"    高分: {d['high']}；中分: {d['mid']}；低分: {d['low']}")
            if d.get("note"):
                lines.append(f"    注意: {d['note']}")
        return "\n".join(lines)

    def _prompt(self, sample: Dict[str, Any], reply: str) -> str:
        category = sample.get("category", "")
        domain = sample.get("domain") or ""
        intensity = sample.get("intensity", 0.5)
        expect = sample.get("expect_terms") or []
        expect_line = f"期望要点（回复命中其中要点可加分）: {', '.join(expect)}" if expect else ""
        return f"""你是「易理明灯」AI 命理助手的话术评测员。请对下面的 AI 回复按 8 个维度打分（0-5 整数或 0.5 步进）。

【评测背景】
- 样本类别: {category}{(' / ' + domain) if domain else ''}
- 用户情绪强度: {intensity}（0=平静, 1=极度波动）
{expect_line}

【用户输入】
{sample.get('text', '')}

【AI 回复】
{reply}

【评分标准】
{self._rubric_block()}

【规则】
1. 只评话术质量，不评命理数据对错（但铁口直断/恐吓在\"不绝对\"维度扣分）。
2. 对闲聊/情绪类样本，不需要引用古籍；\"证据感\"按\"该引用时是否引用、不该用时是否强塞\"评分。
3. 严格输出如下 JSON（不要输出任何其他文字）:
{{"dimensions": {{"l1_receive": 0-5, "l1_warmth": 0-5, "l2_colloquial": 0-5, "l2_metaphor": 0-5, "l2_screenshot": 0-5, "l3_terms": 0-5, "l3_evidence": 0-5, "l3_no_absolutes": 0-5}}, "rationale": "80字以内中文点评"}}"""

    # ---- 调用 ----
    def grade(self, sample: Dict[str, Any], reply: str) -> Optional[Dict[str, Any]]:
        """返回 {dims: {key: score}, rationale: str}，失败返回 None（自动重试一次）。"""
        for attempt in (1, 2):
            try:
                resp = httpx.post(
                    DEEPSEEK_ENDPOINT,
                    headers={"Authorization": f"Bearer {self.api_key}",
                             "Content-Type": "application/json"},
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": self._prompt(sample, reply)}],
                        # deepseek-v4-flash 是推理模型：reasoning_content 会吃掉大量
                        # token，预算不足会出现 finish_reason=length 且 content 为空，
                        # 所以 max_tokens 要给足（推理 + 最终 JSON）
                        "max_tokens": 2500,
                        "temperature": 0.2,
                        "response_format": {"type": "json_object"},
                    },
                    timeout=120.0,
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                parsed = parse_judge_json(content)
                if parsed:
                    return parsed
                print(f"    [judge] 第{attempt}次评分 JSON 解析失败，重试...")
            except Exception as e:
                print(f"    [judge] 第{attempt}次调用失败: {e}")
            time.sleep(2)
        return None


def _regex_dims(text: str) -> Optional[Dict[str, float]]:
    """兜底：JSON 整体损坏（如 rationale 里有未转义引号/换行）时，
    用正则逐个抽取维度分数（0-5 数字），8 个维度缺一不可。"""
    scores = {}
    for d in DIMENSIONS:
        key = d["key"]
        m = re.search(r'"' + re.escape(key) + r'"\s*:\s*([0-9]+(?:\.[0-9]+)?)', text)
        if not m:
            return None
        scores[key] = max(0.0, min(5.0, float(m.group(1))))
    return scores


def parse_judge_json(text: str) -> Optional[Dict[str, Any]]:
    """鲁棒解析评卷 JSON：剥代码围栏、截取首尾花括号、正则兜底。"""
    if not text:
        return None
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1)
    start, end = t.find("{"), t.rfind("}")
    obj = None
    if start != -1 and end > start:
        try:
            obj = json.loads(t[start:end + 1])
        except json.JSONDecodeError:
            obj = None
    if isinstance(obj, dict) and isinstance(obj.get("dimensions"), dict):
        dims = obj["dimensions"]
        scores = {}
        for d in DIMENSIONS:
            key = d["key"]
            v = dims.get(key)
            try:
                v = float(v)
            except (TypeError, ValueError):
                scores = None
                break
            scores[key] = max(0.0, min(5.0, v))
        if scores:
            return {"dims": scores, "rationale": str(obj.get("rationale", ""))[:200]}
    # 正则兜底：维度分数是简单数字，即使 JSON 被 rationale 里的引号/换行破坏也能提取
    scores = _regex_dims(t)
    if scores is None:
        return None
    m = re.search(r'"rationale"\s*:\s*"((?:[^"\\]|\\.)*)"', t)
    rationale = m.group(1) if m else ""
    return {"dims": scores, "rationale": rationale[:200]}


# ---------------------------------------------------------------------------
# 汇总计算
# ---------------------------------------------------------------------------
def compute_layer_scores(dims: Dict[str, float]) -> Dict[str, float]:
    """返回 {L1, L2, L3, weighted(满分25), total_20(满分20)}"""
    layers = {k: sum(dims[kv] for kv in v) / len(v) for k, v in LAYER_DIMS.items()}
    weighted = sum(layers[k] * LAYER_WEIGHTS[k] for k in layers)
    total_20 = round(weighted * 20.0 / RAW_MAX, 1)
    return {"L1": layers["L1"], "L2": layers["L2"], "L3": layers["L3"],
            "weighted": round(weighted, 1), "total_20": total_20}


def pass_verdict(scores: Dict[str, float], l1: float) -> bool:
    return scores["total_20"] >= PASS_TOTAL_20 and l1 >= PASS_L1


def match_expect_terms(sample: Dict[str, Any], reply: str) -> List[str]:
    return [t for t in (sample.get("expect_terms") or []) if t and t in reply]


# ---------------------------------------------------------------------------
# 评测主流程
# ---------------------------------------------------------------------------
def run_eval(engine: Engine, judge: Judge, samples: List[Dict[str, Any]],
             user_id: str, l4_mode: bool = False) -> List[Dict[str, Any]]:
    results = []
    total = len(samples)
    for i, sample in enumerate(samples, 1):
        sid = sample.get("id", f"sample_{i}")
        cat = sample.get("category", "")
        print(f"[{i:02d}/{total:02d}] {cat} {sid} ", end="", flush=True)

        # 取回复：L4 离线样本有记录回复则直接用，否则调被测引擎
        reply = sample.get("reply")
        engine_error = False
        error_msg = ""
        if not reply:
            try:
                reply = engine.generate(sample.get("text", ""), user_id)
                if l4_mode:
                    sample["reply"] = reply  # 回写，便于导出
            except Exception as e:
                # 注意: 不能在 except 块外再引用 e（Python 会在块结束时清除它）
                error_msg = str(e)
                print(f"引擎失败: {error_msg}")
                engine_error = True
                reply = ""
        else:
            print("(使用记录回复) ", end="")

        if engine_error:
            results.append({**sample, "reply": f"[引擎调用失败] {error_msg}",
                            "engine_error": error_msg, "graded": False})
            print("→ ENGINE_FAIL")
            continue

        print(f"回复{len(reply)}字 ", end="")
        verdict = judge.grade(sample, reply)
        if verdict is None:
            results.append({**sample, "reply": reply, "graded": False})
            print("→ JUDGE_FAIL")
            continue
        dims = verdict["dims"]
        layers = compute_layer_scores(dims)
        hit = match_expect_terms(sample, reply)
        results.append({**sample, "reply": reply, "graded": True,
                        "dims": dims, "layers": layers,
                        "rationale": verdict["rationale"],
                        "expect_hit": hit})
        mark = "PASS" if pass_verdict(layers, layers["L1"]) else "FAIL"
        print(f"L1={layers['L1']:.1f} L2={layers['L2']:.1f} L3={layers['L3']:.1f} "
              f"总分={layers['total_20']:.1f} [{mark}]")
    return results


# ---------------------------------------------------------------------------
# 输出：控制台表格
# ---------------------------------------------------------------------------
def _w(s: str) -> int:
    """显示宽度（CJK 记 2）"""
    return sum(2 if ord(c) > 127 else 1 for c in s)


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _w(s))


def print_table(results: List[Dict[str, Any]], title: str = "评测明细") -> None:
    graded = [r for r in results if r.get("graded")]
    failed = [r for r in results if not r.get("graded")]
    print(f"\n===== {title} =====")
    if not graded:
        print("无有效评分样本")
        return
    header = ["id", "类别", "L1接", "L1温", "L2口", "L2比", "L2截", "L3术", "L3证", "L3不",
              "L1", "L2", "L3", "20制", "判定"]
    widths = [_w("L1接") + 1, _w("L1温") + 1, _w("L2口") + 1, _w("L2比") + 1, _w("L2截") + 1,
              _w("L3术") + 1, _w("L3证") + 1, _w("L3不") + 1]
    colw = [_w("sample_99") + 1, _w("专业问题") + 1] + widths + [4, 4, 4, 5, 5]
    print("  ".join(_pad(h, w) for h, w in zip(header, colw)).rstrip())
    for r in graded:
        d, l = r["dims"], r["layers"]
        verdict = "PASS" if pass_verdict(l, l["L1"]) else "FAIL"
        row = [r.get("id", "?"), r.get("category", ""),
               f"{d['l1_receive']:.1f}", f"{d['l1_warmth']:.1f}",
               f"{d['l2_colloquial']:.1f}", f"{d['l2_metaphor']:.1f}", f"{d['l2_screenshot']:.1f}",
               f"{d['l3_terms']:.1f}", f"{d['l3_evidence']:.1f}", f"{d['l3_no_absolutes']:.1f}",
               f"{l['L1']:.1f}", f"{l['L2']:.1f}", f"{l['L3']:.1f}",
               f"{l['total_20']:.1f}", verdict]
        print("  ".join(_pad(str(c), w) for c, w in zip(row, colw)).rstrip())
    if failed:
        print(f"\n未评分 {len(failed)} 条: "
              + ", ".join(r.get("id", "?") for r in failed)
              + "（引擎失败或评卷失败，详见报告）")


def print_summary(results: List[Dict[str, Any]]) -> None:
    graded = [r for r in results if r.get("graded")]
    print("\n===== 汇总 =====")
    if not graded:
        print("无有效评分，无法汇总")
        return

    # 总体
    keys = [d["key"] for d in DIMENSIONS]
    means = {k: sum(r["dims"][k] for r in graded) / len(graded) for k in keys}
    l1 = sum(r["layers"]["L1"] for r in graded) / len(graded)
    l2 = sum(r["layers"]["L2"] for r in graded) / len(graded)
    l3 = sum(r["layers"]["L3"] for r in graded) / len(graded)
    tot = sum(r["layers"]["total_20"] for r in graded) / len(graded)
    print(f"样本数: {len(graded)}（另有 {len(results) - len(graded)} 条未评分）")
    dim_line = "  ".join(f"{d['name']} {means[d['key']]:.2f}" for d in DIMENSIONS)
    print(f"维度均值: {dim_line}")
    print(f"层级均值: L1(情绪回应) {l1:.2f} | L2(语言质量) {l2:.2f} | L3(专业质量) {l3:.2f}")
    print(f"总分(20制): {tot:.2f}  (原始加权 {WEIGHT_L1:g}×L1+{WEIGHT_L2:g}×L2+{WEIGHT_L3:g}×L3, 满分 {RAW_MAX:g})")

    # 分类别
    cats = {}
    for r in graded:
        cats.setdefault(r.get("category", "?"), []).append(r)
    print("\n分类别均值:")
    print(f"  {'类别':<8} {'条数':<5} {'L1':<7} {'L2':<7} {'L3':<7} {'20制':<7} {'通过率'}")
    for cat, rs in sorted(cats.items()):
        cl1 = sum(r["layers"]["L1"] for r in rs) / len(rs)
        cl2 = sum(r["layers"]["L2"] for r in rs) / len(rs)
        cl3 = sum(r["layers"]["L3"] for r in rs) / len(rs)
        ct = sum(r["layers"]["total_20"] for r in rs) / len(rs)
        cpass = sum(1 for r in rs if pass_verdict(r["layers"], r["layers"]["L1"])) / len(rs)
        print(f"  {cat:<8} {len(rs):<5} {cl1:<7.2f} {cl2:<7.2f} {cl3:<7.2f} {ct:<7.2f} {cpass * 100:.0f}%")

    # 达标结论
    ok = pass_verdict({"total_20": tot}, l1) and l1 >= PASS_L1
    print(f"\n达标线: 总分(20制) ≥ {PASS_TOTAL_20:g} 且 L1 ≥ {PASS_L1:g}")
    print(f"总分 {tot:.2f} | L1 {l1:.2f} → {'PASS ✅ 话术引擎达标，可上线' if ok else 'FAIL ❌ 未达标，需优化话术'}")
    if not ok:
        if tot < PASS_TOTAL_20:
            print("  提示: 总分低于达标线，建议优先打磨 L2 语言质量（口语/金句/截图友好）")
        if l1 < PASS_L1:
            print("  提示: L1 情绪回应是底线，未达标需优先修复共情链路（先接情绪，再给内容）")


# ---------------------------------------------------------------------------
# 导出报告
# ---------------------------------------------------------------------------
def export_report(results: List[Dict[str, Any]], args: argparse.Namespace,
                  target_desc: str, model: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(args.export)
    if not out.is_absolute() and out.parent == Path("."):
        out = RESULTS_DIR / out.name  # 相对裸文件名 → data/eval/results/
    graded = [r for r in results if r.get("graded")]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "# M9 话术引擎评测报告",
        "",
        f"- 生成时间: {now}",
        f"- 被测目标: {target_desc}",
        f"- 评卷模型: DeepSeek ({model})",
        f"- 评测样本: {len(graded)}/{len(results)} 条评分成功",
        f"- 达标线: 总分(20制) ≥ {PASS_TOTAL_20:g} 且 L1(情绪回应) ≥ {PASS_L1:g}",
        "",
        "## 总评",
        "",
    ]
    if graded:
        l1 = sum(r["layers"]["L1"] for r in graded) / len(graded)
        l2 = sum(r["layers"]["L2"] for r in graded) / len(graded)
        l3 = sum(r["layers"]["L3"] for r in graded) / len(graded)
        tot = sum(r["layers"]["total_20"] for r in graded) / len(graded)
        ok = tot >= PASS_TOTAL_20 and l1 >= PASS_L1
        lines += [
            f"**结论: {'PASS ✅ 达标' if ok else 'FAIL ❌ 未达标'}**",
            "",
            "| 指标 | 得分 | 达标线 |",
            "|---|---|---|",
            f"| 总分(20制) | {tot:.2f} | ≥ {PASS_TOTAL_20:g} |",
            f"| L1 情绪回应 | {l1:.2f} | ≥ {PASS_L1:g} |",
            f"| L2 语言质量 | {l2:.2f} | - |",
            f"| L3 专业质量 | {l3:.2f} | - |",
            "",
            "| 维度 | 均值 |",
            "|---|---|",
        ]
        for d in DIMENSIONS:
            m = sum(r["dims"][d["key"]] for r in graded) / len(graded)
            lines.append(f"| {d['layer']} {d['name']} | {m:.2f} |")
        lines.append("")

        # 分类别
        cats = {}
        for r in graded:
            cats.setdefault(r.get("category", "?"), []).append(r)
        lines += ["### 分类别汇总", "", "| 类别 | 条数 | L1 | L2 | L3 | 20制 | 通过率 |",
                  "|---|---|---|---|---|---|---|"]
        for cat, rs in sorted(cats.items()):
            cl1 = sum(r["layers"]["L1"] for r in rs) / len(rs)
            cl2 = sum(r["layers"]["L2"] for r in rs) / len(rs)
            cl3 = sum(r["layers"]["L3"] for r in rs) / len(rs)
            ct = sum(r["layers"]["total_20"] for r in rs) / len(rs)
            cp = sum(1 for r in rs if pass_verdict(r["layers"], r["layers"]["L1"])) / len(rs)
            lines.append(f"| {cat} | {len(rs)} | {cl1:.2f} | {cl2:.2f} | {cl3:.2f} | {ct:.2f} | {cp * 100:.0f}% |")
        lines.append("")

    # 逐条明细
    lines += ["## 逐条明细", ""]
    for r in results:
        sid = r.get("id", "?")
        lines.append(f"### {sid}（{r.get('category', '')}{' / ' + r.get('domain', '') if r.get('domain') else ''}，情绪强度 {r.get('intensity', '-')}）")
        if not r.get("graded"):
            lines += ["", f"> 未评分（引擎失败或评卷失败）: {r.get('reply', '')[:200]}", ""]
            continue
        expect = r.get("expect_terms") or []
        hit = r.get("expect_hit") or []
        lines += [
            "",
            "**用户输入**",
            "",
            f"> {r.get('text', '')}",
            "",
            "**AI 回复**",
            "",
            f"> {r['reply']}",
            "",
        ]
        if expect:
            lines.append(f"**期望要点**: {'、'.join(expect)} → 命中: {('、'.join(hit)) if hit else '无'}")
            lines.append("")
        d, l = r["dims"], r["layers"]
        verdict = "PASS" if pass_verdict(l, l["L1"]) else "FAIL"
        lines += [
            "| 维度 | 得分 | 维度 | 得分 |",
            "|---|---|---|---|",
            f"| L1 接住度 | {d['l1_receive']:.1f} | L1 温度 | {d['l1_warmth']:.1f} |",
            f"| L2 口语度 | {d['l2_colloquial']:.1f} | L2 比喻/金句 | {d['l2_metaphor']:.1f} |",
            f"| L2 截图友好 | {d['l2_screenshot']:.1f} | L3 术语翻译 | {d['l3_terms']:.1f} |",
            f"| L3 证据感 | {d['l3_evidence']:.1f} | L3 不绝对 | {d['l3_no_absolutes']:.1f} |",
            f"| L1 {l['L1']:.1f} | L2 {l['L2']:.1f} | L3 {l['L3']:.1f} | 总分(20制) {l['total_20']:.1f} |",
            "",
            f"**判定**: {verdict}（20制 ≥ {PASS_TOTAL_20:g} 且 L1 ≥ {PASS_L1:g}）",
            "",
            f"**评卷点评**: {r.get('rationale', '')}",
            "",
        ]

    lines += [
        "## 附录：评测集与使用",
        "",
        f"- 评测集: `data/eval/eval_set.json`（{len(results)} 条样本）",
        "- 添加样本: `python scripts/tone_eval.py --add-sample \"新样本\" --category 失眠 --intensity 0.7`",
        "- L4 真实对话抽检: `python scripts/tone_eval.py --l4`（内置真实用户语料 seed，"
        "也可将线上日志导出为 JSONL: 每行 {id, category, text, reply?} 后指定 --l4-file）",
        "- 复跑: `python scripts/tone_eval.py --target api --export 报告.md`",
        "",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告已导出: {out}")
    return out


# ---------------------------------------------------------------------------
# L4 真实对话抽检
# ---------------------------------------------------------------------------
def load_l4_samples(file: Path) -> List[Dict[str, Any]]:
    if not file.exists():
        raise FileNotFoundError(f"L4 真实对话文件不存在: {file}")
    samples = []
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        obj.setdefault("expect_terms", [])
        obj.setdefault("intensity", 0.5)
        obj.setdefault("reply", None)
        samples.append(obj)
    return samples


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="M9 话术引擎评测系统 — 四层评测（L1情绪回应/L2语言质量/L3专业质量/L4真实对话抽检）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("用法：")[1].split("被测引擎：")[0].strip()
    )
    p.add_argument("--target", choices=["api", "local"], default="api",
                   help="被测引擎: api=调用 8767 接口(默认) / local=直接 handler.process")
    p.add_argument("--api-url", default=DEFAULT_API_URL,
                   help=f"被测 API 地址（默认 {DEFAULT_API_URL}）")
    p.add_argument("--eval-api-key", default=None,
                   help="评卷 LLM 的 DeepSeek key（默认取 settings.claude_api_key，"
                        "其次环境变量 DEEPSEEK_API_KEY）")
    p.add_argument("--eval-model", default=DEFAULT_EVAL_MODEL,
                   help=f"评卷模型（默认 {DEFAULT_EVAL_MODEL}）")
    p.add_argument("--user-id", default="eval_m9",
                   help="评测用 user_id（默认 eval_m9）")
    p.add_argument("--limit", type=int, default=0,
                   help="只评测前 N 条（0=全部）")
    p.add_argument("--category", default=None,
                   help="只评测指定类别（失眠/分手/受挫/分享/生日/专业问题/闲聊）")
    p.add_argument("--list", action="store_true",
                   help="列出当前题库后退出")
    p.add_argument("--add-sample", default=None, metavar="TEXT",
                   help="向题库添加新样本（配合 --category/--intensity/--expect-terms）")
    p.add_argument("--intensity", type=float, default=0.5,
                   help="--add-sample 时的情绪强度 0-1（默认 0.5）")
    p.add_argument("--expect-terms", default=None,
                   help="--add-sample 时的期望要点，分号分隔，如 \"风水;煞\"")
    p.add_argument("--domain", default=None,
                   help="--add-sample 时的子领域（八字/紫微/解梦等）")
    p.add_argument("--export", default=None, metavar="FILE",
                   help="导出评测报告（裸文件名存到 data/eval/results/）")
    p.add_argument("--l4", action="store_true",
                   help="L4 真实对话抽检模式（读 data/eval/real_dialogues.jsonl）")
    p.add_argument("--l4-file", default=None,
                   help="L4 自定义真实对话文件（JSONL: 每行 {id, category, text, intensity?, reply?, expect_terms?}）")
    return p


def resolve_eval_key(cli_key: Optional[str]) -> str:
    if cli_key:
        return cli_key
    env = os.environ.get("DEEPSEEK_API_KEY")
    if env:
        return env
    try:
        from src.config import load_settings
        key = load_settings().claude_api_key
        if key:
            return key
    except Exception as e:
        print(f"[warn] 读取 settings.claude_api_key 失败: {e}")
    print("错误: 未找到评卷 LLM key（请配置 config/settings.yaml 的 claude_api_key，"
          "或设置环境变量 DEEPSEEK_API_KEY，或 --eval-api-key）")
    sys.exit(2)


def main() -> int:
    args = build_parser().parse_args()

    # 动态题库：添加样本
    if args.add_sample:
        expect = [t.strip() for t in args.expect_terms.split(";")] if args.expect_terms else None
        sample = add_sample(args.add_sample, args.category or "未分类",
                            args.intensity, domain=args.domain,
                            expect_terms=expect)
        print(f"已添加样本: {sample['id']} | {sample['category']} | 强度 {sample['intensity']}")
        print(f"题库: {EVAL_SET_FILE}（共 {len(load_eval_set())} 条）")
        return 0

    if args.list:
        for s in load_eval_set():
            print(f"{s['id']:<20} {s.get('category', ''):<6} "
                  f"{s.get('domain', '') or '':<4} 强度{s.get('intensity', 0.5):.2f}  {s.get('text', '')[:50]}")
        print(f"\n共 {len(load_eval_set())} 条样本 | 题库: {EVAL_SET_FILE}")
        return 0

    # 评测集
    if args.l4:
        l4_file = Path(args.l4_file) if args.l4_file else REAL_DIALOGUES_FILE
        if l4_file == REAL_DIALOGUES_FILE and not l4_file.exists():
            REAL_DIALOGUES_FILE.parent.mkdir(parents=True, exist_ok=True)
            REAL_DIALOGUES_FILE.write_text(
                "\n".join(json.dumps(s, ensure_ascii=False) for s in REAL_SEED) + "\n",
                encoding="utf-8")
            print(f"[init] 已创建 L4 真实对话 seed: {REAL_DIALOGUES_FILE}（{len(REAL_SEED)} 条真实用户输入）")
        samples = load_l4_samples(l4_file)
    else:
        ensure_eval_set()
        samples = load_eval_set()
        if args.category:
            samples = [s for s in samples if s.get("category") == args.category]
        if args.limit:
            samples = samples[:args.limit]

    if not samples:
        print("评测集为空")
        return 2

    # 被测引擎
    key = resolve_eval_key(args.eval_api_key)
    engine = make_engine(args.target, args.api_url, key)
    if args.target == "api":
        # 快速探活
        try:
            httpx.get(args.api_url.replace("/api/chat", "/api/health"), timeout=5)
        except Exception:
            pass
    judge = Judge(api_key=key, model=args.eval_model)

    mode = "L4 真实对话抽检" if args.l4 else f"标准评测集({len(samples)}条)"
    target_desc = (f"API {args.api_url}" if args.target == "api" else "local handler.process")
    print(f"== M9 话术评测 | {mode} | 被测引擎: {target_desc} | 评卷: {args.eval_model} ==")

    results = run_eval(engine, judge, samples, args.user_id, l4_mode=args.l4)

    print_table(results, title=f"评测明细（{mode}）")
    print_summary(results)

    if args.export:
        export_report(results, args, target_desc, args.eval_model)
    return 0 if any(r.get("graded") for r in results) else 3


if __name__ == "__main__":
    sys.exit(main())
