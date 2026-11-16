#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""逐笔代金券明细（★ 2026-11-16 找到的正确接口）。

## 之前的误判

早前查 `/zyuc/gift/ajaxrequest?index=2`，只拿到**聚合后的券包**：

```text
290 券  name="获得10阅饼"  expire=11-19
 86 券  name="获得11阅饼"  expire=11-23
```

据此错误地写下「服务端不提供逐笔流水」。**错在只看了列表接口，没点进详情。**

## 正确接口

```text
GET https://ah2.zhangyue.com/zyuc/gift/voucherDetail?voucherId=<券包ID>
```

`voucherId` 从列表页的卡片属性拿：

```html
<div class="card_useless" data-js="voucherDetail"
     data-id="i1086644292-261119" ...>
```

返回的是「礼品中心-详情」H5 页（约 10KB），**逐笔明细直接写在 HTML 里**：

```html
赠送10阅饼代金券 | 看视频送礼
获得时间：2026-11-16 12:46:53
有效期至：2026-11-19
```

所以每一笔的面额、来源、**精确到秒的获得时间**都能拿到。

## 券包 ID 的含义

`i1086644292-261119` = `<usr>-<YYMMDD>`
  `261119` → 2026-11-19（该券包的有效期）

## 用法

    python src/voucher_ledger.py              # 今天全部逐笔明细
    python src/voucher_ledger.py --all        # 所有券包
    python src/voucher_ledger.py --source 看视频   # 只看某来源
"""
import argparse
import re
import sys
import urllib.parse
from collections import defaultdict

sys.path.insert(0, "src")
from ireader_welfare import config as zconfig, auth  # noqa: E402
from ireader_welfare.http import get_json  # noqa: E402

API = "https://ah2.zhangyue.com"

RE_CARD = re.compile(
    r'<div class="card_useless(?P<used>[^"]*)"[^>]*data-id="(?P<vid>[^"]+)"')
RE_NUM = re.compile(r'<div class="num">([0-9.]+)</div>')
RE_NAME = re.compile(r'<div class="name">(.*?)</div>', re.S)
RE_TXT = re.compile(r'<div class="txt">([^<]*)</div>')

# 详情页的逐笔条目（实际结构）：
#   <div class="card">
#     <div class="name">赠送10阅饼代金券 | 看视频送礼</div>
#     <div class="time">获得时间：2026-11-16 12:46:53</div>
#     <div class="deadline">有效期至：<span class="date">2026-11-19</span></div>
#   </div>
RE_ITEM = re.compile(
    r'<div class="name">\s*(?:赠送|获得)([0-9.]+)阅饼代金券?\s*\|\s*([^<]{0,40}?)\s*</div>'
    r'\s*<div class="time">\s*获得时间：\s*'
    r'([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2})\s*</div>'
    r'\s*<div class="deadline">\s*有效期至：\s*(?:<span class="date">)?\s*'
    r'([0-9]{4}-[0-9]{2}-[0-9]{2})',
    re.S)


def list_packs(cred):
    """列出所有券包（聚合卡）。"""
    url = (f"{API}/zyuc/gift/ajaxrequest?index=2"
           f"&usr={urllib.parse.quote(cred.usr)}&zysid={cred.zysid}")
    raw = get_json(url)
    html = raw.get("html") or raw.get("_raw") or ""

    packs = []
    # 按卡片切分，每张卡取 data-id / num / name / expire
    parts = re.split(r'(?=<div class="card_useless)', html)[1:]
    for ch in parts:
        m = RE_CARD.search(ch)
        if not m:
            continue
        vid = m.group("vid")
        num = RE_NUM.search(ch)
        name = RE_NAME.search(ch)
        txt = RE_TXT.search(ch)
        exp = None
        if txt:
            e = re.search(r"(\d{4}-\d{2}-\d{2})", txt.group(1))
            exp = e.group(1) if e else None
        nm = re.sub(r"<[^>]+>", "", name.group(1)) if name else ""
        packs.append({
            "voucher_id": vid,
            "total": float(num.group(1)) if num else 0.0,
            "name": nm.strip(),
            "expire": exp,
            "used": "used" in (m.group("used") or ""),
        })
    return packs


def pack_detail(cred, voucher_id):
    """取单个券包的逐笔明细。"""
    url = (f"{API}/zyuc/gift/voucherDetail?voucherId={urllib.parse.quote(voucher_id)}"
           f"&usr={urllib.parse.quote(cred.usr)}&zysid={cred.zysid}")
    raw = get_json(url)
    html = raw.get("html") or raw.get("_raw") or ""
    if not html:
        return []

    items = []
    for m in RE_ITEM.finditer(html):
        items.append({
            "amount": float(m.group(1)),
            "source": re.sub(r"\s+", " ", m.group(2)).strip(),
            "time": m.group(3),
            "expire": m.group(4),
        })
    return items


def fmt_row(i, it):
    return (f"  {i:>2d}. {it['time']}  {it['amount']:>5g} 券  "
            f"{it['source']}  (有效期至 {it['expire']})")


def main():
    ap = argparse.ArgumentParser(description="逐笔代金券明细")
    ap.add_argument("--all", action="store_true", help="所有券包")
    ap.add_argument("--source", help="只显示该来源（如 看视频）")
    ap.add_argument("--date", help="只显示该日期的入账（如 2026-11-16）")
    a = ap.parse_args()

    cfg = zconfig.load("config.yaml")
    phone, pwd = zconfig.resolve_credentials(cfg, None, None)
    cred = auth.login(phone, pwd)

    packs = list_packs(cred)
    if not packs:
        print("未取到券包列表")
        return 1

    # 默认只看「今天」相关的包：名字里含视频，或有效期最近
    if not a.all:
        packs = [p for p in packs if "看视频" in p["name"]] or packs[:2]

    all_items = []
    for p in packs:
        items = pack_detail(cred, p["voucher_id"])
        for it in items:
            it["pack"] = p["voucher_id"]
            it["pack_total"] = p["total"]
        all_items.extend(items)
        print(f"\n=== 券包 {p['voucher_id']}  汇总 {p['total']:g} 券  "
              f"明细 {len(items)} 笔  ({p['name']}) ===")
        for i, it in enumerate(items, 1):
            print(fmt_row(i, it))
        if items:
            s = sum(x["amount"] for x in items)
            mark = "✓" if abs(s - p["total"]) < 0.01 else f"⚠ 汇总对不上（{p['total']:g}）"
            print(f"  小计 {s:g} 券 / {len(items)} 笔  {mark}")

    # 过滤
    sel = all_items
    if a.source:
        sel = [x for x in sel if a.source in x["source"]]
    if a.date:
        sel = [x for x in sel if x["time"].startswith(a.date)]

    if not sel:
        print("\n（无匹配明细）")
        return 0

    print(f"\n{'='*62}")
    print(f"合计 {sum(x['amount'] for x in sel):g} 券 / {len(sel)} 笔")

    # 按来源统计
    by_src = defaultdict(lambda: [0, 0.0])
    for x in sel:
        by_src[x["source"]][0] += 1
        by_src[x["source"]][1] += x["amount"]
    print("\n按来源：")
    for s, (n, amt) in sorted(by_src.items(), key=lambda kv: -kv[1][1]):
        print(f"  {s:20s} {n:>3d} 笔  {amt:>7g} 券")

    # 按小时统计（看分布）
    by_hour = defaultdict(lambda: [0, 0.0])
    for x in sel:
        h = x["time"][11:13]
        by_hour[h][0] += 1
        by_hour[h][1] += x["amount"]
    print("\n按小时：")
    for h in sorted(by_hour):
        n, amt = by_hour[h]
        bar = "█" * min(n, 30)
        print(f"  {h}:00  {n:>2d} 笔  {amt:>6g} 券  {bar}")

    # 面额分布（回答「有没有单笔 >10 券」）
    by_amt = defaultdict(int)
    for x in sel:
        by_amt[x["amount"]] += 1
    print("\n面额分布：")
    for amt in sorted(by_amt):
        print(f"  {amt:>5g} 券 × {by_amt[amt]} 笔")
    return 0


if __name__ == "__main__":
    sys.exit(main())