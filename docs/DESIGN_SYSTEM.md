# Design System — 易理明灯

## Brand Identity

易理明灯 ("Yi Li Ming Deng") positions itself as a premium AI-assisted Chinese metaphysics platform. The visual identity evokes **luxury publishing** (The Economist, Monocle) and **fine watchmaking** (Patek Philippe, A. Lange & Sohne) — not tech dashboard or gaming.

The reader should feel like they are holding a silk-bound folio or a limited-edition magazine pull-out, not looking at a screen.

---

## Color Palette

### Primary
| Token    | Hex       | Role                         |
|----------|-----------|------------------------------|
| Primary  | `#1a1a1a` | Deep charcoal, not pure black. Headlines, body text, structural elements. |
| Accent   | `#c9a96e` | Warm gold, not bright yellow. Borders, highlights, decorative lines, key data. |
| Text     | `#e8e4dd` | Warm white, not pure white. Text on dark backgrounds. |
| TextDark | `#e8e4dd` | (same) Used in hero overlays on dark accents. |
| Secondary| `#8b8680` | Warm gray. Labels, secondary info, metadata, captions. |
| Muted    | `#b5b0a8` | Muted warm gray. Placeholder text, disabled states. |

### Semantic
| Token    | Hex       | Role                         |
|----------|-----------|------------------------------|
| Success  | `#7ab87a` | Muted jade. Auspicious indicators, positive trends, growth. |
| Warning  | `#c96a5e` | Terracotta. Caution, inauspicious signals, alerts. |
| Info     | `#6a9ec4` | Sapphire. Information, neutral data points, auxiliary info. |

### Backgrounds
| Token       | Hex       | Role                         |
|-------------|-----------|------------------------------|
| Paper       | `#faf8f5` | Warm paper white. Report backgrounds, page body. |
| Card        | `#ffffff` | Pure white. Card surfaces, modal backgrounds. |
| Surface     | `#f5f2ed` | Slightly darker warm. Hover states, subtle dividers. |
| Border      | `#e0ddd6` | Warm gray border. Card borders, dividers, input borders. |
| BorderLight | `#edeae4` | Subtle border. Table rows, light dividers. |

### Element Colors (五行)
| Element | Hex       | Visual Reference    |
|---------|-----------|---------------------|
| 金 Metal| `#dedad1` | Platinum / silver   |
| 木 Wood | `#7ab87a` | Jade / muted green  |
| 水 Water| `#6a9ec4` | Sapphire / muted blue|
| 火 Fire | `#c96a5e` | Terracotta / muted red|
| 土 Earth| `#c9a96e` | Gold / ochre         |

These are deliberately **toned, not neon** — they sit comfortably next to long-form text.

---

## Typography

### Font Stack
```css
/* Display / Headlines */
font-family: "Noto Serif SC", "Source Han Serif SC", serif;

/* Body text */
font-family: "PingFang SC", "Microsoft YaHei", "Hiragino Sans GB", sans-serif;

/* Numbers / Data / Mono */
font-family: "JetBrains Mono", "Source Code Pro", monospace;
```

### Type Scale (reports)
| Token   | Size  | Weight | Line H | Use                     |
|---------|-------|--------|--------|-------------------------|
| h1      | 28px  | 700    | 1.2    | Section titles          |
| h2      | 20px  | 600    | 1.3    | Chart titles            |
| h3      | 16px  | 600    | 1.4    | Card headers            |
| body    | 14px  | 400    | 1.7    | Paragraphs              |
| small   | 12px  | 400    | 1.5    | Captions, metadata      |
| tiny    | 10px  | 400    | 1.4    | Footnotes, disclaimers  |
| display | 48px  | 700    | 1.0    | Hero numbers (日主)     |

### Letter Spacing
- Uppercase labels: `letter-spacing: 0.15em` to `0.3em`
- Body text: normal
- Small caps / decorative: `letter-spacing: 0.1em`

---

## Spacing System

Base unit: **8px grid**

| Token | PX  | Use                          |
|-------|-----|------------------------------|
| xs    | 4   | Tight, inline gaps           |
| sm    | 8   | Between related items        |
| md    | 16  | Between sections, card padding|
| lg    | 24  | Between major sections       |
| xl    | 32  | Page padding, hero padding   |
| 2xl   | 48  | Wide margins                 |

Card internal padding: 24px (desktop), 16px (mobile).

---

## Card Design

Cards are the primary structural unit in reports:

```css
.card {
  background: #ffffff;
  border: 1px solid #e0ddd6;
  border-radius: 12px;
  padding: 24px;
  box-shadow:
    0 1px 3px rgba(0,0,0,0.04),
    0 4px 12px rgba(0,0,0,0.06);
}
```

### Card Variants
- **Hero card**: Larger padding (32px), optional top gold accent line (`1px solid #c9a96e` at 30% width)
- **Pillar card**: 3-column grid card, 16px padding, subtle top gold line decoration
- **Mini card**: 12px padding, used for tags and compact data

### Dividers
```css
.divider {
  height: 1px;
  background: linear-gradient(
    90deg,
    transparent,
    rgba(201,169,110,0.25) 20%,
    rgba(201,169,110,0.25) 80%,
    transparent
  );
}
```

---

## Chart Design Principles

1. **Light foundation**: All charts use `#faf8f5` body background. This is warm paper, not tech-dashboard dark.
2. **Gold accents**: Primary decorative element is warm gold (`#c9a96e`). Used for titles, borders, key data highlights, and timelines.
3. **Typography hierarchy**: Each chart has a clear visual hierarchy — hero element (日主) is largest, then section titles, then data.
4. **White cards**: Data sections are white cards with subtle gray borders and soft shadows.
5. **Minimalist**: No heavy gradients, no neon glows, no dark overlays. Clean lines, ample whitespace.
6. **Footer**: Every chart carries "易理明灯 · 以古籍为灯 照命理之路" in muted warm gray.

---

## Share Card Spec (1200x630)

Social share cards follow the same brand system:

- **Size**: 1200x630 px (OG standard)
- **Background**: White with subtle paper texture feel
- **Gold accent**: Top decorative border (4px solid #c9a96e)
- **Title**: "易理明灯" in Noto Serif SC, gold, centered
- **Content area**: Bazi four pillars, day master (large), pattern
- **QR placeholder**: Bottom-right, 80x80 box with gold dashed border
- **Typography**: Follow report type scale, with hero day master at 48-56px
