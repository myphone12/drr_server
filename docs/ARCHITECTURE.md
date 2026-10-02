# 架构与维护指南

本文档面向要改这个服务器的人：说明分层设计、数据模型、并发与持久化策略、
以及常见维护操作的步骤。

---

## 目录

- [整体分层](#整体分层)
- [一次请求的完整路径](#一次请求的完整路径)
- [数据模型](#数据模型)
- [并发与持久化](#并发与持久化)
- [错误处理策略](#错误处理策略)
- [如何新增一个接口](#如何新增一个接口)
- [常见维护操作](#常见维护操作)
- [测试策略](#测试策略)
- [已知限制与后续可做的事](#已知限制与后续可做的事)

---

## 整体分层

```
            客户端 (GameMaker)
                   │  GET /DanceRail/xxx.php?...
                   ▼
    ┌──────────────────────────────────────┐
    │ routes/            接口层             │  ← 只做参数解析与格式拼装
    │  accounts / ranking / savegames /    │
    │  scores / views / misc               │
    │  _helpers: text_response / arg / ... │
    └──────────────────────────────────────┘
                   │  调用服务方法
                   ▼
    ┌──────────────────────────────────────┐
    │ services/          业务层             │  ← 规则、校验、统计口径
    │  accounts  scores  savegame          │
    │  cards     ranking  security         │
    └──────────────────────────────────────┘
                   │  读写模型
                   ▼
    ┌──────────────────────────────────────┐
    │ models.py / storage.py               │  ← 数据结构与持久化
    │  Account / ScoreRecord               │
    │  JsonFileStore / Database            │
    └──────────────────────────────────────┘
                   │
                   ▼
              data/*.json
```

依赖方向严格单向：`routes → services → storage`。
`utils` 是纯函数库，任何层都可以用，它自身不依赖其它模块。

### 为什么这样分

| 层 | 只关心 | 换掉它意味着 |
|---|---|---|
| `routes` | HTTP 细节、参数名、应答字符串格式 | 改协议（例如换成 JSON API）不用动业务逻辑 |
| `services` | 游戏规则、统计口径、校验 | 改玩法数值不用动接口 |
| `storage` | 文件读写、锁、序列化 | 换 SQLite/Redis 不用动业务逻辑 |
| `utils` | 客户端格式的复刻 | 客户端改公式时只改这里 |

具体收益（在本次重构中实际体现出来的）：

- 协议格式（`>` 分隔、GML 数字格式、md5 公式、dataname 编解码）
  全部集中在 `utils`，写测试时可以直接用客户端的解析算法验证；
- 业务规则（经验只增不减、通关线 80 分、最好成绩合并）
  集中在 `services`，可以脱离 HTTP 单独测试；
- 曲目数推断这种“跨文件的状态”由 `storage.Database` 统一维护，
  不会散落在各个路由里。

### 依赖注入方式

服务实例挂在 `app.extensions` 上，而不是模块级全局变量：

```python
app.extensions["drr_accounts"] = AccountService(db)
app.extensions["drr_scores"]   = ScoreService(db)
...
```

路由里这样取：

```python
def _accounts() -> AccountService:
    return current_app.extensions["drr_accounts"]
```

好处：测试可以并发创建多个指向不同临时目录的应用实例，互不干扰
（旧版用模块全局 `save` / `song_num`，测试之间必然串味）。

---

## 一次请求的完整路径

以 `scoreupload.php` 为例：

```
1. 客户端 obj_r_Create_0.gml:99  拼出 URL（9 个参数 + password）
        ↓
2. Flask 路由 routes/scores.py::scoreupload_php
   - 可选：services/security.verify_score_upload 验签
        ↓
3. 校验账号存在
   services/accounts.AccountService.get(online_id)
        ↓
4. 写入成绩
   services/scores.ScoreService.record_upload(...)
   - 校验曲目/难度/档位/成绩范围
   - 构造 ScoreRecord（models.py）
   - 在存储层锁内更新 scores.json：
       best = max(已有, 本次)
       plays += 1
       history 保留最近 50 条
        ↓
5. 返回 "0"（客户端不解析，但约定 0 = 成功）
```

其中第 4 步的 `best = max(...)` 是刻意的幂等设计：
客户端上传的本来就是历史最好成绩（`max(global.SCORE, oldSCORE)`），
所以收到更低的值时不能覆盖。

---

## 数据模型

### `Account`（`models.py`）

与客户端变量一一对应：

| 模型字段 | 客户端变量 | 类型 | 备注 |
|---|---|---|---|
| `online_id` | `global.ONLINE_ID` | `str` | 主键，**不能是 `"0"`** |
| `password` | —— | `str` | 由昵称派生的 md5，用于找回账号 |
| `nickname` | `global.PLAYERNAME` | `str` | 排行榜展示 |
| `exp` | `global.PLAYEREXP` | `int` | 等级 = `floor(sqrt(exp))` |
| `all_score` | `global.ALLSCORE` | `float` | **带小数**，别用 int |
| `subtitle` | `global.MISSION_SHINDO` | `int` | 称号进度计数 |
| `rating` | `global.RATING` | `float` | 两位小数 |
| `banned` | —— | `bool` | 服务器侧封禁 |

`all_score` 用 `float` 是有原因的：客户端把它当浮点累加
（`S*100/F`），经 GML `string()` 变成 `"1200.50"` 上传；
按整数存会抹掉小数，与玩家本机显示不一致。

### 成绩表结构（`data/scores.json`）

```json
{
  "version": 2,
  "players": {
    "1": {
      "total_plays": 42,
      "songs": {
        "2/10138/3": {
          "best": "97.50",
          "best_death_mode": 1,
          "death_mode": 1,
          "plays": 7,
          "last_played": "2026-10-02T18:48:00+08:00",
          "history": [ { "score": "97.50", "...": "..." } ]
        }
      }
    }
  }
}
```

键 `难度/曲目/档位`（带分隔符是刻意的）比客户端的
`0101383S` 式无分隔拼接更易读，也避免解析歧义。
`history` 上限 50 条，用于将来做“最近成绩”类功能与审计。

### 云存档结构（`data/saves.json`）

与客户端上传的键值一一对应，**不做任何转换**：

```json
{
  "3e9c42f461ce92e304ac3903d4378950": {
    "playerexp": "3600",
    "onlineid": "1",
    "0101383S": "95.5",
    "0101383F": "1200"
  }
}
```

刻意保留原始字符串：客户端上传什么就存什么，回读时逐字返回，
最不容易出问题（例如 `"1.00"` 不会被改写成 `"1.0"`）。

---

## 并发与持久化

### 锁的粒度

`JsonFileStore` 每个文件一把 `threading.RLock`，
把「读 → 改 → 写」整体包在锁里：

```python
def update(self, mutator):
    with self._lock:
        data = self._load_locked()
        mutator(data)
        self._write_locked(data)
        return data
```

用 `RLock` 而不是 `Lock`，因为 `update()` 内部会再次调用
`read()` / `write()`（同一线程重入）。

之所以不做跨文件事务：客户端的每个接口只操作**单一**数据集，
不存在跨文件一致性需求，把锁粒度压到最小可以让上传突发流量
（96 并发请求）少互相等待。

### 原子写入

```python
temp = self.path.with_suffix(self.path.suffix + ".tmp")
temp.write_text(text, encoding="utf-8")
os.replace(temp, self.path)          # 原子替换
```

即使进程在写盘途中被杀，磁盘上要么是旧文件、要么是新文件，
不会出现半截 JSON。旧版直接 `open(..., "w")`，一崩就是损坏文件。

### 性能考量

- **内存缓存**：`JsonFileStore` 首次读取后把整份数据放在内存里，
  之后 `read()` 直接返回缓存对象，不重复解析 JSON。
- **写入放大**：每次 `update()` 都会重写整个文件。对当前量级
  （几千账号 / 十几万存档条目）没有问题；如果存档规模上到百万级，
  应换成 SQLite。`services` 层不直接接触文件，
  所以替换存储实现不会波及业务逻辑。
- **曲目数推断的开销**：`sync_song_count()` 在生成榜单时会被调用，
  它只读一次内存缓存 + 一次 `meta.json`，开销可忽略。

---

## 错误处理策略

核心约束（详见 [API.md](API.md#为什么要永远返回-200)）：
**客户端不看 HTTP 状态码，只看传输层 status，所以任何非 2xx 的 HTML
错误页都会被当成数据解析**，最严重的后果是把玩家的云存档写坏。

因此 `app.py` 里注册了统一错误处理：

| 触发 | 处理 |
|---|---|
| 404 未匹配路由 | `200` + `0`（纯文本） |
| 405 方法不允许 | `200` + `0` |
| 未捕获异常 | 记录完整堆栈，返回 `200` + `0` |

路由内部的“正常失败”也统一返回可解析值：

| 接口 | 失败时返回 | 客户端表现 |
|---|---|---|
| `signup.php` | 仍返回有效 ID | 极少失败 |
| `update.php` | `0` | 无感知（不解析） |
| `scoreupload.php` | `0` | 无感知（不解析） |
| `getsavedata.php` | 语义默认值 | 不会改动本地数据 |
| `getsavedatanum.php` | `0` | 显示“未找到存档” |
| `usecard.php` | `-1` | 显示“此卡不可用” |
| 未知接口 | `0` | 失败但不崩 |

---

## 如何新增一个接口

假设客户端新增了一个 `getsonginfo.php`：

1. **确认契约**：在 `original_gamecode/` 里 grep 该脚本名，
   找到发请求的对象与 `async_load` 处理处，确定参数名与应答格式。
2. **写业务逻辑**：在 `drr_server/services/` 里加方法（或新建服务模块），
   并确保它不依赖 Flask。
3. **加路由**：在 `drr_server/routes/` 里加视图，使用
   `_helpers.text_response()` 返回纯文本、`arg()` 读参数。
4. **注册蓝图**：如果新建了蓝图，加入 `routes/__init__.py` 的
   `ALL_BLUEPRINTS`（`/DanceRail/<path:data>` 兜底分发会自动发现新路由，
   无需额外登记）。
5. **写测试**：
   - `tests/test_services.py` 测业务规则；
   - `tests/test_endpoints.py` 测 HTTP 形态与应答格式；
   - 若涉及 `>` 协议，用 `utils.phpdata()` 反向解析验证字段顺序。
6. **更新文档**：在 `docs/API.md` 里加一节，注明客户端出处。

---

## 常见维护操作

### 封禁 / 解封玩家

```shell
.venv\Scripts\python.exe scripts\manage.py ban 3
.venv\Scripts\python.exe scripts\manage.py unban 3
```

也可以直接编辑 `data/accounts.json`，把对应账号的 `"banned"` 改成 `true`。

> 注意：客户端只在 `update.php` / `scoreupload.php` 上传数据，
> 封禁的效果是**该账号从排行榜消失**。
> 要让客户端自己跳到封禁画面，需要带 `onlineid` 调用
> `cheat_test.php`（原版客户端不传，所以只能靠本地 `ban` 文件）。

### 登记兑换卡

```shell
.venv\Scripts\python.exe scripts\manage.py card 12345678 500
```

把 `DRR_CARD_MODE` 设为 `whitelist` 后，只有登记过的卡才能兑换。

### 查看服务器状态

```shell
.venv\Scripts\python.exe scripts\manage.py status
```

或直接打开 <http://127.0.0.1/DanceRail/admin>。

### 调整曲目数量

一般情况下**不用手动调**：玩家上传云存档后服务器会自动推断。
需要手工覆盖时：

```shell
set DRR_SONG_COUNT=150
```

### 修改数据后让服务器重新读取

服务器在内存里缓存了 JSON 内容。直接编辑文件后需要**重启服务**
才会生效。（如果想支持热加载，可以在 `JsonFileStore` 上加
`mtime` 检查后调用 `reload()`。）

### 备份

`data/` 目录就是全部状态，直接整个复制即可。
由于写入是原子替换，运行中复制也不会拿到半截文件。

---

## 测试策略

三层测试，各自关注点不同：

| 文件 | 关注 | 典型用例 |
|---|---|---|
| `tests/test_protocol.py` | 协议与公式 | `>` 格式能被 `phpdata` 还原；md5 公式与客户端一致；dataname 编解码能解析真实 `save.json` 的键 |
| `tests/test_services.py` | 业务规则 | 经验只增不减；成绩越界被拒；通关线 80 分；排行榜补足 10 行 |
| `tests/test_endpoints.py` | HTTP 形态 | 每个接口的应答字符串；未知路径返回 `0` 而非 500；旧数据迁移幂等 |

设计要点：

- **用客户端的算法验证服务器**：`utils.phpdata()` 是
  `scr_phpdata` 的 Python 等价实现，测试里用它去解析服务器输出，
  而不是靠肉眼比对字符串。字段顺序写反会被立刻抓到。
- **用真实的存档键**：`test_protocol.py` 直接使用仓库
  `save.json` 里出现过的键（`0101383S` 等），确保解析器对真实数据有效。
- **每个测试一个临时数据目录**：`tests/base.py` 的 `AppTestCase`
  构造独立 `Settings`，测试之间零共享。

运行：

```shell
.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

端到端验证（需要服务器在跑）：

```shell
.venv\Scripts\python.exe scripts\smoke_test_http.py --base http://127.0.0.1:12561
```

---

## 已知限制与后续可做的事

| 限制 | 说明 | 可行改进 |
|---|---|---|
| 无鉴权 | 协议里没有会话/token，知道 `onlineid` 就能改数据 | 需要改客户端（加入签名或 token） |
| 曲目数靠推断 | 服务器读不到 `songlist.drf` | 让运维把 `songlist.drf` 放到 `data/` 下，服务器解析 `<id>` 列 |
| Rating 无法独立验算 | 需要 `songlist.drf` 里的谱面等级（`SONG_RANK`）才能复现公式 | 同上；拿到等级表后可在服务端重算 Rating 做交叉校验 |
| JSON 全量写入 | 每次更新重写整个文件 | 规模上百万级时改 SQLite（只需替换 `storage.py`） |
| 单进程内锁 | 多进程部署（如 gunicorn 多 worker）下锁失效 | 用文件锁或换 SQLite |
| `getscoreone.php` 语义推断 | 客户端该流程不可达（`ONLINE_IDtmp` 恒 0），返回值的语义是从客户端写回逻辑反推的 | 若将来启用旧流程，需实机验证 |
| 分数上限 200 | 客户端理论上限约 `100 + 音符数 × 0.01` | 若出现极端谱面可调 `MAX_REASONABLE_SCORE` |
