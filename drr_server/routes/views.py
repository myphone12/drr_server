"""浏览器页面（非游戏协议）。

=================================  ==================================================
路径                                说明
=================================  ==================================================
``/DanceRail/highscore.view.php``   单谱面排行榜（客户端用 ``url_open`` 打开）
``/DanceRail/admin``                服务器状态总览（运维自用）
``/DanceRail/clock.js``             与 ``date.php`` 算法一致的客户端日期脚本
=================================  ==================================================

``highscore.view.php`` 的调用点
-------------------------------

``original_gamecode/gml_Object_obj_select_Step_0.gml:333-339``::

    if (dif_switch && !release_flag && dmcr(0) && mouse_in(...))
    {
        url = ".../highscore.view.php";
        url += ("?song_id=" + string(song_id[sort[global.SORT, select]]));
        url += ("&game_dif=" + string(global.GAME_DIF));
        url_open(url);
    }

它是在系统浏览器里打开的**人类可读页面**，游戏本身不解析其内容，
因此这里可以自由使用 HTML/CSS。
"""

from __future__ import annotations

import logging

from flask import Blueprint, Response, current_app, render_template

from drr_server.routes._helpers import arg, arg_int
from drr_server.services.scores import ScoreService

logger = logging.getLogger(__name__)

bp = Blueprint("drr_views", __name__)

#: 难度代号，与客户端 ``game_dif`` 对应（``songlist.drf`` 的 E/N/H/U 列）。
DIFFICULTY_LABELS = {
    0: "EASY (E)",
    1: "NORMAL (N)",
    2: "HARD (H)",
    3: "ULTRA (U)",
}


@bp.route("/DanceRail/highscore.view.php", methods=["GET"])
def highscore_view_php() -> str | tuple[str, int]:
    """渲染单谱面榜单页面。

    参数：``song_id``（曲目编号）、``game_dif``（难度 0..3）。
    客户端不传 ``song_hard``，因此这里汇总该难度下所有谱面等级，
    每个等级各列一个榜单，玩家能一次看到全部信息。
    """
    settings = current_app.config["DRR_SETTINGS"]
    song_id = arg_int("song_id", 0)
    difficulty = arg_int("game_dif", 0)
    scores: ScoreService = current_app.extensions["drr_scores"]
    accounts = current_app.extensions["drr_accounts"]

    boards = scores.song_scoreboard(song_id, difficulty)
    rows: list[dict] = []
    for hard_level in range(settings.hard_levels):
        entries = boards.get(hard_level, [])
        decorated = []
        for item in entries[:20]:
            account = accounts.get(item["online_id"])
            decorated.append(
                {
                    "rank": len(decorated) + 1,
                    "name": account.nickname if account else item["online_id"],
                    "score": f"{item['score']:.2f}",
                    "death_mode": item["death_mode"],
                    "updated_at": item["updated_at"],
                }
            )
        rows.append({"hard_level": hard_level, "entries": decorated})

    return render_template(
        "highscore.html",
        song_id=song_id,
        difficulty=difficulty,
        difficulty_label=DIFFICULTY_LABELS.get(difficulty, f"DIFFICULTY {difficulty}"),
        boards=rows,
        song_count=settings.song_count,
        song_id_base=settings.song_id_base,
    )


@bp.route("/DanceRail/clock.js", methods=["GET"])
def clock_js() -> Response:
    """输出与 ``date.php`` 算法一致的客户端日期脚本。

    ``date.php`` 的语义是“服务器所在时区的当天日期”，
    所以这里让浏览器用**本机时区**算出 ``YYYYMMDD`` 并写入页面元素，
    与客户端 ``obj_timer_Create_0`` 得到的结果完全同步，
    不会出现“服务器在 UTC、玩家在 UTC+8”导致签到日期相差一天的问题。
    """
    script = """(function () {
  var d = new Date();
  var pad = function (n) { return (n < 10 ? "0" : "") + n; };
  var stamp = "" + d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate());
  var nodes = document.querySelectorAll("[data-drr-clock]");
  for (var i = 0; i < nodes.length; i++) { nodes[i].textContent = stamp; }
})();"""
    response = Response(script, mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/DanceRail/admin", methods=["GET"])
def admin_page() -> str:
    """服务器状态总览（运维自用）。

    展示：账号数、云存档数、成绩条目数、推断曲数、配置开关。
    不提供写操作，避免把管理接口变成新的攻击面；
    需要改数据时直接编辑 ``data/*.json`` 或用维护脚本。
    """
    settings = current_app.config["DRR_SETTINGS"]
    db = current_app.extensions["drr_db"]
    accounts = current_app.extensions["drr_accounts"]
    scores = current_app.extensions["drr_scores"]

    all_accounts = accounts.all_accounts()
    saves_raw = db.saves.read()
    save_players = [
        {
            "player": key,
            "entries": len(value) if isinstance(value, dict) else 0,
            "online_id": value.get("onlineid") if isinstance(value, dict) else "",
        }
        for key, value in saves_raw.items()
    ]
    save_players.sort(key=lambda item: -item["entries"])

    score_state = scores._state()  # noqa: SLF001 - 管理页只读诊断，内部结构直接用
    stats = {
        "accounts": len(all_accounts),
        "banned": sum(1 for account in all_accounts.values() if account.banned),
        "save_players": len(save_players),
        "save_entries": sum(item["entries"] for item in save_players),
        "score_players": len(score_state.get("players", {})),
        "song_count": settings.song_count,
        "data_dir": str(settings.data_dir),
        "strict_auth": settings.strict_auth,
        "card_mode": settings.card_mode,
        "cards_single_use": settings.cards_single_use,
    }
    return render_template(
        "admin.html",
        stats=stats,
        accounts=sorted(
            all_accounts.values(), key=lambda account: account.online_id
        ),
        save_players=save_players[:50],
    )
