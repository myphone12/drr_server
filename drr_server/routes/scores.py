"""成绩上传 / 拉取接口。

=================================  ==================================================
路径                                说明
=================================  ==================================================
``/DanceRail/scoreupload.php``      结算时上传谱面成绩（现代流程）
``/DanceRail/getscoreone.php``      旧版“从服务器拉回某谱面最好成绩”
=================================  ==================================================

客户端调用点
------------

* ``original_gamecode/gml_Object_obj_r_Create_0.gml:97-110``（上传）
* ``original_gamecode/gml_Object_obj_download_old_Create_0.gml:32-38``（拉取）

上传的到底是哪一次成绩？
------------------------

不是本次成绩，而是**该谱面的历史最好成绩**：客户端发送
``string(max(global.SCORE, oldSCORE))``，其中 ``oldSCORE`` 是从本地
``score.<难度>`` 的 ``.S/.F/.C`` 三元组重算出来的旧纪录。
所以本接口语义上是**幂等的“最好成绩更新”**，
服务器按 ``score = max(已存, 上报)`` 处理即可。
"""

from __future__ import annotations

import logging

from flask import Blueprint, Response, current_app

from drr_server.routes._helpers import arg, log_request, text_response
from drr_server.services.scores import ScoreService
from drr_server.services.security import enforce, verify_score_upload

logger = logging.getLogger(__name__)

bp = Blueprint("drr_scores", __name__)


def _scores() -> ScoreService:
    return current_app.extensions["drr_scores"]


def _settings():
    return current_app.config["DRR_SETTINGS"]


@bp.route("/DanceRail/scoreupload.php", methods=["GET", "POST"])
def scoreupload_php() -> Response:
    """上传谱面成绩。

    参数（``gml_Object_obj_r_Create_0.gml:100-108``）::

        id, song_id, game_dif, song_hard, deathmode, date, time, score, password

    其中 ``id`` 是 ``global.ONLINE_ID``。返回值客户端不读取，
    但按惯例返回 ``0`` 表示成功（与 ``update.php`` 保持一致）。

    注意客户端只在 ``os_type != os_windows`` 且非自动演示时上传，
    桌面版玩家不会产生成绩记录，这是原版行为。

    宽松/严格模式
    -------------

    ``Settings.strict_auth`` 为真时校验 ``password``（三重 md5），
    不通过则拒绝写入；默认宽松模式只记日志。这是因为不同客户端版本
    的公式常量可能不同（旧的 ``update.php`` 用过当日日期），
    严格模式会误伤老版本。
    """
    online_id = arg("id") or arg("onlineid") or arg("online_id")
    song_id = arg("song_id")
    difficulty = arg("game_dif")
    hard_level = arg("song_hard")
    death_mode = arg("deathmode")
    client_date = arg("date")
    client_time = arg("time")
    score = arg("score")
    password = arg("password")
    settings = _settings()

    accounts = current_app.extensions["drr_accounts"]
    account = accounts.get(online_id)
    if account is None:
        # 未知账号：旧实现会照样把成绩写进 {player: {song: ...}}，
        # 结果是榜单里出现一批没有账号归属的“幽灵成绩”。
        logger.info("scoreupload.php：online_id=%s 不存在，忽略本次上传", online_id)
        return text_response("0")

    verify = verify_score_upload(
        online_id=online_id,
        song_id=song_id,
        difficulty=difficulty,
        hard_level=hard_level,
        death_mode=death_mode,
        client_date=client_date,
        client_time=client_time,
        score=score,
        password=password,
    )
    if not enforce(verify, strict=settings.strict_auth, endpoint="scoreupload.php"):
        return text_response("0")

    record = _scores().record_upload(
        online_id=online_id,
        song_id=song_id,
        difficulty=difficulty,
        hard_level=hard_level,
        score=score,
        death_mode=death_mode,
        client_date=client_date,
        client_time=client_time,
    )
    if record is not None:
        log_request(
            "scoreupload.php",
            online_id=online_id,
            song_id=record.song_id,
            dif=record.difficulty,
            hard=record.hard_level,
            score=f"{record.score:.2f}",
        )
    return text_response("0")


@bp.route("/DanceRail/getscoreone.php", methods=["GET", "POST"])
def getscoreone_php() -> Response:
    """旧版单谱面成绩拉取（``obj_download_old`` 使用）。

    参数（``gml_Object_obj_download_old_Create_0.gml:32-38``）::

        user_id, nickname, song_id, game_dif, song_hard

    客户端拿到结果后的处理（``obj_download_old_Other_62.gml:9-17``）::

        r0[i, j] = result
        if (real(r0) > song_hiscore0[i, j])   // 本地成绩 = S*100/F
        {
            ini_write_real("score.0", "<song>.<hard>.S", real(r0));
            ini_write_real("score.0", "<song>.<hard>.F", 100);   // 注意这里写死 100
        }

    因为客户端把 ``F`` 写成 100，而本地成绩按 ``S*100/F`` 还原，
    所以这里返回**成绩百分比**（2 位小数）即可正确还原，
    与云端存档里 ``.S`` 存“命中数”的表征不同（见下）。

    单位说明
    --------

    * 云端存档（``uploadsavedata.php``）里的 ``.S`` 是 ``HIT + POOR*0.5``，
      ``.F`` 是音符总数，两者配合才能算出百分比；
    * 而本接口只有单个返回值，客户端会配套写死 ``F = 100``，
      因此必须返回百分比本身。

    服务器内部统一按“百分比”保存成绩（``ScoreService``），
    所以这里直接取 ``best`` 即可。没有记录时返回 ``0``，
    客户端比较后不会改动本地成绩。
    """
    online_id = arg("user_id") or arg("id")
    song_id = arg("song_id")
    difficulty = arg("game_dif")
    hard_level = arg("song_hard")
    value = _scores().best_score(online_id, song_id, difficulty, hard_level)
    log_request(
        "getscoreone.php",
        online_id=online_id,
        song_id=song_id,
        dif=difficulty,
        hard=hard_level,
        best=f"{value:.2f}",
    )
    # 与客户端约定的两位小数格式；无记录时给出 "0.00"。
    return text_response(f"{value:.2f}")
