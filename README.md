# ireader_welfare：掌阅福利中心自动化工具

纯 Python 实现的掌阅 App 福利中心自动化工具，凭手机号与密码登录，自动完成签到领奖、两个抽奖、阅读时长与开宝箱。不起浏览器、不装模拟器、不需要抓包。

## 💡为什么需要？

- **每天要手动点好几处**：签到、领奖、转盘、阅读时长、开宝箱散落在福利中心不同位置，容易漏
- **拿满阅读奖励要读满 3 小时**：阅读赚代金券 10 档合计 180 分钟，手动读费时费力，本工具在电脑上几秒铺满
- **代金券有效期只有几天**：签到给的 30 代金券过期作废，本工具自动帮你领，能领的都不落下
- **掌阅没有自动化入口**：本工具模拟官方操作，把这些动作收敛成一条命令

每天跑一次即可自动完成这些动作，也可以挂到计划任务里。

## ⭐亮点

- **零设备依赖**：签到、领奖、两个抽奖、阅读时长都在电脑上完成，不需要手机挂机
- **密码登录，不走短信**：手机号 + 密码直接登录，一次登录长期有效
- **阅读时长电脑上拿满**：不用挂着手机，10 档阅读奖励就能全部拿到
- **两个抽奖都不漏**：大转盘与阅读时长抽奖是两个独立活动，`run` 会各自抽完
- **诚实报状态**：领不到的直接说「未开放」，不伪造成功

## 🖥️效果预览

```bash
$ python src/main.py status

—— 账号 ——
  昵称     : 示例用户  (Lv14)
  账号     : 1234567890
  今日签到 : 已签到  (本周第 3 天 / 活动 12788)
  本周活跃值: 15
    宝箱#1 需  75 → 5代金券     差 60
    宝箱#2 需 100 → 7代金券     差 85
    宝箱#3 需 150 → 10代金券    差 135
  转盘机会 : 1 次
  阅读抽奖 : 4 次
  代金券   : 559 可用
    [!] 30  2026-11-12 到期  (2 天后)  签到送礼
    [!] 20  2026-11-13 到期  (3 天后)  阅读抽奖
    —— 三天内到期 共 2 张 / 50，别忘花掉 ——
  满减券   : 1 张可用
    新满1000减200  2026-11-10至 2026-11-13使用

—— 日常任务（4 项）——
  [0] 阅读品质好书 满5分钟
  [0] 收听品质好书满10分钟
  [2] 打开掌阅APP
  [0] 分享今日日签  20

—— 阅读赚代金券（10 档，全领 505）——
  +10代金券         2/2   分钟  可领
  +15代金券         5/5   分钟  可领
  +20代金券        10/10  分钟  可领
  ...
```

一条龙执行（默认精简输出，加 `--verbose` 看完整逐行日志）：

```bash
$ python src/main.py run

签到: 成功，奖励已领（获取奖励成功）

阅读时长: 上报 180 分钟，18/18 段成功

阅读: 10/10 档全领，共 505 代金券

任务: 领取 打开掌阅APP +10经验值；3 项待真机（阅读满5分钟、收听品质好书满10分钟、分享日签）

转盘: 1 次抽完 → 「满1000减200阅饼优惠券」
阅读抽奖: 4 次抽完 → 10+10+2+2 代金券
宝箱: 开启成功 #1: 5代金券

—— 账面 ——
  代金券 1118 可用｜[!] 30 于 2026-11-12 到期（1 天后）
  转盘 0 次｜阅读抽奖 0 次｜满减券 1 张
```

## 🚀快速开始

### 1. 克隆项目

```bash
# Gitee 镜像（国内访问快）
git clone https://gitee.com/yhl5244/ireader_welfare.git

# GitHub 原仓库
git clone https://github.com/5244DragonLin/ireader_welfare.git

cd ireader_welfare
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置账号

推荐用环境变量，避免密码落盘：

```bash
# Windows
set IREADER_PHONE=你的手机号
set IREADER_PWD=你的密码

# macOS / Linux
export IREADER_PHONE=你的手机号
export IREADER_PWD=你的密码
```

也可以复制配置模板：

```bash
cp config.example.yaml config.yaml
# 编辑 config.yaml 填入 phone / password
```

### 4. 运行

```bash
# 只看状态，不做任何写操作
python src/main.py status

# 执行一条龙（自动先铺阅读时长，再签到领奖抽奖开箱）
python src/main.py run
```

> `run` 已经把「铺阅读时长」并进来了：它会先把 180 分钟阅读时长报到网页版接口，再领 10 档代金券、签到、抽奖、开箱。**日常只需这一条命令**。

## ⌨️CLI 模式

```bash
python src/main.py [command] [options]
```

### 命令

| 参数 | 说明 |
|------|------|
| `status` | 只查看状态，不做任何写操作 |
| `run` | 铺阅读时长 → 签到 → 领阅读奖 → 领日常任务 → 转盘 → 阅读抽奖 → 开箱（默认命令） |
| `sign` | 只签到并领签到奖励 |
| `wheel` | 把大转盘的次数一次抽完 |
| `lottery` | 领里程碑机会并抽完阅读时长抽奖 |
| `read` | 只领取已达标的阅读奖励 |
| `box` | 只开启已达标的宝箱 |
| `tasks` | 领取已完成的日常任务奖励 |

> `run` 的顺序已按活动逻辑排好：抽奖排在领奖之后，刚领到的抽奖机会不会被浪费。

### 账号

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--phone` | 手机号 | 环境变量 `IREADER_PHONE` |
| `--password` | 密码 | 环境变量 `IREADER_PWD` |
| `--config` | 配置文件路径 | 向上查找 `config.yaml` |

### 控制

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--no-read-pc` | 跳过「先铺阅读时长」这一步 | `false` |
| `--read-pc-minutes` | 铺多少分钟阅读时长 | `180` |
| `--no-wheel` | 跳过转盘抽奖 | `false` |
| `--no-lottery` | 跳过阅读时长抽奖 | `false` |
| `--no-read` | 跳过阅读领奖 | `false` |
| `--no-box` | 跳过开箱 | `false` |
| `--no-tasks` | 跳过日常任务领奖 | `false` |

### 输出

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--json` | 以 JSON 输出状态 | `false` |
| `--verbose` | 完整逐行输出（含操作后全量状态） | `false` |
| `--save` | 保存凭证摘要到输出目录 | `false` |

## 📖阅读时长与抽奖

「阅读赚代金券」共 10 档（需读 2/5/10/15/20/30/45/60/120/180 分钟，全领 505 代金券）。`python src/main.py run` 已内置这一步：会先把 180 分钟时长报到网页版接口，再领这 10 档奖励，**不需要额外操作**。想调铺的时长用 `--read-pc-minutes N`，想跳过用 `--no-read-pc`。

重复跑安全：已上报过的时段会自动避开，且只铺在「今天零点 → 当前时刻」之间，不会占用未来的时间。

### 阅读时长抽奖（每天 4 次）

读完 30 / 60 / 120 / 180 分钟各解锁 1 次，每天最多 4 次。它和大转盘是**两个独立活动**，抽完大转盘不代表抽完了。

```bash
python src/main.py lottery      # 领里程碑机会 + 抽完
```

注意：阅读时长够了之后，还要领一次才会变成抽奖机会；领完奖后顺手把它跑掉即可（`run` 里已含此步）。

## 📄配置文件

```yaml
phone: "13800138000"
password: "your-password"

output:
  dir: output
  save_credentials: false
```

优先级：`命令行参数` > `环境变量` > `config.yaml` > `内置默认值`。

`config.yaml` 已加入 `.gitignore`，不会被提交到仓库。

## 📁项目结构

```text
ireader_welfare/
├── src/
│   ├── main.py                    # 入口
│   ├── share_daily.py             # 真机上真实分享日签
│   ├── read_keep.py               # 真机挂机备选
│   ├── read_probe.py              # 阅读任务探测（只读）
│   └── ireader_welfare/
│       ├── cli.py                 # 命令行与状态展示
│       ├── auth.py                # 密码登录、凭证解析
│       ├── crypto.py              # 登录加密
│       ├── reading.py             # 阅读时长上报（run 会自动调用）
│       ├── welfare.py             # 福利中心操作
│       ├── config.py              # 配置加载
│       ├── http.py                # HTTP 请求
│       └── native_keys.json       # 登录密钥
├── docs/
│   ├── 掌阅逆向笔记.md             # 开发者看的还原笔记
│   ├── 阅读时长方案.md             # 阅读时长怎么实现
│   └── 自动化方案评估.md           # 各任务自动化方案对比
├── config.example.yaml
├── requirements.txt
├── LICENSE
└── README.md
```

## ⚙️配置说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `phone` | 手机号 | 空 |
| `password` | 密码 | 空 |
| `output.dir` | 输出目录 | `output` |
| `output.save_credentials` | 是否落盘凭证摘要 | `false` |

> 想跳过某个动作（签到 / 转盘 / 领奖 / 开箱等），用命令行开关 `--no-wheel`、`--no-read` 等，见上方「控制」表；这些不在配置文件里设置。

## ❓️FAQ

**Q：怎么判断任务真的成功了？**

A：不用看返回码。本工具一律按任务的实际状态判断，显示成功就是真的成功。

**Q：哪些任务必须真机才能完成？**

A：四项：分享今日日签（+20 代金券）、阅读品质好书满5分钟与收听品质好书满10分钟（各 +10 活跃值）、打开掌阅APP（+10 经验值）——它们的完成信号都在客户端原生层，纯 HTTP 够不到。分享日签可配 `src/share_daily.py`，用 adb 驱动真机真实分享，不用 root；打开掌阅APP 需要真机冷启动一次 App（详见 [docs/掌阅逆向笔记.md](docs/掌阅逆向笔记.md)）。观看视频需要真机，且当前账号服务端未开放额度。

**Q：阅读时长真的能在电脑上拿满吗？**

A：这项默认就包含在 `run` 里了——跑一次 `python src/main.py run`，它会先铺 180 分钟时长再领奖，10 档全部拿到。想调整时长用 `--read-pc-minutes N`。

**Q：凭证会泄露吗？**

A：默认不落盘。开了 `--save` 也只保存账号和昵称等摘要，不包含密码。

**Q：多久跑一次合适？**

A：每天一次即可。宝箱按自然周累计，周日 23:59:59 清零，建议每天固定时间跑，避免漏签。

## 📝已知问题 / 待改进点

- [ ] 把「分享今日日签」接进一条龙 `run`（目前需用 `src/share_daily.py` 在真机单独分享一次）
- [ ] 实现「观看视频」自动化（当前需在真机 App 内手动观看，每次 10 代金券）
- [ ] 「打开掌阅APP」任务需要真机冷启动一次 App 才能完成（纯 HTTP 做不到，见 `docs/掌阅逆向笔记.md`）

## 🤝贡献

欢迎提交 Issue 与 Pull Request。

1. Fork 本仓库
2. 新建分支 `git checkout -b feature/your-feature`
3. 提交改动 `git commit -m "feat: 你的改动"`
4. 推送 `git push origin feature/your-feature`
5. 发起 Pull Request

## 📋更新日志

### v0.1

- 手机号密码登录掌阅福利中心，无需验证码与抓包
- 一条龙 `run` 完成签到、领阅读奖、领日常任务、两个抽奖与开宝箱
- 阅读赚代金券 10 档纯 HTTP 拿满，无需真机挂机
- 阅读时长上报自动避开已报时段，只铺当天已过去的时间
- 状态面板显示代金券余额与到期提醒，避免过期作废
- 默认精简输出，`--verbose` 查看完整逐行日志

## 🔗关联项目

- [**ireader-picker**](https://github.com/5244DragonLin/ireader-picker) — 掌阅选书助手，消费本项目挣到的代金券：查余额、按券额筛书、下单
  - GitHub：https://github.com/5244DragonLin/ireader-picker
  - Gitee：https://gitee.com/yhl5244/ireader-picker
- [**ireader_bookshelf_exporter**](https://github.com/5244DragonLin/ireader_bookshelf_exporter) — 掌阅书架 EPUB 导出工具，把已购书籍导出为带完整元数据的标准 EPUB
  - GitHub：https://github.com/5244DragonLin/ireader_bookshelf_exporter
  - Gitee：https://gitee.com/yhl5244/ireader_bookshelf_exporter

## ☕捐赠

如果这个项目帮你省下了每天点来点去的时间，欢迎请作者喝杯咖啡。

| 支付宝 | 微信 |
|--------|------|
| ![支付宝](https://gitee.com/yhl5244/images/raw/master/donate_alipay.jpg) | ![微信](https://gitee.com/yhl5244/images/raw/master/donate_wechat.jpg) |

## ⚠️免责声明

本项目仅供个人学习与技术研究，用于自动化操作自己的账号。自动化操作可能违反掌阅用户协议，使用后可能面临账号被限制的风险。请勿用于批量操作他人账号或任何商业用途。使用本工具所产生的一切后果由使用者自行承担。

## 📃许可证

本项目基于 [MIT](LICENSE) 协议开源。
