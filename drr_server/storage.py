"""轻量 JSON 持久化层。

设计目标
--------

* **可读**：数据文件就是普通 JSON，方便用编辑器直接查看/手改。
* **安全**：写入走“临时文件 + 原子替换”，进程被杀也不会留下半截文件。
* **并发可控**：每个文件一把可重入锁，把“读-改-写”整个过程包在锁里，
  避免 waitress（多线程）下两个请求互相覆盖。
* **容错**：文件损坏时自动备份并把内容重置为初始值，服务器照常启动。

数据文件布局（``Settings.data_dir``）::

    accounts.json  账号表：{online_id: {...}}
    saves.json     云存档：{player_key: {dataname: value}}
    scores.json    成绩表：{online_id: {song_key: {...}}}
    cards.json     兑换卡使用记录：{card_id: {"coins": n, "used": "ISO8601"}}
    meta.json      服务器运行元信息（推断出的曲目数、迁移标记等）
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

JSONValue = Any


class JsonFileStore:
    """单个 JSON 文件的线程安全存取封装。

    参数
    ----
    path:
        目标文件路径。父目录会被自动创建。
    default:
        文件不存在或内容损坏时使用的初始值。
    ascii_output:
        写盘时是否转义非 ASCII 字符。``False`` 可以让中文昵称保持可读。
    """

    def __init__(
        self,
        path: Path,
        default: JSONValue = None,
        *,
        ascii_output: bool = False,
    ) -> None:
        self.path = Path(path)
        self._default = default
        self._ascii = ascii_output
        # 使用 RLock：``update()`` 内部会再次调用 ``read()`` / ``write()``。
        self._lock = threading.RLock()
        self._cache: JSONValue = None
        self._loaded = False

    # ------------------------------------------------------------------ 读
    def _load_locked(self) -> JSONValue:
        """在持锁状态下读取文件，必要时初始化。"""
        if self._loaded:
            return self._cache
        if not self.path.exists():
            self._cache = self._clone_default()
            self._write_locked(self._cache)
            logger.info("已创建数据文件：%s", self.path)
        else:
            try:
                raw = self.path.read_text(encoding="utf-8")
                self._cache = json.loads(raw) if raw.strip() else self._clone_default()
            except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
                # 损坏文件不能直接丢弃：先改名留档，方便人工抢救。
                broken = self.path.with_suffix(self.path.suffix + ".broken")
                try:
                    self.path.replace(broken)
                    logger.error("数据文件损坏，已备份到 %s：%s", broken, exc)
                except OSError:  # pragma: no cover - 极端情况（如只读挂载）
                    logger.error("数据文件损坏且无法备份：%s", exc)
                self._cache = self._clone_default()
                self._write_locked(self._cache)
        self._loaded = True
        return self._cache

    def _clone_default(self) -> JSONValue:
        """深拷贝默认值，避免多个实例共享同一个可变对象。"""
        if self._default is None:
            return {}
        return json.loads(json.dumps(self._default))

    def read(self) -> JSONValue:
        """返回整份数据的**浅拷贝引用**。

        调用方若需要“读-改-写”原子性，请改用 :meth:`update`。
        """
        with self._lock:
            return self._load_locked()

    # ------------------------------------------------------------------ 写
    def _write_locked(self, payload: JSONValue) -> None:
        """在持锁状态下原子写入文件。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        text = json.dumps(
            payload,
            ensure_ascii=self._ascii,
            sort_keys=True,
            indent=4,
        )
        temp.write_text(text, encoding="utf-8")
        # ``os.replace`` 在 Windows / POSIX 上都是原子替换语义。
        os.replace(temp, self.path)

    def write(self, payload: JSONValue) -> None:
        """整份覆盖写入。"""
        with self._lock:
            self._cache = payload
            self._loaded = True
            self._write_locked(payload)

    def update(self, mutator) -> JSONValue:
        """在锁内执行 ``mutator(data)``，随后落盘并返回数据。

        ``mutator`` 直接原地修改传入对象；返回值被忽略，
        这样调用方不必关心“是否需要重新赋值”。
        """
        with self._lock:
            data = self._load_locked()
            result = mutator(data)
            # 允许 mutator 返回新对象来整体替换（例如从 list 迁移到 dict）。
            if result is not None:
                data = result
            self._cache = data
            self._write_locked(data)
            return data

    def reload(self) -> None:
        """丢弃内存缓存，强制下次读取时从磁盘重新加载。"""
        with self._lock:
            self._loaded = False
            self._cache = None

    @contextmanager
    def transaction(self) -> Iterator[JSONValue]:
        """以上下文管理器方式做一次“读-改-写”。

        ::

            with store.transaction() as data:
                data["k"] = "v"
        """
        with self._lock:
            data = self._load_locked()
            try:
                yield data
            except Exception:
                # 出错时不落盘，保持文件原样。
                raise
            else:
                self._write_locked(data)


class Database:
    """聚合所有 JSON 数据文件，并提供旧版数据迁移。

    各 ``JsonFileStore`` 之间相互独立（不共享锁），
    因为客户端的每个接口都只操作单一数据集，
    不存在跨文件事务需求；这样可以把锁粒度保持在最小。
    """

    def __init__(self, settings) -> None:
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.accounts = JsonFileStore(settings.accounts_file, default={})
        self.saves = JsonFileStore(settings.saves_file, default={})
        self.scores = JsonFileStore(settings.scores_file, default={})
        self.cards = JsonFileStore(settings.cards_file, default={})
        self.meta = JsonFileStore(
            settings.meta_file,
            default={
                "song_count": settings.song_count,
                "migrated": {},
                "strict_auth": settings.strict_auth,
            },
        )

    # -------------------------------------------------------- 文件路径代理
    # 便于调用方（管理页、测试、维护脚本）直接拿到各数据文件路径，
    # 而不用绕道 ``db.settings.xxx_file``。
    @property
    def accounts_file(self) -> Path:
        return self.settings.accounts_file

    @property
    def saves_file(self) -> Path:
        return self.settings.saves_file

    @property
    def scores_file(self) -> Path:
        return self.settings.scores_file

    @property
    def cards_file(self) -> Path:
        return self.settings.cards_file

    @property
    def meta_file(self) -> Path:
        return self.settings.meta_file

    # ------------------------------------------------------------ 元信息
    def inferred_song_count(self) -> int:
        """返回服务器认为的曲目总数。

        优先使用云端存档里推断出的值（真实可信），
        其次是配置里的兜底值（默认 132）。

        注意 ``meta.json`` 里的值是历次运行累积的，
        而 ``settings.song_count`` 是本进程启动时的配置，
        两者取较大者才不会因为配置回退把已推断的结果丢掉。
        """
        meta = self.meta.read()
        value = meta.get("song_count")
        try:
            count = int(value)
        except (TypeError, ValueError):
            count = self.settings.song_count
        return max(count, self.settings.song_count)

    def sync_song_count(self) -> int:
        """把推断出的曲目数写回 ``settings``，让校验上界一并生效。

        为什么必须回写 ``settings``：``song_id_limit`` 依赖
        ``settings.song_count``，而路由层拿到的是 ``Settings`` 对象。
        如果只把推断值存在 ``meta.json`` 里，
        ``scoreupload.php`` 仍会用旧的较小上界做校验，
        结果是真实曲目编号的成绩被**静默丢弃**。
        """
        count = self.inferred_song_count()
        if count > self.settings.song_count:
            logger.info(
                "按云端存档把曲目数从 %d 修正为 %d（新的编号上界 %d）",
                self.settings.song_count,
                count,
                self.settings.song_id_base + count - 1,
            )
            self.settings.song_count = count
        return self.settings.song_count

    def remember_song_count(self, count: int) -> None:
        """记录从玩家存档中推断出的曲目总数（只在变大时更新）。"""
        if count <= self.inferred_song_count():
            return

        def _mutate(data: dict) -> None:
            data["song_count"] = count

        self.meta.update(_mutate)
        self.sync_song_count()

    # -------------------------------------------------------------- 迁移
    def migrate_legacy_files(self) -> dict[str, int]:
        """把根目录下的旧 ``data.json`` / ``save.json`` 合并进数据结构。

        迁移是**幂等**的：``meta.json`` 里的 ``migrated`` 记录已完成项，
        重复启动不会重复导入。返回各数据集新增的条目数，便于日志展示。
        """
        results = {"accounts": 0, "saves": 0}
        if not self.settings.migrate_legacy_data:
            return results

        flags = self.meta.read().setdefault("migrated", {})

        # ---------------------------------------------------- 账号 data.json
        legacy_accounts = self.settings.legacy_accounts_file
        if not flags.get("accounts") and legacy_accounts.exists():
            results["accounts"] = self._migrate_accounts(legacy_accounts)
            self._mark_migrated("accounts")

        # ---------------------------------------------------- 存档 save.json
        legacy_saves = self.settings.legacy_save_file
        if not flags.get("saves") and legacy_saves.exists():
            results["saves"] = self._migrate_saves(legacy_saves)
            self._mark_migrated("saves")

        # 迁移完成后按存档内容刷新曲目总数推断值。
        self.refresh_song_count_from_saves()
        return results

    def _mark_migrated(self, key: str) -> None:
        def _mutate(data: dict) -> None:
            data.setdefault("migrated", {})[key] = True

        self.meta.update(_mutate)

    def _migrate_accounts(self, source: Path) -> int:
        """旧格式是 ``[{...}, {...}]``（数组），新格式是 ``{online_id: {...}}``。"""
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            logger.warning("旧账号文件无法解析，跳过迁移：%s", exc)
            return 0

        records: list[dict] = []
        if isinstance(raw, list):
            records = [item for item in raw if isinstance(item, dict)]
        elif isinstance(raw, dict):
            # 容错：有人可能已经手工改成了字典形式。
            records = [v for v in raw.values() if isinstance(v, dict)]

        added = 0

        def _mutate(data: dict) -> None:
            nonlocal added
            for record in records:
                online_id = str(record.get("onlineid") or "").strip()
                if not online_id or online_id == "0":
                    # 没有 online_id 的旧记录无法作为账号主键，
                    # 但它的 password 仍是登录凭据，改用 password 当键。
                    password = str(record.get("password") or "").strip()
                    if not password:
                        continue
                    online_id = password
                merged = dict(record)
                merged.setdefault("onlineid", online_id)
                if online_id in data:
                    # 已存在则补齐缺失字段，不覆盖新数据里更完整的信息。
                    for key, value in merged.items():
                        data[online_id].setdefault(key, value)
                else:
                    data[online_id] = merged
                    added += 1

        self.accounts.update(_mutate)
        logger.info("旧账号数据已迁移：新增 %d 个账号", added)
        return added

    def _migrate_saves(self, source: Path) -> int:
        """旧 ``save.json`` 就是 ``{player: {dataname: data}}``，直接合并。"""
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            logger.warning("旧存档文件无法解析，跳过迁移：%s", exc)
            return 0
        if not isinstance(raw, dict):
            return 0

        added = 0

        def _mutate(data: dict) -> None:
            nonlocal added
            for player, entries in raw.items():
                if not isinstance(entries, dict):
                    continue
                merged = data.setdefault(str(player), {})
                for name, value in entries.items():
                    if name not in merged:
                        added += 1
                    merged[name] = value

        self.saves.update(_mutate)
        logger.info("旧云存档已迁移：新增 %d 条记录", added)
        return added

    def refresh_song_count_from_saves(self) -> int:
        """从全部云存档中推断曲目总数并记录。

        推断依据：客户端为每首曲子上传 ``difficulties × hard_levels`` 个条目
        （默认 4 × 6 = 24），曲目编号从 ``song_id_base``（10000）开始连续递增。
        因此“出现过的最大曲目编号”可以给出曲目数的**下界**：
        ``max_id - base + 1``。

        真实曲数由客户端的 ``songlist.drf`` 决定，服务器无法读取，
        所以这里取“存档推断值”和“配置兜底值”的较大者，
        保证 ``getstageclear.php`` / ``gethit.php`` 至少覆盖所有已知曲目。
        """
        from drr_server.utils import decode_song_entry

        seen: set[int] = set()
        for entries in self.saves.read().values():
            if not isinstance(entries, dict):
                continue
            for name in entries:
                decoded = decode_song_entry(name)
                if decoded is not None:
                    seen.add(decoded[1])
        if seen:
            estimate = max(seen) - self.settings.song_id_base + 1
            self.remember_song_count(estimate)
        return self.inferred_song_count()

