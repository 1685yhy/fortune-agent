// 易理明灯 — Skeleton Loading Component
Component({
  properties: {
    loading: {
      type: Boolean,
      value: true,
    },
    type: {
      type: String,
      value: 'text', // text | card | list
    },
    lines: {
      type: Array,
      value: [60, 90, 75, 50],
    },
    listCount: {
      type: Number,
      value: 4,
    },
  },
});
