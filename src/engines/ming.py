"""AI 取名引擎 —— 生成 + 五维评分 + 五行补益 + 古籍出处(RAG) + 深度报告。

设计(方案-AI取名.md,权威):
- 五维加权: 音形义 30% / 五行 25% / 数理 25% / 笔画 10% / 性别匹配 10%(百分制)
- 免费 5 名: 第 4、5 名只给分数不给深度解析(解锁线)
- 深度报告(付费): 八字契合度(五行补益矩阵)/ 改名对比 / 备选 15
- 出处只来自 RAG 命中(有则示),绝不编造;无检索器/无命中 → 无出处字段
- LLM 生成失败 → 规则字库兜底(宁缺毋滥,不编造);规则也失败 → 调用方报错

字库: 首版人工标注 ~180 常用取名用字(五行/性别倾向/风格标签),后续按 800 字
库扩充计划补全。笔画数据复用 xingming 引擎 STROKE_TABLE,未知字笔画维度
如实降分(数据缺失不装懂)。
"""
import json
import logging
import random
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 五维权重(方案锁定,严禁改动) ─────────────────────────────────
WEIGHTS = {"音形义": 0.30, "五行": 0.25, "数理": 0.25, "笔画": 0.10, "性别匹配": 0.10}
STYLE_CHIPS = ["文雅", "大气", "古典", "现代", "诗意"]
DIM_KEYS = ["音形义", "五行", "数理", "笔画", "性别匹配"]

# 负面联想字(音形义维度扣分): 绝不用于生成,仅用于评分红线
NEG_CHARS = set("病痛穷苦死亡灾祸囚狱败辱愚呆痴疯伤离别孤寡怨恨")
# 字义风格词(规则兜底点评用)
_TAGWORD = {"文雅": "雅正", "大气": "开阔", "古典": "古雅", "现代": "清朗", "诗意": "有画意"}

# ── 首版取名用字库: 字符 → {wx: 五行, g: 性别倾向(m/f/b), s: 风格标签} ──
# 五行/性别/风格为首版人工标注(基于字形部首与常用取名倾向),仅供评分参考。
CHAR_LIB: Dict[str, dict] = {
    # ── 水 ──
    "云": {"wx": "水", "g": "b", "s": ["诗意", "古典"]},
    "雨": {"wx": "水", "g": "f", "s": ["诗意", "古典"]},
    "雪": {"wx": "水", "g": "f", "s": ["诗意", "古典", "文雅"]},
    "雯": {"wx": "水", "g": "f", "s": ["文雅", "古典"]},
    "霖": {"wx": "水", "g": "b", "s": ["文雅", "大气"]},
    "露": {"wx": "水", "g": "f", "s": ["诗意", "文雅"]},
    "汐": {"wx": "水", "g": "f", "s": ["诗意", "现代"]},
    "洛": {"wx": "水", "g": "f", "s": ["文雅", "古典"]},
    "涵": {"wx": "水", "g": "f", "s": ["文雅", "现代"]},
    "洁": {"wx": "水", "g": "f", "s": ["文雅", "现代"]},
    "淑": {"wx": "水", "g": "f", "s": ["文雅", "古典"]},
    "沐": {"wx": "水", "g": "b", "s": ["现代", "诗意"]},
    "沛": {"wx": "水", "g": "b", "s": ["大气", "现代"]},
    "沁": {"wx": "水", "g": "f", "s": ["诗意", "现代"]},
    "清": {"wx": "水", "g": "b", "s": ["文雅", "诗意"]},
    "淼": {"wx": "水", "g": "b", "s": ["大气"]},
    "澄": {"wx": "水", "g": "b", "s": ["文雅", "诗意"]},
    "泽": {"wx": "水", "g": "b", "s": ["大气", "现代"]},
    "洋": {"wx": "水", "g": "b", "s": ["大气", "现代"]},
    "海": {"wx": "水", "g": "b", "s": ["大气", "古典"]},
    "江": {"wx": "水", "g": "b", "s": ["大气", "古典"]},
    "波": {"wx": "水", "g": "b", "s": ["大气"]},
    "涛": {"wx": "水", "g": "b", "s": ["大气"]},
    "澜": {"wx": "水", "g": "f", "s": ["诗意", "大气"]},
    "溪": {"wx": "水", "g": "f", "s": ["诗意", "文雅"]},
    "淳": {"wx": "水", "g": "b", "s": ["古典", "文雅"]},
    "源": {"wx": "水", "g": "b", "s": ["大气", "文雅"]},
    "瀚": {"wx": "水", "g": "b", "s": ["大气"]},
    "鸿": {"wx": "水", "g": "b", "s": ["大气", "古典"]},
    "浩": {"wx": "水", "g": "b", "s": ["大气"]},
    "润": {"wx": "水", "g": "b", "s": ["文雅", "大气"]},
    "汀": {"wx": "水", "g": "f", "s": ["诗意", "文雅"]},
    "沅": {"wx": "水", "g": "b", "s": ["诗意"]},
    "泓": {"wx": "水", "g": "b", "s": ["文雅"]},
    "漪": {"wx": "水", "g": "f", "s": ["诗意", "文雅"]},
    "文": {"wx": "水", "g": "b", "s": ["文雅", "古典", "现代"]},
    "慧": {"wx": "水", "g": "f", "s": ["文雅", "古典"]},
    "敏": {"wx": "水", "g": "f", "s": ["文雅", "现代"]},
    "冬": {"wx": "水", "g": "b", "s": ["古典", "诗意"]},
    "子": {"wx": "水", "g": "b", "s": ["古典", "现代"]},
    "航": {"wx": "水", "g": "b", "s": ["大气", "现代"]},
    "晚": {"wx": "水", "g": "f", "s": ["诗意", "现代"]},
    "风": {"wx": "水", "g": "b", "s": ["大气", "诗意"]},
    "翰": {"wx": "水", "g": "b", "s": ["文雅", "大气"]},
    "博": {"wx": "水", "g": "b", "s": ["大气", "文雅"]},
    "学": {"wx": "水", "g": "b", "s": ["文雅", "古典"]},
    "鹏": {"wx": "水", "g": "b", "s": ["大气", "现代"]},
    "霆": {"wx": "水", "g": "b", "s": ["大气"]},
    "恒": {"wx": "水", "g": "b", "s": ["大气", "古典"]},
    "弘": {"wx": "水", "g": "b", "s": ["大气", "古典"]},
    "望": {"wx": "水", "g": "b", "s": ["大气", "文雅"]},
    "曼": {"wx": "水", "g": "f", "s": ["文雅", "现代"]},
    "姝": {"wx": "金", "g": "f", "s": ["古典", "文雅"]},
    # ── 木 ──
    "林": {"wx": "木", "g": "b", "s": ["大气", "古典"]},
    "森": {"wx": "木", "g": "b", "s": ["大气"]},
    "楠": {"wx": "木", "g": "f", "s": ["文雅"]},
    "桐": {"wx": "木", "g": "b", "s": ["文雅", "古典"]},
    "柏": {"wx": "木", "g": "b", "s": ["古典", "大气"]},
    "荣": {"wx": "木", "g": "b", "s": ["大气", "现代"]},
    "华": {"wx": "木", "g": "b", "s": ["大气", "古典"]},
    "艺": {"wx": "木", "g": "f", "s": ["文雅", "现代"]},
    "若": {"wx": "木", "g": "f", "s": ["诗意", "文雅"]},
    "芷": {"wx": "木", "g": "f", "s": ["古典", "文雅"]},
    "萱": {"wx": "木", "g": "f", "s": ["古典", "文雅"]},
    "兰": {"wx": "木", "g": "f", "s": ["文雅", "古典"]},
    "竹": {"wx": "木", "g": "b", "s": ["文雅", "古典", "诗意"]},
    "梅": {"wx": "木", "g": "f", "s": ["古典", "诗意"]},
    "柳": {"wx": "木", "g": "f", "s": ["古典", "诗意"]},
    "桂": {"wx": "木", "g": "f", "s": ["古典"]},
    "芳": {"wx": "木", "g": "f", "s": ["古典", "文雅"]},
    "芬": {"wx": "木", "g": "f", "s": ["文雅"]},
    "英": {"wx": "木", "g": "f", "s": ["大气", "古典"]},
    "蕊": {"wx": "木", "g": "f", "s": ["文雅", "诗意"]},
    "薇": {"wx": "木", "g": "f", "s": ["古典", "文雅"]},
    "蔚": {"wx": "木", "g": "b", "s": ["大气", "文雅"]},
    "菁": {"wx": "木", "g": "f", "s": ["文雅", "现代"]},
    "桦": {"wx": "木", "g": "b", "s": ["大气"]},
    "栋": {"wx": "木", "g": "b", "s": ["大气", "现代"]},
    "梧": {"wx": "木", "g": "b", "s": ["古典", "诗意"]},
    "梓": {"wx": "木", "g": "b", "s": ["古典", "文雅"]},
    "栩": {"wx": "木", "g": "b", "s": ["现代", "文雅"]},
    "芸": {"wx": "木", "g": "f", "s": ["文雅", "古典"]},
    "芙": {"wx": "木", "g": "f", "s": ["古典", "诗意"]},
    "荷": {"wx": "木", "g": "f", "s": ["古典", "诗意"]},
    "蓉": {"wx": "木", "g": "f", "s": ["古典"]},
    "莲": {"wx": "木", "g": "f", "s": ["古典", "文雅"]},
    "莉": {"wx": "木", "g": "f", "s": ["现代", "文雅"]},
    "苗": {"wx": "木", "g": "f", "s": ["现代"]},
    "青": {"wx": "木", "g": "b", "s": ["诗意", "大气"]},
    "松": {"wx": "木", "g": "b", "s": ["大气", "古典"]},
    "枫": {"wx": "木", "g": "b", "s": ["诗意", "大气"]},
    "杰": {"wx": "木", "g": "b", "s": ["大气", "现代"]},
    "槿": {"wx": "木", "g": "f", "s": ["古典", "文雅"]},
    "蕾": {"wx": "木", "g": "f", "s": ["现代", "诗意"]},
    "苏": {"wx": "木", "g": "f", "s": ["文雅", "古典"]},
    "芊": {"wx": "木", "g": "f", "s": ["诗意", "文雅"]},
    "芃": {"wx": "木", "g": "b", "s": ["大气"]},
    "筠": {"wx": "木", "g": "f", "s": ["文雅", "古典"]},
    "筱": {"wx": "木", "g": "f", "s": ["文雅", "诗意"]},
    "莞": {"wx": "木", "g": "f", "s": ["文雅", "现代"]},
    "春": {"wx": "木", "g": "f", "s": ["诗意", "古典"]},
    "嘉": {"wx": "木", "g": "b", "s": ["大气", "古典"]},
    "可": {"wx": "木", "g": "f", "s": ["现代", "文雅"]},
    "元": {"wx": "木", "g": "b", "s": ["大气", "古典"]},
    "君": {"wx": "木", "g": "b", "s": ["古典", "大气"]},
    "彦": {"wx": "木", "g": "b", "s": ["文雅", "古典"]},
    "广": {"wx": "木", "g": "b", "s": ["大气", "古典"]},
    "群": {"wx": "木", "g": "b", "s": ["大气"]},
    "琴": {"wx": "木", "g": "f", "s": ["古典", "文雅"]},
    "棋": {"wx": "木", "g": "b", "s": ["古典", "文雅"]},
    "梦": {"wx": "木", "g": "f", "s": ["诗意", "现代"]},
    "月": {"wx": "木", "g": "f", "s": ["诗意", "古典"]},
    "娟": {"wx": "木", "g": "f", "s": ["文雅", "古典"]},
    "婕": {"wx": "木", "g": "f", "s": ["现代", "文雅"]},
    "雅": {"wx": "木", "g": "f", "s": ["文雅", "古典"]},
    "欣": {"wx": "木", "g": "f", "s": ["现代", "文雅"]},
    "芯": {"wx": "木", "g": "f", "s": ["现代", "文雅"]},
    "舒": {"wx": "木", "g": "b", "s": ["文雅", "诗意"]},
    # ── 火 ──
    "炎": {"wx": "火", "g": "b", "s": ["大气"]},
    "灵": {"wx": "火", "g": "b", "s": ["文雅", "现代"]},
    "煜": {"wx": "火", "g": "b", "s": ["大气", "文雅"]},
    "炜": {"wx": "火", "g": "b", "s": ["大气"]},
    "烨": {"wx": "火", "g": "b", "s": ["大气"]},
    "彤": {"wx": "火", "g": "f", "s": ["古典", "文雅"]},
    "丹": {"wx": "火", "g": "f", "s": ["古典", "文雅"]},
    "晴": {"wx": "火", "g": "f", "s": ["现代", "诗意"]},
    "明": {"wx": "火", "g": "b", "s": ["大气", "古典"]},
    "星": {"wx": "火", "g": "b", "s": ["诗意", "现代"]},
    "晨": {"wx": "火", "g": "b", "s": ["现代", "大气"]},
    "昊": {"wx": "火", "g": "b", "s": ["大气"]},
    "昌": {"wx": "火", "g": "b", "s": ["大气", "古典"]},
    "曦": {"wx": "火", "g": "b", "s": ["现代", "大气"]},
    "晓": {"wx": "火", "g": "b", "s": ["现代", "文雅"]},
    "晖": {"wx": "火", "g": "b", "s": ["大气"]},
    "灿": {"wx": "火", "g": "b", "s": ["现代", "大气"]},
    "炳": {"wx": "火", "g": "b", "s": ["大气", "古典"]},
    "然": {"wx": "火", "g": "b", "s": ["现代", "诗意"]},
    "熹": {"wx": "火", "g": "f", "s": ["古典", "文雅"]},
    "昱": {"wx": "火", "g": "b", "s": ["大气", "现代"]},
    "昭": {"wx": "火", "g": "b", "s": ["古典", "大气"]},
    "曜": {"wx": "火", "g": "b", "s": ["大气", "古典"]},
    "凯": {"wx": "火", "g": "b", "s": ["大气", "现代"]},
    "泰": {"wx": "火", "g": "b", "s": ["大气", "古典"]},
    "南": {"wx": "火", "g": "b", "s": ["大气"]},
    "东": {"wx": "火", "g": "b", "s": ["大气", "古典"]},
    "志": {"wx": "火", "g": "b", "s": ["大气", "古典"]},
    "智": {"wx": "火", "g": "b", "s": ["文雅", "大气"]},
    "龙": {"wx": "火", "g": "b", "s": ["大气"]},
    "晗": {"wx": "火", "g": "f", "s": ["现代", "诗意"]},
    "昕": {"wx": "火", "g": "b", "s": ["现代", "文雅"]},
    "晶": {"wx": "火", "g": "f", "s": ["现代", "文雅"]},
    "耀": {"wx": "火", "g": "b", "s": ["大气"]},
    "宁": {"wx": "火", "g": "f", "s": ["文雅", "现代"]},
    "乐": {"wx": "火", "g": "b", "s": ["现代", "大气"]},
    "夏": {"wx": "火", "g": "b", "s": ["现代", "大气"]},
    "天": {"wx": "火", "g": "b", "s": ["大气", "古典"]},
    "阳": {"wx": "火", "g": "b", "s": ["大气"]},
    "光": {"wx": "火", "g": "b", "s": ["大气", "现代"]},
    "达": {"wx": "火", "g": "b", "s": ["大气", "现代"]},
    "贞": {"wx": "火", "g": "f", "s": ["古典", "文雅"]},
    "恬": {"wx": "火", "g": "f", "s": ["文雅", "现代"]},
    "婷": {"wx": "火", "g": "f", "s": ["现代", "文雅"]},
    "媛": {"wx": "火", "g": "f", "s": ["文雅", "古典"]},
    "之": {"wx": "火", "g": "b", "s": ["古典", "文雅"]},
    # ── 土 ──
    "山": {"wx": "土", "g": "b", "s": ["大气", "古典"]},
    "岩": {"wx": "土", "g": "b", "s": ["大气"]},
    "峰": {"wx": "土", "g": "b", "s": ["大气", "古典"]},
    "岚": {"wx": "土", "g": "f", "s": ["诗意", "文雅"]},
    "屿": {"wx": "土", "g": "b", "s": ["现代", "诗意"]},
    "宇": {"wx": "土", "g": "b", "s": ["大气", "现代"]},
    "安": {"wx": "土", "g": "b", "s": ["文雅", "现代"]},
    "坤": {"wx": "土", "g": "b", "s": ["大气", "古典"]},
    "均": {"wx": "土", "g": "b", "s": ["文雅"]},
    "培": {"wx": "土", "g": "b", "s": ["大气"]},
    "城": {"wx": "土", "g": "b", "s": ["大气", "现代"]},
    "基": {"wx": "土", "g": "b", "s": ["大气"]},
    "峻": {"wx": "土", "g": "b", "s": ["大气"]},
    "嵩": {"wx": "土", "g": "b", "s": ["大气", "古典"]},
    "岳": {"wx": "土", "g": "b", "s": ["大气", "古典"]},
    "岱": {"wx": "土", "g": "b", "s": ["古典", "大气"]},
    "垚": {"wx": "土", "g": "b", "s": ["古典"]},
    "堃": {"wx": "土", "g": "b", "s": ["古典"]},
    "佳": {"wx": "土", "g": "f", "s": ["现代", "文雅"]},
    "依": {"wx": "土", "g": "f", "s": ["现代", "诗意"]},
    "佑": {"wx": "土", "g": "b", "s": ["大气", "古典"]},
    "亦": {"wx": "土", "g": "b", "s": ["现代", "文雅"]},
    "恩": {"wx": "土", "g": "b", "s": ["大气", "文雅"]},
    "悠": {"wx": "土", "g": "f", "s": ["诗意", "文雅"]},
    "婉": {"wx": "土", "g": "f", "s": ["文雅", "古典"]},
    "仪": {"wx": "土", "g": "f", "s": ["文雅", "古典"]},
    "懿": {"wx": "土", "g": "f", "s": ["古典", "文雅"]},
    "怡": {"wx": "土", "g": "f", "s": ["文雅", "现代"]},
    "妍": {"wx": "土", "g": "f", "s": ["现代", "文雅"]},
    "韵": {"wx": "土", "g": "f", "s": ["诗意", "文雅"]},
    "墨": {"wx": "土", "g": "b", "s": ["文雅", "古典", "诗意"]},
    "砚": {"wx": "土", "g": "b", "s": ["文雅", "古典"]},
    "影": {"wx": "土", "g": "f", "s": ["诗意", "古典"]},
    "远": {"wx": "土", "g": "b", "s": ["大气", "古典"]},
    "巍": {"wx": "土", "g": "b", "s": ["大气"]},
    "一": {"wx": "土", "g": "b", "s": ["现代", "大气"]},
    "予": {"wx": "土", "g": "b", "s": ["现代", "文雅"]},
    "允": {"wx": "土", "g": "b", "s": ["大气", "现代"]},
    # ── 金 ──
    "金": {"wx": "金", "g": "b", "s": ["大气"]},
    "铭": {"wx": "金", "g": "b", "s": ["大气", "现代"]},
    "锐": {"wx": "金", "g": "b", "s": ["大气", "现代"]},
    "锦": {"wx": "金", "g": "b", "s": ["古典", "大气"]},
    "锋": {"wx": "金", "g": "b", "s": ["大气"]},
    "钰": {"wx": "金", "g": "f", "s": ["文雅", "现代"]},
    "瑞": {"wx": "金", "g": "b", "s": ["大气", "文雅"]},
    "静": {"wx": "金", "g": "f", "s": ["文雅", "古典"]},
    "硕": {"wx": "金", "g": "b", "s": ["大气", "文雅"]},
    "帆": {"wx": "金", "g": "b", "s": ["大气", "现代"]},
    "瑜": {"wx": "金", "g": "f", "s": ["文雅", "古典"]},
    "瑾": {"wx": "金", "g": "f", "s": ["古典", "文雅"]},
    "琦": {"wx": "金", "g": "f", "s": ["文雅", "古典"]},
    "琳": {"wx": "金", "g": "f", "s": ["文雅", "现代"]},
    "琪": {"wx": "金", "g": "f", "s": ["文雅", "现代"]},
    "瑶": {"wx": "金", "g": "f", "s": ["古典", "文雅"]},
    "璇": {"wx": "金", "g": "f", "s": ["古典", "文雅"]},
    "珊": {"wx": "金", "g": "f", "s": ["文雅", "古典"]},
    "玲": {"wx": "金", "g": "f", "s": ["文雅", "现代"]},
    "琛": {"wx": "金", "g": "b", "s": ["古典", "文雅"]},
    "铮": {"wx": "金", "g": "b", "s": ["大气", "古典"]},
    "悦": {"wx": "金", "g": "f", "s": ["现代", "文雅"]},
    "初": {"wx": "金", "g": "b", "s": ["现代", "诗意"]},
    "诗": {"wx": "金", "g": "f", "s": ["诗意", "文雅"]},
    "书": {"wx": "金", "g": "b", "s": ["文雅", "古典"]},
    "尚": {"wx": "金", "g": "b", "s": ["大气", "现代"]},
    "素": {"wx": "金", "g": "f", "s": ["文雅", "古典"]},
    "真": {"wx": "金", "g": "b", "s": ["文雅"]},
    "思": {"wx": "金", "g": "b", "s": ["文雅", "现代"]},
    "秋": {"wx": "金", "g": "f", "s": ["诗意", "古典"]},
    "舟": {"wx": "金", "g": "b", "s": ["古典", "诗意"]},
    "夕": {"wx": "金", "g": "f", "s": ["诗意", "现代"]},
    "拾": {"wx": "金", "g": "b", "s": ["诗意", "现代"]},
    "疏": {"wx": "金", "g": "b", "s": ["诗意", "古典"]},
    "川": {"wx": "金", "g": "b", "s": ["大气", "诗意"]},
    "睿": {"wx": "金", "g": "b", "s": ["大气", "文雅"]},
    "宸": {"wx": "金", "g": "b", "s": ["大气", "古典"]},
    "骁": {"wx": "金", "g": "b", "s": ["大气", "现代"]},
    "秀": {"wx": "金", "g": "f", "s": ["文雅", "古典"]},
    "歆": {"wx": "金", "g": "f", "s": ["文雅", "古典"]},
    "纯": {"wx": "金", "g": "f", "s": ["文雅", "现代"]},
    "卿": {"wx": "金", "g": "f", "s": ["古典", "文雅"]},
    "娴": {"wx": "金", "g": "f", "s": ["古典", "文雅"]},
    "姗": {"wx": "金", "g": "f", "s": ["文雅"]},
}

_GENDER_TAG = {"男": "m", "女": "f"}


# ── 基础工具 ────────────────────────────────────────────────────

def char_wuxing(ch: str) -> str:
    return CHAR_LIB.get(ch, {}).get("wx", "")


def char_gender_tag(ch: str) -> str:
    return CHAR_LIB.get(ch, {}).get("g", "b")


def char_styles(ch: str) -> set:
    return set(CHAR_LIB.get(ch, {}).get("s", []))


def parse_yongshen(yongshen: str) -> Tuple[str, List[str]]:
    """解析用神串(排盘引擎输出): '水为用神（喜水、木）' → ('水', ['水','木'])。"""
    yongshen = yongshen or ""
    use = ""
    m = re.search(r"([金木水火土])为用神", yongshen)
    if m:
        use = m.group(1)
    helpful: List[str] = []
    m = re.search(r"喜([^）)]+)", yongshen)
    if m:
        helpful = re.findall(r"[金木水火土]", m.group(1))
    return use, helpful


def _clamp(v: float, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(round(v))))


def level_of(total: int) -> str:
    if total >= 90:
        return "上选"
    if total >= 80:
        return "佳名"
    if total >= 70:
        return "可用"
    return ""


# ── 五维评分器(确定性: 同输入同输出) ────────────────────────────

def _score_yin_xing_yi(given: str, styles: Optional[List[str]]) -> int:
    """音形义 30%: 字库风格标签命中 + 负面联想过滤 + 叠字扣分。"""
    styles = [s for s in (styles or []) if s in STYLE_CHIPS]
    base = 82 if not styles else 80
    bonus = 0
    for ch in given:
        hit = len(char_styles(ch) & set(styles))
        bonus += min(4 * hit, 8)
    penalty = 6 * sum(1 for ch in given if ch in NEG_CHARS)
    if len(given) == 2 and given[0] == given[1]:
        penalty += 4
    return _clamp(base + min(bonus, 14) - penalty, 40, 96)


def _score_wuxing(given: str, yongshen: str, wuxing_counts: Optional[dict]) -> int:
    """五行 25%: 用神喜用 → 补益得分; 过剩五行同气 → 扣分; 无八字 → 均衡分。"""
    if yongshen:
        _use, helpful = parse_yongshen(yongshen)
        if helpful:
            base = 72
            first = True
            for ch in given:
                wx = char_wuxing(ch)
                if not wx:
                    continue
                if wx in helpful:
                    base += 8 if first else 5
                    first = False
                elif wuxing_counts and wuxing_counts.get(wx, 0) >= 3:
                    base -= 3
            return _clamp(base, 40, 98)
    if wuxing_counts:
        # 有排盘但用神串异常: 按过剩/缺失均衡给分
        base = 76
        for ch in given:
            wx = char_wuxing(ch)
            if wx and wuxing_counts.get(wx, 0) == 0:
                base += 4  # 补了八字缺失的五行
            elif wx and wuxing_counts.get(wx, 0) >= 3:
                base -= 4  # 与过剩五行同气
        return _clamp(base, 40, 94)
    # 无八字: 名字五行不重复即均衡
    wxs = [char_wuxing(ch) for ch in given if char_wuxing(ch)]
    distinct = len(set(wxs))
    if not wxs:
        return 76
    base = 78 + min(2 * distinct, 6)
    if distinct == 1:
        base -= 4
    return _clamp(base, 40, 92)


def _score_shuli(surname: str, given: str) -> int:
    """数理 25%: 复用 xingming 引擎(五格/81 数理/三才); 未知字笔画 → 数据缺失分。"""
    from .xingming import get_stroke_count, XingmingEngine  # 延迟导入避免环
    if not all(get_stroke_count(c) > 0 for c in surname + given):
        return 62  # 字库笔画缺失,数理不可算(宁缺毋滥,如实降分)
    try:
        res = XingmingEngine().analyze(surname, given)
    except Exception:
        return 62
    sancai_bonus = {"大吉": 12, "吉": 7, "半吉": 2, "凶": -10}.get(res.sancai_ji, 2)
    lucky = sum(1 for k in ("天格", "人格", "地格", "外格", "总格")
                if res.analysis.get(k, {}).get("吉凶") in ("吉", "大吉"))
    bad = sum(1 for k in ("天格", "人格", "地格", "外格", "总格")
              if res.analysis.get(k, {}).get("吉凶") == "凶")
    return _clamp(70 + sancai_bonus + 3 * lucky - 5 * bad, 40, 98)


def _score_bihua(given: str) -> int:
    """笔画 10%: 总笔画适中 + 单字均衡; 未知字 → 数据缺失分。"""
    from .xingming import get_stroke_count
    strokes = [get_stroke_count(ch) for ch in given]
    if not strokes or any(s == 0 for s in strokes):
        return 65  # 笔画数据暂缺(如实降分,不装懂)
    total = sum(strokes)
    if 6 <= total <= 14:
        base = 90
    elif total in (5, 15, 16):
        base = 82
    elif total in (4, 17, 18):
        base = 70
    else:
        base = 60
    diff = max(strokes) - min(strokes)
    if diff <= 3:
        base += 6
    elif diff <= 6:
        base += 2
    else:
        base -= 4
    return _clamp(base, 40, 98)


def _score_gender(given: str, gender: str) -> int:
    """性别匹配 10%: 字库性别倾向; 未知字按中性给分。"""
    tag = _GENDER_TAG.get(gender, "b")
    base = 60
    for ch in given:
        g = char_gender_tag(ch)
        if g == tag:
            base += 16
        elif g == "b":
            base += 8
        else:
            base -= 22
    return _clamp(base, 20, 99)


def score_name(surname: str, given: str, gender: str = "男",
               styles: Optional[List[str]] = None,
               yongshen: str = "",
               wuxing_counts: Optional[dict] = None) -> dict:
    """五维评分(确定性): 返回 {dims, total, level, buyi}。"""
    dims = {
        "音形义": _score_yin_xing_yi(given, styles),
        "五行": _score_wuxing(given, yongshen, wuxing_counts),
        "数理": _score_shuli(surname, given),
        "笔画": _score_bihua(given),
        "性别匹配": _score_gender(given, gender),
    }
    total = _clamp(sum(WEIGHTS[k] * dims[k] for k in DIM_KEYS), 0, 99)
    return {
        "dims": dims,
        "total": total,
        "level": level_of(total),
        "buyi": build_buyi(given, yongshen, wuxing_counts),
    }


def build_buyi(given: str, yongshen: str, wuxing_counts: Optional[dict]) -> List[dict]:
    """五行补益标签(免费即示): 用神喜用 → 补 X +8/+5; 过剩五行同气 → 泄 X -3。"""
    labels: List[dict] = []
    if not yongshen:
        return labels
    _use, helpful = parse_yongshen(yongshen)
    if not helpful:
        return labels
    first = True
    for ch in given:
        wx = char_wuxing(ch)
        if not wx:
            continue
        if wx in helpful:
            labels.append({"k": wx, "v": "+8" if first else "+5", "type": "add"})
            first = False
        elif wuxing_counts and wuxing_counts.get(wx, 0) >= 3:
            labels.append({"k": wx, "v": "-3", "type": "xie"})
    return labels


# ── 名字生成 ────────────────────────────────────────────────────

def _llm_generate_names(surname: str, gender_abbr: str, styles: List[str],
                        custom: str, wuxing_text: str,
                        api_key: str, timeout: float = 25.0) -> List[dict]:
    """LLM 生成 5 名。失败/超时/解析失败 → 抛异常(由调用方降级)。"""
    from src.llm.client import deepseek_anthropic_completion
    gender_cn = "男" if gender_abbr == "m" else "女"
    style_desc = "、".join(styles) if styles else "不限定"
    expect_desc = f"期望: {custom}。" if custom else ""
    prompt = (
        f"你是易理明灯的取名先生。请为姓氏「{surname}」的{gender_cn}性取 5 个名字"
        f"(每个名字 1-2 个字,以 2 字为主,不要带姓氏)。要求:\n"
        f"- 整体风格:{style_desc}。{expect_desc}\n"
        f"- {wuxing_text}\n"
        f"- 字义积极、读音顺口、避免生僻字与不雅谐音、避免负面联想字\n"
        f"- 每个名字配一句 20 字以内的人话点评\n"
        f"只输出 JSON 数组,每项格式 {{\"given\": \"云舒\", \"ping\": \"如云舒展,一生从容。\"}},"
        f"不要输出任何其他文字。"
    )
    messages = [
        {"role": "system", "content": "你是严谨的取名助手,只输出合法 JSON。"},
        {"role": "user", "content": prompt},
    ]
    raw = deepseek_anthropic_completion(
        api_key, messages, model="deepseek-v4-flash",
        max_tokens=800, temperature=0.8, timeout=timeout)
    return _parse_names(raw)


def _parse_names(raw: str) -> List[dict]:
    """LLM 输出解析: 容错剥离代码块/前缀,校验字段。解析失败抛异常。"""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        raise ValueError("LLM 输出无 JSON 数组")
    data = json.loads(text[start:end + 1])
    names = []
    for it in data[:8]:
        given = str(it.get("given", "")).strip()
        ping = str(it.get("ping", "")).strip()
        if not re.fullmatch(r"[一-龥]{1,2}", given):
            continue
        if any(ch in NEG_CHARS for ch in given):
            continue
        names.append({"given": given, "ping": ping})
        if len(names) >= 5:
            break
    if len(names) < 5:
        raise ValueError(f"LLM 输出合格名字不足 5 个({len(names)})")
    return names


def _rule_ping(given: str) -> str:
    """规则兜底点评(无 LLM 时): 基于字库风格标签如实描述,不编造。"""
    words = []
    for ch in given:
        tags = char_styles(ch)
        tag = next((t for t in ("文雅", "诗意", "大气", "古典", "现代") if t in tags), "")
        words.append(_TAGWORD.get(tag, "端正"))
    if len(words) == 1:
        return f"{given}字{words[0]}可取。"
    return f"{given[0]}字{words[0]}，{given[1]}字{words[1]}，二字相合。"


def rule_generate(surname: str, gender_abbr: str, styles: List[str],
                  rng: random.Random, count: int = 5) -> List[str]:
    """规则字库生成(LLM 降级兜底): 性别过滤 + 风格偏好 + 笔画适中。

    随机配对采样(每名两字不同、笔画和适中),返回去重候选;
    调用方再按五维评分排序取前 N(保证质量)。
    """
    from .xingming import get_stroke_count
    style_set = set(styles)
    pool = [c for c, info in CHAR_LIB.items()
            if (info["g"] in (gender_abbr, "b"))
            and (not style_set or (set(info["s"]) & style_set))
            and c not in NEG_CHARS
            and get_stroke_count(c) > 0]
    if len(pool) < 2:
        # 风格过滤后不足 → 放宽到全库(仍带性别过滤,宁缺毋滥)
        pool = [c for c, info in CHAR_LIB.items()
                if (info["g"] in (gender_abbr, "b"))
                and c not in NEG_CHARS
                and get_stroke_count(c) > 0]
        if len(pool) < 2:
            return []
    seen: set = set()
    out: List[str] = []
    tries = 0
    while len(out) < count and tries < 400:
        tries += 1
        a, b = rng.sample(pool, 2)
        total = get_stroke_count(a) + get_stroke_count(b)
        if not (5 <= total <= 15):
            continue
        name = a + b
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _rank_names(surname: str, gender: str, styles: List[str],
                candidates: List[dict], yongshen: str = "",
                wuxing_counts: Optional[dict] = None, limit: int = 5) -> List[dict]:
    """对候选名按五维综合分排序(综合分 > 五行契合 > 数理),取前 limit。"""
    scored = []
    for c in candidates:
        s = score_name(surname, c["given"], gender, styles, yongshen, wuxing_counts)
        scored.append((s["total"], s["dims"]["五行"], s["dims"]["数理"], c))
    scored.sort(key=lambda t: (-t[0], -t[1], -t[2]))
    return [dict(s[3], **{"_score": s[0]}) for s in scored[:limit]]


def generate_names(surname: str, gender_abbr: str, styles: List[str],
                   custom: str, wuxing_text: str,
                   api_key: str = "", rng: Optional[random.Random] = None,
                   llm_fn=None, gender: str = "",
                   yongshen: str = "", wuxing_counts: Optional[dict] = None) -> List[dict]:
    """生成 5 名主入口: LLM 优先(失败降级规则库),返回 [{given, ping}]。

    - llm_fn 可注入(测试 mock);None 时用默认 LLM 实现。
    - LLM 失败/无密钥 → 规则库; 规则库不足 5 → 返回不足(宁缺毋滥)。
    """
    rng = rng or random.Random()
    if api_key:
        try:
            fn = llm_fn or _llm_generate_names
            names = fn(surname, gender_abbr, styles, custom, wuxing_text, api_key)
            return names[:5]
        except Exception as exc:
            logger.warning("ming LLM 生成失败(%s), 降级规则字库", exc)
    candidates = [{"given": n, "ping": _rule_ping(n)}
                  for n in rule_generate(surname, gender_abbr, styles, rng, count=12)]
    if not candidates:
        return []
    gen = gender or ("男" if gender_abbr == "m" else "女")
    return _rank_names(surname, gen, styles, candidates, yongshen, wuxing_counts, limit=5)


# ── 古籍出处(RAG) ──────────────────────────────────────────────

def lookup_source(retriever, given: str) -> Optional[dict]:
    """出处只来自 RAG 命中: 文本须含名字完整字序,否则视为未命中。

    无检索器 / 检索异常 / 无命中 → None(有则示,绝不编造出处)。
    """
    if retriever is None or not given:
        return None
    try:
        hits = retriever.search(f"{given} 名字 出处 古籍", top_k=8)
    except Exception as exc:
        logger.warning("ming 出处检索失败: %s", exc)
        return None
    for hit in hits or []:
        text = (getattr(hit, "text", "") or "").replace("\n", "").strip()
        src = (getattr(hit, "source", "") or "").strip()
        if not src or not text:
            continue
        if given in text and all(ch in text for ch in given):
            return {"book": src, "quote": text[:48]}
    return None


# ── 深度报告(付费) ──────────────────────────────────────────────

def wuxing_overview(wuxing_counts: dict) -> dict:
    """八字五行分布摘要: {counts, strong(旺), weak(缺)}。"""
    counts = {k: wuxing_counts.get(k, 0) for k in ("金", "木", "水", "火", "土")}
    strong = [k for k, v in counts.items() if v >= 3]
    weak = [k for k, v in counts.items() if v == 0]
    return {"counts": counts, "strong": strong, "weak": weak}


def _buyi_why(wx: str, ch: str) -> str:
    """补益矩阵人话解释(与引擎用神输出一致,规则生成,不编造)。"""
    return {
        "水": f"字「{ch}」水旁谐润，正补用神",
        "木": f"字「{ch}」含木气，木生火势缓，暗助全局",
        "金": f"字「{ch}」金气清肃，助用神归位",
        "火": f"字「{ch}」含火，暖局调候",
        "土": f"字「{ch}」土气敦厚，生化有源",
    }.get(wx, f"字「{ch}」含{wx}气，助用神")


def build_buyi_matrix(given: str, yongshen: str, wuxing_counts: Optional[dict]) -> dict:
    """付费核心: 单名五行补益矩阵(补啥/补多少/人话解释 + 总评)。"""
    rows = []
    first = True
    if yongshen:
        _use, helpful = parse_yongshen(yongshen)
        for ch in given:
            wx = char_wuxing(ch)
            if not wx:
                continue
            if helpful and wx in helpful:
                rows.append({
                    "k": wx, "v": "+8" if first else "+5", "type": "add",
                    "why": _buyi_why(wx, ch),
                })
                first = False
            elif wuxing_counts and wuxing_counts.get(wx, 0) >= 3:
                rows.append({
                    "k": wx, "v": "-3", "type": "xie",
                    "why": f"字「{ch}」与八字偏旺的{wx}同气，宜泄不宜增",
                })
    comment = ""
    if yongshen and wuxing_counts:
        use, helpful = parse_yongshen(yongshen)
        over = wuxing_overview(wuxing_counts)
        strong = "、".join(over["strong"]) if over["strong"] else "不偏"
        adds = "、".join(f"补{r['k']}" for r in rows if r["type"] == "add") or "无强补"
        if over["strong"]:
            comment = f"八字{strong}偏旺，此名{adds}，可缓其势"
        elif over["weak"]:
            comment = f"八字缺{'、'.join(over['weak'])}，此名{adds}，助全局平衡"
        else:
            comment = f"八字五行均衡，此名{adds}，平稳助力"
    return {"rows": rows, "comment": comment}


def build_report(surname: str, given: str, gender: str, mode: str,
                 current_name: str, styles: List[str], custom: str,
                 bazi_result=None, retriever=None, rng: Optional[random.Random] = None,
                 api_key: str = "") -> dict:
    """深度报告(付费): 八字概览 + 单名契合 + 改名对比 + 备选 15 + 落款。"""
    rng = rng or random.Random()
    yongshen = (bazi_result.yongshen if bazi_result else "") or ""
    counts = bazi_result.wuxing if bazi_result else None

    report: dict = {
        "surname": surname, "given": given, "full": surname + given,
        "gender": gender, "mode": mode, "footnote": "未填时辰按子时计 · 仅供参考 · 非科学承诺",
    }

    # ① 八字概览
    if bazi_result is not None:
        report["bazi"] = {
            "pillars": list(bazi_result.bazi or []),
            "day_master": bazi_result.day_master,
            "wuxing": wuxing_overview(bazi_result.wuxing),
            "yongshen": yongshen,
            "note": "未填时辰，按子时计，仅供五行参考" if getattr(bazi_result, "_hour_note", "") else "",
        }

    # ② 单名深度: 五维 + 补益矩阵 + 五格/三才详情
    scored = score_name(surname, given, gender, styles, yongshen, counts)
    xm_detail = {}
    try:
        from .xingming import get_stroke_count, XingmingEngine
        if all(get_stroke_count(c) > 0 for c in surname + given):
            res = XingmingEngine().analyze(surname, given)
            xm_detail = {
                "wuge": res.wuge,
                "sancai": res.sancai,
                "sancai_ji": res.sancai_ji,
                "cells": {k: res.analysis.get(k, {}).get("吉凶", "") for k in res.analysis},
            }
    except Exception:
        pass
    report["name_analysis"] = {
        "dims": scored["dims"], "total": scored["total"], "level": scored["level"],
        "buyi_matrix": build_buyi_matrix(given, yongshen, counts),
        **xm_detail,
    }

    # ③ 改名对比(成人场景)
    if mode == "adult" and current_name:
        cur = score_name(surname, current_name, gender, styles, yongshen, counts)
        issues = []
        try:
            from .xingming import get_stroke_count, XingmingEngine
            if all(get_stroke_count(c) > 0 for c in surname + current_name):
                r = XingmingEngine().analyze(surname, current_name)
                if r.sancai_ji in ("凶", "半吉"):
                    issues.append(f"三才配置{r.sancai_ji}：天格{r.wuge.get('天格')}、人格{r.wuge.get('人格')}、地格{r.wuge.get('地格')}的五行组合欠佳")
                for k in ("天格", "人格", "地格", "外格", "总格"):
                    jx = r.analysis.get(k, {}).get("吉凶", "")
                    if jx == "凶":
                        issues.append(f"{k}{r.wuge.get(k)}画为凶数：{r.analysis.get(k, {}).get('详解', '')[:20]}")
        except Exception:
            pass
        if counts and yongshen:
            _use, helpful = parse_yongshen(yongshen)
            for ch in current_name:
                wx = char_wuxing(ch)
                if wx and helpful and wx not in helpful and counts.get(wx, 0) >= 3:
                    issues.append(f"「{ch}」字属{wx}，与八字过剩{wx}同气，未助用神")
        verdict = "建议改名" if cur["total"] < 80 or issues else "可改可不改，改名需综合证件与社交成本"
        report["rename_compare"] = {
            "current": {"given": current_name, "full": surname + current_name,
                        "total": cur["total"], "dims": cur["dims"], "issues": issues},
            "recommended": {"given": given, "full": surname + given,
                            "total": scored["total"], "dims": scored["dims"]},
            "verdict": verdict,
            "notice": "改名后需同步证件/银行/社保等登记，请知悉",
        }

    # ④ 备选扩展: 15 名(规则库生成,按契合度排序;不含深度推理,防复制)
    extras: List[dict] = []
    gender_abbr = _GENDER_TAG.get(gender, "b")
    candidates = rule_generate(surname, gender_abbr, styles, rng, count=40)
    seen = {given}
    ranked = []
    for g in candidates:
        if g in seen:
            continue
        seen.add(g)
        s = score_name(surname, g, gender, styles, yongshen, counts)
        ranked.append((s["total"], s["dims"]["五行"], s["dims"]["数理"], g, s))
    ranked.sort(key=lambda t: (-t[0], -t[1], -t[2]))
    for total, _wx, _sl, g, s in ranked[:15]:
        extras.append({
            "given": g, "full": surname + g, "score": total, "level": s["level"],
            "dims": s["dims"], "ping": _rule_ping(g),
            "src": lookup_source(retriever, g),
        })
    report["extras"] = extras

    # ⑤ 名笺落款
    comment = report.get("name_analysis", {}).get("buyi_matrix", {}).get("comment", "")
    report["seal"] = {
        "recommended": surname + given,
        "summary": f"综合{scored['total']}分，{scored['level']}。{comment}" if comment
                   else f"综合{scored['total']}分，{scored['level']}。音形义上佳，可入名笺。",
        "level": scored["level"],
    }
    return report
