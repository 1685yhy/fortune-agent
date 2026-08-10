"""微信服务号模板消息封装(晨笺/晚安推送通道)。"""
import logging, os, time
import httpx

logger = logging.getLogger(__name__)
_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
_SEND_URL = "https://api.weixin.qq.com/cgi-bin/message/template/send"

_token_cache: dict = {"token": "", "expire_at": 0.0}

def _env(key: str) -> str:
    return os.getenv(key, "").strip()

def mp_ready() -> bool:
    return all(_env(k) for k in ("MP_APP_ID", "MP_APP_SECRET", "MP_JIAN_TEMPLATE_ID", "MP_NIGHT_TEMPLATE_ID"))

def _reset_cache() -> None:
    _token_cache.update({"token": "", "expire_at": 0.0})

class MpError(Exception):
    pass

def get_mp_access_token() -> str:
    if _token_cache["token"] and time.time() < _token_cache["expire_at"]:
        return _token_cache["token"]
    r = httpx.get(_TOKEN_URL, params={
        "grant_type": "client_credential",
        "appid": _env("MP_APP_ID"),
        "secret": _env("MP_APP_SECRET"),
    }, timeout=10)
    r.raise_for_status()
    data = r.json()
    if "access_token" not in data:
        raise MpError(f"access_token 失败: {data}")
    _token_cache["token"] = data["access_token"]
    _token_cache["expire_at"] = time.time() + max(60, int(data.get("expires_in", 7200)) - 200)
    return _token_cache["token"]

def send_template(openid: str, template_id: str, data: dict, url: str = "") -> dict:
    token = get_mp_access_token()
    body = {"touser": openid, "template_id": template_id, "data": data}
    if url:
        body["url"] = url
    r = httpx.post(_SEND_URL, params={"access_token": token}, json=body, timeout=10)
    r.raise_for_status()
    result = r.json()
    if result.get("errcode") not in (0, None):
        raise MpError(f"模板发送失败: {result}")
    return result
