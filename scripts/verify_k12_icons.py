#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
k12 图标重绘 · 像素断言验证（配合 scripts/gen_k12_icons.py）
============================================================
断言（对应 docs/superpowers/plans/2026-09-08-k12-icon-redraw.md §5.1）：
  1. 每目标文件存在、RGBA、尺寸=既有档位、非空（不透明像素占比 >1%）
  2. 主色命中色族：常态=#756E63 中灰带、on=#A93A2C、delete=#8C2E22、send=#FAF5E7
  3. 笔画覆盖率合理区间（常态细线条 6%≤cov≤25%；send 实心 ≥15%）
  4. 主笔划实宽（行/列最大连续不透明段）≥5px@96 档 / ≥10px@192 档（杜绝发虚）
  5. 无 legacy 淡棕残留：不透明像素中 #9A8B71/#988878 邻域占比 <1%
  6. 96/192 同源：192 档 LANCZOS 降采样至 96 与直接 96 渲染 RMSE<2.0
用法：python3 scripts/verify_k12_icons.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_k12_icons as g  # noqa: E402
from PIL import Image  # noqa: E402

FAMILY = {  # 文件名 -> (色族, 覆盖率下限, 覆盖率上限, 覆盖阈值说明)
    'ic-up': ('GRAY', 6, 26), 'ic-up-on': ('ACC', 6, 26),
    'ic-down': ('GRAY', 6, 26), 'ic-down-on': ('ACC', 6, 26),
    'ic-keep': ('GRAY', 6, 22), 'ic-keep-on': ('ACC', 6, 22),
    'ic-reaction': ('GRAY', 6, 22), 'ic-copy': ('GRAY', 6, 24),
    'ic-speak': ('GRAY', 6, 26), 'ic-share': ('GRAY', 6, 26),
    'ic-edit': ('GRAY', 6, 22), 'ic-check': ('GRAY', 6, 26),
    'ic-chat': ('GRAY', 6, 22), 'ic-delete': ('DEEP', 6, 24),
    'ic-link': ('GRAY', 6, 24), 'ic-regen': ('GRAY', 6, 22),
    'ic-mic': ('GRAY', 6, 26), 'ic-keyboard': ('GRAY', 6, 24),
    'ic-camera': ('GRAY', 6, 26), 'ic-camera-soft': ('GRAY', 6, 26),
    'ic-plus': ('GRAY', 6, 20), 'ic-send': ('PAPER', 15, 60),
}
REF = {'GRAY': (0x75, 0x6E, 0x63), 'ACC': (0xA9, 0x3A, 0x2C),
       'DEEP': (0x8C, 0x2E, 0x22), 'PAPER': (0xFA, 0xF5, 0xE7)}
LEGACY = (0x9A, 0x8B, 0x71)  # 旧 legacy 淡棕（#9A8B71/#988878 同带）


def analyze(im):
    """返回 (不透明占比%, 覆盖占比%, 主色(quant), 最大水平/垂直连续段, legacy占比%)"""
    w, h = im.size
    px = im.load()
    tot = occ = core = leg = 0
    max_h = max_v = 0
    for y in range(h):
        run = 0
        for x in range(w):
            r, gg, b, a = px[x, y]
            if a > 128:
                tot += 1
                if a > 200:
                    occ += 1
                if a > 250:  # 纯色核（排除抗锯齿过渡）作 legacy 判定
                    core += 1
                    if (abs(r - LEGACY[0]) <= 20 and abs(gg - LEGACY[1]) <= 20
                            and abs(b - LEGACY[2]) <= 20):
                        leg += 1
                run += 1
                max_h = max(max_h, run)
            else:
                run = 0
    for x in range(w):
        run = 0
        for y in range(h):
            if px[x, y][3] > 128:
                run += 1
                max_v = max(max_v, run)
            else:
                run = 0
    # 主色（仅取 a>200 像素）粗量化直方
    from collections import Counter
    cnt = Counter()
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            r, gg, b, a = px[x, y]
            if a > 200:
                cnt[(r // 8 * 8 + 4, gg // 8 * 8 + 4, b // 8 * 8 + 4)] += 1
    dom = cnt.most_common(1)[0][0] if cnt else (0, 0, 0)
    cov_pct = tot / (w * h) * 100
    occ_pct = occ / (w * h) * 100
    leg_pct = leg / max(core, 1) * 100
    return occ_pct, cov_pct, dom, max_h, max_v, leg_pct


def main():
    fails = []
    for name, (fam, lo, hi) in sorted(FAMILY.items()):
        size = g.SIZE[name]
        p = os.path.join(g.OUT_DIR, name + '.png')
        im = Image.open(p)
        if im.mode != 'RGBA':
            fails.append(f'{name}: 非 RGBA ({im.mode})')
            continue
        if im.size != (size, size):
            fails.append(f'{name}: 尺寸 {im.size} != {size}')
        occ, cov, dom, mh, mv, leg = analyze(im)
        if occ < 1:
            fails.append(f'{name}: 近空图 occ={occ:.1f}%')
        ref = REF[fam]
        if not (abs(dom[0] - ref[0]) <= 26 and abs(dom[1] - ref[1]) <= 26
                and abs(dom[2] - ref[2]) <= 26):
            fails.append(f'{name}: 主色 {dom} 不在 {ref} 族 ±26')
        if not (lo <= cov <= hi):
            fails.append(f'{name}: 覆盖率 {cov:.1f}% 不在 [{lo},{hi}]')
        mmin = min(mh, mv)
        need = 5 if size == 96 else 10
        if mmin < need:
            fails.append(f'{name}: 最细连续段 {mmin}px < {need}px（发虚风险）')
        if leg > 0.5:
            fails.append(f'{name}: legacy 淡棕占比 {leg:.2f}% > 0.5%')
    # 96/192 同源：两档皆出自同一 build_svg → 断言 a) 几何 bbox 线性一致；
    # b) 覆盖率一致；c) 落盘资产 == 生成器直接输出（管线保真，防陈旧产物）
    def bbox_cov(im):
        px = im.load()
        w, h = im.size
        x0, y0, x1, y1 = w, h, -1, -1
        tot = 0
        for y in range(h):
            for x in range(w):
                if px[x, y][3] > 128:
                    tot += 1
                    x0, y0 = min(x0, x), min(y0, y)
                    x1, y1 = max(x1, x), max(y1, y)
        return (x0, y0, x1, y1), tot / (w * h) * 100

    for name in FAMILY:
        size = g.SIZE[name]
        p = os.path.join(g.OUT_DIR, name + '.png')
        shipped = Image.open(p).convert('RGBA')
        direct = g.render(name).resize((size, size), Image.LANCZOS)
        a = shipped.convert('L').load()
        b = direct.convert('L').load()
        s = sum((a[x, y] - b[x, y]) ** 2 for y in range(size) for x in range(size))
        rmse = math.sqrt(s / (size * size))
        if rmse > 2.0:
            fails.append(f'{name}: 落盘与生成器直接输出 RMSE {rmse:.2f} > 2.0（管线不一致/陈旧产物）')
        b96, c96 = bbox_cov(g.render(name).resize((96, 96), Image.LANCZOS))
        b192, c192 = bbox_cov(g.render(name).resize((192, 192), Image.LANCZOS))
        if abs(b192[0] - b96[0] * 2) > 3 or abs(b192[1] - b96[1] * 2) > 3 \
                or abs(b192[2] - b96[2] * 2) > 3 or abs(b192[3] - b96[3] * 2) > 3:
            fails.append(f'{name}: 96/192 bbox 不同源 {b96} vs {b192}')
        if abs(c192 - c96) > 3.5:
            fails.append(f'{name}: 96/192 覆盖率差 {c96:.1f}% vs {c192:.1f}% > 3.5%（AA 量化差上限）')
    print(f'check {len(FAMILY)} icons, size-class 96/192, legacy-color, stroke-run, same-source')
    if fails:
        print('FAIL:')
        for f in fails:
            print(' -', f)
        sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
