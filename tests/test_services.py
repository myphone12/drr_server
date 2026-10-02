"""业务层测试：服务对象的直接调用（不经 HTTP）。"""

from __future__ import annotations

import unittest

from tests.base import AppTestCase
from drr_server.utils import signup_password


class TestAccountService(AppTestCase):
    """账号注册 / 更新 / 封禁。"""

    def service(self):
        return self.app.extensions["drr_accounts"]

    def test_signup_returns_nonzero_id(self) -> None:
        """客户端把 ``ONLINE_ID == 0`` 视为未登录，绝不能返回 0。"""
        account = self.service().signup("PLAYER", signup_password("PLAYER"))
        self.assertNotEqual(account.online_id, "0")
        self.assertEqual(account.nickname, "PLAYER")

    def test_signup_is_idempotent_per_password(self) -> None:
        """同一 password 重复注册必须复用账号，而不是新建。"""
        first = self.service().signup("PLAYER", signup_password("PLAYER"))
        second = self.service().signup("PLAYER", signup_password("PLAYER"))
        self.assertEqual(first.online_id, second.online_id)
        self.assertEqual(len(self.service().all_accounts()), 1)

    def test_signup_ids_are_distinct_for_distinct_players(self) -> None:
        a = self.service().signup("AAA", signup_password("AAA"))
        b = self.service().signup("BBB", signup_password("BBB"))
        self.assertNotEqual(a.online_id, b.online_id)
        self.assertEqual(len(self.service().all_accounts()), 2)

    def test_update_is_monotonic_for_exp_and_score(self) -> None:
        """经验/总分只增不减，避免重装客户端把榜单刷低。"""
        account = self.service().signup("PLAYER", signup_password("PLAYER"))
        self.service().update(
            online_id=account.online_id, exp=500, all_score=1000, rating=11.5
        )
        updated = self.service().get(account.online_id)
        self.assertEqual(updated.exp, 500)
        self.assertEqual(updated.all_score, 1000)

        # 上报一个更小的值：不应覆盖
        self.service().update(
            online_id=account.online_id, exp=10, all_score=5, rating=11.5
        )
        updated = self.service().get(account.online_id)
        self.assertEqual(updated.exp, 500)
        self.assertEqual(updated.all_score, 1000)

    def test_update_rating_may_decrease(self) -> None:
        """Rating 允许下降（成绩变差、榜单重算时）。"""
        account = self.service().signup("PLAYER", signup_password("PLAYER"))
        self.service().update(online_id=account.online_id, rating=12.0)
        self.service().update(online_id=account.online_id, rating=4.25)
        self.assertAlmostEqual(self.service().get(account.online_id).rating, 4.25)

    def test_update_unknown_account_returns_none(self) -> None:
        """旧实现会给未知 online_id 追加重复记录，这里必须拒绝。"""
        self.assertIsNone(self.service().update(online_id="9999", exp=1))

    def test_ban(self) -> None:
        account = self.service().signup("PLAYER", signup_password("PLAYER"))
        self.assertFalse(self.service().is_banned(account.online_id))
        self.assertTrue(self.service().set_banned(account.online_id))
        self.assertTrue(self.service().is_banned(account.online_id))

    def test_level_uses_gml_sqrt(self) -> None:
        """等级 = floor(sqrt(exp))，exp<=1 时为 1。"""
        account = self.service().signup("PLAYER", signup_password("PLAYER"))
        for exp, level in [(1, 1), (3, 1), (4, 2), (8, 2), (9, 3), (120, 10)]:
            self.service().update(online_id=account.online_id, exp=exp)
            current = self.service().get(account.online_id)
            # exp 只增不减，用小值序列依次递增即可
            if current.exp == exp:
                self.assertEqual(current.level, level, f"exp={exp}")


class TestScoreService(AppTestCase):
    """成绩上传、最好成绩、热度、通关率。"""

    def service(self):
        return self.app.extensions["drr_scores"]

    def accounts(self):
        return self.app.extensions["drr_accounts"]

    def _account(self, name: str = "PLAYER") -> str:
        return self.accounts().signup(name, signup_password(name)).online_id

    def test_record_and_best(self) -> None:
        pid = self._account()
        self.service().record_upload(
            online_id=pid, song_id=10138, difficulty=0, hard_level=3, score=95.5
        )
        self.assertAlmostEqual(
            self.service().best_score(pid, 10138, 0, 3), 95.5
        )

    def test_lower_score_does_not_override_best(self) -> None:
        """客户端上传的是历史最好成绩，服务器仍按 max 处理，保证幂等。"""
        pid = self._account()
        self.service().record_upload(
            online_id=pid, song_id=10138, difficulty=0, hard_level=3, score=95.5
        )
        self.service().record_upload(
            online_id=pid, song_id=10138, difficulty=0, hard_level=3, score=80.0
        )
        self.assertAlmostEqual(self.service().best_score(pid, 10138, 0, 3), 95.5)

    def test_rejects_out_of_range_parameters(self) -> None:
        """越界参数不应写库（防止脏数据污染榜单）。"""
        pid = self._account()
        cases = [
            dict(song_id=999, difficulty=0, hard_level=0, score=90),
            dict(song_id=10138, difficulty=9, hard_level=0, score=90),
            dict(song_id=10138, difficulty=0, hard_level=9, score=90),
            dict(song_id=10138, difficulty=0, hard_level=0, score=-5),
            dict(song_id=10138, difficulty=0, hard_level=0, score=9999),
        ]
        for case in cases:
            self.assertIsNone(self.service().record_upload(online_id=pid, **case))

    def test_rejects_unknown_player(self) -> None:
        self.assertIsNone(
            self.service().record_upload(
                online_id="", song_id=10138, difficulty=0, hard_level=0, score=90
            )
        )

    def test_hit_counts_accumulate_plays(self) -> None:
        pid = self._account()
        for _ in range(3):
            self.service().record_upload(
                online_id=pid, song_id=10138, difficulty=0, hard_level=0, score=90
            )
        self.service().record_upload(
            online_id=pid, song_id=10107, difficulty=1, hard_level=2, score=88
        )
        counts = self.service().song_play_counts()
        self.assertEqual(counts.get(10138), 3)
        self.assertEqual(counts.get(10107), 1)

    def test_clear_rate_is_percentage_of_cleared_charts(self) -> None:
        """通关线为 80 分（与客户端 ``score_to_rank`` 一致）。"""
        pid = self._account()
        # 两个谱面：一个 95（通关）、一个 70（未通关）
        self.service().record_upload(
            online_id=pid, song_id=10138, difficulty=0, hard_level=0, score=95
        )
        self.service().record_upload(
            online_id=pid, song_id=10138, difficulty=0, hard_level=1, score=70
        )
        rates = self.service().song_clear_rates()
        self.assertAlmostEqual(rates[10138], 50.0)

    def test_song_scoreboard_groups_by_hard_level(self) -> None:
        a = self._account("AAA")
        b = self._account("BBB")
        self.service().record_upload(
            online_id=a, song_id=10138, difficulty=2, hard_level=1, score=90
        )
        self.service().record_upload(
            online_id=b, song_id=10138, difficulty=2, hard_level=1, score=99
        )
        boards = self.service().song_scoreboard(10138, 2)
        self.assertEqual(len(boards[1]), 2)
        # 降序排列，第一名应是 99
        self.assertEqual(boards[1][0]["online_id"], b)


class TestSaveGameService(AppTestCase):
    """云存档写入 / 读取 / 计数。"""

    def service(self):
        return self.app.extensions["drr_saves"]

    KEY = "3e9c42f461ce92e304ac3903d4378950"

    def test_upload_then_get(self) -> None:
        self.assertTrue(self.service().upload(self.KEY, "0101383S", "95.5"))
        self.assertEqual(self.service().get(self.KEY, "0101383S"), "95.5")

    def test_each_upload_is_persisted_immediately(self) -> None:
        """旧实现只在条目数等于一个算错的总数时才写盘，进程退出即丢失。"""
        self.service().upload(self.KEY, "0101383S", "1")
        # 直接读磁盘文件，确认已落盘而不是只留在内存。
        raw = self.db.saves_file.read_text(encoding="utf-8")
        self.assertIn("0101383S", raw)

    def test_missing_key_returns_default_not_error(self) -> None:
        """缺失条目必须返回安全默认值，绝不能 500（否则会写坏客户端存档）。"""
        self.assertEqual(self.service().get("a" * 32, "0101383S"), "0")
        # ``.F`` 是音符总数，客户端默认值为 1（否则会除零）。
        self.assertEqual(self.service().get(self.KEY, "0101383F"), "1")
        self.assertEqual(self.service().get(self.KEY, "playerexp"), "0")

    def test_dlkey_is_case_insensitive(self) -> None:
        upper = self.KEY.upper()
        self.service().upload(upper, "0101383S", "42")
        self.assertEqual(self.service().get(self.KEY, "0101383S"), "42")
        self.assertEqual(self.service().count(self.KEY), 1)

    def test_invalid_parameters_rejected(self) -> None:
        self.assertFalse(self.service().upload("short", "0101383S", "1"))
        self.assertFalse(self.service().upload(self.KEY, "bad name!", "1"))
        self.assertEqual(self.service().count("short"), 0)

    def test_count_and_progress(self) -> None:
        for index in range(5):
            self.service().upload(self.KEY, f"010138{index}S", str(index))
        self.assertEqual(self.service().count(self.KEY), 5)
        progress = self.service().progress(self.KEY)
        self.assertEqual(progress["entries"], 5)
        self.assertEqual(progress["songs_seen"], 1)
        self.assertFalse(progress["complete"])
        # 期望条目数 = 90 固定 + 曲数 * 24 组合 * 4 字段
        expected = (
            self.settings.base_save_entries
            + self.settings.song_count * 24 * 4
        )
        self.assertEqual(progress["expected_entries"], expected)

    def test_online_id_recovered_from_save(self) -> None:
        self.service().upload(self.KEY, "onlineid", "7")
        self.assertEqual(self.service().online_id_of(self.KEY), "7")
        self.assertEqual(self.service().online_id_of("b" * 32), "")


class TestCardService(AppTestCase):
    """兑换卡发放策略。"""

    def service(self):
        return self.app.extensions["drr_cards"]

    def test_auto_mode_grants_default_for_numeric_card(self) -> None:
        self.assertEqual(self.service().redeem("12345678"), "10")

    def test_invalid_card_returns_negative(self) -> None:
        """客户端只在 ``h < 0`` 时提示失败，返回 0 会让对话框卡死。"""
        for card in ["", "zz", "!!", "x" * 40]:
            self.assertEqual(self.service().redeem(card), "-1")

    def test_whitelist_mode_rejects_unregistered(self) -> None:
        self.settings.card_mode = "whitelist"
        self.assertEqual(self.service().redeem("12345678"), "-1")
        self.service().register_card("12345678", 250)
        self.assertEqual(self.service().redeem("12345678"), "250")

    def test_legacy_mode_returns_card_number_capped(self) -> None:
        self.settings.card_mode = "legacy"
        self.assertEqual(self.service().redeem("7"), "7")
        # 上限保护：不能靠输入大数刷金币
        self.assertEqual(
            self.service().redeem("99999999"), str(self.settings.card_coin_max)
        )

    def test_single_use_mode(self) -> None:
        self.settings.cards_single_use = True
        self.assertEqual(self.service().redeem("12345678"), "10")
        self.assertEqual(self.service().redeem("12345678"), "-1")


class TestRankingService(AppTestCase):
    """排行榜字段顺序与补位。"""

    def service(self):
        return self.app.extensions["drr_ranking"]

    def accounts(self):
        return self.app.extensions["drr_accounts"]

    def _seed(self) -> None:
        for index in range(3):
            name = f"P{index}"
            account = self.accounts().signup(name, signup_password(name))
            self.accounts().update(
                online_id=account.online_id,
                exp=(index + 1) * 100,
                all_score=(index + 1) * 1000,
                rating=float(index + 1),
            )

    def test_exp_leaderboard_field_order(self) -> None:
        """``gethiexpnew.php``：名字, rating, exp。"""
        from drr_server.utils import phpdata

        self._seed()
        body = self.service().exp_leaderboard()
        # 第一名是 exp 最大的 P2
        self.assertEqual(phpdata(body, 0), "P2")
        self.assertEqual(phpdata(body, 1), "3.00")
        self.assertEqual(phpdata(body, 2), "300")

    def test_score_leaderboard_field_order(self) -> None:
        """``gethiscorenew.php``：名字, rating, allscore。"""
        from drr_server.utils import phpdata

        self._seed()
        body = self.service().score_leaderboard()
        self.assertEqual(phpdata(body, 0), "P2")
        self.assertEqual(phpdata(body, 1), "3.00")
        self.assertEqual(phpdata(body, 2), "3000")

    def test_rating_leaderboard_field_order_is_reversed(self) -> None:
        """``gethiratingnew.php``：名字, **exp**, rating —— 顺序与另外两张相反。"""
        from drr_server.utils import phpdata

        self._seed()
        body = self.service().rating_leaderboard()
        self.assertEqual(phpdata(body, 0), "P2")
        self.assertEqual(phpdata(body, 1), "300")  # exp
        self.assertEqual(phpdata(body, 2), "3.00")  # rating

    def test_leaderboards_are_padded_to_ten_rows(self) -> None:
        """客户端固定读 10 行，不足必须补空。"""
        self._seed()
        for method in (
            self.service().exp_leaderboard,
            self.service().score_leaderboard,
            self.service().rating_leaderboard,
        ):
            body = method()
            self.assertEqual(body.count(">"), 30, method.__name__)

    def test_banned_accounts_excluded(self) -> None:
        from drr_server.utils import phpdata

        self._seed()
        accounts = self.accounts()
        target = accounts.find_by_nickname("P2")
        accounts.set_banned(target.online_id, True)
        body = self.service().rating_leaderboard()
        self.assertNotEqual(phpdata(body, 0), "P2")

    def test_hit_ranking_has_placeholder_first_pair(self) -> None:
        """第一组是占位符（客户端会把它归零），其后才是真实曲目。"""
        from drr_server.utils import phpdata

        body = self.service().hit_ranking()
        self.assertEqual(phpdata(body, 0), "0")
        self.assertEqual(phpdata(body, 1), "0")
        # 占位 1 组 + ``song_count`` 首曲子
        self.assertEqual(body.count(">"), (1 + self.settings.song_count) * 2)

    def test_stage_clear_list_length(self) -> None:
        body = self.service().stage_clear_list()
        self.assertEqual(body.count(">"), self.settings.song_count)
        self.assertEqual(body.startswith(">"), True)


if __name__ == "__main__":
    unittest.main()
