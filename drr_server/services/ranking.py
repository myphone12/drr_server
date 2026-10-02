"""排行榜服务。

对应客户端接口
--------------

* ``gethiexpnew.php``   —— 等级（经验值）榜
* ``gethiscorenew.php`` —— 总得分榜
* ``gethiratingnew.php``—— Rating 榜
* ``gethit.php``        —— 曲目热度（“命中次数”）榜
* ``getstageclear.php`` —— 每首曲目的全球通关率

客户端调用点
------------

* ``original_gamecode/gml_Object_obj_timer_Alarm_3.gml:3-9``（三个榜单）
* ``original_gamecode/gml_Object_obj_timer_Alarm_1.gml:3``（gethit）
* ``original_gamecode/gml_Object_obj_timer_Alarm_2.gml:3``（getstageclear）

应答格式（关键）
----------------

客户端用 ``scr_phpdata`` 按 ``>`` 切分，并且**从下标 0 开始取值**，
所以应答体必须以 ``>`` 开头。三个榜单的字段顺序各不相同，
以 ``obj_timer_Step_0`` 的解析代码为准：

============================  ==========================================
接口                           每条记录的字段顺序
============================  ==========================================
``gethiexpnew.php``           ``名字, rating, exp``
``gethiscorenew.php``         ``名字, rating, allscore``
``gethiratingnew.php``        ``名字, exp, rating``
============================  ==========================================

注意第三张榜的顺序（exp 在前、rating 在后）与第一张榜相反，
这是客户端显示的列顺序决定的（``obj_title_Draw_0.gml:211-212``），
服务器必须照抄。

另外客户端固定读 10 行（``for (i = 0; i < 10; i++)``），
所以**不足 10 行必须补空**：``real("")`` 在 GML 里等于 0，
空行会渲染成空白而不是报错。旧 ``app.py`` 用 ``Name0..Name9``
之类的假数据占位，这里改为显式的空占位。
"""

from __future__ import annotations

import logging

from drr_server.models import Account
from drr_server.services.accounts import AccountService
from drr_server.services.scores import ScoreService
from drr_server.storage import Database
from drr_server.utils import (
    format_php_fields,
    format_php_pairs,
    format_rating,
    gml_number_str,
    sanitize_player_name,
)

logger = logging.getLogger(__name__)

#: 客户端排行榜固定渲染的行数（``obj_title_Draw_0.gml:115,151,187``）。
CLIENT_LEADERBOARD_ROWS = 10

#: 榜单空位填充值：空名字 + 0 值，客户端 ``real("")`` 会得到 0。
_EMPTY_NAME = ""


class RankingService:
    """把账号表与成绩表汇总成客户端需要的榜单字符串。"""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.accounts = AccountService(db)
        self.scores = ScoreService(db)

    def _song_count(self) -> int:
        """取当前生效的曲目数。

        每次生成列表前都调一次 :meth:`Database.sync_song_count`：
        玩家陆续上传云存档会带出更大的曲目编号，
        服务器借此逐步修正曲目总数，热度榜与通关率列表也随之变长。
        """
        return self.db.sync_song_count()

    # ------------------------------------------------------------ 榜单主体
    def _rows(self) -> int:
        """实际返回的行数：至少覆盖客户端渲染的 10 行。"""
        return max(CLIENT_LEADERBOARD_ROWS, self.db.settings.leaderboard_size)

    def exp_leaderboard(self) -> str:
        """``gethiexpnew.php``：``>名字>rating>exp`` × 10。"""
        rows = self._rows()
        accounts = self.accounts.ranked_by_exp(rows)
        fields: list[str] = []
        for account in accounts:
            fields.extend(self._exp_row(account))
        return format_php_fields(self._pad_rows(fields, accounts, rows))

    def score_leaderboard(self) -> str:
        """``gethiscorenew.php``：``>名字>rating>allscore`` × 10。"""
        rows = self._rows()
        accounts = self.accounts.ranked_by_score(rows)
        fields: list[str] = []
        for account in accounts:
            fields.extend(self._score_row(account))
        return format_php_fields(self._pad_rows(fields, accounts, rows))

    def rating_leaderboard(self) -> str:
        """``gethiratingnew.php``：``>名字>exp>rating`` × 10。

        字段顺序与另外两张榜不同，见模块文档。
        """
        rows = self._rows()
        accounts = self.accounts.ranked_by_rating(rows)
        fields: list[str] = []
        for account in accounts:
            fields.extend(self._rating_row(account))
        return format_php_fields(self._pad_rows(fields, accounts, rows))

    # ------------------------------------------------------------ 行格式
    @staticmethod
    def _exp_row(account: Account) -> list[str]:
        """经验榜一行：名字、Rating、经验值。"""
        return [
            sanitize_player_name(account.nickname),
            format_rating(account.rating),
            str(account.exp),
        ]

    @staticmethod
    def _score_row(account: Account) -> list[str]:
        """总分榜一行：名字、Rating、总得分。

        总得分按 GML ``string()`` 规则输出：整数不带小数点、
        非整数两位小数（客户端用 ``real()`` 解析后原样显示，
        ``"3000"`` 比 ``"3000.0"`` 更接近玩家本机看到的数字）。
        """
        return [
            sanitize_player_name(account.nickname),
            format_rating(account.rating),
            gml_number_str(account.all_score),
        ]

    @staticmethod
    def _rating_row(account: Account) -> list[str]:
        """Rating 榜一行：名字、经验值、Rating（注意顺序）。"""
        return [
            sanitize_player_name(account.nickname),
            str(account.exp),
            format_rating(account.rating),
        ]

    @staticmethod
    def _pad_rows(
        fields: list[str], accounts: list[Account], rows: int
    ) -> list[str]:
        """把已有行补齐到 ``rows`` 行（每行固定 3 个字段）。

        空行用 ``"" / "0" / "0"`` 填充：
        客户端把三个字段分别 ``real()`` 成数字，空字符串会得到 0，
        配合空名字渲染成一行空白，不会出现 "Name0" 这类假数据。
        """
        for _ in range(rows - len(accounts)):
            fields.extend([_EMPTY_NAME, "0", "0"])
        return fields

    # ------------------------------------------------------ 曲目热度 / 通关
    def hit_ranking(self) -> str:
        """``gethit.php``：``>曲目ID>命中次数>曲目ID>命中次数...``。

        客户端 ``obj_timer_Other_62`` 的解析逻辑有两点值得注意：

        1. 它按 ``string_count(">", hit) / 2`` 推算条目数，
           随后立刻把 ``song_hit[0]`` 强制置 0 —— 也就是说
           **第一组数据被当作占位符**（``obj_timer_Other_62.gml:31-37``）。
           这里在最前面插入 ``(0, 0)``，与旧 ``app.py`` 返回
           ``>1>1>2>2>3>3>4>4`` 的写法保持一致。
        2. 它会按命中次数做一次冒泡降序排序，再用排序后的下标
           给每首曲子标上 ``No.x``（``:39-53``）。
           所以服务器**不需要**排序，按曲目顺序输出即可，
           客户端会自行排序并取前 10/20 名显示。
        """
        settings = self.db.settings
        counts = self.scores.song_play_counts()
        pairs: list[tuple[int, int]] = [(0, 0)]  # 占位（客户端会把它归零）
        for index in range(self._song_count()):
            song_id = settings.song_id_base + index
            pairs.append((song_id, counts.get(song_id, 0)))
        return format_php_pairs(pairs)

    def stage_clear_list(self) -> str:
        """``getstageclear.php``：``>通关率>通关率...``，按曲目顺序。

        客户端 ``obj_mission_Draw_0.gml:26`` 直接把它拼成
        ``"CLEAR RATE : " + stagep[select] + "%"``，
        所以这里输出不带百分号的数字即可。

        没有记录的曲目返回 ``0``，让界面显示 ``0%`` 而不是空白
        （旧实现返回 ``(i*10)%100`` 的假数据，会误导玩家）。
        """
        settings = self.db.settings
        rates = self.scores.song_clear_rates()
        values: list[str] = []
        for index in range(self._song_count()):
            song_id = settings.song_id_base + index
            rate = rates.get(song_id, 0.0)
            # 通关率是 0~100 的百分比，输出整数更符合界面展示习惯。
            values.append(str(int(round(rate))))
        return format_php_fields(values)
