// 易理明灯 — Empty State Component
const { MESSAGES } = require('../../utils/messages');

Component({
  properties: {
    type: {
      type: String,
      value: 'network', // network | empty | server | quota | unavailable
    },
    subtype: {
      type: String,
      value: '', // When type=empty: 'reports' | 'bazi' | 'chat'
    },
    showAction: {
      type: Boolean,
      value: true,
    },
    customTitle: {
      type: String,
      value: '',
    },
    customSubtitle: {
      type: String,
      value: '',
    },
  },

  data: {
    title: '',
    subtitle: '',
    action: '',
    detail: '',
    hints: [],
    hasAction: false,
  },

  observers: {
    'type,subtype,customTitle,customSubtitle': function () {
      this._updateContent();
    },
  },

  attached() {
    this._updateContent();
  },

  methods: {
    _updateContent() {
      const { type, subtype, customTitle, customSubtitle } = this.properties;
      const msg = MESSAGES[type];

      if (!msg) {
        this.setData({
          title: customTitle || '',
          subtitle: customSubtitle || '',
          hasAction: false,
          hints: [],
          action: '',
          detail: '',
        });
        return;
      }

      let title = customTitle || msg.title;
      let subtitle = customSubtitle || msg.subtitle;
      let action = msg.action || '';
      let detail = msg.detail || '';
      let hints = [];

      if (type === 'empty' && subtype) {
        const sub = msg[subtype];
        if (sub) {
          title = customTitle || sub.title;
          subtitle = customSubtitle || sub.subtitle;
          action = sub.action || action;
          detail = sub.detail || detail;
          hints = sub.hints || [];
        }
      }

      this.setData({
        title,
        subtitle,
        action,
        detail,
        hints,
        hasAction: this.properties.showAction && !!action,
      });
    },

    onAction() {
      this.triggerEvent('action', { type: this.properties.type, subtype: this.properties.subtype });
    },
  },
});
