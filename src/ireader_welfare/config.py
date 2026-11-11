#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""配置加载：内置默认值 → config.yaml → 环境变量 / 命令行参数。"""
import os

DEFAULTS = {
    "phone": "",
    "password": "",
    "output": {
        "dir": "output",
        "save_credentials": False,
    },
}

CONFIG_NAME = "config.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def find_config(start: str = None) -> str:
    """从当前目录向上查找 config.yaml。"""
    cur = os.path.abspath(start or os.getcwd())
    while True:
        candidate = os.path.join(cur, CONFIG_NAME)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(cur)
        if parent == cur:
            return ""
        cur = parent


def load(path: str = None) -> dict:
    """加载配置，未提供路径时自动向上查找。"""
    cfg = _deep_merge({}, DEFAULTS)
    path = path or find_config()
    if path and os.path.isfile(path):
        try:
            import yaml
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("读取配置文件需要 PyYAML：pip install pyyaml") from e
        with open(path, "r", encoding="utf-8") as f:
            cfg = _deep_merge(cfg, yaml.safe_load(f) or {})
    return cfg


def resolve_credentials(cfg: dict, phone: str = None, password: str = None):
    """按 命令行 → 环境变量 → 配置文件 的优先级取账号密码。"""
    phone = phone or os.environ.get("IREADER_PHONE") or cfg.get("phone") or ""
    password = password or os.environ.get("IREADER_PWD") or cfg.get("password") or ""
    return phone.strip(), password.strip()