// 择吉日 7 场景元数据 —— 唯一来源（fix-later: zeri.js 与 zeri_guide.js 三处重复抽到此处）
// 与后端 src/engines/zeri.py SCENES 的 key 一致（场景规则以后端为准，这里只管前端展示）。
// seal 为印章单字（即图标映射：网格/卡片上的朱印印章充当图标）；sub 对齐 dir_b v7 原型文案。
const SCENES = [
  { key: '嫁娶', seal: '嫁', sub: '择吉 · 订盟 · 行礼' },
  { key: '搬家', seal: '迁', sub: '入宅 · 移徙 · 安床' },
  { key: '开业', seal: '开', sub: '开市 · 纳财 · 剪彩' },
  { key: '晋升', seal: '晋', sub: '升职 · 加薪 · 竞聘' },
  { key: '出行', seal: '行', sub: '启程 · 会友 · 求财' },
  { key: '提车', seal: '车', sub: '祈福 · 出行 · 安机械' },
  { key: '签约', seal: '签', sub: '交易 · 订盟 · 纳财' },
];

module.exports = { SCENES };
