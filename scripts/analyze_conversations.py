#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对话数据分析脚本（方案 §7.3：情绪分布 / 话题(意图)分布 / 反馈联动统计）

读取 sessions（对话）+ consultations（咨询/反馈），输出：
  1. 概览：消息量 / 用户数 / 时间范围
  2. 意图分布（sessions.intent + consultations.intent）
  3. 情绪分布（sessions.emotion，LLM 分析结果）
  4. 检索命中分布（retrieval_hit: hit/miss/unused）
  5. 工具调用分布（tool_calls JSON：类型/命中）
  6. 反馈统计（consultations.feedback）与 反馈联动（按意图/情绪的反馈率）

用法：
  .venv/bin/python scripts/analyze_conversations.py
  .venv/bin/python scripts/analyze_conversations.py --db /path/to.db --since 2026-07-01
  .venv/bin/python scripts/analyze_conversations.py --json   # 机器可读 JSON
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from src.config import load_settings

INTENT_CN = {
    "bazi": "八字", "ziwei": "紫微", "liuyao": "六爻", "fengshui": "风水",
    "zeri": "择日", "mianxiang": "面相", "dream": "解梦", "calendar": "日历",
    "advisor": "建议", "hehun": "合婚", "qimen": "奇门", "xingming": "姓名",
    "xuetang": "学堂", "free_chat": "自由对话", "": "未知",
}
EMOTION_CN = {
    "happy": "开心", "neutral": "平稳", "sadness": "低落", "anxiety": "焦虑",
    "anger": "烦躁", "heartbreak": "感情困扰", "confusion": "迷茫",
}


def _connect(db_path):
    from src.storage.models import connect
    return connect(db_path)


def _pct(n, total):
    return round(100.0 * n / total, 1) if total else 0.0


def _dist(counter: Counter, total: int, cn_map=None, top: int = 12) -> list:
    items = []
    for k, v in counter.most_common(top):
        label = (cn_map or {}).get(k, k) if k != "" else "未标注"
        items.append({"key": k, "label": label, "count": v, "pct": _pct(v, total)})
    return items


def analyze(db_path: str, since: str) -> dict:
    conn = _connect(db_path)
    cond = " WHERE created_at >= ?" if since else ""
    params = (since,) if since else ()

    # ── sessions ──────────────────────────────────────────────
    total_msgs = conn.execute(
        f"SELECT COUNT(*) FROM sessions{cond}", params).fetchone()[0]
    users = conn.execute(
        f"SELECT COUNT(DISTINCT user_id) FROM sessions{cond}", params).fetchone()[0]
    time_range = conn.execute(
        "SELECT MIN(created_at), MAX(created_at) FROM sessions").fetchone()

    intent_cnt = Counter()
    emotion_cnt = Counter()
    retrieval_cnt = Counter()
    tool_type_cnt = Counter()
    tool_hit_cnt = Counter()
    model_cnt = Counter()
    safety_cnt = Counter()
    for row in conn.execute(
            f"""SELECT intent, emotion, tool_calls, retrieval_hit, model, safety_flag
                FROM sessions{cond}""", params).fetchall():
        intent_cnt[row[0] or ""] += 1
        emotion_cnt[row[1] or ""] += 1
        retrieval_cnt[row[2] or "unused"] += 1
        model_cnt[row[4] or ""] += 1
        if row[5]:
            safety_cnt[row[5]] += 1
        if row[2]:
            try:
                calls = json.loads(row[2])
                for c in calls:
                    tool_type_cnt[c.get("type", "?")] += 1
                    tool_hit_cnt[bool(c.get("hit"))] += 1
            except (ValueError, TypeError):
                pass

    # ── consultations 反馈联动 ─────────────────────────────────
    fb_total = conn.execute(
        "SELECT COUNT(*) FROM consultations"
        " WHERE feedback IS NOT NULL AND feedback != ''").fetchone()[0]
    fb_pos = conn.execute(
        "SELECT COUNT(*) FROM consultations WHERE feedback='positive'").fetchone()[0]
    fb_neg = conn.execute(
        "SELECT COUNT(*) FROM consultations WHERE feedback='negative'").fetchone()[0]

    # 按意图的反馈率
    fb_by_intent = defaultdict(lambda: [0, 0])
    for row in conn.execute(
            "SELECT intent, feedback FROM consultations"
            " WHERE feedback IS NOT NULL AND feedback != ''").fetchall():
        fb_by_intent[row[0] or ""][0] += 1
        if row[1] == "positive":
            fb_by_intent[row[0] or ""][1] += 1

    # 反馈联动：情绪 × 反馈（同用户同日近似关联，仅供趋势参考）
    fb_by_emotion = defaultdict(lambda: [0, 0])
    for row in conn.execute(
            """SELECT c.intent, c.feedback, c.created_at, s.emotion
               FROM consultations c
               JOIN sessions s ON s.user_id = c.user_id
               WHERE c.feedback IS NOT NULL AND c.feedback != ''
                 AND substr(s.created_at,1,10) = substr(c.created_at,1,10)
                 AND s.intent = c.intent""").fetchall():
        emo = row[3] or ""
        fb_by_emotion[emo][0] += 1
        if row[1] == "positive":
            fb_by_emotion[emo][1] += 1

    conn.close()

    intent_rows = [_d for _d in _dist(intent_cnt, max(1, total_msgs), INTENT_CN)]
    # intent 按消息量归一（每条消息一行；自由对话消息最多属正常）
    return {
        "generated_at": datetime.now().isoformat(),
        "overview": {
            "messages": total_msgs,
            "users": users,
            "time_range": [time_range[0], time_range[1]] if time_range else None,
            "feedback_total": fb_total,
            "feedback_positive": fb_pos,
            "feedback_positive_rate": _pct(fb_pos, fb_total),
        },
        "intent_distribution": intent_rows,
        "emotion_distribution": _dist(emotion_cnt, max(1, total_msgs), EMOTION_CN),
        "retrieval_hit_distribution": _dist(retrieval_cnt, max(1, total_msgs)),
        "tool_usage": {
            "by_type": [{"type": k, "count": v} for k, v in tool_type_cnt.most_common()],
            "hit_rate": _pct(tool_hit_cnt.get(True, 0), max(1, sum(tool_hit_cnt.values()))),
        },
        "safety_events": [{"flag": k, "count": v} for k, v in safety_cnt.items()],
        "model_distribution": [{"model": k, "count": v} for k, v in model_cnt.most_common()],
        "feedback_by_intent": [
            {"intent": k, "label": INTENT_CN.get(k, k), "feedback": n, "positive": p,
             "positive_rate": _pct(p, n)}
            for k, (n, p) in sorted(fb_by_intent.items(),
                                    key=lambda x: -x[1][0])
        ],
        "feedback_by_emotion": [
            {"emotion": k, "label": EMOTION_CN.get(k, k), "feedback": n, "positive": p,
             "positive_rate": _pct(p, n)}
            for k, (n, p) in sorted(fb_by_emotion.items(),
                                    key=lambda x: -x[1][0])
        ],
    }


def render(data: dict) -> str:
    o = data["overview"]
    lines = [
        f"=== 易理明灯 · 对话数据分析（{data['generated_at'][:19]}） ===",
        f"消息量 {o['messages']} · 用户数 {o['users']} · 时间段 "
        f"{o['time_range'][0] if o['time_range'] else '-'} ~ "
        f"{o['time_range'][1] if o['time_range'] else '-'}",
        f"反馈 {o['feedback_total']} 条（好评 {o['feedback_positive']}，"
        f"好评率 {o['feedback_positive_rate']}%）",
        "",
        "【意图分布】",
        *[f"  {d['label']:<6} {d['count']:>6}  {d['pct']}%" for d in data["intent_distribution"]],
        "",
        "【情绪分布】",
        *[f"  {d['label']:<6} {d['count']:>6}  {d['pct']}%" for d in data["emotion_distribution"]],
        "",
        "【检索命中】",
        *[f"  {d['key']:<8} {d['count']:>6}  {d['pct']}%" for d in data["retrieval_hit_distribution"]],
        "",
        "【工具调用】",
        *[f"  {t['type']:<6} {t['count']}" for t in data["tool_usage"]["by_type"]],
        f"  工具命中率 {data['tool_usage']['hit_rate']}%",
        "",
        "【反馈联动 · 按意图】",
        *[f"  {d['label']:<6} 反馈{d['feedback']:>4} 好评{d['positive']:>4}  {d['positive_rate']}%"
          for d in data["feedback_by_intent"]],
        "",
        "【反馈联动 · 按情绪（同日近似）】",
        *[f"  {d['label']:<8} 反馈{d['feedback']:>4} 好评{d['positive']:>4}  {d['positive_rate']}%"
          for d in data["feedback_by_emotion"]],
    ]
    if data["safety_events"]:
        lines += ["", "【安全事件留痕】",
                  *[f"  {d['flag']} × {d['count']}" for d in data["safety_events"]]]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="对话数据分析（情绪/意图/反馈联动）")
    ap.add_argument("--db", default="", help="SQLite 路径（默认 settings.db_path）")
    ap.add_argument("--since", default="", help="仅统计该日期以来的数据")
    ap.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
    args = ap.parse_args(argv)

    settings = load_settings()
    db_path = args.db or str(settings.db_path)
    data = analyze(db_path, args.since)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(render(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
