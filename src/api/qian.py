"""抽灵签 API:签文库(8 支原型原文 + 三签种 251 支)/摇签/收藏/历史。全接口 require_user。

- 签文库:
  - QIAN_LIBRARY: 8 支原型原文签,字段 no(签号)/jx(吉凶标签)/cls(等级 up|mid|low)/
    poem(四行签诗)/jie(解曰)/suo(所求)——逐字使用,scripts/test_qian_api.py
    含逐字一致性断言(与独立副本比对),防手误。
  - QIAN_KINDS: 按 kind 分组全量签库(original 8 + guanyin 100 + guandi 100 +
    xuanwushan 51),数据来自 src/api/qian_data/*.json:
      - guanyin:  观音灵签 100 支(MIT 源 bagmhmy110/t-qiuqian data.json)
      - guandi:   关帝灵签 100 支(经典公有领域签谱,维基文库《關聖帝君靈籤》对照)
      - xuanwushan: 玄武山佛祖灵签 51 支(MIT 源 bagmhmy110/t-qiuqian xuanwu_data.json)
- 摇签: 纯随机(random.choice),每摇可换;每日首摇提示由前端本地日期标记
  实现(娱乐用途,无额度/频率限制)。
- 收藏: 轻量表 qian_saves,UNIQUE(user_id, no, kind) 防重复(跨签种同 no 不冲突);
  已收藏返回 already=true 提示,不报错。
- 历史: 近 20 条(no+jx+诗首行+时间,可按 kind 过滤),供「我的」页收藏/历史展示。
"""
import json
import logging
import random
from pathlib import Path
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

# ─────────────────────────── 三签种签库(数据文件 src/api/qian_data/*.json) ───────────────────────────
# kind 值域: original | guanyin | guandi | xuanwushan;QIAN_KINDS["original"] 即 QIAN_LIBRARY。
KIND_ORDER = ["original", "guanyin", "guandi", "xuanwushan"]
KIND_NAMES = {"original": "灵签原版", "guanyin": "观音灵签",
              "guandi": "关帝灵签", "xuanwushan": "玄武山签"}
_KIND_FILE = {"guanyin": "guanyin.json", "guandi": "guandi.json",
              "xuanwushan": "xuanwushan.json"}
_QIAN_DATA_DIR = Path(__file__).parent / "qian_data"


def _load_kind_cards(kind: str) -> list:
    """从数据文件加载一个签种的全部签卡(启动时一次性加载)。"""
    with open(_QIAN_DATA_DIR / _KIND_FILE[kind], encoding="utf-8") as f:
        cards = json.load(f)
    for c in cards:
        assert isinstance(c["no"], int) and len(c["poem"]) == 4, \
            f"{kind} 签库数据非法: {c.get('no')}"
        assert c["jx"] in ("上上签", "上吉签", "中吉签", "中平签", "下签"), \
            f"{kind} 签 {c['no']} jx 未归一化: {c['jx']}"
        assert c["cls"] in ("up", "mid", "low"), f"{kind} 签 {c['no']} cls 非法"
    return cards


QIAN_KINDS = {"original": QIAN_LIBRARY}
QIAN_KINDS.update({k: _load_kind_cards(k) for k in _KIND_FILE})
QIAN_KIND_NOS = {kind: {c["no"] for c in cards}
                 for kind, cards in QIAN_KINDS.items()}

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
    """按签号取原版签卡;不存在 404。"""
    card = QIAN_BY_NO.get(no)
    if card is None:
        raise HTTPException(status_code=404, detail=f"签不存在: 第 {no} 签")
    return card


def _check_kind(kind: str):
    """校验 kind 合法;非法 400(路由参数统一走这里)。"""
    if kind not in QIAN_KINDS:
        raise HTTPException(status_code=400,
                            detail=f"无效签种: {kind}(可选 {KIND_ORDER})")


# ─────────────────────────── 请求模型 ───────────────────────────

class DrawBody(BaseModel):
    kind: str = "original"


class SaveBody(BaseModel):
    no: int
    kind: str = "original"


# ─────────────────────────── 摇签 / 收藏 / 历史 ───────────────────────────

@router.post("/draw")
def draw(body: Optional[DrawBody] = None, uid: str = Depends(require_user)):
    """摇一支签(可指定签种 kind,缺省 original):纯随机(random.choice),
    返回完整签卡(no/jx/cls/poem/jie/suo)。

    每摇可换(无每日锁定);每日首摇提示由前端本地日期标记实现。
    """
    kind = (body.kind if body else "original")
    _check_kind(kind)
    return {"card": random.choice(QIAN_KINDS[kind])}


@router.post("/save")
def save(body: SaveBody, uid: str = Depends(require_user)):
    """收藏一支签(幂等)。UNIQUE(user_id, no, kind) 防重复(跨签种同 no 不冲突):
    - 首次收藏 → saved=true, already=false
    - 已收藏 → saved=false, already=true(提示,不报错)
    """
    _check_kind(body.kind)
    if body.no not in QIAN_KIND_NOS[body.kind]:
        raise HTTPException(status_code=400, detail=f"无效签号: {body.no}")
    saved, already = _qdao().save(uid, body.no, body.kind)
    if already:
        return {"saved": False, "already": True,
                "msg": "这张签已在您的收藏中", "no": body.no, "kind": body.kind}
    return {"saved": True, "already": False, "msg": "签卡已保存",
            "no": body.no, "kind": body.kind}


@router.get("/history")
def history(kind: Optional[str] = None, uid: str = Depends(require_user)):
    """收藏历史(近 20 条): no+jx+诗首行+时间。按收藏时间倒序。
    可选 kind 过滤(缺省返回全部签种)。"""
    if kind is not None:
        _check_kind(kind)
    rows = _qdao().list_history(uid, limit=20, kind=kind)
    items = []
    for r in rows:
        cards = QIAN_KINDS.get(r["kind"], [])
        card = next((c for c in cards if c["no"] == r["no"]), None)
        if card is None:
            continue
        items.append({"no": card["no"], "kind": r["kind"], "jx": card["jx"],
                      "poem_first": card["poem"][0], "drawn_at": r["drawn_at"]})
    return {"items": items}
