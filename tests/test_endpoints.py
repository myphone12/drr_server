"""HTTP 层测试：按客户端真实请求形态打通全部接口。

每个接口的用例都注明了对应的客户端调用点，
并用 ``phpdata``（客户端解析器）校验应答体，
确保字段顺序与切分方式与游戏一致。
"""

from __future__ import annotations

import datetime
import unittest

from tests.base import AppTestCase
from drr_server.utils import (
    md5_hex,
    phpdata,
    score_upload_password,
    signup_password,
    update_password,
)


class TestAccountEndpoints(AppTestCase):
    """``date.php`` / ``signup.php`` / ``update.php`` / ``cheat_test.php`` / ``usecard.php``。"""

    def test_date_returns_yyyymmdd(self) -> None:
        """``obj_timer_Create_0.gml:3``：结果会被 ``real()`` 转换，必须是纯数字。"""
        body = self.get("/DanceRail/date.php")
        self.assertTrue(body.isdigit(), body)
        self.assertEqual(len(body), 8)
        now = datetime.datetime.now()
        self.assertEqual(int(body[:4]), now.year)
        self.assertEqual(int(body[4:6]), now.month)

    def test_signup_returns_usable_id(self) -> None:
        body = self.get(
            "/DanceRail/signup.php",
            nickname="PLAYER",
            password=signup_password("PLAYER"),
        )
        self.assertTrue(body.isdigit(), body)
        self.assertNotEqual(body, "0")

    def test_signup_same_nickname_returns_same_id(self) -> None:
        """客户端启动时只要 ``ONLINE == 0`` 就会重新 signup，必须幂等。"""
        first = self.get(
            "/DanceRail/signup.php",
            nickname="PLAYER",
            password=signup_password("PLAYER"),
        )
        second = self.get(
            "/DanceRail/signup.php",
            nickname="PLAYER",
            password=signup_password("PLAYER"),
        )
        self.assertEqual(first, second)

    def test_signup_without_password_still_works(self) -> None:
        """password 缺失时退化为按昵称派生，保证接口不会 500。"""
        body = self.get("/DanceRail/signup.php", nickname="NOBODY")
        self.assertTrue(body.isdigit())
        self.assertNotEqual(body, "0")

    def test_update_applies_and_returns_zero(self) -> None:
        online_id = self.register("UPDATER")
        body = self.get(
            "/DanceRail/update.php",
            onlineid=online_id,
            nickname="UPDATER",
            exp="250",
            allscore="5000",
            subtitle="3",
            rating="7.50",
            password=update_password(
                online_id, "UPDATER", "250", "5000", "3", "7.50"
            ),
        )
        self.assertEqual(body, "0")
        account = self.app.extensions["drr_accounts"].get(online_id)
        self.assertEqual(account.exp, 250)
        self.assertEqual(account.all_score, 5000)
        self.assertEqual(account.subtitle, 3)
        self.assertAlmostEqual(account.rating, 7.5)

    def test_update_unknown_online_id_is_harmless(self) -> None:
        """未知账号不能抛异常，也不能新建重复记录。"""
        before = len(self.app.extensions["drr_accounts"].all_accounts())
        body = self.get("/DanceRail/update.php", onlineid="4242", exp="10")
        self.assertEqual(body, "0")
        self.assertEqual(
            len(self.app.extensions["drr_accounts"].all_accounts()), before
        )

    def test_update_accepts_gml_formatted_numbers(self) -> None:
        """exp/allscore/rating 常带两位小数，校验串要按原始字符串复刻。"""
        online_id = self.register("FLOATY")
        params = dict(
            onlineid=online_id,
            nickname="FLOATY",
            exp="100.00",
            allscore="1200.50",
            subtitle="0",
            rating="12.30",
        )
        params["password"] = update_password(
            online_id, "FLOATY", "100.00", "1200.50", "0", "12.30"
        )
        self.assertEqual(self.get("/DanceRail/update.php", **params), "0")
        account = self.app.extensions["drr_accounts"].get(online_id)
        self.assertEqual(account.exp, 100)
        self.assertAlmostEqual(account.all_score, 1200.5)

    def test_cheat_test_normal(self) -> None:
        """``obj_cheat_Other_62``：``>= 1`` 才会跳封禁画面。"""
        self.assertEqual(self.get("/DanceRail/cheat_test.php"), "0")

    def test_cheat_test_banned_account(self) -> None:
        online_id = self.register("CHEATER")
        self.app.extensions["drr_accounts"].set_banned(online_id, True)
        self.assertEqual(
            self.get("/DanceRail/cheat_test.php", onlineid=online_id), "1"
        )

    def test_usecard_positive_for_valid_card(self) -> None:
        body = self.get("/DanceRail/usecard.php", cardid="12345678")
        self.assertTrue(int(body) > 0, body)

    def test_usecard_negative_for_invalid_card(self) -> None:
        """客户端 ``h == 0`` 会让对话框卡死，失败必须是负数。"""
        body = self.get("/DanceRail/usecard.php", cardid="zzzzzzzz")
        self.assertTrue(int(body) < 0, body)


class TestRankingEndpoints(AppTestCase):
    """三个榜单 + 热度 + 通关率。"""

    def _seed_players(self, count: int = 3) -> None:
        for index in range(count):
            name = f"R{index}"
            online_id = self.register(name)
            self.get(
                "/DanceRail/update.php",
                onlineid=online_id,
                nickname=name,
                exp=str((index + 1) * 100),
                allscore=str((index + 1) * 1000),
                subtitle="0",
                rating=str(float(index + 1)),
                password=update_password(
                    online_id, name, str((index + 1) * 100),
                    str((index + 1) * 1000), "0", str(float(index + 1)),
                ),
            )

    def test_exp_leaderboard_shape(self) -> None:
        self._seed_players()
        body = self.get("/DanceRail/gethiexpnew.php")
        self.assertEqual(body.count(">"), 30)
        self.assertEqual(phpdata(body, 0), "R2")
        self.assertEqual(phpdata(body, 1), "3.00")
        self.assertEqual(phpdata(body, 2), "300")

    def test_score_leaderboard_shape(self) -> None:
        self._seed_players()
        body = self.get("/DanceRail/gethiscorenew.php")
        self.assertEqual(body.count(">"), 30)
        self.assertEqual(phpdata(body, 0), "R2")
        self.assertEqual(phpdata(body, 2), "3000")

    def test_rating_leaderboard_shape(self) -> None:
        self._seed_players()
        body = self.get("/DanceRail/gethiratingnew.php")
        self.assertEqual(body.count(">"), 30)
        # 名字, exp, rating
        self.assertEqual(phpdata(body, 0), "R2")
        self.assertEqual(phpdata(body, 1), "300")
        self.assertEqual(phpdata(body, 2), "3.00")

    def test_leaderboards_never_contain_forbidden_characters(self) -> None:
        """应答体必须是单行、无 ``#``、无 ``<br>``（客户端不会剔除）。"""
        self._seed_players()
        for path in (
            "/DanceRail/gethiexpnew.php",
            "/DanceRail/gethiscorenew.php",
            "/DanceRail/gethiratingnew.php",
            "/DanceRail/getstageclear.php",
        ):
            body = self.get(path)
            self.assertNotIn("\n", body, path)
            self.assertNotIn("\r", body, path)
            self.assertNotIn("<br>", body, path)
            self.assertNotIn("#", body, path)

    def test_gethit_pairs_are_even(self) -> None:
        """``string_count(">") / 2`` 必须整除，否则客户端会读到半条数据。"""
        body = self.get("/DanceRail/gethit.php", song_num=self.settings.song_count)
        self.assertEqual(body.count(">") % 2, 0)
        # 占位 1 组 + song_count 首
        self.assertEqual(body.count(">"), (1 + self.settings.song_count) * 2)
        self.assertEqual(phpdata(body, 0), "0")

    def test_gethit_reflects_uploaded_scores(self) -> None:
        online_id = self.register("HOT")
        for _ in range(2):
            self.get(
                "/DanceRail/scoreupload.php",
                id=online_id,
                song_id="10138",
                game_dif="0",
                song_hard="0",
                deathmode="0",
                date="20251021",
                time="90503",
                score="95.5",
                password=score_upload_password(
                    online_id, "10138", "0", "0", "0", "20251021", "90503", "95.5"
                ),
            )
        body = self.get("/DanceRail/gethit.php", song_num=self.settings.song_count)
        # 第二组是曲目编号 10000（song_id_base），第十组是 10138
        target_index = 1 + (10138 - self.settings.song_id_base)
        self.assertEqual(phpdata(body, target_index * 2), "10138")
        self.assertEqual(phpdata(body, target_index * 2 + 1), "2")

    def test_stage_clear_length_and_percent(self) -> None:
        body = self.get("/DanceRail/getstageclear.php")
        self.assertEqual(body.count(">"), self.settings.song_count)
        for index in range(self.settings.song_count):
            value = phpdata(body, index)
            self.assertTrue(value.isdigit(), value)
            self.assertLessEqual(int(value), 100)


class TestSaveGameEndpoints(AppTestCase):
    """云存档三件套。"""

    KEY = "3e9c42f461ce92e304ac3903d4378950"

    def test_upload_returns_exactly_one(self) -> None:
        """客户端用精确相等判断上传完成，必须逐字返回 ``1``。"""
        body = self.get(
            "/DanceRail/uploadsavedata.php",
            player=self.KEY,
            dataname="0101383S",
            data="95.5",
        )
        self.assertEqual(body, "1")

    def test_download_roundtrip(self) -> None:
        self.get(
            "/DanceRail/uploadsavedata.php",
            player=self.KEY,
            dataname="playerexp",
            data="1234",
        )
        self.assertEqual(
            self.get("/DanceRail/getsavedata.php", player=self.KEY, dataname="playerexp"),
            "1234",
        )

    def test_download_unknown_key_returns_zero_not_error(self) -> None:
        """返回 HTML 错误页会被客户端写进本地存档，必须返回数字。"""
        body = self.get(
            "/DanceRail/getsavedata.php", player="c" * 32, dataname="playerexp"
        )
        self.assertEqual(body, "0")

    def test_download_default_for_note_count_field(self) -> None:
        """``.F``（音符总数）默认应为 1，否则客户端会除零。"""
        body = self.get(
            "/DanceRail/getsavedata.php", player="c" * 32, dataname="0101383F"
        )
        self.assertEqual(body, "1")

    def test_savedatanum(self) -> None:
        for index in range(4):
            self.get(
                "/DanceRail/uploadsavedata.php",
                player=self.KEY,
                dataname=f"010138{index}S",
                data="0",
            )
        self.assertEqual(
            self.get("/DanceRail/getsavedatanum.php", player=self.KEY), "4"
        )
        self.assertEqual(
            self.get("/DanceRail/getsavedatanum.php", player="d" * 32), "0"
        )

    def test_large_upload_is_persisted(self) -> None:
        """模拟客户端逐条上传（每曲 96 条），确认全部落盘。"""
        for hard in range(6):
            for field in ("S", "F", "C", "D"):
                for difficulty in range(4):
                    self.get(
                        "/DanceRail/uploadsavedata.php",
                        player=self.KEY,
                        dataname=f"{difficulty}10138{hard}{field}",
                        data="1",
                    )
        self.assertEqual(
            self.get("/DanceRail/getsavedatanum.php", player=self.KEY), "96"
        )
        raw = self.db.saves_file.read_text(encoding="utf-8")
        self.assertIn("0101383S", raw)


class TestScoreEndpoints(AppTestCase):
    """``scoreupload.php`` 与 ``getscoreone.php``。"""

    def _upload(self, online_id: str, score: str, **overrides) -> str:
        params = dict(
            id=online_id,
            song_id="10138",
            game_dif="1",
            song_hard="2",
            deathmode="0",
            date="20251021",
            time="90503",
            score=score,
        )
        params.update(overrides)
        params["password"] = score_upload_password(
            params["id"],
            params["song_id"],
            params["game_dif"],
            params["song_hard"],
            params["deathmode"],
            params["date"],
            params["time"],
            params["score"],
        )
        return self.get("/DanceRail/scoreupload.php", **params)

    def test_score_upload_records_best(self) -> None:
        online_id = self.register("SCORER")
        self._upload(online_id, "88")
        best = self.app.extensions["drr_scores"].best_score(online_id, 10138, 1, 2)
        self.assertAlmostEqual(best, 88.0)

    def test_score_upload_max_is_idempotent(self) -> None:
        """客户端上传的是历史最好成绩，重复上传不应改变结果。"""
        online_id = self.register("SCORER")
        self._upload(online_id, "88")
        self._upload(online_id, "88")
        best = self.app.extensions["drr_scores"].best_score(online_id, 10138, 1, 2)
        self.assertAlmostEqual(best, 88.0)

    def test_score_upload_rejects_unknown_player(self) -> None:
        """未知账号的成绩不应进入榜单。"""
        self._upload("9999", "99")
        state = self.app.extensions["drr_scores"]._state()
        self.assertNotIn("9999", state["players"])

    def test_getscoreone_returns_percentage(self) -> None:
        """旧流程把返回值写进 ``.S`` 并硬编码 ``F = 100``，所以给百分比。"""
        online_id = self.register("SCORER")
        self._upload(online_id, "93.25")
        body = self.get(
            "/DanceRail/getscoreone.php",
            user_id=online_id,
            nickname="SCORER",
            song_id="10138",
            game_dif="1",
            song_hard="2",
        )
        self.assertEqual(body, "93.25")

    def test_getscoreone_without_record_returns_zero(self) -> None:
        body = self.get(
            "/DanceRail/getscoreone.php",
            user_id="1",
            song_id="10138",
            game_dif="0",
            song_hard="0",
        )
        self.assertEqual(float(body), 0.0)

    def test_strict_auth_rejects_bad_password(self) -> None:
        """打开严格验签后，篡改校验串必须被拒绝。"""
        self.settings.strict_auth = True
        online_id = self.register("SCORER")
        self.get(
            "/DanceRail/scoreupload.php",
            id=online_id,
            song_id="10138",
            game_dif="0",
            song_hard="0",
            deathmode="0",
            date="20251021",
            time="90503",
            score="99",
            password="0" * 32,
        )
        best = self.app.extensions["drr_scores"].best_score(online_id, 10138, 0, 0)
        self.assertEqual(best, 0.0)


class TestViewEndpoints(AppTestCase):
    """浏览器页面与非接口路径。"""

    def test_highscore_page_renders(self) -> None:
        response = self.client.get(
            "/DanceRail/highscore.view.php", query_string={"song_id": 10138, "game_dif": 2}
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("10138", body)
        self.assertIn("<html", body.lower())

    def test_highscore_page_with_scores(self) -> None:
        online_id = self.register("PAGED")
        accounts = self.app.extensions["drr_accounts"]
        accounts.update(online_id=online_id, exp=10)
        self.app.extensions["drr_scores"].record_upload(
            online_id=online_id, song_id=10138, difficulty=2, hard_level=4, score=97.5
        )
        response = self.client.get(
            "/DanceRail/highscore.view.php", query_string={"song_id": 10138, "game_dif": 2}
        )
        body = response.get_data(as_text=True)
        self.assertIn("PAGED", body)
        self.assertIn("97.50", body)

    def test_admin_page_renders(self) -> None:
        self.register("ADMINVIEW")
        response = self.client.get("/DanceRail/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn("ADMINVIEW", response.get_data(as_text=True))

    def test_clock_js_served(self) -> None:
        response = self.client.get("/DanceRail/clock.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("data-drr-clock", response.get_data(as_text=True))

    def test_root_returns_text(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("DanceRail", response.get_data(as_text=True))

    def test_unknown_endpoint_returns_zero_not_500(self) -> None:
        """旧实现在未匹配时返回 ``None``，会变成 500 的 HTML 错误页。"""
        response = self.client.get("/DanceRail/does_not_exist.php")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), "0")

    def test_legacy_path_dispatch(self) -> None:
        """``/DanceRail/<data>`` 兜底路由应把已知脚本名转发到真正的处理函数。"""
        body = self.get("/DanceRail/date.php")
        self.assertEqual(len(body), 8)

    def test_unknown_legacy_path_returns_zero(self) -> None:
        response = self.client.get("/DanceRail/DanceRail/nothing.php")
        self.assertEqual(response.status_code, 200)

    def test_all_responses_are_plain_text(self) -> None:
        """客户端把响应体当字符串读，不能是 JSON/HTML。"""
        for path in (
            "/DanceRail/date.php",
            "/DanceRail/gethit.php",
            "/DanceRail/getstageclear.php",
            "/DanceRail/gethiexpnew.php",
            "/DanceRail/cheat_test.php",
        ):
            response = self.client.get(path)
            self.assertTrue(
                response.headers["Content-Type"].startswith("text/plain"),
                f"{path} -> {response.headers['Content-Type']}",
            )


class TestMigration(AppTestCase):
    """旧 ``data.json`` / ``save.json`` 的迁移。"""

    def test_legacy_account_array_is_migrated(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from drr_server.app import create_app
        from drr_server.config import Settings

        with tempfile.TemporaryDirectory(prefix="drr-migrate-") as tmp:
            root = Path(tmp)
            legacy_accounts = root / "data.json"
            legacy_accounts.write_text(
                json.dumps(
                    [
                        {
                            "allscore": "100",
                            "exp": "5",
                            "nickname": "PLAYER111",
                            "onlineid": "10",
                            "password": "7eb89631c582d34c7a1114d551ef33c2",
                            "rating": "0.05",
                            "subtitle": "0",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            legacy_saves = root / "save.json"
            legacy_saves.write_text(
                json.dumps({"a" * 32: {"0101383S": "0"}}), encoding="utf-8"
            )

            settings = Settings(
                data_dir=root / "data",
                legacy_accounts_file=legacy_accounts,
                legacy_save_file=legacy_saves,
                migrate_legacy_data=True,
                verbose_log=False,
            )
            app = create_app(settings)
            accounts = app.extensions["drr_accounts"]
            account = accounts.get("10")
            self.assertIsNotNone(account)
            self.assertEqual(account.nickname, "PLAYER111")
            self.assertEqual(account.exp, 5)
            self.assertAlmostEqual(account.rating, 0.05)
            saves = app.extensions["drr_saves"]
            self.assertEqual(saves.count("a" * 32), 1)

            # 幂等：再次初始化不应重复导入
            app2 = create_app(settings)
            self.assertEqual(
                len(app2.extensions["drr_accounts"].all_accounts()), 1
            )


if __name__ == "__main__":
    unittest.main()
