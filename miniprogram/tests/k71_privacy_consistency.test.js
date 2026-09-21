// k71：两份「用户可见隐私文案」关键口径一致性守卫 + 零调用权限声明守卫。
//
// 背景（k71 必修）：
//  1. `pages/privacy/privacy.wxml`（小程序内「灯下契约」页）此前**完全没提语音输入**，
//     而 `privacy.md`（提审用《隐私保护指引》）k70 已补语音段 → 口径分裂 = 漏披露。
//     独立复审进一步实测出该页与代码**更大范围**的不符（≥3 处直接矛盾 + 2 处漏披露）：
//     存储位置（本地 vs 腾讯云）、是否用于训练、合盘是否上传、免费档是否留记录、
//     第三方大模型（八字+提问发 DeepSeek/智谱）、昵称明文落库。本批一并收口。
//  2. `app.json` 曾声明 `permission.scope.userLocation`（"用于真太阳时校准"），
//     但全仓零调用 → 「声明了却不用」的多披露。
//
// 本文件的判据 = **页面真实渲染出来的字**。privacy.wxml 无 `{{ }}` 动态绑定（下面有断言
// 钉住这一前提），故「源码剥离标签后的文本」与「模拟器实际渲染出的文本」逐字相同
// （k71 报告有 outerWxml() 实测对照）。若日后引入动态绑定，该断言会红，
// 提示必须改用渲染取证而不是文本提取。
//
// 摘掉修复即变红（k71 报告有逐项实测原始输出）：
//  - 删掉 privacy.wxml 的「语音输入」整节 → 第 2 组红；
//  - 把任一旧口径写回页面（不会上传／不留存任何记录／存储在本地设备上／不会训练模型／
//    不会把你的信息给第三方）→ 第 5 组红；
//  - 把 scope.userLocation 写回 app.json（或出现定位调用）→ 第 6 组红；
//  - 把任一「关键口径」单方面改回（只改一份）→ 第 4 组红。
//
// 运行：cd miniprogram && node --test tests/k71_privacy_consistency.test.js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..');
const read = (rel) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
/** 仓根（本工作树）——用于读 src/ 后端源码做「文案 vs 代码」外部对照 */
const REPO = path.join(ROOT, '..');

const WXML_RAW = read('pages/privacy/privacy.wxml');
const MD_RAW = read('privacy.md');
const APPJSON_RAW = read('app.json');

/* 空白归一：两份文案一个换行成段、一个连排，比对前统一 */
const flat = (s) => s.replace(/\s+/g, ' ').trim();
/* 去掉注释与标签 → 用户真正看到的字 */
const wxmlText = (s) => flat(s.replace(/<!--[\s\S]*?-->/g, ' ').replace(/<[^>]+>/g, ' '));

const PAGE = wxmlText(WXML_RAW);
const DOC = flat(MD_RAW);

/* ════════════════════════════════════════════════════════════════
   0. 提取前提：页面无动态绑定（否则"源码文本"≠"渲染文本"，提取不可信）
   ════════════════════════════════════════════════════════════════ */
test('k71 前提：privacy.wxml 无 {{ }} 动态绑定 → 源码文本即用户可见文本', () => {
  assert.ok(!/\{\{/.test(WXML_RAW),
    'privacy.wxml 出现 {{ }} 动态绑定：本测试的文本提取不再等价于渲染结果，'
    + '须改用模拟器 outerWxml() 取证（见 k71 报告渲染段）');
  assert.ok(PAGE.length > 600, `提取到的可见文本过短（${PAGE.length} 字），提取器可能失效`);
});

/* ════════════════════════════════════════════════════════════════
   1. 语音披露：两份文案必须**同时**满足同一组事实（单份缺失即红）
   ════════════════════════════════════════════════════════════════ */
/* 每条事实 = 该事实在两份文案里各自的说法。事实本身来自 k71 独立复核的代码事实：
   - 全仓零 saveFile/getFileSystemManager 用于音频 → 不落盘
   - chat.js:_handleRecognitionResult 只读 res.result，不碰 tempFilePath → 只取文字
   - chat.js:_initSpeech → requirePlugin('WechatSI') → 音频经微信插件上传微信侧识别
   - src/main.py:/api/chat/upload content-type 白名单(_CHAT_UPLOAD_EXT) + _sniff_image_ext
     魔数嗅探 → 音频（含改名）415 拒收 → 本服务端无接收音频的入口
   - utils/streamHost.js:442 固定 messageType:'text'、voiceText 全仓零传入
     → 转写文字与键盘输入同路径 */
const VOICE_FACTS = [
  { key: '披露了语音输入这一功能',
    doc: /语音输入/, page: /语音输入/ },
  { key: '仅在用户主动操作时才开麦（无后台偷听）',
    doc: /仅当您主动(点击|使用)/, page: /只有你主动/ },
  { key: '录音只用于转写为文字',
    doc: /转写为文字/, page: /转成文字/ },
  { key: '本服务端不保存录音原文',
    doc: /不保存您的录音原文/, page: /我们不留你的录音/ },
  { key: '录音不写入设备存储',
    doc: /不写入设备存储/, page: /不写进你的手机/ },
  { key: '录音不传本服务端（服务器没有接收音频的入口）',
    doc: /本服务端没有接收音频的接口/, page: /没有接收录音的入口/ },
  { key: '音频改名上传同样被拒（防"改名绕过"误解）',
    doc: /改名上传也会被拒绝/, page: /改了名字上传也会被拒收/ },
  { key: '录音由微信「同声传译」插件上传到微信侧识别',
    doc: /同声传译/, page: /同声传译/ },
  { key: '第三方提供方为腾讯',
    doc: /提供方：腾讯/, page: /提供方：腾讯/ },
  { key: '明确"这是录音唯一离开设备的情形"',
    doc: /唯一离开您设备的情形/, page: /唯一离开你手机的情形/ },
  { key: '声明无法控制微信侧的处理与留存',
    doc: /无法控制微信侧的处理与留存/, page: /我们控制不了/ },
  { key: '给出退出方式：不想外传就用键盘输入',
    doc: /请使用键盘输入/, page: /就用键盘输入/ },
  { key: '转写文字与键盘输入处理完全相同',
    doc: /与键盘输入\*\*完全相同\*\*/, page: /和打字发出的消息完全相同/ },
];

test('k71-必修1 语音事实：privacy.md 逐条齐全', () => {
  const miss = VOICE_FACTS.filter((f) => !f.doc.test(DOC)).map((f) => f.key);
  assert.deepEqual(miss, [], `privacy.md 缺失语音事实：${miss.join(' / ')}`);
});

test('k71-必修1 语音事实：privacy.wxml（页面渲染文本）逐条齐全', () => {
  const miss = VOICE_FACTS.filter((f) => !f.page.test(PAGE)).map((f) => f.key);
  assert.deepEqual(miss, [], `privacy.wxml 缺失语音事实：${miss.join(' / ')}`);
});

test('k71-必修1 口径一致性：同一组语音事实两份文案都不缺（防"只改一份又分裂"）', () => {
  const broken = VOICE_FACTS
    .filter((f) => !(f.doc.test(DOC) && f.page.test(PAGE)))
    .map((f) => `${f.key}[md:${f.doc.test(DOC) ? '有' : '缺'} page:${f.page.test(PAGE) ? '有' : '缺'}]`);
  assert.deepEqual(broken, [],
    `两份用户可见隐私文案口径分裂（提审会露馅）：${broken.join(' / ')}`);
});

/* ════════════════════════════════════════════════════════════════
   2. 插件 AppID：唯一事实源 = app.json.plugins（数据一致性铁律）
   ════════════════════════════════════════════════════════════════ */
test('k71 插件 ID 单一事实源：两份文案引用的 AppID 必须等于 app.json 声明值', () => {
  const appjson = JSON.parse(APPJSON_RAW);
  const provider = appjson.plugins && appjson.plugins.WechatSI
    && appjson.plugins.WechatSI.provider;
  assert.ok(provider, 'app.json 未声明 plugins.WechatSI.provider');
  assert.match(provider, /^wx[0-9a-f]{16}$/, `provider 形态异常：${provider}`);
  assert.ok(APPJSON_RAW.indexOf(provider) !== -1, 'app.json 自身未含 provider');
  // 两份用户可见文案都必须引用**同一个** AppID 字面量（改插件 → 文案不同步即红）
  assert.ok(DOC.indexOf(provider) !== -1,
    `privacy.md 未引用 app.json 的插件 AppID ${provider}`);
  assert.ok(PAGE.indexOf(provider) !== -1,
    `privacy.wxml（用户可见页）未引用 app.json 的插件 AppID ${provider}`);
});

/* ════════════════════════════════════════════════════════════════
   3. 「文案 vs 代码」外部对照：页面的每条硬话都必须有源码支撑
      （oracle = 源码本身，不是"文案自己等于自己"）
   ════════════════════════════════════════════════════════════════ */
test('k71 页面"八字与提问发给第三方大模型"有代码支撑（DeepSeek / 智谱 GLM）', () => {
  const llm = read(path.join('..', 'src', 'llm', 'client.py'));
  assert.ok(/api\.deepseek\.com/.test(llm),
    'src/llm/client.py 未见 DeepSeek 端点 → 页面的"DeepSeek"披露失去依据');
  assert.ok(/open\.bigmodel\.cn|glm/i.test(llm),
    'src/llm/client.py 未见智谱 GLM 端点 → 页面的"智谱 GLM"披露失去依据');
  // 页面必须点名这两个服务商（漏披露的正是这一条）
  assert.ok(/DeepSeek/.test(PAGE), 'privacy.wxml 未点名 DeepSeek（第三方大模型漏披露）');
  assert.ok(/智谱/.test(PAGE), 'privacy.wxml 未点名智谱 GLM（第三方大模型漏披露）');
  // 页面必须说明发送的是"排盘信息 + 提问"
  assert.ok(/排盘信息/.test(PAGE) && /提问/.test(PAGE),
    'privacy.wxml 未说明发送内容是排盘信息与提问');
});

test('k71 页面"脱敏后进入训练集"有代码支撑（scripts/export_training_data.py）', () => {
  const s = read(path.join('..', 'scripts', 'export_training_data.py'));
  assert.ok(/训练集|微调/.test(s), 'export_training_data.py 不再是训练集导出脚本');
  assert.ok(/def desensitize/.test(s), 'export_training_data.py 未见 desensitize()（脱敏前提）');
  assert.ok(/openid/.test(s) && /手机号/.test(s) && /证件号/.test(s) && /邮箱/.test(s),
    'export_training_data.py 脱敏面变化 → 页面"抹掉微信标识/手机号/证件号/邮箱"须重新核对');
  // 页面必须如实披露"会被用于训练"（这正是改前"不会拿你的数据训练模型"的错处）
  assert.ok(/训练集/.test(PAGE), 'privacy.wxml 未披露对话可能进入训练集');
  assert.ok(/脱敏/.test(PAGE), 'privacy.wxml 未说明训练前先脱敏');
});

test('k71 页面"昵称明文保存"有代码支撑（users.nickname 无 _enc）', () => {
  const dao = read(path.join('..', 'src', 'storage', 'dao.py'));
  assert.ok(/nickname/.test(dao), 'dao.py 未见 nickname 处理');
  // 手机号是加密列（phone_enc），昵称是明文列（nickname）—— 结构性对照
  assert.ok(/phone_enc/.test(dao),
    'dao.py 未见 phone_enc 加密列 → 页面的"手机号加密/昵称明文"对照失去依据');
  assert.ok(/"UPDATE users SET nickname=\?, updated_at=\? WHERE user_id=\?"/.test(dao),
    'dao.py 的 nickname 写库语句形态变化 → 页面"昵称明文"须重新核对');
  assert.ok(/明文/.test(dao), 'dao.py 未再标注 nickname 明文 → 页面披露须重新核对');
  assert.ok(/昵称/.test(PAGE) && /明文/.test(PAGE),
    'privacy.wxml 未披露昵称明文保存（漏披露）');
});

test('k71 页面"合盘会发送到服务器"有代码支撑（api.union → POST /api/union）', () => {
  const api = read('utils/api.js');
  const hehun = read('pages/hehun/hehun.js');
  assert.ok(/request\('\/api\/union'/.test(api), 'utils/api.js 未向 /api/union 发请求');
  assert.ok(/_buildPayload/.test(hehun) && /person2:/.test(hehun),
    'hehun 未构造 person2 → 页面的"发送到服务器"说法失去依据，须同步改文案');
});

test('k71 页面"服务器不留存对方生辰、只留脱敏摘要"有代码支撑', () => {
  const union = fs.readFileSync(path.join(REPO, 'src', 'api', 'union.py'), 'utf8');
  assert.ok(/@router\.post\("\/api\/union"\)/.test(union), '后端 /api/union 路由不存在');
  assert.ok(/_archive_free_record/.test(union),
    '免费档脱敏归档实现消失 → 页面"服务器只留脱敏摘要"须重新核对');
  assert.ok(/绝不写双方生辰/.test(union),
    '归档"不写双方生辰"的隐私约束注释消失 → 页面该表述须重新核对');
});

test('k71 页面"本机留一份"有代码支撑（本地 storage 实存对话/档案）', () => {
  const host = read('utils/streamHost.js');
  const hist = read('pages/history/history.js');
  assert.ok(/'ylm_chat_messages'/.test(host), 'streamHost 未见本地消息存储键');
  assert.ok(/setStorageSync\(STORAGE_KEY/.test(host), 'streamHost 未落本地 storage');
  assert.ok(/getStorageSync\(STORAGE_KEY\)/.test(hist), 'history 页未从本地 storage 读');
  assert.ok(/在本机留一份/.test(PAGE), 'privacy.wxml 未说明本机留有副本');
});

/* ════════════════════════════════════════════════════════════════
   4. ★ 关键口径一致性（控制方点名四类：存储位置/是否上传/是否用于训练/第三方清单）
      双向断言 —— 任一份被单方面改回旧口径即红（数据一致性铁律）
   ════════════════════════════════════════════════════════════════ */
/* docNot/pageNot = 该口径的**反面表述**，两份都不得出现（否则就是自相矛盾）。
   注意：这里的反面串都写成"原文级"字面，不得写成过宽模式，
   以免误伤本页合法的限定性表述（如"除此之外，我们不把你的信息交给任何第三方"）。 */
const KEY_CONSISTENCY = [
  {
    key: '存储位置：两份都写明"加密存在服务器"，且都不说"只存在本地"',
    doc: /加密后存储在腾讯云服务器/,
    page: /加密存放在中国大陆境内的服务器上/,
    docNot: /(仅|只)(保存|存储)在(您的|你)?(设备|手机)(本地|上)/,
    pageNot: /(仅|只)(保存|存储)在(您的|你)?(设备|手机)(本地|上)/,
  },
  {
    key: '是否上传：两份都披露数据会离开设备，且都不说"不会上传"',
    doc: /上传/,
    page: /发送给第三方大模型服务商|发送到服务器/,
    docNot: /不会上传/,
    pageNot: /不会上传/,
  },
  {
    key: '是否用于训练：两份都披露去标识化后用于改进/训练模型，且都不否认训练',
    doc: /去标识化后使用/,
    page: /训练集/,
    // 反面串取**原文级**字面：只禁"整体否认训练"这一句，
    // 不禁本页合法的限定表述「没有脱敏的记录不会用于训练」。
    docNot: /不会拿你的数据训练模型|不会拿您的数据训练|不会用你的数据训练/,
    pageNot: /不会拿你的数据训练模型|不会拿您的数据训练|不会用你的数据训练/,
  },
  {
    key: '第三方清单：两份都有第三方披露节，且都不声称"不给第三方"',
    doc: /第三方服务/,
    page: /会发给谁/,
    docNot: /不会把(你的|您的)信息给第三方|不与任何第三方/,
    pageNot: /不会把(你的|您的)信息给第三方|不与任何第三方/,
  },
];

test('k71 关键口径一致性：四类口径两份文案逐条对齐（防"只改一份又分裂"）', () => {
  const broken = [];
  for (const c of KEY_CONSISTENCY) {
    const hasDoc = c.doc.test(DOC);
    const hasPage = c.page.test(PAGE);
    const badDoc = c.docNot.test(DOC);
    const badPage = c.pageNot.test(PAGE);
    if (!hasDoc) broken.push(`${c.key} → privacy.md 缺正向表述(${c.doc})`);
    if (!hasPage) broken.push(`${c.key} → privacy.wxml 缺正向表述(${c.page})`);
    if (badDoc) broken.push(`${c.key} → privacy.md 出现反面表述(${c.docNot})`);
    if (badPage) broken.push(`${c.key} → privacy.wxml 出现反面表述(${c.pageNot})`);
  }
  assert.deepEqual(broken, [], `关键口径不一致/自相矛盾：\n  - ${broken.join('\n  - ')}`);
});

/* ════════════════════════════════════════════════════════════════
   5. 旧错误表述不得复活（每条都有 k71 复核出的代码反证）
   ════════════════════════════════════════════════════════════════ */
const FORBIDDEN = [
  { re: /不会上传/,
    why: 'api.union 确会把 person2 POST 到服务端排盘 —— "不会上传"与代码不符' },
  { re: /不留存任何记录/,
    why: 'src/api/union.py:_archive_free_record 免费档确会脱敏归档（「合盘历史」可查）' },
  { re: /存储在本地设备上/,
    why: '对话记录落服务端 sessions.content（AES-256-GCM），非"只存本地设备"' },
  { re: /不会拿你的数据训练模型/,
    why: 'scripts/export_training_data.py 确会把对话脱敏后导出为训练集（微调格式）' },
  { re: /不会把你的信息给第三方/,
    why: '八字与提问确会发给 DeepSeek/智谱 GLM，且录音经微信插件外传，此断言与事实不符' },
  { re: /全部本地处理|全都在本地/,
    why: '排盘信息与提问会发给第三方大模型处理，不存在"全部本地处理"' },
];

test('k71 页面不得回退到与代码不符的旧表述', () => {
  const hit = FORBIDDEN.filter((f) => f.re.test(PAGE));
  assert.deepEqual(hit.map((f) => `${f.re} ← ${f.why}`), [],
    '页面出现与代码不符的旧表述');
});

test('k71 双人合盘三点披露齐备（免费档/深度报告/分享图）', () => {
  for (const re of [/免费档合盘/, /深度报告/, /分享图/]) {
    assert.ok(re.test(PAGE), `页面缺少合盘披露项 ${re}`);
  }
});

/* ════════════════════════════════════════════════════════════════
   6. 必修2：零调用的 scope.userLocation 声明已移除，且确实零调用
   ════════════════════════════════════════════════════════════════ */
test('k71-必修2 app.json 是合法 JSON', () => {
  assert.doesNotThrow(() => JSON.parse(APPJSON_RAW),
    'app.json 不是合法 JSON（开发者工具会直接报错）');
});

test('k71-必修2 app.json 不再声明 scope.userLocation / requiredPrivateInfos', () => {
  const appjson = JSON.parse(APPJSON_RAW);
  assert.equal(appjson.permission, undefined,
    'app.json 仍有 permission 声明（scope.userLocation 零调用，属"声明了却不用"）');
  assert.ok(!/scope\.userLocation/.test(APPJSON_RAW),
    'app.json 仍出现 scope.userLocation 字面量');
  assert.equal(appjson.requiredPrivateInfos, undefined,
    'app.json 出现 requiredPrivateInfos（需逐项与真实调用核对）');
  // 反向钉住：合法能力声明不得被误删
  assert.ok(appjson.plugins && appjson.plugins.WechatSI,
    'plugins.WechatSI 是语音链路必需声明，必须保留');
  assert.ok(Array.isArray(appjson.pages) && appjson.pages.length > 0,
    'pages 声明必须保留');
});

test('k71-必修2 全仓（非测试）零定位 API 调用', () => {
  const LOC_RE = /(wx\s*\.\s*(getLocation|chooseLocation|openLocation|onLocationChange|startLocationUpdate|choosePoi|chooseAddress)|scope\.userLocation|requiredPrivateInfos)/;
  const exts = new Set(['.js', '.wxml', '.json', '.wxss']);
  const hits = [];
  const walk = (dir) => {
    for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
      // tests/ 排除：本守卫自身的正则字面量会自匹配；node_modules 非交付物
      if (ent.isDirectory()) {
        if (ent.name === 'node_modules' || ent.name === 'tests') continue;
        walk(path.join(dir, ent.name));
      } else if (exts.has(path.extname(ent.name))) {
        const p = path.join(dir, ent.name);
        fs.readFileSync(p, 'utf8').split('\n').forEach((line, i) => {
          if (LOC_RE.test(line)) hits.push(`${path.relative(ROOT, p)}:${i + 1}`);
        });
      }
    }
  };
  walk(ROOT);
  assert.deepEqual(hits, [],
    `发现定位能力声明/调用（须重新评估 app.json 权限声明）：${hits.join(', ')}`);
});
