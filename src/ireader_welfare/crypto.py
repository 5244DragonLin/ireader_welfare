#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""加解密与签名：掌阅客户端所用的 RSA / AES 组合。"""
import base64
import json
import os
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import (
    load_der_private_key,
    load_der_public_key,
)

# 硬编码在客户端 dex 中的登录加密公钥（对应原始类 account/i.h 与 account/f.o）
PUB_LOGIN = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCAqSnnZ9BVOZjIMTkATIn29nM0hLOsQsXlPbqr"
    "GE4CUmDCncVBHdkfEIF73tSKjhYfLuPH1gDtHRKeCC1DQ4uYJL83oeHtXSldGUlfuv9rh0Q/2Hx"
    "l3iG8TUc1drTKTZFfkQWWseTb3vAx8Ggse9xZNTjI6enOEjNyGlAIF+RKrwIDAQAB"
)


def rsa_encrypt(pub_b64: str, plain: str) -> str:
    """用公钥做 PKCS1v15 加密，返回 Base64。"""
    pub = load_der_public_key(base64.b64decode(pub_b64))
    return base64.b64encode(pub.encrypt(plain.encode(), padding.PKCS1v15())).decode()


def rsa_public_decrypt(b64_cipher: str, pub_b64: str = PUB_LOGIN) -> bytes:
    """用公钥做“解密”。

    数学上等价于 RSA 验签运算 c^e mod n。服务端下发 token.AesKey 时用的是
    “私钥加密”，因此客户端只能用配对的公钥还原，这是整条链路上最反直觉的一步。
    """
    pub = load_der_public_key(base64.b64decode(pub_b64))
    nums = pub.public_numbers()
    c = int.from_bytes(base64.b64decode(b64_cipher), "big")
    raw = pow(c, nums.e, nums.n).to_bytes((nums.n.bit_length() + 7) // 8, "big")
    if raw[:2] == b"\x00\x01":  # PKCS#1 v1.5 type 1 填充
        return raw[raw.find(b"\x00", 2) + 1:]
    return raw


def gen_aes_key() -> str:
    """生成 16 字节 AES 密钥，返回 Base64。"""
    return base64.b64encode(os.urandom(16)).decode()


def aes_encrypt(aes_key_b64: str, plain: str) -> str:
    """AES/CBC/PKCS7，IV 与密钥相同（对应原始类 tools/m.f）。"""
    key = base64.b64decode(aes_key_b64)
    data = plain.encode()
    pad = 16 - len(data) % 16
    data += bytes([pad]) * pad
    enc = Cipher(algorithms.AES(key), modes.CBC(key)).encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode()


def aes_decrypt(key: bytes, b64_data: str) -> str:
    dec = Cipher(algorithms.AES(key), modes.CBC(key)).decryptor()
    pt = dec.update(base64.b64decode(b64_data)) + dec.finalize()
    return pt[:-pt[-1]].decode("utf-8")


def rsa_sha1_sign(priv, s: str) -> str:
    """SHA1WithRSA 签名，返回 Base64。"""
    raw = priv.sign(s.encode(), padding.PKCS1v15(), hashes.SHA1())
    return base64.b64encode(raw).decode()


def sorted_param_str(d: dict) -> str:
    """按 key 字典序拼成 k=v&k=v，跳过空值。"""
    return "&".join(f"{k}={d[k]}" for k in sorted(d) if d[k] not in (None, ""))


def sign_params(priv, params: dict) -> dict:
    """给参数字典补上 timestamp + sign，返回新字典。"""
    p = {k: v for k, v in params.items() if k != "sign"}
    p["timestamp"] = str(int(time.time() * 1000))
    p["sign"] = rsa_sha1_sign(priv, sorted_param_str(p))
    return p


def build_data_blob(username: str, password: str) -> str:
    """构造登录用的 data 字段：AES 加密业务体，AES 密钥再用 RSA 公钥加密。"""
    aes_key = gen_aes_key()
    inner = json.dumps(
        {"password": password, "user_name": username},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return json.dumps(
        {"AesKey": rsa_encrypt(PUB_LOGIN, aes_key), "Data": aes_encrypt(aes_key, inner)},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def load_client_keys(path: str = None) -> list:
    """读取客户端内置的 RSA 私钥列表（默认读包内的 native_keys.json）。"""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "native_keys.json")
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return [load_der_private_key(base64.b64decode(x["der_b64"]), password=None) for x in raw]


def login_key(keys: list):
    """登录签名用第 4 把私钥（下标 3），已通过实测验证。"""
    return keys[3]