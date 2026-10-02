# DanceRail Refresh 私有服务器

> 为 GameMaker Studio 1 游戏 **《DanceRail Refresh》** 实现的 Python / Flask 私服。
> 全部接口严格对齐反编译客户端源码（见 [`original_gamecode/`](original_gamecode/)）。

本项目最初是一个单文件 `app.py`（一个通配路由 + 一串 `startswith` 判断，
用 `try/except` 控制流程，只实现了约一半接口且存在数据丢失缺陷）。
现在已重构为分层架构，并把客户端用到的 **16 个接口全部实现**。

---

## 目录

- [快速开始](#快速开始)
- [部署要点（必读）](#部署要点必读)
- [实现了哪些功能](#实现了哪些功能)
- [项目结构](#项目结构)
- [配置项](#配置项)
- [数据文件](#数据文件)
- [测试](#测试)
- [文档索引](#文档索引)
- [相对旧版的修正清单](#相对旧版的修正清单)

---

## 快速开始

环境要求：**Python 3.10+**（使用了 `dataclass(slots=True)` 与 `X | None` 语法）。

```shell
# 1. 创建虚拟环境（可选但推荐）
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux / macOS

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动（生产：waitress 多线程）
python run.py
```

访问 <http://127.0.0.1/DanceRail/admin> 可以看到服务器状态页。

调试模式（Flask 自带服务器 + 热重载）：

```shell
python app.py
```

---

## 部署要点（必读）

### 1. 端口必须是 80

客户端源码把服务器地址**硬编码**成了：

```gml
http://192.168.0.100/DanceRail/date.php
```

URL 里没有端口号 ⇒ 实际请求 **80 端口**。因此：

- 本服务器默认监听 `0.0.0.0:80`；
- 本机 IP 需要是 `192.168.0.100`（或把客户端重打包成你的地址）；
- 若 80 端口被 IIS / Apache 占用，用 `DRR_PORT` 换端口后自己加转发规则。

> 旧版 `run.py` 监听 12561，客户端是**连不上**的。

### 2. 云存档是并发突发流量

客户端在上传/下载云存档时，会在**同一帧内并发发出 96 个 GET 请求**
（`obj_upload_Step_0.gml:83-92`，4 难度 × 6 谱面 × 4 字段），
一整套存档共 `90 + 96 × 曲目数` 个请求（132 首曲子约 12762 个）。

因此**必须使用多线程 WSGI 服务器**：

- ✅ `python run.py`（waitress，默认 16 线程）
- ❌ `flask run` / `app.run()`（单线程，会被堵死）

线程数可用 `DRR_THREADS` 调整。

### 3. 客户端只在非 Windows 平台上榜

客户端源码中有 `os_type != os_windows` 判断：

| 接口 | 触发条件 |
|---|---|
| `update.php` | 非 Windows 且已登录 |
| `scoreupload.php` | 非 Windows 且已登录且非自动演示 |

所以用 **Windows 版客户端测试时，榜单永远是空的**，这是原版行为。
想验证排行榜，请用 Android 客户端，或临时把客户端里的判断去掉再打包。

### 4. 服务器无法读到 `songlist.drf`

曲目数量由客户端从 `songlist.drf` 读取，服务器拿不到这个文件。
服务器采用的策略是：

1. 配置兜底 `DRR_SONG_COUNT`（默认 132）；
2. 玩家上传云存档后，按出现过的**最大曲目编号**推断真实曲数并自动抬高
   （写入 `data/meta.json` 并回写生效配置）。

`getstageclear.php` / `gethit.php` 的列表长度、以及成绩编号的合法性校验
都以这个生效值为准，因此换曲包后无需手工维护。

---

## 实现了哪些功能

### 游戏内接口（客户端主动调用）

| 接口 | 作用 | 客户端调用点 |
|---|---|---|
| `date.php` | 返回服务器日期 `YYYYMMDD`，决定每日签到奖励 | `obj_timer_Create_0.gml:3` |
| `signup.php` | 注册 / 取回账号 ID（非零） | `obj_timer_Create_0.gml:17-20` |
| `update.php` | 上报经验、总分、称号、Rating | `obj_title_Create_0.gml:34-42` |
| `cheat_test.php` | 封禁检查 | `obj_cheat_Create_0.gml:4` |
| `usecard.php` | 兑换卡换金币 | `obj_usecard_Create_0.gml:3` |
| `gethit.php` | 曲目热度榜（选择界面显示 Popularity No.x） | `obj_timer_Alarm_1.gml:3` |
| `getstageclear.php` | 每首曲目的全球通关率（任务界面显示） | `obj_timer_Alarm_2.gml:3` |
| `gethiexpnew.php` | 等级（经验值）排行榜 | `obj_timer_Alarm_3.gml:3-4` |
| `gethiscorenew.php` | 总得分排行榜 | `obj_timer_Alarm_3.gml:5-6` |
| `gethiratingnew.php` | Rating 排行榜 | `obj_timer_Alarm_3.gml:7-8` |
| `scoreupload.php` | 上传谱面成绩 | `obj_r_Create_0.gml:99-109` |
| `getscoreone.php` | 旧版单谱面成绩拉取 | `obj_download_old_Create_0.gml:32-38` |
| `uploadsavedata.php` | 云存档上传（逐条） | `obj_upload_Step_0.gml` |
| `getsavedata.php` | 云存档下载（逐条） | `obj_download_Step_0.gml` |
| `getsavedatanum.php` | 查询 DLKEY 下的条目数 | `obj_download_question_Alarm_0.gml:1` |
| `highscore.view.php` | 单谱面排行榜网页（浏览器打开） | `obj_select_Step_0.gml:335-338` |

> 旧版实现了前 5 个 + `gethit` / `getstageclear` / 三个榜单 + 云存档三件套，
> 但榜单全是编造的假数据（`Name0`/`Name1`…），云存档有丢数据缺陷。
> **`scoreupload.php` 与 `getscoreone.php` 完全没有实现** —— 这意味着
> 成绩永远进不了服务器，热度榜和通关率也只能靠假数据填充。

### 运维页面（非游戏协议）

| 路径 | 说明 |
|---|---|
| `/DanceRail/admin` | 账号列表、云存档统计、配置开关总览（只读） |
| `/DanceRail/highscore.view.php?song_id=&game_dif=` | 单谱面排行榜网页，按谱面档位分组 |
| `/` | 纯文本状态说明 |

---

## 项目结构

```
drr_server/
├── run.py                      # 生产启动入口（waitress 多线程）
├── app.py                      # 开发启动入口（Flask 调试 + 热重载）
├── requirements.txt
├── README.md                   # 本文件
├── docs/
│   ├── API.md                  # 逐接口契约（参数、应答格式、客户端解析）
│   └── ARCHITECTURE.md         # 分层设计、数据模型、扩展与维护指南
├── scripts/
│   ├── smoke_test_http.py      # 对运行中的服务器做端到端冒烟测试
│   └── manage.py               # 运维命令行（封禁、登记卡、查看状态）
├── drr_server/                 # 服务器包
│   ├── app.py                  # 应用工厂
│   ├── config.py               # Settings 配置
│   ├── storage.py              # JSON 存储 + 旧数据迁移
│   ├── models.py               # Account / ScoreRecord 数据模型
│   ├── utils/__init__.py       # 协议格式化、md5 公式、dataname 编解码
│   ├── services/               # 业务层
│   │   ├── accounts.py         #   账号
│   │   ├── scores.py           #   成绩 / 热度 / 通关率
│   │   ├── savegame.py         #   云存档
│   │   ├── cards.py            #   兑换卡
│   │   ├── ranking.py          #   排行榜
│   │   └── security.py         #   请求验签
│   ├── routes/                 # 接口层（Blueprint）
│   │   ├── _helpers.py         #   纯文本应答、参数读取、路径分发
│   │   ├── accounts.py         #   date/signup/update/cheat_test/usecard
│   │   ├── ranking.py          #   三个榜单 + gethit + getstageclear
│   │   ├── savegames.py        #   云存档三件套
│   │   ├── scores.py           #   scoreupload / getscoreone
│   │   ├── views.py            #   highscore 页面 / admin / clock.js
│   │   └── misc.py             #   根路径、旧式路径兜底分发
│   └── templates/
│       ├── highscore.html
│       └── admin.html
├── tests/                      # 测试（93 个用例）
│   ├── base.py
│   ├── test_protocol.py        #   协议格式与 md5 公式
│   ├── test_services.py        #   业务层
│   └── test_endpoints.py       #   HTTP 接口
├── data/                       # 运行期数据（自动创建）
└── original_gamecode/          # 客户端反编译源码（只读参考）
```

分层规则：**路由只做参数解析与格式拼装，业务规则一律在 `services/`，
持久化细节一律在 `storage.py`。** 这样接口层可以被 Flask 测试客户端直接测，
业务层可以脱离 HTTP 单独测。

---

## 配置项

全部通过环境变量设置（也可在代码里构造 `Settings` 传入 `create_app`）。

### 部署相关

| 变量 | 默认 | 说明 |
|---|---|---|
| `DRR_HOST` | `0.0.0.0` | 监听地址 |
| `DRR_PORT` | `80` | 监听端口，**必须能被客户端访问到** |
| `DRR_THREADS` | `16` | waitress 工作线程数（仅 `run.py`） |
| `DRR_DATA_DIR` | `./data` | 数据目录 |
| `DRR_DEBUG` | `0` | Flask 调试模式 |
| `DRR_VERBOSE_LOG` | `1` | 是否输出逐请求业务日志 |

### 玩法相关

| 变量 | 默认 | 说明 |
|---|---|---|
| `DRR_SONG_COUNT` | `132` | 曲目数量兜底值（会被存档推断值抬高） |
| `DRR_MIGRATE_LEGACY` | `1` | 启动时是否迁移旧的 `data.json` / `save.json` |

### 安全相关

| 变量 | 默认 | 说明 |
|---|---|---|
| `DRR_STRICT_AUTH` | `0` | 是否严格校验客户端的 md5 校验串 |
| `DRR_CARD_MODE` | `auto` | 兑换卡策略：`legacy` / `auto` / `whitelist` |
| `DRR_CARD_COIN_DEFAULT` | `10` | `auto` 模式给未登记数字卡的金币数 |
| `DRR_CARD_COIN_MAX` | `9999` | 单张卡金币上限 |
| `DRR_CARDS_SINGLE_USE` | `0` | 同一卡号是否只能兑换一次 |

关于 `DRR_STRICT_AUTH`：客户端的 `password` 参数其实是**参数自校验串**
（把请求参数按固定 md5 三重拼接算出来），不是登录凭据。
默认关闭是因为不同客户端版本的公式常量不同（`update.php` 里从“当日日期”
改成了固定字符串 `2017`），严格模式会误伤老版本。
需要防刷榜时再打开。

---

## 数据文件

首次启动会在 `data/` 下自动创建：

| 文件 | 内容 |
|---|---|
| `accounts.json` | `{online_id: {onlineid, password, nickname, exp, allscore, subtitle, rating, banned, ...}}` |
| `saves.json` | `{DLKEY: {dataname: value}}` —— 与旧 `save.json` 结构一致 |
| `scores.json` | `{players: {online_id: {total_plays, songs: {"难度/曲目/档位": {...}}}}}` |
| `cards.json` | `{card_id: {coins, used, uses, used_at}}` |
| `meta.json` | 服务器元信息（推断出的曲目数、迁移标记） |

全部是普通 JSON，可直接用编辑器查看/修改；写入采用
「临时文件 + 原子替换」，进程被杀不会留下半截文件。

启动时会**自动迁移**项目根目录下旧的 `data.json`（JSON 数组）与
`save.json`（`{player: {...}}`），迁移是幂等的（记录在 `meta.json`），
原文件不会被删除。

---

## 测试

```shell
# 单元测试（93 个用例，覆盖协议 / 业务 / HTTP 三层）
.venv\Scripts\python.exe -m unittest discover -s tests -t . -v

# 端到端测试：需要先启动服务器
$env:DRR_PORT='12561'; .venv\Scripts\python.exe run.py      # 终端 A
.venv\Scripts\python.exe scripts\smoke_test_http.py --base http://127.0.0.1:12561   # 终端 B
```

端到端脚本会复刻客户端的 md5 公式与 `scr_phpdata` 解析算法，
完整走一遍：注册 → 上报状态 → 上传成绩 → 上传/下载云存档 →
读取三个榜单 + 热度 + 通关率 → 打开网页 → 异常路径容错。

维护脚本（封禁、登记兑换卡、查看曲目数推断）：

```shell
.venv\Scripts\python.exe scripts\manage.py --help
```

---

## 文档索引

- **[docs/API.md](docs/API.md)** —— 每个接口的参数、应答格式、
  客户端解析代码出处、以及“为什么是这样”的说明。
  改造客户端或排查联机问题时看这份。
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** —— 分层设计、
  数据模型、并发与持久化策略、如何新增接口、常见维护操作。

---

## 相对旧版的修正清单

旧版 `app.py` 虽然能跑，但有几处会让玩家数据出问题。以下逐条列出。

### 功能性缺陷

| # | 问题 | 影响 | 现状 |
|---|---|---|---|
| 1 | `scoreupload.php` 未实现 | 成绩永远进不了服务器，热度榜/通关率只能造假 | 已实现，含参数校验与最好成绩合并 |
| 2 | `getscoreone.php` 未实现 | 旧版存档下载流程拿不到成绩 | 已实现，返回成绩百分比 |
| 3 | `highscore.view.php` 未实现 | 客户端点“查看高分榜”打开的是错误页 | 已实现，带谱面分组的 HTML 页面 |
| 4 | 云存档只在 `len(keys) == 89 + song_num*24` 时写盘 | **条目数算错一位**（正确固定字段是 90），且少传一条就永不落盘，进程退出即丢数据 | 每条立即落盘 |
| 5 | 云存档条目数口径错误 | 客户端实际发 `90 + song_num*96` 个键（`S/F/C/D` 各占一键） | 修正为 `90 + 曲数 × 24 组合 × 4 字段` |
| 6 | `getsavedata.php` 用 `save[player][dataname]` 直接索引 | 未知 DLKEY 抛 `KeyError` → 500 + HTML 错误页，客户端把 HTML 当成绩写进本地存档 | 返回安全默认值（`.F` 默认 1，其余 0） |
| 7 | 三个榜单返回编造数据（`Name0`…`Name9`） | 排行榜毫无意义 | 改读真实账号表，并按客户端要求补齐 10 行 |
| 8 | `gethit.php` 恒返回 `>1>1>2>2>3>3>4>4` | 热度榜全是假数据 | 按成绩表统计真实游玩次数，覆盖全部曲目 |
| 9 | `getstageclear.php` 返回 `(i*10)%100` | 通关率是假的 | 按“曾达 80 分以上的谱面占比”计算 |
| 10 | `signup.php` / `update.php` 都以 `password` 为键 | `update.php` 发的是每帧变化的校验串，永远匹配不到 → 每次进标题画面都往 `data.json` 追加一条重复记录 | 改为以 `onlineid` 为键，未知 ID 明确忽略 |
| 11 | `signup.php` 用 `try/except` 包住 for 循环判断“是否找到用户” | 循环体内任何异常都会导致同一用户被追加两次 | 改为显式查表 |
| 12 | `song_num` 硬编码 132，校验上界也写死 | 曲数变化后列表长度错误、真实曲目编号的成绩被静默丢弃 | 从云端存档推断并回写生效值 |
| 13 | 未匹配接口返回 `None` | Flask 抛 `TypeError` → 500 | 统一返回 `200` + `0` |
| 14 | 每个请求字符串比较 `data.startswith(...)` | 可读性差、易漏 | 按客户端真实脚本名注册路由 |
| 15 | `usecard.php` 直接 `return str(int(cardid))` | **可刷金币**：输入 `99999999` 白拿近一亿 | 三档策略，默认按面额表 + 上限保护 |
| 16 | 无效兑换卡返回 `0` | 客户端 `h == 0` 时对话框**永久卡死** | 无效卡返回负数（`-1`） |
| 17 | 无封禁能力 | `cheat_test.php` 恒返回 0 | 支持账号级封禁与全局开关文件 |

### 架构与健壮性

| # | 问题 | 现状 |
|---|---|---|
| 18 | 单文件 150 行、无注释、全局变量 + `global` 语句 | 分层为 config / storage / models / services / routes，全中文注释 |
| 19 | 数据无锁，waitress 多线程下并发写会互相覆盖 | 每个文件一把 `RLock`，“读-改-写”整体加锁 |
| 20 | 直接 `open(...,"w")` 写入 | 临时文件 + `os.replace` 原子替换 |
| 21 | JSON 损坏时服务器起不来 | 自动备份为 `.broken` 并重建 |
| 22 | 无测试 | 93 个单元测试 + 36 项端到端检查 |
| 23 | 无输入校验（曲目号、难度、成绩任意值） | 越界参数直接拒绝并记日志 |
| 24 | 日志用 `print`，无级别无时间戳 | `logging` 分级输出 |
| 25 | 导入 `app` 即产生副作用（读写文件） | 应用工厂 `create_app()`，测试可建独立实例 |
| 26 | `send_from_directory`、`add_zero` 等未使用导入/函数 | 已清理 |
| 27 | 无 `requirements.txt`、无 Markdown 文档 | 已补齐 |

### 特意保留的“原版行为”

| 行为 | 原因 |
|---|---|
| 桌面版不上榜（`os_type != os_windows`） | 客户端源码写死的判断，服务器无法也不应补偿 |
| `gethit.php` 第一组是 `(0,0)` 占位 | 客户端会强制把 `song_hit[0]` 归零 |
| 成绩可能略高于 100% | 暴击每下加 0.01，客户端显示时不裁剪 |
| 昵称限制 `[0-9A-Za-z_-]{1,16}` | 客户端输入框限制；服务器只做超集校验 |
