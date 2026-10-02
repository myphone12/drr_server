"""生产环境启动脚本（waitress）。

用法::

    python run.py

为什么用 waitress 而不是 Flask 自带的开发服务器
------------------------------------------------

客户端在云存档上传/下载时会在**同一帧内并发发起 96 个 GET 请求**
（``original_gamecode/gml_Object_obj_upload_Step_0.gml:83-92``），
整套存档共 90 + 96 × 曲目数 ≈ 12000+ 个请求。
Flask 的 ``app.run()`` 默认单线程，会被这种突发流量直接堵死，
所以生产环境必须用多线程 WSGI 服务器。

环境变量
--------

``DRR_HOST`` / ``DRR_PORT``
    监听地址与端口。**默认 80** —— 客户端把地址硬编码为
    ``http://192.168.0.100/DanceRail/xxx.php``（无端口号，即 80 端口）。
    本机 80 端口被 IIS/其它服务占用时，用 ``DRR_PORT`` 换一个端口，
    再做端口转发，或把客户端重新打包指向新地址。

``DRR_THREADS``
    工作线程数，默认 16。可按并发上传的玩家数量调整。
"""

from __future__ import annotations

import logging
import os
import socket
import sys

from waitress import serve

from drr_server import create_app
from drr_server.config import Settings

DEFAULT_THREADS = 16


def _check_port_available(host: str, port: int) -> bool:
    """粗略检测端口是否已被占用，用于给出更友好的启动提示。"""
    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((probe_host, port))
        except OSError:
            return False
    return True


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log = logging.getLogger("drr_server.run")

    settings = Settings.from_env()
    app = create_app(settings)

    threads = int(os.environ.get("DRR_THREADS", DEFAULT_THREADS))
    log.info(
        "DanceRail Refresh 服务器启动：http://%s:%d/DanceRail/ （线程 %d，数据目录 %s）",
        settings.host,
        settings.port,
        threads,
        settings.data_dir,
    )
    if settings.port != 80:
        log.warning(
            "当前监听端口为 %d，而客户端硬编码访问的是 80 端口；"
            "请确认已做端口转发，或把客户端指向 192.168.0.100:%d。",
            settings.port,
            settings.port,
        )
    if not _check_port_available(settings.host, settings.port):
        log.error(
            "端口 %d 已被占用（常见原因：IIS / Apache / 另一个实例）。"
            "请释放端口或设置环境变量 DRR_PORT 换端口。",
            settings.port,
        )

    try:
        serve(app, host=settings.host, port=settings.port, threads=threads)
    except OSError as exc:
        log.error("绑定 %s:%d 失败：%s", settings.host, settings.port, exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
