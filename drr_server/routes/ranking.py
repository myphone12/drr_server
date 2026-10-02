"""排行榜与曲目统计接口。

=================================  ==================================================
路径                                说明
=================================  ==================================================
``/DanceRail/gethiexpnew.php``      等级（经验值）榜
``/DanceRail/gethiscorenew.php``    总得分榜
``/DanceRail/gethiratingnew.php``   Rating 榜
``/DanceRail/gethit.php``           曲目热度榜（客户端排序后取前 N 名）
``/DanceRail/getstageclear.php``    每首曲目的全球通关率
=================================  ==================================================

所有应答都是 ``>`` 开头的纯文本，字段顺序见
:mod:`drr_server.services.ranking` 的模块文档。
"""

from __future__ import annotations

import logging

from flask import Blueprint, Response, current_app

from drr_server.routes._helpers import arg_int, log_request, text_response
from drr_server.services.ranking import RankingService

logger = logging.getLogger(__name__)

bp = Blueprint("drr_ranking", __name__)


def _ranking() -> RankingService:
    return current_app.extensions["drr_ranking"]


@bp.route("/DanceRail/gethiexpnew.php", methods=["GET", "POST"])
def gethiexpnew_php() -> Response:
    """等级（经验值）榜。

    客户端（``obj_timer_Alarm_3.gml:3-4``）::

        url = ".../gethiexpnew.php";
        gethiexp = http_get(url);

    解析见 ``obj_timer_Step_0.gml:28-38``：每行 ``名字, rating, exp``。
    """
    body = _ranking().exp_leaderboard()
    log_request("gethiexpnew.php", rows=body.count(">") // 3)
    return text_response(body)


@bp.route("/DanceRail/gethiscorenew.php", methods=["GET", "POST"])
def gethiscorenew_php() -> Response:
    """总得分榜（``gml_Object_obj_timer_Alarm_3.gml:5-6``）。

    每行 ``名字, rating, allscore``（``obj_timer_Step_0.gml:40-50``）。
    """
    body = _ranking().score_leaderboard()
    log_request("gethiscorenew.php", rows=body.count(">") // 3)
    return text_response(body)


@bp.route("/DanceRail/gethiratingnew.php", methods=["GET", "POST"])
def gethiratingnew_php() -> Response:
    """Rating 榜（``gml_Object_obj_timer_Alarm_3.gml:7-8``）。

    每行 ``名字, exp, rating`` —— 注意与上面两张榜的字段顺序不同
    （``obj_timer_Step_0.gml:52-62`` 的取值下标）。
    """
    body = _ranking().rating_leaderboard()
    log_request("gethiratingnew.php", rows=body.count(">") // 3)
    return text_response(body)


@bp.route("/DanceRail/gethit.php", methods=["GET", "POST"])
def gethit_php() -> Response:
    """曲目热度榜。

    客户端（``obj_timer_Alarm_1.gml:3``）::

        url = ".../gethit.php?song_num=" + string(song_num);

    参数 ``song_num`` 是客户端自己的曲目数量。服务器按自己的
    ``Settings.song_count`` 输出（该值会在有存档上传后被推断修正，
    见 :class:`~drr_server.storage.Database`），
    并额外在最前面放一组 ``(0, 0)`` 占位——
    客户端解析后会强制把 ``song_hit[0]`` 置 0
    （``obj_timer_Other_62.gml:37``），说明协议本就如此设计。
    """
    client_song_count = arg_int("song_num", 0)
    settings = current_app.config["DRR_SETTINGS"]
    if settings.verbose_log:
        logger.info(
            "[gethit.php] 客户端 song_num=%s，服务器曲数=%s",
            client_song_count,
            settings.song_count,
        )
    body = _ranking().hit_ranking()
    log_request("gethit.php", pairs=body.count(">") // 2)
    return text_response(body)


@bp.route("/DanceRail/getstageclear.php", methods=["GET", "POST"])
def getstageclear_php() -> Response:
    """每首曲目的全球通关率。

    客户端（``obj_timer_Alarm_2.gml:3``）不带任何参数，
    并把结果按曲目顺序填进 ``stagep[]``；
    任务界面显示为 ``"CLEAR RATE : " + stagep[select] + "%"``
    （``obj_mission_Draw_0.gml:23-26``），因此这里返回 0~100 的整数。
    """
    body = _ranking().stage_clear_list()
    log_request("getstageclear.php", songs=body.count(">"))
    return text_response(body)
