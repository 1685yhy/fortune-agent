"""账号/隐私相关的**用户可见文案**（唯一事实源）—— k78-必修2。

为什么要集中（根因）：同一句"说给用户听的话"此前在 3 个文件里各写一份，必然漂移 ——
实测到的分裂：

  - `src/api/user.py` 的**登录 403** 与**注销响应**写「账号已注销，数据保留 90 天后删除」，
    缺"支付流水依法留存"这一例外；而小程序的同义文案三处**都带**该例外 ⇒ 同一事实
    在客户端与服务端两个口径；
  - `src/main.py` 与 `src/security/router.py` **重复**同一句「所有个人数据已删除
    （不可恢复）」，且这句话本身两处不实：「所有」不含依法留存的
    `payments` / `midas_orders`；「不可恢复」与「备份仍覆盖时可人工尝试找回」冲突
    （文件类内容不在备份内，数据库记录在备份窗口内**可尝试**）。

文案是**同一份事实**的表述；事实改一次就要改 N 处，就一定会漏改。故把"账号注销 /
数据删除"这两条事实的面向用户表述收在这里，调用方只引常量。

## 本模块在守卫的扫描面内 —— 边界的**准确**表述（k79-必修2 更正）

守卫：`miniprogram/tests/k71_privacy_consistency.test.js` §14「后端用户可见字符串面」。

**改前这里写的是"绕过本模块直接写内联字面量**不会逃逸**"—— 这句话是错的**（复审
实测四类逃逸，守卫当时**全绿**）。现在把边界写清楚，别再说过头：

拦得住的形态（两条路，都必须过）：
  - `detail=` / `message=` **关键字实参**，值为：字面量、**可静态折叠**的表达式
    （`"a" + "b"` / `"%s…" % x` / `"…".format(x)`）、模块级常量名、**函数内局部
    常量名**（`zz = "…"; raise …(detail=zz)`）；
  - 字典字面量的 10 个键（`message/detail/msg/error/reason/hint/disclaimer/
    action/label/title/description`）的值，同上；
  - **宽面**：`src/**/*.py` 里**全部中文字符串常量 + 折叠结果**逐条过禁语表
    （本仓实测 3.4 万条对 4 条禁语**零命中**，故不设"汉字数据表白名单"）。

**仍在面外**（如实列出，别当成已覆盖）：
  - 运行时才拼出来的文案：变量插值后再拼接、`.join()` / `.replace()` 的结果、
    从数据库/配置读来的字符串、`f"{X}…"` 里 X 的取值；
  - 跨函数/跨模块传递后才进入 `detail=` 的值（本扫描器只做**局部 + 模块级**常量
    解析，不做跨函数数据流）；
  - 非这 10 个键的字典键（如 `{"desc": "…"}`）、列表/元组里的文案、`print` 日志；
  - 非 `src/` 路径下的文案（脚本、部署文件、SQL 里的字面量）。

⇒ 结论：**"`detail=`/`message=`/这 10 个键 + 全部中文字符串常量"这一面不会逃逸；
上面那几类仍在面外**，写文案时别指望守卫替你把关。

## 这几条文案"谁看得见"——可见面要说准（k79-必修3 更正）

k78 为这几条文案的可见性背书时用了「客户端 ≥9 处渲染 `err.detail`」。逐条核过，
**这个理由说过头了**（结论——"该改"——没错，理由不准）：

  - 作为**一般性判断成立**：`miniprogram/utils/api.js` 对非 2xx（含 403）
    `reject(res.data || {error:'请求失败'})`，整个响应体（含 `detail`）确实会交给
    调用点；客户端也确实有多处把 `err.detail` 直接渲染给用户（如
    `pages/settings/settings.js` 手机号绑定处）。
  - 但这三条常量**当前在小程序里一处都不显示**：
      · `ACCOUNT_CANCELLED_NOTICE` 作**登录 403** 的 `detail` → `app.js` 的
        `catch` 只 `console.warn` + 进本地模式，**不渲染**；
      · 同一条常量作**注销响应**的 `message` → `pages/settings/settings.js` 的
        `.then()` 只 `setData` + 弹自己的 toast「账号已注销」，**完全忽略响应体**；
      · `USER_DATA_PURGED_NOTICE`（`DELETE /api/user/data/{id}`、
        `DELETE /api/security/user/{id}/data`，见 `main.py` / `security/router.py`）
        与 `DATA_RETENTION_ACTION_NOTICE`（`GET /retention/info`）→
        **小程序全仓零调用点**。

⇒ 本模块的正确理由是「**API / OpenAPI 接入方可见面**」：直连接口的调用方、对外
可读的 OpenAPI 文档（`security/router.py` 的 `confirm` description 已按这个口径写），
以及将来任何新接线。**不要**用"客户端会渲染"当理由。

事实依据（改文案前必须逐条核对，依据都在代码里）：
  - 注销：`users.status='cancelled'`，业务数据保留 **90 天**，期满由
    `storage.dao.cleanup_cancelled_accounts()` 物理删除（范围 =
    `storage/models.py::ACCOUNT_PURGE_TABLES`）；**唯一依法留存的是支付流水**
    （`ACCOUNT_RETAIN_TABLES` = payments / midas_orders，《中华人民共和国电子商务法》
    第三十一条，保存不少于三年）；**注销时立即删除的是"对外公开面"**——匿名分享记录
    与本人报告文件/分享图（`storage/dao.py::purge_report_files`）；
  - 找回：**没有自助恢复入口**（注销后登录 403）；90 天保留期内可申请人工从备份尝试
    （每日一次、保留最近 14 份），**文件类内容不在备份范围内**，故**不保证成功**；
  - 注销后**登录入口立即关闭**（`src/api/user.py` 登录拦截 status=cancelled → 403）。
"""
from __future__ import annotations

#: 登录被拦截（账号已注销）时的 403 detail。**同时**是注销接口的响应 message ——
#: 同一事实、同一句话，两处引用同一常量（改前是两份字面量，缺例外的那份会漂移）。
ACCOUNT_CANCELLED_NOTICE = (
    "账号已注销，数据保留 90 天后删除（支付流水等依法需留存的交易记录除外）"
)

#: 用户触发「删除我的数据」（PIPL 第 47 条）接口的响应 message。
#: 说清三件事：① 删了；② 依法留存的支付流水例外；③ 找回的真实条件（文件类内容
#: 不在备份内、数据库记录仅在备份窗口内可尝试，不保证成功）——不再写"所有/不可恢复"。
USER_DATA_PURGED_NOTICE = (
    "个人数据已删除（依法需留存的支付流水除外）。"
    "文件类内容不在备份范围内、无法找回；数据库记录仅在备份仍覆盖的窗口内"
    "可由人工尝试找回，不保证成功"
)

#: 数据保留策略（`GET /retention/info`）里"到期执行什么动作"的可见描述。
#: 改前是「数据删除（不可恢复）」——与"备份仍覆盖时可人工尝试找回"冲突；
#: 这里用本仓已统一的口径「不可自助恢复」（与隐私文案的"注销没有自助恢复入口"同词）。
DATA_RETENTION_ACTION_NOTICE = "数据删除（不可自助恢复）"

#: 「删除我的数据」**没删干净**时的响应 message（k79-M1）。
#: 改前无论删成没删干净都回 `USER_DATA_PURGED_NOTICE`（"个人数据已删除…"）——
#: 表删除失败每处只写一行日志、计数记 0，"删失败"与"本来就没有"在响应里**不可区分**，
#: 用户看到的是**与实况不符的成功**。现在真失败（非"表不存在"）走这条：
#: 说清"没删完"+"这不是没有数据"+可重试，并附 `failed_tables` 便于人工跟进。
DATA_PURGE_INCOMPLETE_NOTICE = (
    "删除未完成：仍有数据未能删除（服务器内部错误，不是「本来就没有数据」）。"
    "请稍后重试；若反复失败请联系客服人工处理"
)

__all__ = ["ACCOUNT_CANCELLED_NOTICE", "USER_DATA_PURGED_NOTICE",
           "DATA_RETENTION_ACTION_NOTICE", "DATA_PURGE_INCOMPLETE_NOTICE"]
