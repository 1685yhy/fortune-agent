"""每日运势推送脚本.

查询所有保存了八字的用户，计算今日干支与用户八字的互动关系，
生成个性化运势推送消息并记录推送日志。
"""
import sys
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lunar_python import Solar
from src.config import load_settings
from src.storage.dao import UserDAO
from src.engines.bazi import TIANGAN, DIZHI, WUXING_TG, WUXING_DZ, SHENG_XU, SHENG_XU_MAP

logger = logging.getLogger(__name__)

# 农历月份别称
LUNAR_MONTH_NAMES = [
    "", "正月", "二月", "三月", "四月", "五月", "六月",
    "七月", "八月", "九月", "十月", "冬月", "腊月",
]

# 冲煞生肖映射（地支→生肖）
ZHI_SHENGXIAO = {
    "子": "鼠", "丑": "牛", "寅": "虎", "卯": "兔",
    "辰": "龙", "巳": "蛇", "午": "马", "未": "羊",
    "申": "猴", "酉": "鸡", "戌": "狗", "亥": "猪",
}

# 十二建除宜忌（简化版）
JIANCHU_YI = {
    "建": ["祭祀", "出行"], "除": ["除服", "沐浴"],
    "满": ["祭祀", "嫁娶"], "平": ["修墓", "平治"],
    "定": ["会友", "定盟"], "执": ["筑堤", "修屋"],
    "破": ["求医", "破屋"], "危": ["安床", "交易"],
    "成": ["开业", "嫁娶"], "收": ["纳财", "收藏"],
    "开": ["开市", "出行"], "闭": ["补墙", "安葬"],
}

JIANCHU_JI = {
    "建": ["动土", "开仓"], "除": ["嫁娶", "出行"],
    "满": ["动土", "掘井"], "平": ["开市", "祭祀"],
    "定": ["诉讼", "出行"], "执": ["开市", "嫁娶"],
    "破": ["嫁娶", "出行"], "危": ["开市", "入宅"],
    "成": ["诉讼", "安葬"], "收": ["动土", "出行"],
    "开": ["诸事不宜"], "闭": ["开市", "出行"],
}


def get_today_ganzhi() -> dict:
    """获取今日干支信息"""
    now = datetime.now()
    solar = Solar.fromYmdHms(now.year, now.month, now.day, 0, 0, 0)
    lunar = solar.getLunar()
    eight_char = lunar.getEightChar()

    day_ganzhi = eight_char.getDay()  # e.g. "庚午"

    # 计算今日干支索引
    day_gan = day_ganzhi[0]
    day_zhi = day_ganzhi[1]
    day_gan_idx = TIANGAN.index(day_gan)
    day_zhi_idx = DIZHI.index(day_zhi)

    # 冲：六冲关系（地支相冲：子午、丑未、寅申、卯酉、辰戌、巳亥）
    chong_map = {
        "子": "午", "丑": "未", "寅": "申", "卯": "酉",
        "辰": "戌", "巳": "亥", "午": "子", "未": "丑",
        "申": "寅", "酉": "卯", "戌": "辰", "亥": "巳",
    }
    chong_zhi = chong_map.get(day_zhi, "无")
    chong_shengxiao = ZHI_SHENGXIAO.get(chong_zhi, "无")

    # 五行
    day_wuxing = f"{WUXING_TG[day_gan]}{WUXING_DZ[day_zhi]}"

    # 十二建除
    lunar_day = lunar.getDay()
    jianchu_name = lunar.getDayByEightChar() if hasattr(lunar, "getDayByEightChar") else ""
    if not jianchu_name:
        jianchu_name = _calc_jianchu(lunar.getMonth(), lunar_day)

    yi = "、".join(JIANCHU_YI.get(jianchu_name, ["祭祀"]))
    ji = "、".join(JIANCHU_JI.get(jianchu_name, ["诸事不宜"]))

    # 农历日期
    lunar_month = lunar.getMonth()
    lunar_day_str = str(lunar.getDay())
    lunar_str = f"{LUNAR_MONTH_NAMES[lunar_month]}{lunar_day_str}"

    return {
        "date": now.strftime("%Y年%m月%d日"),
        "day_ganzhi": day_ganzhi,
        "day_gan": day_gan,
        "day_zhi": day_zhi,
        "day_wuxing": day_wuxing,
        "chong_zhi": chong_zhi,
        "chong_shengxiao": chong_shengxiao,
        "jianchu": jianchu_name,
        "yi": yi,
        "ji": ji,
        "lunar": lunar_str,
        "year_ganzhi": eight_char.getYear(),
        "month_ganzhi": eight_char.getMonth(),
    }


def _calc_jianchu(month: int, day: int) -> str:
    """简化计算建除十二神（月支起日辰法）"""
    month_zhi_idx = (month + 1) % 12  # 寅=1月, 卯=2月...
    day_idx = (month_zhi_idx + day - 1) % 12
    names = ["建", "除", "满", "平", "定", "执", "破", "危", "成", "收", "开", "闭"]
    return names[day_idx]


def compare_bazi_with_today(bazi_pillars: list, today: dict) -> dict:
    """比较用户八字与今日干支，生成运势判定"""
    # 日柱天干对比
    user_day_gan = bazi_pillars[2][0] if len(bazi_pillars) > 2 else "甲"
    today_gan = today["day_gan"]
    today_zhi = today["day_zhi"]

    user_gan_idx = TIANGAN.index(user_day_gan)
    today_gan_idx = TIANGAN.index(today_gan)

    # 天干关系判定
    if user_gan_idx == today_gan_idx:
        gan_relation = "same"  # 比肩/劫财
    elif (user_gan_idx - today_gan_idx) % 10 == 1 or (today_gan_idx - user_gan_idx) % 10 == 1:
        gan_relation = "ke_me"  # 相克（具体看生克）
    else:
        # 五行生克
        user_wx = WUXING_TG[user_day_gan]
        today_wx = WUXING_TG[today_gan]
        if user_wx == today_wx:
            gan_relation = "same_wuxing"
        elif _is_sheng(user_wx, today_wx):
            gan_relation = "sheng_me"  # 今日生日主
        elif _is_sheng(today_wx, user_wx):
            gan_relation = "me_sheng"  # 日主生今日
        elif _is_ke(user_wx, today_wx):
            gan_relation = "me_ke"  # 日主克今日
        elif _is_ke(today_wx, user_wx):
            gan_relation = "ke_me"  # 今日克日主
        else:
            gan_relation = "neutral"

    # 地支关系
    user_zhi = bazi_pillars[2][1] if len(bazi_pillars) > 2 else "子"
    zhi_relation = _calc_zhi_relation(user_zhi, today_zhi)

    # 冲判定
    user_zhi_idx = DIZHI.index(user_zhi)
    today_zhi_idx = DIZHI.index(today_zhi)
    is_chong = abs(user_zhi_idx - today_zhi_idx) == 6  # 六冲

    return {
        "gan_relation": gan_relation,
        "zhi_relation": zhi_relation,
        "is_chong": is_chong,
        "chong_zhi": today["chong_zhi"],
        "chong_shengxiao": today["chong_shengxiao"],
        "today_wuxing": today["day_wuxing"],
    }


def _is_sheng(a: str, b: str) -> bool:
    """五行相生：a生b?"""
    cycle = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
    return cycle.get(a) == b


def _is_ke(a: str, b: str) -> bool:
    """五行相克：a克b?"""
    cycle = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}
    return cycle.get(a) == b


def _calc_zhi_relation(user_zhi: str, today_zhi: str) -> str:
    """地支关系判定"""
    # 三合
    sanhe = {
        ("申", "子", "辰"): "水局", ("寅", "午", "戌"): "火局",
        ("巳", "酉", "丑"): "金局", ("亥", "卯", "未"): "木局",
    }
    for trio, name in sanhe.items():
        if user_zhi in trio and today_zhi in trio:
            return f"{name}三合"

    # 六合
    liuhe = {
        "子": "丑", "丑": "子", "寅": "亥", "亥": "寅",
        "卯": "戌", "戌": "卯", "辰": "酉", "酉": "辰",
        "巳": "申", "申": "巳", "午": "未", "未": "午",
    }
    if liuhe.get(user_zhi) == today_zhi:
        return "六合"

    # 六冲
    chong = {
        "子": "午", "午": "子", "丑": "未", "未": "丑",
        "寅": "申", "申": "寅", "卯": "酉", "酉": "卯",
        "辰": "戌", "戌": "辰", "巳": "亥", "亥": "巳",
    }
    if chong.get(user_zhi) == today_zhi:
        return "相冲"

    return "平"


GAN_RELATION_DESC = {
    "same": "今日天干与您日主相同，比肩旺，人缘佳，但竞争也较激烈。",
    "same_wuxing": "今日五行与您日主同类，增强自身能量，适合主动出击。",
    "sheng_me": "今日天干生助日主，得贵人运，做事顺遂。",
    "me_sheng": "今日日主生天干，付出较多，宜奉献、投资。",
    "me_ke": "今日日主克制天干，能控制局面，适合管理、规划。",
    "ke_me": "今日天干克制日主，压力较大，宜低调、收敛。",
    "neutral": "今日与日主五行关系平和，平稳过渡即可。",
}


def generate_push_message(user_id: str, bazi_info: dict, today: dict, comparison: dict) -> str:
    """生成用户运势推送消息"""
    bazi_pillars = bazi_info.get("bazi", [])
    bazi_str = " ".join(bazi_pillars) if bazi_pillars else "未知"
    gan_relation = comparison["gan_relation"]
    description = GAN_RELATION_DESC.get(gan_relation, "今日运势平稳。")

    # 冲煞信息
    chong_info = ""
    if comparison["is_chong"]:
        chong_info = f"\n注：今日地支与您日支{comparison['chong_zhi']}相冲，注意情绪管理。"
    elif comparison["chong_shengxiao"]:
        chong_info = f"\n今日冲{comparison['chong_shengxiao']}，属{comparison['chong_shengxiao']}的朋友需注意。"
    else:
        chong_info = ""

    message = f"""📅 今日运势 ({today['date']})
{today['year_ganzhi']}年 {today['month_ganzhi']}月 {today['day_ganzhi']}日
{today['lunar']} | {today['jianchu']}
冲{comparison['chong_shengxiao']} | 宜: {today['yi']} 忌: {today['ji']}

🔮 您的运势（八字：{bazi_str}）：
{description}{chong_info}

📌 今日建议：
{_generate_advice(gan_relation, comparison)}

💬 详细分析请回复「运势」
"""

    return message.strip()


def _generate_advice(gan_relation: str, comparison: dict) -> str:
    """根据干支关系生成建议"""
    advice_map = {
        "same": "适合团队合作、社交活动；注意控制冲动消费。",
        "same_wuxing": "能量充沛，适合推进重要事项；保持谦逊避免冲突。",
        "sheng_me": "顺应趋势，主动争取机会；适合拜见贵人、洽谈合作。",
        "me_sheng": "宜分享知识、帮助他人、投资未来；注意不要过度消耗。",
        "me_ke": "适合整理规划、财务管理、解决积压问题；把握节奏。",
        "ke_me": "以静制动，不宜冒进；适合学习思考、养精蓄锐。",
        "neutral": "平常心度过，可安排常规工作；适合整理和复盘。",
    }

    advice = advice_map.get(gan_relation, "保持平常心，顺其自然。")

    if comparison["is_chong"]:
        advice += "\n💥 日支相冲日，避免重要决策，行车出行多加小心。"

    if gan_relation in ("sheng_me", "same_wuxing"):
        advice += "\n🌟 今日整体运势向好，把握良机！"
    elif gan_relation in ("ke_me",):
        advice += "\n🌊 运势稍有阻力，放平心态，稳步前行。"

    return advice


def run_push_batch(dao: UserDAO, today: dict, dry_run: bool = False) -> dict:
    """执行每日运势推送"""

    logger.info(f"开始每日运势推送: {today['date']}")

    users = dao.get_all_users_with_bazi()
    logger.info(f"找到 {len(users)} 个有八字信息的用户")

    stats = {"total": len(users), "pushed": 0, "skipped": 0, "errors": 0, "details": []}

    for user in users:
        user_id = user["user_id"]
        bazi_info = user["bazi_info"]
        push_enabled = user["push_enabled"]

        if not push_enabled:
            logger.info(f"用户 {user_id} 已关闭推送，跳过")
            stats["skipped"] += 1
            continue

        if not bazi_info or "bazi" not in bazi_info:
            logger.info(f"用户 {user_id} 八字信息不完整，跳过")
            stats["skipped"] += 1
            continue

        try:
            bazi_pillars = bazi_info["bazi"]
            comparison = compare_bazi_with_today(bazi_pillars, today)
            message = generate_push_message(user_id, bazi_info, today, comparison)

            if dry_run:
                logger.info(f"[DRY RUN] 用户 {user_id}: 将推送消息 ({len(message)}字符)")
                logger.debug(f"消息内容:\n{message}")
                stats["pushed"] += 1
            else:
                dao.log_push(
                    user_id=user_id,
                    push_date=today["date"],
                    message=message,
                    success=True,
                )
                stats["pushed"] += 1
                logger.info(f"用户 {user_id} 推送成功")

            stats["details"].append({"user_id": user_id, "success": True})

        except Exception as e:
            logger.error(f"用户 {user_id} 推送失败: {e}")
            if not dry_run:
                dao.log_push(
                    user_id=user_id,
                    push_date=today["date"],
                    message="",
                    success=False,
                    error=str(e),
                )
            stats["errors"] += 1
            stats["details"].append({"user_id": user_id, "success": False, "error": str(e)})

    logger.info(f"推送完成: 总计{stats['total']}, 成功{stats['pushed']}, "
                f"跳过{stats['skipped']}, 失败{stats['errors']}")
    return stats


# ──────────────────────────────────────────
# Sprint 4: Weekly Summary Mode
# ──────────────────────────────────────────


def get_week_range() -> dict:
    """获取本周日期范围"""
    now = datetime.now()
    # Monday of this week
    monday = now - __import__("datetime").timedelta(days=now.weekday())
    sunday = monday + __import__("datetime").timedelta(days=6)
    return {
        "start": monday.strftime("%Y-%m-%d"),
        "end": sunday.strftime("%Y-%m-%d"),
        "start_cn": monday.strftime("%m月%d日"),
        "end_cn": sunday.strftime("%m月%d日"),
    }


def generate_weekly_summary(user_id: str, bazi_info: dict, today: dict, comparison: dict, dao: UserDAO) -> str:
    """生成周度运势总结（含准确率显示）"""
    week = get_week_range()
    bazi_pillars = bazi_info.get("bazi", [])
    bazi_str = " ".join(bazi_pillars) if bazi_pillars else "未知"
    gan_relation = comparison["gan_relation"]
    description = GAN_RELATION_DESC.get(gan_relation, "本周运势平稳。")

    # 获取上周运势回顾（基于用户反馈）
    week_preview = _generate_week_preview(user_id, dao)

    # 获取上周预测准确率
    accuracy_info = ""
    try:
        accuracy = dao.get_user_accuracy(user_id)
        if accuracy["accuracy_pct"] is not None:
            # 按运势类型分类：这里简单用天干关系映射
            accuracy_info = (
                f"\n📊 上个月你的运势预测准确率: {accuracy['accuracy_pct']}%"
                f"（共{accuracy['total_feedback']}次反馈，"
                f"{accuracy['positive']}次准确/{accuracy['negative']}次偏差）"
            )
    except Exception:
        pass

    # 个性化 - 基于用户历史
    personalization = _generate_personal_note(user_id, dao, bazi_pillars)

    message = f"""📅 本周运势概览 ({week['start_cn']}-{week['end_cn']})
{today['year_ganzhi']}年 {today['month_ganzhi']}月
冲{comparison['chong_shengxiao']}

🔮 您的命盘（{bazi_str}）：
{description}

{personalization}
{week_preview}{accuracy_info}

📌 本周建议：
{_generate_advice(gan_relation, comparison)}

💬 详细分析请回复「运势」
"""

    return message.strip()


def _generate_week_preview(user_id: str, dao: UserDAO) -> str:
    """生成上周回顾"""
    try:
        consultations = dao.get_user_consultations(user_id, limit=5)
        if not consultations:
            return "\n📋 上周暂无运势记录。本周开始关注你的运势变化！\n"

        preview = "\n📋 最近运势回顾：\n"
        for c in consultations[:3]:
            intent_label = {
                "bazi": "八字", "ziwei": "紫微", "liuyao": "六爻",
                "fengshui": "风水", "mianxiang": "面相", "zeri": "择日",
                "qimen": "奇门", "xingming": "姓名", "hehun": "合婚",
                "dream": "解梦",
            }.get(c.get("intent"), c.get("intent", ""))
            question = (c.get("question") or "")[:30]
            date_str = (c.get("created_at") or "")[:10]
            if question:
                preview += f"  · {date_str} {intent_label}: {question}\n"

        return preview
    except Exception:
        return ""


def _generate_personal_note(user_id: str, dao: UserDAO, bazi_pillars: list) -> str:
    """基于用户历史生成个性化备注"""
    try:
        consultations = dao.get_user_consultations(user_id, limit=3)
        intents = [c.get("intent") for c in consultations if c.get("intent")]
        if not intents:
            return ""

        # 统计最常用的功能
        from collections import Counter
        top_intent = Counter(intents).most_common(1)[0][0]
        intent_labels = {
            "bazi": "八字", "ziwei": "紫微斗数", "liuyao": "六爻占卜",
            "fengshui": "风水", "mianxiang": "面相", "zeri": "择日",
            "dream": "解梦",
        }
        label = intent_labels.get(top_intent, top_intent)
        return f"💡 根据你的使用习惯，你似乎对{label}比较感兴趣，本周多关注{label}相关的运势变化。\n"
    except Exception:
        return ""


def get_weekly_forecast(today: dict) -> str:
    """生成下周运势概览"""
    # 基于当前日期的干支推算下周趋势
    day_gan = today["day_gan"]
    day_gan_idx = TIANGAN.index(day_gan) if day_gan in TIANGAN else 0
    next_gan = TIANGAN[(day_gan_idx + 1) % 10]  # 下一天干

    return (
        f"🔮 下周展望：\n"
        f"下周天干趋势偏向{next_gan}，整体能量转向"
        f"{'积极活跃' if day_gan_idx % 2 == 0 else '沉稳内敛'}的方向。\n"
        f"建议关注人际关系和财务方面的变化，提前做好准备。"
    )


def run_weekly_push_batch(dao: UserDAO, today: dict, dry_run: bool = False) -> dict:
    """执行周度运势总结推送"""
    logger.info(f"开始周度运势推送: {today['date']}")
    week = get_week_range()

    users = dao.get_all_users_with_bazi()
    logger.info(f"找到 {len(users)} 个有八字信息的用户")

    stats = {"total": len(users), "pushed": 0, "skipped": 0, "errors": 0, "details": []}

    for user in users:
        user_id = user["user_id"]
        bazi_info = user["bazi_info"]
        push_enabled = user["push_enabled"]

        if not push_enabled:
            logger.info(f"用户 {user_id} 已关闭推送，跳过")
            stats["skipped"] += 1
            continue

        if not bazi_info or "bazi" not in bazi_info:
            logger.info(f"用户 {user_id} 八字信息不完整，跳过")
            stats["skipped"] += 1
            continue

        try:
            bazi_pillars = bazi_info["bazi"]
            comparison = compare_bazi_with_today(bazi_pillars, today)
            message = generate_weekly_summary(user_id, bazi_info, today, comparison, dao)

            if dry_run:
                logger.info(f"[DRY RUN] 用户 {user_id}: 将推送周报 ({len(message)}字符)")
                logger.debug(f"消息内容:\n{message}")
                stats["pushed"] += 1
            else:
                dao.log_push(
                    user_id=user_id,
                    push_date=week["start"],
                    message=message,
                    success=True,
                )
                stats["pushed"] += 1
                logger.info(f"用户 {user_id} 周报推送成功")

            stats["details"].append({"user_id": user_id, "success": True})

        except Exception as e:
            logger.error(f"用户 {user_id} 周报推送失败: {e}")
            if not dry_run:
                dao.log_push(
                    user_id=user_id,
                    push_date=week["start"],
                    message="",
                    success=False,
                    error=str(e),
                )
            stats["errors"] += 1
            stats["details"].append({"user_id": user_id, "success": False, "error": str(e)})

    logger.info(f"周报推送完成: 总计{stats['total']}, 成功{stats['pushed']}, "
                f"跳过{stats['skipped']}, 失败{stats['errors']}")
    return stats


def main():
    """主入口"""
    import argparse

    parser = argparse.ArgumentParser(description="每日/周运势推送")
    parser.add_argument("--dry-run", action="store_true", help="仅测试，不记录推送日志")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志输出")
    parser.add_argument("--weekly", action="store_true", help="周度运势总结模式")
    args = parser.parse_args()

    # Ensure logs directory exists before configuring file handler
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    log_file = "weekly_push.log" if args.weekly else "daily_push.log"
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                str(log_dir / log_file),
                encoding="utf-8",
            ),
        ],
    )

    settings = load_settings()
    dao = UserDAO(str(settings.db_path))
    today = get_today_ganzhi()

    if args.weekly:
        logger.info(f"=== 每周运势总结推送 ===")
        logger.info(f"本周: {get_week_range()['start']} ~ {get_week_range()['end']}")
        stats = run_weekly_push_batch(dao, today, dry_run=args.dry_run)
    else:
        logger.info(f"=== 每日运势推送 ===")
        logger.info(f"日期: {today['date']}")
        logger.info(f"干支: {today['day_ganzhi']}")
        logger.info(f"农历: {today['lunar']}")
        stats = run_push_batch(dao, today, dry_run=args.dry_run)

    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
