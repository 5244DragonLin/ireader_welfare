#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""广告策略拉取（`/ad/cfg`）。

## 这个接口有什么用

`/ad/cfg` 下发每个广告位的投放策略（哪些广告源、优先级、价格、pid/pidSign 等）。
能拿到非空 rules，说明服务端**已识别本客户端为真实 App**（需要完整设备指纹）；
拿不到说明被判为脚本。所以它也可以当作「设备指纹是否有效」的探针。

## 已知的 slotId（2026-11-16 实测 8 个）

| slotId | 说明 | gUrl 里的 pid |
|--------|------|--------------|
| VIDEO_PAYTASK | 看视频送奖励 | 10156 |
| MARKET_AD_UNLOCK_CHAPTER | 广告解锁章节 | 12824 |
| MARKET_READ_OBTAIN_VOUCHER | 阅读得代金券 | 12963 |
| MARKET_RETAIN_OBTAIN_VIP | 留存得 VIP | 12962 |
| MARKET_SIGN_OBTAIN_VOUCHER | 签到得代金券 | 12964 |
| MARKET_USER_ECPM_COLLECT | 用户 ECPM 采集 | 12977 |
| MYPAGE_MATRIX_ICON | 我的页矩阵图标 | 10555 |
| SCREEN | 开屏 | 10000 |

## 返回结构（节选）

```json
[{"slotId": "VIDEO_PAYTASK",
  "gUrl": ".../union-track?...&pid=10156&...",
  "pvUrl": ".../track-empty",
  "rule": [{"priority": 1, "strategies": [
      {"id": 147659, "pid": "10156", "pidSign": "tt5LsX...",
       "source": "ssp", "price": 10, "filterId": 101463,
       "startTime": "2026-07-14 00:00:00", "endTime": "2096-08-31 23:59:59"}]}]}]
```

7 个源：`ssp` / `sigmobSDK` / `takuSDK` / `fenglanSDK` / `qutoutiao` / `gdt` / `csj`。

> 注意：这个接口拉到的 `filterId`（如 101463）与「看视频领券」用的奖励 id
> （100063 等）**不是同一套**。前者是广告投放策略，后者是发奖配置。
> 领券不需要本模块，见 `reward.py`。
"""
import json

from .http import get_json

API = "https://saad.ms.zhangyue.net"


def fetch_rules(cred) -> list:
    """拉取全部广告位策略。失败返回空列表（不抛异常）。

    需要完整设备指纹才可能下发；脚本环境常见返回空。
    """
    url = f"{API}/ad/cfg?app_id=10000&usr={cred.usr}"
    try:
        raw = get_json(url)
    except Exception:
        return []
    if not isinstance(raw, dict):
        return []
    body = raw.get("body") or raw.get("data") or []
    if isinstance(body, dict):
        body = body.get("rules") or body.get("list") or []
    return body if isinstance(body, list) else []


def slot_strategies(rules, slot_id) -> list:
    """取出某个广告位的全部策略（拍平 rule[].strategies[]）。"""
    out = []
    for r in rules or []:
        if r.get("slotId") != slot_id:
            continue
        for item in r.get("rule") or []:
            out.extend(item.get("strategies") or [])
    return out


def slot_ids(rules) -> list:
    """列出所有广告位 id。"""
    return [r.get("slotId") for r in (rules or []) if r.get("slotId")]


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "src")
    from ireader_welfare import auth, config

    cfg = config.load("config.yaml")
    phone, pwd = config.resolve_credentials(cfg, None, None)
    cred = auth.login(phone, pwd)
    rules = fetch_rules(cred)
    print(f"拿到 {len(rules)} 个广告位")
    for sid in slot_ids(rules):
        st = slot_strategies(rules, sid)
        srcs = [s.get("source") for s in st]
        print(f"  {sid:32s} {len(st)} 个策略  {srcs}")
