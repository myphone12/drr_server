"""运维命令行工具。

用法::

    python scripts/manage.py status
    python scripts/manage.py ban <online_id>
    python scripts/manage.py unban <online_id>
    python scripts/manage.py card <card_id> <coins> [备注]
    python scripts/manage.py account <online_id>
    python scripts/manage.py saves [<DLKEY 前缀>]
    python scripts/manage.py songcount [<新值>]

它直接操作 ``data/*.json``，**不需要服务器停止运行**
（存储层用文件锁 + 原子替换，且本脚本每次操作都重新读盘）。
但如果服务器正在运行，它内存里可能还持有旧数据，
因此改完之后管理员页面的数字可能滞后到下次重启才更新。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 让脚本可以直接运行（把项目根加入 sys.path）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from drr_server.config import Settings  # noqa: E402
from drr_server.services.accounts import AccountService  # noqa: E402
from drr_server.services.cards import CardService  # noqa: E402
from drr_server.services.savegame import SaveGameService  # noqa: E402
from drr_server.storage import Database  # noqa: E402


def _build(args) -> tuple[Settings, Database]:
    settings = Settings.from_env()
    if args.data_dir:
        settings.data_dir = Path(args.data_dir).expanduser().resolve()
    db = Database(settings)
    db.migrate_legacy_files()
    db.sync_song_count()
    return settings, db


# ---------------------------------------------------------------------- 命令
def cmd_status(args) -> int:
    """打印服务器概览。"""
    settings, db = _build(args)
    accounts = AccountService(db)

    all_accounts = accounts.all_accounts()
    saves_raw = db.saves.read()
    total_entries = sum(
        len(value) for value in saves_raw.values() if isinstance(value, dict)
    )
    score_players = len(db.scores.read().get("players", {}))

    print("=== DanceRail Refresh 服务器状态 ===")
    print(f"数据目录        : {settings.data_dir}")
    print(f"监听端口配置    : {settings.port}")
    print(f"曲目数（生效）  : {settings.song_count}")
    print(f"曲目编号范围    : {settings.song_id_base}-{settings.song_id_limit}")
    print(f"固定存档条目数  : {settings.base_save_entries}")
    print(f"每曲条目数      : {settings.saves_per_song}（请求数 {settings.requests_per_song}）")
    print(f"严格验签        : {'开启' if settings.strict_auth else '关闭'}")
    print(f"兑换卡策略      : {settings.card_mode}")
    print("-" * 48)
    print(f"账号数          : {len(all_accounts)}")
    print(f"封禁账号数      : {sum(1 for a in all_accounts.values() if a.banned)}")
    print(f"云存档 DLKEY 数 : {len(saves_raw)}")
    print(f"云存档条目总数  : {total_entries}")
    print(f"有成绩的玩家数  : {score_players}")

    expected = (
        settings.base_save_entries
        + settings.song_count * settings.saves_per_song * len(settings.score_fields)
    )
    print(f"完整存档条目数  : {expected}（90 + 曲数×96）")

    if all_accounts:
        print("\n--- 账号列表 ---")
        for account in sorted(all_accounts.values(), key=lambda a: a.online_id):
            flag = " [封禁]" if account.banned else ""
            print(
                f"  id={account.online_id:<6} {account.nickname:<20} "
                f"Lv.{account.level:<4} exp={account.exp:<10} "
                f"score={account.all_score:<12} rating={account.rating:.2f}{flag}"
            )
    return 0


def cmd_ban(args) -> int:
    settings, db = _build(args)
    accounts = AccountService(db)
    if accounts.set_banned(args.online_id, True):
        print(f"已封禁账号 {args.online_id}（{accounts.get(args.online_id).nickname}）")
        return 0
    print(f"未找到账号 {args.online_id}", file=sys.stderr)
    return 1


def cmd_unban(args) -> int:
    settings, db = _build(args)
    accounts = AccountService(db)
    if accounts.set_banned(args.online_id, False):
        print(f"已解封账号 {args.online_id}")
        return 0
    print(f"未找到账号 {args.online_id}", file=sys.stderr)
    return 1


def cmd_card(args) -> int:
    settings, db = _build(args)
    cards = CardService(db)
    cards.register_card(args.card_id, args.coins, note=args.note or "")
    print(
        f"已登记兑换卡 {args.card_id} → {args.coins} 金币"
        f"（当前策略 {settings.card_mode}）"
    )
    return 0


def cmd_account(args) -> int:
    settings, db = _build(args)
    accounts = AccountService(db)
    account = accounts.get(args.online_id)
    if account is None:
        print(f"未找到账号 {args.online_id}", file=sys.stderr)
        return 1
    print(json.dumps(account.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))

    # 附带该账号的成绩概览
    scores = db.scores.read().get("players", {}).get(str(args.online_id), {})
    songs = scores.get("songs", {}) if isinstance(scores, dict) else {}
    if songs:
        print(f"\n--- 成绩记录 {len(songs)} 条 ---")
        for key in sorted(songs)[:40]:
            entry = songs[key]
            print(f"  {key:<16} best={entry.get('best')} plays={entry.get('plays')}")
    saves = SaveGameService(db)
    save_keys = [
        key
        for key, value in db.saves.read().items()
        if isinstance(value, dict) and str(value.get("onlineid", "")) == str(args.online_id)
    ]
    if save_keys:
        print("\n--- 关联云存档 ---")
        for key in save_keys:
            print(f"  {key}  条目数={saves.count(key)}")
    return 0


def cmd_saves(args) -> int:
    settings, db = _build(args)
    saves = SaveGameService(db)
    prefix = (args.prefix or "").lower()
    rows = [
        (key, len(value))
        for key, value in db.saves.read().items()
        if isinstance(value, dict) and key.startswith(prefix)
    ]
    rows.sort(key=lambda item: -item[1])
    if not rows:
        print("没有匹配的云存档")
        return 0
    print(f"{'DLKEY':<36} {'条目数':>8}  {'进度':>8}")
    for key, count in rows[:100]:
        progress = saves.progress(key)
        print(
            f"{key:<36} {count:>8}  "
            f"{count * 100 // max(1, progress['expected_entries']):>7}%"
        )
    return 0


def cmd_songcount(args) -> int:
    settings, db = _build(args)
    if args.value is None:
        meta = db.meta.read()
        print(f"meta.json 记录值 : {meta.get('song_count')}")
        print(f"settings 生效值  : {settings.song_count}")
        print(f"编号上界         : {settings.song_id_limit}")
        return 0

    value = int(args.value)

    def _mutate(data: dict) -> None:
        data["song_count"] = value

    db.meta.update(_mutate)
    print(f"已把曲目数写为 {value}（下次启动生效，编号上界 {settings.song_id_base + value - 1}）")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DanceRail Refresh 服务器运维工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--data-dir",
        help="覆盖数据目录（默认取 DRR_DATA_DIR 或项目下的 data/）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="打印服务器概览").set_defaults(func=cmd_status)

    ban = sub.add_parser("ban", help="封禁账号")
    ban.add_argument("online_id")
    ban.set_defaults(func=cmd_ban)

    unban = sub.add_parser("unban", help="解封账号")
    unban.add_argument("online_id")
    unban.set_defaults(func=cmd_unban)

    card = sub.add_parser("card", help="登记兑换卡面额")
    card.add_argument("card_id", help="卡号（客户端为 8 位十六进制）")
    card.add_argument("coins", type=int, help="金币面额")
    card.add_argument("note", nargs="?", help="备注")
    card.set_defaults(func=cmd_card)

    account = sub.add_parser("account", help="查看单个账号及其成绩")
    account.add_argument("online_id")
    account.set_defaults(func=cmd_account)

    saves = sub.add_parser("saves", help="列出云存档及上传进度")
    saves.add_argument("prefix", nargs="?", help="DLKEY 前缀（可选）")
    saves.set_defaults(func=cmd_saves)

    songcount = sub.add_parser("songcount", help="查看/设置曲目数")
    songcount.add_argument("value", nargs="?", help="新的曲目数（省略则只查看）")
    songcount.set_defaults(func=cmd_songcount)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
