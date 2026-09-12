// 易理明灯 E1 — 首访功能导览 & 未建档提示条：storage 标记契约 + 纯判定逻辑
//
// 本模块零 wx/小程序 API 依赖 → 可直接 node 单测（miniprogram/tests/guide.test.js，
// 运行：cd miniprogram && node --test tests/）。
//
// ── 标记契约 ──
// 1) 功能导览（onboarding 页 phase==='tour' 尾链，3 卡：排盘→今日→问明灯）：
//    ylm_tour_done / ylm_tour_skipped 二选一——
//    「开始使用」（第 3 卡看完）→ ylm_tour_done=1；「跳过」→ ylm_tour_skipped=1。
//    导览卡去向按钮（去排盘/去今日/去对话）不落标记：中途离开未做「完成/跳过」
//    决定，保持二选一不变量。
//    P3 seen-once（拍板）：看过一次（done/skipped 任一）后 done/skipped 页主按钮
//    不再尾链导览、直接进入 App（shouldAutoShowTour 判定，onboarding.js 读取
//    storage 调用）；显式「再看一遍导览」入口随时可重看（不落新标记，重看后
//    完成/跳过仍写原有二键）。
//    ⚠ 不影响现有 ylm_onboard_done / ylm_onboard_skipped 判定——app.js
//    _maybeOnboard 只读 onboard 两键，导览标记与其互不读写（见 guide.test.js
//    的键隔离断言），onboarding 不再自动弹出的逻辑保持不变。
// 2) 未建档提示条（today 页 + paipan 页共用）：ylm_noarch_tip_closed=1 —— 关闭后
//    本会话+后续启动都不显示；重新建档成功（本地有档案）自动消失。取舍：关闭标记在
//    「无档案」前提下永久生效（不按日重置——简单且不打扰）；一旦有档案，
//    提示条跟随「有无档案」走，已关闭的标记不再拦截（用户已见过并关过，
//    不重复打扰；将来删档后也不再弹出）。
//    k36 A32：该键**跨页生效**（today 页 E1 提示条 与 paipan 页未建档引导条同键）——
//    两页是同一句「还没建档」诉求，关一次处处不打扰。

const TOUR_DONE_KEY = 'ylm_tour_done';
const TOUR_SKIP_KEY = 'ylm_tour_skipped';
const NOARCH_CLOSED_KEY = 'ylm_noarch_tip_closed';

/* 未建档提示条是否显示：本地无档案 且 未被用户关闭 */
function shouldShowNoarchTip(hasArchive, tipClosed) {
  return !hasArchive && !tipClosed;
}

/* Q3（批次2 收尾）+ k36 A32：排盘页未建档引导条是否显示——未建档用户点「排盘」
   入口（E1 提示条 / onboarding 导览卡 / 测算页八字排盘卡）落地 paipan 建档表单页时，
   显示「还没建档」建档口径引导（E1 提示条同款文案）；已有档案 → 不显示。
   A32（k36 实现 / k34 补交互与用例）：可关闭——**复用今日页关闭键 NOARCH_CLOSED_KEY**
   （与原今日页「× 关闭后不再打扰」同一语义，两页共用一枚标记：任一页关过都不再弹，
   不发明新键/新交互）；「给他人排盘」场景同样受益（未建档用户对引导条已读即关，
   不必每次访问都现）。
   ⚠ 该键作用域 = 全局（跨页生效）：今日页关闭后本页引导条同样不再显示，反之亦然；
   将来若产品要按页拆分，需引入页级键并改本判定。
   纯判定，供 paipan.js onLoad/onShow 调用（node 单测见 paipan_noarch.test.js）。 */
function shouldShowPaipanNoarchGuide(hasArchive, tipClosed) {
  return !hasArchive && !tipClosed;
}

/* 导览卡是否还有下一张（tourIdx 非最后一张 → 显示「下一步」而非「开始使用」） */
function tourHasNext(idx, total) {
  return idx < total - 1;
}

/* P3 seen-once：看过一次（完成 ylm_tour_done 或关闭 ylm_tour_skipped）→
   不再自动弹出（done/skipped 页主按钮直接进入 App）；显式「再看一遍导览」
   入口不受此判定限制（onboarding.js replayTour 直接进 phase==='tour'） */
function shouldAutoShowTour(tourDone, tourSkipped) {
  return !tourDone && !tourSkipped;
}

module.exports = {
  TOUR_DONE_KEY,
  TOUR_SKIP_KEY,
  NOARCH_CLOSED_KEY,
  shouldShowNoarchTip,
  shouldShowPaipanNoarchGuide,
  tourHasNext,
  shouldAutoShowTour,
};
