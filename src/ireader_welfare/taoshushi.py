#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""淘书市活动满减券的纯 HTTP 领取（2026-11-17 打通）。

══════════════════════════════════════════════════════════════
入口在哪：App「书城 → 出版」频道页顶部的滚动 banner（约一月两次换期）
══════════════════════════════════════════════════════════════

链路（全部实测，详见 docs/掌阅逆向笔记.md 二十一节）：

    ① 频道内容接口（匿名可访问）：
       GET ah2.zhangyue.com/zybk/channel/ajaxrequest?page=1&key=ch_publish&index=0
       → blockData（HTML）里 class="slider-banner" 就是顶部滚动栏，
         每张图一个 <li class="slider-item" data-url="...">。
         淘书市那期的 data-url 是 h5.zhangyue.com/opr/<act_id>.html。

    ② 活动页（匿名可访问）：
       GET h5.zhangyue.com/opr/<act_id>.html
       SSR HTML 里 class="amp_coupon_*" 块含每张券的面额/门槛/有效期。

    ③ 券的 product_id 在页面的 JS bundle 里（每期活动一个独立 bundle）：
       <script src=".../opr/js/<act_id>.<hash>.js">
       req_params:{ids:["31226",...]},req_type:"coupon" 按组件顺序排列，
       与 HTML 里 amp_coupon 块的顺序一一对应。

    ④ 领取（需登录态 + 双请求头签名）：
       POST ah2.zhangyue.com/operation/wap/coupon_receive?act_id=...&usr=...&zysid=...
       body: product_id=<id>
       headers: X-Nonce = unix秒的十六进制
                X-Sign  = sha256( sha256(签名串)[9:25] + unix秒 )

签名串构造（与 H5 的 Sign.getSignNonce 完全一致）：
    path + "k=v&k=v"   —— 参数按 key 字典序，body 与 query 合并后一起排
    2026-11-17 实测：漏拼 query 或给 body/query 分两段各排都会 53001。

返回码（实测）：
    0      ok                领取成功（重复领取不会误报）
    200003 您已领取过该优惠券  已领过 → 视为成功，幂等
    130001 获取数据错误        product_id 无效 / 活动已下线
    53001  签名错误           签名串拼错（调试用）
"""
import json
import re
import time
import urllib.parse
import urllib.request

from .http import UA

API = "https://ah2.zhangyue.com"
H5 = "https://h5.zhangyue.com"

# 滚动栏可能出现的频道（出版/精选都实测挂过淘书市 banner）
CHANNEL_KEYS = ("ch_publish", "ch_feature")

# 领券响应码
CODE_OK = 0
CODE_RECEIVED = 200003     # 已领取过 → 幂等成功
CODE_INVALID = 130001      # product_id 无效 / 活动下线
CODE_SIGN_ERROR = 53001


# ---------------- 签名（H5 Sign.getSignNonce 的 Python 复现） ----------------
def _sorted_query(d: dict) -> str:
    """参数按 key 字典序拼 k=v&k=v（空值保留，与 JS 版一致）。"""
    parts = []
    for i, k in enumerate(sorted(d)):
        v = "" if d[k] is None else d[k]
        parts.append(f"{k}={v}" if i == 0 else f"&{k}={v}")
    return "".join(parts)


def sign_headers(path: str, body: dict, query: dict) -> dict:
    """构造 X-Nonce / X-Sign。

    签名串 = path + 排序后的(body+query 合并参数)，再：
      u = sha256(串) 的第 9~25 位（16 字符）
      s = int(time())
      X-Nonce = hex(s)
      X-Sign  = sha256(u + str(s))
    """
    merged = {**body, **query}
    raw = path + _sorted_query(merged)
    u = _sha256(raw)[9:25]
    s = int(time.time())
    return {
        "X-Nonce": format(s, "x"),
        "X-Sign": _sha256(u + str(s)),
    }


def _sha256(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------- ① 滚动栏 → 淘书市活动页 ----------------
# 识别关键词：每期 banner 名称（如「淘书市180期」「淘书市181期」）与活动页正文都带「淘书」
TAOSHU_KEYWORDS = ("淘书",)

# 滚动栏可能出现的频道（出版/精选都实测挂过淘书市 banner）
CHANNEL_KEYS = ("ch_publish", "ch_feature")

# 领券响应码
CODE_OK = 0
CODE_RECEIVED = 200003     # 已领取过 → 幂等成功
CODE_INVALID = 130001      # product_id 无效 / 活动下线
CODE_SIGN_ERROR = 53001


def fetch_banner_items(channel_key: str) -> list:
    """拉频道页滚动栏，返回 [(跳转url, banner名称)]（匿名）。

    banner 名称在 li 的 data-point JSON 里（如「淘书市180期」），用来区分
    滚动栏上挂的是淘书市还是别的活动。
    """
    from .http import get_json
    url = (f"{API}/zybk/channel/ajaxrequest?page=1&key={channel_key}&index=0")
    try:
        j = get_json(url)
    except Exception:
        return []
    html = (j.get("blockData") or "") if isinstance(j, dict) else ""
    items = []
    for m in re.finditer(r'<li[^>]*class="slider-item[^"]*"[^>]*>', html):
        tag = m.group(0)
        url_m = re.search(r'data-url="([^"]+)"', tag)
        if not url_m:
            continue
        name = ""
        point_m = re.search(r"data-point='([^']*)'", tag)
        if point_m:
            try:
                name = (json.loads(point_m.group(1)) or {}).get("name") or ""
            except ValueError:
                pass
        items.append((url_m.group(1), name))
    return items


def find_taoshushi_activities(channel_keys=CHANNEL_KEYS) -> list:
    """在滚动栏里找淘书市活动页，返回 [(act_id, 来源说明)]（去重、按优先级）。

    三级判定，专为「新一期换活动」设计 —— 全流程都不依赖写死的 act_id：

      ① banner 名称带「淘书」（如「淘书市180期」）→ 直接认定，零网络开销
      ② 名称不带 → 抓活动页正文确认带「淘书」→ 认定
      ③ 全部不带（官方改了文案）→ 认定「有券组件的 opr 活动页」：
         券组件是统一模板渲染的，有券就值得领，避免漏抓。

    没有运营活动页（没换期）返回空列表。
    """
    named, to_check = [], []
    seen = set()
    for key in channel_keys:
        for url, name in fetch_banner_items(key):
            m = re.match(r"https?://h5\.zhangyue\.com/opr/([A-Za-z0-9]+)\.html", url)
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            if any(kw in name for kw in TAOSHU_KEYWORDS):
                named.append((m.group(1), f"banner名称「{name}」"))
            else:
                to_check.append(m.group(1))

    if named:
        return named

    # ② 抓页面正文确认
    has_coupon = []
    for act_id in to_check:
        try:
            html = fetch_activity_page(act_id)
        except Exception:
            continue
        if any(kw in html for kw in TAOSHU_KEYWORDS):
            named.append((act_id, "活动页正文含淘书"))
        elif "amp_coupon" in html:
            has_coupon.append((act_id, "滚动栏券活动（兜底）"))
        time.sleep(0.5)

    # ③ 兜底：官方连「淘书」二字都不用了，但页面照常发券 → 照领
    return named or has_coupon


def find_taoshushi_act_id(channel_keys=CHANNEL_KEYS) -> str:
    """兼容旧调用：只返回第一个淘书市活动 id，没有则空串。"""
    acts = find_taoshushi_activities(channel_keys)
    return acts[0][0] if acts else ""


# ---------------- ② 活动页 → 券面额与门槛 ----------------
def fetch_activity_page(act_id: str, cred=None) -> str:
    """抓活动页 SSR HTML。带 usr/zysid 请求（页面渲染结果实测不区分登录态）。"""
    qs = {"act_id": act_id, "launch": "inside"}
    if cred is not None:
        qs.update({"usr": cred.usr, "zysid": cred.zysid})
    url = f"{H5}/opr/{act_id}.html?{urllib.parse.urlencode(qs)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "ignore")


def parse_coupons(html: str) -> list:
    """从活动页 HTML 解析 amp_coupon 块 → [{anchor, amount, quota, valid}]。

    块内每张券是一段 content：`<b>面额</b>阅饼` + `满X阅饼使用` + 有效期。
    顺序与 bundle 里 req_params.ids 的组件顺序一一对应（块级对应）。

    面额正则允许 <b> 与数字之间插任意标签（模板升级防一手）；
    就算全配不上，领取走的是 bundle 里的 product_id，只影响展示不影响到账。
    """
    out = []
    blocks = re.split(r'(?=<div id="anchor_\d+" class="amp_coupon)', html)[1:]
    for b in blocks:
        m = re.search(r'id="(anchor_\d+)" class="(amp_coupon[^"]*)"', b)
        anchor, cls = (m.group(1), m.group(2)) if m else ("", "")
        # 面额：<b>标签可隔</b>数字 —— 兼容模板在 b 内再嵌样式标签的升级
        amounts = re.findall(r"<b[^>]*>(?:<[^>]+>)*([\d.]+)", b)
        quotas = re.findall(r"<p[^>]*>(满[\d.]+阅饼[^<]*)</p>", b)
        valid = re.findall(r'class="time"[^>]*>([^<]+)<', b)
        for i, amount in enumerate(amounts):
            out.append({
                "anchor": anchor,
                "block_cls": cls,
                "amount": float(amount),
                "quota": quotas[i] if i < len(quotas) else "",
                "valid": valid[0] if valid else "",
            })
    return out


# ---------------- ③ 页面 bundle → 券 product_id ----------------
def fetch_bundle_url(act_id: str) -> str:
    """从活动页 HTML 抠出活动专属 bundle 的地址（每期一个，券 id 就在里面）。"""
    html = fetch_activity_page(act_id)
    # bundle 名与 act_id 同名：<script src=".../opr/js/<act_id>.<hash>.js">
    m = re.search(rf'src="[^"]*/opr/js/{re.escape(act_id)}\.([0-9a-f]+)\.js"', html)
    if m:
        return f"https://static-tos-front-h.zhangyuecdn.com/opr/js/{act_id}.{m.group(1)}.js"
    # 兜底：任何 opr/js 下的非公共 chunk
    for m in re.finditer(r'src="([^"]*/opr/js/[a-z0-9]+\.[0-9a-f]+\.js)"', html):
        name = m.group(1).rsplit("/", 1)[-1]
        if not name.startswith(("chunk-",)):
            return m.group(1) if m.group(1).startswith("http") else f"https:{m.group(1)}"
    return ""


def parse_product_ids(bundle_js: str) -> list:
    """从 bundle JS 里按组件顺序抠出券的 product_id。"""
    ids = []
    for m in re.finditer(r'req_params:\{ids:\[([^\]]*)\]\},req_type:"coupon"', bundle_js):
        ids.extend(re.findall(r'"(\d+)"', m.group(1)))
    return ids


def build_coupon_list(act_id: str, cred=None) -> list:
    """汇总：[{product_id, amount, quota, valid}]。

    HTML 的 amp_coupon 块（带面额/门槛）与 bundle 的 ids（按组件顺序）
    都是**从上到下**排列的，直接按顺序配对。两边数量对不上时，
    以 ids 为准、面额留空 —— 宁可少显示也不猜错配。
    """
    html = fetch_activity_page(act_id, cred)
    coupons = parse_coupons(html)
    bundle_url = fetch_bundle_url(act_id)
    ids = []
    if bundle_url:
        req = urllib.request.Request(bundle_url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            ids = parse_product_ids(r.read().decode("utf-8", "ignore"))

    flat = []
    for c in coupons:
        flat.append(c)
    out = []
    for i, pid in enumerate(ids):
        info = flat[i] if i < len(flat) else {}
        out.append({
            "product_id": pid,
            "amount": info.get("amount"),
            "quota": info.get("quota") or "",
            "valid": info.get("valid") or "",
        })
    return out


# ---------------- ④ 领取 ----------------
def receive_coupon(cred, product_id, act_id, timeout=15) -> dict:
    """领一张券。返回 {code, msg, ok, already}。

    活动页 URL 的 location 参数（act_id/usr/zysid 等）会整体进 query ——
    这是 H5 端 Url.assignParam(withLocationParams) 的行为，缺了会 130001。
    """
    query = {
        "act_id": act_id,
        "launch": "inside",
        "rgt": "",
        "pk": "ch_publish",
        "uios": "",
        "zyeid": "",
        "usr": cred.usr,
        "zysid": cred.zysid,
    }
    body = {"product_id": str(product_id)}
    headers = {
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": f"{H5}/opr/{act_id}.html",
        **sign_headers("/operation/wap/coupon_receive", body, query),
    }
    url = f"{API}/operation/wap/coupon_receive?{urllib.parse.urlencode(query)}"
    req = urllib.request.Request(url, data=urllib.parse.urlencode(body).encode(),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception as e:
        return {"code": "ERR", "msg": str(e), "ok": False, "already": False}

    code = j.get("code")
    return {
        "code": code,
        "msg": j.get("msg"),
        "ok": code in (CODE_OK, CODE_RECEIVED),
        "already": code == CODE_RECEIVED,
    }


def claim_all(cred, verbose=True) -> dict:
    """找当前期淘书市（可能同时挂多期）→ 解析券列表 → 逐张领取。

    返回 {act_id, acts, found, claimed, already, failed, total_amount, items}。
    没找到活动（没换期）时 found=False，不算错误。
    """
    out = {"act_id": "", "acts": [], "found": False, "claimed": 0, "already": 0,
           "failed": 0, "total_amount": 0.0, "items": []}

    acts = find_taoshushi_activities()
    if not acts:
        if verbose:
            print("淘书市: 本期活动不在滚动栏里（未换期或已结束）")
        return out
    out["acts"] = [a for a, _ in acts]
    out["act_id"] = out["acts"][0]
    out["found"] = True

    for act_id, source in acts:
        try:
            coupons = build_coupon_list(act_id, cred)
        except Exception as e:
            out["msg"] = f"解析活动页失败: {e}"
            if verbose:
                print(f"淘书市: 解析活动页 {act_id} 失败（{e}）")
            continue
        if not coupons:
            if verbose:
                print(f"淘书市: 活动 {act_id}（{source}）里没有满减券组件")
            continue

        if verbose:
            tail = f"（{source}）" if len(acts) > 1 else ""
            print(f"\n淘书市: 活动 {act_id}{tail}，{len(coupons)} 张券")

        for cinfo in coupons:
            pid = cinfo["product_id"]
            amount = cinfo.get("amount")
            quota = cinfo.get("quota") or ""
            label = f"{amount:g}券" if amount else f"#{pid}"
            r = receive_coupon(cred, pid, act_id)
            item = {"act_id": act_id, "product_id": pid, "amount": amount,
                    "quota": quota, "valid": cinfo.get("valid") or "",
                    "code": r["code"], "msg": r["msg"], "ok": r["ok"],
                    "already": r["already"]}
            out["items"].append(item)
            if r["ok"]:
                if r["already"]:
                    out["already"] += 1
                    if verbose:
                        print(f"  {label:<8s} 已领过（幂等跳过）")
                else:
                    out["claimed"] += 1
                    if amount:
                        out["total_amount"] += amount
                    if verbose:
                        print(f"  {label:<8s} 领取成功  {quota}")
            else:
                out["failed"] += 1
                if verbose:
                    print(f"  {label:<8s} 失败 code={r['code']} {r['msg']}")
            time.sleep(1)

    return out
