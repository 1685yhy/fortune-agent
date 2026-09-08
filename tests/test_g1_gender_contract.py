# -*- coding: utf-8 -*-
"""Task G1（P0 紧急，2026-08-29 生产实测 P15 立案）：性别契约统一 +
档案读取单一事实源 + 对话纠正性别自动重排。

三条根因：
P0-A 前端 persons.genderCode 产 male/female（persons.js:47-51），引擎
     bazi.py calc_gender 只认中文男/女 → 任何经编辑页/建档页设「女」的
     档案引擎一律按男排（P15 实测：档案女、排盘按男 8岁起运）。
P0-B _get_user_birth_profile 优先陈旧的 users.bazi_info → 编辑页改
     persons 永不生效；排盘又把旧性别写回两份档案。
P0-C _extract_partial_birth 性别正则只认独立「男/女」→「我是女孩儿」
     「我是女生」全部漏识别，识别不出就不重排不更新。

本文件按 brief §修复方向 1-5 逐项落地：
- P0-C：口语性别词识别（新词 + 防误伤回归 + 生产原句）
- P0-B：persons 优先读取 + 自愈回写 bazi_info（真实 DAO）
- 纠正重排：档案男 + 「我是女孩儿」→ 女命排盘 + 回执 + 档案双写
- 引擎 male/female 兼容
"""
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from unittest.mock import Mock  # noqa: E402

from src.bot.handler import MessageHandler  # noqa: E402

ARCHIVE_MALE = {"year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
                "city": "北京", "gender": "男"}


def make_handler(**kw) -> MessageHandler:
    """同 test_bazi_archive_priority.make_handler：object.__new__ 装配
    _handle_bazi 所需 Mock 属性（跑真实方法体）。"""
    h = object.__new__(MessageHandler)
    h.engine = Mock()
    h.llm = Mock()
    h.dao = Mock()
    h.dao.get_user_bazi.return_value = None
    h.retriever = Mock()
    h.memory = None
    h.memory_system = None
    h._downgraded = {}
    h._deep_night = {}
    h._analysis_facts = {}
    h.tool_logs = {}
    h._try_reuse_chart = Mock(return_value="")
    h._extract_bazi_info = Mock(return_value=None)
    h._get_user_birth_profile = Mock(return_value=dict(ARCHIVE_MALE))
    h._do_bazi_analysis = Mock(return_value="分析结果")
    h._gen_info_collection_prompt = Mock(return_value="渐进引导")
    h._gen_reuse_acknowledgment = Mock(return_value="")
    h._quick_flash = Mock(return_value="")
    h._emit_stream_event = Mock()
    h.session_dao = Mock()
    h.session_dao.get_context_for_llm.return_value = []
    for k, v in kw.items():
        setattr(h, k, v)
    return h


# ================================================================
# P0-C：对话口语性别识别（_extract_partial_birth 正则扩展）
# ================================================================

def test_g1_spoken_female_terms():
    """女系口语词全识别：女孩儿/女生/姑娘/丫头/女的/小姑娘/闺女/
    显式性别女 + 生产原句。"""
    h = make_handler()
    cases = [
        "我是女孩儿",
        "我是女生",
        "我是姑娘",
        "我是丫头",
        "我是女的",
        "我是小姑娘",
        "我是闺女",
        "我应该是…并且是女孩儿",   # 2026-08-29 生产实测原句（12:44 被忽略）
        "性别女",
    ]
    for msg in cases:
        r = h._extract_partial_birth(msg)
        assert r.get("gender") == "女", f"{msg!r} → {r}"


def test_g1_spoken_male_terms():
    """男系口语词全识别：男孩/男生/男的/小伙子/显式性别男。"""
    h = make_handler()
    cases = [
        "我是男孩",
        "我是男生",
        "我是男的",
        "我是小伙子",
        "性别男",
    ]
    for msg in cases:
        r = h._extract_partial_birth(msg)
        assert r.get("gender") == "男", f"{msg!r} → {r}"


def test_g1_spoken_gender_no_false_positive_regression():
    """防误伤回归：渣男/美女（前字粘连）不命中；女儿/儿子不带性别断言
    （归属判定归第三方链路，不入本人性别提取）。"""
    h = make_handler()
    for msg in ("我讨厌渣男", "我老婆是美女", "帮我女儿排个盘",
                "我儿子今年5岁了"):
        r = h._extract_partial_birth(msg)
        assert "gender" not in r, f"{msg!r} → {r}"


def test_g1_partial_full_birth_with_spoken_gender():
    """部分信息整句：「1999年2月26日 7点 北京 女的」→ 全键命中。"""
    h = make_handler()
    r = h._extract_partial_birth("1999年2月26日 7点 北京 女的")
    assert r["year"] == 1999 and r["month"] == 2 and r["day"] == 26
    assert r["hour"] == 7 and r["minute"] == 0
    assert r["city"] == "北京" and r["gender"] == "女"


# ================================================================
# P0-A：引擎 male/female 兼容（bazi.py calc_gender）
# ================================================================

def test_g1_engine_female_english_equals_chinese():
    """female 与 女 完全同盘：1999-02-26 07:00 北京 → 真太阳时修正 06:32 →
    卯时丁卯（默认开口径），阴年女顺排 起运 2年8月12天3时 → 虚岁3岁丁卯大运。

    R2-4（2026-09-03 产品裁决真太阳时默认开）：修正后锚点回归 R1-3 期实测值
    （R2-1 期默认关 07:00 = 辰时戊辰 的 9天19时 口径随默认反转退役）。"""
    from src.engines.bazi import BaziEngine
    eng = BaziEngine()
    r_cn = eng.calculate(1999, 2, 26, 7, 0, "北京", "女")
    r_en = eng.calculate(1999, 2, 26, 7, 0, "北京", "female")
    assert r_en.bazi == r_cn.bazi == ["己卯", "丙寅", "己酉", "丁卯"]
    assert list(r_en.qiyun_detail) == list(r_cn.qiyun_detail) == [2, 8, 12, 3, 24]
    assert r_en.qiyun_desc == r_cn.qiyun_desc == "出生后2年8月12天3时起运"
    assert r_en.dayun == r_cn.dayun
    assert r_en.dayun[0] == (3, "丁卯")  # 虚岁3岁丁卯大运（女命顺排）
    assert r_en.gender == "女"


def test_g1_engine_male_english_equals_chinese():
    """male 与 男 完全同盘：阴年男逆排 → 起运 7年2月17天21时（8岁乙丑）。

    R2-4（真太阳时默认开）：修正后锚点回归 R1-3 期实测值（R2-1 期默认关
    7年2月20天5时54分 口径随默认反转退役）。"""
    from src.engines.bazi import BaziEngine
    eng = BaziEngine()
    r_cn = eng.calculate(1999, 2, 26, 7, 0, "北京", "男")
    r_en = eng.calculate(1999, 2, 26, 7, 0, "北京", "male")
    assert r_en.bazi == r_cn.bazi
    assert list(r_en.qiyun_detail) == list(r_cn.qiyun_detail) == [7, 2, 17, 21, 54]
    assert r_en.dayun == r_cn.dayun
    assert r_en.dayun[0] == (8, "乙丑")
    assert r_en.gender == "男"


def test_g1_engine_unknown_defaults_male():
    """unknown（P1-3 既定默认）与 男 同盘；结果性别保留 unknown 标记
    （中性表述信号，P1-3 行为保持）。"""
    from src.engines.bazi import BaziEngine
    eng = BaziEngine()
    r_u = eng.calculate(1999, 2, 26, 7, 0, "北京", "unknown")
    r_m = eng.calculate(1999, 2, 26, 7, 0, "北京", "男")
    assert r_u.bazi == r_m.bazi
    assert r_u.dayun == r_m.dayun
    assert r_u.gender == "unknown"


# ================================================================
# P0-B：_get_user_birth_profile persons 优先 + 自愈（真实 DAO）
# ================================================================

def _real_handler(tmp_path):
    """真实 UserDAO + 空 chart_dao 装配（跑真实 _get_user_birth_profile 与
    _save_bazi_records 落库路径）。"""
    from src.storage.dao import UserDAO
    from src.storage.chart_dao import ChartDAO
    h = object.__new__(MessageHandler)
    h.dao = UserDAO(str(tmp_path / "u.db"))
    h.chart_dao = ChartDAO(str(tmp_path / "c.db"))
    h.session_dao = None
    h.memory = None
    h.memory_system = None
    h._analysis_facts = {}
    return h


def _create_person(tmp_path, user_id, gender, birth_year=1999, is_default=True):
    from src.storage.person_dao import PersonDAO
    pdao = PersonDAO(str(tmp_path / "u.db"))
    pdao.create_person(
        user_id, name="我", relation="自己", is_default=is_default,
        birth={"gender": gender, "birth_year": birth_year, "birth_month": 2,
               "birth_day": 26, "birth_hour": 7, "birth_minute": 0,
               "calendar": "solar", "city": "北京"})
    return pdao


def test_g1_profile_persons_first_when_bazi_info_stale(tmp_path):
    """persons 默认档案（女）优先于陈旧 bazi_info（男）→ 返回 persons，
    且自愈把 bazi_info 单向回写为女（保留 bazi 四柱键）。"""
    h = _real_handler(tmp_path)
    _create_person(tmp_path, "u1", "女")
    # 陈旧 bazi_info：男 + 四柱（P15 20:47 双写实测形态）
    h.dao.save_user_bazi("u1", {
        "year": 1999, "month": 2, "day": 26, "hour": 7, "minute": 0,
        "city": "北京", "gender": "男",
        "bazi": ["己卯", "丙寅", "己酉", "丁卯"],
    })
    saved = h._get_user_birth_profile("u1")
    assert saved is not None
    assert saved["gender"] == "女"          # 单一事实源 = persons
    assert saved["year"] == 1999
    # k8（2026-09-05 21:44 根因）：自愈改为「以 persons 全量重建」——旧行
    # bazi 四柱键一律丢弃（四柱只属于 chart_records），不再保留既有键
    bazi = h.dao.get_user_bazi("u1")
    assert bazi["gender"] == "女"
    assert "bazi" not in bazi, "自愈重建不得保留旧行 bazi 四柱键"


def test_g1_profile_persons_first_when_bazi_info_missing(tmp_path):
    """persons 有出生数据、bazi_info 完全缺失 → 自愈写入 bazi_info。"""
    h = _real_handler(tmp_path)
    _create_person(tmp_path, "u2", "女")
    saved = h._get_user_birth_profile("u2")
    assert saved is not None and saved["gender"] == "女"
    bazi = h.dao.get_user_bazi("u2")
    assert bazi is not None and bazi["gender"] == "女"
    assert bazi["year"] == 1999


def test_g1_profile_falls_back_to_bazi_info_when_persons_incomplete(tmp_path):
    """persons 无出生数据 → 退回 bazi_info（行为保持）。"""
    from src.storage.person_dao import PersonDAO
    h = _real_handler(tmp_path)
    pdao = _create_person(tmp_path, "u3", "女", birth_year=None)
    pdao.update_person("u3", pdao.list_persons("u3")[0]["id"],
                       birth={"gender": "女", "birth_year": None})
    h.dao.save_user_bazi("u3", {
        "year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
        "city": "北京", "gender": "男",
    })
    saved = h._get_user_birth_profile("u3")
    assert saved is not None
    assert saved["year"] == 1990 and saved["gender"] == "男"
    # persons 无数据 → 不触发自愈（bazi_info 不被覆盖为空）
    assert h.dao.get_user_bazi("u3")["year"] == 1990


def test_g1_profile_self_heal_only_once(tmp_path):
    """自愈收敛：二次读取不再重复回写（bazi_info 已一致 → 无额外写入）。"""
    from src.storage.dao import UserDAO
    h = _real_handler(tmp_path)
    _create_person(tmp_path, "u4", "女")
    h.dao.save_user_bazi("u4", {"year": 1999, "gender": "男"})
    h._get_user_birth_profile("u4")
    before = h.dao.get_user_bazi("u4")["gender"]
    assert before == "女"
    # 再次读取后值不变（一致 → 无自愈写）
    h._get_user_birth_profile("u4")
    after = h.dao.get_user_bazi("u4")
    assert after["gender"] == "女"
    assert after["year"] == 1999


# ================================================================
# P0-C：_handle_bazi 性别纠正自动重排分支
# ================================================================

def test_g1_correction_female_recharts_and_acks():
    """档案男 + 「我是女孩儿」→ 视为纠正：按女重排（_do_bazi_analysis
    收 gender=女）+ 回执「重新排盘」+ 不调 LLM 复用确认。"""
    h = make_handler()
    out = h._handle_bazi("我是女孩儿", "u1")
    h._do_bazi_analysis.assert_called_once()
    args = h._do_bazi_analysis.call_args[0]
    assert args[:7] == (1990, 5, 20, 15, 0, "北京", "女")  # 女命重排
    assert "重新排盘" in out
    assert "女" in out
    h._gen_reuse_acknowledgment.assert_not_called()  # 纠正走固定回执


def test_g1_correction_archives_double_written(tmp_path):
    """纠正排盘 → 档案双写新性别：persons + users.bazi_info 均为女
    （经真实 _save_bazi_records 落库路径）。"""
    h = _real_handler(tmp_path)
    from src.storage.person_dao import PersonDAO
    pdao = _create_person(tmp_path, "u5", "男", birth_year=1990)
    h.dao.save_user_bazi("u5", {
        "year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
        "city": "北京", "gender": "男", "bazi": ["庚午", "辛巳", "甲申", "壬申"]})

    class _R:
        bazi = ["庚午", "辛巳", "甲申", "壬申"]
        day_master = "甲"
        wuxing = {}
        shishen = []
        dayun = []
        liunian = {}
        liunian_full = []
        shensha = []
        geju = ""
        yongshen = ""
        nayin = []
        taiyuan = ""
        qiyun_detail = None

    h._save_bazi_records(_R(), {"year": 1990, "month": 5, "day": 20,
                                "hour": 15, "minute": 0, "city": "北京",
                                "gender": "女"},
                         "我是女孩儿", "u5")
    persons = pdao.list_persons("u5")
    assert persons[0]["gender"] == "女"          # persons 双写
    assert h.dao.get_user_bazi("u5")["gender"] == "女"  # bazi_info 双写
    # k8：_R 的四柱锚点（庚午辛巳甲申壬申）是 R2-4 修正默认开前的旧值时柱
    # （15:00 北京 → 引擎复算 癸未），与 birth 键矛盾 → dao 一致性守卫丢弃
    # bazi 键（四柱只属 chart_records；画像层不再承载四柱键）
    assert "bazi" not in h.dao.get_user_bazi("u5")


def test_g1_same_gender_no_correction():
    """档案女 + 「我是女孩儿」→ 一致：走原 merged 逻辑（不纠正、无重排回执）。"""
    h = make_handler()
    h._get_user_birth_profile = Mock(return_value={
        "year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
        "city": "北京", "gender": "女"})
    out = h._handle_bazi("我是女孩儿", "u1")
    args = h._do_bazi_analysis.call_args[0]
    assert args[6] == "女"
    assert "重新排盘" not in out
    h._gen_reuse_acknowledgment.assert_called_once()  # 原确认路径保留


def test_g1_saved_unknown_supplements_without_correction():
    """档案性别 unknown + 消息「我是女孩儿」→ 补充（merged 含女），无纠正回执。"""
    h = make_handler()
    h._get_user_birth_profile = Mock(return_value={
        "year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
        "city": "北京", "gender": "unknown"})
    out = h._handle_bazi("我是女孩儿", "u1")
    args = h._do_bazi_analysis.call_args[0]
    assert args[6] == "女"
    assert "重新排盘" not in out


def test_g1_year_conflict_priority_over_gender_correction():
    """年份冲突（规则3）优先于性别纠正：绝不静默重排。"""
    h = make_handler()
    h._gen_birth_conflict_ask = Mock(return_value="冲突确认")
    out = h._handle_bazi("我是1976年生的女孩儿", "u1")
    assert out == "冲突确认"
    h._do_bazi_analysis.assert_not_called()


# ================================================================
# C2：纠正路径 → 记忆画像层性别强制覆写（画像层不再永久滞后）
# ================================================================

def test_g1_correction_passes_force_gender_flag():
    """纠正分支 → _do_bazi_analysis 带 force_gender=True（画像层覆写依据）。"""
    h = make_handler()
    h._handle_bazi("我是女孩儿", "u1")
    assert h._do_bazi_analysis.call_args.kwargs.get("force_gender") is True


def test_g1_non_correction_no_force_gender_flag():
    """非纠正路径（性别一致 / 档案 unknown 补充）→ 不带 force_gender
    （静默冲突拒绝覆写语义不变，不得扩大强制覆写面）。"""
    h = make_handler()
    h._get_user_birth_profile = Mock(return_value={
        "year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
        "city": "北京", "gender": "女"})
    h._handle_bazi("我是女孩儿", "u1")
    assert h._do_bazi_analysis.call_args.kwargs.get("force_gender") is not True


def test_g1_correction_memory_profile_gender_updated(tmp_path):
    """C2：纠正重排落库 → 记忆画像层 gender 由男强制覆写为女
    （真实 UserMemory + 真实 DAO 落库链；纠正前画像层男与权威档案男，
    纠正后三者全部同步为女）。"""
    h = _real_handler(tmp_path)
    from src.memory.user_memory import UserMemory
    h.memory_system = UserMemory(base_dir=str(tmp_path / "mem"))
    h.memory_system.save_bazi_info("u7", {
        "year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
        "city": "北京", "gender": "男"})
    h.dao.save_user_bazi("u7", {
        "year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
        "city": "北京", "gender": "男", "bazi": ["庚午", "辛巳", "甲申", "壬申"]})
    assert h.memory_system._load("u7")["bazi_info"]["gender"] == "男"  # 前置

    class _R:
        bazi = ["庚午", "辛巳", "甲申", "壬申"]
        day_master = "甲"
        wuxing = {}
        shishen = []
        dayun = []
        liunian = {}
        liunian_full = []
        shensha = []
        geju = ""
        yongshen = ""
        nayin = []
        taiyuan = ""
        qiyun_detail = None

    h._save_bazi_records(_R(), {"year": 1990, "month": 5, "day": 20,
                                "hour": 15, "minute": 0, "city": "北京",
                                "gender": "女"},
                         "我是女孩儿", "u7", force_gender=True)
    assert h.memory_system._load("u7")["bazi_info"]["gender"] == "女"  # 画像层已更新
    assert h.dao.get_user_bazi("u7")["gender"] == "女"                 # 权威档案同步


def test_g1_correction_memory_profile_unchanged_without_force(tmp_path):
    """C2 红线：同一纠正场景若不传 force_gender（如静默数据冲突路径）→
    画像层拒绝覆写（旧值保留）——强制覆写面只属于用户明示纠正路径。"""
    h = _real_handler(tmp_path)
    from src.memory.user_memory import UserMemory
    h.memory_system = UserMemory(base_dir=str(tmp_path / "mem2"))
    h.memory_system.save_bazi_info("u8", {
        "year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
        "city": "北京", "gender": "男"})

    class _R:
        bazi = ["庚午", "辛巳", "甲申", "壬申"]
        day_master = "甲"
        wuxing = {}
        shishen = []
        dayun = []
        liunian = {}
        liunian_full = []
        shensha = []
        geju = ""
        yongshen = ""
        nayin = []
        taiyuan = ""
        qiyun_detail = None

    h._save_bazi_records(_R(), {"year": 1990, "month": 5, "day": 20,
                                "hour": 15, "minute": 0, "city": "北京",
                                "gender": "女"},
                         "排盘", "u8")
    assert h.memory_system._load("u8")["bazi_info"]["gender"] == "男"  # 拒绝覆写


# ================================================================
# C2b：parsed 直排路径（完整生辰+性别同一句）同款纠正判定
# ================================================================
# C2 修复（5d63fa6）只覆盖 partial 路径（纠正分支 ~4216 传 force_gender）；
# parsed 直排路径（handler.py 完整生辰分支，_extract_bazi_info 直排）此前
# 无纠正判定、未传 force_gender → DAO 无条件跟随新性别落库、画像层拒绝
# 覆写 → C2 症状（纠正后 LLM 画像仍持旧性别）在该路径复现。本批修复：
# parsed 路径同款 _is_gender_correction 判定 + force_gender 穿透 + 固定回执。

_PARSED_FEMALE = (1990, 5, 20, 7, 0, "北京", "女")


def test_g1_c2b_parsed_path_correction_forces_gender():
    """C2b：完整生辰+性别纠正消息（档案男 + 「我是1990年5月20日7点北京生的
    女孩儿」）→ 视为纠正：_do_bazi_analysis 收 gender=女 + force_gender=True
    （画像层强制覆写依据）+ 固定回执（重排确认）。"""
    h = make_handler()
    h._extract_bazi_info = Mock(return_value=_PARSED_FEMALE)
    out = h._handle_bazi("我是1990年5月20日7点北京生的女孩儿", "u1")
    args = h._do_bazi_analysis.call_args[0]
    assert args[:7] == (1990, 5, 20, 7, 0, "北京", "女")  # 女命重排
    assert h._do_bazi_analysis.call_args.kwargs.get("force_gender") is True
    assert "重新排盘" in out
    assert "女" in out
    h._gen_reuse_acknowledgment.assert_not_called()  # 纠正走固定回执


def test_g1_c2b_parsed_path_same_gender_no_force():
    """C2b：parsed 路径 + 同性别（档案女 + 女孩儿）→ 行为保持现状：
    force_gender 非 True、无纠正回执。"""
    h = make_handler()
    h._extract_bazi_info = Mock(return_value=_PARSED_FEMALE)
    h._get_user_birth_profile = Mock(return_value={
        "year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
        "city": "北京", "gender": "女"})
    out = h._handle_bazi("我是1990年5月20日7点北京生的女孩儿", "u1")
    assert h._do_bazi_analysis.call_args.kwargs.get("force_gender") is not True
    assert "重新排盘" not in out
    assert h._do_bazi_analysis.call_args[0][6] == "女"


def test_g1_c2b_parsed_path_archive_unknown_no_force():
    """C2b：parsed 路径 + 档案性别 unknown → 补充而非纠正（不强制、无回执）。"""
    h = make_handler()
    h._extract_bazi_info = Mock(return_value=_PARSED_FEMALE)
    h._get_user_birth_profile = Mock(return_value={
        "year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
        "city": "北京", "gender": "unknown"})
    out = h._handle_bazi("我是1990年5月20日7点北京生的女孩儿", "u1")
    assert h._do_bazi_analysis.call_args.kwargs.get("force_gender") is not True
    assert "重新排盘" not in out
    assert h._do_bazi_analysis.call_args[0][6] == "女"


def test_g1_c2b_parsed_path_third_party_no_force():
    """C2b 红线：parsed 路径 + 第三方指代（规则2，为他人排盘）→ 不判定
    纠正、不传 force（新性别属于第三方，绝不强制覆写本人画像）。"""
    h = make_handler()
    h._extract_bazi_info = Mock(return_value=_PARSED_FEMALE)
    out = h._handle_bazi("帮我女儿排个盘，她是1990年5月20日7点北京生的女孩儿",
                         "u1")
    assert h._do_bazi_analysis.call_args.kwargs.get("force_gender") is not True
    assert "重新排盘" not in out
    assert h._do_bazi_analysis.call_args[0][6] == "女"


def test_g1_c2b_parsed_path_year_conflict_priority():
    """C2b：parsed 路径年份冲突（规则3）优先于性别纠正：绝不静默重排。"""
    h = make_handler()
    h._extract_bazi_info = Mock(return_value=(1976, 5, 20, 7, 0, "北京", "女"))
    h._gen_birth_conflict_ask = Mock(return_value="冲突确认")
    out = h._handle_bazi("我是1976年5月20日7点北京生的女孩儿", "u1")
    assert out == "冲突确认"
    h._do_bazi_analysis.assert_not_called()


class _ResultStub:
    """排盘结果桩（与上文 _R 同构，C2b 全链路测试共用）。"""
    bazi = ["庚午", "辛巳", "甲申", "壬申"]
    day_master = "甲"
    wuxing = {}
    shishen = []
    dayun = []
    liunian = {}
    liunian_full = []
    shensha = []
    geju = ""
    yongshen = ""
    nayin = []
    taiyuan = ""
    qiyun_detail = None


def test_g1_c2b_parsed_path_full_chain(tmp_path):
    """C2b 全链路：真实 _handle_bazi（parsed 直排）+ 真实落库——档案男 +
    完整生辰性别消息 → 纠正判定 → force_gender=True 穿透：画像层 gender
    男→女 强制覆写 + persons/users.bazi_info 双写女 + 回执确认重排。"""
    from src.memory.user_memory import UserMemory
    h = _real_handler(tmp_path)
    h._downgraded = {}
    h.memory_system = UserMemory(base_dir=str(tmp_path / "mem"))
    h.memory_system.save_bazi_info("u9", {
        "year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
        "city": "北京", "gender": "男"})
    pdao = _create_person(tmp_path, "u9", "男", birth_year=1990)
    h.dao.save_user_bazi("u9", {
        "year": 1990, "month": 5, "day": 20, "hour": 15, "minute": 0,
        "city": "北京", "gender": "男", "bazi": ["庚午", "辛巳", "甲申", "壬申"]})
    # 重看盘直读兜底与本修复正交（有存量即短路，test_chart_reuse 已覆盖）→ 关掉
    h._try_reuse_chart = Mock(return_value="")

    _calls = {}

    def _fake_analysis(year, month, day, hour, minute, city, gender,
                       question, user_id, stream_cb=None, force_gender=False,
                       solar_time=True):  # k11c：契约含档案真太阳时开关
        _calls.update(gender=gender, force_gender=force_gender,
                      solar_time=solar_time)
        h._save_bazi_records(_ResultStub(), {
            "year": year, "month": month, "day": day,
            "hour": hour, "minute": minute,
            "city": city, "gender": gender,
        }, question, user_id, force_gender=force_gender)
        return "分析结果"
    h._do_bazi_analysis = _fake_analysis

    out = h._handle_bazi("我是1990年5月20日7点北京生的女孩儿", "u9")

    assert _calls["gender"] == "女"                      # 女命重排
    assert _calls["force_gender"] is True                # 纠正穿透
    # 画像层强制覆写：男 → 女（C2b 核心，此前 parsed 路径画像层永久滞后）
    assert h.memory_system._load("u9")["bazi_info"]["gender"] == "女"
    # 权威档案双写女
    assert h.dao.get_user_bazi("u9")["gender"] == "女"
    assert pdao.list_persons("u9")[0]["gender"] == "女"
    assert "重新排盘" in out                              # 固定回执
    assert "「女」" in out
