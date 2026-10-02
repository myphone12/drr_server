"""接口层（Flask Blueprint）。

所有 URL 都挂在 ``/DanceRail/`` 前缀下 —— 客户端把地址硬编码为
``http://192.168.0.100/DanceRail/<xxx>.php``（见各 ``obj_*`` 的
``http_get`` 调用），因此路径与文件名必须逐字对应。

分组：

``accounts``
    ``date.php`` / ``signup.php`` / ``update.php`` / ``cheat_test.php`` / ``usecard.php``
``ranking``
    ``gethit.php`` / ``getstageclear.php`` / ``gethiexpnew.php`` /
    ``gethiscorenew.php`` / ``gethiratingnew.php``
``savegames``
    ``uploadsavedata.php`` / ``getsavedata.php`` / ``getsavedatanum.php``
``scores``
    ``scoreupload.php`` / ``getscoreone.php``
``views``
    浏览器直接访问的页面：``highscore.view.php`` 与管理页
``misc``
    根路径与 favicon 等杂项
"""

from drr_server.routes.accounts import bp as accounts_bp
from drr_server.routes.misc import bp as misc_bp
from drr_server.routes.ranking import bp as ranking_bp
from drr_server.routes.savegames import bp as savegames_bp
from drr_server.routes.scores import bp as scores_bp
from drr_server.routes.views import bp as views_bp

#: 供应用工厂统一注册的顺序（互不冲突，顺序仅影响调试输出）。
ALL_BLUEPRINTS = (
    accounts_bp,
    ranking_bp,
    savegames_bp,
    scores_bp,
    views_bp,
    misc_bp,
)

__all__ = [
    "ALL_BLUEPRINTS",
    "accounts_bp",
    "misc_bp",
    "ranking_bp",
    "savegames_bp",
    "scores_bp",
    "views_bp",
]
