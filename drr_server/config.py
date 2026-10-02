"""运行期配置。

配置来源有两条：内置默认值、以及环境变量覆盖（前缀 ``DRR_``）。
之所以不引入第三方配置库，是因为本服务器只需要少量开关，
而游戏客户端对输出格式很敏感，配置项越少越不容易出错。

示例::

    set DRR_PORT=12561
    set DRR_DATA_DIR=D:\\drr_data
    set DRR_STRICT_AUTH=1
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

# 项目根目录：本文件位于 <root>/drr_server/config.py
_PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _PACKAGE_DIR.parent

# 默认值集中在这里，便于 ``from_env`` 在没有环境变量时直接引用。
# 注意：dataclass 使用 ``slots=True`` 后，类属性会变成 slot 描述符，
# 因此不能写 ``cls.port`` 之类的写法来取默认值。
DEFAULT_HOST = "0.0.0.0"
# 客户端把地址硬编码成 ``http://192.168.0.100/DanceRail/xxx.php``，
# URL 里没有端口号 ⇒ 实际请求的是 **80 端口**。
# 旧 ``app.py``/``run.py`` 监听 12561，客户端根本连不上，
# 需要玩家自己改包或做端口转发。这里默认对齐客户端（80），
# 若本机 80 端口被占用，可通过 ``DRR_PORT`` 改成别的端口再自行转发。
DEFAULT_PORT = 80
DEFAULT_SONG_COUNT = 132
DEFAULT_SONG_ID_BASE = 10000
DEFAULT_CARD_COIN_DEFAULT = 10
DEFAULT_CARD_COIN_MAX = 9999


def _env_flag(name: str, default: bool) -> bool:
    """读取布尔型环境变量，接受 1/true/yes/on（大小写不敏感）。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y"}


def _env_int(name: str, default: int) -> int:
    """读取整型环境变量；解析失败时回退默认值而不是让服务启动失败。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


@dataclass(slots=True)
class Settings:
    """服务器全部可调参数。

    字段顺序即“重要性顺序”：host/port/data_dir 是部署相关，
    其余是游戏数值相关。
    """

    # ---------------------------------------------------------------- 部署
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    """监听端口。客户端源码里写死了 ``http://192.168.0.100/DanceRail/``，
    因此部署时要么把本机 IP 设为 192.168.0.100，要么改包重编译。"""

    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data")
    """JSON 数据目录。默认放在项目根的 ``data/`` 子目录。"""

    debug: bool = False
    """Flask 调试模式。仅本地排查时开启；生产用 ``run.py``（waitress）。"""

    # ------------------------------------------------------------ 数据迁移
    legacy_accounts_file: Path = field(
        default_factory=lambda: PROJECT_ROOT / "data.json"
    )
    """旧版账号文件（JSON 数组）。启动时会自动迁移到 ``data_dir/accounts.json``。"""

    legacy_save_file: Path = field(default_factory=lambda: PROJECT_ROOT / "save.json")
    """旧版云存档文件（``{player: {dataname: data}}``）。"""

    migrate_legacy_data: bool = True
    """是否在启动时执行旧数据迁移。迁移是幂等的：已迁移过就跳过。"""

    # ---------------------------------------------------------------- 玩法
    song_count: int = DEFAULT_SONG_COUNT
    """曲目数量 ``song_num``。

    客户端从 ``songlist.drf`` 读取真实数量，服务器无法直接得知，
    因此这里作为*兜底值*：一旦有玩家成功上传过云存档，
    服务器会从存档的曲目条目里推断出真实曲数并使用推断值。
    """

    song_id_base: int = DEFAULT_SONG_ID_BASE
    """曲目 ID 起点。``song_times`` 与存档 ``dataname`` 都基于 10000+ 的编号。

    客户端 ``songlist.drf`` 的 ``<id>`` 字段从 10000 起递增
    （教学曲固定为 10000，见 ``obj_init_Create_0.gml:21``）。
    """

    difficulties: int = 4
    """难度档位数（k = 0..3，对应 E/N/H/U 与隐藏 M 之外的 4 个上传档）。"""

    hard_levels: int = 6
    """每难度下的谱面等级数（j = 0..5）。"""

    score_fields: tuple[str, ...] = ("S", "F", "C", "D")
    """云存档中每个 (难度, 谱面) 组合的四个字段：
    ``S`` 命中数、``F`` 音符总数、``C`` 暴击数、``D`` 死亡模式标记。"""

    leaderboard_size: int = 10
    """排行榜条数。客户端 ``obj_title_Draw_0`` 只画 10 行。"""

    ranking_recent_scores: int = 100
    """计算 Rating 时取用的最近成绩条数上限（游戏内为 last10，这里留足余量）。"""

    # ---------------------------------------------------------------- 安全
    strict_auth: bool = False
    """是否严格校验客户端提交的 ``password``（md5 三重拼接）。

    默认关闭：客户端在部分版本里把公式中的日期写成了常量，
    严格校验会误伤原版客户端。开启后排行榜写入将被验签保护。
    """

    card_coin_default: int = DEFAULT_CARD_COIN_DEFAULT
    """``usecard.php`` 在 ``auto``/``legacy`` 模式下给未登记数字卡发放的金币数。

    ``auto`` 模式沿用该值；``legacy`` 模式改用卡号本身（见 ``card_mode``）。
    """

    card_coin_max: int = DEFAULT_CARD_COIN_MAX
    """单张卡可兑换金币的上限，防止把卡号当金币刷。"""

    card_mode: str = "auto"
    """``usecard.php`` 的发放策略，取值 ``legacy`` / ``auto`` / ``whitelist``。

    * ``legacy``：复刻旧 ``app.py`` 行为（卡号即面额），仅用于兼容老存档；
    * ``auto``（默认）：登记卡按登记面额发，未登记数字卡发 ``card_coin_default``；
    * ``whitelist``：只认 ``data/cards.json`` 里登记过的卡。
    """

    cards_single_use: bool = False
    """同一卡号是否只能兑换一次。开启后重复使用返回 0（客户端显示“此卡不可用”）。"""

    ban_file: Path | None = None
    """若该文件存在，``cheat_test.php`` 返回封禁标记。默认关闭（None）。"""

    verbose_log: bool = True
    """是否输出每个请求的业务日志（原 ``app.py`` 的 print 行为）。"""

    # ------------------------------------------------------- 序列化 / 其他
    json_as_ascii: bool = False
    """写盘 JSON 是否转义非 ASCII。关闭可让玩家昵称在文件里保持可读。"""

    @property
    def accounts_file(self) -> Path:
        return self.data_dir / "accounts.json"

    @property
    def saves_file(self) -> Path:
        return self.data_dir / "saves.json"

    @property
    def scores_file(self) -> Path:
        return self.data_dir / "scores.json"

    @property
    def cards_file(self) -> Path:
        return self.data_dir / "cards.json"

    @property
    def meta_file(self) -> Path:
        return self.data_dir / "meta.json"

    # ---------------------------------------------------------------- 派生量
    @property
    def effective_song_count(self) -> int:
        """当前生效的曲目数量。

        ``song_count`` 会被 :meth:`~drr_server.storage.Database` 在启动时
        用云端存档推断出的值**就地抬高**（见 ``sync_song_count``），
        因此这里直接返回 ``song_count`` 即可；
        保留这个别名是为了让调用点语义清晰：读的是“生效值”而不是“配置项”。
        """
        return self.song_count

    @property
    def song_id_limit(self) -> int:
        """曲目编号的**闭区间上界**。

        曲目编号从 ``song_id_base`` 起连续递增，因此上界就是
        ``song_id_base + song_count - 1``。之所以不写死一个常量，
        是因为服务器无法读取客户端的 ``songlist.drf``，
        只能靠配置 + 云端存档推断（见 :class:`~drr_server.storage.Database`）；
        写死常量会导致“换了一版曲包之后成绩全被拒绝”这种隐蔽故障
        —— 实测中它就表现为 ``scoreupload.php`` 静默丢弃成绩。
        """
        return self.song_id_base + self.song_count - 1

    @property
    def saves_per_song(self) -> int:
        """单曲占用多少个**存档条目**（``dataname`` 键）。

        客户端为每首曲子上传 ``difficulties × hard_levels`` 个 ``(难度, 谱面)``
        组合，每个组合一个键（值里再用 ``.S/.F/.C/D`` 区分字段时是本地 INI，
        云端则拆成四个独立的键，因此这里已经是 4 × 6 = 24）。

        注意别和**请求数**混淆：客户端每首歌实际发出
        ``difficulties × hard_levels × len(score_fields)`` 次请求
        （默认 4 × 6 × 4 = 96 次，见 ``obj_upload_Step_0.gml:83-92``），
        因为 ``S/F/C/D`` 是四条独立的 ``uploadsavedata.php`` 调用。
        """
        return self.difficulties * self.hard_levels

    @property
    def requests_per_song(self) -> int:
        """单曲上传/下载时客户端发出的 HTTP 请求数（默认 96）。"""
        return self.saves_per_song * len(self.score_fields)

    @property
    def base_save_entries(self) -> int:
        """与曲目数量无关的固定存档条目数。

        客户端上传的固定字段为：

        * 单值：playerexp / onlineid / mission / starttime / playtimes /
          cleartimes / allcombo / allmiss / alltime —— 共 9 条
        * speedtimes0..30 —— 31 条
        * hardtimes0..5 —— 6 条
        * difftimes0..3 —— 4 条
        * rating30_0..29 —— 30 条
        * rating10_0..9 —— 10 条

        合计 90 条。旧 ``app.py`` 里写的是 89，属于差一错误；
        这里修正为 90，并同时保留“条目数推算曲数”的兜底逻辑。
        """
        return 9 + 31 + 6 + 4 + 30 + 10

    @classmethod
    def from_env(cls) -> "Settings":
        """从环境变量构造配置对象，未设置的环境变量沿用默认值。"""
        kwargs: dict[str, object] = {
            "host": os.environ.get("DRR_HOST", DEFAULT_HOST),
            "port": _env_int("DRR_PORT", DEFAULT_PORT),
            "debug": _env_flag("DRR_DEBUG", False),
            "strict_auth": _env_flag("DRR_STRICT_AUTH", False),
            "cards_single_use": _env_flag("DRR_CARDS_SINGLE_USE", False),
            "migrate_legacy_data": _env_flag("DRR_MIGRATE_LEGACY", True),
            "verbose_log": _env_flag("DRR_VERBOSE_LOG", True),
            "song_count": _env_int("DRR_SONG_COUNT", DEFAULT_SONG_COUNT),
            "card_coin_default": _env_int(
                "DRR_CARD_COIN_DEFAULT", DEFAULT_CARD_COIN_DEFAULT
            ),
            "card_coin_max": _env_int("DRR_CARD_COIN_MAX", DEFAULT_CARD_COIN_MAX),
            "card_mode": os.environ.get("DRR_CARD_MODE", "auto").strip().lower(),
        }
        data_dir = os.environ.get("DRR_DATA_DIR")
        if data_dir:
            kwargs["data_dir"] = Path(data_dir).expanduser().resolve()

        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in kwargs.items() if k in known})  # type: ignore[arg-type]
