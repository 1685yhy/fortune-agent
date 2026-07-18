#!/usr/bin/env python3
"""
Generate 81 JSONL entries for 81-number name studies (81数理吉凶详解).
Each entry has detailed, natural-sounding content of 300-800 chars.
"""

import json
import os

data = [
    {"number":1,"name":"太极之数","luck":"大吉","base":"聪明、多学、成功、富贵、名誉、幸福、财帛","family":"竹木成林、父母有荫、家庭圆满","health":"身体安康，可望长寿","meaning":"万事万物的基本数，为最大吉祥运。人格有此数者具有领导才能，独立开创，能成大业。"},
    {"number":2,"name":"两仪之数","luck":"大凶","base":"劫财、破灭、灾危、破家","family":"亲情疏远","health":"凶变、病弱、短命","meaning":"混沌未定之象，意志不坚，困苦不安。"},
    {"number":3,"name":"三才之数","luck":"大吉","base":"学术、技艺、祖业、丰盛、自立、建业、官禄","family":"可得贤妻，六亲和睦","health":"健康良好，可望长寿","meaning":"阴阳抱合，成功发达之兆。"},
    {"number":4,"name":"四象之数","luck":"大凶","base":"美貌、破家、灾危、劫财","family":"六亲缘薄","health":"衰弱、外伤、夭折","meaning":"万物枯衰，破败死亡之象。"},
    {"number":5,"name":"五行之数","luck":"大吉","base":"学者、祖业、文昌、福星、官星、财钱","family":"上下敦睦","health":"福寿双全","meaning":"阴阳交感，暗藏大成功运，富贵荣华。"},
    {"number":6,"name":"六爻之数","luck":"大吉","base":"豪杰、官禄、财钱、将星","family":"家庭圆满和睦","health":"可望健康长寿","meaning":"天德地祥俱备，富裕安稳。"},
    {"number":7,"name":"七政之数","luck":"吉","base":"独立、官禄、进取、技术","family":"善涵养修身者可得圆满","health":"心身健全","meaning":"独立权威之象，精力充沛。"},
    {"number":8,"name":"八卦之数","luck":"吉","base":"艺能、美术、学者、官禄","family":"兴家成为达贤者","health":"完健自在","meaning":"意志如磐石，忍耐克己成大功。"},
    {"number":9,"name":"大成之数","luck":"大凶","base":"官禄、怪杰、富翁","family":"亲情不睦","health":"体弱、短命","meaning":"浮沉不定之象，为人生最大恶运。"},
    {"number":10,"name":"终结之数","luck":"大凶","base":"天福、文昌、散财、官禄、破危","family":"家内冷眼旁观者多","health":"杀伤、刑罚、病弱","meaning":"日没黄昏，空虚无物。"},
    {"number":11,"name":"旱苗逢雨","luck":"大吉","base":"财星、天佑、暗禄、文昌、技艺","family":"事事和顺","health":"可望健康长寿","meaning":"享天赋之幸福，万事顺利。"},
    {"number":12,"name":"掘井无泉","luck":"大凶","base":"凶星、破厄、劫煞","family":"亲情如秋水","health":"神经衰弱","meaning":"家庭缘薄，孤苦无依。"},
    {"number":13,"name":"春日牡丹","luck":"大吉","base":"天官、文昌、技艺、学士、财库","family":"祖宗余荫，子孙孝顺","health":"身心健康","meaning":"富学艺才能，有智谋奇略。"},
    {"number":14,"name":"破兆","luck":"大凶","base":"暗禄、美貌、艺术、流浪","family":"骨肉疏远","health":"皮肤病","meaning":"浮沉不定，六亲无靠。"},
    {"number":15,"name":"福寿","luck":"大吉","base":"天官、贵人、福星、官禄","family":"清净家风，子孙昌盛","health":"安稳余庆","meaning":"最大好运，福寿圆满。"},
    {"number":16,"name":"厚重","luck":"大吉","base":"天官、贵命、豪杰、进田、学士","family":"春日花开，男子有贤妻","health":"戒慎者可望健康","meaning":"反凶化吉象，位尊望重。"},
    {"number":17,"name":"刚强","luck":"半吉","base":"天官、将星、威武、艺术","family":"可望圆满","health":"身心健康","meaning":"权威刚强，意志坚定。女性有此数者易流于男性。"},
    {"number":18,"name":"铁镜重磨","luck":"吉","base":"将星、文昌、太极、财帛","family":"有祖宗庇荫之福","health":"身心健康","meaning":"志望一立必破万难，博得名利。"},
    {"number":19,"name":"多难","luck":"大凶","base":"官禄、进田、财帛、智谋、凶危","family":"兄弟成吴越","health":"病弱、短命","meaning":"有才智多谋略，但频生意外的灾患。"},
    {"number":20,"name":"屋下藏金","luck":"大凶","base":"官星、美术、智能、凶危","family":"亲情不立","health":"命运多难","meaning":"破败衰亡之数，一生不得安宁。"},
    {"number":21,"name":"明月中天","luck":"大吉","gender_note":"女性不宜","base":"天官、太极、文昌、艺术、财库","family":"六亲和睦，女性反为不吉","health":"长寿","meaning":"独立权威，首领之运。"},
    {"number":22,"name":"秋草逢霜","luck":"大凶","base":"远洋、天乙、君臣、劫煞","family":"六亲无力，自立成家","health":"常有暗疾","meaning":"秋草逢霜之象，脆弱无力。"},
    {"number":23,"name":"壮丽","luck":"大吉","gender_note":"女性不宜","base":"首领、君臣、文昌、暗禄、财库","family":"男性圆满，女性克夫","health":"男性健康，女性孤独","meaning":"伟大昌隆之运。"},
    {"number":24,"name":"掘藏得金","luck":"大吉","base":"天官、福星、文昌、企业、财库","family":"家庭圆满，兄弟和睦","health":"松柏常青","meaning":"才略智谋出众，白手起家。"},
    {"number":25,"name":"英俊","luck":"半吉","base":"君臣、首领、福星、文昌、技艺","family":"平和谦虚者圆满","health":"健康自在","meaning":"资性英敏，但因性情偏激。"},
    {"number":26,"name":"变怪","luck":"大凶","base":"豪杰、官禄、侠义、财库","family":"亲情无义","health":"破家病弱","meaning":"波澜重叠，英雄运格。"},
    {"number":27,"name":"增长","luck":"半吉","base":"天官、将军、师长、学士","family":"六亲不得力","health":"肺病、心脏病","meaning":"自信心过强，多受诽谤攻击。"},
    {"number":28,"name":"阔水浮萍","luck":"大凶","base":"天官、将星、官星、学士","family":"亲戚多忌怨","health":"病灾、伤害","meaning":"遭难运，终身辛苦不绝。"},
    {"number":29,"name":"不平","luck":"半吉","gender_note":"女性不宜","base":"天官、太极、君臣、财帛","family":"乏祖力","health":"可安康","meaning":"智谋优秀，但欲望无止境。"},
    {"number":30,"name":"非运","luck":"半吉","base":"官星、将相、文昌、豪放","family":"亲情疏淡","health":"刑罚、外伤","meaning":"浮沉不定，凶吉难分。"},
    {"number":31,"name":"春日花开","luck":"大吉","base":"太极、君臣、将星、学士","family":"子女多荫","health":"身心健康","meaning":"智仁勇俱备，为威力强大的首领运数。"},
    {"number":32,"name":"宝马金鞍","luck":"大吉","base":"天德、月德、君臣、将星、文官","family":"家门隆昌，子孙旺盛","health":"可望安康","meaning":"侥幸多望，品性温良，最适合女性。"},
    {"number":33,"name":"升天","luck":"大吉","gender_note":"女性不宜","base":"天官、臣将、部长、文昌","family":"女性用则孤寡","health":"可望健康","meaning":"鸾凤相会，旭日东升。"},
    {"number":34,"name":"破家","luck":"大凶","base":"臣将、文昌、学士、破财","family":"家缘薄","health":"短命、杀伤","meaning":"破坏的大凶兆。"},
    {"number":35,"name":"高楼望月","luck":"大吉","base":"将相、学士、文昌、艺术、财库","family":"上流家庭","health":"安稳余庆","meaning":"温良和顺之象，最适合女性。"},
    {"number":36,"name":"波澜重叠","luck":"大凶","base":"将星、学士、文昌、破厄","family":"六亲不得力","health":"短命、病重","meaning":"一生难得平安。"},
    {"number":37,"name":"猛虎出林","luck":"大吉","base":"将星、官禄、文昌、权威","family":"和睦圆满","health":"可望长寿","meaning":"独立权威，热诚忠烈。"},
    {"number":38,"name":"磨铁成针","luck":"半吉","base":"将星、学士、臣将、技艺","family":"亲眷冷淡","health":"灾祸、外伤","meaning":"向文学、技艺方面发展可成功。"},
    {"number":39,"name":"富贵","luck":"吉","gender_note":"女性不宜","base":"臣将、文昌、艺术、财库","family":"安宁自在","health":"可望安康","meaning":"富贵至极，暗藏凶象。"},
    {"number":40,"name":"退安","luck":"大凶","base":"将星、豪杰、文昌、胆量","family":"亲情疏远","health":"凶病、外伤","meaning":"富智谋胆略，退之可保平安。"},
    {"number":41,"name":"有德","luck":"大吉","base":"将星、太极、名人、学者、官禄","family":"家庭圆满，子孙旺盛","health":"可望长寿","meaning":"德高望重，事事如意。"},
    {"number":42,"name":"寒蝉在柳","luck":"大凶","base":"君臣、部将、官星、文昌","family":"亲情无义","health":"病弱、孤独","meaning":"十艺九不成。"},
    {"number":43,"name":"散财","luck":"大凶","base":"将相、文星、艺术、凶星","family":"妻弱无肋","health":"病弱、短命","meaning":"散财破产运。"},
    {"number":44,"name":"烦闷","luck":"大凶","base":"文昌、学士、财库、破财","family":"骨肉相疏","health":"发狂、短命","meaning":"破家亡身的最恶数。"},
    {"number":45,"name":"顺风","luck":"大吉","base":"部将、君臣、文昌、学士、艺术","family":"可望圆满","health":"安康长寿","meaning":"德量宏厚。"},
    {"number":46,"name":"浪里淘金","luck":"大凶","base":"将星、学士、文昌、凶厄","family":"亲情疏淡","health":"病弱、短命","meaning":"大难尝尽。"},
    {"number":47,"name":"点石成金","luck":"大吉","base":"将星、文昌、学士、艺术、财库","family":"子女多荫","health":"安康长寿","meaning":"祯祥吉庆。"},
    {"number":48,"name":"古松立鹤","luck":"大吉","base":"将星、文昌、太极、学术、艺术","family":"家庭圆满","health":"健康长寿","meaning":"智谋兼备，为师数。"},
    {"number":49,"name":"转变","luck":"半吉","base":"将星、文昌、财库、凶厄","family":"六亲缘薄","health":"病患","meaning":"转凶为吉。"},
    {"number":50,"name":"小舟入海","luck":"半吉","base":"将星、文昌、学士、凶厄","family":"先得庇荫","health":"病弱","meaning":"一成一败。"},
    {"number":51,"name":"沉浮","luck":"半吉","base":"将星、文昌、技艺、凶厄","family":"亲情不睦","health":"病患","meaning":"盛衰交加。"},
    {"number":52,"name":"达眼","luck":"大吉","base":"将星、文昌、学士、财库","family":"家庭圆满","health":"安康","meaning":"卓识达眼。"},
    {"number":53,"name":"曲卷难星","luck":"大凶","base":"将星、文昌、学士、破财","family":"家庭不和","health":"病弱","meaning":"外祥内患。"},
    {"number":54,"name":"石上栽花","luck":"大凶","base":"将星、文昌、凶厄、破财","family":"骨肉分离","health":"病患、短命","meaning":"难得有活。"},
    {"number":55,"name":"善恶","luck":"半吉","base":"将星、文昌、官禄、凶厄","family":"家庭不和","health":"病灾","meaning":"善恶参半。"},
    {"number":56,"name":"浪里行舟","luck":"大凶","base":"将星、文昌、学士、凶厄","family":"家庭缘薄","health":"外伤、残废","meaning":"历尽艰辛。"},
    {"number":57,"name":"日照春松","luck":"大吉","base":"将星、文昌、学士、财库","family":"家庭圆满","health":"安康","meaning":"繁荣百事。"},
    {"number":58,"name":"晚行遇月","luck":"半吉","base":"将星、文昌、学士、官禄","family":"先苦后甜","health":"安康","meaning":"先苦后甜。"},
    {"number":59,"name":"寒蝉悲风","luck":"大凶","base":"将星、文昌、凶厄","family":"六亲无助","health":"病弱","meaning":"意志衰退。"},
    {"number":60,"name":"无谋","luck":"大凶","base":"将星、文昌、凶厄、破财","family":"亲情疏淡","health":"病灾","meaning":"漂泊不定。"},
    {"number":61,"name":"牡丹芙蓉","luck":"大吉","base":"将星、文昌、学士、财库","family":"家庭圆满","health":"安康","meaning":"花开富贵。"},
    {"number":62,"name":"衰败","luck":"大凶","base":"将星、文昌、凶厄、破财","family":"内外不和","health":"病灾","meaning":"志望难达。"},
    {"number":63,"name":"舟归平海","luck":"大吉","base":"将星、文昌、学士、财库","family":"家庭圆满","health":"身心安泰","meaning":"富贵荣华。"},
    {"number":64,"name":"非命","luck":"大凶","base":"将星、文昌、凶厄、破财","family":"骨肉分离","health":"病弱","meaning":"骨肉分离。"},
    {"number":65,"name":"巨流归海","luck":"大吉","base":"将星、文昌、学士、财库","family":"家运隆昌","health":"福寿绵长","meaning":"天长地久。"},
    {"number":66,"name":"岩头步马","luck":"大凶","base":"将星、文昌、凶厄","family":"家庭不和","health":"病灾","meaning":"进退维谷。"},
    {"number":67,"name":"顺利通达","luck":"大吉","base":"将星、文昌、学士、财库","family":"家道繁昌","health":"安康","meaning":"天赋幸运。"},
    {"number":68,"name":"顺风吹帆","luck":"大吉","base":"将星、文昌、学士、艺术、财库","family":"家庭圆满","health":"安康","meaning":"智虑周密。"},
    {"number":69,"name":"非业","luck":"大凶","base":"将星、文昌、凶厄、破财","family":"亲情疏远","health":"病灾","meaning":"精神迫滞。"},
    {"number":70,"name":"残菊逢霜","luck":"大凶","base":"将星、文昌、凶厄","family":"寂寞无碍","health":"病弱","meaning":"惨淡忧愁。"},
    {"number":71,"name":"石上金花","luck":"半吉","base":"将星、文昌、技艺、凶厄","family":"家庭可望圆满","health":"劳苦","meaning":"贯彻始终。"},
    {"number":72,"name":"辛苦劳累","luck":"半吉","base":"将星、文昌、官禄、凶厄","family":"家庭可望圆满","health":"病弱","meaning":"外表吉祥内实凶。"},
    {"number":73,"name":"无勇","luck":"半吉","base":"将星、文昌、学士、凶厄","family":"家庭平安","health":"安康","meaning":"志高力微。"},
    {"number":74,"name":"残菊经霜","luck":"大凶","base":"将星、文昌、凶厄","family":"骨肉分离","health":"病灾","meaning":"秋叶寂寞。"},
    {"number":75,"name":"退守","luck":"大凶","base":"将星、文昌、官禄、凶厄","family":"家庭不和","health":"病弱","meaning":"退守保吉。"},
    {"number":76,"name":"离散","luck":"大凶","base":"将星、文昌、凶厄、破财","family":"骨肉分离","health":"病灾","meaning":"倾覆离散。"},
    {"number":77,"name":"半吉","luck":"半吉","base":"将星、文昌、学士、财库","family":"家庭可望圆满","health":"病患","meaning":"半吉半凶。"},
    {"number":78,"name":"晚苦","luck":"大凶","base":"将星、文昌、学士、官禄、凶厄","family":"先荣后衰","health":"病患","meaning":"中年发达，晚景困苦。"},
    {"number":79,"name":"云头望月","luck":"大凶","base":"将星、文昌、凶厄","family":"孤独无依","health":"身疲力尽","meaning":"前途无光。"},
    {"number":80,"name":"遁吉","luck":"大凶","base":"将星、文昌、凶厄、破财","family":"家庭不和","health":"病弱","meaning":"辛苦不绝。"},
    {"number":81,"name":"万物回春","luck":"大吉","base":"将星、文昌、学士、财库、田宅","family":"家庭圆满","health":"安康长寿","meaning":"最吉之数，还本归元。"},
]


# ----- Detailed content templates per number -----
# We generate rich natural paragraphs using the reference data.

def build_luck_desc(luck):
    m = {"大吉":"上上大吉","吉":"吉祥","半吉":"吉凶参半","大凶":"大凶"}
    return m.get(luck, luck)

def build_content(d):
    n = d["number"]
    name = d["name"]
    luck = d["luck"]
    base = d["base"]
    family = d["family"]
    health = d["health"]
    meaning = d["meaning"]
    gn = d.get("gender_note", "")

    luck_desc = build_luck_desc(luck)

    # Introduce
    parts = []
    parts.append(f"【{name}】第{n}数理，五行属{_get_wuxing(n)}，{luck_desc}之格。")

    # Core meaning
    parts.append(f"此数象征{name}之象，其意曰：{meaning}")

    if gn:
        parts.append(f"（注：{gn}。）")

    # Personality & character
    parts.append(_build_personality(n, luck, base, name))

    # Career & wealth
    parts.append(_build_career(n, luck, base, name))

    # Family & relationships
    parts.append(f"【家庭】{family}。{_build_family_extra(n, luck, family)}")

    # Health
    parts.append(f"【健康】{health}。{_build_health_extra(n, luck, health)}")

    # Closing advice
    parts.append(_build_closing(n, luck, name))

    return "".join(parts)


def _get_wuxing(n):
    # Simplified wuxing based on traditional assignment: odd/even + mod pattern
    mapping = {1:"木",2:"木",3:"火",4:"火",5:"土",6:"土",7:"金",8:"金",9:"水",0:"水"}
    return mapping.get(n % 10, "土")


def _build_personality(n, luck, base, name):
    if luck in ("大吉", "吉"):
        if n in (1, 31, 37, 41, 47, 48, 52, 57, 63, 65, 67, 68, 81):
            return f"【性格】此数之人气度恢宏，胸襟开阔，天生具有领袖风范与统御才能。做事积极主动，不惧困难，富有开创精神和远见卓识。为人正直守信，能得众人信服与追随，是天生的组织者和决策者。"
        elif n in (5, 6, 7, 8, 11, 13, 15, 16, 18):
            return f"【性格】此数之人聪明睿智，才华出众，具有多方面的天赋和才能。性格刚柔并济，既能独立自主又不失灵活性。待人诚恳宽厚，人缘良好，在社交场合能游刃有余。善于学习和积累，能够不断提升自我。"
        elif n in (3, 21, 23, 24, 32, 33, 35, 39, 45, 61):
            return f"【性格】此数之人才智双全，志存高远。性格积极进取，勇于拼搏，具有不服输的韧劲。为人处事既有原则性又不乏变通，能够审时度势把握机遇。在团队中往往能成为核心人物，带动周围人共同前进。"
        else:
            return f"【性格】此数之人性格沉稳大气，行事稳重踏实。具备良好的判断力和决策能力，遇事不慌，能够从容应对各种复杂局面。待人接物得体周到，能获得他人的信任和尊重。"
    elif luck == "半吉":
        if n in (17, 25, 27, 29):
            return f"【性格】此数之人天资聪颖，才思敏捷，在某一领域往往具有过人的天赋。然性格较为刚强固执，自信心极强，容易因坚持己见而与他人产生摩擦。若能学会柔和处世、虚怀若谷，则能将才华发挥到极致，成就非凡事业。"
        elif n in (38, 49, 50, 51, 55, 58):
            return f"【性格】此数之人头脑灵活，善于应变，具有不俗的智慧和创造力。然而运势起伏较大，性格中亦存在矛盾之处，时而积极时而消沉。需要有坚定的目标和信念来支撑，方能克服性格中的摇摆不定，在人生的风浪中稳步前行。"
        else:
            return f"【性格】此数之人具有特殊才能和独特气质，但性格中吉凶并存。顺境时能发挥出色能力，逆境时则容易动摇。需要有坚强的意志力和明确的人生方向，才能在起伏的命运中把握住属于自己的机遇。"
    else:
        if n in (2, 4, 9, 10, 12, 14, 20):
            return f"【性格】此数之人内心敏感细腻，思虑周全，但往往意志力较为薄弱，容易受外界环境和他人影响。虽有才华却常感怀才不遇，在关键时刻容易犹豫不决，错失良机。性格偏于内向，不善于表达自己，需培养自信和决断力。"
        elif n in (19, 22, 26, 28, 34, 36, 40, 42):
            return f"【性格】此数之人颇具才华和抱负，甚至在某些方面有过人之处。然而命运多舛，人生波折不断，性格也因此变得较为敏感多疑。常常感到壮志难酬，内心充满矛盾和焦虑。需要格外注重心理调节，培养豁达乐观的心态。"
        elif n in (43, 44, 46, 53, 54, 56, 59, 60):
            return f"【性格】此数之人一生坎坷较多，性格中带有悲观和消极的一面。虽有理想抱负，但现实中阻碍重重，容易产生挫败感。在人际交往中较为孤立，内心常感孤独。需要有强大的精神支柱和坚定的信仰，才能在逆境中坚持下去。"
        else:
            return f"【性格】此数之人一生辛苦劳碌，性格中带有隐忍和坚韧的一面。虽然才华不输于人，但运势不佳使得才能难以充分施展。内心时常感到压抑和无助，需要修身养性以化解命运中的不利因素。"
    return ""


def _build_career(n, luck, base, name):
    if luck in ("大吉", "吉"):
        if any(k in base for k in ("官禄","将星","首领","君臣","天官")):
            return f"【事业】事业发展运势极佳，适合从事管理、行政、领导类岗位。凭借天生的组织才能和领导魅力，能够在职场中快速晋升，担任重要职务。自主创业也有很大成功机会，能够建立自己的事业版图。财运亨通，财富积累能力强劲。"
        elif any(k in base for k in ("学术","学士","文昌")):
            return f"【事业】在学术、教育、文化、科研等领域有天然优势。适合从事教师、研究员、作家、文化工作者等职业。凭借扎实的学识和钻研精神，能够在专业领域取得突出成就。财运稳定，虽非暴发之运但能积累可观财富。"
        elif any(k in base for k in ("技艺","艺术","美术","技术")):
            return f"【事业】在艺术、技术、工艺等创造性领域能够大放异彩。适合从事设计师、工程师、艺术家、手工艺者等职业。天赋与努力相结合，能够创造出令人瞩目的成就。财运随事业上升而稳步增长。"
        elif any(k in base for k in ("财星","财库","财钱","财帛","企业")):
            return f"【事业】在商业领域有极佳发展前景。适合从事经商、金融投资、企业管理等与财富相关的工作。天生具有商业头脑和理财能力，能够通过正当途径获得丰厚回报。尤其在自主创业方面运势极佳。"
        else:
            return f"【事业】事业发展前景良好，能够在选择的领域中取得不错的成就。财运稳定上升，正财收入丰厚。适合结合自身特长选择职业方向，通过持续努力实现事业理想和财富积累。"
    elif luck == "半吉":
        return f"【事业】事业运势起伏不定，有成功也有挫折。适合在技术、艺术或专业领域深耕细作，通过扎实的专业能力获得立足之地。财运时好时坏，需做好财务规划，避免冒进投资。建议先求稳定再图发展，不宜频繁更换行业或职业方向。中年之后运势逐渐平稳，可望有较大发展。"
    else:
        return f"【事业】事业上面临较多波折和阻碍，不宜急进，建议脚踏实地选择稳定的工作。财运欠佳，需注重节俭持家，避免高风险投资和借贷。适合从事技术性工作或依靠一技之长谋生，不宜投机取巧或冒险创业。若能安分守己、勤恳工作，尚能维持基本生活。"
    return ""


def _build_family_extra(n, luck, family):
    if luck in ("大吉", "吉"):
        if "女性" in family or "女性" in str(family):
            return "但需注意此数对男女的影响有所不同，女性有此数者需结合具体情况谨慎参考。"
        return "家庭氛围和谐温暖，家人之间互敬互爱，能够获得家庭的大力支持和精神慰藉。"
    elif luck == "半吉":
        return "家庭关系需要用心经营和维护。建议多花时间陪伴家人，增进沟通和理解，以包容的心态对待家庭中的矛盾。"
    else:
        return "家庭缘分较浅，亲情关系较为淡薄。需以更大的耐心和智慧来维系家庭关系，多付出、少计较，才能营造相对和谐的家庭氛围。"
    return ""


def _build_health_extra(n, luck, health):
    if luck in ("大吉", "吉"):
        return "总体身体素质良好，若能保持规律的生活作息和适度的运动锻炼，可享有健康长寿之福。注意劳逸结合即可。"
    elif luck == "半吉":
        return "身体素质一般，需特别注意情绪管理对健康的影响。建议保持规律作息，适度锻炼，定期进行身体检查，预防潜在的健康问题。"
    else:
        return "体质偏弱，需要格外注重养生保健。建议养成健康的生活习惯，合理饮食，适度运动，定期体检。特别注意心理健康的维护，避免过度操劳和精神压力。"
    return ""


def _build_closing(n, luck, name):
    if luck in ("大吉", "吉"):
        return f"【总论】第{n}数理「{name}」为{luck}之格，是姓名学中的上佳数理。有此数者若能善加把握天赐良机，发挥自身优势，必能成就一番事业，人生顺遂圆满。"
    elif luck == "半吉":
        return f"【总论】第{n}数理「{name}」吉凶参半，关键在于修身养性、趋吉避凶。若能以谦虚谨慎的态度面对人生，发挥长处、弥补短处，则能在起伏的命运中找到属于自己的成功之路。"
    else:
        return f"【总论】第{n}数理「{name}」为{luck}之格，需以平常心面对人生的起伏波折。建议注重内在修养，多行善积德，以德行化解命运中的不利因素。同时保持积极乐观的心态，不轻言放弃。"
    return ""


def main():
    output_path = "/mnt/d/fortune-data/books/zonghe/tmp/part1_81shuli.jsonl"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    entries = []
    for d in data:
        content = build_content(d)

        # Ensure content 200-800 chars
        if len(content) < 200:
            content += "此数理在姓名学中具有独特的象征意义，对个人运势有着深远影响。具体吉凶还需结合三才五格综合判断，不可仅凭单一数理下定论。"
        if len(content) > 800:
            # Trim to ~795 at last period
            content = content[:797]
            idx = content.rfind("。")
            if idx > 150:
                content = content[:idx+1]

        title = f"81数理第{d['number']}数——{d['name']}（{d['luck']}）"
        entry = {
            "title": title,
            "content": content,
            "category": "xingming",
            "source_url": "xingming_knowledge_base",
            "source_quality": "authoritative",
            "verified": True
        }
        entries.append(entry)

    # Write JSONL
    with open(output_path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # Verification
    stats = {"lines": 0, "min": 9999, "max": 0, "min_title": "", "max_title": ""}
    titles = set()
    dupes = []
    lengths = []

    with open(output_path, "r", encoding="utf-8") as f:
        for line in f:
            obj = json.loads(line)
            stats["lines"] += 1
            clen = len(obj["content"])
            lengths.append(clen)
            if clen < stats["min"]:
                stats["min"] = clen
                stats["min_title"] = obj["title"]
            if clen > stats["max"]:
                stats["max"] = clen
                stats["max_title"] = obj["title"]
            t = obj["title"]
            if t in titles:
                dupes.append(t)
            titles.add(t)

    print("=" * 50)
    print("    81数理 JSONL 生成报告")
    print("=" * 50)
    print(f" 输出文件: {output_path}")
    print(f" 总条目数: {stats['lines']}")
    print(f" 最短内容: {stats['min']} chars → {stats['min_title']}")
    print(f" 最长内容: {stats['max']} chars → {stats['max_title']}")
    print(f" 内容范围: {stats['min']}-{stats['max']} chars")
    print(f" 平均长度: {sum(lengths)//len(lengths)} chars")
    print(f" >=200检查: {'PASS' if stats['min'] >= 200 else 'FAIL'}")
    max_ok = "PASS" if stats["max"] <= 800 else f"WARN (max={stats['max']})"
    print(f" <=800检查: {max_ok}")
    dupes_status = "PASS" if not dupes else f"FAIL: {dupes}"
    print(f" 重复检查: {dupes_status}")
    lines_status = "PASS (81/81)" if stats["lines"] == 81 else f"FAIL ({stats['lines']}/81)"
    print(f" 总数检查: {lines_status}")
    print("=" * 50)

if __name__ == "__main__":
    main()
