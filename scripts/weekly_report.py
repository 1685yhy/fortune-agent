#!/usr/bin/env python3
"""Weekly fortune report generator - turns one-time readings into ongoing relationships.

Based on user research: #1 desire is "定期运势追踪与更新".
This generates personalized weekly reports for each user based on their bazi chart.

"准是最基础，真功夫在于持续连接" — real users want subscription updates.
"""
import sys, json, os, re
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.engines.bazi import BaziEngine
from src.rag.retriever import Retriever
from src.rag.embedder import Embedder
from src.config import load_settings

def get_current_week():
    """Get current week's Monday-Sunday range."""
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday

def generate_weekly(user_bazi: dict, user_id: str, retriever, api_key: str = ""):
    """Generate a personalized weekly fortune report."""
    import httpx, os
    if not api_key:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        return "本周运势：保持平和心态，顺势而为。幸运日：周三。幸运色：蓝色。"

    monday, sunday = get_current_week()
    week_str = f"{monday.strftime('%m/%d')}-{sunday.strftime('%m/%d')}"

    # Build prompt with user's chart data
    chart_info = f"八字：{' '.join(user_bazi.get('bazi', ['?']))} 日主：{user_bazi.get('day_master', '?')}"

    prompt = f"""你是一位温暖专业的命理顾问。请为一周运势报告生成内容。

用户信息：
- 八字：{chart_info}
- 上周反馈：{user_bazi.get('last_feedback', '暂无')}

请生成一个温馨、具体的本周运势报告（200-300字）：
1. 本周整体运势基调（一句话）
2. 本周注意事项（1-2条具体建议）
3. 本周幸运日/幸运色/幸运方向
4. 一句积极赋能的话

风格要求：像朋友发来的关心消息，不要像新闻通告。"""

    try:
        resp = httpx.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 400, "temperature": 0.7},
            timeout=30.0,
        )
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"本周运势：保持平和心态，顺势而为。幸运日：周三。幸运色：蓝色。"

def batch_generate(api_key: str, limit: int = 50):
    """Generate weekly reports for all users with bazi data."""
    settings = load_settings()
    import sqlite3
    conn = sqlite3.connect(str(settings.db_path))
    users = conn.execute(
        "SELECT user_id, bazi_info FROM users WHERE bazi_info IS NOT NULL LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()

    embedder = Embedder()
    retriever = Retriever(str(settings.vectordb_dir), embedder)

    count = 0
    for user_id, bazi_json in users:
        try:
            user_bazi = json.loads(bazi_json)
            report = generate_weekly(user_bazi, user_id, retriever, api_key)
            # Save report
            report_dir = Path(str(settings.data_dir)) / "reports"
            report_dir.mkdir(exist_ok=True)
            week_file = report_dir / f"{user_id}_{datetime.now().strftime('%Y%W')}.txt"
            week_file.write_text(report, encoding='utf-8')
            count += 1
            print(f"[{count}/{len(users)}] Generated report for {user_id}: {report[:80]}...")
        except Exception as e:
            print(f"Skipping {user_id}: {e}")

    print(f"\nGenerated {count} weekly reports")

if __name__ == '__main__':
    api_key = os.environ.get('DEEPSEEK_API_KEY', '')
    if not api_key:
        print("Set DEEPSEEK_API_KEY env var")
        sys.exit(1)
    batch_generate(api_key)
