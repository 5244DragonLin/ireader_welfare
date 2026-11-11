#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""福利中心接口客户端。

所有请求的签名规则一致：
    参数按 key 字典序拼成 k=v&k=v → SHA1WithRSA 签名（客户端私钥）→ 作为 sign 字段

另外有一个容易踩的坑：福利中心的多数接口必须带 H5 页面参数
（p2/p3/p4/p29/p33），否则服务端返回 code:0 但业务不落账。
"""
import time
import urllib.parse

from .crypto import sorted_param_str
from .http import UA, get_json

API = "https://ah2.zhangyue.com"

# H5 福利页带的设备参数，缺失会导致接口“假成功”
PAGE_PARAMS = {
    "p2": "104583",
    "p3": "18070403",
    "p4": "501603",
    "p29": "zye5b814",
    "p33": "com.chaozh.iReaderFree",
}

# 常用返回码
CODE_OK = 0
CODE_ALREADY = 10240111        # 已领取过奖励
CODE_NO_WHEEL = 32533          # 转盘无次数
CODE_VIDEO_NO_CHANCE = 53002   # 视频次数用完 / 未开放
CODE_VIDEO_UNSUPPORTED = 50003 # 不支持视频


class WelfareClient:
    """福利中心客户端，持有一个已登录的凭证。"""

    def __init__(self, credential, sign_key=None):
        self.cred = credential
        self.usr = credential.usr
        self.zysid = credential.zysid
        # 默认用登录时下发的会话私钥；签到类接口用客户端内置私钥亦可
        self.key = sign_key or credential.native_priv

    # ---------------- 底层 ----------------
    def _call(self, path: str, params: dict = None, with_page: bool = True) -> dict:
        p = dict(PAGE_PARAMS) if with_page else {}
        p.update(params or {})
        p["usr"] = self.usr
        p["timestamp"] = str(int(time.time() * 1000))
        from .crypto import rsa_sha1_sign
        p["sign"] = rsa_sha1_sign(self.key, sorted_param_str(p))
        qs = urllib.parse.urlencode(p) + f"&zysid={self.zysid}"
        return get_json(
            f"{API}{path}?{qs}",
            headers={"Referer": f"{API}/activity/welfareIreader.html"},
        )

    # ---------------- 查询 ----------------
    def sign_page(self) -> dict:
        """福利页聚合数据：用户信息 + 转盘 + 宝箱。"""
        return self._call("/welfare/sign/page")

    def sign_info(self) -> dict:
        """签到活动详情（act_id / week_info / 奖励明细）。"""
        return self._call("/welfare/sign/info")

    def sign_get(self) -> dict:
        """签到状态（is_sign 以此为准，sign_info 可能缓存）。"""
        return self._call("/welfare/market/task/sign_get", {"appId": "com.chaozh.iReaderFree"})

    def sign_state(self) -> dict:
        """今日签到状态的 body，最可靠的判断来源。"""
        return self.sign_get().get("body") or {}

    def has_signed_today(self) -> bool:
        return bool(self.sign_state().get("is_sign"))

    def tasks(self) -> dict:
        """日常任务列表。"""
        return self._call("/welfare/sign/v2/task")

    def read_tasks(self) -> dict:
        """阅读赚代金券任务（10 档）。"""
        return self._call("/welfare/sign/v2/read")

    def read_tasks_v2(self) -> dict:
        """阅读赚代金券任务（新版活动接口）。

        H5 里 `getNewReadInfo` 按 `isNewActivity` 二选一：
        新版走 `/welfare/market/task/read_get`，旧版走 `/welfare/sign/v2/read`。
        老账号多半仍在旧活动，此时本接口 body 为空。
        """
        return self._call("/welfare/market/task/read_get")

    def read_reward_v2(self, params: dict = None) -> dict:
        """领取新版阅读任务奖励。"""
        return self._call("/welfare/market/task/read_reward", params or {})

    def video_status(self, position: str = "VIDEO_PAYTASK") -> dict:
        """看视频任务状态。position 常用值见 VIDEO_POSITIONS。"""
        return self._call("/zycl/welfare/video/status", {"position": position})

    def video_info(self, position: str = "VIDEO_PAYTASK") -> dict:
        """看视频任务文案（50003 表示不支持视频）。"""
        return self._call("/zycl/welfaremarket/video/info", {"position": position})

    def share_status(self) -> dict:
        """分享日签卡片数据（用于渲染日签图）。

        ⚠️ 返回体里的 `status` 是**黄历「宜/忌」标记**（1=宜，2=忌），
        卡片页就是靠它渲染日签上的【宜】/【忌】，**与任务是否完成无关**。

        body 示例：
            {"date": "2026-10-28", "desc": "重要的不是过去，而是未来。",
             "doing": "面对未来", "img_url": "https://...", "status": 1}

        要判断「分享日签」有没有做完，请用 share_task()。
        """
        return self._call("/welfare/sign/today/share")

    def share_task(self) -> dict:
        """「分享今日日签」任务的真实状态。

        从日常任务列表（/welfare/sign/v2/task）里挑出
        target_list[0].value.share_type == "daily_sign_in" 的那条。

        status: 0=未完成 / 1=可领取 / 2=已领取。

        注意：完成信号在 App 原生分享链路里，纯 HTTP 无法伪造。
        本方法只用于**如实展示**它是未完成还是已完成。
        """
        for t in self.tasks().get("body") or []:
            target = (t.get("target_list") or [{}])[0]
            if (target.get("value") or {}).get("share_type") == "daily_sign_in":
                return t
        return {}

    def box_state(self) -> dict:
        """宝箱状态：本周活跃值 + 三档宝箱进度。"""
        box = (self.sign_page().get("body") or {}).get("box") or {}
        score = int(box.get("user_score") or 0)
        chests = []
        for i, item in enumerate(box.get("list") or []):
            gift = (item.get("gift") or [{}])[0]
            chests.append({
                "index": i + 1,
                "need": int(item.get("score") or 0),
                "gift": gift.get("gift_name"),
                "able": bool(item.get("able")),
                "got": bool(item.get("got")),
            })
        nxt = next((c for c in chests if not c["got"] and not c["able"]), None)
        return {
            "score": score,
            "chests": chests,
            "next": nxt,
            "next_gap": (nxt["need"] - score) if nxt else 0,
        }

    # ---------------- 写操作 ----------------
    def do_sign(self) -> dict:
        """执行签到。

        ⚠️ 必须带页面参数，否则接口返回 code:0 但 is_sign 不变化（假成功）。
        调用后请用 has_signed_today() 复核。
        """
        return self._call("/zycl/welfaremarket/sign")

    def claim_sign_reward(self, act_id=None, act_type=None) -> dict:
        """领取签到奖励（签到弹窗里的 autoSign）。

        返回 CODE_OK 表示领取成功，CODE_ALREADY 表示今天已领过。
        """
        st = self.sign_state()
        params = {
            "actId": str(act_id or st.get("act_id") or ""),
            "actType": str(act_type or st.get("act_type") or ""),
        }
        return self._call("/zycl/welfaremarket/draw", params)

    def reset_sign_flow(self) -> dict:
        """签到 + 领奖一步到位，以 has_signed_today() 判定成败。"""
        result = {"signed": False, "reward": None, "already": False}
        if self.has_signed_today():
            result["already"] = True
            result["signed"] = True
        else:
            self.do_sign()
            if not self.has_signed_today():
                return result
            result["signed"] = True
        result["reward"] = self.claim_sign_reward()
        return result

    def wheel_draw(self, act_id) -> dict:
        """大转盘抽奖，act_id 取自 sign_page 的 wheel.act_id。"""
        p = dict(PAGE_PARAMS)
        p.update({"ca": "Activity_NewDzp.Draw", "activeId": str(act_id)})
        p["usr"] = self.usr
        p["timestamp"] = str(int(time.time() * 1000))
        from .crypto import rsa_sha1_sign
        p["sign"] = rsa_sha1_sign(self.key, sorted_param_str(p))
        qs = urllib.parse.urlencode(p) + f"&zysid={self.zysid}"
        return get_json(f"{API}/zyam/app/app.php?{qs}")

    def wheel_chances(self) -> int:
        wheel = (self.sign_page().get("body") or {}).get("wheel") or {}
        return int(wheel.get("draw_times") or 0)

    def wheel_act_id(self):
        wheel = (self.sign_page().get("body") or {}).get("wheel") or {}
        return wheel.get("act_id")

    def mark_app_open(self) -> bool:
        """给「打开掌阅APP」任务制造一次 App 页面访问（碰运气，不保证生效）。

        「打开掌阅APP」（+10 经验值，id=13901）的判定是**客户端冷启动上报**：
        真实字段为 `jump_url: "javascript:void(0);"`、`value.type: 20036`
        （本地行为埋点），说明「访问某个 webview 页面」并不能完成它。

        ⚠️ 2026-11-11 反编译 8.8.1 后确认：此前「跑一次抽奖页就变可领」的观测是巧合
        （当天真机冷启动过 App），逐一复现各类页面均无法触发。
        因此本方法保留作廉价尝试，**不要指望它稳定生效**。

        要可靠完成该任务：真机冷启动一次 App 即可（已实测：冷启动后 35 秒内 status 由 0 翻为 1）。
        详见 docs/掌阅逆向笔记.md 第十一节。
        """
        import urllib.request
        from .http import DEFAULT_TIMEOUT, UA

        url = (f"{API}/zyam/app/app.php?ca=Welfare.Index&pagePos=task"
               f"&usr={self.usr}&zysid={self.zysid}")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
                resp.read(2048)
            return True
        except Exception:
            return False

    def coupons(self) -> list:
        """满减优惠券列表（转盘常抽到这类奖品）。

        ⚠️ 与「代金券」是两套东西，接口也不同：
          代金券 → /zyuc/gift/ajaxrequest   （选书助手 picker skill 查余额用它）
          优惠券 → /zyuc/coupon/ajaxrequest （满1000减200 之类记在这里）

        返回体是 {"html": "<div class='card_use'>..."}，卡片本身是 HTML，
        所以按卡片切段再用正则取券名与有效期；带 expired class 的是已过期。
        """
        import re
        import urllib.request
        from .http import DEFAULT_TIMEOUT, UA

        url = (f"{API}/zyuc/coupon/ajaxrequest?index=1"
               f"&usr={self.usr}&zysid={self.zysid}")
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "ignore")
        try:
            import json as _json
            html = (_json.loads(raw) or {}).get("html") or ""
        except ValueError:
            html = raw

        out = []
        # 每张券以 <div class="card_use ..."> 开头
        chunks = re.split(r'<div class="card_use', html)[1:]
        for seg in chunks:
            expired = seg.lstrip().startswith("expired")
            name = re.search(r'class="name">([^<]*)<', seg)
            valid = re.search(r'class="txt[^"]*">([^<]*)<', seg)
            amount = re.search(r'class="num">([^<]*)<', seg)
            pid = re.search(r'data-productid="(\d+)"', seg)
            out.append({
                "name": (name.group(1).strip() if name else ""),
                "amount": (amount.group(1).strip() if amount else ""),
                "valid": (valid.group(1).strip() if valid else ""),
                "expired": expired,
                "product_id": (pid.group(1) if pid else ""),
            })
        return out

    def vouchers(self) -> list:
        """代金券（阅饼）明细，含面额 / 来源 / 有效期。

        ⚠️ 与「满减优惠券」是两套东西（见 coupons 的注释）：
          代金券 → /zyuc/gift/ajaxrequest   index=2 是「已获得」（含有效期）

        返回体同样是 HTML 卡片片段，解析规则与 ireader-picker 技能一致：
          <div class="card_useless used" data-id="...">
            <div class="num">30</div><div class="tag">代金券</div>
            <div class="name">获得30阅饼 | 签到送礼</div>
            <div class="txt">有效期至2026-11-30</div>
        已使用的卡片带 used class；是否过期按有效期日期判断。
        """
        import re

        url = f"{API}/zyuc/gift/ajaxrequest?index=2&usr={self.usr}&zysid={self.zysid}"
        raw = get_json(url)
        html = raw.get("html") or raw.get("_raw") or ""

        out = []
        for chunk in re.split(r'(?=<div class="card_useless)', html)[1:]:
            amt = re.search(r'<div class="num">([0-9.]+)</div>', chunk)
            if not amt:
                continue
            name = re.search(r'<div class="name">(.*?)</div>', chunk, re.S)
            txt = re.search(r'<div class="txt">([^<]*)</div>', chunk)
            exp = None
            if txt:
                m = re.search(r'(\d{4}-\d{2}-\d{2})', txt.group(1))
                exp = m.group(1) if m else None
            out.append({
                "amount": float(amt.group(1)),
                "source": (re.sub(r"<[^>]+>", "", name.group(1)).split("|")[-1].strip()
                           if name else ""),
                "expire": exp,
                "used": "used" in chunk[:60],
            })
        return out

    def open_box(self, index: int) -> dict:
        """开启宝箱（index 从 1 开始）。活跃值不足时不会误扣。"""
        return self._call("/zycl/welfare/box/open", {"index": index})

    def claim_daily_tasks(self) -> list:
        """领取所有已完成但未领奖的日常任务（如分享日签）。

        注意：任务完成与否由服务端判定，客户端无法伪造。
        这里只负责「已完成 → 领奖」这一步。

        返回 [{name, award, code, msg, ok}]
        """
        out = []
        tasks = self.tasks().get("body") or []
        for t in tasks:
            if not t.get("id") or not t.get("id_sign"):
                continue
            # status: 1=可领取 2=已领取 0=未完成
            if t.get("status") != 1:
                continue
            r = self._call("/zycl/newsign/task/draw", {
                "id": str(t.get("id")),
                "idSign": t.get("id_sign"),
            })
            out.append({
                "name": t.get("name"),
                "award": t.get("award"),
                "code": r.get("code"),
                "msg": r.get("msg"),
                "ok": r.get("code") == CODE_OK,
            })
        return out

    def claim_read(self, task: dict) -> dict:
        """领取单档阅读奖励（需 id + id_sign）。"""
        return self._call("/zycl/newsign/task/draw", {
            "id": str(task.get("id")),
            "idSign": task.get("id_sign"),
        })

    def claim_read_all(self, max_amount: int = 0) -> list:
        """领取所有已达标的阅读档位。max_amount>0 时跳过超过该额度的档。

        已领取（status==2）的档会跳过，避免重复请求换来一串 22030。
        """
        tasks = (self.read_tasks().get("body") or {}).get("list") or []
        done = []
        for t in tasks:
            if t.get("status") == 2:      # 已领取
                continue
            cur, quota = t.get("current") or 0, t.get("quota") or 0
            reached = t.get("status") == 1 or (quota and cur >= quota)
            if not reached:
                continue
            amount = int((t.get("gift") or {}).get("amount") or 0)
            if max_amount and amount > max_amount:
                continue
            done.append({"name": t.get("name"), "amount": amount, "resp": self.claim_read(t)})
        return done

    # ---------------- 阅读时长抽奖（与大转盘是两个活动） ----------------
    def read_lottery_state(self) -> dict:
        """阅读时长抽奖的状态：次数、actSign、是否在活动期。

        这是福利页「去抽奖」跳过去的那个页面（每天最多 4 次，
        由阅读时长里程碑解锁），**与大转盘 act_id=12516 不是同一个活动**，
        活动号是 READ_LOTTERY_ACT_ID。

        页面是服务端渲染的 HTML，状态塞在 `id="dataMap"` 的 data-option
        属性里（未登录时 drawTimes 恒为 0），所以带上 usr/zysid 直接抓页面再解析。
        """
        import re
        import urllib.request
        from .http import DEFAULT_TIMEOUT, UA

        url = (f"{API}/zyam/app/app.php?ca=Activity_NewDzp.Index"
               f"&activeId={READ_LOTTERY_ACT_ID}&a0=ydcc-09"
               f"&usr={self.usr}&zysid={self.zysid}")
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            html = resp.read().decode("utf-8", "ignore")

        i = html.find('id="dataMap"')
        if i < 0:
            return {}
        seg = html[i:i + 8000]
        m = re.search(r"data-option='(\{.*?\})'", seg, re.S)
        if not m:
            return {}
        import json as _json
        st = _json.loads(m.group(1))

        # 顺手把「阅读 X 分钟得 1 次机会」的里程碑列表抠出来，
        # 页面里是 data-list='[{"t":30,"d":1},...]'，领奖时要原样回传
        ml = re.search(r"data-list='(\[[^\]]*\])'", html)
        if ml:
            try:
                st["milestones"] = _json.loads(ml.group(1))
            except ValueError:
                st["milestones"] = []
        return st

    def read_lottery_claim_awards(self) -> dict:
        """领取阅读时长里程碑的抽奖机会（30/60/120/180 分钟各 1 次）。

        这一步不能省：不领里程碑，drawTimes 就是 0。已领过时服务端回
        `code:1 领取失败`，无副作用，可以放心重复调用。
        """
        st = self.read_lottery_state()
        ms = st.get("milestones") or []
        if not ms:
            return {}
        import json as _json
        return self._call("/zyam/app/app.php", {
            "ca": "Activity_NewDzp.DrawReadAward",
            "activeId": READ_LOTTERY_ACT_ID,
            "list": _json.dumps(ms, separators=(",", ":")),
        })

    def read_lottery_draw(self, act_sign: str) -> dict:
        """阅读时长抽奖抽一次。需要页面下发 actSign。"""
        return self._call("/zyam/app/app.php", {
            "ca": "Activity_NewDzp.Draw",
            "activeId": READ_LOTTERY_ACT_ID,
            "actSign": str(act_sign),
        }, with_page=True)

    def read_lottery_draw_all(self, max_times: int = 10, claim_first: bool = True) -> list:
        """把阅读时长抽奖的次数抽完，返回每轮结果。

        `claim_first=True` 时先领里程碑机会——阅读够 30/60/120/180 分钟后
        那 4 个「得1次机会」不会自动到账，必须调一次 DrawReadAward。
        """
        if claim_first:
            self.read_lottery_claim_awards()
        st = self.read_lottery_state()
        if not st.get("isLogin"):
            return []
        left = int(st.get("drawTimes") or 0)
        sign = st.get("actSign")
        out = []
        while left > 0 and len(out) < max_times:
            r = self.read_lottery_draw(sign)
            gift = r.get("gift") or {}
            ok = r.get("code") == CODE_OK
            out.append({
                "ok": ok,
                "name": gift.get("desc") or gift.get("gift_type") or "",
                "code": r.get("code"),
                "msg": r.get("msg"),
                "left": r.get("draw_times"),
            })
            if not ok:
                break
            # 服务端会回剩余次数；没有就重抓页面
            left = r.get("draw_times")
            if left is None:
                left = int((self.read_lottery_state().get("drawTimes") or 0))
            left = int(left)
        return out


# 阅读时长抽奖的活动号（福利页「去抽奖」按钮跳转时带的 activeId）
READ_LOTTERY_ACT_ID = "16416"

VIDEO_POSITIONS = (
    "VIDEO_PAYTASK",
    "SIGN_SHARE",
    "MARKET_SIGN_OBTAIN_VOUCHER",
    "MARKET_READ_OBTAIN_VOUCHER",
)