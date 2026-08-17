/* 出生地选择 · 省/市级联 + 搜索（persons / hehun / bazi 共用）
 * - 省份左列 → 城市右列（两级联）
 * - 顶部搜索框：跨省过滤城市（如搜「广州」直达）
 * - 数据：utils/regions.js 全 34 省级行政区 + 主要城市
 * 契约：
 *   properties: value（存量城市串，兼容「省·市」/纯市名/自由文本）, placeholder
 *   event change: { province, city, full }（full = 「省·市」格式，未命中数据集时 full=原文） */
const regions = require('../../utils/regions');

Component({
  properties: {
    value: { type: String, value: '' },
    placeholder: { type: String, value: '请选择出生地' },
  },

  data: {
    show: false,
    provinces: [],
    provinceIdx: -1,
    cities: [],
    keyword: '',
    searchResults: [],
  },

  observers: {
    value() {
      this._parseCurrent();
    },
  },

  lifetimes: {
    attached() {
      this.setData({ provinces: regions.PROVINCES.map((p) => p.name) });
      this._parseCurrent();
    },
  },

  methods: {
    _parseCurrent() {
      const v = String(this.data.value || '').trim();
      if (!v) return;
      const parsed = regions.parseValue(v);
      if (parsed && parsed.matched) {
        const idx = regions.PROVINCES.findIndex((p) => p.name === parsed.province);
        this.setData({
          provinceIdx: idx,
          cities: (regions.PROVINCES[idx] || {}).cities || [],
        });
      }
    },

    onTap() {
      this.setData({
        show: true,
        keyword: '',
        searchResults: [],
        provinceIdx: this.data.provinceIdx >= 0 ? this.data.provinceIdx : 0,
        cities: this.data.provinceIdx >= 0
          ? this.data.cities
          : ((regions.PROVINCES[0] || {}).cities || []),
      });
    },

    close() {
      this.setData({ show: false });
    },

    noop() {},

    onProvinceTap(e) {
      const idx = parseInt(e.currentTarget.dataset.idx, 10);
      this.setData({
        provinceIdx: idx,
        cities: (regions.PROVINCES[idx] || {}).cities || [],
      });
    },

    onCityTap(e) {
      const city = e.currentTarget.dataset.city;
      const prov = regions.PROVINCES[this.data.provinceIdx] || {};
      this._choose(prov.name, city);
    },

    onSearchInput(e) {
      const keyword = e.detail.value;
      const results = keyword ? regions.search(keyword).slice(0, 80) : [];
      this.setData({ keyword, searchResults: results });
    },

    onSearchResultTap(e) {
      const idx = parseInt(e.currentTarget.dataset.idx, 10);
      const item = this.data.searchResults[idx];
      if (item) this._choose(item.province, item.city);
    },

    _choose(province, city) {
      if (!province || !city) return;
      this.setData({ show: false });
      this.triggerEvent('change', { province, city, full: province + '·' + city });
    },
  },
});
