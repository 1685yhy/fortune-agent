/* 深夜模式判定(方案·灯下漫谈)— 与后端 src/engines/night_mode.py 同规则。
   从晚安推送进入(entry='night')由页面短路本判定,以入口为准不校验时间。 */
const PRESETS = { early: [20, 23], standard: [21, 1], night: [22, 2] };
const PRESET_LABEL = {
  early: '早睡党 20:00-23:00',
  standard: '标准 21:00-01:00',
  night: '夜猫子 22:00-02:00',
};

function bjHour(ts) {
  return new Date((ts || Date.now()) + 8 * 3600e3).getUTCHours();
}

function isNightMode(preset, ts) {
  const w = PRESETS[preset] || PRESETS.standard;
  const h = bjHour(ts);
  if (w[1] > w[0]) return h >= w[0] && h < w[1];   // 同日窗(早睡党 20:00-23:00)
  return h >= w[0] || h < w[1];                     // 跨日窗(标准/夜猫子,次日 end 点前仍算)
}

function nightWindow(preset) {
  const w = PRESETS[preset] || PRESETS.standard;
  const p = (n) => String(n).padStart(2, '0');
  return `${p(w[0])}:00-${p(w[1])}:00`;
}

module.exports = { PRESETS, PRESET_LABEL, isNightMode, nightWindow, bjHour };
