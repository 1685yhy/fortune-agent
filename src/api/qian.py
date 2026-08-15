"""抽灵签 API:签文库(8 支,原型原文)/摇签/收藏/历史。全接口 require_user。

- 签文库: 8 支签,字段 no(签号)/jx(吉凶标签)/cls(等级 up|mid|low)/
  poem(四行签诗)/jie(解曰)/suo(所求)——原型原文逐字使用,scripts/test_qian_api.py
  含逐字一致性断言(与独立副本比对),防手误。
- 摇签: 纯随机(random.choice),每摇可换;每日首摇提示由前端本地日期标记
  实现(娱乐用途,无额度/频率限制)。
- 收藏: 轻量表 qian_saves,UNIQUE(user_id,no) 防重复;已收藏返回 already=true
  提示,不报错。
- 历史: 近 20 条(no+jx+诗首行+时间),供「我的」页收藏/历史展示。
"""
import logging
import random
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.security.auth import require_user
from src.storage.qian_dao import QianDAO

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/qian", tags=["qian"])

# ─────────────────────────── 签文库(原型原文 8 支,逐字使用,不得增删改) ───────────────────────────
QIAN_LIBRARY = [
    {"no": 7, "jx": "上上签", "cls": "up",
     "poem": ["枯木逢春再发花", "云开月出见归鸦", "向来求事皆如意", "何必迟迟问晚霞"],
     "jie": "所求之事如枯木逢春,正是转机萌动之时。旧事可翻篇,新事有贵人扶持;心里想的那件事,放心去做,时机已到。",
     "suo": "所问诸事 · 皆可顺遂"},
    {"no": 12, "jx": "上吉签", "cls": "up",
     "poem": ["顺水行舟稳且安", "前头自有渡人滩", "莫嫌浪小行来缓", "过了此弯天地宽"],
     "jie": "此签主顺。眼前进展虽缓,却是一步一个脚印的稳;再过一程,便是开阔水面。莫急,莫改向,按原路走下去。",
     "suo": "事业 · 缓中得进"},
    {"no": 5, "jx": "中吉签", "cls": "mid",
     "poem": ["桥边问柳柳低头", "半是春来半是愁", "待得东风吹过岸", "一江明月照归舟"],
     "jie": "心有所悬,情有所牵。此事不必急于开口,待三五日后风头转向,自然有台阶可下。今夜先安睡,答案在梦里。",
     "suo": "感情 · 缓一缓再谈"},
    {"no": 9, "jx": "中吉签", "cls": "mid",
     "poem": ["灯下拾珠未辨真", "且看潮退见沙痕", "莫将心事轻言尽", "留待花时对故人"],
     "jie": "眼前的选项有真有假,先别急着亮底牌。过段时日水落石出,再谈不迟。守住口,就是守住机会。",
     "suo": "抉择 · 宜守待明"},
    {"no": 3, "jx": "中平签", "cls": "mid",
     "poem": ["山径独行莫问程", "一程烟雨一程晴", "他年若到桃源口", "再看落花听水声"],
     "jie": "此签平平,不算好也不算坏。独行路上,把每一步走稳就是赢;所求之事,七分靠自己,三分看机缘。",
     "suo": "诸事 · 平顺务实"},
    {"no": 15, "jx": "中平签", "cls": "mid",
     "poem": ["檐下听雨夜迟迟", "心事如丝结万枝", "莫问归期何日至", "且留灯影照人时"],
     "jie": "近来多思多虑,是时候给自己放个假。想不清的事,搁两日再看,多半自己就清了。灯下养神,胜过灯下纠结。",
     "suo": "心境 · 宜歇不宜急"},
    {"no": 1, "jx": "下签", "cls": "low",
     "poem": ["晚渡无舟且系缆", "明朝潮涨自浮还", "莫因一步留行处", "误了东风水一湾"],
     "jie": "此签提醒:眼下时机未熟,强行出发容易搁浅。建议缓行三日,等潮涨风转;不是不成,是时辰未到。",
     "suo": "出行 · 宜缓不宜急"},
    {"no": 20, "jx": "上吉签", "cls": "up",
     "poem": ["高阁登临眼界宽", "四方山水入栏杆", "此时不作凌云想", "更待何年展羽翰"],
     "jie": "此签大吉,主升阶与远见。站高一步看问题,眼前纠结自消;若有升迁、求学、远行之事,此时正是好时机。",
     "suo": "前程 · 可上层楼"},
]
QIAN_BY_NO = {c["no"]: c for c in QIAN_LIBRARY}

_dao: Optional[QianDAO] = None


def setup(dao):
    """在主应用生命周期中注入 DAO(仿 zeri.py setup 模式;测试注入临时库 stub)。"""
    global _dao
    _dao = dao


def _qdao() -> QianDAO:
    global _dao
    if _dao is None:
        from src.storage.dao import get_conn
        _dao = QianDAO(get_conn())
    return _dao


def _card(no: int) -> dict:
    """按签号取签卡;不存在 404。"""
    card = QIAN_BY_NO.get(no)
    if card is None:
        raise HTTPException(status_code=404, detail=f"签不存在: 第 {no} 签")
    return card


# ─────────────────────────── 请求模型 ───────────────────────────

class SaveBody(BaseModel):
    no: int


# ─────────────────────────── 摇签 / 收藏 / 历史 ───────────────────────────

@router.post("/draw")
def draw(uid: str = Depends(require_user)):
    """摇一支签:纯随机(random.choice),返回完整签卡(no/jx/cls/poem/jie/suo)。

    每摇可换(无每日锁定);每日首摇提示由前端本地日期标记实现。
    """
    return {"card": random.choice(QIAN_LIBRARY)}


@router.post("/save")
def save(body: SaveBody, uid: str = Depends(require_user)):
    """收藏一支签(幂等)。UNIQUE(user_id,no) 防重复:
    - 首次收藏 → saved=true, already=false
    - 已收藏 → saved=false, already=true(提示,不报错)
    """
    if body.no not in QIAN_BY_NO:
        raise HTTPException(status_code=400, detail=f"无效签号: {body.no}")
    saved, already = _qdao().save(uid, body.no)
    if already:
        return {"saved": False, "already": True,
                "msg": "这张签已在您的收藏中", "no": body.no}
    return {"saved": True, "already": False, "msg": "签卡已保存", "no": body.no}


@router.get("/history")
def history(uid: str = Depends(require_user)):
    """收藏历史(近 20 条): no+jx+诗首行+时间。按收藏时间倒序。"""
    rows = _qdao().list_history(uid, limit=20)
    items = []
    for r in rows:
        card = QIAN_BY_NO.get(r["no"])
        if card is None:
            continue
        items.append({"no": card["no"], "jx": card["jx"],
                      "poem_first": card["poem"][0], "drawn_at": r["drawn_at"]})
    return {"items": items}
