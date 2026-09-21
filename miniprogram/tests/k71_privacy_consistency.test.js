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
  /* k75 改判（控制方裁定「守卫断言事实，不断言旧字面」）：下面两条原先把正则钉在
     k70/k71 版 md 的原字面上（「本服务端没有接收音频的接口」「改名上传也会被拒绝」）。
     k72 批把 md 这两句改写成更准确的说法（把"为什么拒收"讲清楚了：图片上传接口
     只接收图片 + 按**真实内容**校验，不只认文件名/声明的类型）—— 事实没变、说得更准。
     按「事实为准」重钉到**事实**上（正则是事实的载体，不是版本字面）：
       · 事实①=本服务端不接收录音（上传入口只收图片）；
       · 事实②=改名的音频同样被拒（按真实内容校验，绕不过去）。
     这不是放宽：两条事实各自的**代码依据**由本文件 §11 的 oracle 断言钉住
     （src/main.py 的 _CHAT_UPLOAD_EXT 白名单 + _sniff_image_ext 魔数嗅探 + 415），
     代码一改，oracle 先红。 */
  { key: '录音不传本服务端（上传入口只接收图片，没有接收音频的入口）',
    doc: /只接收图片/, page: /没有接收录音的入口/ },
  { key: '音频改名上传同样被拒（按真实内容校验，不只认文件名/所声明的类型）',
    doc: /改名的音频文件同样会被拒绝/, page: /改了名字上传也会被拒收/ },
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
    /* k75 改判（控制方裁定「守卫断言事实，不断言旧字面」）：原 doc 正则钉在
       k70/k71 版 md 的字面「加密后存储在腾讯云服务器」上；k72 批把 md 改成
       「…使用 AES-256-GCM 加密后存储在**中国大陆境内的**腾讯云服务器上」，
       并**逐项列明哪些字段不加密**（昵称／他人称呼与关系／自动汇总记录／
       收藏与保存内容／图片）—— 这是按代码实际**收窄**了"全部 AES-256"的夸张，
       是更准确的表述。故重钉到事实：md 必须载明「部分字段加密存储于腾讯云」，
       并且**必须同时载明明文项**（下方 §11b 的 k75 断言钉住明文项与代码对照）。
       这不是放宽：原断言只查"有没有这句话"，新断言多查了"明文项有没有如实列出"。 */
    key: '存储位置：两份都写明"加密存在服务器"，且都不说"只存在本地"',
    doc: /加密后存储在中国大陆境内的腾讯云服务器上/,
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
    /* k75 改判：原 doc 正则钉在 k70/k71 版 md 的字面「去标识化后使用」上；
       k72 批改成「以**去除身份标识后**的数据改进本服务的质量与模型准确度」，
       并**把两种方式与脱敏面逐项写明**（汇总统计 / 导出前抹掉微信标识·手机号·
       证件号·邮箱·长数字串，未脱敏不用于训练）—— 事实更全、更准确。故重钉到
       事实：md 必须载明「去除身份标识后的数据用于改进」。§11c 另有 oracle
       断言把脱敏面逐项钉在 scripts/export_training_data.py 上（代码改则先红）。 */
    key: '是否用于训练：两份都披露去标识化后用于改进/训练模型，且都不否认训练',
    doc: /去除身份标识后\*{0,2}的数据改进/,
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

/* ══════════════════════════════════════════════════════════════════════════
   ↓↓↓ k74 追加（控制方「只要是报的，都要修」批）↓↓↓
   本段**只新增断言，未放宽/删除任何既有断言**，也未新增 skip/xfail。
   ══════════════════════════════════════════════════════════════════════════ */

/* ════════════════════════════════════════════════════════════════
   7. k74-必修3：**三份**用户可见文案共用同一张事实表
      privacy.wxml（页内契约）/ privacy.md（提审文档）/ agreement.wxml（用户协议页）
      三份各有各的粒度，但**共用同一组"不得出现"的绝对句与同一张第三方清单**。
   ════════════════════════════════════════════════════════════════ */
const AGREEMENT_RAW = read('pages/agreement/agreement.wxml');
const AGREE = wxmlText(AGREEMENT_RAW);

test('k74 前提：agreement.wxml 剥标签后无 {{ }} 残留（属性绑定不影响文本提取）', () => {
  // agreement 的 scroll-y="{{true}}" 是**属性**绑定，剥标签后不进入可见文本；
  // 若日后把 {{ }} 写进文本节点，这个断言会红，提示必须改用渲染取证。
  assert.ok(!/\{\{/.test(AGREE),
    'agreement.wxml 的可见文本出现 {{ }} 动态绑定：文本提取不再等价于渲染结果，须改用 outerWxml() 取证');
  assert.ok(AGREE.length > 800, `agreement 提取到的可见文本过短（${AGREE.length} 字）`);
  assert.ok(AGREE.indexOf('用户协议') !== -1, 'agreement 提取文本里没有「用户协议」标题，提取器可能失效');
});

/* 三份共用：这些绝对句都**分别有代码反证**（逐条证据见 k74 报告「三、agreement 对照」与
   「k72 证据复核」两节），任何一份文案里复活即红。注意：不包含「这是录音唯一离开你手机的情形」
   ——那句是**录音**专指且属实（VOICE_FACTS 第 10 条要求页面必须保留它）。 */
const THREE_DOC_FORBIDDEN = [
  { re: /这是你主动提问时唯一对外发送的内容|唯一对外发送的内容/,
    why: '实际还会外发：姓名/期望（大模型）、待朗读文字（微软 edge-tts）、检索词（公网搜索引擎）、'
       + '录音（微信插件）、分享页字体请求（Google Fonts）' },
  { re: /不交给任何第三方|不把你的信息交给任何第三方/,
    why: 'src/api/union.py、src/rag/web_search.py、src/main.py:/api/tts 等确有多路第三方外发' },
  { re: /不会上传/,
    why: 'api.union 确会把 person2 POST 到服务端排盘 —— "不会上传"与代码不符' },
  { re: /不留存任何记录/,
    why: 'src/api/union.py:_archive_free_record 免费档确会脱敏归档（「合盘历史」可查）' },
  { re: /存储在本地设备上|只存储在本地|仅存在本地/,
    why: '对话记录落服务端 sessions.content（AES-256-GCM），非"只存本地设备"' },
  { re: /不会拿你的数据训练模型|不会拿您的数据训练|不会用你的数据训练/,
    why: 'scripts/export_training_data.py 确会把对话脱敏后导出为训练集（微调格式）' },
  { re: /不会把(你的|您的)信息给第三方|不与任何第三方/,
    why: '排盘信息与提问确会发给 DeepSeek/智谱 GLM，录音经微信插件外传，此断言与事实不符' },
  { re: /全部本地处理|全都在本地/,
    why: '排盘信息与提问会发给第三方大模型处理，不存在"全部本地处理"' },
  { re: /人工无法直接查看|只有经授权的服务端程序能读到/,
    /* k75：k72 批已把 md 的「三.3 …人工无法直接查看」改成「数据只通过受鉴权的服务端
       接口读写（须持有您的登录凭证）；加密字段须持有服务端密钥才能解密」—— 该句在
       三份文案里都不再存在，故按 k74 留的指示把 'md' **加回** docs（方向只扩大）。
       本表永不缩小：谁把这句话写回任一文案即红。 */
    why: '明文项无需密钥即可读：users.nickname、persons.name/relation、'
       + 'sessions.emotion/tool_calls（content 才是密文）、favorites.summary、'
       + 'zeri_plans.card_json/items_json、night_lamp.text、ming_saves.surname/given、'
       + 'share_entries.content，以及 data/memory/{user_id}.json 这份未加密的记忆文件；'
       + '且 scripts/export_training_data.py、scripts/backup_db.py 可无 owner 校验地全量读/拷。'
       + '（注：session_summaries.summary/memories 与 sessions.content 确为密文，'
       + '故本句的反证面**不**包括"记忆画像全是明文列"这一说法）' },
  { re: /期满彻底删除|期满后彻底删除/,
    /* k75 新增：这句是"注销会把个人数据删干净"的笼统承诺。实测注销清理清单
       （src/storage/dao.py:cleanup_cancelled_accounts）只有 10 张表 + users 行 +
       记忆文件，zeri_plans / night_lamp / ming_saves / qian_saves / user_preferences /
       *_quota / night_prefs / jian_prefs 都不在其中 ⇒ 「彻底删除」与代码不符。
       双向锁见下方 §11d：代码一旦补齐清理范围（这些表被加进清单），本断言与
       §11d 会同时红，提示把文案改回"彻底删除"。 */
    why: '注销清理清单不含 zeri_plans / night_lamp / ming_saves / qian_saves / '
       + 'user_preferences / chat_quota / zeri_quota / ming_quota / night_prefs / jian_prefs '
       + '⇒ 注销后仍有个人数据留存，"期满彻底删除"是不实承诺' },
  { re: /不收集手机号|不收集手机号码/,
    why: '手机号在用户主动绑定时收集并 AES-256 落库（users.phone_enc），"不收集手机号"与事实相反' },
  { re: /搜狗|sogou/i,
    why: 'src/rag/web_search.py:DEFAULT_ENGINES = ("bing","so360","baidu")，搜狗实测被反爬拦截、不在默认集；'
       + '把它列为在用引擎即为不实（若日后真的启用，须同步改本守卫与三份文案）' },
  { re: /智谱[^。]{0,12}联网检索|联网检索接口/,
    why: '智谱 web search（open.bigmodel.cn）已欠费停用，_search_zhipu_legacy 无调用点（dead code）' },
];

test('k74-必修3 三份文案共用同一张"不得出现"的绝对句表（任一份复活即红）', () => {
  const broken = [];
  const scope = (f) => f.docs || ['page', 'doc', 'agree'];   // 默认三份都查
  for (const f of THREE_DOC_FORBIDDEN) {
    const s = scope(f);
    if (s.indexOf('page') !== -1 && f.re.test(PAGE)) broken.push(`privacy.wxml 出现「${f.re}」← ${f.why}`);
    if (s.indexOf('doc') !== -1 && f.re.test(DOC)) broken.push(`privacy.md 出现「${f.re}」← ${f.why}`);
    if (s.indexOf('agree') !== -1 && f.re.test(AGREE)) broken.push(`agreement.wxml 出现「${f.re}」← ${f.why}`);
  }
  assert.deepEqual(broken, [], `三份文案出现与代码不符的绝对句：\n  - ${broken.join('\n  - ')}`);
});

/* 同一张第三方清单：**两份用户可见页**（页内契约 / 用户协议）都必须逐项点名；
   提审文档 privacy.md 的粒度是「第三方服务」整节（其逐项列举由 md 自己的批次维护）。 */
const THIRD_PARTIES = [
  { key: '第三方大模型 DeepSeek', re: /DeepSeek/ },
  { key: '第三方大模型 智谱 GLM', re: /智谱/ },
  { key: '语音合成（微软 Edge / edge-tts）', re: /微软|edge-tts/i },
  { key: '公网搜索引擎', re: /公网搜索引擎|搜索引擎/ },
  { key: '微信「同声传译」插件', re: /同声传译/ },
  { key: '腾讯云（存储与备份）', re: /腾讯云/ },
  { key: 'Google Fonts（分享页字体请求）', re: /Google Fonts|fonts\.googleapis\.com/ },
];

test('k74-必修3 第三方清单：两份用户可见页（privacy.wxml / agreement.wxml）逐项齐全', () => {
  const miss = [];
  for (const p of THIRD_PARTIES) {
    if (!p.re.test(PAGE)) miss.push(`privacy.wxml 缺 ${p.key}`);
    if (!p.re.test(AGREE)) miss.push(`agreement.wxml 缺 ${p.key}`);
  }
  assert.deepEqual(miss, [], `用户可见页第三方清单漏项（外发了却没写）：${miss.join(' / ')}`);
});

test('k74-必修3 用户协议页必须指向《隐私保护指引》且披露"可不用该功能"', () => {
  assert.ok(/隐私保护指引/.test(AGREE), 'agreement.wxml 未指向《隐私保护指引》');
  assert.ok(/\/pages\/privacy\/privacy/.test(AGREEMENT_RAW),
    'agreement.wxml 未给出《隐私保护指引》的可点入口（navigator url）');
  assert.ok(/键盘代替语音|不点朗读|不分享报告/.test(AGREE),
    'agreement.wxml 未给出"不用该功能即可避免外发"的退出方式');
  assert.ok(/不会向第三方出售/.test(AGREE), 'agreement.wxml 未保留"不出售"的正面表述');
});

test('k74-必修3 三份文案都不得否认"信息会离开设备"（各文档各有正向表述）', () => {
  assert.ok(/上传|发送/.test(DOC), 'privacy.md 不再提及信息外发');
  assert.ok(/发送给第三方大模型服务商|发送到服务器/.test(PAGE), 'privacy.wxml 不再提及信息外发');
  assert.ok(/发送给第三方服务商|发送给第三方/.test(AGREE), 'agreement.wxml 不再提及信息外发');
});

/* ════════════════════════════════════════════════════════════════
   8. k74 追加：**新增外发路径**的「文案 vs 代码」外部对照
      （oracle = 源码；每条都在 k74 报告里给了改前/改后证据）
   ════════════════════════════════════════════════════════════════ */
test('k74 页面"语音播报文字发给微软语音合成"有代码支撑（/api/tts → 8768 edge-tts → 微软）', () => {
  const main = read(path.join('..', 'src', 'main.py'));
  assert.ok(/@app\.post\("\/api\/tts"\)/.test(main), '后端 /api/tts 路由不存在 → 页面播报披露失去依据');
  const cfg = read(path.join('..', 'src', 'config.py'));
  assert.ok(/TTS_UPSTREAM_BASE/.test(cfg) && /8768/.test(cfg),
    'TTS 上游配置变化 → 页面"语音合成服务/微软"须重新核对');
  const night = read(path.join('..', 'src', 'engines', 'night_soliloquy.py'));
  assert.ok(/zh-CN-XiaoyiNeural/.test(night),
    '微软神经语音 ID 消失 → 页面"微软 Edge 语音合成"披露须重新核对');
  assert.ok(/微软|edge-tts/i.test(PAGE), 'privacy.wxml 未披露语音播报会把待朗读文字发给微软语音合成');
});

test('k74 页面"检索词发给公网搜索引擎"有代码支撑（Bing/360/百度）', () => {
  const ws = read(path.join('..', 'src', 'rag', 'web_search.py'));
  assert.ok(/DEFAULT_ENGINES = \("bing", "so360", "baidu"\)/.test(ws),
    '默认启用引擎集变化 → 页面"Bing、360、百度"须同步（多写引擎与少写引擎都是不实披露）');
  assert.ok(/_search_zhipu_legacy/.test(ws),
    '智谱 web search 遗留实现消失（说明该链路被改）→ 三份文案的搜索引擎口径须重新核对');
  assert.ok(/检索词/.test(PAGE), 'privacy.wxml 未说明"由模型依提问生成的检索词"会外发');
  assert.ok(/公网搜索引擎/.test(AGREE), 'agreement.wxml 未披露公网搜索引擎');
});

test('k74 页面"分享页字体来自 Google Fonts"有代码支撑', () => {
  const hits = ['src/api/compatibility.py', 'src/api/visual_report.py']
    .map((p) => fs.readFileSync(path.join(REPO, p), 'utf8'))
    .some((s) => /fonts\.googleapis\.com/.test(s));
  assert.ok(hits, '分享/报告网页不再引用 Google Fonts → 页面与协议的 Google Fonts 披露须删除或改口径');
  assert.ok(/Google Fonts/.test(PAGE), 'privacy.wxml 未披露 Google Fonts');
});

test('k74 页面"姓名分析/取名把姓名与期望文字发给大模型"有代码支撑', () => {
  const nar = read(path.join('..', 'src', 'services', 'narrative.py'));
  assert.ok(/姓名：/.test(nar), 'narrative.py 不再把姓名拼进大模型提示词 → 页面披露须重新核对');
  const ming = read(path.join('..', 'src', 'engines', 'ming.py'));
  assert.ok(/期望: /.test(ming), 'ming.py 不再把取名期望拼进提示词 → 页面披露须重新核对');
  assert.ok(/姓名分析/.test(PAGE) && /期望/.test(PAGE),
    'privacy.wxml 未披露姓名/取名期望文字会发给大模型');
});

test('k74 页面"收藏/择日/灯语/自动汇总记录为明文存储"有代码支撑', () => {
  const fav = read(path.join('..', 'src', 'storage', 'favorite_dao.py'));
  assert.ok(/INSERT OR IGNORE INTO favorites/.test(fav) && !/encrypt/i.test(fav),
    '收藏表写库语句变化或已加密 → 页面"明文"表述须重新核对');
  const zeri = read(path.join('..', 'src', 'storage', 'zeri_dao.py'));
  assert.ok(/INSERT INTO zeri_plans/.test(zeri) && !/encrypt/i.test(zeri),
    '择日计划写库语句变化或已加密 → 页面"明文"表述须重新核对');
  const lamp = read(path.join('..', 'src', 'storage', 'lamp_dao.py'));
  assert.ok(/INSERT INTO night_lamp/.test(lamp) && !/encrypt/i.test(lamp),
    '灯语写库语句变化或已加密 → 页面"明文"表述须重新核对');
  const dao = read(path.join('..', 'src', 'storage', 'dao.py'));
  assert.ok(/nickname 明文/.test(dao), 'dao.py 未再标注 nickname 明文 → 页面披露须重新核对');
  assert.ok(/明文/.test(PAGE) && /收藏/.test(PAGE) && /灯语/.test(PAGE),
    'privacy.wxml 未如实披露明文存储项');
});

test('k74 页面"分享内容不随注销删除、不过期"有代码支撑（注销清理清单不含 share）', () => {
  const dao = read(path.join('..', 'src', 'storage', 'dao.py'));
  const m = dao.match(/for table in \(([^)]*)\)/);
  assert.ok(m, '未找到注销清理表清单（dao.py）');
  assert.ok(!/share/.test(m[1]),
    '注销清理清单已包含 share 表 → 页面"不会随账号注销一并删除"须改为"会删除"');
  assert.ok(/不会随账号注销/.test(PAGE) && /不会过期/.test(PAGE),
    'privacy.wxml 未披露分享内容不过期、不随注销删除');
});

/* ════════════════════════════════════════════════════════════════
   9. k74-必修1：`requiredBackgroundModes: ["audio"]` 零调用声明已移除
      验证依据（k74 报告）：插件官方文档的 app.json 面**只有 plugins**（0 处提及
      requiredBackgroundModes/后台播放）；textToSpeech 只返回 filename「可自行下载使用」，
      插件自己不播音频；requiredBackgroundModes 的官方定义是"需要在后台使用的能力
      （音乐播放）"，配 getBackgroundAudioManager；InnerAudioContext 文档 0 处提及该键；
      仓内唯一音频是 chat.js:_initAudio 的**前台** InnerAudioContext。
   ════════════════════════════════════════════════════════════════ */
test('k74-必修1 app.json 不再声明 requiredBackgroundModes（零调用声明）', () => {
  const appjson = JSON.parse(APPJSON_RAW);
  assert.equal(appjson.requiredBackgroundModes, undefined,
    'requiredBackgroundModes 复活：全仓零 getBackgroundAudioManager 调用；'
    + '若确需后台播放，须先补真实调用与提审说明，并同步本断言与 k74 报告依据');
  assert.ok(!/requiredBackgroundModes/.test(APPJSON_RAW),
    'app.json 仍出现 requiredBackgroundModes 字面量');
  // 反向钉住：合法能力声明不得被误删
  assert.ok(appjson.plugins && appjson.plugins.WechatSI,
    'plugins.WechatSI 是语音链路必需声明，必须保留');
  assert.ok(Array.isArray(appjson.pages) && appjson.pages.length > 0,
    'pages 声明必须保留');
});

test('k74-必修1 全仓（非测试）零后台音频 API 调用', () => {
  const BG_RE = /(getBackgroundAudioManager|BackgroundAudioManager|onBackgroundAudio\w*|playBackgroundAudio|requiredBackgroundModes)/;
  const exts = new Set(['.js', '.wxml', '.json', '.wxss']);
  const hits = [];
  const walk = (dir) => {
    for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
      if (ent.isDirectory()) {
        if (ent.name === 'node_modules' || ent.name === 'tests') continue;
        walk(path.join(dir, ent.name));
      } else if (exts.has(path.extname(ent.name))) {
        const p = path.join(dir, ent.name);
        fs.readFileSync(p, 'utf8').split('\n').forEach((line, i) => {
          if (BG_RE.test(line)) hits.push(`${path.relative(ROOT, p)}:${i + 1}`);
        });
      }
    }
  };
  walk(ROOT);
  assert.deepEqual(hits, [],
    `发现后台音频声明/调用（须重新评估 requiredBackgroundModes）：${hits.join(', ')}`);
});

/* ════════════════════════════════════════════════════════════════
   10. k74-必修2：死数据（陷阱）不得回填
   `privacy.js` 曾有 6 个零引用列表，首项即「手机号码」，与事实相反且零引用 ⇒ 已删。
   本断言防止"接线渲染 → 错误口径复活"这条路径重新出现。
   ════════════════════════════════════════════════════════════════ */
test('k74-必修2 privacy.js 不得回填零引用的「不收集手机号」式死数据列表', () => {
  const js = read('pages/privacy/privacy.js');
  // 只认「作为 data 键被定义」（`k:`），注释里提到这些名字不算（本页文件头就写了这段来历）
  const back = ['noCollectList', 'protectList', 'useList', 'noUseList', 'rightsList', 'otherList']
    .filter((k) => new RegExp(k + '\\s*:').test(js));
  assert.deepEqual(back, [],
    `privacy.js 又出现零引用死列表：${back.join(', ')}（一旦被 wxml 接线，会把与事实相反的表述带回用户眼前；`
    + '如需这份信息，请改写进 privacy.wxml 作为单一事实源）');
});

test('k74-必修2 页面不得出现"不收集手机号"式表述（手机号实为主动绑定后 AES 落库）', () => {
  const dao = read(path.join('..', 'src', 'storage', 'dao.py'));
  assert.ok(/phone_enc/.test(dao), 'dao.py 未见 phone_enc（手机号加密列）→ 该口径失去对照依据');
  assert.ok(!/不收集手机号|不收集手机号码/.test(PAGE), 'privacy.wxml 出现与事实相反的"不收集手机号"');
  assert.ok(!/不收集手机号|不收集手机号码/.test(AGREE), 'agreement.wxml 出现与事实相反的"不收集手机号"');
  assert.ok(/手机号/.test(PAGE), 'privacy.wxml 反而不再提手机号（应如实写"主动绑定才有、AES 加密存储"）');
});

/* ══════════════════════════════════════════════════════════════════════════
   ↓↓↓ k75 追加（「合并顺序收口」批：把争议项按**代码事实**重钉）↓↓↓
   本段**只新增断言**，未放宽/删除任何既有断言，也未新增 skip/xfail。
   背景：k72 批把 privacy.md 升到 v1.2（更准确），k74 批的守卫把 4 条断言钉在
   k70/k71 的**旧字面**上 ⇒ 合并后必红 4 条。控制方裁定：**守卫断言事实、
   文案如实描述**（不是把 md 调回旧字面）。上面 VOICE_FACTS / KEY_CONSISTENCY
   已按事实重钉；本段为每条事实补上**代码 oracle**（双向：代码改 → 断言先红），
   并新增两条按代码核实后发现的**新事实锁**（加密范围逐项、注销删除范围双向）。
   ══════════════════════════════════════════════════════════════════════════ */

/* ── §11a. 语音事实的代码 oracle：上传入口只收图片 + 按真实内容校验 ── */
test('k75 语音事实「服务端不接收音频 / 改名同样被拒」有代码支撑（上传白名单 + 魔数嗅探）', () => {
  const main = read(path.join('..', 'src', 'main.py'));
  // 事实①：上传入口有 content-type 白名单，且只列图片类型。
  // k72 起白名单与嗅探下沉到 src/utils/image_sniff.py（单一事实源，两个图片
  // 端点共用）—— 故 oracle 读该模块，而不是在 main.py 里找字面量。
  const sniff = read(path.join('..', 'src', 'utils', 'image_sniff.py'));
  const cts = (sniff.match(/"(image\/[a-z]+)"/g) || []).map((s) => s.slice(1, -1)).sort();
  assert.deepEqual(cts, ['image/gif', 'image/jpeg', 'image/png', 'image/webp'],
    '允许集合不再是"仅图片"四类 → 文案"只接收图片"须重新核对');
  assert.ok(/from \.utils\.image_sniff import/.test(main),
    'main.py 不再共用嗅探单一事实源 → 文案"按真实内容校验"须重新核对');
  // 事实②：按**真实内容**校验（魔数嗅探），不是只看文件名/声明的类型
  assert.ok(/def sniff_image_format/.test(sniff) && /b"\\xff\\xd8\\xff"/.test(sniff),
    '魔数嗅探实现消失 → 文案"按真实内容校验 / 改名的音频同样会被拒绝"失去依据');
  assert.ok(/不是有效图片/.test(main),
    '415 拒收分支消失 → 文案"会被拒绝"失去依据');
  assert.ok(/uuid\.uuid4\(\)\.hex/.test(main),
    '服务端随机文件名消失 → 文案"以服务端随机生成的文件名保存"须重新核对');
  // md 侧：事实必须**两半都在**（只写"不保存录音"而不写"服务端不收音频"即是漏披露）
  assert.ok(/只接收图片/.test(DOC) && /改名的音频文件同样会被拒绝/.test(DOC),
    'privacy.md 未同时写明"上传入口只接收图片"与"改名的音频文件同样会被拒绝"');
});

/* ── §11b. 加密范围：md 必须逐项说明，且与代码逐项一致 ── */
test('k75 加密范围事实锁：md 载明"部分字段加密 + 明文项逐项列出"，且与代码一致', () => {
  // ① 文案侧：加密算法与范围 + 明确列出不额外加密的项
  assert.ok(/AES-256-GCM/.test(DOC), 'privacy.md 未写明加密算法与模式（AES-256-GCM）');
  assert.ok(/加密后存储在中国大陆境内的腾讯云服务器上/.test(DOC),
    'privacy.md 未写明加密数据的存储位置');
  assert.ok(/(按原样存储|不额外加密)/.test(DOC),
    'privacy.md 未如实说明"哪些字段不额外加密"（只写"全部 AES-256 加密"即为夸张表述）');
  ['昵称', '称呼与关系', '收藏', '图片'].forEach((k) => {
    assert.ok(DOC.indexOf(k) !== -1, `privacy.md 的加密范围段未提及明文项「${k}」`);
  });
  // ② 代码侧 oracle：确有条目在写库前加密（否则"部分加密"这句本身就是错的）
  const dao = read(path.join('..', 'src', 'storage', 'dao.py'));
  const sess = read(path.join('..', 'src', 'storage', 'session_dao.py'));
  assert.ok(/_encrypt_text\(phone\)/.test(dao), '手机号不再加密写库 → md 的"手机号加密"须改');
  assert.ok(/_encrypt_text\(bazi_json\)|_encrypt_text\(json\.dumps\(bazi_info/.test(dao),
    '八字档案不再加密写库 → md 的"出生信息加密"须改');
  assert.ok(/_encrypt_text\(content\)/.test(sess), '对话正文不再加密写库 → md 的"对话加密"须改');
  // ③ 代码侧 oracle：确有条目为**明文**（否则"明文项"半句是错的）
  const PLAIN = [
    ['favorite_dao.py', 'favorites（收藏摘要）'],
    ['zeri_dao.py', 'zeri_plans（择日计划与其备注）'],
    ['lamp_dao.py', 'night_lamp（灯语）'],
    ['ming_dao.py', 'ming_saves（姓名与取名保存）'],
  ];
  PLAIN.forEach(([f, label]) => {
    const s = read(path.join('..', 'src', 'storage', f));
    assert.ok(!/encrypt/i.test(s),
      `${label} 已改为加密落库 → md 的"明文"表述须同步更正（本条即为此而设）`);
  });
  const userMem = read(path.join('..', 'src', 'memory', 'user_memory.py'));
  const pos = userMem.indexOf('json.dump(data, f');
  assert.ok(pos !== -1,
    '记忆文件的明文落盘写法消失（json.dump(data, f)）→ md 的"自动汇总记录未加密"须重新核对');
  assert.ok(!/encrypt/i.test(userMem.slice(Math.max(0, pos - 400), pos + 200)),
    '记忆文件的落盘改为加密 → md 的"自动汇总记录未加密"表述须同步更正'
    + '（本条即为此而设：加密面一变，文案必须跟着变）');
});

/* ── §11c. 训练口径：脱敏面逐项钉在导出脚本上 ── */
test('k75 训练口径事实锁：md 写明"去除身份标识后"与"未脱敏不训练"，脱敏面与脚本一致', () => {
  assert.ok(/去除身份标识后\*{0,2}的数据改进/.test(DOC),
    'privacy.md 未写明用于改进/训练的数据已去除身份标识');
  assert.ok(/未经脱敏的记录不会用于训练/.test(DOC),
    'privacy.md 未写明"未经脱敏的记录不会用于训练"（这句是控制方点名的口径）');
  const s = read(path.join('..', 'scripts', 'export_training_data.py'));
  assert.ok(/def desensitize/.test(s), 'export_training_data.py 的 desensitize() 消失');
  assert.ok(/\[openid\]/.test(s) && /\[手机号\]/.test(s) && /\[证件号\]/.test(s)
    && /\[邮箱\]/.test(s) && /\[数字\]/.test(s),
    '脱敏替换面变化（openid/手机号/证件号/邮箱/长数字串）→ md 第四节的脱敏面须同步更正');
  assert.ok(/训练集|微调/.test(s), 'export_training_data.py 不再是训练集导出脚本');
});

/* ── §11d. 注销删除范围：文案 ↔ 代码 双向锁 ── */
test('k75 注销删除范围双向锁：清理清单 = 代码实况，文案逐项如实说明', () => {
  const dao = read(path.join('..', 'src', 'storage', 'dao.py'));
  const m = dao.match(/for table in \(([^)]*)\)/);
  assert.ok(m, '未找到注销清理表清单（src/storage/dao.py:cleanup_cancelled_accounts）');
  const CLEANED = ['consultations', 'sessions', 'session_summaries', 'memberships',
    'payments', 'push_log', 'persons', 'chart_records', 'favorites', 'jian_cards'];
  const NOT_CLEANED = ['zeri_plans', 'night_lamp', 'ming_saves', 'qian_saves', 'user_preferences'];
  CLEANED.forEach((t) => {
    assert.ok(new RegExp(`"${t}"`).test(m[1]),
      `注销清理清单不再包含 ${t} → 文案"该内容随注销删除"须改为"不删除"（双向锁）`);
  });
  NOT_CLEANED.forEach((t) => {
    assert.ok(!new RegExp(`"${t}"`).test(m[1]),
      `注销清理清单**已**纳入 ${t} → 代码已补齐删除范围，请把三份文案改回`
      + `"随注销删除"并删除本条与 THREE_DOC_FORBIDDEN 的「期满彻底删除」禁令`
      + `（本条断言的目的是：代码一改，文案必须跟着改，反之亦然）`);
  });
  // 文案侧：三处（md 第一节第 12 条 / 第五节第 3 条 / 第六节）必须如实说明保留项
  assert.ok(/不在注销删除范围内/.test(DOC),
    'privacy.md 未如实说明"有下列内容不在注销删除范围内"');
  /* 「逐项 + 就近」断言（V2 摘除实测加严）：只查"文档里有没有这句话"是不够的 ——
     实测把第 12 条改回笼统的「并随账号删除一并删除」后，全篇仍可能因别处（第 11 条）
     含"不在注销删除范围内"而假绿。故改为**逐项就近**：每个保留项的**首次出现**处
     前后窗口内必须同时有"不会被注销删除"的如实说明。 */
  ['姓名分析与取名的保存记录', '择日计划', '求签', '灯语'].forEach((k) => {
    const i = DOC.indexOf(k);
    assert.ok(i !== -1, `privacy.md 的注销范围段未列出保留项「${k}」`);
    const win = DOC.slice(Math.max(0, i - 120), i + 300);
    assert.ok(/不在注销删除范围内|未纳入注销删除清单|不在注销清理范围/.test(win),
      `privacy.md 提到保留项「${k}」的附近没有"不会被注销删除"的如实说明`
      + '（改回笼统的"随账号删除一并删除"即触发本断言）');
  });
  // 第 12 条的核心事实：四项保留内容里**只有「收藏」**会随注销删除（favorites 在清理清单内）
  assert.ok(/只有「收藏」[^。]{0,50}删除/.test(DOC),
    'privacy.md 第 12 条未如实说明"四项保留内容里只有收藏会随注销删除"（favorites 在清单内、'
    + 'ming_saves/zeri_plans/night_lamp/qian_saves 不在）');
  ['姓名与取名记录', '择日计划', '求签记录', '灯语'].forEach((k) => {
    assert.ok(PAGE.indexOf(k) !== -1, `privacy.wxml 未列出注销后仍保留的「${k}」`);
  });
  assert.ok(/不在注销清理范围/.test(PAGE),
    'privacy.wxml 未如实说明注销后仍有内容保留（旧版写"期满彻底删除"）');
  // 反向：不得再用"删干净"的笼统说法
  ['期满彻底删除', '全部个人数据将在 48 小时内', '所有个人数据将在 48 小时内'].forEach((s) => {
    assert.ok(DOC.indexOf(s) === -1, `privacy.md 出现与代码不符的笼统删除承诺「${s}」`);
    assert.ok(PAGE.indexOf(s) === -1, `privacy.wxml 出现与代码不符的笼统删除承诺「${s}」`);
  });
});
