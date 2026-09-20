# k61 r9（复审整改：2 Critical + I1 + 零回归锁 + 过度声明更正）

**提交**：`1a766af`（主修）→ `50280f4` / `4c0b83d` / `3fbcce9`（归因锁）→ `8216af3`（取证锁）→ `08e4740`（报告最终态）。
**分支**：`k61-egress-r9`（基点 `d926a95`）。**未合并、未推送、`src/` 零改动、未写任何生产库。**

**本轮的头号问题不是"代码没写"，是"声称覆盖了实际没覆盖的东西"** —— r8 报告写
「每条路径都有**行为锁**」，审查者实测 `grep wrap_bio tests/` = **0 命中**、
把整条守卫摘掉守卫测试文件仍 **91 passed**。所以本报告把"覆盖 / 没覆盖 / 没验证"
三张单子都摆出来，**能实测的给命令 + 原始输出**。

---

## 0. 结论速览

| 项 | 处置 | 关键实测 |
|---|---|---|
| **C1** SNI 可撒谎（r8 自己引入的回归） | **已修**：判据恢复**合取**（SNI 在白名单 **且** 真实对端可接受＝白名单学到的 IP ∪ 明文代理学到的 IP） | 改前 3 条文档化参数攻击全部 NO-BLOCK、监听器**解密后**收到 `CONNECT api.deepseek.com:443`；改后全部 BLOCK 且线上 0 字节（§2） |
| **C1 误杀是否回来**（控制方点名必须实测） | **环境变量配的明文代理：不误杀**（实测 ALLOW，代理收到 CONNECT + TLS 之内业务请求可见） | §2 正向对照表（OK1/OK2/OK2c） |
| **C2** 同族 TLS 入口 | **已钩**：`ssl.SSLSocket._create`、`ssl.SSLObject._create`、C 方法 `_wrap_socket`/`_wrap_bio` 的**遮蔽层** | 改前 4 条全部 0 反应；改后全部 BLOCK（§3） |
| **C2** `os.splice` / 派生谓词漏名 | **已钩**：`os.splice`+`posix.splice`，并**实测新发现** `os.eventfd_write`（一次能塞 8 字节上 socket）；发送族判据从"前缀猜"改为**显式枚举 + 绊线** | §3.3 |
| **I1** `io.FileIO` / `os.fdopen` / `shutil.copyfileobj` | **已钩**（连带 `io.open` / `builtins.open` / `os.fdopen`）：文件对象盖在 socket fd 上 → fail-closed；`copyfileobj` 用**扫描代理**（内容可判 → 零误杀） | 改前三条全部 0 反应、明文出线；改后全部 BLOCK（§4） |
| **零回归锁** | 41 条"摘掉该路径守卫 → 对应锁必须变红"实测：**40 RED / 1 GREEN**（那 1 条是**刻意留的对照**：证明"只摘一层会被同族兜住"，故锁必须带**归因断言**） | §5（原始输出全表） |
| **没覆盖的** | 7 条显式登记（BIO 降级 / C 层未绑定直调 / `_io.FileIO` / `_socket.*` / 子进程 / ctypes / **env 代理端点+撒谎 SNI 残留**）+ 2 条"过拦"面 | §6 |
| **没验证的** | 8 条（无 ZHIPU key → 真实 GLM 一次没跑 / 全量未跑 / 真代理机未验 / asyncio-anyio 原生用法未端到端 / 参数代理误杀未在真实链路 / pin 兜底只测了继承 / C1b 探针不确定 / `_sendfile_use_send` 回退路径未单独实测） | §7 |

---

## 1. 改前（基点 `d926a95`）逐条复现 —— 命令 + 原始输出

```
$ git show d926a95:tests/conftest.py > /dev/shm/k61r9/conftest_r8.py
$ TMPDIR=/dev/shm nice -n 10 /home/a/fortune-agent/.venv/bin/python /dev/shm/k61r9/probe_before.py
== 改前（基点 d926a95 / r8 tip）：逐条复现 ==
C1a wrap_socket(SNI=白名单)           NO-BLOCK               监听器**解密后**收到 b'CONNECT api.deepseek.com:443 HTTP/1.1\r\nHost: api.dee'
C1b httpx sni_hostname 扩展          OTHER-EXC:ConnectError 监听器 raw=[b'SSLError'] payload=b''      ← 本探针下**不确定**（见 §7）
C1c urllib3 server_hostname=       OTHER-EXC:ProtocolError 监听器 raw=[] payload=b'GET / HTTP/1.1\r\nHost: 127.0.0.'
C2a SSLObject._create(SNI=deepseek) NO-BLOCK
C2b SSLSocket._create(SNI=对端IP)    NO-BLOCK               监听器 0 条
C2c1 ctx._wrap_socket(SNI=白名单)     OTHER-EXC:ConnectionResetError 监听器**解密后**收到 b'CONNECT api.deepseek.com:443 ...'
C2c2 ctx._wrap_bio(SNI=deepseek)   NO-BLOCK
C2d os.splice(file→pipe→socket)    NO-BLOCK               监听器**解密后**收到 b'CONNECT api.deepseek.com:443 ...'
I1a io.FileIO(sockfd).write        NO-BLOCK               监听器**解密后**收到 b'CONNECT api.deepseek.com:443 ...'
I1b os.fdopen(sockfd).write        NO-BLOCK               监听器**解密后**收到 b'CONNECT api.deepseek.com:443 ...'
I1b2 open(sockfd,'wb').write       NO-BLOCK               监听器**解密后**收到 b'CONNECT api.deepseek.com:443 ...'
I1c shutil.copyfileobj(→sockfd)    NO-BLOCK               监听器**解密后**收到 b'CONNECT api.deepseek.com:443 ...'
C2e os.eventfd_write(sockfd, 值)    NO-BLOCK               监听器 raw=[] payload=b'CONNECT '
```

（`OTHER-EXC` 不是"拦住了"：那是**守卫之外**的库层异常，且监听器已经收到明文/字节 ——
C1c 的 `ProtocolError` 发生在监听器**解密后**读到 `GET / HTTP/1.1` 之后。）

监听器是**TLS 终结型**（自签证书 CN/SAN = `open.bigmodel.cn`，见 `_tls_cert` 夹具），
**从不转发一个字节**；`decrypted` = 解密后的载荷 = "明文已经进了 TLS 之内"。

---

## 2. C1：判据恢复**合取**（SNI 不得单独作判据）

**代码**（`tests/conftest.py`）：`_tls_peer_acceptable()` + `_tls_target_refused()`。

```
判据：放行 ⇔ ① server_hostname 在白名单（SNI 只是调用方**自报**）
            ② 真实对端可接受 = 白名单主机学到的 IP ∪ **明文代理**主机学到的 IP
任一条不成立 → 拒（server_hostname 缺失也拒）。
```

**为什么不认"任意回环/私网 IP"**：那正是 C1 的绕过形态（`127.0.0.1` 上的假终点 + 撒谎 SNI）。
**为什么必须认代理 IP**：HTTPS 走 `http://` 明文代理时 TLS 是**端到端**的（对端=代理、SNI=目标），
不认代理 IP 就会误杀白名单主机 —— r8 当初就是为这个把 peer 检查整个删掉的。

### 2.1 改后：攻击面全部拦下（命令 + 原始输出）

```
$ TMPDIR=/dev/shm nice -n 10 /home/a/fortune-agent/.venv/bin/python /dev/shm/k61r9/probe2.py
== 攻击面（期望 BLOCK）==
OK C1a wrap_socket(SNI 撒谎)→127.0.0.1              BLOCK:PublicEgressBlocked -
OK C1b httpx sni_hostname + TLS 代理                BLOCK:DeepSeekEgressBlocked -
OK C1c urllib3 server_hostname=                   BLOCK:PublicEgressBlocked -
OK C2a SSLObject._create(SNI=deepseek)            BLOCK:PublicEgressBlocked -
OK C2b SSLSocket._create(SNI=127.0.0.1)           BLOCK:PublicEgressBlocked -
OK C2b2 SSLSocket._create(SNI 撒谎)                 BLOCK:PublicEgressBlocked -
OK C2c1 ctx._wrap_socket(SNI 撒谎) 直调               BLOCK:PublicEgressBlocked -
OK C2c2 ctx._wrap_bio(SNI=deepseek) 直调            BLOCK:PublicEgressBlocked -
OK C2d os.splice(file→pipe→socket)                BLOCK:PublicEgressBlocked -
OK I1a io.FileIO(sockfd).write                    BLOCK:PublicEgressBlocked -
OK I1b os.fdopen(sockfd).write                    BLOCK:PublicEgressBlocked -
OK I1b2 open(sockfd, 'wb').write                  BLOCK:PublicEgressBlocked -
OK I1c shutil.copyfileobj(→ sockfd)               BLOCK:PublicEgressBlocked -
```

拦截层归因（日志实测，避免"其实是别的层拦的"这种自欺）：

```
A(httpx 直连+sni_hostname 扩展)  => ['拦截层: ssl.SSLContext.wrap_socket（客户端 TLS）']
B(httpx https 代理+sni_hostname) => ['拦截层: ssl.SSLContext.wrap_socket（客户端 TLS）']
C(urllib3 server_hostname=)      => ['拦截层: ssl.SSLContext.wrap_socket（客户端 TLS）']
```

### 2.2 "误杀不会回来"的**实测**（控制方点名：不许只断言）

```
== 正向对照（期望 ALLOW；这些是「不许误杀」的证据）==
OK OK1 直连白名单 TLS（对端=学到的 IP）        ALLOW  收到 b'GET /v1 HTTP/1.1\r\nHost: open.bigmodel.cn\r\n\r\n'
OK OK2 明文代理(env)+requests 白名单 TLS      ALLOW  CONNECT='CONNECT open.bigmodel.cn:443 HTTP/1.1' 解密后=b'GET /api/paas/v4/models HTTP/1.1'
OK OK2c 明文代理(env)+httpx 白名单 TLS        ALLOW  CONNECT='CONNECT open.bigmodel.cn:443 HTTP/1.1' 解密后=b'GET /api/paas/v4/models HTTP/1.1'
OK OK3 普通文件 I/O（open/fdopen/FileIO/copyfileobj） ALLOW  -
OK OK4 无 SNI 客户端 TLS（本就 fail-closed）     BLOCK:PublicEgressBlocked -
OK OK5 socket.makefile('wb')（内容可判→拦）      BLOCK:DeepSeekEgressBlocked
```

**隔离复测**（每个进程只跑一条，排除会话内学习状态顺序影响 —— 见 §6 的"学习集会粘住"）：

```
$ python probe3.py ok2_env_proxy     → OK2-env-隔离 => ALLOW  CONNECT='CONNECT open.bigmodel.cn:443 HTTP/1.1' 解密后=b'GET /api/paas/v4/models...'
$ python probe3.py c1a_fresh         → C1a-隔离(127.0.0.2, 无代理 env) => BLOCK PublicEgressBlocked
```

即：**环境变量配的明文代理下，白名单主机的端到端 TLS 照常放行**（r8 的误杀没有回来），
而**撒谎 SNI 打到本机监听器**被拦（C1 修好）。

### 2.3 ⚠️ C1 的两条**残留 / 过拦**（已实测，**未修**，需拍板）

**(a) 残留：环境变量声称"明文代理" + 撒谎 SNI**（隔离实测）

```
$ python probe3.py res2
RES2-隔离 => ALLOW 监听器=b'CONNECT api.deepseek.com:443 HTTP/1.1\r\nHost: api.d'
```

机制：`HTTPS_PROXY=http://<能终结 TLS 的端点>`（且真的连过它 → 该端点 IP 进 `_proxy_ips`）
之后，对**同一端点**发撒谎 SNI 的客户端 TLS → 落入"可接受对端"② 而放行，明文进 TLS 之内。

**(b) 过拦：用 `proxies=` 参数（而非环境变量）配明文代理**（隔离实测）

```
$ python probe3.py ok2b_param_proxy
OK2b-隔离 => BLOCK PublicEgressBlocked          ← 白名单主机的 TLS 被误杀（r7 形态）
```

影响面：本仓 `grep -rn 'proxies=\|proxy=' src/ tests/ scripts/ --include=*.py` = **0 命中**
（除守卫自测文件）；机器级代理走环境变量 → 已覆盖。所以这是**理论面**，但必须登记。

**(c) 学习集是"会话级、粘住"的**（本批实测的顺序依赖）：`_proxy_ips` 一旦学到某 IP 就**不会**清；
于是"先跑过一条设 `HTTPS_PROXY=http://127.0.0.1:P` 的用例"之后，**任何**之后
"撒谎 SNI + 对端 127.0.0.1"的用例都会被放行（同一探针单独跑 BLOCK、串在代理用例之后 ALLOW）。
⇒ 本文件按**地址分工**消除该依赖：`127.0.0.2`=攻击面对端 / `.3`=代理正向对照 /
`.4`=白名单正向对照 / `.5`=参数配代理的过拦取证（专用），且相关用例前置断言"该 IP 不在两个学习集里"。
**这不是空谈**：过拦取证用例起初用 `.3`（与"代理正向对照"同址）→ **假绿**
（前一条用例已经把该 IP 学进 `_proxy_ips`）→ 改专用 `.5` 才有判别力（本项目内实测）。

**为什么不顺手把 (a) 也关掉**：唯一能区分"端到端 TLS"与"对代理做 TLS"的判据是
"这个 fd 上**是否见过明文 CONNECT**"的关联判据 —— 它**既放宽**（攻击方可以先写一行无害 CONNECT
再撒谎）**又收紧**（参数配代理的合法链路会被拒），而且本机没有真代理环境可验证。
控制方给的修法是"合取 + 认代理 IP"（≈2 行），我按给定修法做了，把残留**显式列出**而不是假装覆盖。

---

## 3. C2：同族 TLS 入口 + 同族写路径

### 3.1 新增挂钩（4 条 TLS 入口）

| 入口 | 类型 | 处置 | 判据 |
|---|---|---|---|
| `ssl.SSLSocket._create` | Python 类方法 | 挂钩（`classmethod`，存 `__dict__` 里那份原样还原） | 合取（可拿 socket → 对端可判） |
| `ssl.SSLObject._create` | Python 类方法 | 挂钩 | **只判 SNI（降级）** |
| `_ssl._SSLContext._wrap_socket` | C 方法 | 在 Python 子类 `ssl.SSLContext` 上**遮蔽**同名属性 | 合取 |
| `_ssl._SSLContext._wrap_bio` | C 方法 | 同上（遮蔽） | **只判 SNI（降级）** |

> 控制方说 `_ssl._SSLContext._wrap_bio`"C 层，钩不了 → 进降级声明"。
> 实测：**C 方法本身确实不可赋值，但 `ssl.SSLContext` 是它的 Python 子类** →
> 在子类上挂同名属性即可**遮蔽**它，一切经**实例**的调用都被覆盖
> （实测输出见 `§5` 的 `SSLContext._wrap_socket/_wrap_bio 遮蔽（单独摘）` → 摘掉后对应锁**变红**），
> 因此本批**钩了**它；真正剩下的残留只有"**未绑定直调**"（`_ssl._SSLContext._wrap_bio(ctx, …)`，
> 已实测 + 已声明，见 §6）。

### 3.2 `wrap_bio` 的**降级声明**（不许假装覆盖）

`wrap_bio` / `SSLObject._create` / `_wrap_bio` 拿到的是 `MemoryBIO`，**与 socket 之间没有反向引用**
（asyncio/anyio 的传输层也不把它递进来）→ 合取判据的②**做不到** → 这三条只判 SNI，
**撒谎的 SNI 挡不住**。代码里有 `_tls_target_refused_sni_only` 的专门注释与
`DECLARED_LIMITATIONS` 条目，并有**取证用例** `test_declared_wrap_bio_sni_can_lie`
（一旦被修好它会变红 → 逼人回来更新声明）。

为什么**不能**改成"异步一律 fail-closed"：那会把所有走 asyncio 的 HTTPS（含免费 GLM 正规链路）
全部打死 —— 是**误杀**不是防护。

**顺带把 r8 关于 aiohttp 的断言复核了**（r8 报告里那条"改前会漏"的自称 —— 本批用
r7 / r8 / r9 三个 conftest 各跑同一条探针，`probe_aiohttp.py`）：

```
$ for w in r7 r8 r9; do python probe_aiohttp.py $w; done
r7 aiohttp proxy(https) => OTHER-EXC:ServerDisconnectedError 监听器 **解密后**=b'CONNECT api.deepseek.com:443 HTTP/1.1\r\nHost: api' raw=[]
r8 aiohttp proxy(https) => BLOCK:PublicEgressBlocked ['拦截层: ssl.SSLContext.wrap_bio（客户端 TLS / 异步家族）'] 监听器 解密后=b''
r9 aiohttp proxy(https) => BLOCK:PublicEgressBlocked ['拦截层: ssl.SSLContext.wrap_bio（客户端 TLS / 异步家族）'] 监听器 解密后=b''
```

⇒ **r8 那条断言成立**（r7 时期 aiohttp 显式 `proxy="https://…"` 的明文 CONNECT 确实进了 TLS 之内、
守卫零反应；r8 起被 `wrap_bio` 钩子拦下），且 r9 **没有把它改坏**。
（`ssl=False` 是探针必需的：否则客户端自己的证书校验会先失败，看不到明文出线。）

### 3.3 `os.splice` / `os.eventfd_write` / 发送族判据改为**显式枚举**

- r8 的派生谓词 `name.startswith(("send","write"))` **两个方向都错过**：
  **漏** `os.splice`（两头都不沾）、`os.eventfd_write`（写语义在**后缀**）、
  `io.FileIO`/`os.fdopen`/`builtins.open`/`shutil.copyfileobj`（文件对象族）；
  **误收** `socket.sendmsg_afalg`（只对 AF_ALG 生效）、`os.pwrite/pwritev`（socket 上 `ESPIPE`）、
  `os.copy_file_range`（socket 上 `EINVAL`）。
- r9 起判据 = **显式枚举**：`tests/test_k61_llm_egress_guard.py::TestSendFamilyApiSurface`
  里每个名字给「来源 ①-⑤ + 处置 + 依据 + 锁它的用例」；前缀扫描**退为绊线**
  （`test_tripwire_names_all_have_a_decision`：派生集合里出现没登记的名字就红，逼人做决定）。
- **本批新发现**：`os.eventfd_write(sock_fd, value)` 一次能把 **8 个任意字节**
  （uint64 的 little-endian）写进 socket —— 实测监听器收到 `b'CONNECT '`。处置：fail-closed
  （8 字节窗口里不可能出现请求头形状 → 扫描无意义）。`os.eventfd_write` / `posix.eventfd_write` 均已钩。

---

## 4. I1：把 socket fd 包成**文件对象**的族（r8 时期是灰区）

| 入口 | 处置 | 说明 |
|---|---|---|
| `io.FileIO` | 换成守卫子类 `_GuardedFileIO`（构造期判 fd） | C 层直写 fd，绕过 `os.write`/`socket.send` |
| `os.fdopen` | 挂钩（`fd` 是 socket → 拒） | 实测在正常链路上**是它自己**在拦（归因已钉） |
| `io.open` / `builtins.open` | 挂钩（首参是 **int fd** 且是 socket → 拒；路径一律透传） | `io.open is builtins.open` 为 True 但**是两个绑定**，只补一处会漏 |
| `shutil.copyfileobj` | `dst` 是 socket 支撑 → **扫描代理**（逐块判内容） | 内容可判 → **不** fail-closed，本地 socket 正常拷贝零误杀 |
| `socket.makefile('wb')` | 不钩（**不是入口**）：写经 `socket.send` → 已判 | 有**行为锁**（`test_socket_makefile_writes_are_still_scanned`） |
| `io.BufferedWriter(...)` | 不钩（**不是入口而是包装器**）：raw 只能来自 FileIO/`_io.FileIO`/SocketIO | 有**行为锁**（`test_bufferedwriter_over_socketio_is_still_scanned`） |
| `shutil.copy/copy2/copyfile/copytree` | 不是通道（**路径级**） | 实测 `open('/proc/self/fd/<sockfd>','wb')` → `OSError: [Errno 6] No such device or address` |
| `shutil.SameFileError` / `SpecialFileError` / `_GiveupOnFastCopy`、`builtins.FileExistsError/FileNotFoundError/copyright`、`os.pidfd_open/openpty/popen`、`io.open_code` | 不是通道（异常类 / 非写入口 / 只读 / 子进程） | 逐条给了依据（`open_code(sock_fd)` → `TypeError: argument 'path' must be str, not int`） |

**零误杀对照**：`test_normal_file_io_is_untouched`（真文件上 `open`/`os.fdopen`/`io.FileIO`/`io.open`/`copyfileobj` 全部照常）。

---

## 5. 零回归锁：41 条"摘掉该路径守卫 → 对应锁必须变红"（原始输出）

**方法**（可复现）：在 `tests/conftest.py` 的 `_GUARD.install()` 之后**插入一行/几行**
"把该路径还回原函数"的语句（等价于"把这条路径的守卫摘掉"），跑
`pytest tests/test_k61_llm_egress_guard.py -q --tb=no -k <锁>`，看对应锁是否变红，然后 `git checkout` 还原。
脚本：`/dev/shm/k61r9/teeth.py`（41 条用例的表也在里面）。

**为什么要"归因断言"**：同族多层会互相兜住 —— 单摘 `wrap_socket` 时 `SSLSocket._create` 仍会拦，
锁看着是绿的，但**它声称覆盖的那一层坏了也没人知道**（r8 的教训）。因此关键锁都加了
`_assert_blocked_by(ei.value, "<本层标签>")`：**拦截必须来自本用例声称的那一层**。
归因断言的加与不加，在下面表里能直接对比（`SSLSocket._create` 两行）。

```
$ cd /dev/shm/k61r9 && TMPDIR=/dev/shm nice -n 10 /home/a/fortune-agent/.venv/bin/python teeth.py
== k61 r9 锁的牙：摘掉守卫 → 对应锁是否变红 ==
A socket.getaddrinfo                                 RED（锁有牙）                 1 failed, 135 deselected in 0.20s | ['tests/test_k61_llm_egress_guard.py::TestGuardLayers::test_socket_getaddrinfo_blocked']
B socket.create_connection                           RED（锁有牙）                 1 failed, 135 deselected in 0.19s | ['tests/test_k61_llm_egress_guard.py::TestGuardLayers::test_socket_create_connection_blocked']
C socket.socket.connect                              RED（锁有牙）                 1 failed, 135 deselected in 0.19s | ['tests/test_k61_llm_egress_guard.py::TestAddressJudgment::test_public_ip_literal_direct_socket_blocked']
C socket.socket.connect_ex                           RED（锁有牙）                 1 failed, 135 deselected in 0.20s | ['tests/test_k61_llm_egress_guard.py::TestAddressJudgment::test_public_ip_literal_connect_ex_blocked']
D socket.send                                        RED（锁有牙）                 1 failed, 135 deselected in 0.40s | ['tests/test_k61_llm_egress_guard.py::TestFragmentWriteBypass::test_memoryview_send_blocked']
D socket.sendall                                     RED（锁有牙）                 1 failed, 135 deselected in 0.18s | ['tests/test_k61_llm_egress_guard.py::TestFragmentWriteBypass::test_single_block_still_blocked']
D socket.sendmsg                                     RED（锁有牙）                 1 failed, 135 deselected in 0.19s | ['tests/test_k61_llm_egress_guard.py::TestFragmentWriteBypass::test_sendmsg_blocked']
D socket.sendto                                      RED（锁有牙）                 1 failed, 135 deselected in 0.19s | ['tests/test_k61_llm_egress_guard.py::TestSendtoAndWritevBlocked::test_sendto_blocked']
D os.write                                           RED（锁有牙）                 1 failed, 135 deselected in 0.40s | ['tests/test_k61_llm_egress_guard.py::TestFragmentWriteBypass::test_os_write_blocked']
D os.writev                                          RED（锁有牙）                 1 failed, 135 deselected in 0.40s | ['tests/test_k61_llm_egress_guard.py::TestSendtoAndWritevBlocked::test_os_writev_blocked']
E httpx 同步传输层                                        RED（锁有牙）                 1 failed, 135 deselected in 0.42s | ['tests/test_k61_llm_egress_guard.py::TestGuardLayers::test_httpx_sync_real_transport_blocked']
E httpx 异步传输层                                        RED（锁有牙）                 1 failed, 135 deselected in 0.22s | ['tests/test_k61_llm_egress_guard.py::TestGuardLayers::test_httpx_async_real_transport_blocked']
os.sendfile                                          RED（锁有牙）                 1 failed, 135 deselected in 0.39s | ['tests/test_k61_llm_egress_guard.py::TestSendFamilyApiSurface::test_aliases_are_actually_blocked[os.sendfile]']
posix.write                                          RED（锁有牙）                 1 failed, 1 passed, 134 deselected in 0.60s | ['tests/test_k61_llm_egress_guard.py::TestSendFamilyApiSurface::test_aliases_are_actually_blocked[posix.write]']
posix.writev                                         RED（锁有牙）                 1 failed, 135 deselected in 0.19s | ['tests/test_k61_llm_egress_guard.py::TestSendFamilyApiSurface::test_aliases_are_actually_blocked[posix.writev]']
posix.sendfile                                       RED（锁有牙）                 1 failed, 135 deselected in 0.39s | ['tests/test_k61_llm_egress_guard.py::TestSendFamilyApiSurface::test_aliases_are_actually_blocked[posix.sendfile]']
socket.socket.sendfile                               RED（锁有牙）                 1 failed, 135 deselected in 0.19s | ['tests/test_k61_llm_egress_guard.py::TestSendFamilyApiSurface::test_aliases_are_actually_blocked[socket.socket.sendfile]']
os.splice                                            RED（锁有牙）                 1 failed, 135 deselected in 0.57s | ['tests/test_k61_llm_egress_guard.py::TestSameFamilyWritePaths::test_os_splice_into_socket_blocked']
posix.splice                                         RED（锁有牙）                 1 failed, 135 deselected in 0.59s | ['tests/test_k61_llm_egress_guard.py::TestSameFamilyWritePaths::test_posix_write_aliases_are_blocked[posix.splice]']
os.eventfd_write                                     RED（锁有牙）                 1 failed, 135 deselected in 0.78s | ['tests/test_k61_llm_egress_guard.py::TestSameFamilyWritePaths::test_os_eventfd_write_to_socket_blocked']
posix.eventfd_write                                  RED（锁有牙）                 1 failed, 135 deselected in 0.78s | ['tests/test_k61_llm_egress_guard.py::TestSameFamilyWritePaths::test_posix_write_aliases_are_blocked[posix.eventfd_write]']
os.fdopen                                            RED（锁有牙）                 1 failed, 135 deselected in 0.78s | ['tests/test_k61_llm_egress_guard.py::TestFileObjectOverSocketFd::test_os_fdopen_socket_fd_blocked']
io.open                                              RED（锁有牙）                 1 failed, 135 deselected in 0.77s | ['tests/test_k61_llm_egress_guard.py::TestFileObjectOverSocketFd::test_io_open_socket_fd_blocked']
builtins.open                                        RED（锁有牙）                 1 failed, 135 deselected in 0.79s | ['tests/test_k61_llm_egress_guard.py::TestFileObjectOverSocketFd::test_builtins_open_socket_fd_blocked']
io.FileIO                                            RED（锁有牙）                 1 failed, 135 deselected in 0.77s | ['tests/test_k61_llm_egress_guard.py::TestFileObjectOverSocketFd::test_io_fileio_socket_fd_blocked']
shutil.copyfileobj                                   RED（锁有牙）                 1 failed, 135 deselected in 1.21s | ['tests/test_k61_llm_egress_guard.py::TestFileObjectOverSocketFd::test_copyfileobj_into_socket_file_blocked']
SSLContext.wrap_socket                               RED（锁有牙）                 1 failed, 135 deselected in 0.80s | ['tests/test_k61_llm_egress_guard.py::TestTlsSniCannotLie::test_lying_sni_to_local_endpoint_blocked']
SSLContext.wrap_bio（单独摘）                             RED（锁有牙）                 1 failed, 135 deselected in 0.52s | ['tests/test_k61_llm_egress_guard.py::TestTlsEntryPointsHooked::test_wrap_bio_non_whitelisted_sni_blocked']
SSLSocket._create（单独摘，锁=归因版）                         RED（锁有牙）                 1 failed, 135 deselected in 0.76s | ['tests/test_k61_llm_egress_guard.py::TestTlsEntryPointsHooked::test_sslsocket_create_lying_sni_blocked']
SSLSocket._create（单独摘，锁=无归因版→被同族兜住）                  GREEN（没牙 / 被同族兜住）        1 passed, 135 deselected in 0.76s | -
SSLObject._create（单独摘）                               RED（锁有牙）                 1 failed, 135 deselected in 0.51s | ['tests/test_k61_llm_egress_guard.py::TestTlsEntryPointsHooked::test_sslobject_create_non_whitelisted_sni_blocked']
SSLContext._wrap_socket 遮蔽（单独摘）                      RED（锁有牙）                 1 failed, 135 deselected in 0.81s | ['tests/test_k61_llm_egress_guard.py::TestTlsEntryPointsHooked::test_sslcontext_wrap_socket_shadow_lying_sni_blocked']
SSLContext._wrap_bio 遮蔽（单独摘）                         RED（锁有牙）                 1 failed, 135 deselected in 0.34s | ['tests/test_k61_llm_egress_guard.py::TestTlsEntryPointsHooked::test_sslcontext_wrap_bio_shadow_non_whitelisted_sni_blocked']
B 整族（create_connection + getaddrinfo）                RED（锁有牙）                 1 failed, 135 deselected in 0.20s | ['tests/test_k61_llm_egress_guard.py::TestGuardLayers::test_socket_create_connection_blocked']
sendfile 整族（socket.sendfile + os/posix.sendfile + send） RED（锁有牙）                 1 failed, 135 deselected in 0.40s | ['tests/test_k61_llm_egress_guard.py::TestSendFamilyApiSurface::test_aliases_are_actually_blocked[socket.socket.sendfile]']
文件对象整族（os.fdopen + io.open + builtins.open）          RED（锁有牙）                 1 failed, 135 deselected in 0.59s | ['tests/test_k61_llm_egress_guard.py::TestFileObjectOverSocketFd::test_os_fdopen_socket_fd_blocked']
wrap_socket 三层整族（wrap_socket + SSLSocket._create + _wrap_socket 遮蔽） RED（锁有牙）                 1 failed, 135 deselected in 0.78s | ['tests/test_k61_llm_egress_guard.py::TestTlsSniCannotLie::test_lying_sni_to_local_endpoint_blocked']
wrap_bio 整族（wrap_bio + SSLObject._create + _wrap_bio 遮蔽） RED（锁有牙）                 1 failed, 135 deselected in 1.10s | ['tests/test_k61_llm_egress_guard.py::TestTlsEntryPointsHooked::test_wrap_bio_non_whitelisted_sni_blocked']
SSLSocket._create 整族（_create + _wrap_socket 遮蔽）      RED（锁有牙）                 1 failed, 135 deselected in 0.76s | ['tests/test_k61_llm_egress_guard.py::TestTlsEntryPointsHooked::test_sslsocket_create_lying_sni_blocked']
wrap_socket 整族（wrap_socket + _wrap_socket 遮蔽）        RED（锁有牙）                 1 failed, 135 deselected in 0.80s | ['tests/test_k61_llm_egress_guard.py::TestTlsSniCannotLie::test_lying_sni_to_local_endpoint_blocked']
SSLObject._create 整族（_create + _wrap_bio 遮蔽）         RED（锁有牙）                 1 failed, 135 deselected in 0.19s | ['tests/test_k61_llm_egress_guard.py::TestTlsEntryPointsHooked::test_sslobject_create_non_whitelisted_sni_blocked']

== 汇总 ==
总 41 条：RED 40 / GREEN 1 / 其它 0
GREEN（需要组合摘除或说明）: ['SSLSocket._create（单独摘，锁=无归因版→被同族兜住）']
conftest 已还原: (干净)
```

**怎么读那 1 条 GREEN**：它是**刻意保留的对照** —— 用**没加归因**的那条锁去验
"只摘 `SSLSocket._create` 一层"：结果仍然绿（因为 `_wrap_socket` 遮蔽层接住了）
⇒ 证明"多层冗余确实存在"，也证明**没有归因的锁会漏掉单层失效**。
同一路径换成**加了归因**的锁（`test_sslsocket_create_lying_sni_blocked`）→ **RED** ✓。
其余"整族摘除"的 6 条也是同一目的（把同族全摘掉后必须红）。

**没有牙 / 不成立的情形我一条都没有往"有牙"里凑**：上面每一条 RED 都是**真的把守卫摘掉后跑出来的红**，
不是推断。归因断言的加与不加在同一张表里可见（`SSLSocket._create` 两行红/绿对照）。

### 5.1 测试数字（最终态）

```
$ TMPDIR=/dev/shm nice -n 10 /home/a/fortune-agent/.venv/bin/python -m pytest tests/test_k61_llm_egress_guard.py -q
136 passed in 17.70s                      （r9 改前：91 passed）

$ … pytest tests/test_k61_e2e_smoke.py tests/test_k61_llm_egress_guard.py \
        tests/test_k61_private_collection_guard.py tests/test_k61_public_url_product_bug.py \
        tests/test_k61_repo_hygiene.py tests/test_k61_test_env_isolation.py -q
195 passed, 2 skipped in 22.33s           （2 skip = e2e opt-in 门控）

$ … 上面 6 个文件 + tests/test_adaptive_advisor.py + tests/test_bot.py -q -rs
276 passed, 5 skipped in 30.02s
SKIPPED [1] tests/test_k61_e2e_smoke.py:119 真实 LLM 端到端冒烟：默认 skip，需 K61_E2E=1 显式触发
SKIPPED [1] tests/test_k61_e2e_smoke.py:137 同上
SKIPPED [3] tests/test_adaptive_advisor.py 缺少 ZHIPU_API_KEY（免费 glm-4-flash 路由）
```

（后一次是"**只跑一次**的大范围"：本轮改了 `builtins.open`/`io.open`/TLS 判据这些**全局**点位，
必须有一次跨文件回归；按高峰时段纪律**未跑全量**，其余时间只跑改动文件。）

---

## 6. 没覆盖的（**显式清单**，代码里同步登记在 `DECLARED_*`）

| # | 没覆盖的路径 | 性质 | 实测证据 | 备注 |
|---|---|---|---|---|
| 1 | `ssl.SSLContext.wrap_bio` / `ssl.SSLObject._create` / `ssl.SSLContext._wrap_bio` | **降级：只判 SNI，撒谎可绕** | `test_declared_wrap_bio_sni_can_lie`（取证锁） | 无对端可判；不能改成一律拒（会打死 asyncio HTTPS） |
| 2 | `_ssl._SSLContext._wrap_socket/_wrap_bio` 的**未绑定直调** | 绕过遮蔽层 | `test_declared_unbound_c_level_wrap_bio_can_slip` | C 类型不可赋值 |
| 3 | `from _io import FileIO` 写 socket fd | 绕过 `io.FileIO` 钩子 | `test_declared_io_fileio_c_level_import_can_slip` | 同一 C 类型第二条绑定；`copyfileobj` 那层仍能兜住经它的写入 |
| 4 | `_socket.socket.send/sendall/sendmsg/sendto/sendfile/sendmsg_afalg` | 不可挂钩（C 不可变类型） | 取证锁 `test_declared_socket_c_type_is_immutable`（实测 `TypeError`） | 兜底=pin（无生产 key） |
| 5 | 子进程（子解释器/curl/fork+exec） | 进程边界 | `test_declared_subprocess_can_slip_and_pin_is_inherited` | 同时实测**pin 被继承**（`DEEPSEEK_API_KEY` 子进程里为 `''`） |
| 6 | ctypes 直调 libc `write(2)` | 不经 Python 属性查找 | `test_declared_ctypes_raw_syscall_can_slip` | 同上兜底 |
| 7 | **残留**：env 声称明文代理 + 撒谎 SNI（§2.3a） | 合取判据的已知缺口 | `test_declared_env_plaintext_proxy_endpoint_plus_lying_sni_can_slip` | **未修**，需拍板（§2.3） |
| — | **过拦面**：`proxies=` 参数配明文代理（§2.3b） | 不是漏拦，是过拦 | 取证锁 `test_declared_param_proxy_over_block`（专用地址 `.5`）+ `probe3.py ok2b_param_proxy` | 本仓 0 命中 |
| — | `ioctl` / `fcntl` 这类**设备控制**接口 | **范围外** | **未实测**（只有推理：对 socket 无"写字节"语义） | 若审查者认为算通道，请明示 → 下轮进枚举 |

**每条"没覆盖的"都有取证锁**（§6 全表：BIO 降级 / C 层直调 / `_io.FileIO` / `_socket` 不可挂钩 /
子进程 / ctypes / env 代理端点残留 / 参数代理过拦 —— 8 条各有一条 `test_declared_*`）：
它们的"红"意味着**声明与事实不符**（洞被修好了或行为变了）→ 逼人回来更新声明。

**枚举完备性**：**无法证明**。绊线是启发式的（`eventfd_write` 就是它自己漏过的那类），
所以枚举逐条按文档族对照；"新名字溜过"只能靠绊线逼人做决定，不能靠它保证无漏。

---

## 7. 没验证的 / 无法验证的（不许含糊）

1. **一次真实 GLM 调用都没有**：本机无 `ZHIPU_API_KEY`、无 `.env` → 2 条 e2e 冒烟
   （`K61_E2E=1` 门控）全程 skip。§2.2 的"不误杀"是**自建明文代理桩 + 自签证书**下测的
   （真链路里 TLS 客户端的调用形态一致，但**没有在真实智谱端点验证过**）。
2. **全量 `pytest` 未跑**（高峰时段纪律：禁止全量）。跨文件回归只做了 §5.1 那一次（276 passed）。
   其余测试文件**未验证**是否受 `builtins.open` / TLS 判据改动影响。
3. **真代理机器未验证**：本机 `env | grep -i proxy` 为空。§2.2 的代理用例是**本地桩**。
4. **async 家族（aiohttp / anyio / asyncio sslproto）只做到"aiohttp 显式 `proxy="https://…"`"这一条**
   （r7/r8/r9 三方对比已验证，见 §3.2）；**asyncio `sslproto` / anyio 的原生用法没有端到端跑过**
   （只有 `wrap_bio` / `SSLObject._create` 一级的单测）。
5. **§2.3b 的过拦**只在隔离探针里复现过，**没有**在真实生产代码路径上复现（本仓无此用法）。
6. **pin 的结构性兜底**只验证了"环境变量被子进程继承"，**没有**验证"端到端真调用被 pin 挡住"
   （那需要一次真 DeepSeek 调用 —— 红线禁止）。
7. **§1 里 C1b 那条改前探针结果不确定**（`OTHER-EXC:ConnectError`、监听器握手失败）：
   在本探针的写法下它**没有**给出"明文出线"的干净证据。C1 的"改前会漏"结论由
   **C1a / C1c / C2c1**（都有干净的解密证据）支撑，**不由 C1b 支撑**。
8. **`_sendfile_use_send` 回退路径**（无 `os.sendfile` 的平台才走）在本机**未单独实测**；
   它按"内部实现，走 `self.send` 循环"分类（分类依据是 CPython 源码 + `send` 钩子的行为锁）。

---

## 8. 过度声明更正（本轮头号问题）

r8 报告文件在 **`/home/a/k61-wt/.superpowers/sdd/task-k61-report.md`（另一个工作树，不在本批允许改动范围内）**
—— 我**只读**不改。被证伪的表述与更正后的写法（本批报告 + 代码注释）：

| 原表述（r8） | 实测 | 更正后的写法 |
|---|---|---|
| "每条路径都有**行为锁**（真调一次、必须拦）"（r8-Important 1） | `grep wrap_bio tests/` = **0 命中**；把整条守卫摘掉，该文件仍 **91 passed** | 本批为每条路径补锁，并给出 **41 条"摘掉→变红"实测（40 RED / 1 刻意对照）**；未做到的（BIO 降级、C 层直调、`_io.FileIO`、`_socket.*`）**列进 §6，不算已覆盖** |
| "r8-C1：异步家族……与 `wrap_socket` **同一判据**" | `wrap_bio` 拿不到对端 → 判据**不可能**相同 | 代码/报告改为**降级声明**（只判 SNI、可撒谎），并加取证锁 |
| "r8 全是收紧"（诚实披露 3） | 删掉 peer 检查 = **放宽**（SNI 可撒谎） | 本批报告如实写明："r8 为收窄误杀删掉 peer 检查，**引入了 C1**" |
| "r7 那两条 sendfile 豁免理由是造假的"（✅ 这条 r8 做对了，保留） | — | 本批沿用：豁免理由若站不住就收回，改 fail-closed |
| "守卫（`DeepSeekEgressBlocked`）退为兜底"（多处） | 成立，但**兜底不等于每条路径都有锁** | 本批把"哪些路径有锁、哪些只有声明"分开写（§5 / §6） |

**本批自己的诚实披露**（避免重蹈覆辙）：
- §5 那 41 条里，**40 条是实测红**，1 条绿是**刻意对照**（不是"漏了"）；
- **§6 的 7 条我一条都不算作"已覆盖"**；
- §7 里 8 条"没验证/无法验证"，**没有把它们写成"应该没问题"**；
- r8 关于 aiohttp 漏网的断言我**复核了**（r7/r8/r9 三方对比，见 §3.2）—— 结论是**成立**；
  复核之前它在我的清单里是"未验证"，复核之后才写进正文（顺序如此，不是先写结论再补证据）。

---

## 9. 复现方式（命令清单）

```bash
# 0) 环境
PY=/home/a/fortune-agent/.venv/bin/python        # Python 3.12.3
cd /home/a/k61-r9-wt

# 1) 改前复现（基点代码，只读）
git show d926a95:tests/conftest.py > /dev/shm/k61r9/conftest_r8.py
TMPDIR=/dev/shm nice -n 10 $PY /dev/shm/k61r9/probe_before.py

# 2) 改后：攻击面 + 正向对照 + 残留（每用例独立监听器）
TMPDIR=/dev/shm nice -n 10 $PY /dev/shm/k61r9/probe2.py

# 3) 隔离复测（每进程只跑一条，排除会话内学习状态顺序影响）
for c in ok2_env_proxy ok2b_param_proxy c1a_fresh res2; do \
  TMPDIR=/dev/shm nice -n 10 $PY /dev/shm/k61r9/probe3.py $c; done

# 4) 锁的牙（41 条：摘守卫 → 跑锁 → 还原）
TMPDIR=/dev/shm nice -n 10 $PY /dev/shm/k61r9/teeth.py      # 每条跑完自动 git checkout 还原

# 4b) aiohttp 显式 proxy 的三方对比（r7 / r8 / r9）
for w in r7 r8 r9; do TMPDIR=/dev/shm nice -n 10 $PY /dev/shm/k61r9/probe_aiohttp.py $w; done

# 5) 测试
TMPDIR=/dev/shm nice -n 10 $PY -m pytest tests/test_k61_llm_egress_guard.py -q          # 136 passed
TMPDIR=/dev/shm nice -n 10 $PY -m pytest tests/test_k61_*.py -q                          # 195 passed / 2 skipped
```

**红线自查**：`git diff --name-only d926a95 HEAD` = `tests/conftest.py` + `tests/test_k61_llm_egress_guard.py`
（`src/` **零改动**）；全程**没有**任何字节发往 `api.deepseek.com`（所有"目标"都是本机
`127.0.0.2/3/4` 的自建监听器，且监听器**从不转发**）；未触碰 `/home/a/data/userdata/fortune.db`
与 `/home/a/fortune-run`；未合并、未推送 main。

---

## 附录 A：§5 那 41 条的**精确注入语句**（复现用）

做法：在 `tests/conftest.py` 的 `_GUARD.install()` **之后**插入下面第二列的语句
（等价于"把该路径的守卫还回原函数"），跑第三列的 `-k` 选择器，看对应锁是否变红，然后还原。

| 用例（标签） | 注入语句（摘掉的守卫） | `-k` 选择器 |
|---|---|---|
| A socket.getaddrinfo | `socket.getaddrinfo = _GUARD._orig_getaddrinfo` | `test_socket_getaddrinfo_blocked` |
| B socket.create_connection | `socket.create_connection = _GUARD._orig_create_connection` | `test_socket_create_connection_blocked` |
| C socket.socket.connect | `socket.socket.connect = _GUARD._orig_connect` | `test_public_ip_literal_direct_socket_blocked` |
| C socket.socket.connect_ex | `socket.socket.connect_ex = _GUARD._orig_connect_ex` | `test_public_ip_literal_connect_ex_blocked` |
| D socket.send | `socket.socket.send = _GUARD._orig_send` | `test_memoryview_send_blocked` |
| D socket.sendall | `socket.socket.sendall = _GUARD._orig_sendall` | `test_single_block_still_blocked` |
| D socket.sendmsg | `socket.socket.sendmsg = _GUARD._orig_sendmsg` | `test_sendmsg_blocked` |
| D socket.sendto | `socket.socket.sendto = _GUARD._orig_sendto` | `test_sendto_blocked` |
| D os.write | `os.write = _GUARD._orig_os_write` | `test_os_write_blocked` |
| D os.writev | `os.writev = _GUARD._orig_os_writev` | `test_os_writev_blocked` |
| E httpx 同步传输层 | `import httpx as _hx` + `_hx.HTTPTransport.handle_request = _GUARD._orig_httpx_sync` | `test_httpx_sync_real_transport_blocked` |
| E httpx 异步传输层 | `import httpx as _hx` + `_hx.AsyncHTTPTransport.handle_async_request = _GUARD._orig_httpx_async` | `test_httpx_async_real_transport_blocked` |
| os.sendfile | `os.sendfile = _GUARD._orig_os_sendfile` | `test_aliases_are_actually_blocked and os.sendfile` |
| posix.write | `posix.write = _GUARD._orig_posix_write` | `test_aliases_are_actually_blocked and posix.write` |
| posix.writev | `posix.writev = _GUARD._orig_posix_writev` | `test_aliases_are_actually_blocked and posix.writev` |
| posix.sendfile | `posix.sendfile = _GUARD._orig_posix_sendfile` | `test_aliases_are_actually_blocked and posix.sendfile` |
| socket.socket.sendfile | `socket.socket.sendfile = _GUARD._orig_sock_sendfile` | `test_aliases_are_actually_blocked and socket.socket.sendfile` |
| os.splice | `os.splice = _GUARD._orig_os_splice` | `test_os_splice_into_socket_blocked` |
| posix.splice | `posix.splice = _GUARD._orig_posix_splice` | `test_posix_write_aliases_are_blocked and posix.splice` |
| os.eventfd_write | `os.eventfd_write = _GUARD._orig_eventfd_write` | `test_os_eventfd_write_to_socket_blocked` |
| posix.eventfd_write | `posix.eventfd_write = _GUARD._orig_posix_eventfd_write` | `test_posix_write_aliases_are_blocked and posix.eventfd_write` |
| os.fdopen | `os.fdopen = _GUARD._orig_fdopen` | `test_os_fdopen_socket_fd_blocked` |
| io.open | `io.open = _GUARD._orig_io_open` | `test_io_open_socket_fd_blocked` |
| builtins.open | `builtins.open = _GUARD._orig_builtins_open` | `test_builtins_open_socket_fd_blocked` |
| io.FileIO | `io.FileIO = _GUARD._orig_fileio` | `test_io_fileio_socket_fd_blocked` |
| shutil.copyfileobj | `shutil.copyfileobj = _GUARD._orig_copyfileobj` | `test_copyfileobj_into_socket_file_blocked` |
| SSLContext.wrap_socket | `ssl.SSLContext.wrap_socket = _GUARD._orig_wrap_socket` | `test_lying_sni_to_local_endpoint_blocked` |
| SSLContext.wrap_bio（单独摘） | `ssl.SSLContext.wrap_bio = _GUARD._orig_wrap_bio` | `test_wrap_bio_non_whitelisted_sni_blocked` |
| SSLSocket._create（单独摘，锁=归因版） | `ssl.SSLSocket._create = _GUARD._orig_sslsocket_create` | `test_sslsocket_create_lying_sni_blocked` |
| SSLSocket._create（单独摘，锁=无归因版→被同族兜住） | `ssl.SSLSocket._create = _GUARD._orig_sslsocket_create` | `test_sslsocket_create_non_whitelisted_sni_blocked` |
| SSLObject._create（单独摘） | `ssl.SSLObject._create = _GUARD._orig_sslobject_create` | `test_sslobject_create_non_whitelisted_sni_blocked` |
| SSLContext._wrap_socket 遮蔽（单独摘） | `del ssl.SSLContext._wrap_socket` | `test_sslcontext_wrap_socket_shadow_lying_sni_blocked` |
| SSLContext._wrap_bio 遮蔽（单独摘） | `del ssl.SSLContext._wrap_bio` | `test_sslcontext_wrap_bio_shadow_non_whitelisted_sni_blocked` |
| B 整族（create_connection + getaddrinfo） | `socket.create_connection = _GUARD._orig_create_connection` + `socket.getaddrinfo = _GUARD._orig_getaddrinfo` | `test_socket_create_connection_blocked` |
| sendfile 整族（socket.sendfile + os/posix.sendfile + send） | `socket.socket.sendfile = _GUARD._orig_sock_sendfile` + `os.sendfile = _GUARD._orig_os_sendfile` + `posix.sendfile = _GUARD._orig_posix_sendfile` + `socket.socket.send = _GUARD._orig_send` + `socket.socket.sendall = _GUARD._orig_sendall` | `test_aliases_are_actually_blocked and socket.socket.sendfile` |
| 文件对象整族（os.fdopen + io.open + builtins.open） | `os.fdopen = _GUARD._orig_fdopen` + `io.open = _GUARD._orig_io_open` + `builtins.open = _GUARD._orig_builtins_open` | `test_os_fdopen_socket_fd_blocked` |
| wrap_socket 三层整族（wrap_socket + SSLSocket._create + _wrap_socket 遮蔽） | `ssl.SSLContext.wrap_socket = _GUARD._orig_wrap_socket` + `ssl.SSLSocket._create = _GUARD._orig_sslsocket_create` + `del ssl.SSLContext._wrap_socket` | `test_lying_sni_to_local_endpoint_blocked` |
| wrap_bio 整族（wrap_bio + SSLObject._create + _wrap_bio 遮蔽） | `ssl.SSLContext.wrap_bio = _GUARD._orig_wrap_bio` + `ssl.SSLObject._create = _GUARD._orig_sslobject_create` + `del ssl.SSLContext._wrap_bio` | `test_wrap_bio_non_whitelisted_sni_blocked` |
| SSLSocket._create 整族（_create + _wrap_socket 遮蔽） | `ssl.SSLSocket._create = _GUARD._orig_sslsocket_create` + `del ssl.SSLContext._wrap_socket` | `test_sslsocket_create_lying_sni_blocked` |
| wrap_socket 整族（wrap_socket + _wrap_socket 遮蔽） | `ssl.SSLContext.wrap_socket = _GUARD._orig_wrap_socket` + `del ssl.SSLContext._wrap_socket` | `test_lying_sni_to_local_endpoint_blocked` |
| SSLObject._create 整族（_create + _wrap_bio 遮蔽） | `ssl.SSLObject._create = _GUARD._orig_sslobject_create` + `del ssl.SSLContext._wrap_bio` | `test_sslobject_create_non_whitelisted_sni_blocked` |

还原：`git checkout -- tests/conftest.py`（`teeth.py` 每条跑完自动还原；结束时 `git status --porcelain` 为空）。

**探针脚本**（本报告所有原始输出的产生者，均在 `/dev/shm/k61r9/`，**易失** —— 内容与命令见 §1/§2/§9）：
`probe_before.py`（改前逐条复现，加载 `conftest_r8.py`）、`probe2.py`（改后攻击面+正向对照）、
`probe3.py`（隔离复测：`ok2_env_proxy` / `ok2b_param_proxy` / `c1a_fresh` / `res2`）、
`probe_aiohttp.py`（r7/r8/r9 三方对比）、`teeth.py`（上表的执行器）。
监听器共同点：绑定 `127.0.0.2/.3/.4/.5`，**只记录、从不转发**；TLS 模式用自签证书
（CN/SAN=`open.bigmodel.cn`）**解密后**记录首行 —— 以此证明"明文进了 TLS 之内"而不是猜。
