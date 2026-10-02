"""开发用途的入口：直接跑 Flask 自带服务器。

生产环境请用 ``run.py``（waitress，多线程）。
本文件保留是为了方便本地断点调试：

    python app.py

环境变量与 ``run.py`` 相同；开发模式固定打开调试与热重载。

历史说明：这个文件曾用单文件 ``app.py`` 承载全部逻辑
（一个 ``/DanceRail/<data>`` 路由 + 一串 ``startswith`` 判断、
全局 ``song_num``/``save`` 变量、用 ``try/except`` 控制流程）。
现已拆分为 :mod:`drr_server` 包，本文件只负责启动。
"""

from __future__ import annotations

import logging

from drr_server import create_app
from drr_server.config import Settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    settings = Settings.from_env()
    # 开发模式固定打开调试，便于改代码即生效。
    settings.debug = True
    app = create_app(settings)
    app.run(host=settings.host, port=settings.port, debug=True, use_reloader=True)


if __name__ == "__main__":
    main()
