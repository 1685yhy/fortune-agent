"""存量数据直读：对话问'我的档案/解梦/历史/收藏…' → 直读秒回，不重走全流程。"""
import json, logging
from src.storage.dao import _decrypt_or_plain

logger = logging.getLogger(__name__)

# 类别 → 触发关键词（命中即直读）
CATEGORY_KEYWORDS = {
    "档案": ["档案", "生辰", "出生信息", "我的八字信息", "什么时辰"],
    "排盘": ["排过", "排的盘", "我的盘", "上次的盘"],
    "解梦": ["解过什么梦", "以前.*梦", "梦的解读", "上次那个梦"],
    "历史": ["之前聊过", "以前说过", "历史对话", "上次聊", "之前说过什么"],
    "灵签": ["摇过什么签", "抽过什么签", "我的签"],
    "名笺": ["取过什么名", "起过什么名", "我的名字"],
    "灯语": ["灯语", "昨晚的灯"],
    "择吉": ["选的日子", "吉日", "择吉计划", "办事清单"],
    "晨笺": ["晨笺", "今早的", "早上的运势"],
    "收藏": ["收藏过", "我收藏的"],
    "会员": ["会员", "我花了多少", "充值", "额度"],
}

class RecordQuery:
    def __init__(self, dao, person_dao, session_dao, chart_dao,
                 qian_dao=None, ming_dao=None, lamp_dao=None,
                 zeri_dao=None, jian_dao=None, member_dao=None):
        self.dao, self.person_dao = dao, person_dao
        self.session_dao, self.chart_dao = session_dao, chart_dao
        # 轻量 DAO（Task 8/9 依赖）缺省 None —— 对应类别直读直接返回 None
        # （不建表、不伪造实现；None 判断由各类 _q_* 守卫）
        self.qian_dao = qian_dao
        self.ming_dao = ming_dao
        self.lamp_dao = lamp_dao
        self.zeri_dao = zeri_dao
        self.jian_dao = jian_dao
        self.member_dao = member_dao

    def direct_query(self, user_id: str, msg: str) -> str | None:
        if not msg:
            return None
        import re
        for cat, kws in CATEGORY_KEYWORDS.items():
            for kw in kws:
                if re.search(kw, msg):
                    handler = getattr(self, f"_q_{cat}", None)
                    if handler:
                        out = handler(user_id)
                        if out:
                            return out
        return None

    # —— 各类直读（解密仅服务端内存）——
    def _q_档案(self, user_id):
        p = self.person_dao.get_default_person(user_id)
        if not p:
            return "还没有档案，告诉我出生年月日时我帮你建档。"
        return (f"你的档案（命主：{p.get('name')}）："
                f"{p.get('birth_year')}年{p.get('birth_month')}月{p.get('birth_day')}日"
                f"{p.get('birth_hour')}时 · 出生地{p.get('city') or '未填'} · {p.get('gender') or '性别未知'}")

    def _q_排盘(self, user_id):
        if not self.chart_dao:
            return None
        c = self.chart_dao.get_latest_chart(user_id)
        if not c:
            return None
        bazi = c["bazi_json"].get("bazi") or []
        return "最近排过的盘：" + " ".join(bazi) + \
               f"（{c['created_at']}，日主 {c['bazi_json'].get('day_master','')}）"

    def _q_解梦(self, user_id):
        rows = self.dao.get_user_consultations(user_id, intent="dream", limit=5)
        if not rows:
            return None
        parts = [f"解过 {len(rows)} 次梦："]
        for r in rows:
            parts.append(f"· {_decrypt_or_plain(r.get('question',''))[:30]} → "
                         f"{_decrypt_or_plain(r.get('analysis_preview',''))[:50]}")
        return "\n".join(parts)

    def _q_历史(self, user_id):
        if not self.session_dao:
            return None
        s = self.session_dao.get_summary(user_id)
        if not s or not s.get("summary"):
            return None
        mem = ""
        if s.get("memories"):
            mem = "，记得：" + "；".join(str(m) for m in s["memories"][:5])
        return f"之前的聊天摘要：{s['summary'][:200]}{mem}"

    def _q_灵签(self, user_id):
        if not self.qian_dao: return None
        try:
            saves = self.qian_dao.list_history(user_id, limit=10)
        except Exception:
            return None
        return f"收藏的签：{len(saves)} 支（签号 {[s['no'] for s in saves[:10]]}）" if saves else None

    def _q_名笺(self, user_id):
        if not self.ming_dao: return None
        try:
            saves = self.ming_dao.list_saved(user_id)
        except Exception:
            return None
        if not saves:
            return None
        parts = [f"取过的名字：{len(saves)} 个"]
        for s in saves[:10]:
            style = s.get("style_note") or "未标注风格"
            parts.append(f"· {s.get('full','')}（{s.get('score',0)}分，{style}）")
        return "\n".join(parts)

    def _q_灯语(self, user_id):
        if not self.lamp_dao: return None
        try:
            rows = self.lamp_dao.list_history(user_id, limit=1)
        except Exception:
            return None
        if not rows:
            return None
        l = rows[0]
        return f"最近一条灯语（{l['date']}）：{l['text']}"

    def _q_择吉(self, user_id):
        if not self.zeri_dao: return None
        try:
            plans = self.zeri_dao.list_plans(user_id, limit=3)
        except Exception:
            return None
        return "\n".join(f"· {p['scene']} → {p['lucky_date']}" for p in plans) if plans else None

    def _q_晨笺(self, user_id):
        if not self.jian_dao: return None
        card = self.jian_dao.get_card(user_id)  # Task 8 实现
        return f"今早的晨笺：{card['card_json']['day_ganzhi']} 宜{'/'.join(card['card_json'].get('suitable',[]))}" if card else None

    def _q_收藏(self, user_id):
        if not hasattr(self, "fav_dao") or not self.fav_dao: return None
        favs = self.fav_dao.list_favorites(user_id, limit=10)  # Task 9 实现
        return "\n".join(f"· {f['type']}: {f['summary'][:40]}" for f in favs) if favs else None

    def _q_会员(self, user_id):
        if not self.member_dao: return None
        try:
            m = self.member_dao.get_membership(user_id)
        except Exception:
            return None
        if not m:
            return None
        limit = m.get("queries_limit")
        return f"会员档位：{m.get('plan_label') or m.get('plan')}，额度 {m.get('queries_used', 0)}/{limit if limit is not None else '不限'}"
