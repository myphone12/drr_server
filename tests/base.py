"""测试公共设施：构造使用临时目录的应用实例。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# 允许直接 ``python -m unittest discover -s tests`` 运行时找到包。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from drr_server.app import create_app  # noqa: E402
from drr_server.config import Settings  # noqa: E402


class AppTestCase(unittest.TestCase):
    """带临时数据目录的应用测试基类。

    每个测试方法都会拿到全新的 ``Settings``（指向新建临时目录），
    因此测试之间不会互相污染，也不会碰到真实的 ``data/`` 目录。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="drr-test-")
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name) / "data"
        self.settings = Settings(
            data_dir=self.data_dir,
            # 迁移指向不存在的位置，保证测试不受仓库里的旧文件影响。
            legacy_accounts_file=Path(self._tmp.name) / "nonexistent-data.json",
            legacy_save_file=Path(self._tmp.name) / "nonexistent-save.json",
            migrate_legacy_data=False,
            verbose_log=False,
            # 取 139 首是为了覆盖仓库真实 ``save.json`` 里出现过的
            # 最大曲目编号 10138（song_id_limit = 10000 + 139 - 1 = 10138），
            # 同时保持用例里的曲目数足够小、跑得快。
            song_count=139,
            song_id_base=10000,
        )
        self.app = create_app(self.settings)
        self.client = self.app.test_client()
        self.db = self.app.extensions["drr_db"]

    # ------------------------------------------------------------------ 工具
    def get(self, path: str, **params) -> str:
        """发起 GET 请求并返回响应正文。"""
        response = self.client.get(path, query_string=params)
        self.assertEqual(
            response.status_code,
            200,
            f"{path} 返回了非 200 状态码：{response.status_code}",
        )
        return response.get_data(as_text=True)

    def register(self, nickname: str = "PLAYER", password: str = "") -> str:
        """注册一个账号并返回 ``online_id``。"""
        from drr_server.utils import signup_password

        return self.get(
            "/DanceRail/signup.php",
            nickname=nickname,
            password=password or signup_password(nickname),
        )
