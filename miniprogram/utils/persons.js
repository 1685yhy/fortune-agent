// 易理明灯 — 多人命主档案 · 共享工具（onboarding / persons / bazi 三页共用）
// 契约字段（后端并行实现中，已定稿）：
//   person = { id, name, relation, gender, birth_year, birth_month, birth_day,
//              birth_hour, birth_minute, calendar, city, is_default, created_at }
//   gender: '男' | '女' | 'unknown'；birth_hour: 时辰代表整点（23/1/3/…/21，
//   只知时辰的表单行）或真实时钟小时 0-23（钟表时间/对话解析行，k19 起分钟
//   档必为时钟语义，如 10:55）；birth_minute: 0-59（0=未提供分钟）。
//   calendar: 'solar'|'lunar'
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

/* k19 分钟精度：钟表时间选择器列（0-23 时 / 0-59 分）与时辰窗口映射。
   时辰窗口（前端 picker 标签口径，起点与 utils HOUR_VALUES 一致）：
   子[23,1) 丑[1,3) 寅[3,5) 卯[5,7) 辰[7,9) 巳[9,11) 午[11,13) 未[13,15)
   申[15,17) 酉[17,19) 戌[19,21) 亥[21,23) → 序号 0-11 */
const HOUR24 = [];
const MINUTE60 = [];
for (let _h = 0; _h < 24; _h++) HOUR24.push(`${_h}时`);
for (let _m = 0; _m < 60; _m++) MINUTE60.push(`${_m}分`);
const SHICHEN_WINDOWS = [  // [起点小时, 终点小时(不含), 时辰序号]
  [23, 1, 0], [1, 3, 1], [3, 5, 2], [5, 7, 3], [7, 9, 4], [9, 11, 5],
  [11, 13, 6], [13, 15, 7], [15, 17, 8], [17, 19, 9], [19, 21, 10],
  [21, 23, 11],
];

/** 时钟小时 0-23 → 时辰序号 0-11（子=0 … 亥=11）；非法 → 0 */
function shichenIndexFromClockHour(h) {
  const v = parseInt(h, 10);
  if (Number.isNaN(v) || v < 0 || v > 23) return 0;
  for (let i = 0; i < SHICHEN_WINDOWS.length; i++) {
    const w = SHICHEN_WINDOWS[i];
    if (v >= w[0] && v < w[1]) return w[2];
    if (w[0] === 23 && (v === 23 || v === 0)) return 0; // 子时跨日
  }
  return 0;
}

/** 出生小时（+可选分钟）→ 时辰序号 0-11。

    画像口径（k19 归一）：birth_hour 只存在两形态——时辰代表整点
    （HOUR_VALUES 奇数集，表单「只知时辰」写入）与真实时钟小时 0-23
    （对话解析/精确表单写入，如 10:55）。读回一律按此判定：
    - HOUR_VALUES 代表整点（1/3/…/23）→ 序号查表（起点小时必属本时辰，
      与时钟窗口同义，两形态重合）；
    - 其余 0-23（含 minute>0 行与 12-22、整点时钟行）→ 时钟窗口映射。
      修旧缺陷：10 曾被当旧序号 10=戌时 显示（实为巳时）；12-22 曾被
      漏判落到 子时；12:00 曾显 子时。
    无法识别 → 0（子时）。
    注：P2 前的「旧序号 0-11」直存形态（约 2026-08 前，写路径早已绝迹）
    随本口径按时钟小时读回——属既有近似残余，见 k19 plan 留档。 */
function hourToShichenIndex(hour, minute) {
  const h = parseInt(hour, 10);
  if (Number.isNaN(h) || h < 0) return 0;
  const repIdx = HOUR_VALUES.indexOf(h);
  if (repIdx !== -1) return repIdx;          // 代表整点（含晚子时 23）
  return shichenIndexFromClockHour(h);       // 时钟小时（分钟/12-22/整点行）
}

/** 时辰序号 → 代表整点（存档用） */
function shichenIndexToHour(idx) {
  return HOUR_VALUES[Math.max(0, Math.min(11, parseInt(idx, 10) || 0))];
}

/** 时辰序号 → 中文（子时/丑时…） */
function shichenCN(idx) {
  return HOUR_CN[Math.max(0, Math.min(11, parseInt(idx, 10) || 0))] || '';
}

/** 性别（G1 契约统一，2026-08-29）：单一中文契约 '男'|'女'|'unknown'。
    展示层兼容历史存量 male/female；未知 → '未知'（不再默认显示成男）。 */
function genderCN(g) {
  if (g === '女' || g === 'female') return '女';
  if (g === '男' || g === 'male') return '男';
  return '未知';
}
/** 展示 '男'|'女' → 中文契约 '男'|'女'；其余（含历史英文、空、未定义）→ 'unknown' */
function genderCode(g) {
  return g === '女' ? '女' : (g === '男' ? '男' : 'unknown');
}

/** 出生时刻显示文本：时辰 + （有精确分钟时附钟表时间 10:55）。 */
function timeText(p) {
  if (!p) return '';
  const h = p.birth_hour;
  if (h === undefined || h === null || h === '') return '';
  const idx = hourToShichenIndex(h, p.birth_minute);
  const base = shichenCN(idx);
  const m = parseInt(p.birth_minute, 10);
  if (!Number.isNaN(m) && m > 0) {
    // 精确钟表行：分钟>0 必为时钟小时语义 → 直接拼 HH:MM
    return `${base} ${h}:${String(m).padStart(2, '0')}`;
  }
  return base;
}

/** 历法标记：'lunar' → 农历，其余（含缺失/历史存量）→ 公历。
 *  birthSummary / birthBrief 共用同一判定，禁止各自内联 —— 否则两处文案会对同一
 *  档案给出互相矛盾的历法口径（k70-F2 即「简版」缺标记被读成公历）。 */
function calLabel(p) {
  return (p && p.calendar === 'lunar') ? '农历' : '公历';
}

/** 生辰摘要（原型 birthLine / prof-birth）：公历 1998年5月12日 卯时 女 · 北京 */
function birthSummary(p) {
  if (!p) return '';
  const tt = timeText(p);
  const shi = tt ? tt + ' ' : '';
  return `${calLabel(p)} ${p.birth_year}年${p.birth_month}月${p.birth_day}日 ${shi}${genderCN(p.gender)} · ${p.city || '未填出生地'}`;
}

/** 列表页摘要（原型 prof-birth 简版）：农历 1998 年 5 月 12 日 · 卯时 · 女 · 北京。
 *  k70-F2：该行是**原样回显**（不做换算），但年/月/日数字本身不带历法信息 —— 不标
 *  「农历/公历」会被读成公历（实测 1999 年 3 月 28 日 的农历档被读作公历 3 月 28 日）。
 *  「原样回显」契约不变，只补标记（与 birthSummary 同源 calLabel）。 */
function birthBrief(p) {
  if (!p) return '';
  const tt = timeText(p);
  const shi = tt ? tt + ' · ' : '';
  return `${calLabel(p)} ${p.birth_year} 年 ${p.birth_month} 月 ${p.birth_day} 日 · ${shi}${genderCN(p.gender)} · ${p.city || '未填'}`;
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

/** local_ 前缀条目 → 服务端创建载荷（G1 性别中文契约：与后端 _person_birth 归一一致——
    male/female 历史存量 → 男/女，中文原样，其余 → 'unknown'；绝不产出 male/female） */
function payloadOf(p) {
  const gl = String(p.gender || '').trim().toLowerCase();
  const gender = (gl === '男' || gl === 'male') ? '男' : ((gl === '女' || gl === 'female') ? '女' : 'unknown');
  return {
    name: p.name || '',
    relation: p.relation || '',
    gender,
    birth_year: p.birth_year,
    birth_month: p.birth_month,
    birth_day: p.birth_day,
    birth_hour: p.birth_hour,
    birth_minute: p.birth_minute,
    calendar: p.calendar || 'solar',
    city: p.city || '',
  };
}

/** 全量拉取：接口优先 → 失败降级本地缓存（永不 reject）。
    G3 H-6：服务端成功时不再直接覆盖本地缓存 —— 本地未同步条目（id 以 local_ 开头，
    离线建档/云端失败残留）合并进结果并逐条 upsert 到服务端（POST /api/persons，
    G2 D2 以服务端 person 确认响应为成功）；同步成功即从缓存移除（服务端列表已含，
    下次不重复建）；同步失败保留缓存与展示，等待下次重试。缓存写入按 id 幂等。 */
function loadPersons() {
  const api = require('./api');
  return api.getPersons()
    .then((res) => {
      const server = (res && res.persons) || [];
      const local = getLocalPersons();
      const localOnly = local.filter((p) => p && String(p.id || '').indexOf('local_') === 0);
      if (!localOnly.length) {
        saveLocalPersons(server);
        return server;
      }
      return Promise.all(localOnly.map((p) =>
        api.createPerson(payloadOf(p)).then(() => null).catch(() => p)
      )).then((kept) => {
        const keptList = kept.filter(Boolean);
        const merged = server.concat(keptList);
        saveLocalPersons(merged);
        return merged;
      });
    })
    .catch(() => getLocalPersons());
}

/** 取默认命主（列表可能来自本地缓存） */
function findDefault(list) {
  if (!Array.isArray(list)) return null;
  return list.find((p) => !!p.is_default) || null;
}

/** 本地是否已有命主档案（persons / 旧对话档案 / 登录带回 bazi 任一存在即算）。
    今日页/详解页「未设置命主信息 → 去设置」提示判定用：后端通用日历缓存 6h
    未刷新时，本地已建档即不再提示。 */
function hasLocalArchive() {
  try {
    const persons = wx.getStorageSync(LOCAL_KEY);
    if (Array.isArray(persons) && persons.length) return true;
    const arch = wx.getStorageSync('ylm_chat_archives');
    if (Array.isArray(arch) && arch.length) return true;
    const app = getApp();
    if (app && app.globalData && app.globalData.hasBazi && app.globalData.baziInfo) return true;
  } catch (e) { /* ignore */ }
  return false;
}

module.exports = {
  RELATIONS,
  HOUR_LABELS,
  HOUR_VALUES,
  HOUR_CN,
  HOUR24,
  MINUTE60,
  LOCAL_KEY,
  hasLocalArchive,
  hourToShichenIndex,
  shichenIndexFromClockHour,
  shichenIndexToHour,
  shichenCN,
  genderCN,
  genderCode,
  birthSummary,
  birthBrief,
  timeText,
  sealChar,
  getLocalPersons,
  saveLocalPersons,
  loadPersons,
  findDefault,
};
