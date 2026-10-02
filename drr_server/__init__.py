"""DanceRail Refresh 私有服务器（Flask 实现）。

本包把原先集中在单个 ``app.py`` 里的逻辑拆分为分层结构：

``drr_server.config``
    运行期配置（监听地址、数据目录、玩法参数）。
``drr_server.storage``
    轻量 JSON 持久化层，负责加锁、原子写入与旧数据迁移。
``drr_server.services``
    业务层：账号、排行榜、存档、成绩。路由只做参数解析与格式拼装。
``drr_server.routes``
    接口层（Flask Blueprint），按客户端调用场景分组。
``drr_server.utils``
    与游戏客户端格式强相关的纯函数（``>`` 分隔协议、md5 校验串等）。

对外只暴露 :func:`create_app`，其余模块由应用工厂按需导入。
"""

from drr_server.app import create_app

__all__ = ["create_app"]
__version__ = "2.0.0"
