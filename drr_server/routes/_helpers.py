"""接口层共用小工具。

主要解决三件事：

1. **纯文本应答**：客户端用 ``http_get`` 读字符串，任何 HTML 包裹都会污染数据。
2. **参数提取**：客户端只发 GET + query string，取值失败不应抛 500，
   而应回退到安全默认值，让玩家侧流程能走完。
3. **按文件后缀分发的路由**：``/DanceRail/<data>`` 这种“文件名在路径里”的
   设计（原版 PHP 就是这样）用一个显式映射表实现，比一串 ``startswith`` 清晰。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from flask import Request, Response, current_app, request

logger = logging.getLogger(__name__)

#: 客户端请求的 ``User-Agent`` 是 GameMaker 的默认串，仅用于日志。
_TEXT_MIME = "text/plain; charset=utf-8"


def text_response(body: object, status: int = 200) -> Response:
    """构造客户端可安全解析的纯文本应答。

    注意：

    * 不使用 ``jsonify`` —— 客户端只会把响应体当字符串读；
    * 结尾**不加换行** —— 客户端多处直接 ``real(response)``，
      虽然 GameMaker 的 ``real()`` 能容忍空白，但保持零多余字符最稳。
    """
    payload = "" if body is None else str(body)
    response = Response(payload, status=status, mimetype=None)
    response.headers["Content-Type"] = _TEXT_MIME
    # 结果里包含实时榜单，禁止中间层缓存。
    response.headers["Cache-Control"] = "no-store"
    return response


def arg(name: str, default: str = "") -> str:
    """读取查询参数（GET 或表单），返回去除首尾空白的字符串。"""
    value = request.args.get(name)
    if value is None:
        # 少数客户端版本可能用 POST form 提交。
        value = request.form.get(name)
    if value is None:
        return default
    return str(value).strip()


def arg_int(name: str, default: int = 0) -> int:
    """读取整型查询参数；解析失败返回 ``default``。"""
    from drr_server.utils import to_int

    return to_int(arg(name), default)


def log_request(endpoint: str, **fields: object) -> None:
    """按配置输出一条业务日志。

    ``verbose_log`` 关闭时完全静默，避免高并发下刷屏。
    """
    settings = current_app.config.get("DRR_SETTINGS")
    if settings is not None and not settings.verbose_log:
        return
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.info("[%s] %s", endpoint, details)


def resolve_handler(mapping: dict[str, Callable[[], Response]]):
    """把 ``/DanceRail/<data>`` 的 ``data`` 段映射到处理函数。

    客户端请求的 ``data`` 形如 ``date.php``、``gethit.php?song_num=132``。
    在某些 HTTP 客户端/代理组合下 ``data`` 可能带有前导斜杠，
    因此这里先做归一化再查表。

    返回 ``(handler, endpoint_name)``；未匹配时 ``handler`` 为 ``None``。
    """
    data = (request.view_args or {}).get("data", "") if request.view_args else ""
    if not data:
        return None, ""

    # 去掉查询串（正常情况 Flask 已经分离，这里只是防御）。
    name = data.split("?", 1)[0].strip().lstrip("/").lower()
    handler = mapping.get(name)
    if handler is None:
        # 兼容大小写不一致或带额外前缀（例如 static/ 子目录）的写法。
        for key, value in mapping.items():
            if name.endswith(key):
                handler = value
                name = key
                break
    return handler, name


def request_user_agent() -> str:
    """返回请求的 User-Agent（截断），用于调试不同客户端版本。"""
    return (request.headers.get("User-Agent") or "")[:80]
