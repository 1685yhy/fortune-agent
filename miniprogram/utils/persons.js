// 易理明灯 — 多人命主档案 · 共享工具（onboarding / persons / bazi 三页共用）
// 契约字段（后端并行实现中，已定稿）：
//   person = { id, name, relation, gender, birth_year, birth_month, birth_day,
//              birth_hour, birth_minute, calendar, city, is_default, created_at }
//   gender: 'male' | 'female'；birth_hour: 时辰代表整点（23/1/3/…/21）；calendar: 'solar'|'lunar'
// 接口未就绪时：页面级优雅降级，本地缓存 ylm_persons 兜底（不阻塞）。

const LOCAL_KEY = 'ylm_persons';      // 本地缓存（接口失败时的读兜底 + 乐观写）

/* 关系选项（原型 dir_u RELS） */
const RELATIONS = ['自己', '父母', '伴侣', '子女', '朋友'];

/* 时辰（12 时辰）：序号 → 代表整点（子时23-01 取 23，其后每时辰取起始整点） */
const HOUR_LABELS = [
  '子时 23-01', '丑时 01-03', '寅时 03-05', '卯时 05-07',
  '辰时 07-09', '巳时 09-11', '午时 11-13', '未时 13-15',
  '申时 15-17', '酉时 17-19', '戌时 19-21', '亥时 21-23',
];
const HOUR_VALUES = [23, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21];
const HOUR_CN = ['子时', '丑时', '寅时', '卯时', '辰时', '巳时', '午时', '未时', '申时', '酉时', '戌时', '亥时'];

/** 时辰字段（整点或旧序号）→ 时辰序号 0-11（无法识别 → 0 子时） */
function hourToShichenIndex(hour) {
  const h = parseInt(hour, 10);
  if (Number.isNaN(h) || h < 0) return 0;
  const idx = HOUR_VALUES.indexOf(h);
  if (idx !== -1) return idx;
  if (h <= 11) return h;      // 旧数据：直接存了时辰序号
  return 0;
}

/** 时辰序号 → 代表整点（存档用） */
function shichenIndexToHour(idx) {
  return HOUR_VALUES[Math.max(0, Math.min(11, parseInt(idx, 10) || 0))];
}

/** 时辰序号 → 中文（子时/丑时…） */
function shichenCN(idx) {
  return HOUR_CN[Math.max(0, Math.min(11, parseInt(idx, 10) || 0))] || '';
}

/** 性别：contract 'male'|'female' → 展示 '男'|'女' */
function genderCN(g) {
  return g === 'female' || g === '女' ? '女' : '男';
}
/** 展示 '男'|'女' → contract 'male'|'female' */
function genderCode(g) {
  return g === '女' ? 'female' : 'male';
}

/** 生辰摘要（原型 birthLine / prof-birth）：公历 1998年5月12日 卯时 女 · 北京 */
function birthSummary(p) {
  if (!p) return '';
  const cal = p.calendar === 'lunar' ? '农历' : '公历';
  const shi = p.birth_hour !== undefined && p.birth_hour !== null && p.birth_hour !== ''
    ? shichenCN(hourToShichenIndex(p.birth_hour)) + ' '
    : '';
  return `${cal} ${p.birth_year}年${p.birth_month}月${p.birth_day}日 ${shi}${genderCN(p.gender)} · ${p.city || '未填出生地'}`;
}

/** 列表页摘要（原型 prof-birth 简版）：1998 年 5 月 12 日 · 卯时 · 女 · 北京 */
function birthBrief(p) {
  if (!p) return '';
  const shi = p.birth_hour !== undefined && p.birth_hour !== null && p.birth_hour !== ''
    ? shichenCN(hourToShichenIndex(p.birth_hour)) + ' · '
    : '';
  return `${p.birth_year} 年 ${p.birth_month} 月 ${p.birth_day} 日 · ${shi}${genderCN(p.gender)} · ${p.city || '未填'}`;
}

/** 印章首字：姓名首字（无姓名 → 命） */
function sealChar(p) {
  const n = (p && p.name || '').trim();
  return n ? n.charAt(0) : '命';
}

// ---- 本地缓存（接口降级兜底） ----

function getLocalPersons() {
  try {
    const list = wx.getStorageSync(LOCAL_KEY);
    return Array.isArray(list) ? list : [];
  } catch (e) {
    return [];
  }
}

function saveLocalPersons(list) {
  try {
    wx.setStorageSync(LOCAL_KEY, Array.isArray(list) ? list : []);
  } catch (e) { /* ignore */ }
}

/** 全量拉取：接口优先 → 失败降级本地缓存（永不 reject） */
function loadPersons() {
  const api = require('./api');
  return api.getPersons()
    .then((res) => {
      const list = (res && res.persons) || [];
      saveLocalPersons(list);
      return list;
    })
    .catch(() => getLocalPersons());
}

/** 取默认命主（列表可能来自本地缓存） */
function findDefault(list) {
  if (!Array.isArray(list)) return null;
  return list.find((p) => !!p.is_default) || null;
}

module.exports = {
  RELATIONS,
  HOUR_LABELS,
  HOUR_VALUES,
  HOUR_CN,
  LOCAL_KEY,
  hourToShichenIndex,
  shichenIndexToHour,
  shichenCN,
  genderCN,
  genderCode,
  birthSummary,
  birthBrief,
  sealChar,
  getLocalPersons,
  saveLocalPersons,
  loadPersons,
  findDefault,
};
