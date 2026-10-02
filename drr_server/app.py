"""Flask 应用工厂。

为什么要有工厂函数
------------------

旧 ``app.py`` 在模块顶层读写 ``save.json``、定义全局 ``app``，
导致三个问题：无法在测试里构造独立实例、导入即产生副作用、
多进程部署下每个工作进程各持一份内存缓存。

``create_app`` 把这些副作用收进一次显式调用：

1. 读取配置（环境变量 → :class:`~drr_server.config.Settings`）；
2. 建数据目录、初始化各 JSON 存储（:class:`~drr_server.storage.Database`）；
3. 构造业务服务并挂到 ``app.extensions``（不放在模块全局，
   避免测试之间互相污染）；
4. 注册全部蓝图；
5. 注册统一的错误处理，保证**任何情况下都返回 200 + 可解析的纯文本**。

第 5 点尤其重要：客户端的 HTTP 异步事件只看传输层 ``status``，
不看 HTTP 状态码，所以 404/500 的 HTML 错误页也会被当成数据解析，
进而写坏玩家的本地存档（详见 ``docs/API.md`` 的“应答约定”）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, Response

from drr_server.config import Settings
from drr_server.routes import ALL_BLUEPRINTS
from drr_server.routes._helpers import text_response
from drr_server.services.accounts import AccountService
from drr_server.services.cards import CardService
from drr_server.services.ranking import RankingService
from drr_server.services.savegame import SaveGameService
from drr_server.services.scores import ScoreService
from drr_server.storage import Database

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> Flask:
    """构造并返回 Flask 应用实例。

    参数
    ----
    settings:
        显式配置对象；省略时按环境变量构造（便于测试注入临时目录）。
    """
    settings = settings or Settings.from_env()

    app = Flask(__name__, static_folder=None)
    app.config["DRR_SETTINGS"] = settings
    app.config["JSON_AS_ASCII"] = settings.json_as_ascii
    app.config["PROPAGATE_EXCEPTIONS"] = False

    _configure_logging(settings)

    # ------------------------------------------------------------ 数据层
    db = Database(settings)
    migration = db.migrate_legacy_files()
    if any(migration.values()):
        logger.info(
            "旧数据迁移完成：账号 +%d，存档 +%d",
            migration.get("accounts", 0),
            migration.get("saves", 0),
        )
    # 迁移/推断出的曲目数必须回写 settings，否则 ``song_id_limit``
    # 仍按旧值校验，真实曲目编号的成绩会被静默丢弃。
    song_count = db.sync_song_count()
    logger.info(
        "曲目数=%d（配置 %d，编号范围 %d-%d）",
        song_count,
        settings.song_count,
        settings.song_id_base,
        settings.song_id_limit,
    )

    # ------------------------------------------------------------ 业务层
    # 挂到 app.extensions 而不是模块全局变量：测试可并发创建多个应用实例。
    app.extensions["drr_db"] = db
    app.extensions["drr_accounts"] = AccountService(db)
    app.extensions["drr_scores"] = ScoreService(db)
    app.extensions["drr_saves"] = SaveGameService(db)
    app.extensions["drr_cards"] = CardService(db)
    app.extensions["drr_ranking"] = RankingService(db)

    # ------------------------------------------------------------ 接口层
    for blueprint in ALL_BLUEPRINTS:
        app.register_blueprint(blueprint)

    _register_error_handlers(app)
    return app


def _configure_logging(settings: Settings) -> None:
    """配置根日志器（幂等）。

    只在尚未配置时设置，避免与 waitress / gunicorn 的日志配置打架。
    """
    root = logging.getLogger()
    if root.handlers:
        return
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _register_error_handlers(app: Flask) -> None:
    """把一切错误都转成 200 + 安全应答体。

    客户端把非 2xx 的 HTML 页面也当数据读，因此服务器不能依赖
    “返回错误码让它失败”。这里的策略是：

    * 路由未匹配（404）→ 返回 ``0`` 的纯文本；
    * 服务器内部错误（500）→ 记录堆栈，返回 ``0``；
    * 其它 HTTP 错误 → 同样返回 ``0``。

    ``0`` 对客户端来说等于“数值 0 / 失败但可解析”，
    对 ``getsavedata.php`` 这种会被写进本地存档的接口最安全
    （不会写入一坨 HTML）。
    """

    @app.errorhandler(404)
    def _not_found(error):  # noqa: ANN001, ANN202 - Flask 回调签名
        logger.info("未匹配的请求：%s", getattr(error, "description", error))
        return text_response("0")

    @app.errorhandler(405)
    def _method_not_allowed(error):  # noqa: ANN001, ANN202
        logger.info("方法不被允许：%s", getattr(error, "description", error))
        return text_response("0")

    @app.errorhandler(Exception)
    def _internal_error(error):  # noqa: ANN001, ANN202
        logger.exception("处理请求时发生未捕获异常：%s", error)
        return text_response("0", status=200)

    @app.after_request
    def _add_headers(response: Response) -> Response:
        """补充诊断响应头，方便用浏览器/抓包工具排查。"""
        settings: Settings = app.config["DRR_SETTINGS"]
        db: Database = app.extensions["drr_db"]
        response.headers["X-DRR-Version"] = "2.0.0"
        response.headers["X-DRR-Song-Count"] = str(db.inferred_song_count())
        if not settings.verbose_log:
            response.headers["X-DRR-Quiet"] = "1"
        return response
