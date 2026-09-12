// 易理明灯 k34 A33 — 墨色功能图标族五枚并入 k12/k14 浅细线族（像素级回归锁）
// 运行：cd miniprogram && node --test tests/k34_icon_family.test.js
// 背景：ic-engine/ic-memory/ic-web/ic-image/ic-scroll 长期是 B6-1 时代的墨色实形
//   （#3A2C1E、覆盖 19.6-24.9%），与同屏族内图标（ic-book 等 #756E63 浅细线）混排
//   不一致；k34 用既有管线（scripts/gen_k12_icons.py → verify_k12_icons.py）重绘入族。
// 本用例在 node 侧解 PNG 像素，锁：96×96 RGBA + 主色落中灰族 + 覆盖率落族带 +
//   不带旧墨色（#3A2C1E）——资产被换回墨色版即失败（python 验证器之外的常驻护栏）。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');

const IMG_DIR = path.join(__dirname, '../assets/images');
const K34_ICONS = ['ic-engine', 'ic-memory', 'ic-web', 'ic-image', 'ic-scroll'];
const FAMILY_GRAY = [0x75, 0x6E, 0x63];   // k12 族常态中灰 #756E63
const LEGACY_INK = [0x3A, 0x2C, 0x1E];    // B6-1 墨色实形（本批淘汰）

/* ── 最小 PNG 解码（RGBA8 / 非隔行；zlib 内建，无第三方依赖） ── */
function decodePng(buf) {
  const sig = [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A];
  for (let i = 0; i < 8; i++) assert.equal(buf[i], sig[i], 'PNG 签名');
  let pos = 8; let ihdr = null; const idat = [];
  while (pos < buf.length) {
    const len = buf.readUInt32BE(pos);
    const type = buf.toString('ascii', pos + 4, pos + 8);
    const data = buf.subarray(pos + 8, pos + 8 + len);
    if (type === 'IHDR') {
      ihdr = {
        w: data.readUInt32BE(0), h: data.readUInt32BE(4),
        depth: data[8], colorType: data[9], interlace: data[12],
      };
    } else if (type === 'IDAT') idat.push(data);
    else if (type === 'IEND') break;
    pos += 12 + len;
  }
  assert.ok(ihdr, 'IHDR 存在');
  assert.equal(ihdr.depth, 8, '位深 8');
  assert.equal(ihdr.colorType, 6, '色彩类型 RGBA(6)');
  assert.equal(ihdr.interlace, 0, '非隔行');
  const raw = zlib.inflateSync(Buffer.concat(idat));
  const bpp = 4, stride = ihdr.w * bpp;
  const out = Buffer.alloc(ihdr.h * stride);
  const paeth = (a, b, c) => {
    const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
    return (pa <= pb && pa <= pc) ? a : (pb <= pc ? b : c);
  };
  let p = 0;
  for (let y = 0; y < ihdr.h; y++) {
    const ft = raw[p]; p += 1;
    const row = raw.subarray(p, p + stride); p += stride;
    const cur = out.subarray(y * stride, (y + 1) * stride);
    const prev = y > 0 ? out.subarray((y - 1) * stride, y * stride) : Buffer.alloc(stride);
    for (let x = 0; x < stride; x++) {
      const a = x >= bpp ? cur[x - bpp] : 0;
      const b = prev[x];
      const c = x >= bpp ? prev[x - bpp] : 0;
      let v = row[x];
      if (ft === 1) v += a;
      else if (ft === 2) v += b;
      else if (ft === 3) v += (a + b) >> 1;
      else if (ft === 4) v += paeth(a, b, c);
      cur[x] = v & 0xff;
    }
  }
  return { w: ihdr.w, h: ihdr.h, px: out };
}

/* 不透明像素占比 + 主色（直方粗量化，取 a>200 像素） */
function stats(img) {
  const { w, h, px } = img;
  let opaque = 0;                    // a>128 = 可见像素（verify_k12_icons 同口径）
  const hist = new Map();
  for (let i = 0; i < w * h; i++) {
    const r = px[i * 4], g = px[i * 4 + 1], b = px[i * 4 + 2], a = px[i * 4 + 3];
    if (a > 128) opaque += 1;
    if (a > 200) {
      const k = `${r >> 3},${g >> 3},${b >> 3}`;
      hist.set(k, (hist.get(k) || 0) + 1);
    }
  }
  let dom = null, n = -1;
  for (const [k, v] of hist) if (v > n) { n = v; dom = k.split(',').map((s) => (Number(s) << 3) + 4); }
  return { covPct: (opaque / (w * h)) * 100, dom, domPct: (n / Math.max(opaque, 1)) * 100 };
}

const near = (c, ref, tol) => Math.abs(c[0] - ref[0]) <= tol
  && Math.abs(c[1] - ref[1]) <= tol && Math.abs(c[2] - ref[2]) <= tol;

test('A33 五枚墨色图标已并入浅细线族：96×96 RGBA + 中灰 #756E63 + 覆盖率落族带', () => {
  for (const name of K34_ICONS) {
    const p = path.join(IMG_DIR, `${name}.png`);
    assert.ok(fs.existsSync(p), `${name}.png 应存在（assets/images 单点资产目录）`);
    const img = decodePng(fs.readFileSync(p));
    assert.equal(img.w, 96, `${name} 宽 96`);
    assert.equal(img.h, 96, `${name} 高 96`);
    const { covPct, dom, domPct } = stats(img);
    assert.ok(covPct > 2, `${name} 非空图（实测 ${covPct.toFixed(1)}%）`);
    assert.ok(near(dom, FAMILY_GRAY, 26),
      `${name} 主色应为族中灰 #756E63 ±26（实测 ${dom}，占 ${domPct.toFixed(0)}%）`);
    assert.ok(!near(dom, LEGACY_INK, 26),
      `${name} 不得回退到 B6-1 墨色实形 #3A2C1E（实测 ${dom}）`);
    assert.ok(covPct >= 6 && covPct <= 28,
      `${name} 覆盖率应落浅细线族带 6-28%（实测 ${covPct.toFixed(1)}%）`);
  }
});

test('A33 引用位资产引用关系未变（换画风不动路径）', () => {
  const citation = fs.readFileSync(path.join(__dirname, '../pages/citation/citation.js'), 'utf8');
  for (const n of ['ic-book', 'ic-engine', 'ic-memory', 'ic-web']) {
    assert.match(citation, new RegExp(`/assets/images/${n}\\.png`), `citation.js 引用 ${n}`);
  }
  const chatJs = fs.readFileSync(path.join(__dirname, '../pages/chat/chat.js'), 'utf8');
  for (const n of ['ic-book', 'ic-engine', 'ic-memory', 'ic-web']) {
    assert.match(chatJs, new RegExp(`/assets/images/${n}\\.png`), `chat.js 引用角标映射 ${n}`);
  }
  assert.match(fs.readFileSync(path.join(__dirname, '../pages/share/share.wxml'), 'utf8'),
    /\/assets\/images\/ic-image\.png/, 'share.wxml 通道图标 ic-image');
  for (const page of ['agreement', 'qimen']) {
    assert.match(fs.readFileSync(path.join(__dirname, `../pages/${page}/${page}.wxml`), 'utf8'),
      /\/assets\/images\/ic-scroll\.png/, `${page}.wxml 空态/协议图标 ic-scroll`);
  }
});

test('A33 管线单一事实源：五枚在生成器 SIZE/SPEC 与验证器 FAMILY 中登记', () => {
  const gen = fs.readFileSync(path.join(__dirname, '../../scripts/gen_k12_icons.py'), 'utf8');
  const ver = fs.readFileSync(path.join(__dirname, '../../scripts/verify_k12_icons.py'), 'utf8');
  for (const n of K34_ICONS) {
    assert.match(gen, new RegExp(`'${n}': 96,`), `gen SIZE 登记 ${n}`);
    assert.match(gen, new RegExp(`'${n}': \\(None, GRAY\\)`), `gen SPEC 登记 ${n}（Lucide 造型 + 灰）`);
    assert.match(ver, new RegExp(`'${n}': \\('GRAY'`), `verify FAMILY 登记 ${n}（色族断言）`);
  }
  assert.match(gen, /k34/, '生成器头注应记 k34 节（风格出处/ISC 署名）');
});
