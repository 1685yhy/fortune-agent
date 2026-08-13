#!/usr/bin/env python3
"""Task 4（chat-ux）验证：md.js 解析器 — 围栏表格识别 / 图片节点 / 链接断行样式 / 三版一致。

无 JS 测试基建：解析器输入输出断言走 node（node -e 加载 md.js 跑断言），
另用 `node --check` 保证语法。全部离线。

覆盖：
  1. 纯表格围栏（``` 内全为 | 行且含分隔行）→ table 节点（复用现有表格渲染）
  2. 带 lang 的表格围栏 → table
  3. 围栏内混合内容（代码+表格行）→ code（保持代码块）
  4. 纯代码围栏 → code
  5. 全 | 行但无分隔行 → code（兜底要求存在分隔行）
  6. 冒号对齐分隔行（|:---:|）→ table
  7. 图片 ![alt](url) → image 节点（alt/url 正确，前后文本保留）
  8. 空 alt 图片 ![](url) → image 节点
  9. 链接 [text](url) 行为不变
  10. 引用角标 [1] / 普通感叹号行为不变
  11. 非围栏表格（| a | b | + 分隔行）行为不变
  12. 三版（墨韵/simple/fusion）md.js + chat.wxml + chat.wxss 完全一致
  13. 前端 wxss：.md-p/.md-link/.md-icode 含 word-break（长链接不穿泡）

用法：
  .venv/bin/python3 scripts/test_md_render.py
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MD_JS = os.path.join(ROOT, "miniprogram", "utils", "md.js")
VERSIONS = ["miniprogram", "miniprogram_simple", "miniprogram_fusion"]
REL_FILES = ["utils/md.js", "pages/chat/chat.wxml", "pages/chat/chat.wxss"]

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


HARNESS = r"""
const md = require(process.argv[1]);
const results = [];
function ok(name, cond, detail) { results.push({ name, pass: !!cond, detail: detail || '' }); }
function firstOf(nodes, t) { return nodes.find(n => n.t === t); }
function firstTable(nodes) { return firstOf(nodes, 'table'); }
const inline = (nodes) => nodes[0] ? (nodes[0].children || []) : [];
let nodes, tbl, it;

// 1. 纯表格围栏 → table
nodes = md.parseMd('```\n| 项目 | 宜 | 忌 |\n| --- | --- | --- |\n| 出行 | 吉 | 忌 |\n```');
tbl = firstTable(nodes);
ok('1 纯表格围栏 → table（不渲染成 code）', !!tbl && nodes.length === 1 && !firstOf(nodes, 'code'));
ok('1 表头/单元格解析正确',
   tbl && tbl.headers[0].children[0].s === '项目' && tbl.headers[2].children[0].s === '忌'
   && tbl.rows[0][1].children[0].s === '吉' && tbl.rows[0][2].children[0].s === '忌');

// 2. 带 lang 的表格围栏 → table
nodes = md.parseMd('```markdown\n| a | b |\n| :- | -: |\n| 1 | 2 |\n```');
ok('2 带 lang 表格围栏 → table', !!firstTable(nodes));

// 3. 围栏内混合内容 → code
nodes = md.parseMd('```python\nprint(1)\n| 不是表格 |\n```');
ok('3 混合围栏 → code', !!firstOf(nodes, 'code') && !firstTable(nodes)
   && firstOf(nodes, 'code').s.includes('print(1)'));

// 4. 纯代码围栏 → code
nodes = md.parseMd('```js\nconst a = 1;\n```');
ok('4 纯代码围栏 → code', !!firstOf(nodes, 'code') && !firstTable(nodes));

// 5. 全 | 行但无分隔行 → code（兜底要求存在分隔行）
nodes = md.parseMd('```\n| a | b |\n| c | d |\n```');
ok('5 无分隔行的围栏 → code', !firstTable(nodes) && !!firstOf(nodes, 'code'));

// 5b. 围栏内含解释文字 → code
nodes = md.parseMd('```\n说明如下：\n| a | b |\n| --- | --- |\n```');
ok('5b 含非表格行的围栏 → code', !!firstOf(nodes, 'code') && !firstTable(nodes));

// 6. 冒号对齐分隔行（|:---:|）也算表格
nodes = md.parseMd('```\n| 时辰 | 吉凶 |\n| :---: | :---: |\n| 辰时 | 吉 |\n```');
ok('6 冒号分隔行 → table', !!firstTable(nodes));

// 7. 图片 ![alt](url) → image 节点
nodes = md.parseMd('看这张图：![命盘图](https://example.com/p.png)');
it = inline(nodes);
ok('7 图片节点（alt/url 正确）',
   !!it.find(c => c.t === 'image' && c.url === 'https://example.com/p.png' && c.alt === '命盘图'));
ok('7 图片前后文本保留', it[0].s === '看这张图：' && it[it.length - 1].t === 'image');

// 8. 空 alt 图片 ![](url)
nodes = md.parseMd('![](https://x.com/i.png)');
ok('8 空 alt 图片 → image', !!inline(nodes).find(c => c.t === 'image'));

// 9. 链接行为不变
nodes = md.parseMd('[点这里](https://x.com)');
ok('9 链接仍正常', !!inline(nodes).find(c => c.t === 'link' && c.url === 'https://x.com'));

// 10. 引用角标与普通感叹号不变
nodes = md.parseMd('古籍记载[1]');
ok('10 引用角标 [1] 仍正常', !!inline(nodes).find(c => c.t === 'cite' && c.idx === 1));
nodes = md.parseMd('真好！这里不错');
ok('10 普通感叹号仍为文本', inline(nodes).every(c => c.t === 'text'));

// 11. 非围栏表格不变
nodes = md.parseMd('| x | y |\n| --- | --- |\n| 1 | 2 |');
ok('11 普通表格仍正常', !!firstTable(nodes) && firstTable(nodes).rows[0][0].children[0].s === '1');

const failed = results.filter(r => !r.pass);
console.log(JSON.stringify(results));
process.exit(failed.length ? 1 : 0);
"""


def main():
    print("== node 环境 ==")
    node = shutil.which("node")
    ok("node 可用", bool(node), f"node={node}")

    print("== md.js 语法（node --check） ==")
    if node:
        r = subprocess.run([node, "--check", MD_JS], capture_output=True, text=True)
        ok("node --check 通过", r.returncode == 0, r.stderr.strip())
    else:
        ok("node --check 通过", False, "node 不可用")

    print("== md.js 解析器行为（node 断言） ==")
    if node:
        r = subprocess.run([node, "-e", HARNESS, MD_JS], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            for item in json.loads(r.stdout):
                ok(item["name"], item["pass"], item["detail"])
        else:
            ok("解析器断言运行成功", False, f"rc={r.returncode} stderr={r.stderr.strip()[:300]}")
    else:
        ok("解析器断言运行成功", False, "node 不可用")

    print("== 三版一致性（墨韵/simple/fusion） ==")
    for rel in REL_FILES:
        hashes = {}
        for v in VERSIONS:
            p = os.path.join(ROOT, v, rel)
            if not os.path.exists(p):
                hashes[v] = None
                continue
            hashes[v] = hashlib.md5(open(p, "rb").read()).hexdigest()
        uniq = set(hashes.values())
        ok(f"{rel} 三版一致", len(uniq) == 1 and None not in uniq, str(hashes))

    print("== 前端断行样式（chat.wxss） ==")
    wxss = open(os.path.join(ROOT, "miniprogram", "pages", "chat", "chat.wxss"),
                encoding="utf-8").read()

    def block_for(sel):
        m = re.search(re.escape(sel) + r"\s*\{([^}]*)\}", wxss)
        return m.group(1) if m else ""

    for sel in (".md-p", ".md-link", ".md-icode"):
        ok(f"{sel} 声明块含 word-break: break-all",
           "word-break: break-all" in block_for(sel),
           f"block={block_for(sel)!r}")

    print(f"\n结果: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
