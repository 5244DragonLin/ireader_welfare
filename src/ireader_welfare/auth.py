#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""密码登录与凭证解析。

登录链路：
    POST https://uc.ireader.com/user/slogin/passwd/v2
    业务数据放在 data 字段（AES + RSA 混合加密），参数串用客户端私钥签名。

返回的 token 是“公钥加密”的，需要用硬编码公钥做 RSA 运算还原出 AES 密钥，
再用该密钥解出 session_id 与本次会话的签名私钥。
"""
import base64
import json
import time

from . import crypto
from .http import post_form

UC = "https://uc.ireader.com"
VER = "1.0"
VERCODE = "80740"
CHANNEL = "100"


class Credential:
    """一次登录产生的凭证集合。"""

    def __init__(self, usr, zysid, nick, sign_priv, native_priv, raw=None):
        self.usr = usr
        self.zysid = zysid
        self.nick = nick
        self.sign_priv = sign_priv
        self.native_priv = native_priv
        self.raw = raw or {}

    def __repr__(self):
        return f"<Credential usr={self.usr} nick={self.nick}>"

    def to_dict(self):
        """可序列化形式（不含私钥，仅用于展示/落盘）。"""
        return {
            "usr": self.usr,
            "zysid": self.zysid,
            "nick": self.nick,
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }


def login(phone: str, password: str, imei: str = None) -> Credential:
    """手机号 + 密码登录，返回 Credential。

    注意：只有第 4 把客户端私钥能通过签名校验，其余会返回 [null, 6]。
    """
    keys = crypto.load_client_keys()
    npk = crypto.login_key(keys)

    params = {
        "data": crypto.build_data_blob(phone, password),
        "imei": imei or ("86" + str(int(time.time() * 1000))[-13:]),
        "ver": VER,
        "channel_id": CHANNEL,
        "version_id": VERCODE,
        "encrypt_method": "1",
        "usr": phone,
    }
    res = post_form(f"{UC}/user/slogin/passwd/v2", crypto.sign_params(npk, params))

    if res.get("code") != 0:
        raise RuntimeError(f"登录失败：{res.get('msg') or res}")

    body = res.get("body")
    if isinstance(body, list):
        raise RuntimeError(f"登录被拒（签名私钥不匹配）：{body}")

    token = body["token"]
    aes_raw = base64.b64decode(crypto.rsa_public_decrypt(token["AesKey"]))
    inner = json.loads(crypto.aes_decrypt(aes_raw, token["Data"]))

    return Credential(
        usr=body["name"],
        zysid=inner["session_id"],
        nick=body.get("nick"),
        sign_priv=crypto.load_der_private_key(
            base64.b64decode(inner["rsa_public_key"]), password=None
        ),
        native_priv=npk,
        raw=body,
    )