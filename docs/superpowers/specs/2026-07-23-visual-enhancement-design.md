# Step 2: Miniprogram Visual Enhancement — Design Spec

**Goal**: Premium native-app feel: skeleton loading, score animations, smooth transitions, refined typography.

## 1. Create Shared Component: `components/skeleton/skeleton`

WeChat miniprogram doesn't support CSS `@keyframes` for skeleton shimmer, so use a WXS-based or conditional-class approach:

```xml
<!-- components/skeleton/skeleton.wxml -->
<view class="skeleton {{type}}" wx:if="{{loading}}">
  <view class="skeleton-line" wx:for="{{lines}}" wx:key="index" 
        style="width: {{item}}%; animation-delay: {{index * 0.1}}s;"></view>
</view>
<slot wx:else />
```

4 files: skeleton.wxml, skeleton.wxss, skeleton.js, skeleton.json

Apply skeleton loading to ALL pages (today, chat, reports, me, hehun, qimen, xingming, xuetang).

## 2. Score Animation Component: `components/score-ring/score-ring`

Animated score ring for hehun compatibility scores:
- Canvas-based circular progress that fills from 0 to score
- Number counter that counts up
- Color: red for 0-40, yellow 40-70, green 70-100

## 3. Page Transition Animations

Add to `app.wxss`:
```css
/* Slide-in from right for new pages */
.page-enter {
  animation: slideInRight 0.3s ease-out;
}
@keyframes slideInRight {
  from { transform: translateX(100%); opacity: 0; }
  to { transform: translateX(0); opacity: 1; }
}

/* Fade in for content */
.content-enter {
  animation: fadeIn 0.4s ease-out;
}
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}
```

Apply `.content-enter` to main content containers on each page.

## 4. Refined Micro-Interactions

- **Button press**: `transform: scale(0.97)` on `:active`
- **Card hover**: subtle `box-shadow` increase on touch
- **Tab bar**: smooth icon color transition
- **Input fields**: focus ring animation

## 5. Typography & Spacing Refinement

- Consistent line-height scale (1.4 body, 1.2 heading)
- Proper Chinese text spacing (letter-spacing: 0.5px for body)
- Section dividers with subtle gradient
- Card border-radius consistency (12px standard, 16px for hero cards)

## 6. Color & Gradient Polish

Add to `app.wxss`:
```css
/* Premium gradients */
.gradient-hero { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%); }
.gradient-gold { background: linear-gradient(135deg, #D4A843 0%, #C5923E 50%, #B88332 100%); }
.gradient-card { background: linear-gradient(180deg, #1E293B 0%, #1a2332 100%); }
```
