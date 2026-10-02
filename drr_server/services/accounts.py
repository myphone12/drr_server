"""账号服务：注册、更新、查询、封禁。

对应客户端接口
--------------

* ``signup.php`` —— 首次上线时用昵称派生的 password 注册，返回数字 ID。
* ``update.php`` —— 每次进入标题画面时上报经验/总分/称号/实力值。
* ``cheat_test.php`` —— 客户端用返回值判断是否被封禁。

客户端调用点
------------

* ``original_gamecode/gml_Object_obj_timer_Create_0.gml:17-20``（注册）
* ``original_gamecode/gml_Object_obj_title_Create_0.gml:32-43``（更新）
* ``original_gamecode/gml_Object_obj_cheat_Create_0.gml:3-4``（封禁检查）
"""

from __future__ import annotations

import logging

from drr_server.models import Account
from drr_server.storage import Database
from drr_server.utils import sanitize_player_name, signup_password

logger = logging.getLogger(__name__)


class AccountService:
    """账号表的业务封装。

    账号主键有两个口径，二者都可能出现在请求里：

    * ``online_id``（数字字符串）—— 客户端把它存进 ``savedata`` 并长期使用；
    * ``password``（32 位 md5）—— 由昵称派生，可重复计算，用于找回账号。

    因此 :meth:`signup` 会先按 password 查、再按昵称查，
    最后才新建，避免同名玩家拿到多个 ID。
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    # ---------------------------------------------------------------- 查询
    def all_accounts(self) -> dict[str, Account]:
        """返回 ``{online_id: Account}``。"""
        raw = self.db.accounts.read()
        result: dict[str, Account] = {}
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            account = Account.from_dict({**value, "onlineid": value.get("onlineid", key)})
            result[account.online_id or str(key)] = account
        return result

    def get(self, online_id: object) -> Account | None:
        """按 ``online_id`` 取账号。"""
        key = str(online_id or "").strip()
        if not key:
            return None
        return self.all_accounts().get(key)

    def find_by_password(self, password: str) -> Account | None:
        """按 ``password`` 取账号。"""
        if not password:
            return None
        for account in self.all_accounts().values():
            if account.password == password:
                return account
        return None

    def find_by_nickname(self, nickname: str) -> Account | None:
        """按昵称取账号（昵称不唯一时返回先注册的那个）。"""
        if not nickname:
            return None
        matches = [
            account
            for account in self.all_accounts().values()
            if account.nickname == nickname
        ]
        if not matches:
            return None
        return min(matches, key=lambda account: account.created_at)

    def _next_online_id(self) -> str:
        """分配一个新的 ``online_id``。

        客户端在 ``gml_Object_obj_timer_Create_0.gml:10-11`` 里判断
        ``global.ONLINE_ID == 0`` 即视为“未登录”，所以 **绝不能返回 0**。
        这里从 1 开始，按已存在的最大 ID 递增，保证不与历史数据冲突。
        """
        used = {int(key) for key in self.all_accounts() if str(key).isdigit()}
        return str(max(used) + 1 if used else 1)

    # ---------------------------------------------------------------- 注册
    def signup(self, nickname: str, password: str) -> Account:
        """注册或复用账号，返回最终账号对象。

        与旧实现相比的修正：

        * 旧代码把整个查询参数表（含 password）原样存进 ``data.json``，
          并且用 ``try/except`` 包住 for 循环来“判断是否找到用户”，
          一旦循环体内部抛异常就会把同一个用户追加两次。
          这里改为显式的查表逻辑，不再依赖异常控制流。
        * 旧代码不区分 ``password`` 与昵称，重新注册会把列表里
          第一个 password 相同的记录就地覆盖（即便那是别的玩家）。
        """
        clean_name = sanitize_player_name(nickname)
        provided = (password or "").strip()
        # 客户端理论上总会带 password；缺失时退化为用昵称派生，保持可用。
        effective_password = provided or signup_password(clean_name)

        existing = self.find_by_password(effective_password)
        if existing is None:
            existing = self.find_by_nickname(clean_name)

        if existing is not None:
            # 复用账号：只补齐昵称（玩家可能改了名字后又触发 signup）。
            if existing.nickname != clean_name:
                logger.info(
                    "账号 %s 昵称更新：%s -> %s",
                    existing.online_id,
                    existing.nickname,
                    clean_name,
                )
            self._save(
                Account(
                    online_id=existing.online_id,
                    password=effective_password,
                    nickname=clean_name,
                    exp=existing.exp,
                    all_score=existing.all_score,
                    subtitle=existing.subtitle,
                    rating=existing.rating,
                    banned=existing.banned,
                    created_at=existing.created_at,
                )
            )
            return self.get(existing.online_id) or existing

        account = Account(
            online_id=self._next_online_id(),
            password=effective_password,
            nickname=clean_name,
            exp=1,
        )
        self._save(account)
        logger.info(
            "新账号注册：id=%s nickname=%s", account.online_id, account.nickname
        )
        return account

    # ---------------------------------------------------------------- 更新
    def update(
        self,
        *,
        online_id: object,
        nickname: str | None = None,
        exp: object = None,
        all_score: object = None,
        subtitle: object = None,
        rating: object = None,
    ) -> Account | None:
        """按 ``update.php`` 语义更新账号；账号不存在时返回 ``None``。

        旧实现用 ``password`` 定位记录，但 ``update.php`` 发的 password
        是**每帧变化的校验串**，并不等于注册时那个 md5；
        因此旧代码实际上永远匹配不到，会不断往 ``data.json`` 追加重复记录。
        这里改为用 ``online_id`` 定位，语义正确且不会产生重复。
        """
        account = self.get(online_id)
        if account is None:
            logger.info("update.php 收到未知 online_id=%s，忽略", online_id)
            return None
        account.apply_client_update(
            nickname=sanitize_player_name(nickname) if nickname else None,
            exp=exp,
            all_score=all_score,
            subtitle=subtitle,
            rating=rating,
        )
        self._save(account)
        return account

    # ---------------------------------------------------------------- 封禁
    def is_banned(self, online_id: object) -> bool:
        """判断账号是否被封禁。"""
        account = self.get(online_id)
        return bool(account and account.banned)

    def set_banned(self, online_id: object, banned: bool = True) -> bool:
        """封禁/解封账号，供维护脚本或管理接口调用。"""
        account = self.get(online_id)
        if account is None:
            return False
        account.banned = banned
        account.touch()
        self._save(account)
        return True

    # ------------------------------------------------------------ 榜单排序
    def ranked_by_exp(self, limit: int | None = None) -> list[Account]:
        """按经验值降序返回账号（``gethiexpnew.php`` 用）。"""
        return self._ranked(lambda account: (account.exp, account.rating), limit)

    def ranked_by_score(self, limit: int | None = None) -> list[Account]:
        """按总得分降序返回账号（``gethiscorenew.php`` 用）。"""
        return self._ranked(lambda account: (account.all_score, account.rating), limit)

    def ranked_by_rating(self, limit: int | None = None) -> list[Account]:
        """按 Rating 降序返回账号（``gethiratingnew.php`` 用）。"""
        return self._ranked(lambda account: (account.rating, account.exp), limit)

    def _ranked(self, key, limit: int | None) -> list[Account]:
        """通用排序+截断，自动过滤封禁账号。"""
        accounts = [
            account for account in self.all_accounts().values() if not account.banned
        ]
        accounts.sort(key=key, reverse=True)
        return accounts if limit is None else accounts[:limit]

    # ------------------------------------------------------------ 内部写入
    def _save(self, account: Account) -> None:
        """把账号写回存储（按 ``online_id`` 分片，保留未知字段）。"""
        payload = account.to_dict()

        def _mutate(data: dict) -> None:
            existing = data.get(account.online_id)
            if isinstance(existing, dict):
                # 保留旧文件里的额外字段，避免手工维护的信息丢失。
                merged = dict(existing)
                merged.update(payload)
                data[account.online_id] = merged
            else:
                data[account.online_id] = payload

        self.db.accounts.update(_mutate)
