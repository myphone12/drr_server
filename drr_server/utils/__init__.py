"""与游戏客户端协议强相关的纯函数集合。

这里集中放置“格式转换”逻辑，避免它们散落在路由里。
最重要的两个约定：

1. **``>`` 分隔协议** —— 客户端脚本 ``scr_phpdata(response, i)`` 的语义是
   “返回第 i 个 ``>`` 与第 i+1 个 ``>`` 之间的子串”。
   因为客户端总是从下标 0 开始取值，所以**应答体必须以 ``>`` 开头**，
   否则第一个字段会丢失。例如 4 组“曲目 ID / 数值”应写成::

       >10001>12>10002>7

2. **md5 校验串** —— 客户端用三重 md5 拼接生成 ``password``，
   服务器若要验签必须复刻同样的拼接顺序与常量。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence

# --------------------------------------------------------------------------
# ``>`` 分隔协议
# --------------------------------------------------------------------------

FIELD_SEPARATOR = ">"


def format_php_fields(values: Iterable[object]) -> str:
    """把若干字段拼成客户端 ``scr_phpdata`` 能解析的应答体。

    返回值始终以 ``>`` 开头，字段之间也用 ``>`` 分隔::

        >>> format_php_fields(["10001", 12, "10002", 7])
        '>10001>12>10002>7'

    客户端 ``scr_phpdata`` 的实现（``original_gamecode/gml_Script_scr_phpdata.gml``）
    会把所有 ``>`` 的位置记录到 ``ichi[]``，然后取
    ``ichi[i] + 2`` 起、长度 ``ichi[i+1] - ichi[i] - 1`` 的子串。
    形如 ``>A>B`` 的字符串下标为 ``ichi = [0, 2]``，
    于是 ``i=0`` 得到 ``A``、``i=1`` 得到 ``B``，与预期一致。
    """
    body = FIELD_SEPARATOR.join(str(v) for v in values)
    return FIELD_SEPARATOR + body if body else FIELD_SEPARATOR


def format_php_pairs(pairs: Iterable[tuple[object, object]]) -> str:
    """把“键值对”序列展平为 ``>k1>v1>k2>v2`` 形式的应答体。

    用于 ``gethit.php``（曲目 ID / 命中次数）这类成对数据。
    """
    flat: list[object] = []
    for key, value in pairs:
        flat.append(key)
        flat.append(value)
    return format_php_fields(flat)


def phpdata(payload: str, index: int) -> str:
    """客户端 ``scr_phpdata`` 的 Python 等价实现。

    用于**测试与自检**：拿它去解析服务器生成的应答体，
    就能确认字段顺序与切分方式真的与 GameMaker 一致，
    而不是靠肉眼比对。

    语义（逐字对齐 ``original_gamecode/gml_Script_scr_phpdata.gml``）：
    把所有 ``>`` 的下标记为 ``ichi[]``，取
    ``ichi[index] + 1`` 起、到下一个 ``>`` 为止的子串；
    ``index`` 超出 ``>`` 的数量时返回空串。
    """
    positions = [position for position, char in enumerate(payload) if char == FIELD_SEPARATOR]
    if index < 0 or index >= len(positions):
        return ""
    start = positions[index] + 1
    end = positions[index + 1] if index + 1 < len(positions) else len(payload)
    return payload[start:end]


def sanitize_field(value: object) -> str:
    """清理单个字段，防止玩家昵称里的 ``>``、``#`` 或换行破坏协议。

    两类干扰字符：

    * ``>`` —— 客户端的字段分隔符（``scr_phpdata``），绝不能出现在字段里；
    * ``#`` —— 客户端 ``draw_text`` 的**换行符**
      （``obj_title_Draw_0.gml:139`` 用 ``draw_text_light`` 渲染昵称），
      带 ``#`` 的昵称会把排行榜排版顶乱；
    * ``\r\n\t`` —— 客户端任何接口都不会剔除原始换行，
      因此服务器必须在写入端保证字段是单行。

    顺带剔除 ``<br>``：只有 ``gethit.php`` 会做这种替换
    （``obj_timer_Other_62.gml:29``），其余接口不会，所以统一不输出。
    """
    text = "" if value is None else str(value)
    text = text.replace(FIELD_SEPARATOR, "")
    text = text.replace("#", "")
    text = text.replace("<br>", "").replace("<br/>", "").replace("<br />", "")
    return re.sub(r"[\r\n\t]+", " ", text).strip()


def sanitize_player_name(name: object, fallback: str = "PLAYER") -> str:
    """规范化玩家昵称。

    客户端默认昵称是 ``PLAYER``（见 ``gml_Object_obj_init_Create_0.gml:119``），
    昵称为空时回退到该默认值，保证排行榜里不会出现空名字。
    """
    cleaned = sanitize_field(name)
    return cleaned[:24] if cleaned else fallback


# --------------------------------------------------------------------------
# md5 校验串（复刻客户端公式）
# --------------------------------------------------------------------------


def md5_hex(text: str) -> str:
    """返回 UTF-8 文本的 md5 十六进制小写摘要。"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def signup_password(player_name: str) -> str:
    """复刻 ``signup.php`` 的 password 公式。

    来源 ``original_gamecode/gml_Object_obj_timer_Create_0.gml:19``::

        password = md5(md5(PLAYERNAME + PLAYERNAME)
                     + md5(PLAYERNAME + "sora")
                     + md5(PLAYERNAME + "game"))

    该值同时充当**账号唯一标识**：客户端把 ``signup.php`` 返回的数字存进
    ``global.ONLINE_ID``，后续 ``update.php`` / ``scoreupload.php`` 都以它为身份。
    """
    inner = (
        md5_hex(player_name + player_name)
        + md5_hex(player_name + "sora")
        + md5_hex(player_name + "game")
    )
    return md5_hex(inner)


def update_password(
    online_id: object,
    player_name: str,
    exp: object,
    all_score: object,
    mission: object,
    rating: object,
) -> str:
    """复刻 ``update.php`` 的 password 公式。

    来源 ``original_gamecode/gml_Object_obj_title_Create_0.gml:41``::

        password = md5(md5(ONLINE_ID + PLAYERNAME + "sora")
                     + md5(PLAYEREXP + ALLSCORE + "game")
                     + md5(MISSION_SHINDO + RATING + "2017"))

    注意最后一个常量在当前客户端是 ``"2017"``，
    与更早版本的“当日日期”不同，这里以当前源码为准。
    """
    inner = (
        md5_hex(f"{online_id}{player_name}sora")
        + md5_hex(f"{exp}{all_score}game")
        + md5_hex(f"{mission}{rating}2017")
    )
    return md5_hex(inner)


def score_upload_password(
    online_id: object,
    song_id: object,
    game_dif: object,
    song_hard: object,
    death_mode: object,
    date: str,
    time: str,
    score: object,
) -> str:
    """复刻 ``scoreupload.php`` 的 password 公式。

    来源 ``original_gamecode/gml_Object_obj_r_Create_0.gml:108``::

        password = md5(md5(ONLINE_ID + SONG_ID + GAME_DIF + "sora")
                     + md5(SONG_HARD + DEATHMODE + YEAR + MM + DD + "game")
                     + md5(HH + MM + SS + SCORE + "password"))

    ``date`` = ``YYYYMMDD``，``time`` 由 ``string(current_hour)`` 起头
    （小时**不**补零、分秒补零），因此直接使用调用方传入的原始字符串，
    服务器不重新格式化，保证与原客户端逐字节一致。
    """
    inner = (
        md5_hex(f"{online_id}{song_id}{game_dif}sora")
        + md5_hex(f"{song_hard}{death_mode}{date}game")
        + md5_hex(f"{time}{score}password")
    )
    return md5_hex(inner)


# --------------------------------------------------------------------------
# 云存档 dataname 编解码
# --------------------------------------------------------------------------

# dataname 由客户端拼成 ``f"{k}{song_id}{j}{field}"``：
# 难度 1 位 + 曲目 ID 5 位 + 谱面等级 1 位 + 字段 1 位，例如 ``0101383S``。
# 早期版本还出现过 ``song_id`` 只写 4 位的写法（``010138C``），
# 因此解析侧同时接受 4~5 位曲目编号。
_SONG_ENTRY_RE = re.compile(r"^(?P<dif>\d)(?P<song>\d{4,5})(?P<hard>\d)(?P<field>[SFCD])$")


def encode_song_entry(difficulty: int, song_id: int, hard_level: int, field: str) -> str:
    """生成云存档里单曲条目的 ``dataname``。

    客户端在 ``gml_Object_obj_upload_Step_0.gml:89-92`` 里这样拼接::

        dataname = string(k) + string(song_id[i]) + string(j) + "S"/"F"/"C"/"D"

    ``k`` 是难度（0..3），``song_id`` 是 5 位曲目编号（10000..10999），
    ``j`` 是谱面等级（0..5）。实测键形如 ``0101383S``：首位的 ``0`` 是难度，
    其后 ``10138`` 是曲目编号，接着 ``3`` 是谱面等级，末尾 ``S`` 是字段。
    """
    return f"{difficulty}{song_id}{hard_level}{field}"


def decode_song_entry(dataname: str) -> tuple[int, int, int, str] | None:
    """解析云存档 ``dataname``；无法识别时返回 ``None``。

    返回 ``(难度, 曲目 ID, 谱面等级, 字段)``。
    兼容 ``k`` 与 ``j`` 为个位数字、``song_id`` 为 4~5 位数字的写法。
    """
    match = _SONG_ENTRY_RE.match(dataname)
    if not match:
        return None
    return (
        int(match.group("dif")),
        int(match.group("song")),
        int(match.group("hard")),
        match.group("field"),
    )


def count_song_entries(datanames: Iterable[str]) -> dict[int, int]:
    """统计一批 ``dataname`` 里每首曲子出现的条目数，用于推算曲目总数。"""
    counter: dict[int, int] = {}
    for name in datanames:
        decoded = decode_song_entry(name)
        if decoded is None:
            continue
        counter[decoded[1]] = counter.get(decoded[1], 0) + 1
    return counter


# --------------------------------------------------------------------------
# 数值解析
# --------------------------------------------------------------------------


def to_int(value: object, default: int = 0) -> int:
    """宽松地转成整数：接受 ``"12"``、``12.0``、``""`` 等情况。

    客户端常常把整数当浮点数上传（GameMaker 没有独立整型），
    例如 ``string(global.PLAYEREXP)`` 可能是 ``"5.00"``，
    因此这里先转 float 再取整，而不是直接用 ``int()``。
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value == value else default  # 过滤 NaN
    text = str(value).strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        try:
            return int(float(text))
        except ValueError:
            return default


def to_float(value: object, default: float = 0.0) -> float:
    """宽松地转成浮点数，解析失败时返回默认值。"""
    if value is None:
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    if not text:
        return default
    try:
        number = float(text)
    except ValueError:
        return default
    # 过滤 NaN / inf，避免污染排行榜排序。
    return number if number == number and abs(number) != float("inf") else default


def format_score(value: float) -> str:
    """成绩百分比的统一输出格式（固定两位小数）。

    客户端用 ``string_format(score, -1, 2)`` 显示成绩，
    服务器存档也统一按两位小数保存，避免出现 ``99.99999999``。
    """
    return f"{value:.2f}"


def format_rating(value: float) -> str:
    """Rating 的输出格式（保留两位小数）。

    排行榜里客户端用 ``real()`` 解析该字段，再交给
    ``string_format(rating, -1, 2)`` 显示，因此 ``"19.00"`` 与 ``"19"``
    在界面上完全等价。这里统一成两位小数，便于人工核对数据文件。
    """
    return f"{value:.2f}"


def gml_number_str(value: object) -> str:
    """模拟 GameMaker ``string()`` 对数字的输出规则。

    这是复刻校验串时**必须**处理的细节：GML 的 ``string()``
    “整数不带小数点，非整数固定两位小数”，因此::

        string(100)   -> "100"
        string(100.5) -> "100.50"
        string(12.3)  -> "12.30"

    而 Python 的 ``str()`` 会给出 ``"100.5"`` / ``"12.3"``，
    直接用会导致 ``update.php`` / ``scoreupload.php`` 的校验串算错。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        if not text:
            return ""
        try:
            number = float(text)
        except ValueError:
            # 非数字（例如昵称）按原样返回。
            return text
    if number == int(number) and abs(number) < 1e15:
        return str(int(number))
    return f"{number:.2f}"


def clamp(value: int, low: int, high: int) -> int:
    """把整数限制在 ``[low, high]`` 区间内。"""
    return max(low, min(high, value))


def chunked(sequence: Sequence[object], size: int) -> Iterable[Sequence[object]]:
    """按固定长度切分序列（维护脚本与测试使用）。"""
    for start in range(0, len(sequence), size):
        yield sequence[start : start + size]
