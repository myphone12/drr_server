"""云存档服务（上传 / 下载 / 计数）。

对应客户端接口
--------------

* ``uploadsavedata.php``   —— 逐条上传云端存档
* ``getsavedata.php``      —— 逐条下载
* ``getsavedatanum.php``   —— 查询某个 DLKEY 下已有多少条数据

客户端调用点
------------

* ``original_gamecode/gml_Object_obj_upload_Create_0.gml:3-5``（生成 DLKEY）
* ``original_gamecode/gml_Object_obj_upload_Step_0.gml``（逐条上传）
* ``original_gamecode/gml_Object_obj_download_Step_0.gml``（逐条下载）
* ``original_gamecode/gml_Object_obj_download_question_Alarm_0.gml:1``（查条数）

关于 DLKEY（``player`` 参数）
-----------------------------

客户端并没有用 ``online_id`` 作为存档主键，而是现算一个 md5::

    playerid = md5(ONLINE_ID + PLAYERNAME + (hour + minute + second))

（见 ``gml_Object_obj_upload_Create_0.gml:3``）。
因此服务器无法反推出是哪个账号，只能把它当作**不透明的存档标识**，
这也正是它的设计意图：截图保存 DLKEY 即可在任意设备取回存档。
换句话说，DLKEY 本身就是密码，服务器不会对它做额外的权限校验，
但会校验长度与字符集，避免把文件系统/JSON 玩坏。

关于 ``dataname``
-----------------

条目名有两种形态（详见 :mod:`drr_server.utils` 的编解码函数）：

* 固定字段：``playerexp`` / ``onlineid`` / ``mission`` / ``starttime`` /
  ``playtimes`` / ``cleartimes`` / ``allcombo`` / ``allmiss`` / ``alltime``、
  ``speedtimes0..30``、``hardtimes0..5``、``diftimes0..3``、
  ``rating30_0..29``、``rating10_0..9``（共 90 条）；
* 曲目字段：``{难度}{曲目ID}{谱面等级}{S|F|C|D}``，例如 ``0101383S``。

旧 ``app.py`` 用 ``len(keys) == 89 + song_num*24`` 判断“上传完成”，
其中 89 是笔误（正确是 90），而且只要玩家少上传一条就永远不写盘 ——
数据会一直停留在内存里，进程一退就全丢。
这里改为**每次上传都立即落盘**，并单独记录上传进度用于诊断。
"""

from __future__ import annotations

import logging
import re

from drr_server.storage import Database
from drr_server.utils import (
    count_song_entries,
    decode_song_entry,
    format_score,
    to_int,
)

logger = logging.getLogger(__name__)

# DLKEY 是 md5 十六进制串；宽松接受 8~64 位字母数字，兼容将来客户端换算法。
_DLKEY_RE = re.compile(r"^[0-9a-zA-Z]{8,64}$")

# ``dataname`` 允许的字符集：字母、数字、下划线、点、连字符。
_DATANAME_RE = re.compile(r"^[0-9a-zA-Z_.\-]{1,64}$")


class SaveGameService:
    """云端存档业务封装。"""

    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------ 参数校验
    @staticmethod
    def normalize_dlkey(player: object) -> str | None:
        """校验并规范化 DLKEY（``player`` 参数）。

        md5 大小写不敏感，统一转小写，避免 ``ABC...`` 与 ``abc...``
        被当成两个不同的存档。
        """
        key = str(player or "").strip().lower()
        if not _DLKEY_RE.match(key):
            return None
        return key

    @staticmethod
    def normalize_dataname(dataname: object) -> str | None:
        """校验并规范化 ``dataname``。"""
        name = str(dataname or "").strip()
        if not _DATANAME_RE.match(name):
            return None
        return name

    # ---------------------------------------------------------------- 上传
    def upload(self, player: object, dataname: object, value: object) -> bool:
        """写入一条存档数据。

        返回 ``True`` 表示已接受。

        与旧实现不同，**每条数据都会立即落盘**：
        旧代码只有在条目数恰好等于一个算错的总数时才写文件，
        导致部分上传的存档在服务器重启后全部丢失。
        """
        dlkey = self.normalize_dlkey(player)
        name = self.normalize_dataname(dataname)
        if dlkey is None or name is None:
            logger.info("存档上传参数非法：player=%r dataname=%r", player, dataname)
            return False

        # 客户端把数值都序列化成字符串；统一按字符串保存，
        # 这样原样回传时与客户端预期完全一致（例如 "1.00" 不会被改成 "1.0"）。
        payload = "" if value is None else str(value)

        def _mutate(data: dict) -> None:
            entries = data.setdefault(dlkey, {})
            entries[name] = payload

        self.db.saves.update(_mutate)

        # 每次上传都尝试刷新曲目总数推断值（仅在变大时更新，开销可控）。
        self.db.refresh_song_count_from_saves()
        return True

    # ---------------------------------------------------------------- 下载
    def get(self, player: object, dataname: object) -> str:
        """读取一条存档数据；不存在时返回该字段的安全默认值。

        为什么要有默认值？因为旧实现在 ``save[player][dataname]`` 上
        直接索引，玩家输错 DLKEY 或上传中断时会抛 ``KeyError``，
        Flask 返回 500 + HTML 错误页，客户端拿到一坨 HTML 之后
        会把它当成成绩写进本地存档，把玩家的数据弄坏。
        这里改为返回默认值，客户端至少能正常走完下载流程。
        """
        dlkey = self.normalize_dlkey(player)
        name = self.normalize_dataname(dataname)
        if dlkey is None or name is None:
            return self.default_value(name or "")

        entries = self.db.saves.read().get(dlkey)
        if not isinstance(entries, dict):
            logger.info("存档下载：DLKEY %s 不存在，返回默认值", dlkey[:8])
            return self.default_value(name)
        if name not in entries:
            return self.default_value(name)

        value = entries[name]
        # 兼容手工维护数据时可能存成数字/布尔值的情况。
        if isinstance(value, float):
            return format_score(value)
        return "" if value is None else str(value)

    @staticmethod
    def default_value(dataname: str) -> str:
        """按字段语义给出默认值。

        客户端在下载流程里对 ``.F``（音符总数）的默认值是 1
        （见 ``gml_Object_obj_r_Create_0.gml:42`` 的 ``ini_read_real(..., ".F", 1)``），
        其余字段默认为 0。这样做可以让“空存档”在本地计算出 0 分而不是除零。
        """
        decoded = decode_song_entry(dataname)
        if decoded is not None and decoded[3] == "F":
            return "1"
        return "0"

    # ---------------------------------------------------------------- 计数
    def count(self, player: object) -> int:
        """返回某个 DLKEY 下的条目数；DLKEY 不存在时返回 0。

        客户端在 ``obj_download_question_Alarm_0`` 拿到该数字后
        会与本地期望的条目数比较，用来提示“云端存档不完整”。
        """
        dlkey = self.normalize_dlkey(player)
        if dlkey is None:
            return 0
        entries = self.db.saves.read().get(dlkey)
        return len(entries) if isinstance(entries, dict) else 0

    def exists(self, player: object) -> bool:
        """判断 DLKEY 是否已有数据。"""
        return self.count(player) > 0

    # ---------------------------------------------------------------- 诊断
    def progress(self, player: object) -> dict:
        """返回上传进度诊断信息（供 ``/DanceRail/admin`` 页面与日志使用）。

        计数口径：客户端每首曲子会产生
        ``difficulties × hard_levels × len(score_fields)`` 个**不同的键**
        （默认 4 × 6 × 4 = 96 个：同一 ``(难度, 谱面)`` 组合下
        ``S``/``F``/``C``/``D`` 各占一个 ``dataname``）。
        加上 90 个固定条目，132 首曲子共 12762 条。
        """
        settings = self.db.settings
        dlkey = self.normalize_dlkey(player)
        entries = self.db.saves.read().get(dlkey, {}) if dlkey else {}
        if not isinstance(entries, dict):
            entries = {}
        song_entries = count_song_entries(entries.keys())
        expected = (
            settings.base_save_entries
            + settings.song_count * settings.saves_per_song * len(settings.score_fields)
        )
        return {
            "player": dlkey or "",
            "entries": len(entries),
            "expected_entries": expected,
            "songs_seen": len(song_entries),
            "song_count": settings.song_count,
            "complete": len(entries) >= expected,
        }

    # ------------------------------------------------------------ 内部工具
    def online_id_of(self, player: object) -> str:
        """从存档里读回该 DLKEY 对应的 ``onlineid``。

        客户端上传的第一批固定字段里包含 ``onlineid``
        （见 ``gml_Object_obj_upload_Step_0.gml:10``），
        因此可以用它把“不透明 DLKEY”与账号关联起来，
        便于管理页面展示归属。
        """
        dlkey = self.normalize_dlkey(player)
        if dlkey is None:
            return ""
        entries = self.db.saves.read().get(dlkey)
        if not isinstance(entries, dict):
            return ""
        return str(to_int(entries.get("onlineid"), 0))
