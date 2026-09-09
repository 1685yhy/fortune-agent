#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
k12 · 对话页图标重绘生成器（P4.7 执行批）+ k14 · tab/导航图标族（C1/C2 收口）
==============================================================================
单一事实源 = 本文件内嵌的 96u 几何 + 颜色 token。生成管线：
  SVG(96u) → cairosvg 4x 超采样(768px) → PIL LANCZOS 降采样 → 目标档 RGBA png。

风格规范（docs/superpowers/plans/2026-09-08-k12-icon-redraw.md §1；
k14 追加 docs/superpowers/plans/2026-09-09-k14-tab-icons.md §1）：
  浅色细线条、圆头端点、常态中灰 #756E63、点亮 #A93A2C(--acc)、
  删除 #8C2E22(--acc-deep)、发送钮反白 #FAF5E7。
  主轮廓 7.0u@96（26px 菜单位≈1.9px CSS）；内细节 5.6u。

k14 节：tabbar 双态（lantern/book2/seal + on，chat-on 复用 k12 geo_chat 换 ACC）
+ legacy 淡棕带（bell/moon/book）+ 导航族（back/chev/kebab）入族，13 枚全 96px。

用法：
  python3 scripts/gen_k12_icons.py            # 写入 miniprogram/assets/images/（按既有档位）
  python3 scripts/gen_k12_icons.py --qa DIR   # 出 QA 拼贴（真实显示档+宣纸底/朱砂底），不写资产
依赖：python3 + cairosvg + Pillow

造型出处：ic-up/up-on/down/down-on/speak/share/mic/check（k12 八枚）与
k14 五枚 ic-bell/moon/book/back/chev 的路径几何取自 Lucide 图标集
（ISC License, https://lucide.dev）对应基础符号，并按本族笔画/配色规范
（灰 #756E63、点亮 #A93A2C）着色；其余为本脚本自绘几何。
"""
import math, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, 'miniprogram', 'assets', 'images')

GRAY = '#756E63'    # 常态 中灰
ACC = '#A93A2C'     # 点亮 = var(--acc)
DEEP = '#8C2E22'    # 危险(删除) = var(--acc-deep)
PAPER = '#FAF5E7'   # 发送钮反白

SIZE = {  # 每文件名目标物理尺寸（保持既有档位；ic-regen 新增=96）
    'ic-up': 192, 'ic-up-on': 192, 'ic-down': 192, 'ic-down-on': 192,
    'ic-keep': 192, 'ic-keep-on': 192,
    'ic-reaction': 96, 'ic-copy': 96, 'ic-speak': 96, 'ic-share': 96,
    'ic-edit': 96, 'ic-check': 96, 'ic-chat': 96, 'ic-delete': 96,
    'ic-link': 96, 'ic-mic': 96, 'ic-keyboard': 96, 'ic-camera': 96,
    'ic-camera-soft': 96, 'ic-plus': 96, 'ic-send': 96, 'ic-regen': 96,
    # k14 · tab/导航图标族（沿用既有 96 档位）
    'ic-lantern': 96, 'ic-lantern-on': 96, 'ic-book2': 96, 'ic-book2-on': 96,
    'ic-seal': 96, 'ic-seal-on': 96, 'ic-chat-on': 96,
    'ic-bell': 96, 'ic-moon': 96, 'ic-book': 96, 'ic-back': 96,
    'ic-chev': 96, 'ic-kebab': 96,
}

S = 7.0    # 主轮廓笔划（96u 空间）
D = 5.6    # 内细节笔划


# ── 图元：('P', d, w) 描边路径 ; ('C', cx,cy,r, w) 描边圆 ; ('F', cx,cy,r) 实心圆
#         ('PF', d, w) 实心填充(色=icon色, 细边同色防锯齿) ; ('PC', d, w, hex) 自定色描边
def P(d, w):
    return ('P', d, w)


def C(cx, cy, r, w):
    return ('C', (cx, cy, r), w)


def F(cx, cy, r):
    return ('F', (cx, cy, r))


def rr(x, y, w, h, r):
    return (f'M{x+r},{y} H{x+w-r} A{r},{r} 0 0 1 {x+w},{y+r} V{y+h-r} '
            f'A{r},{r} 0 0 1 {x+w-r},{y+h} H{x+r} '
            f'A{r},{r} 0 0 1 {x},{y+h-r} V{y+r} A{r},{r} 0 0 1 {x+r},{y} Z')


def pt(cx, cy, r, a_deg):
    a = math.radians(a_deg)
    return (cx + r * math.cos(a), cy + r * math.sin(a))


def arc_path(cx, cy, r, a0, a1, sweep=1):
    (x0, y0), (x1, y1) = pt(cx, cy, r, a0), pt(cx, cy, r, a1 % 360)
    large = 1 if (a1 - a0) > 180 else 0
    return f'M{x0:.1f},{y0:.1f} A{r},{r} 0 {large} {sweep} {x1:.1f},{y1:.1f}'


def star_pts(cx, cy, R, r, rot=-90.0):
    pts = []
    for i in range(10):
        pts.append(pt(cx, cy, R if i % 2 == 0 else r, rot + i * 36))
    return ' '.join(f'{p[0]:.1f},{p[1]:.1f}' for p in pts)


# ── 几何 ──────────────────────────────────────────────────────────────────
# 「通用符号直觉」优先采用 Lucide(ISC, 出处见脚本头注)经久验证的标准造型路径，
# 以 <g transform="scale(4)"> 内嵌(24 格 → 96 空间)；笔画在 24 空间按比例取：
#   主轮廓 7/4=1.75、内细节 5.6/4=1.4
# 'L' 图元 = 接受颜色的内嵌片段工厂。

def L(frag):
    return ('L', frag)


LUCIDE_MAIN = 1.75   # = S/4
LUCIDE_DET = 1.4     # = D/4


def lv_up(color):
    return (f'<path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4'
            f'a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z" '
            f'fill="none" stroke="{color}" stroke-width="{LUCIDE_MAIN}" stroke-linejoin="round"/>'
            f'<path d="M7 10v12" fill="none" stroke="{color}" stroke-width="{LUCIDE_DET}" '
            f'stroke-linecap="round"/>')


def lv_down(color):
    return (f'<path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20'
            f'a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z" '
            f'fill="none" stroke="{color}" stroke-width="{LUCIDE_MAIN}" stroke-linejoin="round"/>'
            f'<path d="M17 14V2" fill="none" stroke="{color}" stroke-width="{LUCIDE_DET}" '
            f'stroke-linecap="round"/>')


def lv_speak(color):
    return (f'<path d="M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3'
            f'a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 '
            f'11 19.298Z" fill="none" stroke="{color}" stroke-width="{LUCIDE_MAIN}" '
            f'stroke-linejoin="round"/>'
            f'<path d="M16 9a5 5 0 0 1 0 6" fill="none" stroke="{color}" '
            f'stroke-width="{LUCIDE_MAIN}" stroke-linecap="round"/>'
            f'<path d="M19.364 18.364a9 9 0 0 0 0-12.728" fill="none" stroke="{color}" '
            f'stroke-width="{LUCIDE_MAIN}" stroke-linecap="round"/>')


def lv_share(color):
    return (f'<circle cx="18" cy="5" r="3" fill="none" stroke="{color}" stroke-width="{LUCIDE_MAIN}"/>'
            f'<circle cx="6" cy="12" r="3" fill="none" stroke="{color}" stroke-width="{LUCIDE_MAIN}"/>'
            f'<circle cx="18" cy="19" r="3" fill="none" stroke="{color}" stroke-width="{LUCIDE_MAIN}"/>'
            f'<line x1="8.59" y1="13.51" x2="15.42" y2="17.49" stroke="{color}" '
            f'stroke-width="{LUCIDE_DET}" stroke-linecap="round"/>'
            f'<line x1="15.41" y1="6.51" x2="8.59" y2="10.49" stroke="{color}" '
            f'stroke-width="{LUCIDE_DET}" stroke-linecap="round"/>')


def lv_mic(color):
    return (f'<rect x="9" y="2" width="6" height="13" rx="3" fill="none" stroke="{color}" '
            f'stroke-width="{LUCIDE_MAIN}"/>'
            f'<path d="M19 10v2a7 7 0 0 1-14 0v-2" fill="none" stroke="{color}" '
            f'stroke-width="{LUCIDE_MAIN}" stroke-linecap="round"/>'
            f'<path d="M12 19v3" fill="none" stroke="{color}" stroke-width="{LUCIDE_MAIN}" '
            f'stroke-linecap="round"/>')


def lv_check(color):
    return (f'<path d="M21 10.656V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h12.344" '
            f'fill="none" stroke="{color}" stroke-width="{LUCIDE_MAIN}" stroke-linecap="round" '
            f'stroke-linejoin="round"/>'
            f'<path d="m9 11 3 3L22 4" fill="none" stroke="{color}" stroke-width="{LUCIDE_DET}" '
            f'stroke-linecap="round" stroke-linejoin="round"/>')


# ── k14 · tab/导航族 Lucide 标准符号（ISC，出处见脚本头注；24 格 → 96 空间） ──

def lv_bell(color):
    # lucide bell：钟身 + 铃舌短弧
    return (f'<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" fill="none" '
            f'stroke="{color}" stroke-width="{LUCIDE_MAIN}" stroke-linejoin="round"/>'
            f'<path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" fill="none" '
            f'stroke="{color}" stroke-width="{LUCIDE_MAIN}" stroke-linecap="round"/>')


def lv_moon(color):
    # lucide moon 弯月 + 右上四芒星点（自绘，45° 十字读作星光而非加号）
    return (f'<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" fill="none" '
            f'stroke="{color}" stroke-width="{LUCIDE_MAIN}" stroke-linejoin="round"/>'
            f'<path d="M19.13 3.33 L21.67 5.87 M21.67 3.33 L19.13 5.87" stroke="{color}" '
            f'stroke-width="{LUCIDE_DET}" stroke-linecap="round"/>')


def lv_book_open(color):
    # lucide book-open：双翼展开书
    return (f'<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" fill="none" '
            f'stroke="{color}" stroke-width="{LUCIDE_MAIN}" stroke-linejoin="round"/>'
            f'<path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" fill="none" '
            f'stroke="{color}" stroke-width="{LUCIDE_MAIN}" stroke-linejoin="round"/>')


def lv_arrow_left(color):
    # lucide arrow-left：导航返回
    return (f'<path d="m12 19-7-7 7-7" fill="none" stroke="{color}" '
            f'stroke-width="{LUCIDE_MAIN}" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<path d="M19 12H5" fill="none" stroke="{color}" '
            f'stroke-width="{LUCIDE_MAIN}" stroke-linecap="round"/>')


def lv_chevron_right(color):
    # lucide chevron-right：行尾箭头
    return (f'<path d="m9 18 6-6-6-6" fill="none" stroke="{color}" '
            f'stroke-width="{LUCIDE_MAIN}" stroke-linecap="round" stroke-linejoin="round"/>')


LUCIDE = {'ic-up': lv_up, 'ic-up-on': lv_up, 'ic-down': lv_down, 'ic-down-on': lv_down,
          'ic-speak': lv_speak, 'ic-share': lv_share, 'ic-mic': lv_mic, 'ic-check': lv_check,
          # k14 · tab/导航族
          'ic-bell': lv_bell, 'ic-moon': lv_moon, 'ic-book': lv_book_open,
          'ic-back': lv_arrow_left, 'ic-chev': lv_chevron_right}


def geo_keep():
    return [P(f'M{star_pts(48, 49, 28, 12.5)} Z', S)]


def geo_reaction():
    return [C(48, 49, 21, S),
            F(41, 42, 2.8), F(55, 42, 2.8),
            P('M36,54 Q48,66 60,54', D)]


def geo_copy():
    return [P(rr(34, 34, 36, 36, 9), S),       # 前块（下右）
            P('M26 58 H22 V22 H58 V26', S)]    # 后块露缘（上+左+两短尾）


def geo_edit():
    return [P('M25.3,36.7 L36.7,25.3 L63.7,52.3 L76,76 L52.3,63.7 Z', S),
            P('M34,34 L56,56', D)]


def geo_chat():
    return [P(rr(26, 26, 44, 36, 13), S),
            P('M38,60 L33,72 L47,60', D),
            F(38.5, 44, 3.2), F(48, 44, 3.2), F(57.5, 44, 3.2)]


def geo_delete():
    return [P('M38,34 A9,9 0 0 1 58,34', D),      # 提手弧
            P('M28,40 H68', D),                    # 盖沿
            P(rr(33, 46, 30, 30, 6), S),           # 桶身
            P('M43.5,52 V70', D), P('M52.5,52 V70', D)]


def geo_link():
    return [P('M32,68 H24 A20,20 0 0 1 24,28 H32', S),
            P('M64,28 H72 A20,20 0 0 1 72,68 H64', S),
            P('M28,48 H60', D)]


def geo_keyboard():
    out = [P(rr(20, 26, 56, 42, 9), S)]
    for x in (32, 48, 64):
        for y in (37, 47, 57):
            out.append(F(x, y, 3))
    return out


def geo_camera(soft=False):
    # 机身+顶凸一体化 outline（无交叉节）
    body = ('M36,40 L36,34 Q36,30 40,30 L56,30 Q60,30 60,34 L60,40 '
            'H65 Q74,40 74,49 V61 Q74,70 65,70 H31 Q22,70 22,61 V49 Q22,40 31,40 Z')
    out = [P(body, S), C(48, 55, 11.5, S)]
    if not soft:
        out.append(F(66, 47, 3))
    return out


def geo_plus():
    return [P('M48,28 V68', S + 1),
            P('M28,48 H68', S + 1)]


def geo_send():
    # 实心纸飞机(纸白填充) + 折痕用钮底色(朱砂)细线切开——17px 仍可辨
    return [('PF', 'M88,8 L60,88 L44,52 L8,36 Z', 0),
            ('PC', 'M88,8 L44,52', 3.2, ACC)]


def geo_regen():
    out = [P(arc_path(48, 50, 24, 295, 245 + 360), S)]
    end = pt(48, 50, 24, 245)
    vx, vy = -math.sin(math.radians(245)), math.cos(math.radians(245))  # 弧端切向(顺时针续行)
    tx, ty = end[0] + vx * 11, end[1] + vy * 11
    px, py = -vy, vx
    b1 = (end[0] + px * 5, end[1] + py * 5)
    b2 = (end[0] - px * 5, end[1] - py * 5)
    out.append(P(f'M{b2[0]:.1f},{b2[1]:.1f} L{tx:.1f},{ty:.1f} L{b1[0]:.1f},{b1[1]:.1f}', S))
    return out


# ── k14 · tab/导航族自绘几何（灯笼/封皮书/印章/竖三点；保持既有语义） ──────────

def geo_lantern():
    # 竖灯笼：顶挂柱 + 冬瓜形鼓身 + 穗。轮廓按旧 ic-lantern 逐行实测拟合：
    # 挂柱 x45-50 y12-19 → 口沿(顶缘)宽36 y20 → 肩扩 46 → 直腰宽50（y40-57）→
    # 收口 ~32 y76 → 穗结 y84-89 → 穗尖 y90-92；全高 y5..92.4 守 ≥3.5 边距
    out = [P('M48,13 L48,21.5', D),               # 顶挂柱（入上口沿）
           P('M33,23.5 C29.5,29 27,34 27,40 L27,57 C27.5,66 31,73 36.5,78 '
             'Q48,82.5 59.5,78 C65,73 68.5,66 69,57 L69,40 C69,34 66.5,29 63,23.5 Z', S),  # 灯身
           P('M48,81.5 L48,84', D),               # 穗绳
           F(48, 86.2, 3.2),                      # 穗结
           P('M46.1,90.2 L48,87.8 M49.9,90.2 L48,87.8', D)]  # 穗尖（V 形，至 y93 同旧）
    return out


def geo_book2():
    # 测算 = 封皮书（封面圆角+中缝+右上书签，旧 ic-book2 语义「书+小签」）
    return [P(rr(26, 32, 44, 44, 8), S),          # 封面 x26..70 y32..76
            P('M48,32 V76', D),                   # 中缝
            P('M34,46 L42,46', D), P('M34,56 L42,56', D),   # 左页两道细线
            P('M54,46 L62,46', D), P('M54,56 L62,56', D),   # 右页两道细线
            P(rr(55, 19, 15, 16, 4), S)]          # 右上书签（贴于封面上缘）


def geo_seal():
    # 我的 = 方印（圆角外框+内印文框+十字四宫，旧 ic-seal 语义）
    return [P(rr(27, 27, 42, 42, 9), S),          # 印体
            P(rr(40, 40, 16, 16, 3.5), D),        # 印文内框
            P('M40,48 H56', D), P('M48,40 V56', D)]  # 十字格 → 四宫印文


def geo_kebab():
    # 竖三点（导航右钮/行删除钮/设置行，旧 ic-kebab 语义）
    return [F(48, 20, 3.6), F(48, 48, 3.6), F(48, 76, 3.6)]


SPEC = {  # name -> (几何, 颜色)
    'ic-up': (None, GRAY), 'ic-up-on': (None, ACC),
    'ic-down': (None, GRAY), 'ic-down-on': (None, ACC),
    'ic-keep': (geo_keep, GRAY), 'ic-keep-on': (geo_keep, ACC),
    'ic-reaction': (geo_reaction, GRAY), 'ic-copy': (geo_copy, GRAY),
    'ic-speak': (None, GRAY), 'ic-share': (None, GRAY),
    'ic-edit': (geo_edit, GRAY), 'ic-check': (None, GRAY),
    'ic-chat': (geo_chat, GRAY), 'ic-delete': (geo_delete, DEEP),
    'ic-link': (geo_link, GRAY), 'ic-mic': (None, GRAY),
    'ic-keyboard': (geo_keyboard, GRAY),
    'ic-camera': (geo_camera, GRAY), 'ic-camera-soft': (lambda: geo_camera(True), GRAY),
    'ic-plus': (geo_plus, GRAY), 'ic-send': (geo_send, PAPER),
    'ic-regen': (geo_regen, GRAY),
    # k14 · tab/导航图标族（13 枚入族：同形两态 on 仅换色）
    'ic-lantern': (geo_lantern, GRAY), 'ic-lantern-on': (geo_lantern, ACC),
    'ic-book2': (geo_book2, GRAY), 'ic-book2-on': (geo_book2, ACC),
    'ic-seal': (geo_seal, GRAY), 'ic-seal-on': (geo_seal, ACC),
    'ic-chat-on': (geo_chat, ACC),        # 与 k12 ic-chat 同一几何，点亮色
    'ic-bell': (None, GRAY), 'ic-moon': (None, GRAY), 'ic-book': (None, GRAY),
    'ic-back': (None, GRAY), 'ic-chev': (None, GRAY),
    'ic-kebab': (geo_kebab, GRAY),
}


def build_svg(name):
    geofn, color = SPEC[name]
    if geofn is None:  # lucide 标准造型路径（ISC 许可，@lucide-icons）
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" '
                f'viewBox="0 0 96 96"><g transform="scale(4)">{LUCIDE[name](color)}</g></svg>')
    body = []
    for item in geofn():
        kind = item[0]
        if kind == 'P':
            body.append(f'<path d="{item[1]}" fill="none" stroke="{color}" '
                        f'stroke-width="{item[2]}" stroke-linecap="round" '
                        f'stroke-linejoin="round"/>')
        elif kind == 'C':
            cx, cy, r = item[1]
            body.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
                        f'stroke="{color}" stroke-width="{item[2]}" '
                        f'stroke-linecap="round"/>')
        elif kind == 'F':
            cx, cy, r = item[1]
            body.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{color}"/>')
        elif kind == 'PF':
            body.append(f'<path d="{item[1]}" fill="{color}" stroke="{color}" '
                        f'stroke-width="1" stroke-linejoin="round"/>')
        elif kind == 'PC':
            body.append(f'<path d="{item[1]}" fill="none" stroke="{item[3]}" '
                        f'stroke-width="{item[2]}" stroke-linecap="round" '
                        f'stroke-linejoin="round"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" '
            f'viewBox="0 0 96 96">{"".join(body)}</svg>')


def render(name, px=768):
    import io
    import cairosvg
    from PIL import Image
    svg = build_svg(name)
    png = cairosvg.svg2png(bytestring=svg.encode(), output_width=px, output_height=px)
    return Image.open(io.BytesIO(png)).convert('RGBA')


def gen_file(name, out_dir=OUT_DIR):
    from PIL import Image
    size = SIZE[name]
    im = render(name).resize((size, size), Image.LANCZOS)
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, name + '.png')
    im.save(p)
    return p


def qa_sheet(sheet_dir):
    """各显示档拼贴（宣纸底，send 上朱砂底；3x 预览），供逐枚目检"""
    from PIL import Image, ImageDraw
    rows = [('ic-up', 26, '赞'), ('ic-up-on', 26, '赞on'), ('ic-down', 26, '踩'),
            ('ic-down-on', 26, '踩on'), ('ic-keep', 26, '收藏'), ('ic-keep-on', 26, '藏on'),
            ('ic-reaction', 26, '表情'), ('ic-copy', 26, '复制'), ('ic-speak', 26, '朗读'),
            ('ic-share', 26, '分享'), ('ic-edit', 26, '选取'), ('ic-check', 26, '多选'),
            ('ic-chat', 26, '反馈'), ('ic-delete', 26, '删除'), ('ic-link', 26, '引用'),
            ('ic-regen', 26, '重生'), ('ic-camera', 26, '相机'), ('ic-camera-soft', 26, '相机B'),
            ('ic-mic', 17, '麦17'), ('ic-keyboard', 19, '键盘19'), ('ic-plus', 19, '加19'),
            ('ic-send', 17, '发送17'), ('ic-link', 12, '引用chip12'), ('ic-speak', 16, '朗读16')]
    cols = 6
    cellw, cellh = 140, 140
    rows_n = (len(rows) + cols - 1) // cols
    sheet = Image.new('RGB', (cols * cellw, rows_n * cellh), (245, 239, 225))
    dr = ImageDraw.Draw(sheet)
    for i, (name, disp, label) in enumerate(rows):
        im = render(name)
        if name == 'ic-send':
            bg = Image.new('RGBA', im.size, (169, 58, 44, 255))
            im = Image.alpha_composite(bg, im).convert('RGB')
        else:
            bg = Image.new('RGBA', im.size, (245, 239, 225, 255))
            im = Image.alpha_composite(bg, im).convert('RGB')
        px = disp * 3
        im = im.resize((px, px), Image.LANCZOS)
        x = (i % cols) * cellw + (cellw - px) // 2
        y = (i // cols) * cellh + 12
        sheet.paste(im, (x, y))
        dr.text((i % cols * cellw + 6, y + px + 6), label, fill=(169, 58, 44))
    p = os.path.join(sheet_dir, 'k12-qa-sheet.png')
    sheet.save(p)
    big = Image.new('RGB', (len(SPEC) * 110 + 10, 100), (255, 255, 255))
    bd = ImageDraw.Draw(big)
    for i, name in enumerate(sorted(SPEC)):
        im = render(name).resize((84, 84), Image.LANCZOS)
        big.paste(im, (i * 110 + 13, 8), im)
        bd.text((i * 110 + 8, 86), name.replace('ic-', ''), fill=(0, 0, 0))
    p2 = os.path.join(sheet_dir, 'k12-qa-big.png')
    big.save(p2)
    print('QA:', p, p2)


if __name__ == '__main__':
    if '--qa' in sys.argv:
        idx = sys.argv.index('--qa')
        d = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else '/tmp'
        qa_sheet(d)
    else:
        for name in SPEC:
            print(gen_file(name))
        print('done ->', OUT_DIR)
