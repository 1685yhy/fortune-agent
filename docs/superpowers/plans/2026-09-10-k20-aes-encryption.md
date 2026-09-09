# 2026-09-10 k20-aes-encryption：加密层审计收口（真 AES 核实 + XOR 残留清零 + 补测试）

- 分支：k20-aes-encryption（基线 main=09a3b75）
- 依据：用户 2026-09-10 批准「真 AES」；初查发现 DataEncryptor 已实现
  AES-256-GCM → 本批核实质量、清理 XOR 时代残留注记、补测试、修真实缺口
- 执行纪律：git add 只加本批；不 push 不重启不碰生产库不改 .env
  不动 tool_calls.py；pytest 目标+邻接（勿全量）

## 0. 本批逐项结论速览

| # | 项 | 结论 | 交付 |
|---|----|------|------|
| 1 | 算法审计（encryption.py 全文） | AES-256-GCM ✓（32B 密钥/12B 随机 nonce/16B tag，认证加密） | 审计结论见 §1 |
| 2 | 密钥轮换审计 | v1/v2 版本化解析 ✓、末键为当前键 ✓、旧键保留可解旧数据 ✓ | §1.2 |
| 3 | 全仓加密消费点清单 | 11 类字段全走 DataEncryptor，无明文旁路 ✓ | §1.3 |
| 4 | XOR 时代残留清零 | 服务端零残留；前端 security.js XOR 混淆=认知正确的本地层，标注不清零 | §1.4 |
| 5 | 真实缺口修复 | 2 个解析缺口 + 1 个输入硬化（见 §2） | encryption.py + tests（21 passed） |
| 6 | 补测试 | tests/test_k20_encryption.py 21 passed | §3 |

## 1. 审计结论

### 1.1 算法（src/security/encryption.py 全文核实）
- 加密：`AESGCM`（cryptography.hazmat）AES-256-GCM；`secrets.token_bytes(12)`
  CSPRNG nonce 每 op 唯一；无内嵌 AAD（全仓消费点 aad=b""）。
- 落库格式：`{version}:{base64(nonce(12) || ciphertext || tag(16))}`；
  decrypt 按 12 字节切 nonce、余部整体交 AESGCM（ct||tag）。
- 认证失败：篡改任意字节 → InvalidTag → 捕获 → 返回 None（不吐解密垃圾）。
- dev 兜底：ENCRYPTION_KEY 未配置 → 主机名派生确定性 dev 密钥 + WARNING
  （生产必须配置；main.py 启动日志已区分「已配置/未配置」）。

### 1.2 密钥轮换
- 解析：含 `:` 即版本化（k20 修复后）；多版本逗号分隔；**末合法键为当前
  加密钥**；旧键保留供解密——轮换后旧行仍可解，去掉旧键后旧行解密失败
  返回 None（_decrypt_or_plain 按明文原样兼容，不崩不泄露）。
- 坏格式：单钥分支本有 try/except→SHA-256 派生告警；**版本化分支原无容错
  （k20 缺口 ①）**。

### 1.3 加密消费点清单（2026-09-10 逐点核实，无明文旁路）

| 数据 | 写路径 | 读路径 |
|---|---|---|
| users.bazi_info | dao.save_user_bazi / 懒迁移 / person_dao k11c 镜像（_encrypt_text） | dao.get_user_bazi（懒迁移读时写回） |
| users.phone_enc | dao.py:238（_encrypt_text） | dao.py:258 侧（_decrypt_or_plain） |
| users.session_key_enc | api/user.py _encrypt_session_key | api/user.py _decrypt_session_key（dev 明文兼容） |
| persons.birth_enc / bazi_enc | person_dao / chart_dao save（_encrypt_text） | paipan.py:175/210、chart_dao、person_dao |
| chart_records.birth_enc / bazi_enc | chart_dao.save_chart | paipan.py 重看/历史（_decrypt_or_plain） |
| consultations.question/chart_data/analysis | dao.py:417-419 | dao.py:518/590、privacy._decrypt_field、record_query |
| sessions.content | session_dao.add_message / dedup（_encrypt_text） | session_dao 读 + _decrypt_or_plain |
| jian_cards.card_enc | jian_dao.save_card | jian_dao.get_card |
| L3 记忆文件 fact_entries_enc / topic_evolution_enc | user_memory._save_enc_json / add_evolution | user_memory 读（解密失败 → 空兜底） |

- 明文旁路核查：所有敏感列写入均过 _encrypt_text/DataEncryptor；privacy
  anonymize 写 `[已匿名]` 明文标记为有意设计（_is_ciphertext 判明文原样透传）。
- 预留列 `users.ziwei_info`：schema 声明、无任何写入代码 → 无泄露面；
  已注记 DATABASE.md：启用写入必须走 _encrypt_text。
- `_is_ciphertext` 边界：明文 JSON（{/[ 开头）永不被误判密文；含冒号的
  非 JSON 旧明文会尝试解密一次失败后按明文返回（兼容），代价仅是 ERROR 日志。

### 1.4 XOR 时代残留核查
- 服务端：grep「混淆/^0x/xor/ord^ord/simple cipher」全仓 → **零 XOR 加密路径
  残留**（命中均为中文语义「防混淆」，非加密）。
- 前端 miniprogram/utils/security.js：唯一真 XOR 代码路径（静态密钥 XOR+
  Base64），作用于小程序端本地 storage（auth/userProfile/love 草稿等）；
  文件头与 FUNCTION_GAP_AUDIT L5/L8 早已正确标注「混淆≠加密、P2 可接受、
  升级待 PM」。k20 结论：客户端本地层无法持有真密钥，不属服务端 at-rest
  范围 → 保留 + 头部补 2026-09-10 审计标注（升级 crypto-js/微信安全存储
  待 PM 拍板），不清除不迁移（删除即破坏本地草稿持久化功能）。

## 2. 缺口修复（真实缺口最小修复）

### ① fix: _parse_keys 版本化分支解析容错（原：坏格式直接崩）
- 原状：版本化段 base64 解码无 try/except —— 坏条目（如 `v1:!!!`）抛
  binascii.Error 冒泡 → DataEncryptor() 构造即崩（main.py lifespan 直建
  → 启动崩）；无冒号裸段被静默忽略；全段无效 → keys 空 → 后续 encrypt
  KeyError。
- 修复：逐段 try/except → 告警并跳过；无 ':' 段告警跳过；全部无效 →
  大声告警 + SHA-256 派生降级（与单钥分支同哲学：不崩、可查）。

### ② fix: 单带标签键 "v1:key"（无逗号）误落单钥分支 → 静默派生错误密钥
- 原状：版本化判据为 `"," in s and ":" in s` —— "v1:KEY"（单键带版本
  标签，无逗号）落入单钥分支 → b64decode 含 ':' 校验失败 → 静默
  SHA-256 派生**错误密钥**（本批测试实证）。base64 字母表不含 ':'，
  含 ':' 必为版本化形态。
- 修复：判据收窄为 `":" in key_str`；"v1:key" 现按版本化解析真实键。
- 部署注意：若某部署 .env 恰为单键带标签形态，此前密文为派生错误密钥
  产物 → 修复后解析真实键，历史行将按明文兼容原样返回（不崩不泄露），
  需以真键重加密；DATABASE.md 记载的生产形态为裸 base64 单键（无冒号）
  或 v1,v2 逗号形式 → 不受影响。

### ③ hardening: decrypt(None/""/非 str) → None（原 None 抛 TypeError）
- 调用方已有守卫，此为类型标注一致性兜底，防御未来新调用点。

### ④ 注释清理
- encrypt() 陈旧矛盾注释（宣称 "AESGCM.encrypt returns nonce|ciphertext|tag
  concatenated" 与实际不符）→ 重写为真实格式说明 + aad/tag 语义。
- 模块 docstring 补「2026-09-10 k20 审计确认 AES-256-GCM、无 XOR 路径」。

### ⑤ 文档标注更新
- miniprogram/utils/security.js 头部 2026-09-10 审计标注（唯一 XOR 残留=前端
  本地混淆层，范围外）。
- security/README.md 密钥轮换节补审计确认注。
- docs/DATABASE.md 加密字段清单扩至全量 11 类 + ziwei_info 预留列注记。
- docs/FUNCTION_GAP_AUDIT.md 尾部补 k20 收口附注（S2 已收口；L5/L8 认知
  保持；正文为 2026-08-07 时点记录，sessions 明文遗留条目已过期）。

## 3. 测试清单（tests/test_k20_encryption.py，21 passed）

- GCM 认证篡改：改密文首字节 / 改末 tag 字节 / 改 nonce → 解密 None；
  AAD 不匹配失败、匹配通过（encrypt aad=b"" 默认对不上带 aad 行）。
- nonce 唯一性：500 次同文批量加密 → 输出与 nonce 集合均 500 无重复。
- 密钥轮换：单键 v1 格式往返；裸 32 字节串键；`v1:A,v2:B` 末键 v2 生效、
  旧 v1 行仍可解、v1 单钥实例解不了 v2 行；`v2:B,v1:A` 反序末合法键生效；
  `v1:A,v2:!!!bad` 坏段告警跳过其余可用；全坏段告警降级自洽往返；
  裸段缺 ':' 告警跳过；未知版本/未配版本 → None。
- 空值/异常：decrypt(None/""/无冒号/坏 base64) → None；dev 兜底（env 缺失）
  WARNING + dev: 前缀 + 同主机跨实例可解；跨钥解密 None。
- dao 层旧明文兼容：_is_ciphertext 矩阵；_decrypt_or_plain 对明文 JSON/
  密文/含冒号旧明文/篡改密文/空值的兼容契约；UserDAO.get_user_bazi
  旧明文行读时懒迁移（tmp sqlite，读后原行变 v1: 密文、再读仍可解析）；
  _encrypt_text 空值透传。
- 隔离性：DataEncryptor 级用例显式传密钥（不依赖进程环境）；dao 级用例
  monkeypatch dao._encryptor 为显式密钥实例（不随测试收集顺序漂移）。

## 4. 回归（目标+邻接，90 passed / 0 failed）

- 新：tests/test_k20_encryption.py 21 passed
- 邻接：test_security.py（36）+ test_k19_migrate_e2e.py + test_chart_dao.py +
  test_union_history.py + test_jian_cards.py（合计 51 passed）+ test_k13_retry_dedup.py（18 passed）
- 代码改动面：仅 src/security/encryption.py（解析容错 + 注释）+ tests 新增
