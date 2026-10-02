"""端到端冒烟测试：模拟真实客户端对**运行中的服务器**发请求。

它会复刻客户端的 md5 公式与 ``scr_phpdata`` 解析方式，
把一条完整链路走一遍：注册 → 更新状态 → 上传成绩 → 云存档上传/下载
→ 读取三个榜单、热度与通关率 → 打开网页。

用法::

    # 先启动服务器（另开一个终端）
    $env:DRR_PORT='12561'; .venv\\Scripts\\python.exe run.py

    # 再运行本脚本
    .venv\\Scripts\\python.exe scripts\\smoke_test_http.py --base http://127.0.0.1:12561
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.error
import urllib.parse
import urllib.request

PASSED = 0
FAILED = 0


def md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def phpdata(payload: str, index: int) -> str:
    """客户端 ``scr_phpdata`` 的等价实现。"""
    positions = [i for i, ch in enumerate(payload) if ch == ">"]
    if index < 0 or index >= len(positions):
        return ""
    start = positions[index] + 1
    end = positions[index + 1] if index + 1 < len(positions) else len(payload)
    return payload[start:end]


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  [OK]   {label}")
    else:
        FAILED += 1
        print(f"  [FAIL] {label} {detail}")


def fetch(base: str, path: str, **params) -> str:
    url = f"{base}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    if not path.startswith("/DanceRail/") and not path.startswith("/"):
        raise ValueError(path)
    with urllib.request.urlopen(url, timeout=15) as response:
        return response.read().decode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:12561")
    parser.add_argument("--nickname", default="SMOKETEST")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    print(f"\n=== 对 {base} 执行端到端冒烟测试 ===\n")

    # ---------------------------------------------------------- date.php
    print("[1] date.php")
    today = fetch(base, "/DanceRail/date.php")
    check("返回 8 位纯数字", today.isdigit() and len(today) == 8, f"got={today!r}")

    # -------------------------------------------------------- signup.php
    print("[2] signup.php（注册）")
    nickname = args.nickname
    signup_pw = md5(md5(nickname + nickname) + md5(nickname + "sora") + md5(nickname + "game"))
    online_id = fetch(base, "/DanceRail/signup.php", nickname=nickname, password=signup_pw)
    check("返回非零数字 ID", online_id.isdigit() and online_id != "0", f"got={online_id!r}")
    again = fetch(base, "/DanceRail/signup.php", nickname=nickname, password=signup_pw)
    check("重复注册返回同一 ID（幂等）", again == online_id, f"{online_id} vs {again}")

    # -------------------------------------------------------- update.php
    print("[3] update.php（上报状态）")
    exp, all_score, subtitle, rating = "3600", "1200.50", "3", "12.30"
    update_pw = md5(
        md5(online_id + nickname + "sora")
        + md5(exp + all_score + "game")
        + md5(subtitle + rating + "2017")
    )
    result = fetch(
        base,
        "/DanceRail/update.php",
        onlineid=online_id,
        nickname=nickname,
        exp=exp,
        allscore=all_score,
        subtitle=subtitle,
        rating=rating,
        password=update_pw,
    )
    check("返回 0", result == "0", f"got={result!r}")

    # --------------------------------------------------- scoreupload.php
    print("[4] scoreupload.php（上传成绩）")
    song_id, difficulty, hard_level = "10138", "2", "3"
    score = "97.5"
    date, time_value = "20251021", "90503"
    score_pw = md5(
        md5(online_id + song_id + difficulty + "sora")
        + md5(hard_level + "0" + date + "game")
        + md5(time_value + score + "password")
    )
    result = fetch(
        base,
        "/DanceRail/scoreupload.php",
        id=online_id,
        song_id=song_id,
        game_dif=difficulty,
        song_hard=hard_level,
        deathmode="0",
        date=date,
        time=time_value,
        score=score,
        password=score_pw,
    )
    check("返回 0", result == "0", f"got={result!r}")

    # ------------------------------------------------------- 榜单三件套
    print("[5] 排行榜")
    exp_board = fetch(base, "/DanceRail/gethiexpnew.php")
    check("经验榜 30 个字段", exp_board.count(">") == 30, f"count={exp_board.count('>')}")
    names = [phpdata(exp_board, i * 3) for i in range(10)]
    check("经验榜包含刚注册的玩家", nickname in names, f"names={names}")
    idx = names.index(nickname) if nickname in names else -1
    if idx >= 0:
        check("经验榜字段顺序 = 名字/rating/exp",
              phpdata(exp_board, idx * 3 + 1) == "12.30"
              and phpdata(exp_board, idx * 3 + 2) == "3600",
              f"rating={phpdata(exp_board, idx*3+1)} exp={phpdata(exp_board, idx*3+2)}")

    score_board = fetch(base, "/DanceRail/gethiscorenew.php")
    check("总分榜 30 个字段", score_board.count(">") == 30)
    names = [phpdata(score_board, i * 3) for i in range(10)]
    idx = names.index(nickname) if nickname in names else -1
    if idx >= 0:
        check("总分榜字段顺序 = 名字/rating/allscore",
              phpdata(score_board, idx * 3 + 2) == "1200.50",
              f"allscore={phpdata(score_board, idx*3+2)}")

    rating_board = fetch(base, "/DanceRail/gethiratingnew.php")
    check("Rating 榜 30 个字段", rating_board.count(">") == 30)
    names = [phpdata(rating_board, i * 3) for i in range(10)]
    idx = names.index(nickname) if nickname in names else -1
    if idx >= 0:
        check("Rating 榜字段顺序 = 名字/exp/rating",
              phpdata(rating_board, idx * 3 + 1) == "3600"
              and phpdata(rating_board, idx * 3 + 2) == "12.30",
              f"exp={phpdata(rating_board, idx*3+1)} rating={phpdata(rating_board, idx*3+2)}")

    # ------------------------------------------------------ getscoreone
    print("[6] getscoreone.php（旧版单谱面拉取）")
    one = fetch(
        base,
        "/DanceRail/getscoreone.php",
        user_id=online_id,
        nickname=nickname,
        song_id=song_id,
        game_dif=difficulty,
        song_hard=hard_level,
    )
    check("返回成绩百分比", one == "97.50", f"got={one!r}")

    # --------------------------------------------------- gethit / stage
    print("[7] gethit.php / getstageclear.php")
    hits = fetch(base, "/DanceRail/gethit.php", song_num="132")
    check("热度榜字段数为偶数", hits.count(">") % 2 == 0, f"count={hits.count('>')}")
    check("第一组是占位 (0,0)",
          phpdata(hits, 0) == "0" and phpdata(hits, 1) == "0")
    target = 1 + (int(song_id) - 10000)
    check("含刚上传成绩的曲目",
          phpdata(hits, target * 2) == song_id, f"got={phpdata(hits, target*2)!r}")
    check("该曲目游玩次数 >= 1",
          phpdata(hits, target * 2 + 1).isdigit()
          and int(phpdata(hits, target * 2 + 1)) >= 1)

    stage = fetch(base, "/DanceRail/getstageclear.php")
    check("通关率列表非空且可解析", stage.count(">") > 0 and all(
        phpdata(stage, i).isdigit() for i in range(min(5, stage.count(">")))
    ))

    # --------------------------------------------------------- 云存档
    print("[8] 云存档上传/下载")
    dlkey = md5(f"{online_id}{nickname}27")
    fixed = {"playerexp": "3600", "onlineid": online_id, "starttime": "20251021"}
    for name, value in fixed.items():
        result = fetch(
            base,
            "/DanceRail/uploadsavedata.php",
            player=dlkey,
            dataname=name,
            data=value,
        )
        check(f"uploadsavedata({name}) 返回 1", result == "1", f"got={result!r}")

    # 每曲 96 条：4 难度 × 6 谱面 × 4 字段
    for k in range(4):
        for j in range(6):
            for field in "SFCD":
                fetch(
                    base,
                    "/DanceRail/uploadsavedata.php",
                    player=dlkey,
                    dataname=f"{k}{song_id}{j}{field}",
                    data="1",
                )
    total = fetch(base, "/DanceRail/getsavedatanum.php", player=dlkey)
    check("条目数 = 3 + 96 = 99", total == "99", f"got={total!r}")

    for name, value in fixed.items():
        got = fetch(base, "/DanceRail/getsavedata.php", player=dlkey, dataname=name)
        check(f"getsavedata({name}) 回读一致", got == value, f"{got!r} != {value!r}")

    got = fetch(base, "/DanceRail/getsavedata.php", player=dlkey, dataname=f"0{song_id}3S")
    check("曲目字段回读一致", got == "1", f"got={got!r}")
    got = fetch(base, "/DanceRail/getsavedata.php", player="0" * 32, dataname="playerexp")
    check("未知 DLKEY 返回 0（不是错误页）", got == "0", f"got={got!r}")
    got = fetch(base, "/DanceRail/getsavedata.php", player="0" * 32, dataname="0101383F")
    check("音符总数默认返回 1", got == "1", f"got={got!r}")

    # --------------------------------------------------- 网页与杂项
    print("[9] 网页与管理页")
    page = fetch(base, "/DanceRail/highscore.view.php", song_id=song_id, game_dif=difficulty)
    check("highscore 页面是 HTML 且含昵称", "<html" in page.lower() and nickname in page)
    admin = fetch(base, "/DanceRail/admin")
    check("管理页可访问", "服务器状态" in admin)
    check("clock.js 可访问", "data-drr-clock" in fetch(base, "/DanceRail/clock.js"))

    # --------------------------------------------------- cheat / usecard
    print("[10] cheat_test.php / usecard.php")
    check("封禁检查返回 0", fetch(base, "/DanceRail/cheat_test.php") == "0")
    card = fetch(base, "/DanceRail/usecard.php", cardid="12345678")
    check("兑换卡返回正数金币", card.lstrip("-").isdigit() and int(card) > 0, f"got={card!r}")
    card = fetch(base, "/DanceRail/usecard.php", cardid="not-a-card")
    check("无效卡返回负数（避免客户端卡死）", int(card) < 0, f"got={card!r}")

    # --------------------------------------------------- 未知接口
    print("[11] 未知接口容错")
    unknown = fetch(base, "/DanceRail/does_not_exist.php")
    check("未知接口返回 0 而不是 500", unknown == "0", f"got={unknown!r}")
    root = fetch(base, "/")
    check("根路径返回说明文本", "DanceRail" in root)

    print(f"\n=== 结果：{PASSED} 通过 / {FAILED} 失败 ===\n")
    return 1 if FAILED else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as exc:
        print(f"无法连接服务器：{exc}\n请先启动 run.py", file=sys.stderr)
        sys.exit(2)
