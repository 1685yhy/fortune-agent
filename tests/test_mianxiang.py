"""Tests for Mianxiang Engine - 五行面相分析."""
from src.engines.mianxiang import MianxiangEngine, MianxiangResult


def test_mianxiang_default():
    """默认分析（无描述）应返回合理结果"""
    engine = MianxiangEngine()
    result = engine.analyze()
    assert isinstance(result, MianxiangResult)
    assert result.face_type in ["金形", "木形", "水形", "火形", "土形"]
    assert len(result.three_zones) == 3  # 上停、中停、下停
    assert len(result.five_mountains) == 5  # 五岳
    assert len(result.features) == 5  # 五官
    assert result.overall is not None
    assert len(result.overall) > 0


def test_mianxiang_face_type_from_description():
    """从描述中推断脸型"""
    engine = MianxiangEngine()

    # 金形（方脸）
    result = engine.analyze(description="方脸，骨骼分明，轮廓刚硬")
    assert result.face_type == "金形"

    # 木形（长脸）
    result = engine.analyze(description="长脸，身材瘦高")
    assert result.face_type == "木形"

    # 水形（圆脸）
    result = engine.analyze(description="圆脸，丰满，皮肤润泽")
    assert result.face_type == "水形"

    # 火形（尖脸）
    result = engine.analyze(description="上宽下尖的瓜子脸")
    assert result.face_type == "火形"

    # 土形（厚实）
    result = engine.analyze(description="脸型宽阔敦厚，方正")
    assert result.face_type == "土形"


def test_mianxiang_face_type_from_features():
    """从 features 中指定脸型"""
    engine = MianxiangEngine()
    result = engine.analyze(features={"face_type": "木形"})
    assert result.face_type == "木形"


def test_mianxiang_three_zones():
    """三停分析"""
    engine = MianxiangEngine()

    # 通过 features 指定三停特征
    result = engine.analyze(features={
        "forehead": "饱满宽阔",
        "nose": "端正挺直",
    })
    assert "上停" in result.three_zones
    assert "中停" in result.three_zones
    assert "下停" in result.three_zones
    # 额头饱满
    assert "饱满" in result.three_zones["上停"] or "丰隆" in result.three_zones["上停"]
    # 鼻子端正
    assert "端正" in result.three_zones["中停"] or "挺直" in result.three_zones["中停"]


def test_mianxiang_five_mountains():
    """五岳分析"""
    engine = MianxiangEngine()
    result = engine.analyze(features={
        "forehead": "高耸",
        "nose": "丰隆",
        "chin": "圆润",
    })
    assert "南岳(额头)" in result.five_mountains
    assert "中岳(鼻子)" in result.five_mountains
    assert "北岳(下巴)" in result.five_mountains
    assert "东岳(左颧)" in result.five_mountains
    assert "西岳(右颧)" in result.five_mountains
    # 额头高耸
    assert "高耸" in result.five_mountains["南岳(额头)"]
    # 鼻子丰隆
    assert "丰隆" in result.five_mountains["中岳(鼻子)"]
    # 右颧应该是默认值
    assert result.five_mountains["西岳(右颧)"] is not None


def test_mianxiang_five_features():
    """五官分析"""
    engine = MianxiangEngine()
    result = engine.analyze(features={
        "eyebrows": "浓密清秀",
        "eyes": "有神",
        "ears": "大厚",
        "mouth": "唇红齿白",
    })

    assert "眉(保寿官)" in result.features
    assert "眼(监察官)" in result.features
    assert "耳(采听官)" in result.features
    assert "鼻(审辨官)" in result.features
    assert "口(出纳官)" in result.features
    # 眼睛有神
    assert "有神" in result.features["眼(监察官)"] or "精力充沛" in result.features["眼(监察官)"]


def test_mianxiang_comprehensive():
    """综合分析"""
    engine = MianxiangEngine()
    result = engine.analyze(
        description="方脸，额头饱满，鼻梁挺直，眼睛有神，下巴圆润",
        features={
            "eyebrows": "浓密",
        },
    )
    assert result.face_type == "金形"
    assert "饱满" in result.three_zones["上停"]
    assert "挺直" in result.three_zones["中停"] or "高挺" in result.three_zones["中停"]
    assert "圆润" in result.three_zones["下停"] or "饱满" in result.three_zones["下停"]
    # 综合判断不应为空
    assert len(result.overall) > 20


def test_mianxiang_features_detail_keys():
    """测试五官详尽分析"""
    engine = MianxiangEngine()
    result = engine.analyze(features={
        "eyes": "眼神深邃清澈",
        "ears": "耳朵厚实贴脑",
        "nose": "鼻头丰隆，鼻梁高挺",
    })
    # 鼻子
    nose_analysis = result.features["鼻(审辨官)"]
    assert "丰隆" in nose_analysis or "高挺" in nose_analysis or "自信" in nose_analysis


def test_mianxiang_full_description():
    """长描述测试"""
    desc = (
        "此人面如满月，脸型偏圆润丰满。额头宽阔饱满，印堂光亮。"
        "眉毛清秀弯长，眼睛大而有神，黑白分明。鼻梁高挺端正，鼻头丰隆有肉。"
        "嘴唇红润，厚薄适中。耳朵厚实贴脑，耳垂肥大。下巴圆润饱满。"
    )
    engine = MianxiangEngine()
    result = engine.analyze(description=desc)
    assert result.face_type == "水形"  # 圆脸→水形
    # 各个部位应该都有分析结果
    assert "饱满" in result.three_zones["上停"]
    assert result.face_type is not None
    for feat_name, analysis in result.features.items():
        assert len(analysis) > 0
    print(f"\n脸型: {result.face_type}")
    print(f"三停: {result.three_zones}")
    print(f"五岳: {result.five_mountains}")
    print(f"五官: {result.features}")
    print(f"\n综合判断:\n{result.overall}")


def test_mianxiang_deterministic():
    """相同输入应返回相同结果"""
    engine = MianxiangEngine()
    desc = "方脸，额头饱满"
    r1 = engine.analyze(description=desc)
    r2 = engine.analyze(description=desc)
    assert r1.face_type == r2.face_type
    assert r1.three_zones == r2.three_zones
    assert r1.overall == r2.overall


def test_mianxiang_bad_features():
    """不吉利的特征分析"""
    engine = MianxiangEngine()
    result = engine.analyze(features={
        "forehead": "狭窄凹陷",
        "nose": "歪斜",
        "chin": "尖薄",
    })
    assert "狭窄" in result.three_zones["上停"] or "凹陷" in result.three_zones["上停"]
    assert "不正" in result.three_zones["中停"] or "塌" in result.three_zones["中停"]


def test_mianxiang_all_face_types():
    """测试所有五种脸型"""
    engine = MianxiangEngine()
    face_types = {
        "金形": ["方脸", "国字脸", "方形"],
        "木形": ["长脸", "瘦长", "狭长"],
        "水形": ["圆脸", "圆润", "丰满肉感"],
        "火形": ["尖脸", "心形脸", "倒三角"],
        "土形": ["敦厚", "宽阔厚重"],
    }
    for ftype, keywords in face_types.items():
        for kw in keywords:
            result = engine.analyze(description=kw)
            assert result.face_type == ftype, f"Expected {ftype} for '{kw}', got {result.face_type}"
