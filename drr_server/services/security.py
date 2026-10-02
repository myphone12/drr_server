"""请求验签。

背景：客户端的 ``password`` 不是“登录密码”，而是把若干请求参数
用固定的 md5 三重拼接算出来的**校验串**。服务器可以重算它来确认
请求参数没有被中间人篡改（尤其是排行榜写入）。

默认策略是**宽松模式**（``Settings.strict_auth = False``）：

* 客户端在历史上换过公式常量（例如 ``update.php`` 里从“当日日期”
  改成了固定字符串 ``2017``），不同版本对不上；
* 严格模式会让怀旧版本客户端完全无法上榜。

因此宽松模式下只记录一条警告日志，仍然接受请求；
需要防刷榜时把 ``DRR_STRICT_AUTH=1`` 打开即可。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from drr_server.utils import (
    gml_number_str,
    score_upload_password,
    signup_password,
    update_password,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class VerifyResult:
    """验签结果。

    ``ok`` 表示是否通过；``reason`` 用于日志与 ``highscore`` 页面提示。
    ``expected`` / ``provided`` 只在调试时打印，避免把校验串写进日志。
    """

    ok: bool
    reason: str = ""
    expected: str = ""
    provided: str = ""


def verify_signup(nickname: str, password: str) -> VerifyResult:
    """校验 ``signup.php`` 的 password 是否由 ``nickname`` 派生。

    这是唯一一处服务器**必须**严格校验的地方吗？也不是：
    ``signup.php`` 用 password 作为账号唯一键，只要一致就能复用账号，
    所以即使不校验也不影响功能，只影响“同一昵称是否总是拿到同一 ID”。

    实际实现里我们仍以 password 为主键，昵称只用于展示，
    因此校验失败不会拒绝请求，仅作为异常信号返回。
    """
    expected = signup_password(nickname)
    return VerifyResult(
        ok=expected == password,
        reason="" if expected == password else "signup_password_mismatch",
        expected=expected,
        provided=password,
    )


def verify_update(
    *,
    online_id: str,
    nickname: str,
    exp: object,
    all_score: object,
    subtitle: object,
    rating: object,
    password: str,
) -> VerifyResult:
    """校验 ``update.php`` 的 password。

    **必须用请求里带来的原始字符串**重算，不能先转成数字再格式化：
    客户端把 ``string(global.PLAYEREXP)`` / ``string(global.ALLSCORE)``
    / ``string(global.RATING)`` 的结果直接拼进哈希原像，
    而 GML 的 ``string()`` 规则是“整数无小数、非整数两位小数”
    （``"12.30"`` / ``"19"``），Python 的 ``str(float)`` 会写成
    ``"12.3"`` / ``"19.0"``，一旦替换就必然算错。

    这里用 :func:`~drr_server.utils.gml_number_str` 额外生成一组候选，
    只是为了容忍“框架把参数做了规范化”的边缘情况（例如
    ``"19.0"`` 被读成 ``19``），第一候选始终是原样字符串。
    """
    provided = password or ""
    raw_values = (exp, all_score, subtitle, rating)
    gml_values = tuple(gml_number_str(value) for value in raw_values)

    candidates: list[str] = [
        update_password(online_id, nickname, *raw_values),
        update_password(online_id, nickname, *gml_values),
    ]
    for candidate in candidates:
        if candidate == provided:
            return VerifyResult(ok=True, expected=candidate, provided=provided)
    return VerifyResult(
        ok=False,
        reason="update_password_mismatch",
        expected=candidates[0],
        provided=provided,
    )


def verify_score_upload(
    *,
    online_id: str,
    song_id: object,
    difficulty: object,
    hard_level: object,
    death_mode: object,
    client_date: str,
    client_time: str,
    score: object,
    password: str,
) -> VerifyResult:
    """校验 ``scoreupload.php`` 的 password。"""
    expected = score_upload_password(
        online_id,
        song_id,
        difficulty,
        hard_level,
        death_mode,
        client_date,
        client_time,
        score,
    )
    provided = password or ""
    if expected == provided:
        return VerifyResult(ok=True, expected=expected, provided=provided)
    return VerifyResult(
        ok=False,
        reason="score_password_mismatch",
        expected=expected,
        provided=provided,
    )


def enforce(result: VerifyResult, *, strict: bool, endpoint: str) -> bool:
    """按策略决定是否放行。

    返回 ``True`` 表示放行。宽松模式下对失败请求只记一条 warning，
    并把校验串截断，避免日志泄露完整凭据。
    """
    if result.ok:
        return True
    if strict:
        logger.warning(
            "[%s] 验签失败（严格模式，已拒绝）：%s provided=%s expected=%s",
            endpoint,
            result.reason,
            (result.provided or "")[:8],
            (result.expected or "")[:8],
        )
        return False
    logger.info(
        "[%s] 验签未通过（宽松模式，已放行）：%s", endpoint, result.reason
    )
    return True
