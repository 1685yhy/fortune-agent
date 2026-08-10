"""服务号模板消息封装测试(mock httpx)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest.mock as mock
from src.services.wechat_mp import get_mp_access_token, send_template, mp_ready, _reset_cache

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1; print(f"PASS: {name}")

# 1. mp_ready 缺 env 判定
os.environ.pop("MP_APP_ID", None); os.environ.pop("MP_APP_SECRET", None)
os.environ.pop("MP_JIAN_TEMPLATE_ID", None); os.environ.pop("MP_NIGHT_TEMPLATE_ID", None)
check("未配置时 ready=False", not mp_ready())

os.environ["MP_APP_ID"] = "wx_test"; os.environ["MP_APP_SECRET"] = "sec"
os.environ["MP_JIAN_TEMPLATE_ID"] = "tpl_j"; os.environ["MP_NIGHT_TEMPLATE_ID"] = "tpl_n"
check("配置齐全时 ready=True", mp_ready())

# 2. access_token 获取+缓存
_reset_cache()
fake_resp = mock.Mock(); fake_resp.raise_for_status = lambda: None
fake_resp.json = lambda: {"access_token": "tok_1", "expires_in": 7200}
with mock.patch("httpx.get", return_value=fake_resp) as m:
    t1 = get_mp_access_token()
    check("token 获取", t1 == "tok_1")
    t2 = get_mp_access_token()
    check("token 缓存复用(只请求一次)", m.call_count == 1)

# 3. 发送模板消息
_reset_cache()
with mock.patch("httpx.get", return_value=fake_resp), \
     mock.patch("httpx.post", return_value=fake_resp) as mpost:
    r = send_template("openid1", "tpl_j", {"thing1": {"value": "晨笺"}})
    check("发送成功返回微信响应", r.get("access_token") == "tok_1")
    body = mpost.call_args.kwargs.get("json") or mpost.call_args.args[1]
    check("发送带 openid", body["touser"] == "openid1")
    check("发送带模板", body["template_id"] == "tpl_j")

print(f"\nALL PASS ({ok})")
