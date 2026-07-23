// 易理明灯 — Animated Score Ring Component
Component({
  properties: {
    score: {
      type: Number,
      value: 0,
      observer: 'animateScore',
    },
    size: {
      type: Number,
      value: 200,
    },
    color: {
      type: String,
      value: '',
    },
    showLabel: {
      type: Boolean,
      value: true,
    },
  },

  data: {
    displayScore: 0,
    ringColor: '#D4A843',
  },

  lifetimes: {
    attached() {
      this._initColor();
    },
  },

  methods: {
    _initColor() {
      const color = this.properties.color || this._getAutoColor(this.properties.score);
      this.setData({ ringColor: color });
    },

    _getAutoColor(score) {
      if (score >= 80) return '#D4A843';
      if (score >= 60) return '#5B8C5A';
      if (score >= 40) return '#C2852A';
      return '#C2413D';
    },

    _drawRing(percent) {
      const query = this.createSelectorQuery();
      query.select('#scoreRingCanvas').fields({ node: true, size: true }).exec((res) => {
        if (!res[0] || !res[0].node) return;
        const canvas = res[0].node;
        const ctx = canvas.getContext('2d');
        const dpr = wx.getWindowInfo().pixelRatio;
        const size = this.properties.size;
        const s = size * dpr;
        canvas.width = s;
        canvas.height = s;
        ctx.scale(dpr, dpr);

        const cx = size / 2;
        const cy = size / 2;
        const r = size / 2 - 10;
        const lw = Math.max(4, size * 0.04);

        // Clear
        ctx.clearRect(0, 0, size, size);

        // Background ring
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.strokeStyle = '#1E293B';
        ctx.lineWidth = lw;
        ctx.lineCap = 'round';
        ctx.stroke();

        // Progress ring
        if (percent > 0) {
          const angle = (percent / 100) * Math.PI * 2 - Math.PI / 2;
          ctx.beginPath();
          ctx.arc(cx, cy, r, -Math.PI / 2, angle);
          ctx.strokeStyle = this.data.ringColor;
          ctx.lineWidth = lw;
          ctx.lineCap = 'round';
          ctx.stroke();

          // Glow effect
          ctx.shadowColor = this.data.ringColor;
          ctx.shadowBlur = 8;
          ctx.beginPath();
          ctx.arc(cx, cy, r, -Math.PI / 2, angle);
          ctx.strokeStyle = this.data.ringColor;
          ctx.lineWidth = lw * 0.3;
          ctx.lineCap = 'round';
          ctx.stroke();
          ctx.shadowBlur = 0;
        }
      });
    },

    animateScore(newScore) {
      const target = Math.min(100, Math.max(0, Math.round(newScore || 0)));
      const color = this.properties.color || this._getAutoColor(target);
      this.setData({ ringColor: color });

      // Skip animation if score is 0
      if (target === 0) {
        this.setData({ displayScore: 0 });
        this._drawRing(0);
        return;
      }

      // Animate counting
      let current = 0;
      const steps = Math.min(target, 30);
      const increment = Math.max(1, Math.floor(target / steps));
      const delay = Math.max(30, Math.floor(600 / steps));

      const timer = setInterval(() => {
        current += increment;
        if (current >= target) {
          current = target;
          clearInterval(timer);
        }
        this.setData({ displayScore: current });
        this._drawRing(current);
      }, delay);
    },

    // Public method for external trigger
    play(score) {
      this.animateScore(score || this.properties.score);
    },
  },
});
