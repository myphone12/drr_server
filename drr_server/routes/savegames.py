"""云存档接口。

=================================  ==================================================
路径                                参数
=================================  ==================================================
``/DanceRail/uploadsavedata.php``   ``player``, ``dataname``, ``data`` → ``1``
``/DanceRail/getsavedata.php``      ``player``, ``dataname`` → 单条数据
``/DanceRail/getsavedatanum.php``   ``player`` → 条目数
=================================  ==================================================

客户端调用点
------------

* ``original_gamecode/gml_Object_obj_upload_Step_0.gml:6-92``（逐条上传）
* ``original_gamecode/gml_Object_obj_download_Step_0.gml:6-86``（逐条下载）
* ``original_gamecode/gml_Object_obj_download_question_Alarm_0.gml:1``（查条数）

上传量级提醒
------------

客户端每曲上传 24 条（4 难度 × 6 谱面），132 首曲子加上 90 条固定字段
共约 3258 个 GET 请求，按 ``timeline`` 每帧发一条逐步发送
（``obj_upload_Step_0.gml:77`` 的 ``400 + i * 120``）。
因此本接口必须做到**每次请求都立即落盘**，
否则服务器进程一重启玩家的上传就全丢了。
"""

from __future__ import annotations

import logging

from flask import Blueprint, Response, current_app

from drr_server.routes._helpers import arg, log_request, text_response
from drr_server.services.savegame import SaveGameService

logger = logging.getLogger(__name__)

bp = Blueprint("drr_savegames", __name__)


def _saves() -> SaveGameService:
    return current_app.extensions["drr_saves"]


@bp.route("/DanceRail/uploadsavedata.php", methods=["GET", "POST"])
def uploadsavedata_php() -> Response:
    """写入一条云端存档，返回 ``1``。

    旧 ``app.py`` 在条目数恰好等于 ``89 + song_num*24`` 时才写盘，
    既把总数算错了一位（正确固定字段数是 90），
    又导致“少传一条就永远不落盘、进程退出即丢失”的问题。
    这里改为逐条立即落盘，另用 :meth:`SaveGameService.progress`
    提供进度诊断。
    """
    player = arg("player")
    dataname = arg("dataname")
    data = arg("data")

    accepted = _saves().upload(player, dataname, data)
    if accepted:
        log_request(
            "uploadsavedata.php",
            player=player[:8],
            dataname=dataname,
            value=data[:24],
        )
    return text_response("1")


@bp.route("/DanceRail/getsavedata.php", methods=["GET", "POST"])
def getsavedata_php() -> Response:
    """读取一条云端存档。

    客户端会把返回值直接写进本地 INI 的对应字段，
    因此**绝不能**返回 HTML 错误页：旧实现遇到不存在的 DLKEY 会抛
    ``KeyError``，Flask 返回 500 + HTML，客户端会把这坨 HTML
    当成成绩写进本地存档。这里改为返回该字段的安全默认值
    （``.F`` 默认 1，其余默认 0）。
    """
    player = arg("player")
    dataname = arg("dataname")
    value = _saves().get(player, dataname)
    log_request(
        "getsavedata.php", player=player[:8], dataname=dataname, value=value[:24]
    )
    return text_response(value)


@bp.route("/DanceRail/getsavedatanum.php", methods=["GET", "POST"])
def getsavedatanum_php() -> Response:
    """返回某 DLKEY 下的条目数。

    客户端用它判断“云端是否真有一份完整存档”
    （``obj_download_question_Step_0.gml``）。DLKEY 不存在时返回 ``0``。
    """
    player = arg("player")
    count = _saves().count(player)
    log_request("getsavedatanum.php", player=player[:8], count=count)
    return text_response(count)
