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
const { execFileSync } = require('node:child_process');

const ROOT = path.join(__dirname, '..');
const read = (rel) => fs.readFileSync(path.join(ROOT, rel), 'utf8');
/** 仓根（本工作树）——用于读 src/ 后端源码做「文案 vs 代码」外部对照 */
const REPO = path.join(ROOT, '..');

/* ── k77-M1/M2：**行为型** oracle 的执行入口 ──────────────────────────────
   复审判定（M-1/M-2）：本文件里若干"有代码支撑"的断言是**存在性/读源码文本**型
   —— 实测注入 `if data[:3]==b"ID3": return "jpeg"` 后守卫仍全绿（因为 `def
   sniff_image_format` 与 `b"\xff\xd8\xff"` 仍在源码里）；往 `_archive_free_record`
   的 chart 里注入生辰、docstring 原样保留则守卫也全绿（因为断言读的是注释文本）。
   修法：这些事实改成**跑代码看结果**（真调用函数/真落库再读回），
   而不是在源码里找字符串。源码文本断言**一条不删**（它们仍能拦住"整体删除"），
   行为断言叠加其上 —— 判别力只增不减。
   `py()` 在仓根下真跑 python3；python3 不可用即**报错**（不 skip、不静默降级）。 */
function py(code, timeout = 120000) {
  return execFileSync('python3', ['-c', code], {
    cwd: REPO, encoding: 'utf8', timeout,
    env: Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8' }),
  });
}
function pyJson(code, timeout = 120000) {
  const raw = py(code, timeout);
  const last = raw.trim().split('\n').pop();
  return JSON.parse(last);
}
/*: k79：带**额外环境变量**的 pyJson（禁语表等规则表经 env 传入，避免把带转义的
   正则源码嵌进模板字符串再解析一层）。 */
function pyJsonEnv(code, extraEnv, timeout = 120000) {
  const raw = execFileSync('python3', ['-c', code], {
    cwd: REPO, encoding: 'utf8', timeout,
    env: Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8' }, extraEnv),
  });
  const last = raw.trim().split('\n').pop();
  return JSON.parse(last);
}

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

/* k77 注（判别力归属）：同 §11a —— 本用例读的是 union.py 的源码文本（函数名 +
   "绝不写双方生辰"这句注释），复审实测：往归档 chart 里注入双方生辰、注释原样保留，
   本用例仍全绿。真正有牙的是下面的 `k77-M2（行为型）`（真归档再读回库，注入即红）。 */
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
  /* k77 移出（控制方 A3 裁定）：此处原有**无条件禁语**「期满彻底删除|期满后彻底删除」
     —— 事实依据是"注销清理清单只有 10 张表 + 记忆文件，其余都不在"。
     k76 已把清理范围补齐（models.ACCOUNT_PURGE_TABLES，除支付流水外全删），
     于是"期满删除"这句话**本身变成了事实**，只是**必须带法定留存例外**。
     按 A3 把"绝对句"改成"带例外的准确句"：该模式已移入 §12 的全仓条件规则
     （`unlessNear: /支付流水|依法留存|法定期限|电子商务法/`，例外必须**就近**出现）。
     这不是放宽：绝对句仍然红（例外不在附近就红），且扫描面从三份文件扩到全仓；
     双向实测见 k77 报告「注入证明」A3-a（带例外→绿）/ A3-b（无例外→红）。 */
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

/* k77 重钉（控制方裁定「守卫断言事实，不断言旧字面」）：
   本用例原先断言的事实是"分享内容**不随注销删除、不过期**"（k74 时点属实）。
   k76 把这批控制补齐了：① 分享链接有 30 天有效期（`share_entries.expires_at`
   + `FORTUNE_SHARE_TTL_DAYS`）；② 注销时**立即删除**该账号的分享行
   （`cancel_user` → `ShareDAO.delete_by_owner`）并把 share 表纳入清理清单。
   于是原断言（含它引用的 `for table in (...)` 内联清单写法）整体**过时** ——
   k77 合并 k76 后必然先红，正是 k76 预告的"合并时必然先红"那条。
   重钉方向：**同一段代码 oracle 改成断言新事实** + 页面文案改成新事实，
   并且把 oracle 从"读 dao.py 源码文本"换成**真跑存储层看行为**（M-1/M-2 同款要求：
   源码文本会漂移，行为不会）。判别力只增：改前"文案说不过期"就绿，
   现在"文案说 30 天 + 注销即删"**且**存储层实测确实如此才绿。 */
test('k74→k77 页面"分享有 30 天有效期、注销时立即删除"有代码支撑（行为型 oracle）', () => {
  const out = pyJson(`
import sys, json, os, tempfile, time
sys.path.insert(0, '.')
from src.storage.share_dao import ShareDAO
from src.storage.models import init_db, connect as db_connect
tmp = tempfile.mkdtemp()
db = os.path.join(tmp, 'k77share.db')
init_db(db)
d = ShareDAO(db_connect(db))
now = time.time()
d.insert('live0001', {"q": "x"}, owner_tag='tag-k77')
d.insert('dead0001', {"q": "y"}, owner_tag='tag-k77')
# 造一条"已过期"的行（直接把 expires_at 挪到过去）
d.conn.execute("UPDATE share_entries SET expires_at=? WHERE id='dead0001'", (now - 10,))
d.conn.commit()
live_state, live_entry = d.get_state('live0001')
dead_state, dead_entry = d.get_state('dead0001')
row = d.conn.execute("SELECT expires_at, created_at FROM share_entries WHERE id='live0001'").fetchone()
removed = d.delete_by_owner('tag-k77')
left = d.conn.execute("SELECT COUNT(*) FROM share_entries").fetchone()[0]
print(json.dumps({
  "live_state": live_state, "live_has_content": bool(live_entry),
  "dead_state": dead_state, "dead_has_content": bool(dead_entry),
  "ttl_seconds": (row[0] - row[1]) if row else None,
  "deleted_by_owner": removed, "left_after_cancel": left,
}))
`);
  // 代码侧（行为）：有效期真的存在，且默认 = 30 天（与文案的"30 天"同源）
  assert.equal(out.live_state, 'ok', '分享行写入后竟不可读（get_state 非 ok）');
  assert.equal(out.dead_state, 'expired', '过期行没有被判为 expired（有效期形同虚设）');
  assert.equal(out.dead_has_content, false, '过期行仍能读出内容（fail-closed 被破坏）');
  assert.equal(Math.round(out.ttl_seconds / 86400), 30,
    `分享有效期不是 30 天（实测 ${out.ttl_seconds} 秒）→ 页面"30 天后自动失效"须同步`);
  assert.equal(out.deleted_by_owner, 2, '注销路径的 delete_by_owner 没有删掉该用户的分享行');
  assert.equal(out.left_after_cancel, 0, '注销后分享行仍有残留');
  // 文案侧：页面与提审文档都必须写新事实（旧事实"不会过期/不随注销删除"由 §12 全仓禁语扫）
  assert.ok(/30 天|30天/.test(PAGE), 'privacy.wxml 未写明分享链接有效期（30 天）');
  assert.ok(/注销时.*(立即)?删除|注销.*立即删除|随账号注销.*删除/.test(PAGE),
    'privacy.wxml 未写明分享内容随注销删除（改前写的是"不会随注销删除"）');
  assert.ok(/30 天/.test(DOC), 'privacy.md 未写明分享链接有效期（30 天）');
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
/* k77 注（判别力归属）：本用例是**结构存在性**检查（读 main.py/image_sniff.py 的源码
   文本）—— 注入实测（k77 报告「注入证明 M-1」）证明：把嗅探改成
   `if data[:3]==b"ID3": return "jpeg"` 之后，**本用例仍全绿**。
   真正有牙的是它下面那条 `k77-M1（行为型）`（真调函数看返回值，同一注入即红）。
   两条都保留：本条拦"整体删除"，行为型那条拦"改坏语义"。 */
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

/* ── §11a-b。k77-M1：**行为型** oracle —— 真调用嗅探函数，看返回值 ──────
   为什么要有它（复审 M-1 原始证据）：把 `sniff_image_format` 改成
   `if data[:3]==b"ID3": return "jpeg"`（即"录音改名上传会被当图片放行"，
   正是文案承诺的反面），上面那条读源码文本的断言**仍然全绿** ——
   因为 `def sniff_image_format` 与 `b"\xff\xd8\xff"` 都还在源码里。
   本用例直接执行该函数并检查**返回值**：注入即红（见 k77 报告注入实测）。
   覆盖：真 MP3(ID3v2 头)/真 PNG 伪装成音频/空/极短。 */
test('k77-M1（行为型）嗅探函数对"改名音频/伪装内容"的实际返回值', () => {
  const out = pyJson(`
import sys, json
sys.path.insert(0, '.')
from src.utils.image_sniff import sniff_image_format, sniff_image_ext
mp3 = bytes.fromhex('49443304000000000000') + b'\\x00' * 64   # "ID3" 头（真 MP3）
jpeg = bytes.fromhex('ffd8ffe0') + b'\\x00' * 64
png = bytes.fromhex('89504e470d0a1a0a') + b'\\x00' * 64
print(json.dumps({
  "mp3_format": sniff_image_format(mp3),
  "mp3_ext": sniff_image_ext(mp3),
  "jpeg_format": sniff_image_format(jpeg),
  "png_format": sniff_image_format(png),
  "empty_format": sniff_image_format(b""),
  "short_format": sniff_image_format(bytes.fromhex('ff')),
}))
`);
  assert.equal(out.mp3_format, '',
    '改名的音频（ID3 头）被嗅探当成图片放行了 —— 与"改名的音频同样会被拒绝"直接矛盾');
  assert.equal(out.mp3_ext, '', '音频被给出了图片扩展名（服务端会落盘成图片）');
  assert.equal(out.jpeg_format, 'jpeg', '真 JPEG 未被识别（上传功能被误伤）');
  assert.equal(out.png_format, 'png', '真 PNG 未被识别（上传功能被误伤）');
  assert.equal(out.empty_format, '', '空内容竟被嗅探成了图片');
  assert.equal(out.short_format, '', '不足魔数长度的内容竟被嗅探成了图片');
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
  /* k77-M7：补两项此前漏掉的明文项（复审点名）——
     `qian_saves`（求签保存记录，src/storage/qian_dao.py）与
     `share_entries.content`（分享出去的对话正文，src/storage/share_dao.py）。
     漏项的后果是双重的：md/页面可以悄悄少写一项明文（漏披露），
     而这里也不会红。补上后，这两张表若被改成加密，md 与页面会被要求同步更正。 */
  const PLAIN = [
    ['favorite_dao.py', 'favorites（收藏摘要）'],
    ['zeri_dao.py', 'zeri_plans（择日计划与其备注）'],
    ['lamp_dao.py', 'night_lamp（灯语）'],
    ['ming_dao.py', 'ming_saves（姓名与取名保存）'],
    ['qian_dao.py', 'qian_saves（求签保存记录）'],
  ];
  PLAIN.forEach(([f, label]) => {
    const s = read(path.join('..', 'src', 'storage', f));
    assert.ok(!/encrypt/i.test(s),
      `${label} 已改为加密落库 → md 的"明文"表述须同步更正（本条即为此而设）`);
  });
  /* share_dao 不能用"文件里没有 encrypt 字样"判定 —— 该文件的 owner_tag 走的是
     `encrypt_user_id`（HMAC 伪名，属于**归属标记**而非内容加密）。故对
     `share_entries.content` 改用**行为型**判定：真写一条分享，再把**数据库原始值**
     读出来看哨兵是否明文可见（需解密才可见 = 已加密 → 红）。 */
  const sharePlain = pyJson(`
import sys, json, os, tempfile
sys.path.insert(0, '.')
from src.storage.share_dao import ShareDAO
from src.storage.models import init_db, connect as db_connect
tmp = tempfile.mkdtemp()
db = os.path.join(tmp, 'k77s.db')
init_db(db)
d = ShareDAO(db_connect(db))
d.insert('k77plain', {"q": "K77-PLAIN-SENTINEL", "a": "answer"}, owner_tag='')
raw = d.conn.execute("SELECT content FROM share_entries WHERE id='k77plain'").fetchone()[0]
print(json.dumps({"raw": raw}))
`);
  assert.ok(sharePlain.raw.indexOf('K77-PLAIN-SENTINEL') !== -1,
    'share_entries.content 落库后读不出明文哨兵 → 分享正文已被加密，'
    + 'md/页面的"分享出去的对话正文为明文"表述须同步更正');
  // 明文项清单本身：md 逐项点名（漏一项 = 漏披露），页面同口径（k77-I2）
  ['求签保存记录', '分享出去的对话正文', '姓名分析与取名的保存记录'].forEach((k) => {
    assert.ok(DOC.indexOf(k) !== -1, `privacy.md 的明文项清单未提及「${k}」（漏披露）`);
  });
  ['求签保存记录', '分享出去的对话正文', '姓名分析与取名的保存记录'].forEach((k) => {
    assert.ok(PAGE.indexOf(k) !== -1, `privacy.wxml 的明文项清单未提及「${k}」（漏披露）`);
  });
  const userMem = read(path.join('..', 'src', 'memory', 'user_memory.py'));
  const pos = userMem.indexOf('json.dump(data, f');
  assert.ok(pos !== -1,
    '记忆文件的明文落盘写法消失（json.dump(data, f)）→ md 的"自动汇总记录未加密"须重新核对');
  assert.ok(!/encrypt/i.test(userMem.slice(Math.max(0, pos - 400), pos + 200)),
    '记忆文件的落盘改为加密 → md 的"自动汇总记录未加密"表述须同步更正'
    + '（本条即为此而设：加密面一变，文案必须跟着变）');
});

/* ── §11b-b。k77-M2：**行为型** oracle —— 脱敏归档真的落库了什么？ ──────
   复审 M-2 原始证据：上面那条断言读的是 `union.py` 的**源码文本**
   （`_archive_free_record` 这个名字 + "绝不写双方生辰"这句注释）。
   往 `chart` 里塞双方生辰、注释原样保留 → 守卫全绿，而"服务器不留存双方生辰"
   这句对用户的承诺已经被违反。
   本用例**真跑归档**：临时库 + 带生辰/姓名/出生地的入参 → 调用归档 → 读回落库行，
   断言里面**找不到任何**生辰/姓名/出生地痕迹（含解密后）。
   注入即红（见 k77 报告注入实测）。 */
test('k77-M2（行为型）免费档合盘归档：落库内容里不得出现双方生辰/姓名/出生地', () => {
  const out = pyJson(`
import sys, json, os, tempfile
sys.path.insert(0, '.')
tmp = tempfile.mkdtemp()
db = os.path.join(tmp, 'k77.db')
from src.storage.models import init_db
init_db(db)
from src.storage.dao import UserDAO
import src.api.union as union_mod
dao = UserDAO(db)
union_mod._dao = dao            # 归档写入的目标（与生产同一段代码）
# 入参刻意带上"绝不该落库"的双方生辰/姓名/出生地（哨兵值，便于全库搜索）
union_result = {
    "score": 88, "levelLabel": "上上", "levelSublabel": "天作之合",
    "relation": "恋人", "dimensions": {"a": 80, "b": 90}, "yuan_card": {"x": 1},
    "a_birth": {"year": 1978, "month": 3, "day": 14, "hour": 9, "minute": 30,
                "city": "喀什市", "name": "张三丰"},
    "b_birth": {"year": 1982, "month": 11, "day": 2, "hour": 21, "minute": 5,
                "city": "齐齐哈尔", "name": "李四娘"},
}
quote = {"line": "缘定三生"}
union_mod._archive_free_record('u-k77', union_result, quote)
# 读回该用户的合盘归档（与 /api/union/history 同一条读取路径）
records = dao.get_user_hehun_records('u-k77', limit=50)
raw = json.dumps(records, ensure_ascii=False, default=str)
# 也把明文存储的整行 dump 出来搜（防止归档改写到别的列）
conn = dao._connect()
rows = conn.execute('SELECT user_id, question, intent, chart_data, analysis FROM consultations WHERE user_id=?', ('u-k77',)).fetchall()
conn.close()
dump = json.dumps([list(r) for r in rows], ensure_ascii=False, default=str)
from src.storage.dao import _decrypt_or_plain
decrypted = ' | '.join(_decrypt_or_plain(r[3]) or '' for r in rows)
print(json.dumps({
  "record_count": len(records),
  "haystack": raw + ' ' + dump + ' ' + decrypted,
  "qian": _decrypt_or_plain(rows[0][1]) if rows else '',
}))
`);
  assert.equal(out.record_count, 1, '免费档合盘归档没有落库（归档链路断了，本 oracle 也就失去意义）');
  const hay = out.haystack;
  ['喀什市', '齐齐哈尔', '张三丰', '李四娘', '1978', '1982',
    '03-14', '11-02', '09:30', '21:05'].forEach((sentinel) => {
    assert.ok(hay.indexOf(sentinel) === -1,
      `归档里出现了「${sentinel}」——"服务器不留存双方生辰/出生地/姓名"的承诺被违反`);
  });
  assert.ok(out.qian.indexOf('88') !== -1 || out.qian.indexOf('上上') !== -1,
    '归档应只保留脱敏摘要（得分/等级）——摘要本身缺失说明 oracle 的取样点错了');
});
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

/* ── §11d. 注销删除范围：文案 ↔ 代码 双向锁（k77 重钉到新事实）──────────
   k76 补齐了注销删除范围（`models.ACCOUNT_PURGE_TABLES` 单一事实源，把原先漏掉的
   zeri_plans / night_lamp / ming_saves / qian_saves / user_preferences / *_quota /
   night_prefs / jian_prefs / share_entries 全部纳入），**唯一依法保留的是支付流水**
   （`ACCOUNT_RETAIN_TABLES`：payments / midas_orders，《电子商务法》第 31 条）。
   于是 k75 版断言的两条核心事实都反了：
     ① 原「四项保留内容里只有「收藏」会随注销删除」→ 现在四项**全删**；
     ② 原「代码清单用 `for table in (...)` 内联写法」→ 已上移到 models 单一事实源。
   重钉方式（控制方红线：重钉必须有代码依据、判别力不降、附注入证明）：
     a) oracle 从"读 dao.py 源码文本"→ **真跑清理，看还剩哪些行**（行为型）；
     b) 断言面从"清单里有没有某个表名"→ "清理后**除依法留存表外一行不剩**"
        （更强：漏掉任何一张表都会红，而旧写法只看写没写名字）；
     c) 文案侧从"保留项的附近必须有'不删除'说明"→ "四项内容的附近必须是**删除**说法、
        且不得再出现'不在注销删除范围内'指向它们"（方向反转，同样逐项就近）。 */
test('k75→k77 注销删除范围双向锁：除依法留存外一行不剩（行为型）+ 文案逐项如实说明', () => {
  /* k77 加固（注入实测发现的洞）：第一版 oracle 只对**清单里**的表查残留 ——
     实测把 `ming_saves` 从清单里删掉后守卫仍全绿（清单变短，检查面跟着变短）。
     现改为**从库结构派生**：枚举库里所有带归属列（user_id / owner_tag）的表，
     逐表塞一行 → 跑清理 → 断言"除依法留存表外一行不剩"。
     于是①漏删任何一张表 → 红（哪怕它被从清单里删掉）；②多删依法留存表 → 红；
     ③误删他人 → 红。检查面由**库**决定，不由清单决定。 */
  const out = pyJson(`
import sys, json, os, tempfile
sys.path.insert(0, '.')
from src.storage.models import (init_db, connect as db_connect, ACCOUNT_PURGE_TABLES,
                                ACCOUNT_RETAIN_TABLES)
tmp = tempfile.mkdtemp()
db = os.path.join(tmp, 'k77purge.db')
init_db(db)                       # models.SCHEMA 里的表
# 其余业务表由各 DAO 自建（与 main.py lifespan 的装配同款）——临时库要长成"生产库的样子"
conn = db_connect(db)
from src.storage.chart_dao import ChartDAO
from src.storage.favorite_dao import FavoriteDAO
from src.storage.jian_dao import JianPrefDAO
from src.storage.ming_dao import MingDAO
from src.storage.qian_dao import QianDAO
from src.storage.zeri_dao import ZeriDAO
from src.storage.night_dao import NightPrefDAO
from src.storage.lamp_dao import LampDAO
from src.storage.chat_quota_dao import ChatQuotaDAO
from src.storage.share_dao import ShareDAO
from src.storage.member_dao import MemberDAO
import src.api.pay_midas as midas_mod
ChartDAO(db); FavoriteDAO(db); JianPrefDAO(conn); MingDAO(conn); QianDAO(conn)
ZeriDAO(conn); NightPrefDAO(conn); LampDAO(conn); ChatQuotaDAO(db); ShareDAO(conn)
midas_mod.setup(MemberDAO(db))    # 与 main.py lifespan 同款：建 midas_orders 表

def cols_of(t):
    return [c[1] for c in conn.execute("PRAGMA table_info(%s)" % t).fetchall()]

def owner_col(t):
    c = cols_of(t)
    if "user_id" in c:
        return "user_id"
    if "owner_tag" in c:
        return "owner_tag"
    return None

def seed(t, key_col, key_val):
    """给表塞一行（非主键列按类型填占位值；表不存在 → 返回 False）"""
    if not cols_of(t):
        return False
    names, vals = [], []
    for c in conn.execute("PRAGMA table_info(%s)" % t).fetchall():
        name, ctype, _nn, _df, pk = c[1], (c[2] or "").upper(), c[3], c[4], c[5]
        # 只跳过**自增整型主键**；TEXT 主键（如 ming_quota.user_id PRIMARY KEY）
        # 必须显式给值，否则 NOT NULL 约束报错（=种子行造不出来，oracle 失效）
        if pk and "INT" in ctype:
            continue
        names.append(name)
        if name == key_col:
            vals.append(key_val)
        elif "INT" in ctype:
            vals.append(0)
        elif "REAL" in ctype or "FLOA" in ctype or "DOUB" in ctype:
            vals.append(0.0)
        else:
            # 占位值**按归属值区分**：否则同一张表的两行（本人/控制组）会在
            # UNIQUE 列上撞车（如 midas_orders.out_trade_no、share_entries.id 主键）
            vals.append("k77seed:" + str(key_val))
    conn.execute("INSERT INTO %s (%s) VALUES (%s)" % (t, ",".join(names), ",".join("?" * len(names))), vals)
    return True

# share_entries 不存 user_id，按 HMAC 伪名归属（share_dao 红线）；种子行必须用
# **同一个派生函数**算出标记，否则删除当然命中不了（那是 oracle 造错，不是代码错）
from src.storage.share_dao import owner_tag_for
ME, KEEP = "u-k77", "u-keep"
TAG_ME, TAG_KEEP = owner_tag_for(ME), owner_tag_for(KEEP)

TABLES = sorted(r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall())
missing, seeded, no_owner = [], [], []
for t in TABLES:
    kc = owner_col(t)
    if not kc:
        no_owner.append(t)
        continue
    mine = TAG_ME if kc == "owner_tag" else ME
    other = TAG_KEEP if kc == "owner_tag" else KEEP
    ok1 = seed(t, kc, mine)
    seed(t, kc, other)          # 控制组：另一个用户的行（绝不能被误删）
    (seeded if ok1 else missing).append(t)
conn.commit()

from src.storage.dao import purge_account_data
stats = purge_account_data(conn, ME, purge_files=False)

left, kept = {}, {}
for t in TABLES:
    kc = owner_col(t)
    if not kc:
        continue
    mine = TAG_ME if kc == "owner_tag" else ME
    other = TAG_KEEP if kc == "owner_tag" else KEEP
    left[t] = conn.execute("SELECT COUNT(*) FROM %s WHERE %s=?" % (t, kc), (mine,)).fetchone()[0]
    kept[t] = conn.execute("SELECT COUNT(*) FROM %s WHERE %s=?" % (t, kc), (other,)).fetchone()[0]
listed_missing = [t for t, _c in ACCOUNT_PURGE_TABLES if t not in TABLES]
listed_missing += [t for t, _c, _w in ACCOUNT_RETAIN_TABLES if t not in TABLES]
print(json.dumps({
  "tables": TABLES, "seeded": seeded, "missing_tables": missing,
  "listed_missing": listed_missing,
  "no_owner_col": no_owner, "left": left, "kept": kept,
  "purge_listed": [t for t, _c in ACCOUNT_PURGE_TABLES],
  "retain_listed": [t for t, _c, _w in ACCOUNT_RETAIN_TABLES],
  "retained_basis": {t: why for t, _c, why in ACCOUNT_RETAIN_TABLES},
  "deleted_rows": stats.get("deleted_rows"),
}))
`, 240000);

  /* ① 库结构前提：本次检查面 = 库里**所有**带归属列的表（由库决定，不由清单决定） */
  assert.deepEqual(out.no_owner_col, [],
    `下列表没有 user_id/owner_tag 归属列，本 oracle 覆盖不到：${out.no_owner_col}`
    + '（若新增这类存个人数据的表，请同时把检查方式补上）');
  assert.ok(out.tables.length >= 25,
    `临时库只建出 ${out.tables.length} 张表（改前实测 25 张）——建表装配可能漏了 DAO`);
  /* ② 清单没写过期的表名（清单里的表必须真的存在；唯一允许的例外是遗留表
     `user_tone_feedback`：models.py 注明代码里没有建表语句、生产库却有该表）。
     例外是**双向钉**的：一旦代码里出现它的建表语句，本断言翻红要求重新核对。 */
  const allowedAbsent = ['user_tone_feedback'];
  const unexpected = out.listed_missing.filter((t) => allowedAbsent.indexOf(t) === -1);
  assert.deepEqual(unexpected, [],
    `清理/留存清单列了库里没有的表 ${unexpected}（清单与建表脚本已漂移）`);
  assert.ok(out.listed_missing.indexOf('user_tone_feedback') !== -1,
    'user_tone_feedback 现在能建出来了 —— 请把它从"允许缺席"名单里去掉（名单过期即红）');
  const modelsSrc = fs.readFileSync(path.join(REPO, 'src', 'storage', 'models.py'), 'utf8');
  assert.ok(modelsSrc.indexOf('CREATE TABLE IF NOT EXISTS user_tone_feedback') === -1,
    'user_tone_feedback 已有建表语句，与"遗留表（无建表语句）"的判定矛盾');
  /* ③ 行为（**核心**）：清理后，除依法留存表外一行不剩 —— 覆盖面 = 库里的归属表 */
  const retained = ['midas_orders', 'payments', 'users'];   // users 由 purge 收尾单独删
  const leftovers = Object.keys(out.left)
    .filter((t) => out.left[t] > 0 && retained.indexOf(t) === -1);
  assert.deepEqual(leftovers, [],
    `注销清理后仍有残留：${leftovers.map((t) => t + '=' + out.left[t]).join(', ')}`
    + '（每张有归属列的表都必须被清干净：要么进 ACCOUNT_PURGE_TABLES，'
    + '要么在 ACCOUNT_RETAIN_TABLES 里写明法定依据——双向锁）');
  assert.equal(out.left.users, 0, '注销清理没有删掉 users 行（登录拦截/归属都将失去依据）');
  /* ④ 依法留存：只有这两张（且必须有法条依据）；名单与文案必须一致 */
  assert.deepEqual(out.retain_listed.slice().sort(), ['midas_orders', 'payments'],
    '依法留存表集合变了 → 文案"唯一依法留存的是支付流水"须同步');
  ['midas_orders', 'payments'].forEach((t) => {
    assert.equal(out.left[t], 1, `依法留存表 ${t} 的行被误删了`);
    assert.ok(/电子商务法/.test(out.retained_basis[t] || ''),
      `依法留存表 ${t} 缺法条依据（文案里对用户的解释正是这条依据）`);
  });
  /* ⑤ 控制组：别人的行一行不能少（清理必须只作用于目标用户） */
  const stolen = Object.keys(out.kept).filter((t) => out.kept[t] === 0);
  assert.deepEqual(stolen, [],
    `注销清理误删了**其他用户**在下列表里的行（严重越界）：${stolen.join(', ')}`);
  // ⑤ 文案侧（新事实）：四项内容都在**删除**说法附近，且不再挂"不删除"
  /* "不删除"式表述本身**不是禁语** —— 现在它是**支付流水**的正确说法
     （唯一依法留存项）。禁的是把它按在四项内容上。故逐条判定时看**该句自身附近**
     有没有依法留存的依据：有依据（=说的是支付流水）→ 正当；没有 → 违规。 */
  const obsoleteRetention = (text) => {
    const re = /不在注销删除范围内|未纳入注销删除清单|不在注销清理范围/g;
    const bad = [];
    let m;
    while ((m = re.exec(text)) !== null) {
      const i = m.index;
      const near = text.slice(Math.max(0, i - 90), i + m[0].length + 90);
      if (/支付流水|依法留存|法定期限|电子商务法/.test(near)) continue;
      bad.push(text.slice(Math.max(0, i - 40), i + 60));
    }
    return bad;
  };
  ['姓名分析与取名的保存记录', '择日计划', '求签', '灯语', '收藏'].forEach((k) => {
    const i = DOC.indexOf(k);
    assert.ok(i !== -1, `privacy.md 的注销范围段未列出「${k}」`);
    const win = DOC.slice(Math.max(0, i - 120), i + 320);
    assert.ok(/删除|清除/.test(win),
      `privacy.md 提到「${k}」的附近没有"随注销删除"的如实说明`);
    assert.deepEqual(obsoleteRetention(win), [],
      `privacy.md 在「${k}」附近写"不在注销删除范围内"（且旁边没有依法留存的依据）——`
      + '该事实已被 k76 补齐删除范围反转（现在除支付流水外全部删除）');
  });
  // 全篇：任何"不删除"式表述都必须紧邻依法留存的依据（支付流水），否则就是旧事实复活
  assert.deepEqual(obsoleteRetention(DOC), [],
    'privacy.md 出现没有依法留存依据的"不在注销删除范围内"式表述（指向了不该保留的内容）');
  assert.ok(/唯一(不在注销删除范围内|依法留存)/.test(DOC),
    'privacy.md 未写明"唯一依法/不在注销删除范围内的是支付流水"这一新事实');
  assert.ok(/支付流水/.test(DOC), 'privacy.md 未点名依法留存的支付流水');
  // 旧事实（"四项里只有收藏会删"）不得复活
  assert.ok(!/只有「收藏」[^。]{0,50}删除/.test(DOC),
    'privacy.md 又出现了"四项保留内容里只有收藏会随注销删除"（该事实已被 k76 反转：四项全删）');
  // 页面侧：同口径（四项内容 + 支付流水例外 + 不得再写"不在注销清理范围"指向四项）
  ['姓名分析与取名记录', '择日计划', '求签记录', '灯语'].forEach((k) => {
    assert.ok(PAGE.indexOf(k) !== -1, `privacy.wxml 未列出注销时删除的「${k}」`);
  });
  assert.ok(/支付流水/.test(PAGE), 'privacy.wxml 未写明依法留存的支付流水（唯一例外）');
  assert.ok(!/不(在|纳入)注销(删除|清理)范围[^。]{0,40}(择日|求签|灯语|取名)/.test(PAGE),
    'privacy.wxml 又把"择日/求签/灯语/取名"说成不随注销删除（旧事实复活）');
  // 反向：不得再用"删干净"的笼统说法（无条件禁令，保留）
  ['全部个人数据将在 48 小时内', '所有个人数据将在 48 小时内'].forEach((s) => {
    assert.ok(DOC.indexOf(s) === -1, `privacy.md 出现与代码不符的笼统删除承诺「${s}」`);
    assert.ok(PAGE.indexOf(s) === -1, `privacy.wxml 出现与代码不符的笼统删除承诺「${s}」`);
  });
});

/* ══════════════════════════════════════════════════════════════════════════
   ↓↓↓ k77 追加（「隐私/安全收口批」）↓↓↓
   本段**只新增断言**，未放宽/删除任何既有断言，也未新增 skip/xfail。
   ① §12 = **全仓**用户可见文案扫描（Critical-1 的根因修复：守卫此前只盯
      PAGE/DOC/AGREE 三个常量，第四份用户可见文案整批逃逸）；
   ② §13 = 分享有效期（30 天）文案 ↔ 代码单一事实源。
   ══════════════════════════════════════════════════════════════════════════ */

/* ════════════════════════════════════════════════════════════════
   12. **全仓**扫描：不再有"第四份文案"能逃逸（k77-C / Critical-1 根因）
      Critical-1 的实测事实：settings.wxml 的注销链路写了四处与代码不符的话
      （加密归档 / 90 天可恢复 / "不可恢复"与同页"可找回"自相矛盾 / 期满彻底删除），
      而守卫**全绿** —— 因为守卫只扫三个固定文件（PAGE=privacy.wxml、
      DOC=privacy.md、AGREE=agreement.wxml），settings.wxml 根本不在扫描面里。
      修法不是"把 settings.wxml 也加进白名单"（下一轮还会冒出第五份），而是把
      扫描面**自动枚举到全仓**，并且：
        - 每一处命中都必须判定：符合事实 / 需改 / 有正当例外（例外必须**显式登记**）；
        - 例外表**双向**：例外不再需要（模式在该文件已不命中）→ 也红，防豁免堆积。
   ════════════════════════════════════════════════════════════════ */

/** 扫描面排除项（**只有这两类**，且下面有断言钉住排除面本身）：
    - node_modules：非交付物；
    - tests/**：测试代码不是"用户可见文案"（本守卫自身的正则字面量会自匹配）。 */
const SCAN_EXCLUDE_DIRS = new Set(['node_modules', 'tests']);

function walkFiles(rootDir, exts, out = []) {
  for (const ent of fs.readdirSync(rootDir, { withFileTypes: true })) {
    if (ent.isDirectory()) {
      if (SCAN_EXCLUDE_DIRS.has(ent.name)) continue;
      walkFiles(path.join(rootDir, ent.name), exts, out);
    } else if (exts.has(path.extname(ent.name))) {
      out.push(path.join(rootDir, ent.name));
    }
  }
  return out;
}

/** 提取 JS 里的**字符串字面量**（含模板串），跳过注释与正则/其它词法。
    只保留含中日韩统一表意文字（一-鿿）的串 —— 用户可见文案一定是中文，
    而 API 路径/存储键/错误码是 ASCII，这样能把扫描面收敛到"给人看的字"上。
    为什么要自己过词法：`// 注释里提到"不会过期"` 不是用户可见文案，
    用裸正则扫会把它当命中（假红），用简单字符串提取才能只扫真正的字符串。 */
function jsUserCopy(src) {
  const out = [];
  let i = 0;
  const n = src.length;
  while (i < n) {
    const c = src[i];
    if (c === '/' && src[i + 1] === '/') { while (i < n && src[i] !== '\n') i++; continue; }
    if (c === '/' && src[i + 1] === '*') {
      i += 2; while (i < n && !(src[i] === '*' && src[i + 1] === '/')) i++; i += 2; continue;
    }
    if (c === '"' || c === "'" || c === '`') {
      const quote = c;
      let j = i + 1;
      let buf = '';
      while (j < n) {
        if (src[j] === '\\') { buf += (src[j + 1] || ''); j += 2; continue; }
        if (src[j] === quote) break;
        buf += src[j]; j++;
      }
      if (/[一-鿿]/.test(buf)) out.push({ text: flat(buf), at: i });
      i = j + 1; continue;
    }
    i++;
  }
  return out;
}

/* 扫描面装配（**自动枚举，无白名单**）：
   - 全部 .wxml（用户可见界面）
   - 全部 .js 的中文字符串字面量（弹层/toast/文案）
   - 全部 .md（提审用《隐私保护指引》、审核材料） */
const SCAN_WXML = walkFiles(ROOT, new Set(['.wxml']));
const SCAN_JS = walkFiles(ROOT, new Set(['.js']));
const SCAN_MD = walkFiles(ROOT, new Set(['.md']));

test('k77-C 前提：扫描面 = 全仓自动枚举（wxml/js/md），不含任何固定文件白名单', () => {
  const rel = (p) => path.relative(ROOT, p);
  // ① 覆盖面：仓内所有 wxml 都在扫描面里（逐个比对，漏一个即红）
  const allWxml = walkFiles(ROOT, new Set(['.wxml']));
  assert.deepEqual(SCAN_WXML.map(rel).sort(), allWxml.map(rel).sort(),
    '扫描面与仓内 wxml 清单不一致（不得再用固定白名单）');
  assert.ok(SCAN_WXML.length >= 39,
    `扫描到的 wxml 只有 ${SCAN_WXML.length} 个（改前实测 39 个）——枚举器可能失效`);
  assert.ok(SCAN_JS.length >= 50, `扫描到的 js 只有 ${SCAN_JS.length} 个`);
  assert.ok(SCAN_MD.length >= 2, `扫描到的 md 只有 ${SCAN_MD.length} 个（privacy.md / 审核材料.md）`);
  // ② 关键文件确实在扫描面内（Critical-1 的逃逸文件必须被纳入）
  ['pages/settings/settings.wxml', 'pages/privacy/privacy.wxml',
    'pages/agreement/agreement.wxml', 'pages/history/history.wxml',
    'pages/share/share.wxml', 'privacy.md', '审核材料.md'].forEach((p) => {
    assert.ok(SCAN_WXML.map(rel).concat(SCAN_MD.map(rel)).indexOf(p) !== -1,
      `${p} 不在全仓扫描面内（Critical-1 的逃逸路径又开了）`);
  });
  // ③ 排除面本身也钉住：只有 node_modules 与 tests
  assert.deepEqual([...SCAN_EXCLUDE_DIRS].sort(), ['node_modules', 'tests'],
    '排除面变了 → 必须重新评估"逃逸风险"（排除越多，越可能漏掉用户可见文案）');
});

/* 全仓禁语表 = "绝对句"清单。每条都必须**有代码反证**（why 里写明）。
   与 §7 的 THREE_DOC_FORBIDDEN 的关系：那一张表继续管"三份文案口径对齐"；
   本表把它**扩到全仓**（同一个事实，扫描面更大），并补上 k77 这批的新事实。 */
const REPO_FORBIDDEN = THREE_DOC_FORBIDDEN
  /* 说明：无条件表里的「期满彻底删除」已按 k77-A3 改判为**条件规则**（见文件末尾
     的 `.concat([...])` 里的同名条目 + §7 表内的移出说明），故此处不再过滤，
     直接全量继承 —— k77 起本表**只增不减**。 */
  .concat([
    /* ── k77 新事实（每条的代码依据见 why）── */
    { re: /加密归档|加密封存/,
      why: 'src/storage/dao.py:cancel_user() 只写 status=cancelled + cancelled_at，'
        + '**不做任何加密/归档**；改前 settings.wxml 写"数据进入加密归档"是与代码不符的凭空承诺' },
    { re: /90 ?天可恢复|可恢复期/,
      why: 'src/api/user.py:user_cancel docstring「保留期内不提供恢复接口」——'
        + '小程序内没有恢复入口；能被吹成"90 天可恢复"的只有"人工从每日备份尝试"，'
        + '且备份只留最近 14 份（scripts/backup_db.py:DEFAULT_KEEP）' },
    { re: /(注销|销号)[^。]{0,24}不可恢复|不可恢复[^。]{0,16}(注销|销号)/,
      why: '与上一条同源：注销存在"人工从备份尝试找回"这一路径，'
        + '绝对句"不可恢复"会与同页"期内可联系找回"自相矛盾（Critical-1 实测的自相矛盾）；'
        + '应写"无法自助恢复"' },
    { re: /期满彻底删除|期满后彻底删除/,
      unlessNear: /支付流水|依法留存|法定期限|电子商务法/,
      why: 'k76 起除支付流水（依法留存，见 models.ACCOUNT_RETAIN_TABLES）外确实全删；'
        + '绝对句"期满彻底删除"漏掉法定留存例外 → 必须写成带例外的准确句'
        + '（例外必须在命中处就近出现，不能"文件另一头写了例外"）' },
    { re: /彻底删除/,
      unlessNear: /支付流水|依法留存|法定期限|电子商务法|注销清理范围|删除范围/,
      why: '同"期满彻底删除"：出现"彻底删除"必须就近说明依法留存的例外，否则是不实绝对句' },
    { re: /重新登录后[^。]{0,20}(一切从|从一灯|重新开始)/,
      unlessNear: /保留期|无法登录/,
      why: 'src/api/user.py 登录拦截：status=cancelled → 403；保留期内**登录不进来**，'
        + '"重新登录后一切从一灯开始"只在保留期满数据清除后才成立（Critical-1 之外的第五处：'
        + 'settings.wxml 注销成功页 gs-note）' },
    { re: /不会随(账号)?注销(一并)?删除|不随注销删除/,
      why: 'k76：cancel_user() 注销时**立即删除**该账号的分享记录'
        + '（ShareDAO.delete_by_owner）——改前页面写的"不会随账号注销一并删除"已反转为假' },
    { re: /不会过期|永久有效|永久公开|一直有效/,
      why: 'k76：分享链接有 30 天有效期（src/config.py:share_ttl_days 单一事实源，'
        + 'FORTUNE_SHARE_TTL_DAYS）；k77-F 起报告分享页同款。'
        + '改前 privacy.wxml 写"它目前不会过期"已反转为假' },
  ]);

/* 例外表（**显式登记**，每条都要写清理由；无例外即空表 —— 不许隐式豁免）。
   结构：`{file, re, [quoted: true], why}`
     - 不带 quoted：该文件里这条禁语整体豁免（除非必要，尽量别用 —— 它等于给整个
       文件开了口子）；
     - `quoted: true`：**只**豁免"被引号引起来"的命中（匹配处紧邻的前一个非空白字符
       是引号 `" ' 「 『 “ ‘`）。
   为什么需要 quoted：版本说明 / 勘误记录里**引述**历史错误表述是正当的
   （"此前写过『…』，已更正"），引述 ≠ 断言；但它不能变成整文件豁免 ——
   同一文件别处**断言**同一句话仍必须红。 */
/* 命中处是否落在**一对引号之内**（引述而非断言）。开闭引号配对 + 不跨句。 */
function quotedAt(text, i, len) {
  const pre = text.slice(Math.max(0, i - 120), i);
  const post = text.slice(i + len, i + len + 120);
  const OPEN = ['"', "'", '「', '『', '“', '‘'];
  const PAIR = { '"': '"', "'": "'", '「': '」', '『': '』', '“': '”', '‘': '’' };
  let oi = -1;
  let oChar = '';
  OPEN.forEach((c) => { const k = pre.lastIndexOf(c); if (k > oi) { oi = k; oChar = c; } });
  if (oi < 0) return false;
  const ci = post.indexOf(PAIR[oChar]);
  if (ci < 0) return false;
  // 引号内不得跨句/跨段（避免"开引号在很远处"的巧合把真断言当引述）
  return !/[。\n]/.test(pre.slice(oi)) && !/[。\n]/.test(post.slice(0, ci));
}

const REPO_FORBIDDEN_EXEMPT = [
  { file: 'privacy.md', re: /90 ?天可恢复|可恢复期/, quoted: true,
    why: '版本说明的勘误段**引述**历史上那句没有实现支撑的说法（引号内），引述 ≠ 断言；'
      + '同一文件别处若再断言这句，仍会红' },
  { file: 'privacy.md', re: /不会随(账号)?注销(一并)?删除|不随注销删除/, quoted: true,
    why: '同上：版本说明里引述"此前写过的不随注销删除"这一已反转的旧说法' },
];

/* ════════════════════════════════════════════════════════════════════════
   k80-M1：**上下文例外登记表**（CONTEXT_EXEMPT）—— 跨两张扫描器生效的显式豁免

   与上面那张 REPO_FORBIDDEN_EXEMPT 的分工：那张只管 §12 一张表，且允许"整个文件
   豁免 / 引号内引述豁免"；本表跨 **§12/§13 的 miniprogram 面** 与 **§14 的 src/ 后端面**
   （窄面 + 宽面）统一生效，且**只**按 (file, ruleRe, unit) 三元组精确命中 ——
   没有整文件豁免、没有关键词豁免、没有"附近出现某词就放行"。

   为什么要有它（k79-final2 终验实测）：禁语表是宽面正则，宽面必然有假红。终验注入的
   4 条正当文案里 3 条被判红，逐条给出来源：
     `导出你的所有个人数据（数据可携带权）` ← 命中 /所有个人数据|全部个人数据|…/
       （§14 后端面 BACKEND_FORBIDDEN[0]）：它是**导出权**（PIPL 第 45 条数据可携带权），
       不是"已删除"式的不实绝对句 —— 规则本意是禁"所有个人数据已删除"（漏掉依法留存例外）。
     `注销后立即删除本地缓存` ← 命中"注销即删"条件规则（§12 的 RULES_SHARE_DELETE_ON_CANCEL
       与 §14 的 BACKEND_FORBIDDEN[3] 是同一条正则）：说的是**本地缓存**，不是分享链接 ——
       规则本意是"注销并非一律立即删除**分享链接**"（归属未知的老报告刻意不删）。
     `破镜不可恢复` ← 命中 /不可恢复/（§14 后端面 BACKEND_FORBIDDEN[2]）：是**古籍/签文数据
       表**里的成语（破镜重圆类词条），不是隐私承诺 —— 规则本意是禁与"备份仍可人工尝试找回"
       冲突的恢复承诺。

   正确修法**不是**往 unlessNear 里塞词（那会连真违规一起放过 = 削弱规则，明确禁止），
   而是"逐条显式登记 + 四道防新面外的机制"：
     ① 有界（本表 + `contextExemptShapeErrors`）：只对 (file, ruleRe, unit) 三元组精确生效；
     ② 可枚举（紧邻的 `CONTEXT_EXEMPT_MAX` + §15 的断言）：条目数被钉住，失败时打印整张表；
     ③ 卫生（§15 的"登记过的例外必须仍然被需要"）：一次都没被用到的条目即红；
     ④ 不削弱（§15 的注入证明）：同一条规则的**真违规**在同一文件里仍然红（可执行断言）。

   三元组三个字段（**逐字比对**，不比子串/正则/关键词）：
     - file   ：**完整相对路径**（`src/api/user.py` / `pages/settings/settings.wxml`）。
                例外只对**该文件**生效 —— 同一条文案换个文件出现，仍然红。
     - ruleRe ：被豁免的**那一条规则的正则源码字符串**（与 `rule.re.source` 逐字相等）。
                比 `String(rule.re)` 稳：后者含 flags，写法差一个字符就静默失效（k77 实测踩过）。
     - unit   ：被豁免的**整条文案**的精确文本（`===` 比较）。miniprogram 面 = 该扫描单元的
                折叠全文（wxml 剥标签后 / js 字符串字面量 / md 全文）；后端宽面 = 该字符串
                常量的**完整文本**（`wide_face_hits[].full`，不是截断到 120 字的 `text`）。
                **这一条是防"面外"的核心**：新的正当文案必须**新登记一条**，
                无法靠"出现某个词"自动放行。
     - why    ：≥15 字，写清"为什么这不是该规则要禁的那种断言"。

   三元组里为什么不必再带"哪张表"：miniprogram 面只枚举 `miniprogram/`（SCAN_WXML/SCAN_JS/
   SCAN_MD 的根是 ROOT=miniprogram），后端面只枚举 `src/**.py` —— 两张面的文件集不相交，
   `file` 已经把它们分开（同一条正则同时存在于两张表是**有意**的：豁免按正则源码登记，
   两张表里的同一条规则同时生效，正是我们要的"一处登记、两张面都认"）。

   初始条目 = **0 条**（本仓现存文案零假红，实测 `wide_face_hits=[]`、`leftover=[]`）。 */
const CONTEXT_EXEMPT = [
  /* 无条目。新增一条时必须**同时**满足：① file 是完整相对路径 ② ruleRe 逐字等于某条规则
     的 .source ③ unit 是整条文案的精确文本 ④ why ≥15 字写清"为什么这不是该规则要禁的断言"，
     并把下面紧邻的 CONTEXT_EXEMPT_MAX 同步上调（评审 diff 里可见）。 */
];

/*: 例外条目数上界（**紧邻表**的常量，②可枚举）：新增/删除条目必须同步改这里。 */
const CONTEXT_EXEMPT_MAX = 0;

test('k80-M1 例外表②可枚举：条目数被紧邻表的 CONTEXT_EXEMPT_MAX 精确钉住（失败即打印整张表）', () => {
  assert.equal(CONTEXT_EXEMPT.length, CONTEXT_EXEMPT_MAX,
    `例外表条目数 ${CONTEXT_EXEMPT.length} ≠ 紧邻表的常量 CONTEXT_EXEMPT_MAX=${CONTEXT_EXEMPT_MAX}`
    + '（新增豁免必须同步改这个常量，让"多了一条豁免"在评审 diff 里露出来；'
    + '条目必须逐条可枚举、可审计）。整张表：\n' + JSON.stringify(CONTEXT_EXEMPT, null, 2));
});

/*: 例外判定（**唯一**一处实现：§12 / §13 / §14 窄面与宽面 / 注入证明 都走它）。
    三元组 (file, 规则正则源码, 整条文案) 逐字相等 ⇒ 返回条目下标；否则 -1。 */
function exemptHitIndex(exempt, file, ruleRe, unit) {
  for (let i = 0; i < exempt.length; i++) {
    const e = exempt[i];
    if (e.file === file && e.ruleRe === ruleRe && e.unit === unit) return i;
  }
  return -1;
}

/*: 例外条目形状检查（① 有界）：只允许"完整相对路径 + 某条**存在**的规则的正则源码 +
    整条文案 + ≥15 字的 why"。任何一项不合规即红（含"ruleRe 写了一条不存在的规则"，
    那等于变相削弱规则）。 */
function contextExemptShapeErrors(exempt, knownRuleSources) {
  const errs = [];
  exempt.forEach((e, i) => {
    const at = `#${i} ${JSON.stringify(e)}`;
    if (typeof e.file !== 'string' || !e.file.includes('/') || e.file.startsWith('/')
      || e.file.startsWith('./') || e.file.startsWith('../')) {
      errs.push(`${at}：file 必须是**完整相对路径**（形如 src/api/user.py / `
        + 'pages/settings/settings.wxml）—— 例外只对该文件生效，不许"整个仓/整个目录"的口子');
    }
    if (typeof e.ruleRe !== 'string' || knownRuleSources.indexOf(e.ruleRe) === -1) {
      errs.push(`${at}：ruleRe 必须**逐字等于**某条禁用规则的 .source`
        + '（写错一个字符 ⇒ 豁免静默失效，k77 实测踩过；写一条不存在的规则 = 削弱规则）');
    }
    if (typeof e.unit !== 'string' || e.unit.trim() === '') {
      errs.push(`${at}：unit 必须是被豁免**整条文案**的精确文本（逐字相等；`
        + '不许子串/正则/关键词，空的 unit 等于给整个文件开口子）');
    }
    if (typeof e.why !== 'string' || e.why.trim().length < 15) {
      errs.push(`${at}：why 必须 ≥15 字，写清"为什么这不是该规则要禁的那种断言"`);
    }
  });
  return errs;
}

/*: 判定核心（**纯函数**：真实扫描与注入证明用同一套逻辑，注入证明直接调它）。
    入 units ：{file, text, kind?, line?, name?}
    入 rules ：{re, unlessNear, why}
    入 exempt：CONTEXT_EXEMPT 形状（注入证明里传**局部构造**的测试表，不动真表）
    入 opts.quotedExempt：§12 既有的 REPO_FORBIDDEN_EXEMPT（引述豁免，语义原样保留）
    出 violations：[{file, kind, line, name, unitIndex, hit, why, context}]（未豁免的命中）
    出 usedExempt ：[{index, entry}]（③卫生的输入：登记过的条目必须至少被用到一次） */
function scanUnits(units, rules, exempt = [], opts = {}) {
  const quotedExempt = opts.quotedExempt || [];
  const UNLESS_WINDOW = 160;
  const violations = [];
  const usedExempt = [];
  const usedSeen = new Set();
  units.forEach((u, unitIndex) => {
    for (const rule of rules) {
      /* ① 显式例外登记表（k80-M1）：三元组精确命中 ⇒ 该 (文件, 规则, 整条文案) 整体放行 */
      const exIdx = exemptHitIndex(exempt, u.file, rule.re.source, u.text);
      if (exIdx >= 0) {
        if (!usedSeen.has(exIdx)) {
          usedSeen.add(exIdx);
          usedExempt.push({ index: exIdx, entry: exempt[exIdx] });
        }
        continue;
      }
      /* 既有机制（§12）：不带 quoted 的条目豁免整个文件；带 quoted 的只豁免"引号内"的命中 */
      const exs = quotedExempt.filter(
        (e) => e.file === u.file && e.re.source === rule.re.source);
      if (exs.some((e) => !e.quoted)) continue;
      const re = new RegExp(rule.re.source, rule.re.flags.includes('g') ? rule.re.flags : rule.re.flags + 'g');
      let m;
      while ((m = re.exec(u.text)) !== null) {
        if (!m[0]) { re.lastIndex++; continue; }
        const i = m.index;
        const win = u.text.slice(Math.max(0, i - UNLESS_WINDOW),
          i + m[0].length + UNLESS_WINDOW);
        if (rule.unlessNear && rule.unlessNear.test(win)) continue;   // 例外就近 → 判为"带例外的准确句"
        /* 引号内 ⇒ 视为"引述历史表述"而非断言（见 REPO_FORBIDDEN_EXEMPT 注释）。 */
        if (exs.some((e) => e.quoted) && quotedAt(u.text, i, m[0].length)) continue;
        violations.push({ file: u.file, kind: u.kind, line: u.line, name: u.name, unitIndex,
          hit: m[0], why: rule.why,
          context: '…' + u.text.slice(Math.max(0, i - 30), i + m[0].length + 30) + '…' });
        break;   // 同一规则在同一单元里只报一次
      }
    }
  });
  return { violations, usedExempt };
}

/*: §12/§13 的扫描单元（miniprogram 面：全仓 wxml / js 中文字符串字面量 / md）。
    抽成一处、两张表共用（此前两个测试各写一遍装配，容易一边改一边漏）。 */
let _repoCopyUnits = null;
function repoCopyUnits() {
  if (_repoCopyUnits) return _repoCopyUnits;
  const rel = (p) => path.relative(ROOT, p);
  const units = [];
  SCAN_WXML.forEach((p) => units.push({ file: rel(p), text: wxmlText(fs.readFileSync(p, 'utf8')), kind: 'wxml' }));
  SCAN_JS.forEach((p) => {
    const src = fs.readFileSync(p, 'utf8');
    jsUserCopy(src).forEach((s) => units.push({ file: rel(p), text: s.text, kind: 'js-string' }));
  });
  SCAN_MD.forEach((p) => units.push({ file: rel(p), text: flat(fs.readFileSync(p, 'utf8')), kind: 'md' }));
  _repoCopyUnits = units;
  return units;
}

test('k77-C 全仓禁语扫描：任何用户可见文案都不得复活"与代码不符的绝对句"', () => {
  /* 判定逻辑抽到 `scanUnits`（k80-M1）：真实扫描与注入证明**同一套**函数 ——
     既有的 REPO_FORBIDDEN_EXEMPT（整文件 / 引述豁免，按正则源码比对）语义原样保留，
     另接线显式例外登记表 CONTEXT_EXEMPT（三元组精确命中才放行）。 */
  const { violations } = scanUnits(repoCopyUnits(), REPO_FORBIDDEN, CONTEXT_EXEMPT,
    { quotedExempt: REPO_FORBIDDEN_EXEMPT });
  const bad = violations.map((v) => `${v.file} [${v.kind}] 命中「${v.hit}」：${v.why}\n`
    + `      上下文：${v.context}`);
  assert.deepEqual(bad, [],
    `全仓用户可见文案出现与代码不符的绝对句（共 ${bad.length} 处）：\n  - `
    + bad.join('\n  - '));
});

test('k77-C 例外表卫生：登记过的例外必须仍然被需要（防豁免堆积）', () => {
  const rel = (p) => path.relative(ROOT, p);
  const units = [];
  SCAN_WXML.forEach((p) => units.push({ file: rel(p), text: wxmlText(fs.readFileSync(p, 'utf8')) }));
  SCAN_JS.forEach((p) => {
    const src = fs.readFileSync(p, 'utf8');
    jsUserCopy(src).forEach((s) => units.push({ file: rel(p), text: s.text }));
  });
  SCAN_MD.forEach((p) => units.push({ file: rel(p), text: flat(fs.readFileSync(p, 'utf8')) }));
  const stale = REPO_FORBIDDEN_EXEMPT.filter((e) => {
    const hit = units.filter((u) => u.file === e.file);
    return !hit.some((u) => e.re.test(u.text));
  }).map((e) => `${e.file} ← ${e.re}`);
  assert.deepEqual(stale, [],
    '下列例外登记已不再需要（该文件里那条禁语已经不出现了）→ 请删掉例外，'
    + '避免豁免表只增不减：' + stale.join(', '));
});

/* ════════════════════════════════════════════════════════════════
   13. 分享有效期（30 天）：文案 ↔ 代码单一事实源（k77-A1/F）
   ════════════════════════════════════════════════════════════════ */
test('k77-F 分享有效期：文案里的"30 天"必须等于 config 的单一事实源默认值', () => {
  const cfg = read(path.join('..', 'src', 'config.py'));
  const m = cfg.match(/SHARE_TTL_DAYS_DEFAULT\s*=\s*([0-9.]+)/);
  assert.ok(m, 'src/config.py 未见 SHARE_TTL_DAYS_DEFAULT（有效期单一事实源）');
  const days = Number(m[1]);
  assert.equal(days, 30, '默认有效期不再是 30 天 → 三份文案里的"30 天"必须同步');
  /* 报告分享页（k77-F）：与对话分享**同一配置口径**（不是第二旋钮）——
     同一份事实源 ⇒ 文案只能有一个数字，页面/md/settings 三处都必须是它 */
  ['privacy.wxml', 'privacy.md', 'pages/settings/settings.wxml'].forEach(() => {});
  const files = ['pages/privacy/privacy.wxml', 'privacy.md', 'pages/settings/settings.wxml'];
  const bad = files.filter((f) => {
    const t = flat(read(f));
    return !new RegExp(`${days}\\s*天`).test(t);
  });
  assert.deepEqual(bad, [], `下列文案没有写明分享有效期 ${days} 天：${bad.join(', ')}`);
  // 报告分享页的过期行为由后端实现（src/api/share.py）；这里钉住"它确实挂了 TTL"
  const sharePy = read(path.join('..', 'src', 'api', 'share.py'));
  assert.ok(/_report_share_expires_at/.test(sharePy)
    && /status_code=410/.test(sharePy),
    '报告分享页（/share/{reading_id}）未见有效期判定或 410 过期返回'
    + '（k77-F 要求它与对话分享同款）');
  // 单一事实源：两个入口都必须经 config 取值，不得各写一份天数
  assert.ok(!/=\s*30\s*#/.test(sharePy), 'src/api/share.py 里出现了硬编码的 30 天（应走 config）');
});

/* ══════════════════════════════════════════════════════════════════════════
   ↓↓↓ k78 追加（隐私/安全收尾批 · 最后一批）↓↓↓
   本段**只新增断言**，未放宽/删除任何既有断言，也未新增 skip/xfail。
   ① §12 补一条**条件规则**（必修1）：无条件句"注销时分享链接立即删除"在
      "归属未知的老报告"这一情形上是假话 ⇒ 必须就近写明例外；
   ② §14 = **后端用户可见字符串面**（必修2 的根因修复）：扫描面此前只到
      miniprogram（wxml/js/md），`src/` 下全部 .py 里会返回给用户的 `detail=` /
      `message=` / 字典字面量**整批在面外** —— 实测逃逸的就是这四处
      （user.py 的 403 detail 与注销响应、main.py 与 security/router.py 的
      数据删除 message）。
   ══════════════════════════════════════════════════════════════════════════ */

/*: 必修1 的条件规则（**只新增**，未放松任何既有禁语）：
    "注销 + (分享|链接) + 删除"出现在同一句里 ⇒ 必须就近写明"归属未知的老报告"
    例外。三种语序都覆盖（注销在分享前 / 分享在注销前 / "注销时立即删除"）。 */
const RULES_SHARE_DELETE_ON_CANCEL = {
  re: /(注销|销号)[^。；\n]{0,24}(立即|即刻|马上)删除|(注销|销号)[^。；\n]{0,40}(分享|链接)[^。；\n]{0,20}删除|(分享|链接)[^。；\n]{0,40}(注销|销号)[^。；\n]{0,20}删除/,
  unlessNear: /归属|老报告|无法确认|无法定位|定位不到/,
  why: '注销时并非"一律立即删除"分享链接：`storage/dao.py::purge_report_files` '
    + '只删**归属可确认**（owner_enc 解密后 == 用户）的报告文件与分享图，'
    + '"归属未知的老报告"（本服务开始记录报告归属之前生成的）刻意不动 —— '
    + '文案必须把这一例外写在同一处，否则读者会以为"一定立即删除"',
};

test('k78-必修1 全仓条件规则：注销即删分享链接的绝对句必须就近写明"归属未知"例外', () => {
  /* 背景（k78 复原始测）：注销后
       有归属 /share/aaaa1111 → 404（文件已删）
       无归属 /share/bbbb2222 → 200（仍在对外提供）
     —— `purge_report_files` 刻意跳过"归属未知的老报告"（不能凭一次注销删掉
     可能是别人的东西），而三份文案（privacy.md 三处 + settings.wxml 两处 +
     privacy.wxml 两处）写的都是**无条件**的"注销时你生成的分享链接会立即删除"。
     故本规则：这类句子必须**就近**出现例外词（归属/老报告/无法确认/定位不到），
     否则红。例外不在附近就红 —— 这不是放宽：无条件句仍然红（k78 报告有注入实测）。 */
  const rule = RULES_SHARE_DELETE_ON_CANCEL;
  /* k80-M1：判定同样走 `scanUnits`（三扫描器共用同一套逻辑）⇒ CONTEXT_EXEMPT 对
     §13 这条条件规则一并生效（"注销后立即删除本地缓存"这类正当句可逐条登记豁免）。 */
  const { violations } = scanUnits(repoCopyUnits(), [rule], CONTEXT_EXEMPT);
  const bad = violations.map((v) => `${v.file} 命中「${v.hit}」← ${v.why}`);
  assert.deepEqual(bad, [],
    `出现"注销即删分享链接"的无条件句（缺归属例外）：\n  - ${bad.join('\n  - ')}`);
});

/* ════════════════════════════════════════════════════════════════
   14. **后端**用户可见字符串面（k78-必修2 根因修复）
      判据 = `src/` 下全部 .py 里"会返回给用户的字"：
        - 任意调用/异常构造的**关键字实参** `detail=` / `message=`；
        - 字典字面量的 `message/detail/msg/error/reason/hint/disclaimer/
          action/label/title/description` 值；
        - 集中在 `src/security/account_copy.py` 的**文案常量**（模块级字符串常量）。
      为什么要有这一面：文案是"说给用户听的话"，此前只扫 miniprogram ⇒ 后端
      内联字面量整批逃逸（k78 实测 4 处：缺法定留存例外 / "所有" / "不可恢复"）。
      覆盖面由**两条路**共同保证：内联字面量走 AST 面，常量走常量面；
      两条都过才绿 ⇒ "只改报出来的那 4 处"过不了（新增内联字面量会红）。
   ════════════════════════════════════════════════════════════════ */

//: 文案常量的**唯一**集中地（新增用户可见文案请放这里，见模块 docstring）
const BACKEND_COPY_MODULE = 'src/security/account_copy.py';

//: 后端面禁语表（**只增不减**）。与 §12 的 REPO_FORBIDDEN 一样：每条都必须有
//: 代码反证（why 里写明）。这一批的 4 条对应复审实测的 4 处逃逸。
const BACKEND_FORBIDDEN = [
  { re: /所有个人数据|全部个人数据|所有个人信息|全部个人信息/,
    unlessNear: /依法|留存|支付流水|除外/,
    why: '「所有个人数据已删除」不成立：`models.ACCOUNT_RETAIN_TABLES` 明确依法留存 '
      + 'payments / midas_orders（《电子商务法》第三十一条，保存不少于三年）；'
      + '无条件说"所有"必须就近写明该例外（k78 实测 src/main.py 与 '
      + 'src/security/router.py 各一处）' },
  { re: /个人数据[^。；\n]{0,12}(已删除|已清除)/,
    unlessNear: /依法|留存|支付流水|除外/,
    why: '同上：删除的范围是"除依法留存项以外的个人数据"，就近没有例外词即不实' },
  { re: /不可恢复/,
    unlessNear: /备份|自助|找回|文件类/,
    why: '「不可恢复」与"备份仍覆盖时可由人工尝试找回"冲突（每日备份保留最近 14 份，'
      + '见 privacy.md 第五节第 3 条）；本仓统一口径是"**不可自助恢复**"。'
      + '后端面向用户的字符串里出现"不可恢复"必须就近说明备份/自助的口径' },
  { re: /(注销|销号)[^。；\n]{0,24}(立即|即刻|马上)删除|(注销|销号)[^。；\n]{0,40}(分享|链接)[^。；\n]{0,20}删除|(分享|链接)[^。；\n]{0,40}(注销|销号)[^。；\n]{0,20}删除/,
    unlessNear: /归属|老报告|无法确认|无法定位|定位不到/,
    why: '与 §12 的条件规则同源（必修1）：注销后的分享链接并非"一律立即删除"——'
      + '归属未知的老报告分享页仍在（`purge_report_files` 跳过归属未知者），'
      + '只能按 30 天有效期自然失效' },
];

/*: 旧文案的**逐字**字面量（复审实测逃逸的那几句）。它们不得以任何**字符串常量**
   形式回到 src/（docstring 里引述历史表述不算 —— AST 面天然跳过 docstring）。
   注意 `unlessNear`：这几句**带例外**时是正确表述（新常量就是"旧句 + 例外"），
   所以判据是"出现了旧句 **且** 同一串里没有例外词"，不是单纯出现即红。 */
const BACKEND_OLD_LITERALS = [
  { literal: '账号已注销，数据保留 90 天后删除',
    unlessNear: /支付流水|依法|留存|除外/ },
  { literal: '所有个人数据已删除（不可恢复）',
    unlessNear: /支付流水|依法|留存|除外|备份|自助|找回/ },
  { literal: '数据删除（不可恢复）',
    unlessNear: /备份|自助|找回|文件类/ },
];

/*: 抽取器（python ast，见 §14 说明）。返回 JSON（pyJson 取最后一行）。
    不用正则扫 .py：`# 「所有个人数据已删除」` 这类注释/文档串不是用户可见文案，
    用正则扫必假红；用 AST 才能只取"真的会回给用户"的那些字。 */
const BACKEND_EXTRACTOR = `
import ast, json, os, pathlib, re, sys
COPY_MODULE = ${JSON.stringify(BACKEND_COPY_MODULE)}
OLD_LITERALS = json.loads(${JSON.stringify(JSON.stringify(
  BACKEND_OLD_LITERALS.map((r) => ({ literal: r.literal, unless: r.unlessNear.source }))))})
#: 禁语表**经环境变量**传入（不是嵌进本模板字符串）：规则里带正则转义，
#: 经模板字符串再解析一层容易出歧义 —— env 走的是 JSON 原文，零转义风险。
FORBIDDEN = json.loads(os.environ["K71_BACKEND_FORBIDDEN_JSON"])
MSG_KEYS = {"message", "detail", "msg", "error", "reason", "hint", "disclaimer",
            "action", "label", "title", "description"}
KW_NAMES = {"detail", "message"}
#: unlessNear 的窗口（与 JS 侧同一口径；宽面同样用它）
UNLESS_WINDOW = 160


def has_cjk(s):
    return any("\\u4e00" <= ch <= "\\u9fff" for ch in s)


def _literal_value(node):
    """字面量标量（str/int/float/bool/None）；不是 → (None, False)。"""
    if isinstance(node, ast.Constant):
        v = node.value
        if v is None or isinstance(v, (str, int, float, bool)):
            return v, True
    return None, False


def const_str(node):
    """**静态可求值**的字符串（k79-必修2 的修法之一：常量折叠）。

    改前只认 ast.Constant 与 ast.JoinedStr 的字面量段 ⇒
        raise ValueError("x", detail="账号已注销，" + "数据保留 90 天后删除")
    里 detail= 的值是 BinOp，resolve() 拿到 None，整句**在面外**（复审实测）。
    现在按**编译期能算出来**的规约折叠：加法拼接 / % 格式化 / .format()；
    任一段不是静态字面量 → 返回 None（不猜）。
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values
                       if isinstance(v, ast.Constant) and isinstance(v.value, str))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = const_str(node.left), const_str(node.right)
        if left is not None and right is not None:
            return left + right
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        fmt = const_str(node.left)
        if fmt is None:
            return None
        if isinstance(node.right, ast.Tuple):
            vals = []
            for e in node.right.elts:
                v, ok = _literal_value(e)
                if not ok:
                    return None
                vals.append(v)
            vals = tuple(vals)
        else:
            v, ok = _literal_value(node.right)
            if not ok:
                return None
            vals = v
        try:
            return fmt % vals
        except Exception:
            return None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr == "format":
        base = const_str(node.func.value)
        if base is None:
            return None
        args = []
        for a in node.args:
            v, ok = _literal_value(a)
            if not ok:
                return None
            args.append(v)
        try:
            return base.format(*args)
        except Exception:
            return None
    return None


def const_name(node):
    """」detail=SOME_CONSTANT 里的名字（调用点引用常量时 AST 看不到字面量）。"""
    return node.id if isinstance(node, ast.Name) else None


# ── 第一遍：全 src/ 的模块级字符串常量（名字 → 值），供上面的名字解析 ──
GLOBAL_CONST = {}
for _p in sorted(pathlib.Path("src").rglob("*.py")):
    try:
        _t = ast.parse(_p.read_text(encoding="utf-8"))
    except Exception:
        continue
    for _n in _t.body:
        _v = const_str(_n.value) if isinstance(_n, ast.Assign) else None
        if _v is None:
            continue
        for _tgt in _n.targets:
            if isinstance(_tgt, ast.Name):
                # 文案常量（COPY_MODULE）优先：名字撞车时以文案模块为准
                if _tgt.id not in GLOBAL_CONST or str(_p) == COPY_MODULE:
                    GLOBAL_CONST[_tgt.id] = _v


def collect_locals(func_node):
    """函数体内（**不含嵌套函数**）的 名字 = 静态字符串 表（k79-必修2）。

    改前的解析面只有**模块级**常量 ⇒
        def f(): zz = "…"; raise ValueError("x", detail=zz)
    在面外（复审实测）。现在每个函数有自己的局部常量表，detail= 引用局部名也能解析。
    近似之处（如实写明）：不区分分支/覆盖顺序（同名多次赋值取**最后**一次遍历到的），
    也不做代数/运行时求值 —— 它只是"把本来就写死在同一函数里的字面量认出来"。
    """
    out = {}
    def rec(n):
        for child in ast.iter_child_nodes(n):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue          # 嵌套函数有自己的表，由外层递归处理
            if isinstance(child, ast.Assign):
                v = const_str(child.value)
                if v is not None and len(child.targets) == 1 \
                        and isinstance(child.targets[0], ast.Name):
                    out[child.targets[0].id] = v
            if isinstance(child, ast.AnnAssign) and child.value is not None:
                v = const_str(child.value)
                if v is not None and isinstance(child.target, ast.Name):
                    out[child.target.id] = v
            rec(child)
    rec(func_node)
    return out


def docstring_nodes(tree):
    """全树 docstring 节点集合（模块/类/函数的第一条 Expr 里的常量）。"""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr):
                v = body[0].value
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    out.add(id(v))
                elif isinstance(v, ast.JoinedStr):
                    out.add(id(v))
    return out


user_facing, constants, string_constants = [], [], []
wide_hits = []
resolved_via_local = [0]
files, parse_failures = 0, []


def resolve(node, stack):
    """取字符串：字面量/折叠优先；detail=名字 按**局部 → 外层 → 模块级**解析。"""
    s = const_str(node)
    if s:
        return s
    name = const_name(node)
    if not name:
        return None
    for scope in reversed(stack):
        if name in scope:
            resolved_via_local[0] += 1
            return scope[name]
    return GLOBAL_CONST.get(name)


def window(text, match):
    i = match.start()
    return text[max(0, i - UNLESS_WINDOW): match.end() + UNLESS_WINDOW]


def scan(node, stack, docs):
    # ① 关键字实参 detail= / message=（HTTPException 等）；值可以是字面量/拼接/常量名
    if isinstance(node, ast.Call):
        for kw in node.keywords or []:
            if kw.arg in KW_NAMES:
                s = resolve(kw.value, stack)
                if s and has_cjk(s):
                    user_facing.append({"file": str(p), "line": kw.value.lineno,
                                        "kind": "kw:" + kw.arg, "text": s})
    # ② 字典字面量的 message/detail/... 值（同上）
    if isinstance(node, ast.Dict):
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and k.value in MSG_KEYS:
                s = resolve(v, stack)
                if s and has_cjk(s):
                    user_facing.append({"file": str(p), "line": v.lineno,
                                        "kind": "dict:" + str(k.value), "text": s})
    # ③ 全部字符串常量（排除 docstring）—— 供"旧文案字面量不得回归"与**宽面禁语**扫描
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if id(node) not in docs and has_cjk(node.value):
            string_constants.append({"file": str(p), "line": node.lineno,
                                     "text": node.value, "kind": "const"})
    # ③' 可静态折叠的表达式也进宽面（"a" + "b" 折出来的整句必须看得见）
    elif isinstance(node, (ast.BinOp, ast.JoinedStr)):
        folded = const_str(node)
        if folded and len(folded) > 1 and has_cjk(folded):
            string_constants.append({"file": str(p), "line": node.lineno,
                                     "text": folded, "kind": "folded"})
    # ④ 文案常量模块的模块级字符串常量（含注释性 docstring 之外的常量）
    if str(p) == COPY_MODULE and isinstance(node, ast.Assign):
        tgt = node.targets[0] if node.targets else None
        if isinstance(tgt, ast.Name):
            v = const_str(node.value)
            if v is not None and id(node.value) not in docs:
                constants.append({"file": str(p), "line": node.value.lineno,
                                  "name": tgt.id, "text": v})
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scan(child, stack + [collect_locals(child)], docs)
        else:
            scan(child, stack, docs)


for p in sorted(pathlib.Path("src").rglob("*.py")):
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except Exception as e:
        parse_failures.append(str(p) + ": " + str(e))
        continue
    files += 1
    docs = docstring_nodes(tree)
    scan(tree, [], docs)

# ⑤ **宽面**禁语扫描（k79-必修2 的修法之二）：禁语表不只过窄面（detail=/message=/
#    10 个字典键），而是过**全部中文字符串常量 + 拼接折叠结果**。
#    噪声实测：本仓**全部**中文字符串（含折叠）对 4 条禁语**零命中** ⇒ 不需要为
#    汉字数据表设过滤白名单（白名单本身就是新的"面外"；实测无噪声就不引入）。
#    k80-M2：这里的"条数"**不写死** —— 它随代码变化（每改一次代码就变），手写必然漂移
#    （k79 报告 / 终验 / 本树实测三个数互不相同）。真值由 JS 侧**运行时实测**读出
#    （断言里的 BACKEND.string_constants / BACKEND.folded），本注释只保留结论。
for item in string_constants:
    for rule in FORBIDDEN:
        m = re.search(rule["re"], item["text"])
        if not m:
            continue
        if rule["unless"] and re.search(rule["unless"], window(item["text"], m)):
            continue
        wide_hits.append({"file": item["file"], "line": item["line"],
                          "kind": item["kind"], "hit": m.group(0),
                          "text": item["text"][:120], "full": item["text"],
                          "rule": rule["re"], "why": rule["why"]})
        break

leftover = []
for item in string_constants:
    for rule in OLD_LITERALS:
        if rule["literal"] not in item["text"]:
            continue
        # 带例外（同一串里有"依法留存/备份/自助/找回"等）⇒ 是**正确**表述，不算回归
        if rule["unless"] and re.search(rule["unless"], item["text"]):
            continue
        leftover.append({"file": item["file"], "line": item["line"],
                         "literal": rule["literal"], "text": item["text"][:100]})

print(json.dumps({"files": files, "parse_failures": parse_failures,
                  "user_facing": user_facing, "constants": constants,
                  "string_constants": len(string_constants),
                  "folded": len([x for x in string_constants if x["kind"] == "folded"]),
                  "resolved_via_local": resolved_via_local[0],
                  "wide_face_hits": wide_hits,
                  "forbidden_rules": len(FORBIDDEN),
                  "leftover": leftover}, ensure_ascii=False))

`;

/*: 把禁语表**经环境变量**交给抽取器（k79-必修2 的宽面扫描需要同一份规则表）。
    不用模板字符串内嵌：规则里带 \n 这类转义，多一层解析容易出歧义；env 传的是
    JSON 原文，零转义风险。 */
const BACKEND_FORBIDDEN_JSON = JSON.stringify(
  BACKEND_FORBIDDEN.map((r) => ({ re: r.re.source, unless: r.unlessNear.source,
                                  why: r.why })));

const BACKEND = pyJsonEnv(BACKEND_EXTRACTOR,
  { K71_BACKEND_FORBIDDEN_JSON: BACKEND_FORBIDDEN_JSON }, 180000);

/*: 后端"窄面"扫描单元（detail=/message= 关键字实参 + 10 个字典键 + 文案常量模块的常量）。 */
function backendNarrowUnits() {
  return BACKEND.user_facing.concat(BACKEND.constants)
    .map((u) => ({ file: u.file, line: u.line, kind: u.kind || u.name, text: u.text }));
}

/*: 后端**宽面**的例外判定（k80-M1 的第二台扫描器）：
    检测本身在 python 侧完成（同一份规则表经 env 传入，含 unlessNear 与常量折叠），
    这里**只做例外表判定** —— 命中项的 (file, ruleRe=命中规则的正则源码, unit=常量完整文本)
    三元组若在 CONTEXT_EXEMPT 里登记过 ⇒ 放行（并记为"该条目被用到"，供 ③卫生 断言）。
    为什么在 JS 侧判：例外表经 env 塞进 python 模板串要多一层转义 = 新的歧义面；
    而 `rule`（规则正则源码）与 `full`（未截断的常量原文）已由抽取器回传，JS 侧可逐字比对。
    注意 unit 用 `full` 而不是截断到 120 字的 `text`：登记的必须是**整条文案**的精确文本。 */
function wideFaceScan() {
  const bad = [];
  const usedExempt = [];
  const usedSeen = new Set();
  for (const h of BACKEND.wide_face_hits) {
    const idx = exemptHitIndex(CONTEXT_EXEMPT, h.file, h.rule, h.full);
    if (idx >= 0) {
      if (!usedSeen.has(idx)) {
        usedSeen.add(idx);
        usedExempt.push({ index: idx, entry: CONTEXT_EXEMPT[idx], hit: h });
      }
      continue;
    }
    bad.push(`${h.file}:${h.line} [${h.kind}] 命中「${h.hit}」← ${h.why}`);
  }
  return { bad, usedExempt };
}

test('k78-必修2 前提：后端用户可见字符串面真的被枚举到（抽取器不得失效）', () => {
  assert.deepEqual(BACKEND.parse_failures, [],
    `src/ 下有文件无法解析（抽取面出现盲区）：${BACKEND.parse_failures.join(', ')}`);
  assert.ok(BACKEND.files >= 100,
    `只解析了 ${BACKEND.files} 个 src/*.py（改前实测 100+），枚举器可能失效`);
  //: 阈值 250 **保持不变**（不放宽、不删断言）；括号里改成**实测值**：
  //: k79 实测 414（同一把尺子量 k78 版本是 406 —— 本条注释此前写的"285"
  //: 与实际对不上，k79-M4 按实况改准；实测方法见 k79 报告）。
  assert.ok(BACKEND.user_facing.length >= 250,
    `只抽到 ${BACKEND.user_facing.length} 条后端用户可见字符串（k79 实测 414）——`
    + '抽取器可能失效（抽取器一失效，下面的禁语扫描会"全绿"）');
  const filesHit = new Set(BACKEND.user_facing.map((u) => u.file));
  assert.ok(filesHit.size >= 20,
    `抽到的字符串只来自 ${filesHit.size} 个文件（散布面过窄，疑抽取器失效）`);
  // 抽到的必须**含**这一批刚修过的那些端点（钉住覆盖面：面不能悄悄缩小）
  ['src/api/user.py', 'src/security/account_copy.py'].forEach((f) => {
    assert.ok(BACKEND.user_facing.map((u) => u.file).indexOf(f) !== -1
      || BACKEND.constants.map((u) => u.file).indexOf(f) !== -1,
      `${f} 不在后端用户可见字符串面内（重开盲区）`);
  });
  /*: 阈值 k79 由 3 → 4（**只增不减**）：k79-M1 新增 `DATA_PURGE_INCOMPLETE_NOTICE`。
     "3 常量、4 处引用"是 k78 报告里的说法，**两半都不准**（k79-M4 按实况改准，
     实测方法：`grep -rn <常量名> src/ --include=*.py | grep -v account_copy.py`）：
       - 常量数：k78 是 **3**、k79 是 **4**；
       - 调用点：k78 是 **5 处使用点 / 4 个文件**（user.py 2、main.py 1、router.py 1、
         privacy.py 1）+ 4 处 import（`from … import …` 不算使用点）；
         k79 是 **7 处使用点 / 4 个文件**（新增 main.py、router.py 各 1 处失败分支）。
     下面的断言钉住的是**常量数**（"定义了却没人引用"由另一条断言钉住）。 */
  assert.ok(BACKEND.constants.length >= 4,
    `文案常量模块 ${BACKEND_COPY_MODULE} 只抽到 ${BACKEND.constants.length} 条常量`
    + '（k79 实测 4）');
  assert.ok(BACKEND.string_constants >= 1000,
    `src/ 里的中文字符串常量只有 ${BACKEND.string_constants} 条（疑枚举失效）`);
  //: k79-必修2：宽面里"可静态折叠的拼接"必须真的被抓到 —— 折叠失效则`"a" + "b"`
  //: 那一整类逃逸又看不见了。
  assert.ok(BACKEND.folded >= 100,
    `宽面里可静态折叠的字符串只抽到 ${BACKEND.folded} 条（k79 实测 1000+）`
    + '—— 常量折叠失效，拼接类逃逸会整体回到面外');
  assert.ok(BACKEND.resolved_via_local >= 1,
    '没有任何 detail=/message= 是通过**局部常量**解析出来的（局部名解析失效）');
});

test('k79-必修2 宽面：禁语表过一遍 src/ 全部中文字符串常量（含拼接折叠结果）', () => {
  /* 修的是什么（复审实测的四类逃逸，全部**在窄面之外**）：
       ① `LEGACY_TEXT = "所有个人数据已删除" + "（不可恢复）"`   ← 拼接，窄面解析不到
       ② `_NOTICE = "你的所有个人数据已全部清除"`               ← 模块常量不在 detail=/message= 位
       ③ `zz = "…"; raise ValueError("x", detail=zz)`           ← 局部变量进 detail=
       ④ `raise ValueError("x", detail="账号已注销，" + "数据保留 90 天后删除")` ← 拼接进 detail=
     修法（两条一起，缺一不可）：
       a. **常量折叠 + 局部名解析**（抽取器里的 `const_str` / `collect_locals`）⇒
          ③④ 现在落在窄面上；①② 折出来的整句落在宽面上；
       b. **禁语表也过宽面**（本节）：全部中文字符串常量 + 折叠结果逐条过 4 条禁语。
     噪声：本条规则**自己先实测过** —— 本仓全部中文字符串（含折叠）对 4 条禁语
     **零命中**，故**不引入**"汉字数据表白名单"（白名单本身就是一个新的"面外"）。
     若日后数据表里真的出现这些句子，本断言会红 —— 那是**要人来判**的信号：
     k80-M1 起，唯一的放行路径是**逐条显式登记**（CONTEXT_EXEMPT 的三元组精确命中，
     见 §15 的四道机制），**没有**自动豁免 —— 不许往 unlessNear 里塞词（那是削弱规则）。
     （k80-M2：本注释不写死条数 —— 它随代码变化，手写必漂移；真值见断言里
      `BACKEND.string_constants` / `BACKEND.folded` 的运行时实测。） */
  const { bad } = wideFaceScan();
  assert.deepEqual(bad, [],
    `src/ 的字符串常量里出现与代码不符的绝对句（窄面之外也要拦）：\n  - `
    + bad.join('\n  - '));
  // 反向前提：扫面和空转必须能区分（面太小则本断言形同虚设）
  assert.ok(BACKEND.string_constants >= 1000,
    `宽面只扫到 ${BACKEND.string_constants} 条字符串 —— 面太小`);
});

test('k78-必修2 后端用户可见字符串：禁语表逐条扫描（含常量面）', () => {
  /* k80-M1：判定走共用的 `scanUnits`（与 miniprogram 面、注入证明同一套逻辑），
     CONTEXT_EXEMPT 对后端窄面一并生效。 */
  const { violations } = scanUnits(backendNarrowUnits(), BACKEND_FORBIDDEN, CONTEXT_EXEMPT);
  const bad = violations.map(
    (v) => `${v.file}:${v.line} [${v.kind}] 命中「${v.hit}」：${v.why}`);
  assert.deepEqual(bad, [],
    `后端用户可见文案出现与代码不符的绝对句（共 ${bad.length} 处）：\n  - `
    + bad.join('\n  - '));
});

test('k78-必修2 旧文案字面量不得以字符串常量形式回归 src/', () => {
  const bad = BACKEND.leftover.map((l) => `${l.file}:${l.line} 含「${l.literal}」`);
  assert.deepEqual(bad, [],
    '复审实测逃逸的旧文案又回到了 src/ 的字符串常量里（应改为引用 '
    + `${BACKEND_COPY_MODULE} 的常量，或补上例外）：\n  - ` + bad.join('\n  - '));
});

test('k78-必修2 文案常量确实被用上（单一事实源不是"定义了没人用"）', () => {
  const callSites = BACKEND.user_facing.map((u) => u.text);
  const unused = BACKEND.constants.filter((c) => callSites.indexOf(c.text) === -1)
    .map((c) => `${c.file}:${c.line} ${c.name}`);
  assert.deepEqual(unused, [],
    '下列文案常量**没有任何调用点原样引用**（要么被内联副本取代 = 口径分裂，'
    + '要么常量已死）：\n  - ' + unused.join('\n  - '));
});

/* ════════════════════════════════════════════════════════════════
   15. k80-M1：例外机制的"防新面外"四条机制（① 有界 / ② 可枚举 / ③ 卫生 / ④ 不削弱）
       —— 表本体与 ② 的"条目数钉住"在 §12 前的 `CONTEXT_EXEMPT` 处（紧邻常量
       `CONTEXT_EXEMPT_MAX`），本节放 ① 的形状检查、③ 的卫生、④ 的注入证明。
       k80-M2：消灭本文件里"宽面条数"的手写数字（改为运行时实测）。
   ════════════════════════════════════════════════════════════════ */

/*: 三张禁用规则表的合集（形状检查用它判定"ruleRe 是不是一条**存在**的规则"）。 */
const ALL_FORBIDDEN_RULES = []
  .concat(REPO_FORBIDDEN)
  .concat([RULES_SHARE_DELETE_ON_CANCEL])
  .concat(BACKEND_FORBIDDEN);
const ALL_FORBIDDEN_RULE_SOURCES = ALL_FORBIDDEN_RULES.map((r) => r.re.source);

test('k80-M1 例外表①有界：条目只能对 (完整相对路径, 存在的规则, 整条文案) 三元组生效', () => {
  const errs = contextExemptShapeErrors(CONTEXT_EXEMPT, ALL_FORBIDDEN_RULE_SOURCES);
  assert.deepEqual(errs, [],
    `例外登记表形状不合规（共 ${errs.length} 条）：\n  - ${errs.join('\n  - ')}\n`
    + '（对照：REPO_FORBIDDEN、RULES_SHARE_DELETE_ON_CANCEL、BACKEND_FORBIDDEN 三张表的'
    + `规则正则源码合集共 ${ALL_FORBIDDEN_RULE_SOURCES.length} 条可选）\n整张表：\n`
    + JSON.stringify(CONTEXT_EXEMPT, null, 2));
});

test('k80-M1 例外表③卫生：登记过的例外必须仍然被需要（一次都没用到即红，防豁免堆积）', () => {
  /* 四台扫描器全部跑真实的仓内文案（不注入），把"被用到的条目"汇总起来 ——
     某条登记在四台里**一次都没被用到**，说明那条文案已经不出现/已改写 ⇒ 例外该删。 */
  const runs = [
    ['§12 miniprogram 全仓禁语面', scanUnits(repoCopyUnits(), REPO_FORBIDDEN, CONTEXT_EXEMPT,
      { quotedExempt: REPO_FORBIDDEN_EXEMPT })],
    ['§13 注销即删分享链接条件规则面', scanUnits(repoCopyUnits(), [RULES_SHARE_DELETE_ON_CANCEL], CONTEXT_EXEMPT)],
    ['§14 后端窄面（detail=/message=/常量）', scanUnits(backendNarrowUnits(), BACKEND_FORBIDDEN, CONTEXT_EXEMPT)],
    ['§14 后端宽面（全部中文字符串常量）', wideFaceScan()],
  ];
  const usedIndexes = new Set();
  runs.forEach(([, r]) => r.usedExempt.forEach((x) => usedIndexes.add(x.index)));
  const stale = CONTEXT_EXEMPT.map((e, i) => ({ i, e })).filter((x) => !usedIndexes.has(x.i))
    .map((x) => `#${x.i} file=${x.e.file} ← ruleRe=${JSON.stringify(x.e.ruleRe)} ← unit「${x.e.unit}」`);
  assert.deepEqual(stale, [],
    `下列例外登记在四台扫描器里**一次都没被用到**（那条文案已经不出现 / 已改写）→ `
    + '请删掉例外并同步下调 CONTEXT_EXEMPT_MAX，避免豁免只增不减：\n  - '
    + stale.join('\n  - ') + '\n整张表：\n' + JSON.stringify(CONTEXT_EXEMPT, null, 2));
});

test('k80-M1 例外表④不削弱 + 注入证明：正当文案未登记→红 / 已登记→绿，同文件真违规仍红', () => {
  /* 为什么必须有这条（k79-final2 终验）：宽容的宽面正则必然有假红，终验注入的 4 条
     **正当文案**里 3 条被判红。正确的修法只能是"逐条显式登记"，而"把词塞进 unlessNear"
     会把**真违规**一起放过。本测试用**同一套判定函数** `scanUnits`（真实扫描在用的那个）
     跑三段，逐段打印原始结果：
       ① 未登记      → 判红（复现 k79 终验那 3 条假红）
       ② 登记三元组  → 判绿（且该条目被记为"被用到"，③ 卫生才不形同虚设）
       ③ 同文件真违规 → **仍判红**（证明那条登记没有削弱这条规则）
     三条正当文案逐条给出它命中的**哪张表的哪条规则**（截取自 k79-final2 终验报告）。 */
  const CASES = [
    { name: '导出权（PIPL 第 45 条数据可携带权）',
      file: 'src/security/account_copy.py',
      rule: BACKEND_FORBIDDEN[0],
      faces: [['§14 后端面', [BACKEND_FORBIDDEN[0]]]],
      legit: '导出你的所有个人数据（数据可携带权）',
      truth: '所有个人数据已删除',
      truthHit: '所有个人数据',
      why: '这是**导出权**（把用户自己的全部数据还给他），不是"已删除"式的不实绝对句：'
        + '规则要禁的是"所有个人数据已删除"这类漏掉依法留存例外的断言，导出权与留存无矛盾' },
    { name: '本地缓存（不是分享链接）',
      file: 'pages/settings/settings.wxml',
      rule: RULES_SHARE_DELETE_ON_CANCEL,
      faces: [['§13 条件规则面', [RULES_SHARE_DELETE_ON_CANCEL]],
        ['§14 后端面（同一条正则）', [BACKEND_FORBIDDEN[3]]]],
      legit: '注销后立即删除本地缓存',
      truth: '注销后立即删除分享链接',
      truthHit: '注销后立即删除',
      why: '说的是**本地缓存**，不是分享链接：规则要禁的是"注销 ⇒ 一律立即删除分享链接"'
        + '（归属未知的老报告刻意不删）；本地缓存确实随注销清掉，与代码一致' },
    { name: '古籍/签文数据表里的成语',
      file: 'src/data/classics.py',
      rule: BACKEND_FORBIDDEN[2],
      faces: [['§14 后端面', [BACKEND_FORBIDDEN[2]]]],
      legit: '破镜不可恢复',
      truth: '数据删除（不可恢复）',
      truthHit: '不可恢复',
      why: '这是古籍/签文数据（破镜重圆类词条）里的成语，不是隐私承诺：规则要禁的是'
        + '与"备份仍可由人工尝试找回"冲突的恢复承诺' },
  ];
  const uniq = (xs) => [...new Set(xs)];
  const brief = (r) => JSON.stringify(r.violations.map(
    (v) => ({ file: v.file, unitIndex: v.unitIndex, hit: v.hit })));
  const evidence = ['k80-M1 注入证明（判定函数 = scanUnits，与 §12/§13/§14 真实扫描同一套）：'];

  CASES.forEach((c, ci) => {
    /* 注入用例必须对着**真实规则表里的那条规则对象**跑（防"用例与真表脱节"） */
    assert.ok(ALL_FORBIDDEN_RULES.indexOf(c.rule) !== -1,
      `用例「${c.name}」引用的规则不在任何真实禁用规则表里（注入证明与真表脱节）`);
    const unit = (text) => ({ file: c.file, text, kind: 'js-string' });
    /* 登记条目（局部构造，**不动真表** —— 真表仍必须是 0 条，见 ②可枚举） */
    const entry = { file: c.file, ruleRe: c.rule.re.source, unit: c.legit, why: c.why };

    c.faces.forEach(([faceName, rules]) => {
      /* ① 未登记 → 判红（复现 k79-final2 假红） */
      const before = scanUnits([unit(c.legit)], rules, []);
      assert.ok(before.violations.length >= 1,
        `用例「${c.name}」× ${faceName}：未登记时应判红（这就是 k79 终验报的假红），实际判绿`
        + ' —— 注入串/规则表对不上了？');
      assert.equal(before.usedExempt.length, 0, '未登记时不该有"被用到的例外"');

      /* ② 登记 (file, ruleRe, unit) 三元组 → 判绿（且条目被记为"被用到"） */
      const after = scanUnits([unit(c.legit)], rules, [entry]);
      assert.deepEqual(after.violations, [],
        `用例「${c.name}」× ${faceName}：登记了三元组却仍判红（例外机制没接通）——`
        + `原始命中：${brief(after)}`);
      assert.equal(after.usedExempt.length, 1,
        `用例「${c.name}」× ${faceName}：登记条目没被记为"被用到"`
        + '（③ 卫生测试会因此形同虚设）');

      /* ③ 同一条文件里的**真违规**：同一登记下仍判红（= 没削弱规则） */
      const mixed = scanUnits([unit(c.legit), unit(c.truth)], rules, [entry]);
      assert.ok(mixed.violations.length >= 1,
        `用例「${c.name}」× ${faceName}：同文件的真违规「${c.truth}」被这条登记一起放过了`
        + '（= 削弱规则，判据不成立）');
      assert.ok(mixed.violations.every((v) => v.unitIndex === 1),
        `用例「${c.name}」× ${faceName}：被判红的不是真违规单元（unitIndex 应全为 1）——`
        + `原始命中：${brief(mixed)}`);
      assert.ok(mixed.violations.some((v) => v.hit === c.truthHit),
        `用例「${c.name}」× ${faceName}：真违规命中的不是「${c.truthHit}」——`
        + `原始命中：${brief(mixed)}`);
      /* ④ 只喂真违规（同文件、同登记）也独立判红 */
      const truthOnly = scanUnits([unit(c.truth)], rules, [entry]);
      assert.ok(truthOnly.violations.length >= 1,
        `用例「${c.name}」× ${faceName}：登记后单喂真违规竟判绿 —— 原始命中：${brief(truthOnly)}`);

      evidence.push(`  [用例 ${ci + 1} ${c.name} × ${faceName}]`
        + ` file=${c.file} ruleRe=${c.rule.re.source}`
        + `\n    ① 未登记 正当文案「${c.legit}」 → 判红（命中 ${before.violations.length} 处）：${brief(before)}`
        + `\n    ② 已登记 unit「${c.legit}」 → 判绿（violations=0；登记条目被用到 ${after.usedExempt.length} 次）`
        + `\n    ③ 同一登记下 真违规「${c.truth}」 → 仍判红（命中 ${mixed.violations.length} 处）：${brief(mixed)}`);
    });
  });
  /* 真表必须仍是 0 条（注入用的是局部表）——与 ②可枚举 双保险 */
  assert.equal(CONTEXT_EXEMPT.length, 0,
    `注入证明只该用**局部**构造的例外表，真表 CONTEXT_EXEMPT 必须仍是 0 条，实际 `
    + `${CONTEXT_EXEMPT.length} 条：\n${JSON.stringify(CONTEXT_EXEMPT, null, 2)}`);
  console.log(evidence.join('\n'));
});

/* ── k80-M2：消灭"宽面条数"的手写数字 ─────────────────────────────────────
   根因：这个数**每改一次代码就变**（k79 报告 / 终验 / 本树实测给出三个互不相同的值），
   手写必然漂移 —— 于是本判据**不点名任何历史数字**（点名本身就得把数字写回文件），
   改用**结构判据**：本文件里凡"数字紧贴『条』"且同一行出现"宽度词"的写法一律红。
   真值交给运行时实测（失败信息里带上 `BACKEND.string_constants` / `BACKEND.folded`）。
   既有阈值断言（`>= 1000` / `>= 250` / `>= 4` / `>= 100` 等）一个都不动。 */
test('k80-M2 消灭手写条数：本文件不得再写死"宽面条数"（必须运行时实测）', () => {
  const SELF = fs.readFileSync(__filename, 'utf8');
  /*: 数字紧贴「条」= 在"声称条数"（阈值写法如 `>= 1000,` 后面没有『条』，不会命中）。 */
  const WIDTH_NUMBER = /[0-9]{4,}\s*条|[0-9]+(?:\.[0-9]+)?\s*万\s*条/;
  /*: 同一行出现"宽度词"才算（避免把无关的『第 31 条』『4 条禁语』误判）。 */
  const WIDTH_WORD = /中文字符串|字符串常量|宽面|string_constants|条数/;
  const bad = SELF.split('\n')
    .map((text, i) => ({ line: i + 1, text }))
    .filter((x) => WIDTH_NUMBER.test(x.text) && WIDTH_WORD.test(x.text))
    .map((x) => `第 ${x.line} 行：${x.text.trim()}`);
  assert.deepEqual(bad, [],
    '本文件又把"宽面条数"写成了死数字（数字随代码变化，手写必漂移）——'
    + '请改成运行时实测表述：\n  - ' + bad.join('\n  - ')
    + `\n真值（本次运行实测）：BACKEND.string_constants=${BACKEND.string_constants}`
    + `、BACKEND.folded=${BACKEND.folded}`
    + `（面不能悄悄缩小的判据由既有的 >= 1000 阈值钉住，未改动）`);

  /* 三个历史漂移数字：**不把数字本身写回文件**，所以在这里按位拼出来 ——
     谁把它们粘回本文件即红（"眼不见为净"地删掉判据是不行的）。 */
  const STALE_WIDTH_NUMBERS = [[3, 3, 4, 6, 9], [3, 4, 3, 0, 6], [3, 3, 5, 8, 0]]
    .map((digits) => digits.join(''));
  const revived = STALE_WIDTH_NUMBERS.filter((n) => SELF.indexOf(n) !== -1);
  assert.deepEqual(revived, [],
    '下列"宽面条数"的历史数字又被粘回本文件（应改成运行时实测表述）：'
    + revived.join(', ')
    + `\n真值（本次运行实测）：BACKEND.string_constants=${BACKEND.string_constants}`
    + `、BACKEND.folded=${BACKEND.folded}`);

  console.log(`k80-M2 实测（运行时，非手写）：src/ 中文字符串常量 BACKEND.string_constants=`
    + `${BACKEND.string_constants}（其中可折叠 folded=${BACKEND.folded}）；`
    + `本文件里的"宽面条数"手写数字：${bad.length} 处；历史漂移数字复活：${revived.length} 处`);
});
