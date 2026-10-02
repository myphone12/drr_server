"""数据模型。

服务器内部一律使用这些 dataclass，而不是直接操作裸 dict：
好处是字段名有据可查（对照客户端上传的变量名），
并且能在边界处集中做类型转换与校验。

所有模型都提供 ``to_dict`` / ``from_dict``，用于与 JSON 文件互转。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from drr_server.utils import (
    clamp,
    format_rating,
    format_score,
    gml_number_str,
    to_float,
    to_int,
)


def _now_iso() -> str:
    """返回带时区的 ISO8601 时间戳，便于跨机迁移数据时不产生歧义。"""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def make_song_key(difficulty: int, song_id: int, hard_level: int) -> str:
    """把 ``(难度, 曲目, 谱面等级)`` 编码成成绩表的键。

    选用 ``difficulty/song_id/hard_level`` 这种带分隔符的形式，
    便于人工阅读，也避免 ``0101383S`` 那种“无分隔拼接”的歧义。
    """
    return f"{difficulty}/{song_id}/{hard_level}"


def parse_song_key(key: str) -> tuple[int, int, int] | None:
    """解析 :func:`make_song_key` 生成的键；格式不符返回 ``None``。"""
    parts = key.split("/")
    if len(parts) != 3:
        return None
    try:
        difficulty, song_id, hard_level = (int(p) for p in parts)
    except ValueError:
        return None
    return difficulty, song_id, hard_level


@dataclass(slots=True)
class Account:
    """玩家账号。

    主键是 ``online_id``——客户端在 ``signup.php`` 拿到它并存入
    ``savedata`` 的 ``player.onlineid``，此后所有需要身份的接口都用它。

    字段与客户端变量一一对应：

    ============  ==========================  ==================================
    模型字段       客户端变量                  说明
    ============  ==========================  ==================================
    online_id     global.ONLINE_ID            账号数字 ID，服务器分配
    password      md5 三重拼接                 由昵称派生的登录凭据（见 utils）
    nickname      global.PLAYERNAME           玩家昵称（排行榜展示）
    exp           global.PLAYEREXP            经验值（排行榜按等级展示）
    all_score     global.ALLSCORE             总得分（排行榜按总分展示）
    subtitle      global.MISSION_SHINDO       称号/任务进度
    rating        global.RATING               实力值，两位小数
    ============  ==========================  ==================================

    ``all_score`` 之所以是浮点而不是整数：客户端用
    ``scr_all_score_get()`` 把每首曲子的百分制成绩相加
    （``S*100/F``），结果**常态带小数**，例如 ``1200.5``，
    经 GML ``string()`` 变成 ``"1200.50"`` 后上传。
    若按整数存储会把小数部分抹掉，排行榜数值就会与玩家本机不一致。
    """

    online_id: str
    password: str = ""
    nickname: str = "PLAYER"
    exp: int = 1
    all_score: float = 0.0
    subtitle: int = 0
    rating: float = 0.0
    banned: bool = False
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    # -------------------------------------------------------------- 序列化
    def to_dict(self) -> dict:
        return {
            "onlineid": self.online_id,
            "password": self.password,
            "nickname": self.nickname,
            "exp": self.exp,
            "allscore": gml_number_str(self.all_score),
            "subtitle": self.subtitle,
            "rating": format_rating(self.rating),
            "banned": self.banned,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Account":
        """从 JSON 记录构造账号对象。

        兼容旧 ``data.json``：其中 ``exp`` / ``allscore`` / ``rating``
        都是字符串，且没有 ``created_at`` 等审计字段。
        """
        online_id = str(data.get("onlineid") or data.get("online_id") or "").strip()
        return cls(
            online_id=online_id,
            password=str(data.get("password") or ""),
            nickname=str(data.get("nickname") or "PLAYER"),
            exp=to_int(data.get("exp"), 1),
            all_score=to_float(data.get("allscore", data.get("all_score")), 0.0),
            subtitle=to_int(data.get("subtitle"), 0),
            rating=to_float(data.get("rating"), 0.0),
            banned=bool(data.get("banned", False)),
            created_at=str(data.get("created_at") or _now_iso()),
            updated_at=str(data.get("updated_at") or _now_iso()),
        )

    # ---------------------------------------------------------------- 行为
    @property
    def level(self) -> int:
        """由经验值换算出等级。

        客户端没有等级表，而是直接开方
        （``gml_Script_scr_exptolevel.gml``）：``level = floor(sqrt(exp))``，
        ``exp <= 1`` 时返回 1。因此等级 L 的区间是 ``[L², (L+1)² - 1]``。

        服务器排行榜返回的是**原始经验值**（客户端会自己换算成
        ``Lv.n`` 显示），这个属性只供管理页展示使用。
        """
        if self.exp <= 1:
            return 1
        return int(self.exp**0.5)

    def touch(self) -> None:
        """更新 ``updated_at``。"""
        self.updated_at = _now_iso()

    def apply_client_update(
        self,
        *,
        nickname: str | None = None,
        exp: object = None,
        all_score: object = None,
        subtitle: object = None,
        rating: object = None,
    ) -> None:
        """按 ``update.php`` 的语义就地更新账号。

        客户端每次进入标题画面都会上报这些字段，因此这里有两点约定：

        * ``exp`` / ``all_score`` 只增不减，避免客户端重装或回档把榜单刷低；
        * ``rating`` 允许下降（成绩变差、榜单重算时需要），
          但拒绝非法值（负数、超出合理量级）。
        """
        if nickname is not None:
            self.nickname = nickname
        if exp is not None:
            self.exp = max(self.exp, clamp(to_int(exp, self.exp), 0, 10**9))
        if all_score is not None:
            # 总得分是浮点：客户端把各曲百分比相加，常态带两位小数。
            value = to_float(all_score, self.all_score)
            self.all_score = max(self.all_score, max(0.0, min(value, 10**9)))
        if subtitle is not None:
            self.subtitle = max(self.subtitle, clamp(to_int(subtitle, 0), 0, 10**6))
        if rating is not None:
            value = to_float(rating, self.rating)
            if 0.0 <= value <= 10**6:
                self.rating = value
        self.touch()


@dataclass(slots=True)
class ScoreRecord:
    """单次成绩上传记录（``scoreupload.php``）。

    客户端在结算界面把本次成绩（或历史最好成绩，取 ``max``）上传，
    服务器据此维护：

    * 玩家在该谱面上的最好成绩；
    * 曲目热度（``gethit.php`` 的“命中次数”）；
    * 谱面通关率（``getstageclear.php``）。
    """

    online_id: str
    song_id: int
    difficulty: int
    hard_level: int
    score: float
    death_mode: int = 0
    client_date: str = ""
    client_time: str = ""
    received_at: str = field(default_factory=_now_iso)

    @property
    def song_key(self) -> str:
        return make_song_key(self.difficulty, self.song_id, self.hard_level)

    def to_dict(self) -> dict:
        return {
            "onlineid": self.online_id,
            "song_id": self.song_id,
            "game_dif": self.difficulty,
            "song_hard": self.hard_level,
            "score": format_score(self.score),
            "deathmode": self.death_mode,
            "date": self.client_date,
            "time": self.client_time,
            "received_at": self.received_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ScoreRecord":
        return cls(
            online_id=str(data.get("onlineid") or ""),
            song_id=to_int(data.get("song_id"), 0),
            difficulty=to_int(data.get("game_dif"), 0),
            hard_level=to_int(data.get("song_hard"), 0),
            score=to_float(data.get("score"), 0.0),
            death_mode=to_int(data.get("deathmode"), 0),
            client_date=str(data.get("date") or ""),
            client_time=str(data.get("time") or ""),
            received_at=str(data.get("received_at") or _now_iso()),
        )


def scores_to_dict(records: dict[str, dict[str, dict]]) -> dict:
    """把成绩表的内部结构原样导出（供 JSON 存储使用）。

    内部结构形如::

        {
          "10": {                     # online_id
            "0/10001/3": {            # song_key
              "best": "99.50",        # 该谱面最好成绩
              "deathmode": 1,         # 是否在死亡模式下达成
              "plays": 7,             # 该谱面游玩次数
              "last_played": "...",
              "history": [ {...} ]    # 最近若干次上传（用于近况榜/审计）
            }
          }
        }
    """
    return records
