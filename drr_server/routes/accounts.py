"""账号相关接口。

===============================  ==================================================
路径                              说明
===============================  ==================================================
``/DanceRail/date.php``          返回服务器日期 ``YYYYMMDD``，客户端用它做每日签到
``/DanceRail/signup.php``        注册/取回 ``ONLINE_ID``
``/DanceRail/update.php``        上报经验、总分、称号、Rating
``/DanceRail/cheat_test.php``    封禁检查
``/DanceRail/usecard.php``       兑换卡换金币
===============================  ==================================================

每个处理函数都对应客户端源码里的一处 ``http_get``，注释中标注了出处。
"""

from __future__ import annotations

import datetime
import logging

from flask import Blueprint, Response

from drr_server.routes._helpers import arg, arg_int, log_request, text_response
from drr_server.services.accounts import AccountService
from drr_server.services.cards import CardService
from drr_server.services.security import enforce, verify_signup, verify_update
from drr_server.utils import sanitize_player_name

logger = logging.getLogger(__name__)

bp = Blueprint("drr_accounts", __name__)


def _accounts() -> AccountService:
    from flask import current_app

    return current_app.extensions["drr_accounts"]


def _cards() -> CardService:
    from flask import current_app

    return current_app.extensions["drr_cards"]


def _settings():
    from flask import current_app

    return current_app.config["DRR_SETTINGS"]


@bp.route("/DanceRail/date.php", methods=["GET", "POST"])
def date_php() -> Response:
    """返回服务器当天日期，格式 ``YYYYMMDD``。

    客户端 ``obj_timer_Create_0`` 用它初始化 ``global.DATE``，
    然后与本地签到日期比较，决定是否发放每日登录奖励::

        g_date = http_get(".../date.php");
        ...
        global.DATE = real(ds_map_find_value(async_load, "result"));

    注意客户端做的是 ``real()`` 转换，所以必须是纯数字，不能有连字符。
    依客户端语义这里应使用**玩家所在时区**的日期；
    本服务器与客户端通常在同一局域网/时区，因此直接用本机时间。
    """
    now = datetime.datetime.now()
    today = f"{now.year:04d}{now.month:02d}{now.day:02d}"
    log_request("date.php", result=today)
    return text_response(today)


@bp.route("/DanceRail/signup.php", methods=["GET", "POST"])
def signup_php() -> Response:
    """注册账号并返回 ``ONLINE_ID``。

    客户端调用（``gml_Object_obj_timer_Create_0.gml:17-20``）::

        url  = ".../signup.php?nickname=" + global.PLAYERNAME
        url += "&password=" + md5(md5(name+name) + md5(name+"sora") + md5(name+"game"))
        h = http_get(url)

    返回值被 ``real()`` 转成数字存进 ``global.ONLINE_ID``。
    ``ONLINE_ID == 0`` 在客户端语义是“未登录”，所以服务器**不会**返回 0。
    """
    nickname = sanitize_player_name(arg("nickname"))
    password = arg("password")
    settings = _settings()

    verify = verify_signup(nickname, password)
    if password and not verify.ok:
        # 只记录，不拒绝：不同客户端版本的派生公式可能不同，
        # 而 password 在这里只是“账号键”，不承担鉴权职责。
        logger.info(
            "signup.php 校验串与当前客户端公式不一致（nickname=%s），仍按 password 建档",
            nickname,
        )
    account = _accounts().signup(nickname, password)
    log_request(
        "signup.php",
        nickname=account.nickname,
        online_id=account.online_id,
        strict=settings.strict_auth,
    )
    return text_response(account.online_id)


@bp.route("/DanceRail/update.php", methods=["GET", "POST"])
def update_php() -> Response:
    """上报玩家状态，返回 ``0`` 表示成功。

    客户端调用（``gml_Object_obj_title_Create_0.gml:32-43``）::

        .../update.php?onlineid=..&nickname=..&exp=..&allscore=..
                     &subtitle=..&rating=..&password=<三重 md5>

    注意客户端只在 ``os_type != os_windows`` 时上报，
    因此桌面版玩家不会出现在榜单上，这是原版行为，服务器不做补偿。
    """
    online_id = arg("onlineid") or arg("online_id")
    nickname = arg("nickname")
    exp = arg("exp")
    all_score = arg("allscore")
    subtitle = arg("subtitle")
    rating = arg("rating")
    password = arg("password")
    settings = _settings()

    account = _accounts().get(online_id)
    if account is None:
        # 账号不存在（客户端换了设备但 onlineid 未同步、或数据库被清空）。
        # 旧实现会在这里新建一条重复记录，导致账号表里同一个玩家出现多次。
        # 这里明确拒绝，并提示运维：让玩家重新走 signup.php 即可恢复。
        logger.info("update.php：online_id=%s 不存在，已忽略", online_id)
        return text_response("0")

    verify = verify_update(
        online_id=online_id,
        nickname=nickname or account.nickname,
        exp=exp,
        all_score=all_score,
        subtitle=subtitle,
        rating=rating,
        password=password,
    )
    if not enforce(verify, strict=settings.strict_auth, endpoint="update.php"):
        # 严格模式下拒绝写入，但仍返回 0，避免客户端反复重试刷日志。
        return text_response("0")

    updated = _accounts().update(
        online_id=online_id,
        nickname=nickname or None,
        exp=exp or None,
        all_score=all_score or None,
        subtitle=subtitle or None,
        rating=rating or None,
    )
    if updated is not None:
        log_request(
            "update.php",
            online_id=updated.online_id,
            nickname=updated.nickname,
            exp=updated.exp,
            allscore=updated.all_score,
            rating=f"{updated.rating:.2f}",
        )
    return text_response("0")


@bp.route("/DanceRail/cheat_test.php", methods=["GET", "POST"])
def cheat_test_php() -> Response:
    """封禁检查：返回 ``0`` 正常、``1`` 封禁。

    客户端 ``obj_cheat_Create_0`` 只把结果存进 ``g`` 变量；
    真正的“进入封禁画面”由本地 ``ban`` 文件触发。
    为了让服务器端的封禁生效，这里额外提供两种途径：

    * 账号表里 ``banned = true``（可用管理接口或手工编辑 JSON 设置）；
    * 配置 ``Settings.ban_file`` 指向的文件存在（一键全服封禁开关）。

    由于客户端不会主动带上身份，封禁检查只能做到“全局”：带上
    ``onlineid`` 参数时可以精确判断，否则按全局开关返回。
    """
    settings = _settings()
    online_id = arg("onlineid") or arg("online_id")

    banned = False
    reason = ""
    if online_id:
        banned = _accounts().is_banned(online_id)
        reason = "account"
    if not banned and settings.ban_file is not None:
        from pathlib import Path

        if Path(settings.ban_file).exists():
            banned = True
            reason = "global_flag"

    if banned:
        logger.warning("cheat_test.php：返回封禁标记（原因=%s id=%s）", reason, online_id)
    else:
        log_request("cheat_test.php", result=0)
    return text_response("1" if banned else "0")


@bp.route("/DanceRail/usecard.php", methods=["GET", "POST"])
def usecard_php() -> Response:
    """兑换卡：返回可获得的**金币数量**。

    客户端调用（``gml_Object_obj_usecard_Create_0.gml:3``）::

        g = http_get(".../usecard.php?cardid=" + cardid)

    客户端拿到结果后按 ``real()`` 解析：大于 0 就加金币并提示成功，
    否则显示“此卡不可用”。因此服务器用 ``0`` 表示无效卡。

    与旧实现的差异（重要）
    ----------------------

    旧 ``app.py`` 直接把卡号当金币返回（``return str(id)``），
    这意味着输入 ``cardid=99999999`` 就能白拿近一亿金币，属于可利用漏洞。
    现在由 :class:`~drr_server.services.cards.CardService` 按 ``card_mode``
    决定发放策略（默认 ``auto``：登记卡按面额、未登记数字卡按
    ``card_coin_default``、其余返回 0）。需要完全复刻旧行为时，
    把 ``DRR_CARD_MODE`` 设为 ``legacy`` 即可。
    """
    card_id = arg("cardid")
    settings = _settings()
    result = _cards().redeem(card_id)
    log_request("usecard.php", cardid=card_id[:16], coins=result, cap=settings.card_coin_max)
    return text_response(result)
