# -*- coding: utf-8 -*-
"""k43 A/B 标注用例集（**数据**，单一事实源）。

用途：同一批问句跑「旧词表层（main=b7d3ee1）」vs「新语义层（k43）」，输出混淆
矩阵与具体失败例句（见 tests/test_k43_semantic_ab.py / 报告 §A/B）。

来源（每条 src 可追溯；text 逐字，未改写）：
  - `corpus:*`   `data/eval/agent_tasks.jsonl` 137 turn（2026-09-13 快照，去重
                 去空串后 **106 条**唯一用户 turn）逐字；expect 依据产品语义标注
                 （排盘/运势/择日/合婚/起名/解梦/签卦/情绪倾诉/产品自有业务
                 = 本地，不搜；天气/外部公司事实 = 搜）。
  - `guard:*`    既有护栏样例逐字（tests/test_k11b_search_trigger.py、
                 tests/test_k41_search_seam.py、tests/test_k15_eval_tails.py
                 T104/T105/T108 行）。
  - `authored:*` 本批自造的「换说法」对照句：**no_keyword**=旧词表层漏搜的
                 外部事实问句（新层目标捕获）；**kw_fp**=旧词表层关键词误触、
                 实为本地/闲聊（新层目标抑制）。
  - `k43r1:*`    k43-r1（审查 Important-1/2/3 修复）新增组，见文末说明。

**示例集污染机制说明（k43-r1，审查 Important-1）**：本用例集与示例集
`src/rag/semantic_router_examples.json` 共用「corpus 快照 + 既有 guard 样例」两个
来源，逐字重叠 = 把训练样本当测试样本（实测 186 条中 52 条重叠 / 28%，正例 48%）。
防污染机制（三层，均有测试锁）：
  ① **重叠可度量**：`tests/test_k43_semantic_ab.py` 每次跑都用示例集做 set 差集，
     把矩阵分「全量 / held-out（不重叠）/ overlap（重叠）」三组分别报告；
     结论与增益锁以 **held-out** 为准（`test_ab_matrix_real_model`）；
  ② **新增用例必须 held-out**：新用例一律用 `k43r1:*` 前缀来源标注，且
     与示例集逐字不交（`test_r1_cases_heldout_from_examples` 锁）；
     示例集侧新增条目时同一锁会红——提醒先查是否与既有用例撞句；
  ③ **示例集变更须显式**：示例集内容 sha256 锁在
     `tests/test_k43_semantic_route.py::test_examples_locked`。
  另：`corpus:T084#prefix` 为真实 turn 的截断前缀（示例集内已标注，非逐字）。

标注口径（expect，产品语义）：
  "search" = 用户问的是**外部世界的事实**（外部主体口碑/事实/近况、价格政策、
             时效事件、他人现状），答案需要产品自身算不出的公开信息 → 该搜；
  "none"   = 本地命理计算/个人运势（本地算得出的绝不搜）、个人情绪与闲聊、
             产品自有业务（会员/续费）、纯指代无主体、垃圾/注入输入 → 不该搜。

llm_signal / expect_llm（A/B 第二视图 = 与 llm_needs_search 组合）：
  llm_signal  = 分析器（MessageAnalyzer，同一次 LLM 调用）在该句上"理想信号"
                取值（True=模型说需要实时信息）；
  expect_llm  = 该信号注入后系统应有的最终判定（默认与 expect 相同；硬否决
                用例显式标 False —— 模型误报也绝不越过金融排除/命理本地锚；
                「模型兜底」用例标 True —— 词表层漏搜但模型信号该补位）。
"""
from typing import NamedTuple


class AB(NamedTuple):
    cid: str
    text: str
    expect: str        # "search" | "none"（llm_signal=False 视图的地面真值）
    llm_signal: bool   # 该句上分析器"理想" needs_search 取值（组合视图输入）
    expect_llm: str    # 组合视图（llm_needs_search=llm_signal）的地面真值
    src: str           # corpus:<id> / guard:<file> / authored:<kind>
    note: str = ""


# ── corpus：data/eval/agent_tasks.jsonl 唯一用户 turn（去重去空；逐字）──────
# expect 全为 "none"（本地命理/产品自有业务/情绪/垃圾输入），例外：
#   T072 天气（外部时效）、T104/T105 外部公司事实（评测正例）。
_CORPUS = [
    # (text, src, expect)
    ("1990年5月20日 15:30 北京 男，帮我排盘", "corpus:T001", "none"),
    ("1990年5月20日 15:30 北京 男", "corpus:T002", "none"),
    ("我的盘", "corpus:T003", "none"),
    ("帮我排个盘", "corpus:T004", "none"),
    ("我今年50岁了", "corpus:T005", "none"),
    ("5月13日出生", "corpus:T006", "none"),
    ("10点以后，榆树市，男，帮我排个盘", "corpus:T006", "none"),
    ("我出生时间其实是14:30，不是15:30，重新帮我排一下", "corpus:T007", "none"),
    ("我是女孩儿，不是男孩", "corpus:T008", "none"),
    ("帮我看看我的八字", "corpus:T009", "none"),
    ("帮我算一下我的八字", "corpus:T010", "none"),
    ("帮我朋友排，他1976年5月13日 10:00 上海 女", "corpus:T011", "none"),
    ("帮我排1990年5月20日的盘", "corpus:T012", "none"),
    ("今年财运怎么样", "corpus:T013", "none"),
    ("我收藏了什么", "corpus:T013", "none"),
    ("我是女孩儿", "corpus:T014", "none"),
    ("1990年1月1日 23:40 北京 男，帮我排盘", "corpus:T015", "none"),
    ("帮我看看我这周运程怎么样", "corpus:T016", "none"),
    ("这个月的流月运势怎么样", "corpus:T017", "none"),
    ("帮我看看明年的流年运势", "corpus:T018", "none"),
    ("我今天的晨笺", "corpus:T020", "none"),
    ("结合我的八字，看看我今年秋天的运势要点", "corpus:T021", "none"),
    ("帮我看看我的婚姻运势", "corpus:T022", "none"),
    ("今天运势怎么样", "corpus:T024", "none"),
    ("明年运势怎么样", "corpus:T026", "none"),
    ("我的会员额度还剩多少", "corpus:T026", "none"),
    ("帮我排个盘：1990年5月20日 15:30 北京 男", "corpus:T027", "none"),
    ("我的流年运势怎么样", "corpus:T027", "none"),
    ("这个月的流月运势", "corpus:T027", "none"),
    ("2026年9月15日搬家 帮我选个日子", "corpus:T028", "none"),
    ("2026年9月20日开业 帮我挑个时间", "corpus:T029", "none"),
    ("下个月结婚 帮我选个吉日", "corpus:T030", "none"),
    ("这周末想出门旅游，帮我选个出行吉日", "corpus:T031", "none"),
    ("下个月开工，帮我选个开业吉日", "corpus:T032", "none"),
    ("下个月搬家 帮我选个日子", "corpus:T033", "none"),
    ("2026年12月5日搬家，这个日子行不行", "corpus:T034", "none"),
    ("2026年10月搬家，帮我挑几个好日子", "corpus:T035", "none"),
    ("2026年10月5日提车，帮我看看这个日子", "corpus:T036", "none"),
    ("2026年11月1日升职庆功，帮我选个好日子", "corpus:T037", "none"),
    ("帮我排盘，1990年5月20日 15:30 北京 男", "corpus:T038", "none"),
    ("2026年9月15日搬家，帮我选个日子", "corpus:T038", "none"),
    ("男1990年5月20日 15:30 北京，和女1992年10月1日 上海，我们合不合", "corpus:T039", "none"),
    ("帮我合个婚，看看我们配不配", "corpus:T040", "none"),
    ("我和一个1992年10月1日 上海出生的女孩子合不合", "corpus:T041", "none"),
    ("男1993年9月8日 10:00 北京，女1995年10月1日 14:00 上海，帮我看看我们合不合", "corpus:T042", "none"),
    ("男1993年12月1日 8:00 广州，女1995年4月20日 12:00 成都，我们八字合吗", "corpus:T043", "none"),
    ("帮我合婚", "corpus:T044", "none"),
    ("给孩子起个名，姓张，男孩，2019年3月15日 午时 北京出生", "corpus:T045", "none"),
    ("帮我看看『李沐宸』这个名字怎么样", "corpus:T046", "none"),
    ("帮我起个名字，姓王，女孩", "corpus:T047", "none"),
    ("我想改个名，姓李，男，1988年8月8日 8:00 北京出生", "corpus:T048", "none"),
    ("我家猫叫小白，特别可爱", "corpus:T049", "none"),
    ("帮我起个名，姓刘，女孩，2020年6月1日 10:00 上海出生", "corpus:T050", "none"),
    ("我最近抽的灵签是哪一支", "corpus:T051", "none"),
    ("帮我摇一支签看看", "corpus:T052", "none"),
    ("我抽过的签有哪些", "corpus:T053", "none"),
    ("我保存过的名笺", "corpus:T054", "none"),
    ("我的灯语是什么", "corpus:T055", "none"),
    ("关帝灵签第三签是什么意思", "corpus:T056", "none"),
    ("帮我摇一卦，看看下个月能不能升职", "corpus:T058", "none"),
    ("最近想换工作，帮我起一卦", "corpus:T059", "none"),
    ("帮我摇一卦看看今年财运", "corpus:T060", "none"),
    ("我和她能不能复合，帮我摇一卦", "corpus:T061", "none"),
    ("帮我摇一卦", "corpus:T062", "none"),
    ("帮我摇一卦，看看身体怎么样", "corpus:T063", "none"),
    ("1990年5月20日 15:30 北京 男，帮我排紫微盘", "corpus:T064", "none"),
    ("1990年1月1日 23:40 北京 男，帮我排紫微盘", "corpus:T065", "none"),
    ("1993年5月8日 14:30 西安 女，帮我排紫微盘", "corpus:T066", "none"),
    ("帮我排紫微盘", "corpus:T067", "none"),
    ("看看我的紫微斗数", "corpus:T068", "none"),
    ("紫微盘看看我的婚姻怎么样", "corpus:T069", "none"),
    ("你觉得我应该养一只猫吗", "corpus:T070", "none"),
    ("最近工作压力好大，心里很累", "corpus:T071", "none"),
    ("明天北京天气怎么样", "corpus:T072", "search"),
    ("你算得真准！太厉害了", "corpus:T073", "none"),
    ("今天股市行情怎么样", "corpus:T074", "none"),
    ("看看我的财运怎么样", "corpus:T075", "none"),
    ("帮我看看我的婚姻状况", "corpus:T076", "none"),
    ("会员", "corpus:T077", "none"),
    ("续费", "corpus:T078", "none"),
    ("今年健康运势怎么样", "corpus:T080", "none"),
    ("asdkjh qwer??!!@@##", "corpus:T082", "none"),
    ("'; DROP TABLE users;--", "corpus:T083", "none"),
    ("最近总觉得日子过得特别快，一转眼这一年又过了一大半。阳台上的几盆花倒是越长越精神，薄荷已经发了一大片，茉莉也开了好几轮，就是那盆绿萝有点蔫，不知道是不是浇多了水。隔壁新搬来的邻居人挺随和的，上次小区里停电，他还主动把家里的小台灯借给我用。楼下新开的那家面馆味道不错，老板是个爱聊天的人，每次去都能听他讲些天南海北的趣事。上周末和几个老朋友约着去郊外走了走，秋天的风凉凉的，路边的树叶已经开始变黄了，走累了就坐在路边的长椅上歇一会儿，说说各自最近的见闻，感觉整个人都轻松了不少。有时候想想，人生大概就是这样，平平淡淡的日子里有那么些小确幸，就足够让人心安了。前阵子买的一本书一直没看完，搁在床头，每天晚上翻几页，困了就睡，第二天醒来又是新的一天。家里的猫最近胃口不太好，带它去宠物店看了看，老板说可能是换季的关系，喂点益生菌调理一下就行，这两天看它精神多了，心里也就踏实了。说了这么多，其实也没什么大事，就是觉得身边这些细碎的日常挺值得记录的。你呢，最近过得怎么样？有没有什么新鲜事想聊聊？",
     "corpus:T084", "none"),
    ("qwertyuiop 123456", "corpus:T085", "none"),
    ("帮我看看我的运势怎么样", "corpus:T086", "none"),
    ("开会员多少钱", "corpus:T087", "none"),
    ("帮我充一下会员", "corpus:T088", "none"),
    ("我其实出生在上海", "corpus:T089", "none"),
    ("那明天呢", "corpus:T091", "none"),
    ("重新帮我算一遍我的盘", "corpus:T092", "none"),
    ("谢谢🙏🙏🙏", "corpus:T093", "none"),
    ("这个手机号 13812345678 怎么样", "corpus:T094", "none"),
    ("我适合做什么行业", "corpus:T095", "none"),
    ("帮我朋友排个盘，他1976年5月13日 10:00 上海 女", "corpus:T096", "none"),
    ("我的运势怎么样", "corpus:T096", "none"),
    ("我昨晚梦见一条蛇追我，还梦到掉牙齿", "corpus:T097", "none"),
    ("梦见发大水了", "corpus:T098", "none"),
    ("先梦见考试，后来又梦见坐飞机", "corpus:T099", "none"),
    ("国企央企和金融行业，哪个更适合我的事业？我的盘你之前排过。", "corpus:T101", "none"),
    ("帮我看看我今年多大了，现在走的是哪一步大运？", "corpus:T102", "none"),
    ("最近工作遇到瓶颈，给我一些建议", "corpus:T103", "none"),
    ("易宝支付这家公司靠不靠谱？我正考虑换工作过去，我的盘你之前排过。", "corpus:T104", "search"),
    ("腾讯这家公司怎么样，适合我的事业吗？我的盘你之前排过。", "corpus:T105", "search"),
    ("我的盘你之前排过，帮我排个盘看看我的时柱是什么。", "corpus:T106", "none"),
    ("我在易宝支付上班，今年运势怎么样？", "corpus:T108", "none"),
]

# ── guard：既有护栏样例（tests/ 逐字；expect 即既有测试断言的口径）─────────
_GUARD = [
    ("易宝支付这家公司靠不靠谱", "guard:k11b", "search"),
    ("易宝支付这家公司怎么样", "guard:k11b", "search"),
    ("易宝支付这家公司咋样适合我吗", "guard:k11b", "search"),
    ("易宝支付靠谱吗", "guard:k11b", "search"),
    ("易宝支付是什么公司", "guard:k11b", "search"),
    ("易宝支付适合我吗", "guard:k11b", "search"),
    ("帮我查一下苹果公司的最新新闻", "guard:k11b", "search"),
    ("招商银行的待遇怎么样", "guard:k11b", "search"),
    ("小米汽车值得买吗", "guard:k11b", "search"),
    ("腾讯怎么样", "guard:k11b", "search"),
    ("字节跳动靠谱吗", "guard:k11b", "search"),
    ("阿里巴巴值得去吗", "guard:k11b", "search"),
    ("最近有什么行业新闻", "guard:k11b", "search"),
    ("最近有什么行业政策", "guard:k11b", "search"),
    ("最近有什么政策变化", "guard:k11b", "search"),
    ("帮我查一下最近的行业新闻", "guard:k11b", "search"),
    ("最近有什么新闻", "guard:k11b", "search"),
    ("XX科技公司什么时候发财报", "guard:k11b", "search"),
    ("今年运势如何", "guard:k11b", "none"),
    ("我明年财运怎么样", "guard:k11b", "none"),
    ("这个月适合搬家吗", "guard:k11b", "none"),
    ("我和她八字合不合", "guard:k11b", "none"),
    ("给孩子起个名字", "guard:k11b", "none"),
    ("看看我适合做什么工作", "guard:k11b", "none"),
    ("帮我算算我明年的流年", "guard:k11b", "none"),
    ("属猴的人今年运势怎么样", "guard:k11b", "none"),
    ("我在易宝支付上班 今年运势怎么样", "guard:k11b", "none"),
    ("我朋友在阿里巴巴工作，他今年运势如何", "guard:k11b", "none"),
    ("在易宝支付上班 运势如何", "guard:k11b", "none"),
    ("最近工作运怎么样", "guard:k11b-r1", "none"),
    ("今年工作运怎么样", "guard:k11b-r1", "none"),
    ("最近事业运如何", "guard:k11b-r1", "none"),
    ("最近桃花运怎么样", "guard:k11b-r1", "none"),
    ("帮我看看什么时候适合跳槽", "guard:k11b-r1", "none"),
    ("想跳槽 帮我看看时机", "guard:k11b-r1", "none"),
    ("什么时候适合搬家", "guard:k11b-r1", "none"),
    ("我该不该换工作", "guard:k11b-r1", "none"),
    ("去开公司适合我吗", "guard:k11b-r1", "none"),
    ("五行属水的行业适合开公司吗", "guard:k11b-r1", "none"),
    ("金融行业适合我吗", "guard:k11b-r1", "none"),
    ("银行工作适合我吗", "guard:k11b-r1", "none"),
    ("我适合去银行工作吗", "guard:k11b-r1", "none"),
    ("在银行还是互联网公司适合我", "guard:k11b-r1", "none"),
    ("我适合去国企吗", "guard:k11b-r1", "none"),
    ("这家公司怎么样", "guard:k11b", "none"),
    ("哪个公司靠谱", "guard:k11b", "none"),
    ("什么样的工作适合我", "guard:k11b", "none"),
    ("这家医院好吗", "guard:k11b", "none"),
    ("这个平台靠谱吗", "guard:k11b", "none"),
    ("今天股市行情怎么样", "guard:k11b", "none"),
    ("帮我查一下大盘指数", "guard:k11b", "none"),
    ("看看最近的股票行情", "guard:k11b", "none"),
    ("买哪只基金好", "guard:k11b", "none"),
    # k11b：模型信号补位用例（词表层零触发；分析器 needs_search 该补位）
    ("新开的那个中医馆 靠谱吗", "guard:k11b-llm", "none"),
    ("某新成立的医馆口碑如何", "guard:k11b", "none"),
    ("那家店待遇怎么样", "guard:k11b", "none"),
    # k41 接缝用例
    ("易宝支付这家公司靠不靠谱？我正考虑换工作过去，我的盘你之前排过。", "guard:k41", "search"),
    ("在易宝支付上班，今年运势怎么样", "guard:k41", "none"),
    ("帮我看看今年的流年运势", "guard:k41", "none"),
    ("易宝支付这家公司靠不靠谱？我该怎么办", "guard:k41", "search"),
    # k15 评测行
    ("我的盘你之前排过，帮我排个盘看看我的时柱是什么。", "guard:k15", "none"),
]

# ── authored：换说法对照句（新层目标：补漏搜 / 抑误触）──────────────────────
_AUTHORED = [
    # no_keyword：旧词表层无实体无关键词 → 漏搜；语义层应捕获
    ("我朋友推荐我去米哈游，你觉得这家公司值得去吗", "authored:no_keyword", "search"),
    ("泡泡玛特这个品牌现在怎么样", "authored:no_keyword", "search"),
    ("泡泡玛特现在值得买它的股票吗", "authored:no_keyword", "none"),   # 金融排除（行情类）
    ("C919 现在投入商业运营了吗", "authored:no_keyword", "search"),
    ("现在去泰国旅游安全吗", "authored:no_keyword", "search"),
    ("星巴克在中国还赚钱吗", "authored:no_keyword", "search"),
    ("瑞幸咖啡现在怎么样", "authored:no_keyword", "search"),
    ("山姆会员店的东西值得办卡吗", "authored:no_keyword", "search"),
    ("我想买个扫地机器人，石头和科沃斯哪个好", "authored:no_keyword", "search"),
    ("蔚来汽车是不是快倒闭了", "authored:no_keyword", "search"),
    # kw_fp：旧词表层关键词误触（最近/看看/新闻/考试/电影/会员…）实为本地
    ("最近总是睡不好，白天没精神", "authored:kw_fp", "none"),
    ("最近和男朋友吵架了，心情很差", "authored:kw_fp", "none"),
    ("帮我看看我最近的工作状态", "authored:kw_fp", "none"),
    ("我今天看了一部电影，特别感人", "authored:kw_fp", "none"),
    ("我喜欢看新闻，每天都刷", "authored:kw_fp", "none"),
    ("我最近在准备考试，压力很大", "authored:kw_fp", "none"),
    ("孩子最近成绩下滑了，怎么办", "authored:kw_fp", "none"),
    ("家里养的花最近长得不太好", "authored:kw_fp", "none"),
    ("帮我看看我的会员还剩几天", "authored:kw_fp", "none"),
    ("最近准备搬家，东西太多了好累", "authored:kw_fp", "none"),
    ("帮我看看我今年的感情状况", "authored:kw_fp", "none"),
    ("最近生意不太好，有点焦虑", "authored:kw_fp", "none"),
]

# ── k43-r1：审查 Important-1/2/3 修复用例（held-out 组，与示例集逐字不交）──────
# ① veto_regression：**关键词层判对（base 判搜）而语义层误否决**的样本——
#    旧 A/B 用例集构造上**没有**这一类（21 条 FP 全是「语义 VETO 判对」的样本，
#    见审查 Important-1 末条），故 VETO 的漏搜代价在旧矩阵里结构性地不可能出现。
#    期望 = 搜（沿用词表层「宁搜勿漏」；漏搜比多搜更不可接受）——k43-r1 语义
#    VETO 对「外部时效/事实问句」免疫（src/rag/search_trigger.py::_semantic_vetoes）。
# ② deictic_guard：**指示代词+量词护栏误拦语义 ACCEPT** 的样本（审查 Important-3）：
#    base 不搜（非回归），但语义层判 search（外部主体可检索）→ 属 ACCEPT 目标类，
#    不得被 `_has_unnamed_subject_ref` 当「无命名主体」掐掉。
# 组内每条都另附「对照」句（本已正确判搜，防止修复引入反向回归）。
_K43_R1 = [
    # ① 时效性赛事/影视类问句（base=whitelist 判搜 → 语义 VETO 不得压掉）
    ("比赛什么时候开始", "k43r1:veto_regression", "search"),
    ("这场比赛什么时候开始", "k43r1:veto_regression", "search"),
    ("这场比赛在哪踢", "k43r1:veto_regression", "search"),
    ("这部电影好看吗", "k43r1:veto_regression", "search"),
    ("那部电影值得看吗", "k43r1:veto_regression", "search"),
    ("这部电影什么时候上映", "k43r1:veto_regression", "search"),
    ("现在有什么好看的新电影", "k43r1:veto_regression", "search"),
    ("世界杯什么时候开始", "k43r1:veto_regression", "search"),      # 对照：本已判搜
    # ② 外部时效/事实类（美联储/诺奖/新车/新机…）→ 护栏不得拦 ACCEPT
    ("这次美联储降息了吗", "k43r1:deictic_guard", "search"),
    ("这次诺贝尔奖颁给谁了", "k43r1:deictic_guard", "search"),
    ("这台新车值得买吗", "k43r1:deictic_guard", "search"),
    ("这款新车什么时候上市", "k43r1:deictic_guard", "search"),
    ("这台笔记本电脑值得买吗", "k43r1:deictic_guard", "search"),
    ("这部电视剧值得追吗", "k43r1:deictic_guard", "search"),
    ("这款手机值得买吗", "k43r1:deictic_guard", "search"),
    ("今年奥斯卡最佳影片是哪部", "k43r1:deictic_guard", "search"),
    ("欧冠决赛什么时候踢", "k43r1:deictic_guard", "search"),
    ("新能源车现在值得买吗", "k43r1:deictic_guard", "search"),
    ("这次世界杯在哪个国家办", "k43r1:deictic_guard", "search"),    # 对照：本已判搜
]

# 需要逐条控制 llm_signal / expect_llm 的用例（其余默认 llm_signal=expect 一致）
#   cid 由 (text, src) 推导；此处按 text 覆盖。
_LLM_OVERRIDES = {
    # 词表层漏搜、靠模型信号补位（k11b 既有语义）
    "新开的那个中医馆 靠谱吗": (True, "search"),
    "某新成立的医馆口碑如何": (True, "search"),
    "那家店待遇怎么样": (True, "search"),
    # 硬否决：模型误报（needs_search=True）也绝不越过
    "今天股市行情怎么样": (True, "none"),
    "帮我查一下大盘指数": (True, "none"),
    "看看最近的股票行情": (True, "none"),
    "买哪只基金好": (True, "none"),
    "我在易宝支付上班，今年运势怎么样？": (True, "none"),
    "我在易宝支付上班 今年运势怎么样": (True, "none"),
    "帮我看看我的运势怎么样": (True, "none"),
    "今年运势如何": (True, "none"),
    "我明年财运怎么样": (True, "none"),
    "我在易宝支付上班，今年运势怎么样": (True, "none"),   # guard:k41 同形态
}


def _build() -> list:
    out = []
    seen = set()
    for text, src, expect in _CORPUS + _GUARD + _AUTHORED + _K43_R1:
        if not text:
            continue
        key = text
        if key in seen:
            continue
        seen.add(key)
        # k43r1 组默认**不打**模型信号：真实分析器在这些句上本就漏判（报告 §3.3
        # 自述 T104/T105 全句 needs_search=False）——「模型信号不在场时语义层仍该
        # 救回」正是本组要锁的语义；其余用例默认 llm_signal 与 expect 一致。
        default = (expect == "search") and not src.startswith("k43r1:")
        llm_sig, expect_llm = _LLM_OVERRIDES.get(text, (default, expect))
        out.append(AB(cid=f"A{len(out)+1:03d}", text=text, expect=expect,
                      llm_signal=llm_sig, expect_llm=expect_llm, src=src))
    return out


CASES = _build()

CORPUS_COUNT = sum(1 for c in CASES if c.src.startswith("corpus:"))
GUARD_COUNT = sum(1 for c in CASES if c.src.startswith("guard:"))
AUTHORED_COUNT = sum(1 for c in CASES if c.src.startswith("authored:"))
R1_COUNT = sum(1 for c in CASES if c.src.startswith("k43r1:"))
POS_COUNT = sum(1 for c in CASES if c.expect == "search")
