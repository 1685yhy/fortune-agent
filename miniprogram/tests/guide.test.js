// 易理明灯 E1+P3 — 首访功能导览 & 未建档提示条 纯逻辑 node 单测
// 运行：cd miniprogram && node --test tests/
// 覆盖（brief §测试与验证）：提示条四态判定 / 导览二选一标记契约 /
//   tour 标记与 onboard 标记键隔离（不破坏 app.js _maybeOnboard 判定）/
//   导览卡「下一步/开始使用」边界 / P3 seen-once 不再自动弹判定。
const test = require('node:test');
const assert = require('node:assert/strict');
const guide = require('../utils/guide');

const {
  TOUR_DONE_KEY,
  TOUR_SKIP_KEY,
  NOARCH_CLOSED_KEY,
  shouldShowNoarchTip,
  tourHasNext,
  shouldAutoShowTour,
} = guide;

/* ── 未建档提示条四态（E1-B 契约） ── */
test('提示条：无档案+未关闭 → 显示', () => {
  assert.equal(shouldShowNoarchTip(false, false), true);
});

test('提示条：无档案+已关闭（ylm_noarch_tip_closed=1）→ 不显示（关闭后本会话+后续启动都不显示）', () => {
  assert.equal(shouldShowNoarchTip(false, true), false);
});

test('提示条：有档案（重新建档成功）→ 不显示，且不再被关闭标记拦截（自动消失）', () => {
  assert.equal(shouldShowNoarchTip(true, false), false);
  assert.equal(shouldShowNoarchTip(true, true), false);
});

/* ── 导览标记契约（E1-A） ── */
test('导览标记二选一：done 与 skipped 是两个不同键（绝不双写同一键）', () => {
  assert.notEqual(TOUR_DONE_KEY, TOUR_SKIP_KEY);
});

test('导览标记与建档标记键隔离：不改动 ylm_onboard_* 判定（app.js _maybeOnboard 不读导览键）', () => {
  assert.notEqual(TOUR_DONE_KEY, 'ylm_onboard_done');
  assert.notEqual(TOUR_DONE_KEY, 'ylm_onboard_skipped');
  assert.notEqual(TOUR_SKIP_KEY, 'ylm_onboard_done');
  assert.notEqual(TOUR_SKIP_KEY, 'ylm_onboard_skipped');
  assert.notEqual(NOARCH_CLOSED_KEY, TOUR_DONE_KEY);
  assert.notEqual(NOARCH_CLOSED_KEY, TOUR_SKIP_KEY);
  assert.notEqual(NOARCH_CLOSED_KEY, 'ylm_onboard_done');
});

/* ── 导览卡边界（3 卡：排盘→今日→问明灯） ── */
test('tourHasNext：卡 1/卡 2 有下一步；卡 3（最后一张）→ 显示「开始使用」', () => {
  assert.equal(tourHasNext(0, 3), true);
  assert.equal(tourHasNext(1, 3), true);
  assert.equal(tourHasNext(2, 3), false);
});

/* ── P3 seen-once：看过一次不再自动弹 ── */
test('导览 seen-once：未看过（无标记）→ 自动弹出（done/skipped 页主按钮尾链导览）', () => {
  assert.equal(shouldAutoShowTour(false, false), true);
});

test('导览 seen-once：看完（ylm_tour_done=1）→ 不再自动弹', () => {
  assert.equal(shouldAutoShowTour(true, false), false);
});

test('导览 seen-once：关闭（ylm_tour_skipped=1）→ 不再自动弹', () => {
  assert.equal(shouldAutoShowTour(false, true), false);
});

test('导览 seen-once：双标记并存（防御，正常二选一）→ 不再自动弹', () => {
  assert.equal(shouldAutoShowTour(true, true), false);
});
