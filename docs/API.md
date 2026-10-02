# 接口契约文档

本文档描述服务器实现的每一个接口：**谁在什么时候调用、传什么参数、
必须返回什么**。所有结论都来自反编译客户端源码，每条都标注了出处
（路径相对 `original_gamecode/`）。

改造客户端、排查联机问题、或新增接口时，请以此文档为准。

---

## 目录

- [总体约定](#总体约定)
  - [`>` 分隔协议](#-分隔协议)
  - [为什么要永远返回 200](#为什么要永远返回-200)
  - [数字格式陷阱](#数字格式陷阱)
  - [地址与身份](#地址与身份)
- [接口逐条说明](#接口逐条说明)
- [md5 校验串公式](#md5-校验串公式)
- [云存档 dataname 规范](#云存档-dataname-规范)
- [错误与容错矩阵](#错误与容错矩阵)

---

## 总体约定

### 传输方式

**全部是 GET + query string。** 客户端 307 个 `.gml` 文件里没有任何
`http_post` 调用，也没有 cookie / 自定义请求头鉴权。
参数一律不做 URL 编码（`url_encode` 脚本存在但零调用点），
因此参数值只可能来自安全字符集：

| 参数 | 字符集 |
|---|---|
| `nickname` | `[0-9A-Za-z_-]{1,16}`（`obj_entername_Create_0.gml:4-14`） |
| `cardid` | `[0-9a-f]{8}`（`obj_option_Step_1.gml:487-514`） |
| `player`（DLKEY） | `[0-9a-f]{32}` |
| 其余 | 纯数字（可能带小数） |

### `>` 分隔协议

列表型应答由客户端脚本 `scr_phpdata` 解析
（`gml_Script_scr_phpdata.gml:1-25`）：

```gml
for (i = 0; i < string_length(argument0); i++)
{
    t = string_copy(argument0, i + 1, 1);
    if (t == ">") { ichi[cnt] = i; cnt++; }
}
ichi[cnt] = i;
if (argument1 >= cnt) r = "";
else r = string_copy(argument0, ichi[argument1] + 2,
                     ichi[argument1 + 1] - ichi[argument1] - 1);
```

语义：**返回第 `k` 个 `>` 与第 `k+1` 个 `>` 之间的子串**。
由于客户端总是从下标 0 开始取，所以：

> **应答体 = `">" + ">".join(字段)`**
> —— 必须有前导 `>`，**不能有尾部 `>`**，字段之间不能出现空档。

举例：字段 `[名字, rating, exp] = ["PLAYER", "12.30", "3600"]`
对应应答体 `>PLAYER>12.30>3600` —— 共 3 个 `>`：前导 1 个 + 字段间 2 个。
`phpdata(body, 0)` 得到 `PLAYER`，`phpdata(body, 1)` 得到 `12.30`，
`phpdata(body, 2)` 得到 `3600`。

字段本身的禁忌字符：

| 字符 | 后果 | 服务器处理 |
|---|---|---|
| `>` | 字段被切碎，后续全部错位 | `sanitize_field()` 写入端剔除 |
| `#` | 客户端 `draw_text` 的换行符，把排行榜排版顶乱 | 同上剔除 |
| `\r` `\n` | 没有任何接口会剔除原始换行 | 同上替换为空格 |
| `<br>` | 只有 `gethit.php` 会剔除（`obj_timer_Other_62.gml:29`） | 统一不输出 |

> 客户端**不会**检查 HTTP 状态码，它只看 GameMaker 传输层的 `status`
> （0 = 完成，非 0 = 传输错误）。这意味着 **404 / 500 的 HTML 错误页
> 也会被当成数据解析**。所以服务器必须永远返回 200 + 可解析的正文。

### 为什么要永远返回 200

看这段客户端代码（`obj_download_Alarm_0.gml:1-9`，云存档下载完成时）：

```gml
global.PLAYEREXP = h_playerexp;
global.ONLINE_ID  = h_onlineid;
...
```

服务端取回的每个值都会被**无条件写回本地存档**。
如果旧实现那样抛 `KeyError` 返回 500 + HTML，客户端就会把
`"<!doctype html>..."` 转成 `real()` → 0 → **玩家的经验和统计被清零**。

因此本项目的原则是：

- 任何异常（404 / 405 / 未捕获异常）都返回 `200` + `0`；
- `getsavedata.php` 对未知条目返回**语义安全默认值**（见下）。

### 数字格式陷阱

GameMaker 的 `string()` 规则是「**整数不带小数点，非整数固定两位小数**」：

| 值 | GML `string()` | Python `str()` |
|---|---|---|
| 100 | `"100"` | `"100"` |
| 100.5 | `"100.50"` | `"100.5"` |
| 12.3 | `"12.30"` | `"12.3"` |

这个规则**直接影响 md5 校验串**：客户端把 `string(...)` 的结果拼进哈希原像
再上传，服务器必须用**请求里带来的原始字符串**复刻，
不能先转成数字再格式化。

`drr_server/utils/gml_number_str()` 就是这条规则的 Python 实现，
用于输出侧（排行榜数字、写盘 JSON、日志），
而校验串一律使用请求原始字符串。

另外注意客户端的部分变量天生带小数：

| 变量 | 是否带小数 | 原因 |
|---|---|---|
| `exp` | 整数（但会格式化成 `"3600"`） | 每次 `+=` 的都是 `floor()` 结果 |
| `allscore` | **常态带两位小数** | `scr_all_score_get()` 把各曲百分比相加（`S*100/F`） |
| `rating` | 最多两位小数 | `floor(x*100)/100` |
| `score` | 常态带小数 | 见下文成绩语义 |

### 地址与身份

- 服务器地址硬编码为 `http://192.168.0.100/DanceRail/<脚本名>.php`，
  **无端口号 ⇒ 80 端口**。
- `global.ONLINE_ID` 是账号主键，由 `signup.php` 分配。
  客户端在 `obj_init_Create_0.gml:51-52` 里判断
  `if (ONLINE_ID == 0) ONLINE = 0;`，也就是 **ID 为 0 等于未登录**，
  所以 `signup.php` 绝不能返回 0。
- `password` 不是登录凭据，而是**参数自校验串**（见[公式](#md5-校验串公式)）。
  所有原像分量都在同一个 query string 里，因此它只能防篡改，不能做鉴权。
- 服务器没有任何会话机制，**任何人只要知道 `onlineid` 就能改那个账号的数据**。
  这是原版协议的限制；需要更强的保护时只能改客户端。

---

## 接口逐条说明

### 1. `date.php`

| 项目 | 内容 |
|---|---|
| 调用者 | `obj_timer_Create_0.gml:3`，游戏启动时一次 |
| 参数 | 无 |
| 应答 | 8 位整数 `YYYYMMDD`，如 `20251021` |
| 客户端解析 | `global.DATE = real(result)`（`obj_timer_Other_62.gml:1-7`） |

用途：与本地 INI 里存的 `sign`（默认 `20180101`）比较，
`d < global.DATE` 时发放每日签到奖励 `COIN += 10`
（`obj_title_Step_0.gml:71-82`）。

实现说明：用服务器本机时间。若服务器与玩家时区不同，
管理页与排行榜页面会用 `clock.js` 在**浏览器侧**重算日期
（与客户端 `obj_timer_Create_0` 的算法完全一致），
避免“服务器在 UTC、玩家在 UTC+8，签到差一天”。

### 2. `signup.php`

| 项目 | 内容 |
|---|---|
| 调用者 | `obj_timer_Create_0.gml:17-20`，启动时若 `!global.ONLINE` |
| 参数 | `nickname`、`password` |
| 应答 | 账号数字 ID（**非零**） |
| 客户端解析 | `global.ONLINE = 1; global.ONLINE_ID = real(result);`（`obj_timer_Other_62.gml:9-20`） |

语义：`password` 只由昵称派生 ⇒ **等价于“按昵称取回或创建账号”**。
因此必须幂等：同一昵称重复调用要拿到同一个 ID，否则玩家每次启动都会换账号。

实现说明：

- 以 `password` 为主键查表，找不到再按昵称查，最后才新建；
- 昵称可以随时改（客户端 `rEnterName`），但改完不会重新 signup
  （`ONLINE` 已经是 1），新昵称由 `update.php` 推上来；
- 服务器返回的 ID 从 1 开始递增，且避开已用的数字 ID。

### 3. `update.php`

| 项目 | 内容 |
|---|---|
| 调用者 | `obj_title_Create_0.gml:32-43`，**每次进入标题画面** |
| 触发条件 | `os_is_network_connected() && ONLINE_ID != 0 && os_type != os_windows` |
| 参数 | `onlineid, nickname, exp, allscore, subtitle, rating, password` |
| 应答 | 任意（客户端不解析），实现返回 `0` |

参数语义：

| 参数 | 变量 | 说明 |
|---|---|---|
| `onlineid` | `global.ONLINE_ID` | 账号主键 |
| `nickname` | `global.PLAYERNAME` | 昵称 |
| `exp` | `global.PLAYEREXP` | 经验（等级 = `floor(sqrt(exp))`） |
| `allscore` | `global.ALLSCORE` | 总得分，**带小数** |
| `subtitle` | `global.MISSION_SHINDO` | 称号/任务进度计数（不是文本） |
| `rating` | `global.RATING` | 实力值 |

实现说明：

- **必须以 `onlineid` 为键**。旧实现按 `password` 查——
  而这里的 `password` 是每帧变化的校验串，永远匹配不上，
  于是每次进标题画面都追加一条重复账号。
- 经验与总分**只增不减**：客户端重装或回档会把本地值刷低，
  若照单全收会让榜单数字来回跳。
- Rating 允许下降（成绩变差、榜单重算）。
- 未知 `onlineid` 明确忽略（不新建记录）。

### 4. `cheat_test.php`

| 项目 | 内容 |
|---|---|
| 调用者 | `obj_cheat_Create_0.gml:4` |
| 参数 | 无（本实现额外接受可选 `onlineid`） |
| 应答 | `0` = 正常；`>= 1` = 封禁 |

客户端解析（`obj_cheat_Other_62.gml:1-20`）：
`h = real(result); if (h >= 1 && room != rCheat)` → 跳转到封禁画面。

实现说明：客户端**不传身份**，所以原版只能在离线侧用本地 `ban` 文件封禁。
本服务器额外支持两种服务端封禁：

1. `data/accounts.json` 里 `"banned": true`（可用 `scripts/manage.py` 设置）；
   配合带 `onlineid` 参数调用时精确生效；
2. 配置 `Settings.ban_file` 指向的文件存在 ⇒ 全服封禁开关。

### 5. `usecard.php`

| 项目 | 内容 |
|---|---|
| 调用者 | `obj_usecard_Create_0.gml:3` |
| 参数 | `cardid`（8 位十六进制） |
| 应答 | **有符号十进制整数**：`> 0` 金币数，`< 0` 卡不可用 |

客户端解析（`obj_usecard_Step_0.gml:1-13`）：

```gml
h = real(result);
if (h > 0)      global.COIN += h;   // 提示 "Get Coin : N"
else if (h < 0) /* 提示“此卡不可用” */
else            /* 什么都不做 —— 对话框永久卡住 */
```

> **绝不可返回 `0`。** 失败必须返回负数。

实现说明（三档策略，`DRR_CARD_MODE`）：

| 模式 | 行为 |
|---|---|
| `legacy` | 复刻旧版「卡号即面额」，受 `card_coin_max` 上限保护 |
| `auto`（默认） | 登记卡按登记面额；未登记数字卡发 `card_coin_default` 并自动登记 |
| `whitelist` | 只认 `data/cards.json` 里登记过的卡 |

### 6. `gethit.php`

| 项目 | 内容 |
|---|---|
| 调用者 | `obj_timer_Alarm_1.gml:3`（`alarm[1] = 30` 帧后触发一次） |
| 参数 | `song_num`（客户端自己的曲目数） |
| 应答 | `>曲目ID>次数>曲目ID>次数...`，**`>` 数量必须是偶数** |

客户端解析（`obj_timer_Other_62.gml:22-74`）：

```gml
hit = string_replace_all(result, "<br>", "");
for (i = 0; i < (string_count(">", hit) / 2); i++)
{
    song_hid[i] = scr_phpdata(hit, (i * 2) + 0);   // 曲目 ID
    song_hit[i] = scr_phpdata(hit, (i * 2) + 1);   // 次数
}
song_hit[0] = 0;                                    // ← 第一组被强制归零
```

然后按次数做降序冒泡排序，给每首曲子标 `song_j = 排名`（`:39-65`），
选择界面显示 `"Popularity No." + (排名+1)`（`obj_select_Draw_0.gml:45-55`）。

实现要点：

1. **第一组是占位符**（客户端会把它归零），所以输出 `(0, 0)` 打头；
2. 其后是**全部**曲目（`song_id_base .. song_id_base + 曲数 - 1`），
   服务器不排序 —— 客户端会自己排；
3. 服务器用**自己的**曲目数，与参数 `song_num` 无关
   （参数只记日志，便于发现客户端曲包与服务器推断值不一致）。

### 7. `getstageclear.php`

| 项目 | 内容 |
|---|---|
| 调用者 | `obj_timer_Alarm_2.gml:3`（`alarm[2] = 200` 帧后一次） |
| 参数 | 无 |
| 应答 | `>通关率>通关率...`，**不带百分号**，一个曲目一个值 |

客户端解析：`stagep[i] = scr_phpdata(t, i)`（`obj_timer_Other_62.gml:103-114`），
任务界面显示 `"CLEAR RATE : " + stagep[select] + "%"`（`obj_mission_Draw_0.gml:23-26`）。

实现口径：某曲目的**全球通关率** = 所有玩家在该曲目上曾达到 80 分
（`score_to_rank` 的通关线，`gml_Script_score_to_rank.gml:11-12`）的
`(玩家, 难度, 谱面档位)` 组合数 ÷ 该曲目被游玩过的组合数 × 100。
无记录的曲目返回 `0`。

> 注意客户端是按曲目**顺序**取值，但用**任务索引**去读，
> 所以服务器输出顺序必须与 `songlist.drf` 一致，
> 也就是按曲目编号递增。列表长度取服务器生效曲目数。

### 8-10. 三个排行榜

| 接口 | 调用者 | 每行字段顺序 |
|---|---|---|
| `gethiexpnew.php` | `obj_timer_Alarm_3.gml:3-4` | 名字, **rating**, **exp** |
| `gethiscorenew.php` | `obj_timer_Alarm_3.gml:5-6` | 名字, **rating**, **allscore** |
| `gethiratingnew.php` | `obj_timer_Alarm_3.gml:7-8` | 名字, **exp**, **rating** |

> ⚠️ 第三张榜的前两个数值字段顺序与另外两张**相反**，
> 这是客户端显示的列顺序决定的（`obj_title_Draw_0.gml:211-212`），
> 照抄解析代码即可，不要“顺手统一”。

应答要求：

- 固定 **10 行 × 3 字段 = 30 个 `>`**；
- 不足 10 行必须补空（空名字 + `0`）；
  客户端 `for (i = 0; i < 10; i++)` 会把缺失行渲染成 `Lv.0` / `0.00`；
- 不能包含 `<br>` 或换行（这三个接口不做任何替换）；
- 客户端用 `real()` 解析数字字段、用原始字符串显示名字。

客户端的解析入口（`obj_timer_Step_0.gml:28-62`）：

```gml
best_name1[i]  = scr_phpdata(bestexp, (i * 3) + 0);
best_rating1[i]= real(scr_phpdata(bestexp, (i * 3) + 1));
best_exp1[i]   = real(scr_phpdata(bestexp, (i * 3) + 2));
```

显示（`obj_title_Draw_0.gml`）：

| 榜 | 展示方式 |
|---|---|
| 经验榜 | `名字(Rt.12.30)` + `Lv.60`（等级由 `floor(sqrt(exp))` 算出） |
| 总分榜 | `名字(Rt.12.30)` + 原始总得分 |
| Rating 榜 | `名字(Lv.60)` + 着色的 Rating 数字 |

排序依据：分别按 `exp`、`allscore`、`rating` 降序；封禁账号排除。

### 11. `scoreupload.php`

| 项目 | 内容 |
|---|---|
| 调用者 | `obj_r_Create_0.gml:99-109`，结算界面 |
| 触发条件 | `os_is_network_connected() && ONLINE_ID != 0 && os_type != os_windows`，且在外层 `if (!global.AUTOPLAY)` 内 |
| 参数 | `id, song_id, game_dif, song_hard, deathmode, date, time, score, password` |
| 应答 | 任意（客户端不解析），实现返回 `0` |

参数细节：

| 参数 | 取值 | 说明 |
|---|---|---|
| `id` | `global.ONLINE_ID` | 账号主键（注意参数名是 `id`） |
| `song_id` | 10000+ | 曲目编号 |
| `game_dif` | 0..3 | 游戏模式：VERY EASY / NORMAL / HARD / HELL |
| `song_hard` | 0..4 | 谱面档位：Easy/Normal/Hard/Ultra/Master |
| `deathmode` | 0/1 | 是否死亡模式 |
| `date` | `YYYYMMDD` | 零填充 |
| `time` | `H` + 补零 MM + 补零 SS | **小时不补零**，所以长度是 5 或 6 |
| `score` | 百分比 | 见下 |

**上传的是历史最好成绩，不是本次成绩**：

```gml
score = string(max(global.SCORE, oldSCORE));
```

其中 `oldSCORE` 是从本地 `.S/.F/.C` 三元组重算出来的旧纪录。
因此本接口语义上是幂等的「最好成绩更新」，服务器按
`best = max(已有, 本次)` 处理，重复上传不会改变结果。

成绩数值语义（`obj_game_Step_0.gml:494`）：

```gml
global.SCORE = (((global.HIT + (global.POOR * 0.5)) / global.ONPU_CNT) * 100)
             + (global.CRITICAL * 0.01);
```

即「命中率百分比 + 暴击加成」。暴击每下加 0.01，所以**成绩可能略高于 100**
（全暴击时约 `100 + 音符数 × 0.01`）。服务器按 0..200 做合法性校验，
不裁剪上限，与客户端显示保持一致。

实现说明：

- 校验曲目编号在 `[song_id_base, song_id_base + 曲数 - 1]` 内、
  难度 0..3、档位 0..5、成绩 0..200，越界直接拒绝并记日志；
- 未知 `onlineid` 忽略（旧实现会写进一份没有账号归属的“幽灵成绩”）；
- 开启 `DRR_STRICT_AUTH` 时校验 `password`，不通过则拒绝写入。

### 12. `getscoreone.php`

| 项目 | 内容 |
|---|---|
| 调用者 | `obj_download_old_Create_0.gml:32-38` |
| 状态 | **旧版遗留流程**，客户端里 `global.ONLINE_IDtmp` 恒为 0，实际不可达 |
| 参数 | `user_id, nickname, song_id, game_dif, song_hard` |
| 应答 | 成绩百分比（裸数字，如 `97.50`） |

客户端处理（`obj_download_old_Other_62.gml:9-17`）：

```gml
r0[i, j] = result;
if (real(r0) > song_hiscore0[i, j])     // 本地成绩 = S*100/F
{
    ini_write_real("score.0", "<song>.<hard>.S", real(r0));
    ini_write_real("score.0", "<song>.<hard>.F", 100);   // ← 写死 100
}
```

因为客户端把 `F` 写死成 100，而本地成绩按 `S*100/F` 还原，
所以这里**必须返回百分比**而不是命中数。
没有记录时返回 `0.00`，客户端的 `>` 比较不成立，不会改动本地数据。

> 云存档里的 `.S` 存的是**命中数**（`HIT + POOR*0.5`）、`.F` 是音符总数，
> 那是另一套表征，两者不要混淆。服务器内部统一按百分比保存成绩。

### 13-15. 云存档三件套

#### `uploadsavedata.php`

| 项目 | 内容 |
|---|---|
| 参数 | `player`(DLKEY), `dataname`, `data` |
| 应答 | **必须逐字返回 `1`** |

客户端完成判定（`obj_upload_Step_0.gml:100-111`）：

```gml
cnt = 0;
for (i = 0; i < song_num; i++) cnt += s[i];
if (cnt == song_num && !endflag) { show_message("UPLOAD OK"); endflag = 1; }
```

`cnt` 累加的是每个曲目**最后一次请求**的响应值，判定用的是**精确相等**。
所以：

- 返回 `0` ⇒ 上传永远不结束，卡在“上传中”界面；
- 返回 `> 1` ⇒ 累加值超出，同样不结束。

服务器对**每一次**上传都返回 `1`。

#### `getsavedata.php`

| 项目 | 内容 |
|---|---|
| 参数 | `player`(DLKEY), `dataname` |
| 应答 | 该条目的原始值（裸数字字符串） |

**必须始终返回 200 + 可解析的数字。** 客户端在下载完成时会无条件把
取回的值写进本地存档（`obj_download_Alarm_0.gml:1-9`），
返回 HTML 会直接清空玩家的经验与统计数据。

未知 DLKEY / 未知条目返回**语义默认值**：

| 条目类型 | 默认值 | 原因 |
|---|---|---|
| `...F`（音符总数） | `1` | 客户端本地默认也是 1，0 会导致 `S*100/F` 除零 |
| 其它 | `0` | 与 `real("")` 的结果一致，不会误增数值 |

DLKEY 按**大小写不敏感**处理（客户端输入框只接受小写十六进制，
但从剪贴板粘贴可能是大写）。

#### `getsavedatanum.php`

| 项目 | 内容 |
|---|---|
| 参数 | `player`(DLKEY) |
| 应答 | 该 DLKEY 下的条目数（整数） |

客户端用它判断云端是否有存档（`g < 1` 时禁用“确定”按钮，
`g == 0` 直接卡住界面）。未知 DLKEY 返回 `0`（**不能 404**）。

### 16. `highscore.view.php`

| 项目 | 内容 |
|---|---|
| 调用者 | `obj_select_Step_0.gml:335-338`，通过 `url_open()` 打开**系统浏览器** |
| 参数 | `song_id`、`game_dif`（**没有 `song_hard`**） |
| 应答 | 人类可读 HTML，客户端不解析 |

因为客户端不传 `song_hard`，页面按**该难度下所有谱面档位**分组展示，
每档位一个榜单（玩家一次能看到全部信息）。
页面还内嵌 `clock.js` 显示服务器日期，并提供通往 `/DanceRail/admin` 的链接。

历史依据：客户端里保留了 `song<game_dif>_best{1..5}{name,score,death}` 这类
从未被 HTTP 填充过的表（`obj_timer_Create_0.gml:57-165`），
说明早期接口返回的是「每曲每难度前 5 名（昵称、成绩、死亡模式）」，
本页面的展示形式即照此还原。

---

## md5 校验串公式

所有公式都是**小写十六进制 md5**，原像里的值就是 query string 里的原始字符串。

```python
import hashlib
H = lambda s: hashlib.md5(s.encode("utf-8")).hexdigest()

# signup.php           出处: obj_timer_Create_0.gml:19
def signup_password(nickname):
    return H(H(nickname + nickname)
           + H(nickname + "sora")
           + H(nickname + "game"))

# update.php           出处: obj_title_Create_0.gml:41
def update_password(onlineid, nickname, exp, allscore, subtitle, rating):
    return H(H(onlineid + nickname + "sora")
           + H(exp + allscore + "game")
           + H(subtitle + rating + "2017"))

# scoreupload.php      出处: obj_r_Create_0.gml:108
def scoreupload_password(id, song_id, game_dif, song_hard,
                         deathmode, date, time, score):
    return H(H(id + song_id + game_dif + "sora")
           + H(song_hard + deathmode + date + "game")
           + H(time + score + "password"))

# DLKEY（客户端自己生成，服务器只存储与匹配）  出处: obj_upload_Create_0.gml:3
def dlkey(online_id, playername, hour, minute, second):
    return H(f"{online_id}{playername}{hour + minute + second}")
```

注意事项：

- 公式里的数值分量必须用**客户端发来的原始字符串**，
  因为 GML `string()` 的格式规则与 Python `str()` 不同（见[上文](#数字格式陷阱)）；
- `update.php` 用的是固定常量 `"2017"`（不是当日日期）——
  旧版客户端曾是日期，因此本服务器默认**宽松模式**，
  只记日志不拒绝，避免误伤老版本；
- DLKEY 的低熵种子 `hour + minute + second`（三个整数**相加**，如 19+5+3=27）
  说明它并不安全，只是为了让每次上传产生不同的存档键。服务器只做长度与字符集校验。

---

## 云存档 dataname 规范

客户端在 `obj_upload_Step_0.gml:83-92` 拼接：

```gml
dataname = string(k) + string(song_id[i]) + string(j) + "S"/"F"/"C"/"D"
```

| 组成 | 取值 | 说明 |
|---|---|---|
| `k` | 0..3 | 游戏模式（难度） |
| `song_id` | 10000.. | 曲目编号（5 位） |
| `j` | 0..5 | 谱面档位 |
| 字段 | `S`/`F`/`C`/`D` | 命中数 / 音符总数 / 暴击数 / 死亡模式标记 |

例（与仓库真实 `save.json` 一致）：

| dataname | 解析结果 |
|---|---|
| `0101383S` | 难度 0、曲目 10138、档位 3、字段 S |
| `3101070F` | 难度 3、曲目 10107、档位 0、字段 F |
| `1101365S` | 难度 1、曲目 10136、档位 5、字段 S |

固定条目（与曲目无关，共 **90** 条）：

| 条目 | 数量 |
|---|---|
| `playerexp` `onlineid` `mission` `starttime` `playtimes` `cleartimes` `allcombo` `allmiss` `alltime` | 9 |
| `speedtimes0` .. `speedtimes30` | 31 |
| `hardtimes0` .. `hardtimes5` | 6 |
| `diftimes0` .. `diftimes3` | 4 |
| `rating300` .. `rating3029` | 30 |
| `rating100` .. `rating109` | 10 |

单曲条目数 = `难度数 × 档位数 × 字段数` = 4 × 6 × 4 = **96**。

因此完整存档的条目总数 = `90 + 96 × 曲目数`
（132 首曲子 = 12762 条，对应同样数量的 HTTP 请求）。

> 旧版 `app.py` 用的是 `89 + song_num*24`：`89` 少算一条固定字段，
> `24` 又漏掉了 `S/F/C/D` 四个字段是四条独立请求，所以那个等式**永远不成立**，
> 云存档于是永远不会被写盘。

---

## 错误与容错矩阵

| 情况 | 旧版行为 | 现行为 | 理由 |
|---|---|---|---|
| 未知接口路径 | 返回 `None` → Flask `TypeError` → 500 + HTML | `200` + `0` | HTML 会被当数据解析 |
| 未知 DLKEY 下载 | `KeyError` → 500 + HTML | `200` + 语义默认值 | 避免清空玩家存档 |
| JSON 数据文件损坏 | 启动崩溃 | 备份为 `.broken` 后重建 | 保证服务可用 |
| 并发写同一文件 | 互相覆盖 | 每文件加锁 + 原子替换 | waitress 多线程 |
| 成绩参数越界 | 照单全收 | 拒绝 + 日志 | 防止脏数据污染榜单 |
| 未知 `onlineid` 上传成绩 | 写入无主记录 | 忽略 | 榜单干净 |
| 未知 `onlineid` 更新账号 | 追加重复账号 | 忽略 | 榜单干净 |
| 无效兑换卡 | 返回 `0`（客户端卡死） | 返回 `-1` | 客户端只在 `< 0` 时提示失败 |
| 兑换卡超大金额 | 白送金币 | 上限保护 | 见 `DRR_CARD_COIN_MAX` |
