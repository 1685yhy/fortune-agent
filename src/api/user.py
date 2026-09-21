"""用户管理 API — 登录、资料、八字、订阅、反馈。

提供小程序所需的所有 /api/user/* 端点。
"""

import hashlib
import os
import re
import secrets
import time
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.storage.dao import UserDAO
from src.storage.session_dao import SessionDAO
from src.storage.preference_dao import PreferenceDAO
from src.storage.person_dao import (
    PersonDAO, FORM_EXPLICIT_CTX,
    # k32（A8/A9）：② 源降级直写 payload 与镜像/读路径同一实现（单一事实源）
    bazi_info_of_person, solar_time_on,
    _normalize_gender as normalize_gender_stored,
)
from src.storage.birth_profile import bazi_info_out_of_sync
from src.storage.models import connect as db_connect
from src.security.auth import require_user
# k78：账号注销/数据删除的**用户可见文案**单一事实源（此前同一句在 3 个文件各写一份，
# 服务端这份缺"支付流水依法留存"例外 ⇒ 与小程序侧口径分裂）。
from src.security.account_copy import ACCOUNT_CANCELLED_NOTICE
# k72：上传内容校验（魔数嗅探）的单一事实源——与 /api/chat/upload 共用同一实现。
from src.utils.image_sniff import (
    IMAGE_CONTENT_TYPES, IMAGE_EXT_FORMAT, sniff_image_format,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["user"])

# 全局引用，由 main.py 在 lifespan 中设置
_dao: Optional[UserDAO] = None
_session_dao: Optional[SessionDAO] = None
_preference_dao: Optional[PreferenceDAO] = None
_person_dao: Optional[PersonDAO] = None
_auth_handler = None

# dev 模式稳定 openid：进程内生成一次（可被 .env DEV_OPENID 固定为跨重启稳定）
_dev_openid = None

# ── 微信虚拟支付 session_key 存储（仅此新增；users.session_key_enc 加密落库）──
# 用户态签名 signature = hex(hmac_sha256(session_key, signData)) 需要 session_key，
# 它在 code2session 响应里返回，登录时加密保存（AES-256-GCM，与 dao.py 同款加密），
# 支付时由 src/api/pay_midas.py 解密读取。列在 users 表上懒迁移（幂等）。
_SESSION_KEY_COLUMN = "session_key_enc"

# ── Task1 登录增强：手机号绑定 + 头像昵称 ────────────────────────────
# 手机号经微信 getPhoneNumber 换取后 AES 加密存 users.phone_enc（列懒迁移见 dao.py）；
# access_token 模块级缓存（key=appid，110 分钟，官方有效期 120 分钟留缓冲）。
_ACCESS_TOKEN_CACHE: dict = {}
_ACCESS_TOKEN_TTL_SECONDS = 110 * 60

MAX_AVATAR_BYTES = 2 * 1024 * 1024
_AVATAR_CHUNK_BYTES = 64 * 1024  # 头像分块读取块大小（累计超限立即中止）
# k72：三张表全部**由单一事实源派生**（src/utils/image_sniff.py），本文件不再
# 自带一份字面量判断——头像端点此前漏掉魔数嗅探，根因就是"同一条规则写了两遍"。
# 声明类型白名单（content-type 只是客户端自述，仅作入口条件）
_ALLOWED_AVATAR_TYPES = {"image/jpeg", "image/png", "image/webp"}
# 允许的**真实内容**格式（魔数嗅探得出）
_ALLOWED_AVATAR_FORMATS = {IMAGE_CONTENT_TYPES[ct] for ct in _ALLOWED_AVATAR_TYPES}
# 允许的客户端文件名后缀（与上表同源，防两处漂移）
_ALLOWED_AVATAR_EXTS = {
    ext for ext, fmt in IMAGE_EXT_FORMAT.items() if fmt in _ALLOWED_AVATAR_FORMATS
}


def _ensure_session_key_column():
    """users 表懒迁移：增加 session_key_enc 列（幂等，缺列才 ALTER）。"""
    global _dao
    if _dao is None:
        return
    try:
        conn = db_connect(_dao.db_path, timeout=10)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        if _SESSION_KEY_COLUMN not in cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {_SESSION_KEY_COLUMN} TEXT")
            conn.commit()
            logger.info("users 表已增加 %s 列（session_key 加密存储）", _SESSION_KEY_COLUMN)
        conn.close()
    except Exception as e:
        logger.warning("session_key 列迁移失败: %s", e)


def _encrypt_session_key(key: str) -> str:
    from src.security.encryption import DataEncryptor
    return DataEncryptor().encrypt(key)


def _decrypt_session_key(enc: str) -> Optional[str]:
    """解密 session_key；非密文（dev 明文兼容）原样返回，解密失败按明文返回。"""
    if not enc:
        return None
    if ":" not in enc:
        return enc  # dev 明文兼容
    try:
        from src.security.encryption import DataEncryptor
        decrypted = DataEncryptor().decrypt(enc)
        if decrypted is not None:
            return decrypted
    except Exception:
        pass
    return enc  # 解密失败 → 按明文兼容处理


def save_user_session_key(user_id: str, session_key: Optional[str]):
    """加密保存用户 session_key（虚拟支付用户态签名用）。"""
    global _dao
    if _dao is None or not session_key:
        return
    _ensure_session_key_column()
    try:
        conn = db_connect(_dao.db_path, timeout=10)
        conn.execute(
            f"UPDATE users SET {_SESSION_KEY_COLUMN}=? WHERE user_id=?",
            (_encrypt_session_key(session_key), user_id),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("session_key 保存失败 user=%s: %s", user_id, e)


def get_user_session_key(user_id: str) -> Optional[str]:
    """读取用户 session_key（解密）。无记录返回 None。"""
    global _dao
    if _dao is None:
        return None
    _ensure_session_key_column()
    try:
        conn = db_connect(_dao.db_path, timeout=10)
        row = conn.execute(
            f"SELECT {_SESSION_KEY_COLUMN} FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()
        conn.close()
    except Exception as e:
        logger.warning("session_key 读取失败 user=%s: %s", user_id, e)
        return None
    if not row or not row[0]:
        return None
    return _decrypt_session_key(row[0])


def _dev_session_key(openid: str) -> str:
    """dev 模式确定性 session_key（跨登录稳定，便于沙箱测试）。"""
    return hashlib.sha256(f"dev_session_key:{openid}".encode()).hexdigest()


def _dev_openid_value() -> str:
    """返回 dev 模式稳定 openid（同一部署内所有 dev 登录共用同一用户）。"""
    global _dev_openid
    if _dev_openid is None:
        pinned = os.getenv("DEV_OPENID", "").strip()
        if pinned:
            _dev_openid = pinned
        else:
            _dev_openid = f"dev_user_{secrets.token_hex(6)}"
    return _dev_openid


def setup(dao: UserDAO, session_dao: Optional[SessionDAO] = None, auth_handler=None):
    """在应用启动时设置 DAO 和认证引用。"""
    global _dao, _session_dao, _preference_dao, _person_dao, _auth_handler
    _dao = dao
    _session_dao = session_dao
    _auth_handler = auth_handler
    # Lazy init PreferenceDAO from UserDAO's db_path
    if dao and hasattr(dao, 'db_path') and dao.db_path:
        _preference_dao = PreferenceDAO(dao.db_path)
        _person_dao = PersonDAO(dao.db_path)


def get_person_dao() -> Optional[PersonDAO]:
    """获取 PersonDAO（未 setup 时从 _dao 惰性构造，测试直连可用）。"""
    global _person_dao
    if _person_dao is None and _dao is not None and _dao.db_path:
        _person_dao = PersonDAO(_dao.db_path)
    return _person_dao


# ── 请求模型 ────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    code: str  # wx.login 获取的临时 code


class PhoneBindRequest(BaseModel):
    code: str = ""  # button open-type="getPhoneNumber" 返回的授权 code（5 分钟单次有效）


class ProfileUpdateRequest(BaseModel):
    nickname: str = ""  # 1-20 字，服务端 strip


class BaziRequest(BaseModel):
    birth_year: int = 0
    birth_month: int = 0
    birth_day: int = 0
    birth_hour: int = 0
    birth_minute: int = 0
    gender: str = "unknown"
    calendar: str = "solar"  # "solar" or "lunar"
    city: str = ""
    # k11c：档案级真太阳时开关（1=开默认；0=关=北京时间直排）。None/未传 =
    # 不更新（update 合并保留既有值 / create 缺省开）。
    solar_time: Optional[int] = None


class SubscriptionRequest(BaseModel):
    daily_push: bool = False
    push_time: Optional[str] = None  # 推送时间 HH:MM（可选，默认 08:00）


class FeedbackRequest(BaseModel):
    text: str = ""
    is_anonymous: bool = False


class MemoryDeleteRequest(BaseModel):
    entry_id: str = ""    # 按条目 ID 删除
    subject: str = ""     # 按主题键删除（如 "bazi"、"找工作"）
    entry_type: str = ""  # 与 subject 组合精确匹配（可选）
    all: bool = False     # 清空全部记忆条目


class PersonRequest(BaseModel):
    """命主档案（P2 多人档案）。"""
    name: str = ""
    relation: str = "其他"   # 自己/父母/伴侣/子女/朋友/其他
    gender: str = "unknown"
    birth_year: int = 0
    birth_month: int = 0
    birth_day: int = 0
    birth_hour: int = 0
    birth_minute: int = 0
    calendar: str = "solar"  # solar/lunar
    city: str = ""
    # k11c：档案级真太阳时开关（1=开默认；0=关=按本地时间直排）。None/未传 =
    # update 不覆盖既有值 / create 缺省开（读路径默认开兼容）。
    solar_time: Optional[int] = None


class CancelRequest(BaseModel):
    """账号注销二次确认。"""
    confirm: bool = False
    code: str = ""   # 确认码，须为 "注销"


# ── API 端点 ─────────────────────────────────────────────────────

@router.post("/api/user/login")
async def user_login(req: LoginRequest):
    """微信登录或开发模式登录。

    生产环境：配置了 WECHAT_APP_ID + WECHAT_APP_SECRET 时真实调用
    https://api.weixin.qq.com/sns/jscode2session 换取 openid。
    开发环境（未配置 secret）：使用固定 dev openid（可用 .env DEV_OPENID
    固定），同一用户重复登录返回同一 user_id，响应带 dev_mode=true。

    修复：同一用户每次登录返回同一 user_id（按 openid 查 users 表复用）。
    """
    global _dao, _auth_handler

    dev_mode = False
    openid = ""
    session_key = ""  # 虚拟支付用户态签名用（code2session 返回，加密保存）

    # 尝试真正的微信登录（仅当配置了 appid/secret）
    app_id = os.getenv("WECHAT_APP_ID", "").strip()
    app_secret = os.getenv("WECHAT_APP_SECRET", "").strip()
    if req.code and app_id and app_secret:
        try:
            result = await _wechat_code2session(req.code, app_id, app_secret)
            if result:
                openid, session_key = result
        except Exception as e:
            logger.warning("微信 code2session 调用失败，降级 dev 模式: %s", e)
            openid = ""
            session_key = ""
    elif req.code and req.code == "dev_code":
        openid = ""

    if not openid:
        dev_mode = True
        openid = _dev_openid_value()
        session_key = _dev_session_key(openid)
        logger.info("登录降级 dev 模式：code2session 未返回 openid（code=%s）", (req.code or "")[:16])

    # 稳定 user_id：按 openid 派生，同一 openid 永远同一 user_id
    user_id = f"wx_{openid}"

    # P2 账号注销拦截：status=cancelled → 403（不重建用户、不创建新记录）
    if _dao:
        try:
            if _dao.get_user_status(user_id) == "cancelled":
                logger.info("登录被拦截：账号已注销 user=%s", user_id)
                raise HTTPException(
                    status_code=403,
                    detail=ACCOUNT_CANCELLED_NOTICE,
                )
        except HTTPException:
            raise
        except Exception:
            pass

    # 查已有用户：存在则复用（is_new=False），不存在则创建
    is_new = True
    has_bazi = False
    if _dao:
        try:
            existing = _dao.get_user_bazi(user_id)
            if existing is not None:
                is_new = False
                has_bazi = bool(existing.get("bazi"))
            else:
                _dao.save_user_bazi(user_id, {})  # 创建空记录
        except Exception:
            pass

    # 保存 session_key（加密落库，虚拟支付用户态签名 signature 用）
    if session_key:
        save_user_session_key(user_id, session_key)

    # Task2 同源契约：登录响应补 bazi（默认命主出生信息 dict 或 null，供前端
    # globalData 使用）。与 /api/user/profile 的 bazi_info 同源——persons 默认
    # 命主（default_person_bazi_info）；老用户无命主时 auto_migrate 自动迁移
    # users.bazi_info 旧单档案为「我/自己」命主；仍无 → null。
    bazi = None
    if _dao:
        pdao = get_person_dao()
        if pdao is not None:
            try:
                bazi = pdao.default_person_bazi_info(user_id)
            except Exception as e:
                logger.warning("登录读取默认命主出生信息失败 user=%s: %s", user_id, e)

    # 生成 JWT token（sub=user_id，openid 进 payload，7 天过期）
    token = ""
    if _auth_handler:
        token = _auth_handler.create_user_token(user_id, openid=openid)
    else:
        token = f"dev_token_{user_id}"

    logger.info("登录成功: user=%s dev_mode=%s is_new=%s", user_id, dev_mode, is_new)

    return {
        "token": token,
        "dev_mode": dev_mode,
        "bazi": bazi,
        "user": {
            "id": user_id,
            "has_bazi": has_bazi,
            "is_new": is_new,
        },
    }


# ──────────────────────────────────────────────────────────────
# Task1 登录增强：手机号绑定（getPhoneNumber）+ 头像昵称采集
# 安全红线：手机号 AES 加密落库；日志/响应只输出脱敏号；
# watermark.appid 必须等于本应用 AppID，否则拒绝。
# ──────────────────────────────────────────────────────────────

def _mask_phone(phone: str) -> str:
    """手机号脱敏：保留前 3 后 4，中间打码（如 138****1234）。"""
    phone = (phone or "").strip()
    if not phone:
        return ""
    if len(phone) <= 7:
        return phone[:1] + "*" * max(len(phone) - 1, 0)
    return f"{phone[:3]}{'*' * (len(phone) - 7)}{phone[-4:]}"


async def _get_wx_access_token() -> str:
    """获取微信全局 access_token（cgi-bin/token），模块级缓存 110 分钟（key=appid）。

    失败抛 HTTPException(400)；access_token 不打日志。
    """
    app_id = os.getenv("WECHAT_APP_ID", "").strip()
    app_secret = os.getenv("WECHAT_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        raise HTTPException(status_code=400, detail="微信配置缺失，无法获取手机号")
    now = time.time()
    cached = _ACCESS_TOKEN_CACHE.get(app_id)
    if cached and cached.get("expires_at", 0) > now:
        return cached["token"]
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.weixin.qq.com/cgi-bin/token",
                params={"grant_type": "client_credential", "appid": app_id, "secret": app_secret},
            )
        data = resp.json()
    except Exception as e:
        logger.warning("微信 access_token 请求失败: %s", e)
        raise HTTPException(status_code=400, detail="微信服务暂不可用，请稍后重试")
    token = str(data.get("access_token") or "")
    if data.get("errcode") or not token:
        logger.warning("微信 access_token 返回错误: errcode=%s errmsg=%s",
                       data.get("errcode"), data.get("errmsg"))
        raise HTTPException(status_code=400, detail="微信授权失败，请稍后重试")
    _ACCESS_TOKEN_CACHE[app_id] = {"token": token, "expires_at": now + _ACCESS_TOKEN_TTL_SECONDS}
    return token


# 微信 getuserphonenumber 常见 errcode → 用户可操作的明确文案（v2026-08-17 排查）
_PHONE_ERR_HINTS = {
    40029: "授权码已失效，请重新点击绑定",
    40163: "授权码已使用，请重新点击绑定",
    47001: "参数异常，请重新点击绑定",
    40001: "微信凭证失效，请稍后重试",
    40003: "微信凭证异常，请稍后重试",
    43004: "调用过于频繁，请稍后重试",
    87011: "当前微信暂不支持获取手机号，请稍后重试",
    87012: "手机号获取受限，请稍后重试",
    87013: "授权与小程序不匹配，请用正式版重试",
    87017: "手机号能力未开通，请在小程序后台开通后重试",
    87018: "手机号能力未开通，请在小程序后台开通后重试",
}


async def _wechat_get_phone_number(code: str) -> dict:
    """调用微信 wxa/business/getuserphonenumber 换取手机号信息。

    成功返回 data.phone_info dict；微信返回错误 → HTTPException(400)（明确文案）。
    errcode 有映射表时透出可操作提示；无映射时保留原始 errmsg 供诊断（不泄手机号）。
    """
    token = await _get_wx_access_token()
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.weixin.qq.com/wxa/business/getuserphonenumber",
                params={"access_token": token},
                json={"code": code},
            )
        data = resp.json()
    except Exception as e:
        logger.warning("微信 getuserphonenumber 请求失败: %s", e)
        raise HTTPException(status_code=400, detail="微信服务暂不可用，请稍后重试")
    errcode = data.get("errcode")
    if errcode:
        # 只记 errcode/errmsg/响应结构，不落手机号明文
        logger.warning("微信 getuserphonenumber 返回错误: errcode=%s errmsg=%s has_data=%s",
                       errcode, data.get("errmsg"), "data" in data)
        detail = _PHONE_ERR_HINTS.get(errcode)
        if not detail:
            detail = f"手机号获取失败（{errcode}），请重新授权"
        raise HTTPException(status_code=400, detail=detail)
    return data.get("data") or {}


@router.post("/api/user/phone-bind")
async def user_phone_bind(req: PhoneBindRequest, uid: str = Depends(require_user)):
    """绑定/换绑手机号：微信 getPhoneNumber 授权 code → 换手机号 → AES 加密落库。

    - watermark.appid 必须 == WECHAT_APP_ID，否则 400；
    - 重复绑定 → 覆盖（换绑）并提示；
    - 响应/日志只输出脱敏号。
    """
    global _dao
    code = (req.code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="缺少微信授权 code")
    # fail-closed：DAO 未 setup 时 503，绝不假 200（与 /api/user/cancel 等服务未就绪模式一致）
    if _dao is None:
        raise HTTPException(status_code=503, detail="服务未就绪")

    info = await _wechat_get_phone_number(code)
    phone_info = info.get("phone_info") or {}
    phone = str(phone_info.get("phoneNumber") or "").strip()
    if not phone:
        # 记录响应结构（不含手机号明文）便于诊断「微信 200 但无 phoneNumber」
        logger.warning("微信未返回手机号: has_phone_info=%s watermark_appid=%s user=%s",
                       "phone_info" in info,
                       (phone_info.get("watermark") or {}).get("appid"),
                       uid)
        raise HTTPException(status_code=400, detail="微信未返回手机号，请重新授权")
    # watermark 缺失/部分字段（缺 appid）→ 视为校验失败拒绝
    watermark = phone_info.get("watermark") or {}
    if watermark.get("appid") != os.getenv("WECHAT_APP_ID", "").strip():
        logger.warning("手机号绑定被拒绝：watermark.appid 与 WECHAT_APP_ID 不符 user=%s", uid)
        raise HTTPException(status_code=400, detail="手机号授权校验失败，请重新授权")

    replaced = _dao.save_user_phone(uid, phone)
    logger.info("用户 %s 绑定手机号 %s", uid, _mask_phone(phone))
    return {
        "success": True,
        "phone_masked": _mask_phone(phone),
        "message": "手机号已更新" if replaced else "手机号绑定成功",
    }


@router.get("/api/user/phone")
async def get_user_phone(uid: str = Depends(require_user)):
    """查询手机号绑定状态（只返回脱敏号，不泄露明文）。"""
    global _dao
    # fail-closed：DAO 未 setup 时 503，绝不假 200（与 phone-bind 守卫一致）
    if _dao is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    phone = _dao.get_user_phone(uid)
    if not phone:
        return {"bound": False, "phone_masked": None}
    return {"bound": True, "phone_masked": _mask_phone(phone)}


@router.post("/api/user/profile")
async def user_update_profile(req: ProfileUpdateRequest, uid: str = Depends(require_user)):
    """保存用户昵称（strip 后 1-20 字，否则 400）。"""
    global _dao
    nickname = (req.nickname or "").strip()
    if not nickname:
        raise HTTPException(status_code=400, detail="昵称不能为空")
    if len(nickname) > 20:
        raise HTTPException(status_code=400, detail="昵称最长 20 个字符")
    # fail-closed：DAO 未 setup 时 503，绝不假 200（与 phone-bind 守卫一致）
    if _dao is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    _dao.set_user_nickname(uid, nickname)
    return {"success": True, "nickname": nickname}


def _avatar_dir() -> Path:
    """头像存储目录（data/avatars；AVATAR_DIR 环境变量可覆盖，测试注入临时目录用）。"""
    base = os.getenv("AVATAR_DIR", "").strip()
    if base:
        return Path(base)
    return Path(__file__).resolve().parent.parent.parent / "data" / "avatars"


@router.post("/api/user/avatar")
async def user_upload_avatar(file: UploadFile = File(...), uid: str = Depends(require_user)):
    """上传头像：≤2MB、jpg/png/webp → 存 data/avatars/{user_id}.jpg（覆盖）。

    返回 {avatar_url: "/api/user/avatar/{user_id}"}（公开读取）。

    大小防护双路径（防已认证攻击者用超大 body 吃内存/OOM）：
    1. 读前预拒：file.size（Content-Length 派生，可用时）> MAX_AVATAR_BYTES → 不读 body 直接 400；
    2. 分块读取：64KB 分块累计，超 MAX_AVATAR_BYTES 立即中止 400（无 Content-Length 的流式上传兜底）。
    任何路径下内存占用有界（≤ MAX + 一块）。

    k72 补内容校验：原实现**只查 content-type 与文件名后缀**（都是客户端自述），
    不查真实内容。实测把 MP3 改名 x.png、声明 image/png 打进来 → HTTP 200 且
    原样落盘（落盘文件头 8 字节为 `ID3`）。现补魔数嗅探，与 /api/chat/upload
    同一实现（src/utils/image_sniff.py 单一事实源）。
    """
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    ext = os.path.splitext(file.filename or "")[1].lower()
    if content_type not in _ALLOWED_AVATAR_TYPES or (ext and ext not in _ALLOWED_AVATAR_EXTS):
        raise HTTPException(status_code=400, detail="仅支持 jpg/png/webp 图片")
    # 读前预拒：Content-Length 已知且超限 → 不读 body
    if file.size is not None and file.size > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=400, detail="图片大小不能超过 2MB")
    # 分块读取：累计超限立即中止（读不到全部 body，内存有界）
    chunks = []
    total = 0
    while True:
        chunk = await file.read(_AVATAR_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_AVATAR_BYTES:
            raise HTTPException(status_code=400, detail="图片大小不能超过 2MB")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(status_code=400, detail="图片内容为空")
    # k72：**内容**校验（魔数嗅探）——上面的 content-type/后缀都只是客户端自述，
    # 伪装者改得了声明、改不了自己文件开头的字节。声明与内容都要过关。
    # 错误码沿用本端点既有口径（400 + 同一句 detail），不改变前端已依赖的契约。
    if sniff_image_format(data) not in _ALLOWED_AVATAR_FORMATS:
        raise HTTPException(status_code=400, detail="仅支持 jpg/png/webp 图片")
    # 防路径穿越：user_id 只保留安全字符再拼文件名
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", uid)
    avatar_dir = _avatar_dir()
    avatar_dir.mkdir(parents=True, exist_ok=True)
    (avatar_dir / f"{safe_id}.jpg").write_bytes(data)
    logger.info("头像已保存 user=%s bytes=%d", uid, len(data))
    return {"success": True, "avatar_url": f"/api/user/avatar/{uid}"}


@router.get("/api/user/avatar/{user_id}")
async def get_user_avatar(user_id: str):
    """公开读取头像（非敏感，与微信头像公开语义一致）。文件不存在 → 404。"""
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", user_id)
    path = _avatar_dir() / f"{safe_id}.jpg"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="头像不存在")
    resp = FileResponse(path, media_type="image/jpeg")
    resp.headers["X-Content-Type-Options"] = "nosniff"  # 防 MIME 嗅探（公开读取头）
    return resp


@router.get("/api/user/profile")
async def user_profile(uid: str = Depends(require_user), user_id: str = ""):
    """获取用户资料，包括八字信息、偏好设置、会员状态。

    安全修复：user_id 一律取 JWT sub（uid），query 参数被忽略。
    """
    global _dao

    user_id = uid or user_id

    bazi_info = None
    has_bazi = False
    push_settings = {"daily_push": False, "push_time": "08:00"}
    stats = {"total_consultations": 0}
    # Task4 昵称头像联动：GET profile 补齐 nickname/avatar_url（前端 me/settings 页统一数据源）
    nickname = ""
    avatar_url = ""

    chart = None
    if _dao:
        # ── Task2 同源契约（对话数据复用体系）────────────────────────────
        # 生日字段（bazi_info）事实源 = persons 默认命主（default_person_bazi_info；
        # 无档案时自动迁移 users.bazi_info 旧单档案）；
        # 四柱字段（chart/bazi_label）来源 = users.bazi_info（get_user_bazi）。
        # 两者由同一保存路径写入（/api/user/bazi 与对话 _sync_person_profile
        # 同时写 persons 默认命主 + users.bazi_info），故天然一致；契约由
        # tests/test_profile_consistency.py 守护（改动任一读取源须同步更新测试）。
        # k8（2026-09-05 根因）：兜底链改经 get_user_birth_profile（persons →
        # bazi_info → chart_records 统一读取，含 G1 自愈），不再裸读
        # users.bazi_info——21:44 事故后「persons 已修 1999、bazi_info 仍 1995」
        # 时维护页/画像直读旧值显示 1995 的路径即此；响应 bazi_info 剥离旧行
        # bazi 键（bazi 四柱只属于 chart_records，不向显示层暴露画像 bazi 键）。
        pdao = get_person_dao()
        if pdao is not None:
            try:
                bazi_info = pdao.default_person_bazi_info(user_id)
            except Exception:
                bazi_info = None
        if bazi_info is None:
            from src.storage.birth_profile import get_user_birth_profile
            try:
                bazi_info = get_user_birth_profile(_dao, user_id)
            except Exception:
                bazi_info = None
            if isinstance(bazi_info, dict) and "bazi" in bazi_info:
                bazi_info = {k: v for k, v in bazi_info.items() if k != "bazi"}
        # 命盘（bazi 四柱）k8 起只取「出生档案匹配的 chart_records 盘」
        # （get_user_birth_profile_full；不读 users.bazi_info.bazi——旧行 bazi
        # 键可能为历史他人盘污染，21:44 事故源）
        from src.storage.birth_profile import get_user_birth_profile_full
        try:
            chart = get_user_birth_profile_full(_dao, user_id)
        except Exception:
            chart = None
        has_bazi = bool(chart and chart.get("bazi"))
        push_settings = {
            "daily_push": _dao.get_user_push_settings(user_id).get("push_enabled", False),
            "push_time": _dao.get_user_push_settings(user_id).get("push_time", "08:00"),
        }
        consultations = _dao.get_user_consultations(user_id, limit=1000)
        stats["total_consultations"] = len(consultations)
        # 昵称：users.nickname（未设置返回空串）；头像：文件存在才给公开读取路径（同上传响应格式）
        nickname = _dao.get_user_nickname(user_id)
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", user_id)
        if (_avatar_dir() / f"{safe_id}.jpg").is_file():
            avatar_url = f"/api/user/avatar/{user_id}"

    # 生成八字标签
    bazi_label = ""
    if chart and chart.get("bazi"):
        bazi_list = chart["bazi"]
        if len(bazi_list) >= 8:
            bazi_label = f"{bazi_list[0]}{bazi_list[1]} {bazi_list[2]}{bazi_list[3]} {bazi_list[4]}{bazi_list[5]} {bazi_list[6]}{bazi_list[7]}"

    return {
        "id": user_id,
        "has_bazi": has_bazi,
        "bazi_info": bazi_info,
        "bazi_label": bazi_label,
        "push_settings": push_settings,
        "stats": stats,
        "member_plan": "free",
        "queries_remaining": 50,
        "nickname": nickname,
        "avatar_url": avatar_url,
    }


def _existing_bazi_info(dao, user_id: str) -> dict:
    """读 ② 源既有档案（失败/缺失 → {}；只读，绝不抛）。"""
    try:
        return dao.get_user_bazi(user_id) or {}
    except Exception:
        return {}


def _form_bazi_normalized(req: "BaziRequest", bazi_info: dict,
                          existing: dict) -> dict:
    """表单 dict → ② 源归一 payload（k32 A9 降级直写专用，persons 不可用时）。

    - gender：走 persons 存储层单一归一实现（male/female → 男/女，脏值 → unknown），
      与 persons 中文契约一致（旧代码直写表单原样 dict → 库内出现 'male'）；
    - solar_time：表单显式携带优先；未携带 → 保留 ② 源既有值（与 update_person
      合并语义一致，不误翻转用户开关）；两者都无 → 缺省开=1（读口径 solar_time_on）；
    - 键集 = birth 键（year/month/day/hour/minute/city/gender/calendar/solar_time），
      不带 bazi 四柱键（k8：四柱只属于 chart_records）。
    """
    payload = dict(bazi_info)
    payload["gender"] = normalize_gender_stored(req.gender)
    payload["solar_time"] = (solar_time_on((existing or {}).get("solar_time"))
                             if req.solar_time is None
                             else (1 if req.solar_time else 0))
    return payload


@router.post("/api/user/bazi")
async def user_update_bazi(req: BaziRequest, uid: str = Depends(require_user), user_id: str = ""):
    """保存或更新用户八字信息（P2 兼容迁移：写入默认命主，旧字段同步保留）。

    k28（k25 审查 M-6）：② 源 users.bazi_info 由 persons 写侧镜像漏斗**单点**
    写入（payload 与读路径同构），本接口不再二次 `save_user_bazi` 覆盖——旧
    字段因此仍被推送/报告等旧模块读取，且与 persons 逐键一致（收敛后读路径
    零写）。bazi_info 形参仅在 persons 链路异常时作降级直写兜底。

    安全修复：user_id 一律取 JWT sub，query/body 传参被忽略（防覆写他人八字）。
    """
    global _dao

    user_id = uid or user_id

    bazi_info = {
        "year": req.birth_year,
        "month": req.birth_month,
        "day": req.birth_day,
        "hour": req.birth_hour,
        "minute": req.birth_minute,
        "gender": req.gender,
        "calendar": req.calendar,
        "city": req.city,
    }
    # k11c：solar_time 仅显式携带时写入（0/1 均有效；None=旧调用方不传 →
    # 保持旧字段形态，persons 侧 update 合并保留既有开关值）
    if req.solar_time is not None:
        bazi_info["solar_time"] = 1 if req.solar_time else 0

    if _dao:
        # P2 多人档案：写入默认命主（无档案时自动迁移/新建）
        pdao = get_person_dao()
        if pdao is not None:
            _degrade_payload = None   # 非 None = 需降级直写（persons 权威 payload）
            _degrade_reason = None
            _pid = None
            try:
                default = pdao.get_default_person(user_id)
                birth = {
                    "gender": req.gender,
                    "birth_year": req.birth_year or None,
                    "birth_month": req.birth_month or None,
                    "birth_day": req.birth_day or None,
                    "birth_hour": req.birth_hour or None,
                    "birth_minute": req.birth_minute or None,
                    "calendar": req.calendar,
                    "city": req.city,
                    "solar_time": (None if req.solar_time is None
                                   else (1 if req.solar_time else 0)),
                }
                # k28（k25 审查 M-6 收口）：本次请求对 users.bazi_info（② 源）
                # 只有**一个**写入口 = persons 写侧镜像漏斗（update_person/
                # create_person 内的 mirror_bazi_info_to_users，payload =
                # bazi_info_of_person(默认命主)，与读路径 out 逐键同构）。
                # 旧代码紧随其后又 `_dao.save_user_bazi(user_id, bazi_info)`
                # 原样重写表单 dict → 覆盖镜像：gender 传 male/female 时未归一
                # （与 persons 中文契约分裂）、未传 solar_time 时整键丢失
                # （② 源读口径回落默认开，而档案可能是关）、并 bump
                # consultation_count。两写最终值取决于调用顺序 → 口径分裂，
                # 读路径每次再自愈。故本路径不再二次写。
                if default:
                    _pid = default["id"]
                    pdao.update_person(user_id, _pid, birth=birth,
                                       birth_ctx=FORM_EXPLICIT_CTX)
                else:
                    _created = pdao.create_person(
                        user_id, name="我", relation="自己", is_default=True,
                        birth=birth, birth_ctx=FORM_EXPLICIT_CTX)
                    _pid = (_created or {}).get("id")
                # k32（A8）：镜像结果核查——漏斗内镜像静默失败（② 源行缺失时
                # create_if_missing=False 直接 no-op / 写异常被内部吞掉返回
                # False）时，k28 收敛后本路径**没有**第二次写 → ② 源不落库、
                # 两库分裂（直到下次读路径自愈）。此处按读路径同款口径
                # （birth_profile.bazi_info_out_of_sync，单一实现）核对 ② 源；
                # 未同步即降级直写（payload 取 persons 权威行，与镜像同构）。
                _latest = pdao.get_person(user_id, _pid) if _pid else None
                if _latest and _latest.get("birth_year"):
                    _want = bazi_info_of_person(_latest)
                    if bazi_info_out_of_sync(_dao.get_user_bazi(user_id), _want):
                        _degrade_payload = _want
                        _degrade_reason = "② 源镜像未同步"
            except Exception as e:
                _degrade_reason = e
                # 降级直写 payload 优先取 persons 权威行（与镜像同构；写失败时
                # 行内仍是既有值 → ② 源与 persons 保持一致，不写入表单新值造成
                # 新的分裂）。persons 完全不可用 → None（走表单归一兜底）。
                try:
                    _row = pdao.get_person(user_id, _pid) if _pid else None
                    if _row and _row.get("birth_year"):
                        _degrade_payload = bazi_info_of_person(_row)
                except Exception:
                    _degrade_payload = None
            if _degrade_reason is not None:
                # 降级兜底：persons 链路不可用（建表/加密/写失败，非写入守卫
                # ——表单提交永远豁免守卫）或 ② 源镜像未同步时保留直写，避免整次
                # 保存静默丢失。正常路径绝不到这里，② 源写入口仍是镜像单点。
                # k32（A9）：直写 payload 必须**归一**（persons 权威镜像 payload
                # 或表单 dict 本地归一）——旧代码写表单原样 dict：gender 可能
                # 'male'/'female'（与 persons 中文契约分裂）、未传 solar_time
                # 时整键丢失（② 源读口径回落默认开，而档案可能是关）。
                payload = _degrade_payload or _form_bazi_normalized(
                    req, bazi_info, _existing_bazi_info(_dao, user_id))
                logger.warning("写入默认命主失败/镜像未同步 user=%s（降级直写 ② 源）：%s",
                               user_id, _degrade_reason)
                _dao.save_user_bazi(user_id, payload)

    return {"success": True, "message": "八字信息已保存"}


@router.get("/api/user/subscription")
async def get_user_subscription(uid: str = Depends(require_user)):
    """查询每日推送订阅设置。

    安全修复：user_id 一律取 JWT sub。
    返回 {daily_push, push_time}；订阅消息送达链路状态见 .env 注释
    （WECHAT_SUBSCRIBE_TEMPLATE_ID 未配置时仅存开关状态，不影响接口契约）。
    """
    global _dao
    if _dao:
        settings = _dao.get_user_push_settings(uid)
        return {
            "daily_push": settings.get("push_enabled", False),
            "push_time": settings.get("push_time", "08:00"),
        }
    return {"daily_push": False, "push_time": "08:00"}


@router.post("/api/user/subscription")
async def user_subscription(req: SubscriptionRequest, uid: str = Depends(require_user), user_id: str = ""):
    """更新每日推送订阅设置。

    安全修复：user_id 一律取 JWT sub。
    """
    global _dao

    user_id = uid or user_id

    if _dao:
        _dao.set_push_enabled(user_id, req.daily_push)
        if req.push_time:
            _dao.set_push_time(user_id, req.push_time)

    return {"success": True, "daily_push": req.daily_push, "push_time": req.push_time}


@router.post("/api/user/feedback")
async def user_feedback(req: FeedbackRequest, uid: str = Depends(require_user), user_id: str = ""):
    """提交用户反馈。

    安全修复：user_id 一律取 JWT sub。
    """
    global _dao

    user_id = uid or user_id

    feedback_text = req.text.strip()
    if not feedback_text:
        raise HTTPException(status_code=400, detail="反馈内容不能为空")

    # 存储反馈（保存在最近的 consultation 或单独存储）
    if _dao:
        try:
            # 查找最近一次咨询
            consultations = _dao.get_user_consultations(user_id, limit=1)
            if consultations:
                _dao.save_feedback(consultations[0]["id"], "positive" if "好" in feedback_text else "negative")
        except Exception:
            pass

    # 使用匿名用户 ID 如果要求
    display_user = "anonymous" if req.is_anonymous else user_id

    return {
        "success": True,
        "message": "感谢你的反馈！我们会认真对待每一条建议。",
        "submitted_as": display_user,
    }


@router.get("/api/user/preferences")
async def user_preferences(uid: str = Depends(require_user), user_id: str = ""):
    """获取用户偏好画像。

    返回学习到的话题偏好、长度偏好、准确率等信息。
    安全修复：user_id 一律取 JWT sub。

    k62：`preferred_style` / `preferred_style_key` 两键已移除（早期「3 模式
    人设」残留；它是「把自身输出当输入」的自强化回路，会注入用户从未表达过的
    风格偏好 —— 见 storage/models.py 列注释）。消费方核实：全仓
    （含 miniprogram/）无该字段引用，见 docs/API.md。
    """
    global _preference_dao

    user_id = uid or user_id

    if not _preference_dao:
        return {
            "has_data": False,
            "top_topics": [],
            "accuracy_pct": None,
            "feedback_count": 0,
            "is_mature": False,
        }

    prefs = _preference_dao.get(user_id)
    topic_names = {"wealth": "财运", "love": "感情", "career": "事业",
                   "health": "健康", "growth": "个人成长"}

    # Top 3 topics
    topic_weights = {"wealth": prefs.topic_wealth, "love": prefs.topic_love,
                     "career": prefs.topic_career, "health": prefs.topic_health,
                     "growth": prefs.topic_growth}
    sorted_topics = sorted(topic_weights, key=topic_weights.get, reverse=True)
    top_topics = [topic_names.get(t, t) for t in sorted_topics[:3] if topic_weights[t] > 0.1]

    return {
        "has_data": prefs.is_mature,
        "top_topics": top_topics,
        "length_preference": "short" if prefs.prefer_short else "normal",
        "accuracy_pct": prefs.accuracy_pct,
        "feedback_count": prefs.feedback_count,
        "is_mature": prefs.is_mature,
    }


@router.get("/api/user/memories")
async def user_memories(uid: str = Depends(require_user), user_id: str = ""):
    """查看 L3 长期记忆条目（方案 §5.5 隐私：用户可查看自己的记忆）。

    返回结构化条目（type/subject/content/confidence/ttl 等）。
    安全修复：user_id 一律取 JWT sub。
    """
    from src.memory.user_memory import UserMemory
    user_id = uid or user_id
    mem = UserMemory()
    entries = mem.list_entries(user_id)
    type_cn = UserMemory.ENTRY_TYPE_CN
    return {
        "status": "ok",
        "user_id": user_id,
        "count": len(entries),
        "entries": [
            {
                "id": e["id"],
                "type": e.get("type", ""),
                "type_cn": type_cn.get(e.get("type", ""), e.get("type", "")),
                "subject": e.get("subject", ""),
                "content": e.get("content", ""),
                "confidence": e.get("confidence", 0),
                "created_at": e.get("created_at", ""),
                "ttl_days": e.get("ttl_days"),
                "is_key": bool(e.get("is_key", False)),
            }
            for e in entries
        ],
    }


@router.delete("/api/user/memories")
async def delete_user_memories(req: MemoryDeleteRequest,
                               uid: str = Depends(require_user),
                               user_id: str = ""):
    """删除 L3 长期记忆（方案 §5.5 隐私：用户可删除）。

    按 entry_id / subject(+entry_type) 精确删除，或 all=True 清空全部条目。
    安全修复：user_id 一律取 JWT sub。
    """
    from src.memory.user_memory import UserMemory
    user_id = uid or user_id
    mem = UserMemory()
    if req.all:
        removed = mem.clear_entries(user_id)
    elif req.entry_id:
        removed = mem.delete_entry(user_id, entry_id=req.entry_id)
    elif req.subject:
        removed = mem.delete_entry(user_id, subject=req.subject,
                                   entry_type=req.entry_type or "")
    else:
        raise HTTPException(status_code=400, detail="请指定 entry_id / subject / all")
    return {"status": "ok", "removed": removed,
            "message": f"已删除 {removed} 条记忆"}


# ──────────────────────────────────────────────────────────────
# P2 · 多人档案（persons）— 全走 require_user 鉴权 + 归属校验
# ──────────────────────────────────────────────────────────────

def _person_birth(req: PersonRequest) -> dict:
    """PersonRequest → birth dict（PersonDAO 加密字段）。

    G1（2026-08-29）性别契约统一：male→'男'、female→'女'（大小写不敏感）、
    中文原样保留；None/空/"unknown" 归一为 None → update 路径不覆盖既有值
    （与 save_bazi_info 的 gender 保护约定一致；创建时由 _birth_dict 落 "unknown"）。
    既有接口契约零破坏：persons API 仍接受 male/female 入参（归一兼容）。
    """
    gender = (req.gender or "").strip()
    _gl = gender.lower()
    if _gl in ("male", "男"):
        gender = "男"
    elif _gl in ("female", "女"):
        gender = "女"
    else:
        gender = None
    return {
        "gender": gender,
        "birth_year": req.birth_year or None,
        "birth_month": req.birth_month or None,
        "birth_day": req.birth_day or None,
        "birth_hour": req.birth_hour or None,
        "birth_minute": req.birth_minute or None,
        "calendar": req.calendar,
        "city": (req.city or "").strip() or None,
        # k11c：solar_time 透传（None=未提供：update 不覆盖 / create 缺省开）。
        # 0 必须显式携带（不可被 `or None` 吞掉——关=0 是有效值）。
        # getattr 兜底：测试/旧调用方的鸭子类型 request 可无该字段（G1 防御同款）
        "solar_time": (None if getattr(req, "solar_time", None) is None
                       else (1 if int(getattr(req, "solar_time")) else 0)),
    }


@router.get("/api/persons")
async def list_persons(uid: str = Depends(require_user)):
    """获取该用户全部命主（默认在前）。"""
    pdao = get_person_dao()
    if pdao is None:
        raise HTTPException(status_code=503, detail="档案服务未就绪")
    persons = pdao.list_persons(uid)
    return {
        "status": "ok",
        "count": len(persons),
        "default_id": next((p["id"] for p in persons if p["is_default"]), None),
        "persons": persons,
    }


@router.post("/api/persons")
async def create_person(req: PersonRequest, uid: str = Depends(require_user)):
    """创建命主（首个自动 is_default=True）。"""
    pdao = get_person_dao()
    if pdao is None:
        raise HTTPException(status_code=503, detail="档案服务未就绪")
    if not (req.name or "").strip():
        raise HTTPException(status_code=400, detail="姓名不能为空")
    person = pdao.create_person(
        uid, name=req.name, relation=req.relation, birth=_person_birth(req),
        birth_ctx=FORM_EXPLICIT_CTX)
    if person is None:
        raise HTTPException(status_code=500, detail="创建失败")
    return {"status": "ok", "message": "命主已创建", "person": person}


@router.put("/api/persons/{person_id}")
async def update_person(person_id: str, req: PersonRequest, uid: str = Depends(require_user)):
    """更新命主（归属校验：只能改自己的；None/空字段不覆盖）。"""
    pdao = get_person_dao()
    if pdao is None:
        raise HTTPException(status_code=503, detail="档案服务未就绪")
    name = (req.name or "").strip()
    person = pdao.update_person(
        uid, person_id,
        name=name or None,
        relation=req.relation or None,
        birth=_person_birth(req),
        birth_ctx=FORM_EXPLICIT_CTX,
    )
    if person is None:
        raise HTTPException(status_code=404, detail="命主不存在或无权操作")
    return {"status": "ok", "message": "命主已更新", "person": person}


@router.delete("/api/persons/{person_id}")
async def delete_person(person_id: str, uid: str = Depends(require_user)):
    """删除命主（归属校验；删除默认命主时剩余最早者自动成为默认）。"""
    pdao = get_person_dao()
    if pdao is None:
        raise HTTPException(status_code=503, detail="档案服务未就绪")
    if not pdao.delete_person(uid, person_id):
        raise HTTPException(status_code=404, detail="命主不存在或无权操作")
    return {"status": "ok", "message": "命主已删除"}


@router.post("/api/persons/{person_id}/default")
async def set_default_person(person_id: str, uid: str = Depends(require_user)):
    """设默认命主（事务：清旧默认 → 置新默认；归属校验）。"""
    pdao = get_person_dao()
    if pdao is None:
        raise HTTPException(status_code=503, detail="档案服务未就绪")
    if not pdao.set_default(uid, person_id):
        raise HTTPException(status_code=404, detail="命主不存在或无权操作")
    return {"status": "ok", "message": "已设为默认命主"}


# ──────────────────────────────────────────────────────────────
# P2 · 账号注销（软删 + 90 天归档，用户拍板方案）
# ──────────────────────────────────────────────────────────────

@router.post("/api/user/cancel")
async def user_cancel(req: CancelRequest, uid: str = Depends(require_user)):
    """注销账号（二次确认）：confirm=true 或 code="注销" → 软删。

    数据保留 90 天后由 cleanup_cancelled_accounts() 物理清除；
    保留期内不提供恢复接口（用户拍板方案：保留数据库备份可人工恢复）。
    前端收到成功响应后应清除本地 token。
    """
    if not req.confirm and req.code != "注销":
        raise HTTPException(
            status_code=400,
            detail="请二次确认注销：confirm=true 或 code=\"注销\"",
        )
    if _dao:
        _dao.cancel_user(uid)
        return {
            "success": True,
            # k78：与登录 403 同一常量（同一事实），不再各写一份
            "message": ACCOUNT_CANCELLED_NOTICE,
        }
    raise HTTPException(status_code=503, detail="服务未就绪")


# ──────────────────────────────────────────────────────────────
# P2 · 「明灯记得你」记忆管理（L2 事实条目 + 演化链）
# ──────────────────────────────────────────────────────────────

@router.get("/api/memory")
async def get_memory(uid: str = Depends(require_user)):
    """查看记忆：L2 事实条目 + 话题演化链（方案 §三「明灯记得你」）。"""
    from src.memory.user_memory import UserMemory
    mem = UserMemory()
    facts = mem.list_fact_entries(uid)
    evolutions = mem.get_evolutions(uid)
    return {
        "status": "ok",
        "facts": [
            {
                "id": f["id"],
                "type": f.get("type", "fact"),
                "type_cn": UserMemory.ENTRY_TYPE_CN.get(
                    f.get("type", ""), f.get("type", "")),
                "content": f.get("content", ""),
                "subject": f.get("subject", "self"),
                "confidence": f.get("confidence", 0),
                "created_at": f.get("created_at", ""),
                "updated_at": f.get("updated_at", ""),
            }
            for f in facts
        ],
        "evolutions": [
            {
                "topic": t,
                "count": v["count"],
                "last_at": v["last_at"],
            }
            for t, v in evolutions.items()
        ],
    }


@router.delete("/api/memory/facts")
async def clear_memory_facts(uid: str = Depends(require_user)):
    """清空全部事实条目（不动 persons/生辰）。"""
    from src.memory.user_memory import UserMemory
    removed = UserMemory().clear_fact_entries(uid)
    return {"status": "ok", "removed": removed, "message": f"已清空 {removed} 条事实"}


@router.delete("/api/memory/{fact_id}")
async def delete_memory_fact(fact_id: str, uid: str = Depends(require_user)):
    """删除单条事实条目。"""
    from src.memory.user_memory import UserMemory
    removed = UserMemory().delete_fact_entry(uid, fact_id)
    if not removed:
        raise HTTPException(status_code=404, detail="事实条目不存在")
    return {"status": "ok", "removed": removed, "message": "事实条目已删除"}


# ── 辅助函数 ─────────────────────────────────────────────────────

async def _wechat_code2session(code: str, app_id: str, app_secret: str) -> Optional[tuple]:
    """真实调用微信 code2session 接口换取 openid 与 session_key。

    GET https://api.weixin.qq.com/sns/jscode2session
        ?appid=...&secret=...&js_code=...&grant_type=authorization_code

    - 成功：返回 (openid, session_key)（session_key 供虚拟支付用户态签名使用）
    - 失败（code 无效/过期、网络异常等）：返回 None，由调用方降级
    """
    import httpx

    params = {
        "appid": app_id,
        "secret": app_secret,
        "js_code": code,
        "grant_type": "authorization_code",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.weixin.qq.com/sns/jscode2session",
                params=params,
            )
        data = resp.json()
    except Exception as e:
        logger.warning("微信 code2session 请求失败: %s", e)
        return None

    if data.get("errcode"):
        logger.warning("微信 code2session 返回错误: %s %s", data.get("errcode"), data.get("errmsg"))
        return None

    openid = data.get("openid", "")
    if not openid:
        return None
    # 微信 unionid 可用于跨应用识别（保留给未来多端互通）
    # unionid = data.get("unionid", "")
    return openid, data.get("session_key", "")
