"""兑换卡服务（``usecard.php``）。

客户端行为
----------

``gml_Object_obj_usecard_Create_0.gml``::

    cardid = obj_option.input_string;
    g = http_get(".../usecard.php?cardid=" + cardid);

拿到响应后按 ``real()`` 解析：``> 0`` 则视为成功并给玩家加那么多金币，
``<= 0`` 则显示“此卡不可用”。因此在服务器侧，**返回值就是金币面额**。

三种工作模式
------------

旧 ``app.py`` 直接 ``return str(int(cardid))``，即“卡号即面额”，
这在家里玩自己的服务器时最省事，但把卡号输入成 ``99999999``
就会白拿上亿金币。为了兼顾“开箱即用”和“可管控”，这里提供：

``legacy``
    完全复刻旧行为：数字卡按卡号发金币，且限制在 ``card_coin_max`` 以内。
``auto``（默认）
    * 卡表里登记过的卡 → 发登记面额；
    * 未登记但形如 ``1..card_coin_max`` 的数字卡 → 按 ``card_coin_default`` 发放
      并自动登记，方便事后在 ``cards.json`` 里核对与调整；
    * 其它 → 返回 0（不可用）。
``whitelist``
    只认卡表里登记过的卡，其余一律 0。适合公网部署。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from drr_server.storage import Database
from drr_server.utils import clamp, to_int

logger = logging.getLogger(__name__)

# 卡号字符集：实体卡是 8 位十六进制序列号
# （``obj_option_Step_1.gml:487-514`` 把输入限制在 "0123456789abcdef"），
# 这里放宽到 1~32 位字母数字以兼容将来可能的变化。
_CARD_ID_RE = re.compile(r"^[0-9a-zA-Z]{1,32}$")

VALID_MODES = ("legacy", "auto", "whitelist")

#: 卡片不可用时的返回值。客户端 ``obj_usecard_Step_0`` 只在
#: ``h < 0`` 时提示“此卡不可用”，``h == 0`` 会让对话框卡死，
#: 所以失败必须返回负数。
INVALID_CARD = "-1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class CardService:
    """兑换卡发放逻辑。"""

    def __init__(self, db: Database) -> None:
        self.db = db

    # ---------------------------------------------------------------- 公开
    def redeem(self, card_id: object) -> str:
        """兑换一张卡，返回应发放的金币数量字符串。

        返回值语义（对齐客户端 ``obj_usecard_Step_0``）：

        * ``> 0`` —— 成功，客户端执行 ``global.COIN += h`` 并提示 "Get Coin : N"；
        * ``< 0`` —— 失败，客户端显示“此卡不可用”；
        * ``== 0`` —— **客户端对话框会一直停住**，因此服务器绝不返回 0。
        """
        settings = self.db.settings
        raw = str(card_id or "").strip()
        if not raw or not _CARD_ID_RE.match(raw):
            logger.info("usecard.php：卡号格式非法 %r", raw[:16])
            return INVALID_CARD

        mode = getattr(settings, "card_mode", "auto")
        if mode not in VALID_MODES:
            mode = "auto"

        cards = self.db.cards.read()
        entry = cards.get(raw) if isinstance(cards, dict) else None

        # 已使用过的卡：按配置决定是否允许重复兑换。
        if isinstance(entry, dict) and entry.get("used") and settings.cards_single_use:
            logger.info("usecard.php：卡 %s 已被使用过，拒绝重复兑换", raw[:8])
            return INVALID_CARD

        if mode == "whitelist" and not isinstance(entry, dict):
            logger.info("usecard.php：卡 %s 不在白名单中", raw[:8])
            return INVALID_CARD

        coins = self._coins_for(raw, entry, mode)
        if coins <= 0:
            return INVALID_CARD

        coins = clamp(coins, 0, settings.card_coin_max)
        if coins <= 0:
            return INVALID_CARD
        self._register(raw, coins, entry)
        logger.info("usecard.php：卡 %s 发放 %d 金币", raw[:8], coins)
        return str(coins)

    def register_card(self, card_id: str, coins: int, note: str = "") -> None:
        """登记（或修改）一张卡的固定面额，供维护脚本/管理页调用。"""
        self._register(str(card_id), clamp(int(coins), 0, 10**9), None, note=note)

    def list_cards(self) -> dict:
        """返回卡表快照（管理页展示用）。"""
        data = self.db.cards.read()
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------ 内部逻辑
    def _coins_for(self, card_id: str, entry: dict | None, mode: str) -> int:
        """计算某张卡应发放的金币数量。"""
        settings = self.db.settings
        if isinstance(entry, dict):
            registered = to_int(entry.get("coins"), 0)
            if registered > 0:
                return registered
        if mode == "whitelist":
            # 白名单模式下未登记的卡已经在上面被拒绝，走到这里说明登记面额为 0。
            return 0
        if card_id.isdigit():
            if mode == "legacy":
                return to_int(card_id, 0)
            return settings.card_coin_default
        return 0

    def _register(
        self, card_id: str, coins: int, existing: dict | None, note: str = ""
    ) -> None:
        """写入/更新卡表记录。"""
        record = {
            "coins": coins,
            "used": True,
            "used_at": _now_iso(),
        }
        if existing:
            record["used_at"] = existing.get("used_at") or record["used_at"]
            record["uses"] = to_int(existing.get("uses"), 0) + 1
            if existing.get("note"):
                record["note"] = existing["note"]
        else:
            record["uses"] = 1
        if note:
            record["note"] = note

        def _mutate(data: dict) -> None:
            data[card_id] = record

        self.db.cards.update(_mutate)
