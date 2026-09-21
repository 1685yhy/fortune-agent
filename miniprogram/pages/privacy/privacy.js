// 隐私保护指引页面
//
// k74：本页 data 曾带 6 个**零引用**列表（noCollectList/protectList/useList/
// noUseList/rightsList/otherList）。它们既不被本页 wxml 渲染，也不被任何页面/测试
// 读取（零引用证据见 k74 报告）——但内容与事实相反且是陷阱：首项
// `noCollectList[0] = '手机号码'`（手机号实际是「主动绑定后 AES-256 落库」，
// `privacy.wxml` 与 `privacy.md` 均已如实披露），一旦有人把它接上渲染，
// "不收集手机号"会立刻回到用户眼前。故整块删除，页面文案唯一事实源 = `privacy.wxml`。
Page({
  data: {},

  onLoad() {
    wx.setNavigationBarTitle({
      title: '隐私保护指引',
    });
  },

  goBack() {
    wx.navigateBack();
  },

  onShareAppMessage() {
    return {
      title: '易理明灯 - 隐私保护指引',
    };
  },
});
