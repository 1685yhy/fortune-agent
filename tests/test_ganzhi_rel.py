"""干支关系分析测试 — 伏吟/反吟/盖头/截脚/争合/妒合（问真 newgetGZRelaction 同款，P0-3）。

覆盖：
1. 伏吟：两柱干支完全相同（乙丑 vs 乙丑）
2. 反吟：天干同性相克 + 地支六冲（乙丑 vs 己未）
3. 盖头：柱内天干克地支（庚寅/丙申）；负例——甲子非盖头（子水生甲木）
4. 截脚：柱内地支克天干（甲申/丙子）；负例——乙亥非截脚（亥水生乙木）
5. 争合：两干争合一干（两庚争合乙，被合者非日主）
6. 妒合：两干争合日主（多干合一日干 → 妒合，与争合互斥）
7. 天克地冲：异性天克地冲不成反吟；单独天克/地冲
8. BaziResult 集成：闫海洋盘（己卯 己巳 乙丑 壬午）原局两两关系 + rel_with（大运/流年视角）
"""
from src.engines.ganzhi_rel import analyze_relations, RelationItem
from src.engines.bazi import BaziEngine

PILLARS = ['己卯', '己巳', '乙丑', '壬午']  # 闫海洋命盘（1999-05-13 11:25 男，问真锚点案例）
ENGINE = BaziEngine()


def _chart():
    return ENGINE.calculate(1999, 5, 13, 11, 25, "", "男")


# ---------------------------------------------------------------- 1. 伏吟
def test_fuyin():
    rels = analyze_relations('乙丑', '乙丑', PILLARS)
    assert any(r.type == '伏吟' for r in rels)
    f = next(r for r in rels if r.type == '伏吟')
    assert '乙丑' in f.desc and '乙丑' in f.desc
    # 不同干支不成伏吟
    assert not any(r.type == '伏吟' for r in analyze_relations('乙丑', '己未', PILLARS))


# ---------------------------------------------------------------- 2. 反吟
def test_fanyin():
    rels = analyze_relations('乙丑', '己未', PILLARS)
    assert any(r.type == '反吟' for r in rels)  # 乙己同性相克 + 丑未冲
    f = next(r for r in rels if r.type == '反吟')
    assert '乙克己' in f.desc and '丑冲未' in f.desc
    # 天克但无地冲 → 不成反吟（仅天克）
    assert not any(r.type == '反吟' for r in analyze_relations('乙丑', '己巳', PILLARS))
    assert any(r.type == '天克' for r in analyze_relations('乙丑', '己巳', PILLARS))


# ---------------------------------------------------------------- 3. 盖头
def test_gaitou():
    # 庚寅：庚金克寅木
    rels = analyze_relations('庚寅', '乙丑', PILLARS)
    assert any(r.type == '盖头' and '庚克' in r.desc and '寅' in r.desc for r in rels)
    # 丙申：丙火克申金
    rels2 = analyze_relations('丙申', '乙丑', PILLARS)
    assert any(r.type == '盖头' and '丙克' in r.desc and '申' in r.desc for r in rels2)
    # 标准口径负例：甲子非盖头（子水生甲木，非天干克地支）
    assert not any(r.type == '盖头' for r in analyze_relations('甲子', '戊辰', PILLARS))


# ---------------------------------------------------------------- 4. 截脚
def test_jiejiao():
    # 甲申：申金克甲木
    rels = analyze_relations('甲申', '丙子', PILLARS)
    assert any(r.type == '截脚' and '申克' in r.desc and '甲' in r.desc for r in rels)
    # 丙子：子水克丙火
    assert any(r.type == '截脚' and '子克' in r.desc and '丙' in r.desc for r in rels)
    # 标准口径负例：乙亥非截脚（亥水生乙木，非地支克天干）
    assert not any(r.type == '截脚' for r in analyze_relations('乙亥', '戊辰', PILLARS))


# ---------------------------------------------------------------- 5. 争合
def test_zhenghe():
    # 两庚争合乙（乙在年柱，非日主丙）→ 争合
    rels = analyze_relations('庚午', '庚辰', ['乙卯', '丁巳', '丙午', '壬午'])
    assert any(r.type == '争合' and '争合乙' in r.desc for r in rels)
    assert not any(r.type == '妒合' for r in rels)
    # pillars=None → 跳过争合/妒合，不抛异常
    rels2 = analyze_relations('庚午', '庚辰')
    assert not any(r.type in ('争合', '妒合') for r in rels2)


# ---------------------------------------------------------------- 6. 妒合
def test_duhe():
    # 两庚争合日主乙（闫海洋盘日主为乙）→ 妒合
    rels = analyze_relations('庚午', '庚辰', PILLARS)
    assert any(r.type == '妒合' and '日主' in r.desc for r in rels)
    # 日主被争合时只报妒合，不报争合（互斥）
    assert not any(r.type == '争合' for r in rels)


# ---------------------------------------------------------------- 7. 天克地冲 / 单独天克 / 单独地冲
def test_tianke_dichong():
    # 异性之克 + 冲 → 天克地冲（不成反吟）
    rels = analyze_relations('甲午', '己子', PILLARS)
    assert any(r.type == '天克地冲' for r in rels)
    assert not any(r.type == '反吟' for r in rels)
    # 单独天克（甲子 vs 戊戌：甲克戊，子戌不冲）
    assert any(r.type == '天克' for r in analyze_relations('甲子', '戊戌', PILLARS))
    # 单独地冲（甲子 vs 甲午：子午冲，天干比和）
    rels3 = analyze_relations('甲子', '甲午', PILLARS)
    assert any(r.type == '地冲' for r in rels3)
    assert not any(r.type == '天克' for r in rels3)


# ---------------------------------------------------------------- 8. BaziResult 集成（闫海洋盘）
def test_bazi_result_ganzhi_rel():
    """原局四柱间两两关系：只输出有关系的对。"""
    r = _chart()
    assert r.bazi == PILLARS
    rels = r.ganzhi_rel
    # 天克对：年-日 / 月-日（乙克己）、年-时 / 月-时（己克壬）
    assert {'between': '年-日', 'type': '天克', 'desc': '天干乙克己'} in rels
    assert {'between': '月-日', 'type': '天克', 'desc': '天干乙克己'} in rels
    assert {'between': '年-时', 'type': '天克', 'desc': '天干己克壬'} in rels
    assert {'between': '月-时', 'type': '天克', 'desc': '天干己克壬'} in rels
    # 无关系对不输出：年-月（己卯己巳 比和）、日-时（壬水生乙木）
    assert all(x['between'] != '年-月' for x in rels)
    assert all(x['between'] != '日-时' for x in rels)
    # 本盘无伏吟/反吟/争合/妒合/盖头/截脚（两两视角）
    assert all(x['type'] not in ('伏吟', '反吟', '争合', '妒合', '盖头', '截脚')
               for x in rels)


def test_bazi_result_rel_with():
    """大运/流年 vs 原局各柱（问真点大运流年看干支关系同款入口）。"""
    r = _chart()
    # 伏吟：大运乙丑 与 日柱乙丑 完全相同
    rels = r.rel_with('乙丑')
    assert any(x['between'] == '日柱' and x['type'] == '伏吟' for x in rels)
    # 反吟：流年己未 与 日柱乙丑（乙己同性克 + 丑未冲）
    rels2 = r.rel_with('己未')
    assert any(x['between'] == '日柱' and x['type'] == '反吟' for x in rels2)
    # 天克：庚午 与 日柱乙丑（庚金克乙木）
    rels3 = r.rel_with('庚午')
    assert any(x['between'] == '日柱' and x['type'] == '天克' for x in rels3)
    # 自身柱内性质：庚寅盖头、甲申截脚
    assert any(x['between'] == '自身' and x['type'] == '盖头'
               for x in r.rel_with('庚寅'))
    assert any(x['between'] == '自身' and x['type'] == '截脚'
               for x in r.rel_with('甲申'))
    # 输出结构完整
    for x in r.rel_with('乙丑'):
        assert set(x.keys()) == {'between', 'type', 'desc'}


def test_rel_item_dataclass():
    """RelationItem 字段契约：{type, desc, between}。"""
    it = RelationItem('伏吟', '乙丑与乙丑完全相同（伏吟）')
    assert it.type == '伏吟' and it.desc.startswith('乙丑') and it.between == ''


# ---------------------------------------------------------------- 9. 争合/妒合茎池双计数回归（P0-3 修复）
def test_rel_with_single_he_day_master():
    """大运/流年庚合日主乙 → 单合"庚合日主乙"，非争合非妒合，desc 无伪造双乙。"""
    r = _chart()  # 闫海洋盘：己卯 己巳 乙丑 壬午，日主乙
    for ganzhi in ('庚午', '庚辰'):
        rels = r.rel_with(ganzhi)
        # 单合：庚(大运/流年) 合日主乙
        assert any(x['type'] == '合' and x['desc'] == '庚合日主乙' for x in rels), rels
        # 非争合非妒合：仅一个庚伴，不得伪造"两干争合"
        assert not any(x['type'] in ('争合', '妒合') for x in rels), rels
        # desc 无伪造双乙：日主乙只物理存在一次，不再被茎池双计（旧 bug：乙、乙两干争合庚）
        assert all('乙、乙' not in x['desc'] for x in rels), rels
        assert all('两干' not in x['desc'] for x in rels), rels


def test_zhenghe_original_chart():
    """原局含两庚争合一乙（庚子 庚辰 戊午 乙巳）：恰好 1 条争合、计数精确（无 3 庚膨胀、无重复）。"""
    rels = BaziEngine._calc_ganzhi_rel(['庚子', '庚辰', '戊午', '乙巳'])
    zhenghe = [x for x in rels if x['type'] == '争合']
    # 年-月/年-时/月-时 多对检出同一条池级关系，仅首个柱对输出，无重复
    assert len(zhenghe) == 1, rels
    assert zhenghe[0]['desc'] == '庚、庚两干争合乙', zhenghe
    # 被合者乙非日主（日主戊午）→ 争合，不报妒合
    assert not any(x['type'] == '妒合' for x in rels), rels
    # 无 3 庚膨胀：茎池按物理存在去重（a/b 本就是原局柱，天干不重复计入）
    assert all('庚、庚、庚' not in x['desc'] for x in rels), rels
    assert all('三干' not in x['desc'] for x in rels), rels
    # 三干案例：原局两庚 + 大运庚 → 三庚争合一乙（"三干"措辞，计数精确）
    items3 = analyze_relations('庚午', '丙辰', ['庚子', '庚寅', '戊午', '乙巳'])
    zh3 = [it for it in items3 if it.type == '争合']
    assert len(zh3) == 1, items3
    assert zh3[0].desc == '庚、庚、庚三干争合乙', zh3


def test_duhe_integration():
    """日主被两干争合 → 妒合，desc 计数精确（原局庚 + 大运庚 两庚争合日主乙）。"""
    # 2020-03-23 12:00 → 庚子 己卯 乙丑 壬午：年干庚 + 日主乙
    r = ENGINE.calculate(2020, 3, 23, 12, 0, "", "男")
    assert r.bazi == ['庚子', '己卯', '乙丑', '壬午']
    rels = r.rel_with('庚午')  # 大运庚 + 原局庚 → 两庚争合日主乙
    duhe = [x for x in rels if x['type'] == '妒合']
    assert len(duhe) == 1, rels
    assert duhe[0]['desc'] == '庚、庚两干争合乙（日主被争合）', duhe
    # 日主被争合只报妒合，不报争合（互斥）
    assert not any(x['type'] == '争合' for x in rels), rels
    # analyze_relations 直接入口：两外干合日主乙（闫海洋盘无原局庚）→ 妒合，且无重复输出
    items = analyze_relations('庚午', '庚辰', PILLARS)
    duhe2 = [it for it in items if it.type == '妒合']
    assert len(duhe2) == 1, items
    assert duhe2[0].desc == '庚、庚两干争合乙（日主被争合）', duhe2
    # 三干妒合案例：原局三庚争合日主乙 → "三干"
    items3 = analyze_relations('庚午', '丙辰', ['庚子', '庚寅', '乙巳', '庚午'])
    duhe3 = [it for it in items3 if it.type == '妒合']
    assert len(duhe3) == 1, items3
    assert duhe3[0].desc == '庚、庚、庚三干争合乙（日主被争合）', duhe3
