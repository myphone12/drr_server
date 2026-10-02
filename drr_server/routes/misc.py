"""杂项路由：根路径、旧版兼容入口。

这些路径不是客户端正式调用的接口，但保留它们可以：

* 让运维用浏览器直接访问根路径时看到可读的说明（而不是一堆报错）；
* 兼容旧 ``app.py`` 那种 ``/DanceRail/<任意文件名>`` 的调用方式
  （例如有人把客户端地址改成了别的脚本名）；
* 提供 favicon，避免浏览器反复请求产生 404 噪声。
"""

from __future__ import annotations

import logging

from flask import Blueprint, Response, current_app

from drr_server.routes._helpers import resolve_handler, text_response

logger = logging.getLogger(__name__)

bp = Blueprint("drr_misc", __name__)


@bp.route("/", methods=["GET"])
def index() -> Response:
    """根路径：返回简短的纯文本说明。

    刻意不返回 HTML —— 这个路径可能被客户端误当作某个 ``.php`` 接口调用，
    纯文本更容易被发现“地址配错了”。
    """
    settings = current_app.config["DRR_SETTINGS"]
    return text_response(
        "DanceRail Refresh private server is running. "
        f"Data dir: {settings.data_dir}. "
        "Endpoints are under /DanceRail/*.php; "
        "see /DanceRail/admin for status."
    )


@bp.route("/favicon.ico", methods=["GET"])
def favicon() -> Response:
    """返回 204，避免浏览器日志里出现无意义的 404。"""
    return Response(status=204)


@bp.route("/DanceRail/", methods=["GET"])
def dance_rail_root() -> Response:
    """``/DanceRail/`` 本身没有对应接口，给出与根路径一致的信息。"""
    return index()


@bp.route("/DanceRail/<path:data>", methods=["GET", "POST"])
def legacy_dispatch(data: str) -> Response:
    """旧式“文件名写在路径里”的兜底分发。

    旧 ``app.py`` 用 ``@app.route("/DanceRail/<data>")`` + 一串
    ``data.startswith("xxx.php")`` 实现，未匹配时返回 ``None``，
    Flask 会抛出 ``TypeError`` 并回 500（客户端把错误页当数据读）。

    这里改为：把 ``data`` 归一化后查表转发到真正的处理函数；
    仍然未匹配时返回 ``0`` 而不是抛异常。
    """
    settings = current_app.config["DRR_SETTINGS"]
    mapping = _handler_map()
    handler, name = resolve_handler(mapping)
    if handler is not None:
        if settings.verbose_log:
            logger.info("旧式路径 %s 转发到 %s", data, name)
        return handler()

    if settings.verbose_log:
        logger.info("收到未知接口请求：%s", data)
    return text_response("0")


def _handler_map() -> dict:
    """构造 ``脚本名 → 视图函数`` 的映射表。

    使用 Flask 自身的 URL 映射（``app.url_map``）而不是手写字典，
    这样新增接口时不用同步维护两份清单。
    """
    app = current_app
    mapping: dict[str, object] = {}
    for rule in app.url_map.iter_rules():
        path = str(rule)
        if not path.startswith("/DanceRail/"):
            continue
        name = path[len("/DanceRail/") :]
        if not name or "<" in name:
            # 跳过带参数的规则（包括本兜底路由自身）。
            continue
        view = app.view_functions.get(rule.endpoint)
        if view is None:
            continue
        mapping[name.lower()] = view
    return mapping
