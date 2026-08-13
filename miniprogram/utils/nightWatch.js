/* 守夜人成就(方案·灯下漫谈)— 本地存储:连续 7 夜点亮「守夜人」印章,30 夜升「长明灯」。
   计夜规则:21:00-04:00 有 ≥1 次 ≥1 分钟对话计 1 夜;中断重计。
   调用点:聊天页深夜模式每发送一条消息 → nightWatch.touch(Date.now())。 */
const KEY = 'ylm_night_watch';
const TARGET = 7;
const LONG_TARGET = 30;

/* 北京时间日期串(YYYY-MM-DD):本地时钟 +8h 后取 UTC 日期 */
function bjDateStr(ts) {
  return new Date((ts || Date.now()) + 8 * 3600e3).toISOString().slice(0, 10);
}

function _load() {
  try { return wx.getStorageSync(KEY) || { log: [] }; } catch (e) { return { log: [] }; }
}
function _save(state) {
  try { wx.setStorageSync(KEY, state); } catch (e) { /* ignore */ }
}

/* 深夜对话发送消息时调用:登记当夜(首条/最后条时间戳/条数) */
function touch(ts) {
  const t = ts || Date.now();
  const state = _load();
  const date = bjDateStr(t);
  const log = (state.log || []).slice();
  let entry = log.find((e) => e.date === date);
  if (!entry) { entry = { date, firstTs: t, lastTs: t, msgs: 0 }; log.push(entry); }
  entry.lastTs = Math.max(entry.lastTs, t);
  entry.msgs += 1;
  log.sort((a, b) => (a.date < b.date ? -1 : 1));
  while (log.length > 90) log.shift();   // 防膨胀,最多 90 夜
  _save({ log });
  return entry;
}

/* 当夜是否计夜:≥1 条消息且持续 ≥1 分钟 */
function isActiveNight(entry) {
  return !!entry && entry.msgs >= 1 && entry.lastTs - entry.firstTs >= 60000;
}

/* 连续达标夜数:从当天往回数;今夜未达标不计(从昨夜继续),中断即停 */
function computeStreak(log, today) {
  const byDate = {};
  (log || []).forEach((e) => { byDate[e.date] = e; });
  const t = today || bjDateStr(Date.now());
  let streak = 0;
  let d = new Date(t + 'T00:00:00Z');
  for (let i = 0; i < 90; i++) {
    const key = d.toISOString().slice(0, 10);
    const e = byDate[key];
    if (i === 0 && !(e && isActiveNight(e))) {
      d = new Date(d.getTime() - 86400e3);   // 今夜未完成:跳过,从昨夜继续
      continue;
    }
    if (!(e && isActiveNight(e))) break;     // 中断重计
    streak += 1;
    d = new Date(d.getTime() - 86400e3);
  }
  return streak;
}

/* 成就总览:display = 第 X 夜(今夜进行中 +1);achieved = 点亮守夜人;longLit = 长明灯 */
function getState() {
  const log = _load().log || [];
  const tonight = log.find((e) => e.date === bjDateStr(Date.now()));
  const nightsToday = isActiveNight(tonight);
  const streak = computeStreak(log);
  const display = streak + (nightsToday ? 0 : 1);
  return {
    streak,
    display,
    target: TARGET,
    longTarget: LONG_TARGET,
    nightsToday,
    achieved: display >= TARGET,
    longLit: display >= LONG_TARGET,
  };
}

/* 近 n 夜网格:每夜 {date, done, pending} */
function recentNights(n) {
  const log = _load().log || [];
  const byDate = {};
  log.forEach((e) => { byDate[e.date] = e; });
  const out = [];
  let d = new Date(bjDateStr(Date.now()) + 'T00:00:00Z');
  for (let i = 0; i < n; i++) {
    const key = d.toISOString().slice(0, 10);
    const e = byDate[key];
    out.push({
      date: key.slice(5),
      done: !!(e && isActiveNight(e)),
      pending: !!e && !isActiveNight(e),
    });
    d = new Date(d.getTime() - 86400e3);
  }
  return out;
}

module.exports = { touch, isActiveNight, computeStreak, getState, recentNights, bjDateStr, TARGET, LONG_TARGET };
