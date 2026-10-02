"""成绩服务：谱面成绩上传、曲目热度、通关率。

对应客户端接口
--------------

* ``scoreupload.php`` —— 结算界面把本次（或历史最好）成绩上传。
* ``getscoreone.php`` —— 旧版“从服务器拉回自己的成绩”，
  客户端会把它写进本地 ``score.k`` 的 ``.S`` 字段。
* ``gethit.php`` / ``getstageclear.php`` 所需的数据源。

客户端调用点
------------

* ``original_gamecode/gml_Object_obj_r_Create_0.gml:97-110``（上传）
* ``original_gamecode/gml_Object_obj_download_old_Create_0.gml:32-38``（拉取）

成绩数值的含义
--------------

客户端在 ``gml_Object_obj_game_Step_0.gml:494`` 计算::

    SCORE = ((HIT + POOR * 0.5) / ONPU_CNT) * 100 + CRITICAL * 0.01

即“命中率百分比 + 暴击加成”，满分 100 分时再加上暴击数 ×0.01，
所以理论上限略高于 100（全暴击时约 100 + 音符数 ×0.01）。
服务器不做上限裁剪，只做合法性检查（非负、不超过 200）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from drr_server.models import ScoreRecord, make_song_key, parse_song_key
from drr_server.storage import Database
from drr_server.utils import clamp, format_score, to_float, to_int

logger = logging.getLogger(__name__)

# 单曲历史上限：只保留最近 N 次上传，防止成绩表无限膨胀。
MAX_HISTORY_PER_SONG = 50

# 成绩合法上限。正常上限约 100 + 音符数*0.01，留出余量到 200 以防异常数据。
MAX_REASONABLE_SCORE = 200.0

# 通关判定线：与客户端 ``score_to_rank`` 里“>= 80 视为通关”一致
# （见 ``original_gamecode/gml_Script_score_to_rank.gml:11-12``）。
CLEAR_SCORE_THRESHOLD = 80.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class ScoreService:
    """成绩表业务封装。

    存储结构（``data/scores.json``）::

        {
          "players": {
            "1": {                          # online_id
              "total_plays": 42,            # 上传次数（用于 gethit 热度）
              "songs": {
                "0/10001/3": {              # difficulty/song_id/hard_level
                  "best": "99.50",
                  "best_critical": 0,
                  "death_mode": 1,
                  "plays": 7,
                  "last_played": "2026-01-01T12:00:00+08:00",
                  "history": [ {ScoreRecord...} ]
                }
              }
            }
          }
        }
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------ 内部工具
    def _state(self) -> dict:
        """读取成绩表，并把历史遗留的顶层扁平结构升级为带 ``players`` 的结构。"""
        raw = self.db.scores.read()
        if not isinstance(raw, dict):
            raw = {}
        if "players" not in raw or not isinstance(raw.get("players"), dict):
            # 兼容早期可能直接写成 {online_id: {...}} 的形式。
            legacy = {
                key: value
                for key, value in raw.items()
                if isinstance(value, dict) and key not in {"version", "players"}
            }
            raw = {"version": 2, "players": legacy or {}}
        raw.setdefault("version", 2)
        raw.setdefault("players", {})
        return raw

    # ---------------------------------------------------------------- 写入
    def record_upload(
        self,
        *,
        online_id: object,
        song_id: object,
        difficulty: object,
        hard_level: object,
        score: object,
        death_mode: object = 0,
        client_date: str = "",
        client_time: str = "",
    ) -> ScoreRecord | None:
        """记录一次成绩上传，返回落库的 :class:`ScoreRecord`。

        参数非法（曲目/难度越界、成绩为负）时返回 ``None`` 且不写库，
        避免脏数据污染排行榜。
        """
        settings = self.db.settings
        pid = str(online_id or "").strip()
        sid = to_int(song_id, -1)
        dif = to_int(difficulty, -1)
        hard = to_int(hard_level, -1)
        value = to_float(score, -1.0)

        if not pid or pid == "0":
            logger.info("成绩上传缺少有效 online_id，已忽略")
            return None
        if not (settings.song_id_base <= sid <= settings.song_id_limit):
            logger.info("成绩上传曲目编号越界：%s", sid)
            return None
        if not (0 <= dif < settings.difficulties):
            logger.info("成绩上传难度越界：%s", dif)
            return None
        if not (0 <= hard < settings.hard_levels):
            logger.info("成绩上传谱面等级越界：%s", hard)
            return None
        if value < 0 or value > MAX_REASONABLE_SCORE:
            logger.info("成绩上传数值异常（%s），已拒绝", value)
            return None

        record = ScoreRecord(
            online_id=pid,
            song_id=sid,
            difficulty=dif,
            hard_level=hard,
            score=value,
            death_mode=clamp(to_int(death_mode, 0), 0, 1),
            client_date=str(client_date or ""),
            client_time=str(client_time or ""),
        )

        def _mutate(data: dict) -> None:
            players = data.setdefault("players", {})
            player = players.setdefault(pid, {"total_plays": 0, "songs": {}})
            player.setdefault("songs", {})
            player["total_plays"] = to_int(player.get("total_plays"), 0) + 1

            songs = player["songs"]
            entry = songs.get(record.song_key)
            if not isinstance(entry, dict):
                entry = {"best": "0.00", "plays": 0, "history": []}
                songs[record.song_key] = entry

            entry["plays"] = to_int(entry.get("plays"), 0) + 1
            entry["last_played"] = record.received_at
            if record.death_mode:
                entry["death_mode"] = 1

            best = to_float(entry.get("best"), 0.0)
            if record.score >= best:
                entry["best"] = format_score(record.score)
            # 记录最高分时是否在死亡模式达成（用于界面上的特殊标记）。
            if record.death_mode and record.score >= best:
                entry["best_death_mode"] = 1

            history = entry.setdefault("history", [])
            history.insert(0, record.to_dict())
            del history[MAX_HISTORY_PER_SONG:]

        self.db.scores.update(_mutate)
        return record

    # ---------------------------------------------------------------- 读取
    def best_score(
        self, online_id: object, song_id: object, difficulty: object, hard_level: object
    ) -> float:
        """返回玩家在指定谱面的最好成绩；没有记录时返回 ``0.0``。"""
        key = make_song_key(
            to_int(difficulty, 0), to_int(song_id, 0), to_int(hard_level, 0)
        )
        player = self._state()["players"].get(str(online_id or "").strip())
        if not isinstance(player, dict):
            return 0.0
        entry = player.get("songs", {}).get(key)
        if not isinstance(entry, dict):
            return 0.0
        return to_float(entry.get("best"), 0.0)

    def player_song_stats(self, online_id: object) -> dict[int, dict]:
        """汇总某玩家每首曲子的统计，供通关率与热度计算使用。

        返回::

            {song_id: {"best_total": float, "clears": int, "total_plays": int}}
        """
        player = self._state()["players"].get(str(online_id or "").strip())
        result: dict[int, dict] = {}
        if not isinstance(player, dict):
            return result
        for key, entry in (player.get("songs") or {}).items():
            parsed = parse_song_key(key)
            if parsed is None or not isinstance(entry, dict):
                continue
            _dif, song_id, _hard = parsed
            bucket = result.setdefault(
                song_id, {"best_total": 0.0, "clears": 0, "total_plays": 0}
            )
            best = to_float(entry.get("best"), 0.0)
            bucket["best_total"] = max(bucket["best_total"], best)
            bucket["total_plays"] += to_int(entry.get("plays"), 0)
            if best >= CLEAR_SCORE_THRESHOLD:
                bucket["clears"] += 1
        return result

    # ------------------------------------------------------------ 全局统计
    def song_play_counts(self) -> dict[int, int]:
        """返回 ``{song_id: 总游玩次数}``，用于 ``gethit.php``。"""
        counts: dict[int, int] = {}
        for entries in self._state()["players"].values():
            if not isinstance(entries, dict):
                continue
            for key, entry in (entries.get("songs") or {}).items():
                parsed = parse_song_key(key)
                if parsed is None or not isinstance(entry, dict):
                    continue
                song_id = parsed[1]
                counts[song_id] = counts.get(song_id, 0) + to_int(
                    entry.get("plays"), 0
                )
        return counts

    def song_clear_rates(self) -> dict[int, float]:
        """返回 ``{song_id: 全球通关率百分比}``，用于 ``getstageclear.php``。

        口径：把所有玩家在该曲目上“曾达到通关线”的谱面数，
        除以“所有玩家在该曲目上游玩过的不同谱面数”，再乘 100。
        若某曲目没有任何游玩记录，返回 0.0。

        这里的“谱面”= ``(难度, 谱面等级)`` 组合，
        与客户端每曲 4×6 个存档槽位对应。
        """
        settings = self.db.settings
        attempts: dict[int, set[tuple[int, int]]] = {}
        cleared: dict[int, set[tuple[int, int]]] = {}
        for pid, entries in self._state()["players"].items():
            if not isinstance(entries, dict):
                continue
            for key, entry in (entries.get("songs") or {}).items():
                parsed = parse_song_key(key)
                if parsed is None or not isinstance(entry, dict):
                    continue
                dif, song_id, hard = parsed
                attempts.setdefault(song_id, set()).add((pid, dif, hard))
                if to_float(entry.get("best"), 0.0) >= CLEAR_SCORE_THRESHOLD:
                    cleared.setdefault(song_id, set()).add((pid, dif, hard))
        rates: dict[int, float] = {}
        for song_id, attempted in attempts.items():
            total = len(attempted)
            if total == 0:
                continue
            rates[song_id] = round(len(cleared.get(song_id, set())) * 100.0 / total, 2)
        return rates

    def song_max_scores(self) -> dict[int, float]:
        """返回 ``{song_id: 全服最高成绩}``（备用，便于后续扩展排行榜）。"""
        best: dict[int, float] = {}
        for entries in self._state()["players"].values():
            if not isinstance(entries, dict):
                continue
            for key, entry in (entries.get("songs") or {}).items():
                parsed = parse_song_key(key)
                if parsed is None or not isinstance(entry, dict):
                    continue
                song_id = parsed[1]
                best[song_id] = max(best.get(song_id, 0.0), to_float(entry.get("best"), 0.0))
        return best

    def song_scoreboard(self, song_id: int, difficulty: int) -> dict[int, list[dict]]:
        """返回某曲目某难度下、按谱面等级分组的排行榜。

        供 ``highscore.view.php`` 页面使用。结构::

            {hard_level: [{"online_id", "score", "death_mode", "updated_at"}, ...]}

        每个列表已按成绩降序排列。页面上再按需要截断行数。
        """
        settings = self.db.settings
        boards: dict[int, list[dict]] = {level: [] for level in range(settings.hard_levels)}
        for pid, player in self._state()["players"].items():
            if not isinstance(player, dict):
                continue
            for key, entry in (player.get("songs") or {}).items():
                parsed = parse_song_key(key)
                if parsed is None or not isinstance(entry, dict):
                    continue
                dif, sid, hard = parsed
                if sid != song_id or dif != difficulty:
                    continue
                if hard not in boards:
                    continue
                boards[hard].append(
                    {
                        "online_id": pid,
                        "score": to_float(entry.get("best"), 0.0),
                        "death_mode": to_int(entry.get("death_mode"), 0),
                        "updated_at": str(entry.get("last_played") or ""),
                    }
                )
        for entries in boards.values():
            entries.sort(key=lambda item: item["score"], reverse=True)
        return boards

    def recent_scores(self, online_id: object, limit: int = 10) -> list[ScoreRecord]:
        """按时间倒序返回玩家最近若干条成绩（用于重新计算 Rating）。"""
        player = self._state()["players"].get(str(online_id or "").strip())
        if not isinstance(player, dict):
            return []
        collected: list[ScoreRecord] = []
        for entry in (player.get("songs") or {}).values():
            if not isinstance(entry, dict):
                continue
            for item in entry.get("history", []):
                if isinstance(item, dict):
                    collected.append(ScoreRecord.from_dict(item))
        collected.sort(key=lambda record: record.received_at, reverse=True)
        return collected[:limit]

    def top_scores(self, online_id: object, limit: int = 30) -> list[float]:
        """返回玩家最好的 ``limit`` 条成绩（用于重新计算 Rating 的 BEST30）。"""
        values: list[float] = []
        player = self._state()["players"].get(str(online_id or "").strip())
        if not isinstance(player, dict):
            return values
        for entry in (player.get("songs") or {}).values():
            if isinstance(entry, dict):
                values.append(to_float(entry.get("best"), 0.0))
        values.sort(reverse=True)
        return values[:limit]
