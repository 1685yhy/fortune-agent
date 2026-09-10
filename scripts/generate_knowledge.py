#!/usr/bin/env python3
"""AI辅助知识生成 — 从古籍+竞品数据生成高质量结构化知识条目。

策略:
  1. 竞品QA转换: 53,719 条竞品问答 → RAG知识条目
  2. 古籍拆解: 64卦/奇门/手相古籍 → 分条解读
  3. LLM扩写: 短板分类定向生成

使用: python scripts/generate_knowledge.py [--category qimen] [--count 1000] [--dry-run]
"""

import json, re, sys, time, logging, os
from pathlib import Path
from datetime import datetime
from typing import List, Dict

import httpx

PROJ = Path(__file__).parent.parent
STAGING = Path("/mnt/d/fortune-data/books/zonghe/staging")
COMPETITOR_DIR = PROJ / "data" / "competitor_data"
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(message)s]')
log = logging.getLogger()

MIN_LEN = 80
MAX_LEN = 2000

# ── 古籍素材 ────────────────────────────────────────────────────

QI_MEN_BASICS = [
    "奇门遁甲以洛书九宫为框架，配以八卦、八门、九星、八神、天干地支，构成多维时空预测模型。",
    "八门为：休门(吉·休养)、生门(吉·生机)、伤门(凶·伤害)、杜门(平·隐匿)、景门(平·文书)、死门(凶·终结)、惊门(凶·惊恐)、开门(吉·开创)。",
    "九星为：天蓬(水·贪狼)、天芮(土·巨门)、天冲(木·禄存)、天辅(木·文曲)、天禽(土·廉贞)、天心(金·武曲)、天柱(金·破军)、天任(土·左辅)、天英(火·右弼)。",
    "八神为：值符(领袖)、螣蛇(虚诈)、太阴(荫庇)、六合(和合)、白虎(凶险)、玄武(盗贼)、九地(稳固)、九天(高远)。",
    "三奇六仪：乙(日奇)、丙(月奇)、丁(星奇)为三奇；戊己庚辛壬癸为六仪。",
    "奇门遁甲排盘以时柱定局数，冬至后用阳遁(顺布六仪)，夏至后用阴遁(逆布六仪)。",
    "值符星随值符使(时柱旬首)落宫而定，各星按顺序飞布九宫。",
    "奇门四害：门迫(门克宫)、击刑(干犯刑冲)、入墓(干落墓库)、空亡(宫空无气)。",
    "奇门占断看三要素：天时(九星)、地利(九宫八卦)、人和(八门)。",
    "古籍《烟波钓叟赋》云：'阴阳顺逆妙难穷，二至还乡一九宫。若能了达阴阳理，天地都来一掌中。'",
    "《奇门遁甲统宗》记载：'奇门者，帝王之术也。上可安邦定国，下可趋吉避凶。'",
    "奇门布局之理：'先观二至以分顺逆，次看节气以定三元，然后依符头而起局。'",
    "八门克应：开门属金，宜出行征讨；生门属土，宜营造求财；休门属水，宜集会修造。",
    "九星吉凶论：天辅星(文曲)利考学文书，天心星(武曲)利医药求医，天禽星(廉贞)利安葬祭祀。",
    "奇门择时：'急则从神缓从门'—紧急事看值符所在之宫，从容事择吉门而用。",
]

PALM_READING_BASICS = [
    "手相学以掌纹、掌型、掌色、指型为四大观察要素，综合判断一个人的性格、健康和运势。",
    "三大主线为：生命线(起于虎口、绕大鱼际)、智慧线(起于生命线旁、横穿掌心)、感情线(起于小指下方、向食指延伸)。",
    "生命线主健康与生命力：线条清晰深长者为长寿之相，岛纹或断裂处对应特定年龄段的健康隐患。",
    "智慧线主思维与学业：线条长直清晰者逻辑思维强，下垂者想象力丰富，断续者注意力难集中。",
    "感情线主情感与婚姻：线条清晰修长至食指下方者感情专注，短而弯者热情但多变。",
    "手掌分为六丘：金星丘(拇指根部·爱情)、木星丘(食指根部·权力)、土星丘(中指根部·责任)、太阳丘(无名指根部·艺术)、水星丘(小指根部·智慧)、太阴丘(掌缘·直觉)。",
    "手型分五类：金型(方掌短指·务实)、木型(长掌长指·理想)、水型(圆掌柔指·感性)、火型(尖掌细指·热情)、土型(厚掌短指·稳重)。",
    "传统相书《神相全编》云：'手者，其用执持，其情取舍。故纤长者性慈，厚短者性鄙。'",
    "《麻衣神相》论手：'十指润泽者多才艺，十指干枯者劳碌命。指节漏缝者漏财，指并无缝者善守。'",
    "掌色判断：红润为血气旺、运势佳；苍白为气血虚、运势平；发黄为肝胆弱、防疾病。",
    "手相流年：男左女右为主手(先天)，另一手为辅(后天)。生命线上的年龄刻度大致按三分法划分。",
    "特殊掌纹：川字纹(无连线·独立自主)、M字纹(掌中有M·富贵之相)、断掌(横贯一线·个性强烈)。",
    "指甲相法：指甲红润光泽者血气旺，有月白者精力旺，竖纹多者操劳，横纹者近期压力大。",
    "《玉管照神局》云：'手心纹如乱丝者，多思虑而少决断。手心明净无纹者，心无机巧而福厚。'",
    "婚姻线(小指根部侧纹)：清晰一条为婚姻平稳，多条为感情丰富，交叉者婚姻多波折。",
]

MIAN_XIANG_BASICS = [
    "面相学以三停、五官、十二宫为核心分析框架，结合气色、骨相、肉相进行综合判断。",
    "上停(额部)管15-30岁早运，中停(眉眼鼻颧)管31-50岁中运，下停(口颏)管51岁后晚运。",
    "五官对应：眉为保寿官(兄弟缘)、眼为监察官(智慧)、鼻为审辨官(财帛)、口为出纳官(食禄)、耳为采听官(寿元)。",
    "额相论：天庭饱满为聪慧之相，额角峥嵘主早年得志，额有乱纹主早年挫折。",
    "眉相论：《神相全编》云'眉为两目之华盖，一面之仪表'。眉清秀者聪明，眉粗乱者性刚。",
    "眼相论：眼为心之窗。黑白分明者心术正，眼有神采者运势佳，四白眼(三白眼)者性情偏激。",
    "鼻相论：鼻为财帛宫。鼻梁端正挺拔者财运稳，鼻头有肉(悬胆鼻)者聚财，山根低陷者中年波折。",
    "口相论：口为出纳官。唇红齿白者善言辞，口角上扬(笑口)者人缘好，口角下垂者性情忧。",
    "耳相论：耳为采听官。耳垂厚大者福寿长，耳高于眉者智识高，耳轮分明者意志坚。",
    "十二宫：命宫(印堂)、财帛(鼻准)、兄弟(眉)、夫妻(眼尾)、子女(眼下)、疾厄(山根)、迁移(额角)、交友(颧下)、官禄(额中)、田宅(眼上)、福德(眉上)、父母(额角上)。",
    "《麻衣神相》云：'天地相朝，五岳相拱，此乃富贵之基。'天指额，地指颏，五岳为额鼻颏双颧。",
    "气色论：面色红黄明润者吉运当道，青黑暗滞者运势低迷，白而无华者气血不足。",
    "面型分五行：木型(长脸·清秀)、火型(尖脸·热情)、土型(方脸·稳重)、金型(圆脸·刚毅)、水型(圆胖·灵活)。",
    "痣相论：吉痣宜隐(藏于发际、耳内)，面显大痣为破相。额头正中痣名'佛顶珠'，主智慧但晚婚。",
    "《柳庄相法》云：'未观其形，先观其气。气者，神之表也。气清则神旺，气浊则神衰。'",
]

# ── 批量生成 ────────────────────────────────────────────────────

def call_llm(prompt: str, max_tokens: int = 300) -> str:
    """调用 DeepSeek Flash 生成内容"""
    if not API_KEY:
        return ""
    try:
        resp = httpx.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-flash",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.7,
            },
            timeout=30.0,
        )
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.warning(f"LLM call failed: {e}")
        return ""


def generate_from_basics(basics: List[str], category: str, count: int, dry_run: bool = False) -> int:
    """从基础知识条目中，用LLM为每条生成一个结构化知识条目"""
    entries = []
    batch_size = min(count, len(basics))

    for i, basic in enumerate(basics[:batch_size]):
        prompt = f"""你是中国传统文化学者。请将以下命理知识扩展为一条结构化的学习条目。

        原始知识: {basic}

        格式要求:
        1. 标题: 10-20字的概括
        2. 正文: 80-200字的详细解读(包含古籍引用或实践应用)
        3. 语言: 现代中文，学术但不晦涩

        直接返回格式: 标题: <标题>\\n正文: <正文>"""

        text = call_llm(prompt, max_tokens=200)
        if text and len(text) >= MIN_LEN:
            title = basic[:50]
            content = text
            # 尝试解析 LLM 返回
            title_match = re.search(r'标题[:：]\s*(.+?)(?:\n|$)', text)
            content_match = re.search(r'正文[:：]\s*(.+)', text, re.DOTALL)
            if title_match:
                title = title_match.group(1).strip()
            if content_match:
                content = content_match.group(1).strip()
            else:
                content = text

            entries.append({
                "title": title,
                "content": content,
                "source": f"AI_generated_{category}",
                "category": category,
                "generated_at": datetime.now().isoformat(),
                "quality": "accepted",
            })

        if (i + 1) % 20 == 0:
            log.info(f"  [{category}] Generated {i + 1}/{batch_size}...")
            time.sleep(1)  # Rate limiting

    # 去重 + 验证 + 写入
    accepted = 0
    rejected = 0
    seen_titles = set()

    if not dry_run and entries:
        # 加载已有标题做去重
        existing = set()
        out_file = STAGING / f"{category}_accepted.jsonl"
        if out_file.exists():
            for line in open(out_file):
                try:
                    e = json.loads(line)
                    existing.add(e.get("title", "")[:50])
                except Exception:
                    pass

        with open(out_file, "a") as f:
            for entry in entries:
                title_key = entry["title"][:50]
                if title_key in seen_titles or title_key in existing:
                    rejected += 1
                    continue
                if len(entry["content"]) < MIN_LEN:
                    rejected += 1
                    continue
                seen_titles.add(title_key)
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                accepted += 1

    log.info(f"  [{category}] {accepted} accepted, {rejected} rejected (of {len(entries)} generated)")
    return accepted


def convert_competitor_qa(max_count: int = 5000, dry_run: bool = False) -> int:
    """将竞品 QA 对转换为 RAG 知识条目"""
    qa_file = COMPETITOR_DIR / "competitors_clean.jsonl"
    if not qa_file.exists():
        log.warning("Competitor QA file not found")
        return 0

    entries = []
    seen = set()
    count = 0

    for line in open(qa_file):
        if count >= max_count:
            break
        try:
            qa = json.loads(line)
            query = qa.get("query", "")
            response = qa.get("response", "")
            platform = qa.get("platform", "unknown")

            if not query or not response:
                continue

            # 生成高质量的知识条目
            prompt = f"""将以下算命问答转化为一条知识条目。

用户问题: {query}
回答: {response[:500]}

请提取其中的命理知识点，格式化为:
标题: <15字以内的知识点概括>
正文: <80-150字的结构化知识，包含理论依据和实践指导>"""

            text = call_llm(prompt, max_tokens=200)
            if text and len(text) >= MIN_LEN:
                title = query[:50]
                content = text

                title_match = re.search(r'标题[:：]\s*(.+?)(?:\n|$)', text)
                content_match = re.search(r'正文[:：]\s*(.+)', text, re.DOTALL)
                if title_match:
                    title = title_match.group(1).strip()
                if content_match:
                    content = content_match.group(1).strip()

                key = title[:50]
                if key not in seen:
                    seen.add(key)
                    entries.append({
                        "title": title,
                        "content": content,
                        "source": f"competitor_qa_{platform}",
                        "category": "bazi_cases",
                        "generated_at": datetime.now().isoformat(),
                        "quality": "accepted",
                    })
            count += 1
            if count % 100 == 0:
                log.info(f"  [qa_convert] Processed {count}/{max_count}...")
                time.sleep(1)

        except Exception:
            continue

    # 写入
    accepted = 0
    if not dry_run and entries:
        out_file = STAGING / "bazi_cases_accepted.jsonl"
        with open(out_file, "a") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                accepted += 1

    log.info(f"  [qa_convert] {accepted} accepted from {count} processed")
    return accepted


# ── 主流程 ──────────────────────────────────────────────────────

def main(category: str = None, count: int = 500, dry_run: bool = False):
    total = 0
    log.info(f"=== AI Knowledge Generation {'(DRY RUN)' if dry_run else ''} ===")

    if not API_KEY:
        log.error("ANTHROPIC_API_KEY not set! LLM generation requires API access.")
        sys.exit(1)

    categories = {
        "qimen": (QI_MEN_BASICS, 1845),
        "palm_reading": (PALM_READING_BASICS, 918),
        "mianxiang": (MIAN_XIANG_BASICS, 744),
    }

    if category:
        if category not in categories:
            log.error(f"Unknown category: {category}")
            sys.exit(1)
        basics, needed = categories[category]
        n = min(count, needed)
        total += generate_from_basics(basics, category, n, dry_run)
    else:
        # All categories
        for cat, (basics, needed) in categories.items():
            n = min(count, needed)
            total += generate_from_basics(basics, cat, n, dry_run)
            time.sleep(2)

    # Convert competitor QA
    total += convert_competitor_qa(max_count=3000, dry_run=dry_run)

    log.info(f"\n=== Total accepted: {total} ===")

    # Final category counts
    log.info("\n=== All category counts ===")
    grand_total = 0
    for f in sorted(STAGING.glob("*_accepted.jsonl")):
        try:
            n = sum(1 for _ in open(f))
            cat_name = f.stem.replace("_accepted", "")
            log.info(f"  {cat_name}: {n}")
            grand_total += n
        except Exception:
            pass
    log.info(f"  GRAND TOTAL: {grand_total}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", type=str, default=None,
                    help="Specific category to generate (qimen/palm_reading/mianxiang)")
    ap.add_argument("--count", type=int, default=500,
                    help="Number of entries to generate per category")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    main(category=args.category, count=args.count, dry_run=args.dry_run)
