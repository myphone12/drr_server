"""协议层测试：``>`` 分隔格式与 dataname 编解码。

这些用例的价值在于**用客户端自己的解析算法**（``phpdata``）去读
服务器生成的应答体，从而把“字段顺序写反”这类错误在测试阶段挡住。
"""

from __future__ import annotations

import unittest

from tests.base import PROJECT_ROOT  # noqa: F401  （确保 sys.path 就绪）
from drr_server.utils import (
    count_song_entries,
    decode_song_entry,
    encode_song_entry,
    format_php_fields,
    format_php_pairs,
    gml_number_str,
    md5_hex,
    phpdata,
    sanitize_player_name,
    score_upload_password,
    signup_password,
    to_int,
    update_password,
)


class TestPhpDataProtocol(unittest.TestCase):
    """``>`` 分隔协议。"""

    def test_format_php_fields_has_leading_separator(self) -> None:
        """应答体必须以 ``>`` 开头，否则客户端会丢掉第一个字段。"""
        self.assertEqual(format_php_fields(["a", "b"]), ">a>b")

    def test_no_trailing_separator(self) -> None:
        """不能有多余的尾部 ``>``，否则客户端的字段计数会多一个。"""
        body = format_php_fields([1, 2, 3])
        self.assertFalse(body.endswith(">"))
        self.assertEqual(body.count(">"), 3)

    def test_phpdata_roundtrip(self) -> None:
        """服务器写出的格式可以被客户端解析器逐字段还原。"""
        fields = ["PLAYER", "12.34", "5678", "10001", "7"]
        body = format_php_fields(fields)
        for index, expected in enumerate(fields):
            self.assertEqual(phpdata(body, index), expected, f"第 {index} 个字段")

    def test_phpdata_out_of_range_returns_empty(self) -> None:
        """越界取值返回空串（客户端 ``real("")`` 即为 0）。"""
        body = format_php_fields(["a"])
        self.assertEqual(phpdata(body, 1), "")
        self.assertEqual(phpdata(body, 99), "")

    def test_phpdata_matches_multi_digit_and_negative(self) -> None:
        """多位数与负数不应被切分错位。"""
        body = format_php_pairs([(10000, 0), (10138, 27)])
        self.assertEqual(phpdata(body, 0), "10000")
        self.assertEqual(phpdata(body, 1), "0")
        self.assertEqual(phpdata(body, 2), "10138")
        self.assertEqual(phpdata(body, 3), "27")

    def test_sanitize_removes_separator_and_newlines(self) -> None:
        """含分隔符/换行的昵称不能破坏协议。"""
        name = sanitize_player_name("a>b\nc#d")
        self.assertNotIn(">", name)
        self.assertNotIn("\n", name)
        # ``#`` 在客户端 draw_text 里是换行符，也必须剔除。
        self.assertNotIn("#", name)

    def test_sanitize_blank_falls_back_to_default(self) -> None:
        self.assertEqual(sanitize_player_name("   "), "PLAYER")


class TestGmlNumberFormatting(unittest.TestCase):
    """GML ``string()`` 的数字格式（校验串复刻的关键）。"""

    def test_integral_has_no_decimals(self) -> None:
        self.assertEqual(gml_number_str(100), "100")
        self.assertEqual(gml_number_str("19.00"), "19")
        self.assertEqual(gml_number_str(0), "0")

    def test_fractional_uses_two_decimals(self) -> None:
        self.assertEqual(gml_number_str(100.5), "100.50")
        self.assertEqual(gml_number_str("12.3"), "12.30")
        self.assertEqual(gml_number_str(1.005), "1.00")  # 银行家舍入不参与

    def test_non_numeric_passthrough(self) -> None:
        self.assertEqual(gml_number_str("PLAYER"), "PLAYER")
        self.assertEqual(gml_number_str(""), "")
        self.assertEqual(gml_number_str(None), "")


class TestPasswordFormulas(unittest.TestCase):
    """md5 校验串公式（与客户端源码逐字对齐）。"""

    def test_signup_formula_matches_gml(self) -> None:
        """复刻 ``gml_Object_obj_timer_Create_0.gml:19``。"""
        name = "PLAYER"
        expected = md5_hex(
            md5_hex(name + name) + md5_hex(name + "sora") + md5_hex(name + "game")
        )
        self.assertEqual(signup_password(name), expected)

    def test_update_formula_matches_gml(self) -> None:
        """复刻 ``gml_Object_obj_title_Create_0.gml:41``。"""
        expected = md5_hex(
            md5_hex("7" + "PLAYER" + "sora")
            + md5_hex("100" + "1200" + "game")
            + md5_hex("3" + "12.30" + "2017")
        )
        self.assertEqual(
            update_password("7", "PLAYER", "100", "1200", "3", "12.30"), expected
        )

    def test_score_upload_formula_matches_gml(self) -> None:
        """复刻 ``gml_Object_obj_r_Create_0.gml:108``。

        注意 ``time`` 的小时**不补零**（``9`` 而不是 ``09``），
        这里用客户端真实可能发出的 ``90503`` 验证。
        """
        expected = md5_hex(
            md5_hex("7" + "10138" + "2" + "sora")
            + md5_hex("3" + "0" + "20251021" + "game")
            + md5_hex("90503" + "98.5" + "password")
        )
        self.assertEqual(
            score_upload_password(
                "7", "10138", "2", "3", "0", "20251021", "90503", "98.5"
            ),
            expected,
        )


class TestSaveDataNames(unittest.TestCase):
    """云存档 ``dataname`` 编解码。"""

    #: 取自仓库里真实 ``save.json`` 的键。
    REAL_KEYS = [
        "0101383S",
        "0101384C",
        "0101370F",
        "1101365S",
        "3101070F",
        "2101365D",
        "0101383D",
        "0101370S",
    ]

    def test_decode_real_keys(self) -> None:
        """真实存档键必须全部可解析（旧 ``app.py`` 完全没解析过它们）。"""
        for key in self.REAL_KEYS:
            self.assertIsNotNone(decode_song_entry(key), f"{key} 解析失败")

    def test_decode_known_values(self) -> None:
        self.assertEqual(decode_song_entry("0101383S"), (0, 10138, 3, "S"))
        self.assertEqual(decode_song_entry("3101070F"), (3, 10107, 0, "F"))
        self.assertEqual(decode_song_entry("1101365S"), (1, 10136, 5, "S"))

    def test_encode_matches_client_concat(self) -> None:
        """编码结果必须与客户端 ``string(k)+string(song_id)+string(j)+letter`` 相同。"""
        for difficulty in range(4):
            for hard_level in range(6):
                for field in ("S", "F", "C", "D"):
                    key = encode_song_entry(difficulty, 10138, hard_level, field)
                    self.assertEqual(key, f"{difficulty}10138{hard_level}{field}")
                    self.assertEqual(
                        decode_song_entry(key),
                        (difficulty, 10138, hard_level, field),
                    )

    def test_decode_rejects_garbage(self) -> None:
        for bad in ["", "playerexp", "rating30_5", "0101383X", "010138", "abc"]:
            self.assertIsNone(decode_song_entry(bad), f"{bad} 不应被解析成功")

    def test_count_song_entries(self) -> None:
        counts = count_song_entries(["0101383S", "0101383F", "2101365D"])
        self.assertEqual(counts, {10138: 2, 10136: 1})


class TestNumberParsing(unittest.TestCase):
    """宽松数值解析（客户端会用浮点字符串上传整数）。"""

    def test_to_int(self) -> None:
        self.assertEqual(to_int("5"), 5)
        self.assertEqual(to_int("5.00"), 5)
        self.assertEqual(to_int(5.9), 5)
        self.assertEqual(to_int(""), 0)
        self.assertEqual(to_int(None, -1), -1)
        self.assertEqual(to_int("abc", 7), 7)


if __name__ == "__main__":
    unittest.main()
