import asyncio
import base64
import json
import logging
import logging.handlers
import os
import re
import sys
import time
import traceback
import threading
import uuid
from dataclasses import asdict
from datetime import datetime

import websockets

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# admin module removed — is_admin / is_group_feature_enabled / ensure_group_settings / make_group_key
# are now in bot_state
from config import (
    WS_URL,
    WS_TOKEN,
    WX_WS_URL,
    WX_WS_PING_INTERVAL,
    WX_WS_PING_TIMEOUT,
    WX_WXID,
    WX_SAFEKEY,
    ENABLE_QQ,
    ENABLE_WECHAT,
    ADMINS,
    SUBSCRIPTION_CHECK_INTERVAL,
    CURRENT_SEASON,
)
from ow2 import (
    LatestMatchQuery, MatchStrengthAnalyzer, PlayerProfileQuery, AuthExpiredError,
    StreakQuery, HeroStatsQuery, CompareQuery, LeaderboardQuery,
    HeroBillboardQuery, NewsQuery, RankHistoryQuery, PlayerDistributionQuery,
)
from ow2.weeklysummary import WeeklySummaryQuery
from renderer import html_to_image

from bot_state import (BINDINGS_FILE, SUBSCRIPTIONS_FILE, GROUP_SETTINGS_FILE,
    _json_backup_path, _load_json_file, _atomic_save_json, _cleanup_temp_reports,
    _user_bindings, _group_members, _migrate_old_bindings,
    _load_bindings_from_disk, _save_bindings_to_disk,
    _get_user_binding, _set_user_binding, _ensure_group_membership, _get_group_bindings,
    _unbind_confirm, UNBIND_CONFIRM_TIMEOUT, _subscribe_confirm, SUBSCRIBE_CONFIRM_TIMEOUT,
    _subscriptions, _load_subscriptions_from_disk, _save_subscriptions_to_disk,
    _infer_subscription_platform, _get_subscription_platform,
    _set_subscription, _clear_subscription, _get_user_subscription,
    _load_group_settings_from_disk, _save_group_settings_to_disk,
    _group_settings, _rate_limits, MAX_QUERIES_PER_MINUTE,
    check_rate_limit, _player_name_in_use,
    ensure_group_settings, make_group_key, is_admin, is_group_feature_enabled)

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR    = os.path.join(BASE_DIR, "config")
LOGS_DIR      = os.path.join(BASE_DIR, "logs")
REPORTS_DIR   = os.path.join(BASE_DIR, "reports")
os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

# ── 文件日志（按日期分割）────────────────────────────────────────────────────

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_USE_COLOR = False


def _enable_windows_virtual_terminal() -> bool:
    if os.name != "nt":
        return True

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        enable_vt = 0x0004
        enabled = False
        for handle_id in (-11, -12):
            handle = kernel32.GetStdHandle(handle_id)
            if handle in (0, -1):
                continue
            mode = ctypes.c_uint()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                continue
            if kernel32.SetConsoleMode(handle, mode.value | enable_vt):
                enabled = True
        return enabled
    except Exception:
        return False


def _strip_ansi(value: str) -> str:
    return _ANSI_RE.sub("", value)


def _clean_log_message(msg: str) -> str:
    text = _strip_ansi(str(msg)).replace("\r\n", "\n").replace("\r", "\n")
    parts = [part.strip() for part in text.split("\n") if part.strip()]
    return " | ".join(parts) if parts else "-"


class _BotFileFormatter(logging.Formatter):
    def format(self, record):
        timestamp = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        level = record.levelname.ljust(5)
        event = str(getattr(record, "event", "LOG")).ljust(4)
        uid = str(getattr(record, "uid_full", "-"))
        source = str(getattr(record, "source_label", "")).strip()
        message = _clean_log_message(record.getMessage())
        parts = [timestamp, level, event, f"uid={uid}"]
        if source:
            parts.append(f"src={source}")
        parts.append(message)
        return " | ".join(parts)


class _BotConsoleFormatter(logging.Formatter):
    _EVENT_COLOR = {
        "SYS": "\033[96m",
        "CMD": "\033[94m",
        "STEP": "\033[93m",
        "OK": "\033[92m",
        "ERR": "\033[91m",
    }
    _RESET = "\033[0m"
    _DIM = "\033[90m"
    _BOLD = "\033[1m"

    def format(self, record):
        timestamp = self.formatTime(record, "%H:%M:%S")
        event = str(getattr(record, "event", "LOG")).upper()
        event_label = event[:4].ljust(4)
        uid = str(getattr(record, "uid_short", "-")).ljust(6)
        source = str(getattr(record, "source_label", "")).strip()
        message = _clean_log_message(record.getMessage())
        source_text = f" [{source}]" if source else ""
        plain = f"{timestamp}  {event_label}  {uid}{source_text}  {message}"
        if not _USE_COLOR:
            return plain

        event_color = self._EVENT_COLOR.get(event, self._DIM)
        source_color = getattr(record, "source_color", event_color)
        message_prefix = self._BOLD if event == "CMD" else ""
        return (
            f"{self._DIM}{timestamp}{self._RESET}  "
            f"{event_color}{event_label}{self._RESET}  "
            f"{self._DIM}{uid}{self._RESET}"
            f"{source_color}{source_text}{self._RESET}  "
            f"{message_prefix}{event_color}{message}{self._RESET}"
        )


def _setup_logging():
    global _USE_COLOR

    today = datetime.now().strftime("%Y%m%d")
    archive_log_file = os.path.join(LOGS_DIR, f"bot_{today}.log")
    latest_log_file = os.path.join(LOGS_DIR, "bot.log")

    logger = logging.getLogger("ow2bot")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    file_formatter = _BotFileFormatter()

    archive_handler = logging.FileHandler(archive_log_file, mode="a", encoding="utf-8-sig")
    archive_handler.setFormatter(file_formatter)
    logger.addHandler(archive_handler)

    latest_handler = logging.FileHandler(latest_log_file, mode="w", encoding="utf-8-sig")
    latest_handler.setFormatter(file_formatter)
    logger.addHandler(latest_handler)

    _USE_COLOR = bool(getattr(sys.stdout, "isatty", lambda: False)()) and _enable_windows_virtual_terminal()
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(_BotConsoleFormatter())
    logger.addHandler(console_handler)

    return logger


_log = _setup_logging()

# ── 单例（启动时初始化一次）──────────────────────────────────────────────────

querier    = LatestMatchQuery()
analyzer   = MatchStrengthAnalyzer()
profiler   = PlayerProfileQuery()
rank_history = RankHistoryQuery()
streaker   = StreakQuery()
hero_stats = HeroStatsQuery()
comparer   = CompareQuery()
leaderboard   = LeaderboardQuery()
billboard     = HeroBillboardQuery()
player_distribution = PlayerDistributionQuery()
news_query    = NewsQuery()
weekly_summary = WeeklySummaryQuery()

# 帮助图预渲染缓存
_help_image: bytes | None = None


async def _render_menu_image(template_name: str) -> bytes:
    if template_name.startswith("admin/"):
        return await html_to_image(os.path.join(BASE_DIR, template_name))
    return await html_to_image(os.path.join(BASE_DIR, "templates", template_name))


async def _send_cached_menu(ws, msg_type, target_id, user_id, image_cache, template_name: str,
                            fallback_text: str, send_text_fn, send_image_fn):
    if image_cache:
        await send_image_fn(ws, msg_type, target_id, image_cache)
        return

    try:
        image = await _render_menu_image(template_name)
        await send_image_fn(ws, msg_type, target_id, image)
    except Exception as e:
        log_err(user_id, str(e))
        await send_text_fn(ws, msg_type, target_id, fallback_text)


async def _prerender_help():
    global _help_image, _admin_help_image
    try:
        _help_image = await _render_menu_image("help.html")
        log_sys("帮助图预渲染完成")
    except Exception as e:
        log_err("sys", f"帮助图预渲染失败：{e}")

    try:
        _admin_help_image = await _render_menu_image("admin/templates/admin_help.html")
        log_sys("管理员菜单预渲染完成")
    except Exception as e:
        log_err("sys", f"管理员菜单预渲染失败：{e}")

_user_bindings, _group_members = _load_bindings_from_disk()



MAX_CONCURRENT_MESSAGES = 8
_message_semaphore: asyncio.Semaphore | None = None


def _get_message_semaphore() -> asyncio.Semaphore:
    global _message_semaphore
    if _message_semaphore is None:
        _message_semaphore = asyncio.Semaphore(MAX_CONCURRENT_MESSAGES)
    return _message_semaphore


async def _run_limited_message_task(task_name: str, coro):
    sem = _get_message_semaphore()
    async with sem:
        try:
            await coro
        except Exception as e:
            log_err("sys", f"{task_name} failed: {e}")
            traceback.print_exc()

# ── 控制台日志 ────────────────────────────────────────────────────────────────

_R  = "\033[0m"
_GR = "\033[90m"   # 灰
_CY = "\033[96m"   # 青
_GN = "\033[92m"   # 绿
_RD = "\033[91m"   # 红
_YL = "\033[93m"   # 黄
_BD = "\033[1m"    # 粗体
_MG = "\033[95m"   # 紫（微信）

_SRC_COLOR = {"private": _CY, "group": _GR, "wx_private": _MG, "wx_group": _MG}
_SRC_LABEL = {"private": "私聊", "group": "群聊", "wx_private": "微信私", "wx_group": "微信群"}


def _ts():
    return datetime.now().strftime("%H:%M:%S")

def _uid_short(uid) -> str:
    """取 uid 后6位，避免长数字撑宽日志"""
    s = str(uid)
    return s[-6:] if len(s) > 6 else s.ljust(6)

def _write_log(level: int, event: str, uid, msg: str, source: str | None = None):
    source_label = _SRC_LABEL.get(source or "", source or "")
    source_color = _SRC_COLOR.get(source or "", _CY)
    _log.log(
        level,
        _clean_log_message(msg),
        extra={
            "event": event,
            "uid_full": str(uid),
            "uid_short": _uid_short(uid),
            "source_label": source_label,
            "source_color": source_color,
        },
    )


def log_sys(msg: str):
    _write_log(logging.INFO, "SYS", "system", msg)

def log_cmd(uid, msg_type: str, text: str):
    _write_log(logging.INFO, "CMD", uid, text, source=msg_type)

def log_step(uid, msg: str):
    _write_log(logging.INFO, "STEP", uid, msg)

def log_ok(uid, msg: str):
    _write_log(logging.INFO, "OK", uid, msg)

def log_err(uid, msg: str):
    _write_log(logging.ERROR, "ERR", uid, msg)

# ── 发消息工具（NapCat / OneBot）────────────────────────────────────────────

async def _send(ws, action: str, params: dict):
    await ws.send(json.dumps({"action": action, "params": params}, ensure_ascii=False))

async def send_text(ws, msg_type: str, target_id: int, text: str):
    action = "send_private_msg" if msg_type == "private" else "send_group_msg"
    key    = "user_id"          if msg_type == "private" else "group_id"
    await _send(ws, action, {key: target_id, "message": [{"type": "text", "data": {"text": text}}]})

async def send_image(ws, msg_type: str, target_id: int, image_bytes: bytes):
    action = "send_private_msg" if msg_type == "private" else "send_group_msg"
    key    = "user_id"          if msg_type == "private" else "group_id"
    b64    = base64.b64encode(image_bytes).decode()
    await _send(ws, action, {key: target_id, "message": [{"type": "image", "data": {"file": f"base64://{b64}"}}]})

# ── 发消息工具（千寻微信框架）────────────────────────────────────────────────

async def wx_send_text(ws, target_wxid: str, text: str):
    text = _sanitize_wechat_text(text)
    payload = {
        "wxid":      WX_WXID,
        "safekey":   WX_SAFEKEY,
        "type":      "sendText",
        "data": {
            "wxid": target_wxid,
            "msg":  text,
        },
    }
    await ws.send(json.dumps(payload, ensure_ascii=False))


_WECHAT_TEXT_REPLACEMENTS = (
    ("⚠️", "[警告] "),
    ("ℹ️", "[提示] "),
    ("✅", "[成功] "),
    ("❌", "[失败] "),
    ("⏳", "[处理中] "),
    ("🏆", "[连胜] "),
    ("💀", "[连败] "),
    ("🔥", "[火热] "),
)


def _sanitize_wechat_text(text: str) -> str:
    sanitized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    for source, target in _WECHAT_TEXT_REPLACEMENTS:
        sanitized = sanitized.replace(source, target)
    sanitized = sanitized.replace("\ufe0f", "").replace("\u200d", "")
    sanitized = re.sub(r"[\u2600-\u27BF]", "", sanitized)
    sanitized = re.sub(r"[\U00010000-\U0010FFFF]", "", sanitized)
    sanitized = re.sub(r"(\[[^\]]+\])\s+", r"\1 ", sanitized)
    return sanitized

async def wx_send_image(ws, target_wxid: str, image_bytes: bytes):
    b64 = base64.b64encode(image_bytes).decode()
    payload = {
        "wxid":      WX_WXID,
        "safekey":   WX_SAFEKEY,
        "type":      "sendImage",
        "data": {
            "wxid":     target_wxid,
            "path":     f"base64,{b64}",
            "fileName": "image.png",
        },
    }
    await ws.send(json.dumps(payload, ensure_ascii=False))



async def _send_platform_text(platform: str, msg_type: str, target_id, text: str):
    if platform == "wechat":
        if not (_wx_ws and ENABLE_WECHAT):
            raise RuntimeError("微信机器人未连接")
        await wx_send_text(_wx_ws, str(target_id), text)
        return

    if not (_qq_ws and ENABLE_QQ):
        raise RuntimeError("QQ 机器人未连接")
    if not str(target_id).isdigit():
        raise RuntimeError(f"QQ 目标不是纯数字: {target_id}")
    await send_text(_qq_ws, msg_type, int(target_id), text)


def _split_group_key(group_key: str) -> tuple[str, str]:
    if ":" not in group_key:
        raise ValueError("群标识格式不正确，应为 platform:group_id")
    platform, group_id = group_key.split(":", 1)
    platform = platform.strip()
    group_id = group_id.strip()
    if not platform or not group_id:
        raise ValueError("群标识格式不正确，应为 platform:group_id")
    return platform, group_id



# ── 指令处理 ──────────────────────────────────────────────────────────────────

HELP_TEXT = (
    "OW2 战绩查询\n"
    "━━━━━━━━━━━━━━━\n"
    "/查询 玩家名#数字 [休闲/竞技]  近12场强度分析\n"
    "/休闲查询 玩家名#数字  休闲近12场分析（兼容旧写法）\n"
    "/竞技查询 玩家名#数字  竞技近12场分析（兼容旧写法）\n"
    "/休闲 玩家名#数字  最新一场休闲对局\n"
    "/对局 [n] 玩家名#数字  查询第n场对局\n"
    "/竞技 [n] 玩家名#数字  查询竞技第n场\n"
    "━━━━━━━━━━━━━━━\n"
    "/玩家 玩家名#数字 [赛季] [竞技/休闲]  查询个人资料\n"
    "  赛季可选：19/20/21/22/当前，不填则为当前赛季\n"
    "/赛季总结 玩家名#数字  国服赛季段位汇总（S19-S22）\n"
    "/总结 玩家名#数字 [今日/昨日/上周/本周] [休闲/竞技]  生成周期战绩总结\n"
    "/英雄 玩家名#数字 [竞技/休闲]  查询英雄统计\n"
    "/英雄排名 玩家名#数字  英雄全服排名\n"
    "/对比 玩家A 玩家B  对比两名玩家\n"
    "━━━━━━━━━━━━━━━\n"
    "/绑定 玩家名#数字  绑定你的ID\n"
    "/解绑             解除绑定\n"
    "/排行             群内段位排行\n"
    "/分布             本群已绑定玩家分布\n"
    "/全局排行         所有绑定玩家排行\n"
    "/全局分布         所有绑定玩家分布\n"
    "━━━━━━━━━━━━━━━\n"
    "/资讯             活动资讯\n"
    "/调整             英雄调整\n"
    "━━━━━━━━━━━━━━━\n"
    "/订阅 玩家名#数字 模式  订阅战绩推送\n"
    "  模式可选：休闲、竞技、全部\n"
    "/订阅            查看我的订阅\n"
    "/取消订阅        取消订阅\n"
    "（订阅仅限私聊使用）\n"
    "━━━━━━━━━━━━━━━\n"
    "/帮助             显示本帮助"
)


def _auth_expired_reply() -> str:
    _schedule_admin_alert("auth.expired", "检测到接口认证已过期，请尽快更新 config/auth.json 中的 Cookie。", cooldown_seconds=1800)
    return "❌ 认证已过期，请更新 auth.json 中的 Cookie"


def _build_streak_reaction(streak_data: dict | None) -> str | None:
    if not streak_data:
        return None

    streak_type = streak_data.get("streak_type")
    count = int(streak_data.get("count", 0) or 0)
    game_mode = streak_data.get("game_mode", "leisure")
    mode_label = "竞技" if game_mode == "sport" else "休闲"

    if streak_type == "连胜" and count >= 2:
        if count >= 5:
            return f"🔥 {mode_label}已经 {count} 连胜了，状态很猛，继续冲。"
        if count >= 3:
            return f"👏 {mode_label}已经 {count} 连胜，手感明显在线。"
        return f"🙂 {mode_label}目前有个 {count} 连胜，小节奏不错。"

    if streak_type == "连败" and count >= 2:
        if count >= 5:
            return f"抱抱，{mode_label}已经 {count} 连败了，先别急，这波更适合稳一稳心态。"
        if count >= 3:
            return f"别灰心，{mode_label}最近 {count} 连败，先看看这几把里最容易出问题的点。"
        return f"这会儿 {mode_label}有点小逆风，先别急，我们看看问题出在哪。"

    return None


def _build_streak_reaction_by_count(streak_data: dict | None) -> str | None:
    if not streak_data:
        return None

    streak_type = streak_data.get("streak_type")
    count = int(streak_data.get("count", 0) or 0)
    game_mode = streak_data.get("game_mode", "leisure")
    mode_label = "竞技" if game_mode == "sport" else "休闲"

    if streak_type == "连胜" and count >= 2:
        if count >= 8:
            return f"🔥 {mode_label}已经 {count} 连胜了，这波真的是热手启动，状态特别猛。"
        if count >= 6:
            return f"🔥 {mode_label}这 {count} 连胜很硬，节奏和手感都很顺，继续冲。"
        if count >= 4:
            return f"👏 {mode_label}已经 {count} 连胜，今天状态明显在线。"
        if count >= 3:
            return f"🙂 {mode_label}已经 {count} 连胜，小连胜已经起来了。"
        return f"🙂 {mode_label}目前有个 {count} 连胜，可以继续把节奏稳住。"

    if streak_type == "连败" and count >= 2:
        if count >= 8:
            return f"抱抱，{mode_label}已经 {count} 连败了，这时候更重要的是先把心态稳住。"
        if count >= 6:
            return f"别急，{mode_label}这 {count} 连败确实有点难受，不过慢慢找问题就能拉回来。"
        if count >= 4:
            return f"别灰心，{mode_label}已经 {count} 连败了，先看看这几把最稳定能改的点。"
        if count >= 3:
            return f"这波 {mode_label}有点逆风，现在最适合先稳一把，不用急。"
        return f"这会儿 {mode_label}有点小逆风，先别急，我们看看问题出在哪。"

    return None


async def _send_streak_reaction(ws, msg_type: str, target_id, user_id, player_name: str,
                                game_mode: str = "leisure", send_text_fn=None):
    if send_text_fn is None:
        send_text_fn = send_text

    try:
        streak_data = await asyncio.get_running_loop().run_in_executor(
            None, lambda: streaker.query_streak(player_name, game_mode=game_mode)
        )
        streak_reply = _build_streak_reaction_by_count(streak_data)
        if streak_reply:
            await send_text_fn(ws, msg_type, target_id, streak_reply)
    except AuthExpiredError:
        raise
    except Exception as e:
        log_err(user_id, f"连胜连败提示失败: {e}")


async def handle_analyze(ws, msg_type: str, target_id, user_id, player_name: str,
                         game_mode: str = "leisure",
                         send_text_fn=None, send_image_fn=None):
    """近12场强度分析"""
    if send_text_fn is None:  send_text_fn  = send_text
    if send_image_fn is None: send_image_fn = send_image

    t0 = time.time()
    report_path = os.path.join(REPORTS_DIR, f".report_{uuid.uuid4().hex[:8]}.html")
    mode_label = "竞技" if game_mode == "sport" else "休闲"

    log_cmd(user_id, msg_type, f"/查询({mode_label}) {player_name}")
    await send_text_fn(ws, msg_type, target_id, f"⏳ 分析中：{player_name}（{mode_label}近12场，约需15s）")

    try:
        log_step(user_id, "拉取近12场数据...")
        result = await asyncio.get_running_loop().run_in_executor(
            None, lambda: analyzer.analyze_matches(player_name, output_path=report_path, game_mode=game_mode)
        )
        if not result:
            log_err(user_id, f"未找到玩家：{player_name}")
            await send_text_fn(ws, msg_type, target_id, f"❌ 未找到玩家：{player_name}")
            return

        log_step(user_id, "渲染截图中...")
        image = await html_to_image(report_path)
        await send_image_fn(ws, msg_type, target_id, image)
        log_ok(user_id, f"发送完成  {time.time() - t0:.1f}s")

    except AuthExpiredError:
        log_err(user_id, "认证过期")
        await send_text_fn(ws, msg_type, target_id, _auth_expired_reply())
    except Exception as e:
        log_err(user_id, str(e))
        await send_text_fn(ws, msg_type, target_id, "❌ 查询失败，请稍后重试")
        traceback.print_exc()
    finally:
        if os.path.exists(report_path):
            os.unlink(report_path)


async def handle_query(ws, msg_type: str, target_id, user_id, player_name: str,
                       nth: int = 1, game_mode: str = "leisure",
                       send_text_fn=None, send_image_fn=None):
    """查询第 nth 场对局"""
    if send_text_fn is None:  send_text_fn  = send_text
    if send_image_fn is None: send_image_fn = send_image

    t0 = time.time()
    report_path = os.path.join(REPORTS_DIR, f".report_{uuid.uuid4().hex[:8]}.html")
    mode_label = "竞技" if game_mode == "sport" else "休闲"
    nth_desc = "最新" if nth == 1 else f"第{nth}场"

    log_cmd(user_id, msg_type, f"/对局:{nth}({mode_label}) {player_name}")
    await send_text_fn(ws, msg_type, target_id, f"⏳ 查询中：{player_name}（{mode_label} · {nth_desc}对局）")

    try:
        log_step(user_id, f"查询{nth_desc}对局数据...")
        match_data = await asyncio.get_running_loop().run_in_executor(
            None, lambda: querier.query_nth_match(player_name, nth, game_mode=game_mode)
        )
        if not match_data:
            log_err(user_id, f"未找到玩家或对局：{player_name}")
            await send_text_fn(ws, msg_type, target_id, f"❌ 未找到玩家：{player_name}\n请确认格式为「昵称#数字」，或该玩家不足{nth}场对局")
            return

        log_step(user_id, "生成 HTML 报告...")
        querier.generate_html_report(match_data, output_path=report_path)

        log_step(user_id, "渲染截图中...")
        image = await html_to_image(report_path)
        await send_image_fn(ws, msg_type, target_id, image)
        log_ok(user_id, f"发送完成  {time.time() - t0:.1f}s")

    except AuthExpiredError:
        log_err(user_id, "认证过期")
        await send_text_fn(ws, msg_type, target_id, _auth_expired_reply())
    except Exception as e:
        log_err(user_id, str(e))
        await send_text_fn(ws, msg_type, target_id, "❌ 查询失败，请稍后重试")
        traceback.print_exc()
    finally:
        if os.path.exists(report_path):
            os.unlink(report_path)


async def handle_profile(ws, msg_type: str, target_id, user_id, player_name: str,
                         game_mode: str = "leisure", season: str | None = None,
                         send_text_fn=None, send_image_fn=None):
    """查询玩家个人资料"""
    if send_text_fn is None:  send_text_fn  = send_text
    if send_image_fn is None: send_image_fn = send_image

    t0 = time.time()
    report_path = os.path.join(REPORTS_DIR, f".report_{uuid.uuid4().hex[:8]}.html")
    mode_label = "竞技" if game_mode == "sport" else "休闲"
    
    # 构建赛季标签
    if season is None:
        season_label = f" S{CURRENT_SEASON}"
        season_param = CURRENT_SEASON
    elif season == "":
        season_label = f" S{CURRENT_SEASON}"
        season_param = CURRENT_SEASON
    else:
        season_label = f" S{season}"
        season_param = season

    log_cmd(user_id, msg_type, f"/玩家({mode_label}{season_label}) {player_name}")
    await send_text_fn(ws, msg_type, target_id, f"⏳ 查询中：{player_name}（{mode_label}{season_label}）")

    try:
        log_step(user_id, "拉取玩家资料...")
        profile_data = await asyncio.get_running_loop().run_in_executor(
            None, lambda: profiler.query_profile(player_name, game_mode=game_mode, season=season_param)
        )
        if not profile_data:
            log_err(user_id, f"未找到玩家：{player_name}")
            await send_text_fn(ws, msg_type, target_id, f"❌ 未找到玩家：{player_name}")
            return

        log_step(user_id, "生成 HTML 报告...")
        profiler.generate_html_report(profile_data, output_path=report_path)

        log_step(user_id, "渲染截图中...")
        image = await html_to_image(report_path)
        await send_image_fn(ws, msg_type, target_id, image)
        log_ok(user_id, f"发送完成  {time.time() - t0:.1f}s")

    except AuthExpiredError:
        log_err(user_id, "认证过期")
        await send_text_fn(ws, msg_type, target_id, _auth_expired_reply())
    except Exception as e:
        log_err(user_id, str(e))
        await send_text_fn(ws, msg_type, target_id, "❌ 查询失败，请稍后重试")
        traceback.print_exc()
    finally:
        if os.path.exists(report_path):
            os.unlink(report_path)


async def handle_rank_history(ws, msg_type: str, target_id, user_id, player_name: str,
                              season: str | None = None, send_text_fn=None, send_image_fn=None):
    """查询玩家竞技历史段位"""
    if send_text_fn is None:
        send_text_fn = send_text
    if send_image_fn is None:
        send_image_fn = send_image

    t0 = time.time()
    report_path = os.path.join(REPORTS_DIR, f".report_{uuid.uuid4().hex[:8]}.html")
    season_label = "全部赛季" if season is None else ("当前赛季" if season == "" else f"S{season}")

    log_cmd(user_id, msg_type, f"/历史段位 {player_name} ({season_label})")
    await send_text_fn(ws, msg_type, target_id, f"⏳ 查询中：{player_name}（{season_label}）")

    try:
        log_step(user_id, "拉取历史段位...")
        history_data = await asyncio.get_running_loop().run_in_executor(
            None, lambda: rank_history.query_rank_history(player_name, season=season)
        )
        if not history_data:
            log_err(user_id, f"未找到玩家：{player_name}")
            await send_text_fn(ws, msg_type, target_id, f"❌ 未找到玩家：{player_name}")
            return

        if history_data.get("invalid_season"):
            await send_text_fn(ws, msg_type, target_id, "❌ 赛季格式无效，请使用纯数字或 S+数字，例如：15 或 S15")
            return

        entries = history_data.get("entries", [])
        if not entries:
            if season is None:
                await send_text_fn(ws, msg_type, target_id, f"❌ 未找到 {player_name} 的历史段位数据")
            else:
                season_text = "当前赛季" if season == "" else f"S{season}"
                await send_text_fn(ws, msg_type, target_id, f"❌ {player_name} 在 {season_text} 没有可用段位数据")
            return

        log_step(user_id, "生成 HTML 报告...")
        rank_history.generate_html_report(history_data, output_path=report_path)

        log_step(user_id, "渲染截图中...")
        image = await html_to_image(report_path)
        await send_image_fn(ws, msg_type, target_id, image)
        log_ok(user_id, f"发送完成  {time.time() - t0:.1f}s")

    except AuthExpiredError:
        log_err(user_id, "认证过期")
        await send_text_fn(ws, msg_type, target_id, _auth_expired_reply())
    except Exception as e:
        log_err(user_id, str(e))
        await send_text_fn(ws, msg_type, target_id, "❌ 查询失败，请稍后重试")
        traceback.print_exc()
    finally:
        if os.path.exists(report_path):
            os.unlink(report_path)


async def handle_season_summary(ws, msg_type: str, target_id, user_id, player_name: str,
                                send_text_fn=None, send_image_fn=None):
    """查询赛季总结，展示国服 S19-S22 段位汇总"""
    if send_text_fn is None:
        send_text_fn = send_text
    if send_image_fn is None:
        send_image_fn = send_image

    t0 = time.time()
    report_path = os.path.join(REPORTS_DIR, f".report_{uuid.uuid4().hex[:8]}.html")

    log_cmd(user_id, msg_type, f"/赛季总结 {player_name}")
    await send_text_fn(ws, msg_type, target_id, f"⏳ 查询中：{player_name}（赛季总结）")

    try:
        log_step(user_id, "拉取赛季总结数据...")
        summary_data = await asyncio.get_running_loop().run_in_executor(
            None, lambda: rank_history.query_season_summary(player_name)
        )
        if not summary_data:
            log_err(user_id, f"未找到玩家：{player_name}")
            await send_text_fn(ws, msg_type, target_id, f"❌ 未找到玩家：{player_name}")
            return

        entries = summary_data.get("entries", [])
        if not entries:
            await send_text_fn(ws, msg_type, target_id, f"❌ {player_name} 没有可用的赛季数据")
            return

        log_step(user_id, "生成赛季总结报告...")
        rank_history.generate_season_summary_html(summary_data, output_path=report_path)

        log_step(user_id, "渲染截图中...")
        image = await html_to_image(report_path)
        await send_image_fn(ws, msg_type, target_id, image)
        log_ok(user_id, f"发送完成  {time.time() - t0:.1f}s")

    except AuthExpiredError:
        log_err(user_id, "认证过期")
        await send_text_fn(ws, msg_type, target_id, _auth_expired_reply())
    except Exception as e:
        log_err(user_id, str(e))
        await send_text_fn(ws, msg_type, target_id, "❌ 查询失败，请稍后重试")
        traceback.print_exc()
    finally:
        if os.path.exists(report_path):
            os.unlink(report_path)


async def handle_weekly_summary(ws, msg_type: str, target_id, user_id, player_name: str,
                                period: str = "today", game_mode: str = "leisure",
                                send_text_fn=None, send_image_fn=None):
    """生成今日/昨日/本周/上周总结，支持休闲和竞技分开查询。"""
    if send_text_fn is None:
        send_text_fn = send_text
    if send_image_fn is None:
        send_image_fn = send_image

    t0 = time.time()
    report_path = os.path.join(REPORTS_DIR, f".report_{uuid.uuid4().hex[:8]}.html")
    mode_label = "竞技" if game_mode == "sport" else "休闲"
    period_label = {
        "today": "今日",
        "yesterday": "昨日",
        "last_week": "上周",
        "week": "本周",
    }.get(period, "今日")

    log_cmd(user_id, msg_type, f"/总结 {player_name} {period_label} {mode_label}")
    await send_text_fn(ws, msg_type, target_id, f"⏳ 正在生成总结：{player_name}（{period_label} · {mode_label}）")

    try:
        data = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: weekly_summary.query_weekly_summary(
                player_name,
                game_mode=game_mode,
                period=period,
                max_pages=20,
            ),
        )
        if not data:
            await send_text_fn(ws, msg_type, target_id, f"❌ 未找到玩家：{player_name}")
            return

        weekly_summary.generate_html_report(data, output_path=report_path)
        image = await html_to_image(report_path)
        await send_image_fn(ws, msg_type, target_id, image)
        log_ok(user_id, f"发送完成 {time.time() - t0:.1f}s")

    except AuthExpiredError:
        await send_text_fn(ws, msg_type, target_id, _auth_expired_reply())
    except Exception as e:
        log_err(user_id, str(e))
        await send_text_fn(ws, msg_type, target_id, "❌ 总结生成失败，请稍后重试")
        traceback.print_exc()
    finally:
        if os.path.exists(report_path):
            os.unlink(report_path)


async def handle_herostats(ws, msg_type: str, target_id, user_id, player_name: str,
                           game_mode: str = "leisure", send_text_fn=None, send_image_fn=None):
    if send_text_fn is None:  send_text_fn  = send_text
    if send_image_fn is None: send_image_fn = send_image
    t0 = time.time()
    report_path = os.path.join(REPORTS_DIR, f".report_{uuid.uuid4().hex[:8]}.html")
    mode_label = "竞技" if game_mode == "sport" else "休闲"
    log_cmd(user_id, msg_type, f"/英雄({mode_label}) {player_name}")
    await send_text_fn(ws, msg_type, target_id, f"⏳ 查询中：{player_name}（{mode_label}）")
    try:
        data = await asyncio.get_running_loop().run_in_executor(
            None, lambda: hero_stats.query_hero_stats(player_name, game_mode=game_mode)
        )
        if not data:
            await send_text_fn(ws, msg_type, target_id, f"❌ 未找到玩家：{player_name}")
            return
        hero_stats.generate_html_report(data, output_path=report_path)
        image = await html_to_image(report_path)
        await send_image_fn(ws, msg_type, target_id, image)
        log_ok(user_id, f"发送完成  {time.time() - t0:.1f}s")
    except AuthExpiredError:
        await send_text_fn(ws, msg_type, target_id, _auth_expired_reply())
    except Exception as e:
        await send_text_fn(ws, msg_type, target_id, "❌ 查询失败，请稍后重试")
        traceback.print_exc()
    finally:
        if os.path.exists(report_path):
            os.unlink(report_path)


async def handle_compare(ws, msg_type: str, target_id, user_id,
                         player_a: str, player_b: str,
                         send_text_fn=None, send_image_fn=None):
    if send_text_fn is None:  send_text_fn  = send_text
    if send_image_fn is None: send_image_fn = send_image
    t0 = time.time()
    report_path = os.path.join(REPORTS_DIR, f".report_{uuid.uuid4().hex[:8]}.html")
    log_cmd(user_id, msg_type, f"/对比 {player_a} {player_b}")
    await send_text_fn(ws, msg_type, target_id, f"⏳ 对比中：{player_a} vs {player_b}")
    try:
        data = await asyncio.get_running_loop().run_in_executor(
            None, lambda: comparer.query_compare(player_a, player_b)
        )
        if not data:
            await send_text_fn(ws, msg_type, target_id, "❌ 未找到其中一名玩家，请检查名称")
            return
        comparer.generate_html_report(data, output_path=report_path)
        image = await html_to_image(report_path)
        await send_image_fn(ws, msg_type, target_id, image)
        log_ok(user_id, f"发送完成  {time.time() - t0:.1f}s")
    except AuthExpiredError:
        await send_text_fn(ws, msg_type, target_id, _auth_expired_reply())
    except Exception as e:
        await send_text_fn(ws, msg_type, target_id, "❌ 查询失败，请稍后重试")
        traceback.print_exc()
    finally:
        if os.path.exists(report_path):
            os.unlink(report_path)


async def handle_leaderboard(ws, msg_type: str, target_id, user_id,
                             send_text_fn=None, send_image_fn=None):
    if send_text_fn is None:  send_text_fn  = send_text
    if send_image_fn is None: send_image_fn = send_image

    # 排行榜仅群聊可用
    if msg_type != "group":
        await send_text_fn(ws, msg_type, target_id, "❌ 排行榜功能仅在群聊中可用")
        return

    # 获取当前群的绑定数据
    group_bindings = _get_group_bindings(target_id)
    if not group_bindings:
        await send_text_fn(ws, msg_type, target_id, "本群暂无绑定玩家，快来第一个绑定吧！\n使用 /绑定 玩家名#数字")
        return

    t0 = time.time()
    report_path = os.path.join(REPORTS_DIR, f".report_{uuid.uuid4().hex[:8]}.html")
    log_cmd(user_id, msg_type, "/排行")
    await send_text_fn(ws, msg_type, target_id, f"⏳ 拉取本群 {len(group_bindings)} 名玩家段位中...")
    try:
        board = await asyncio.get_running_loop().run_in_executor(
            None, lambda: leaderboard.query_leaderboard(group_bindings)
        )
        if not board:
            await send_text_fn(ws, msg_type, target_id, "❌ 获取段位失败，请稍后重试")
            return
        leaderboard.generate_html_report(
            board,
            output_path=report_path,
            highlight_user_id=str(user_id),
        )
        image = await html_to_image(report_path)
        await send_image_fn(ws, msg_type, target_id, image)
        log_ok(user_id, f"发送完成  {time.time() - t0:.1f}s")
    except AuthExpiredError:
        await send_text_fn(ws, msg_type, target_id, _auth_expired_reply())
    except Exception as e:
        await send_text_fn(ws, msg_type, target_id, "❌ 查询失败，请稍后重试")
        traceback.print_exc()
    finally:
        if os.path.exists(report_path):
            os.unlink(report_path)


async def handle_global_leaderboard(ws, msg_type: str, target_id, user_id,
                                    send_text_fn=None, send_image_fn=None):
    """全局排行榜：显示所有绑定玩家"""
    if send_text_fn is None:  send_text_fn  = send_text
    if send_image_fn is None: send_image_fn = send_image

    if not _user_bindings:
        await send_text_fn(ws, msg_type, target_id, "❌ 暂无绑定玩家")
        return

    t0 = time.time()
    report_path = os.path.join(REPORTS_DIR, f".report_{uuid.uuid4().hex[:8]}.html")
    log_cmd(user_id, msg_type, "/全局排行")
    await send_text_fn(ws, msg_type, target_id, f"⏳ 拉取全局 {len(_user_bindings)} 名玩家段位中...")
    try:
        board = await asyncio.get_running_loop().run_in_executor(
            None, lambda: leaderboard.query_leaderboard(_user_bindings)
        )
        if not board:
            await send_text_fn(ws, msg_type, target_id, "❌ 获取段位失败，请稍后重试")
            return
        leaderboard.generate_html_report(
            board,
            output_path=report_path,
            title="全局排行榜",
            highlight_user_id=str(user_id),
        )
        image = await html_to_image(report_path)
        await send_image_fn(ws, msg_type, target_id, image)
        log_ok(user_id, f"发送完成  {time.time() - t0:.1f}s")
    except AuthExpiredError:
        await send_text_fn(ws, msg_type, target_id, _auth_expired_reply())
    except Exception as e:
        await send_text_fn(ws, msg_type, target_id, "❌ 查询失败，请稍后重试")
        traceback.print_exc()
    finally:
        if os.path.exists(report_path):
            os.unlink(report_path)


_DISTRIBUTION_STATUS_REASONS = {
    "missing_player": "未找到玩家",
    "missing_bnet_id": "缺少玩家ID",
    "missing_province": "榜单无省份",
    "request_error": "接口请求失败",
}


def _build_distribution_summary(data: dict) -> str:
    total = int(data.get("total_bound", 0) or 0)
    resolved = int(data.get("resolved_count", 0) or 0)
    unresolved = int(data.get("unresolved_count", 0) or 0)
    top_province = data.get("top_province") or "无"
    top_count = int(data.get("top_count", 0) or 0)
    lines = [
        f"📊 分布统计：已识别 {resolved}/{total} 人，未识别 {unresolved} 人",
        f"🏆 最多省份：{top_province} {top_count} 人",
    ]

    unresolved_players = data.get("unresolved_players", []) or []
    if unresolved_players:
        reason_counts: dict[str, int] = {}
        for item in unresolved_players:
            status = item.get("status", "request_error")
            reason_counts[status] = reason_counts.get(status, 0) + 1
        reason_text = "，".join(
            f"{_DISTRIBUTION_STATUS_REASONS.get(status, status)} {count} 人"
            for status, count in sorted(reason_counts.items(), key=lambda pair: (-pair[1], pair[0]))
        )
        sample_text = "、".join(
            f"{item.get('display_name') or item.get('player_name')}({_DISTRIBUTION_STATUS_REASONS.get(item.get('status'), item.get('status'))})"
            for item in unresolved_players[:5]
        )
        lines.append(f"⚠️ 未识别原因：{reason_text}")
        lines.append(f"示例：{sample_text}" + (" ..." if len(unresolved_players) > 5 else ""))
    return "\n".join(lines)


async def _handle_distribution_report(ws, msg_type: str, target_id, user_id,
                                      bindings: dict, title: str, command_name: str,
                                      progress_label: str, empty_reply: str,
                                      send_text_fn=None, send_image_fn=None):
    if send_text_fn is None:
        send_text_fn = send_text
    if send_image_fn is None:
        send_image_fn = send_image

    if not bindings:
        await send_text_fn(ws, msg_type, target_id, empty_reply)
        return

    t0 = time.time()
    report_path = os.path.join(REPORTS_DIR, f".report_{uuid.uuid4().hex[:8]}.html")
    log_cmd(user_id, msg_type, command_name)
    await send_text_fn(ws, msg_type, target_id, progress_label.format(count=len(bindings)))

    try:
        data = await asyncio.get_running_loop().run_in_executor(
            None, lambda: player_distribution.query_distribution(bindings, title=title)
        )
        if not data:
            await send_text_fn(ws, msg_type, target_id, "❌ 获取玩家分布失败，请稍后重试")
            return
        if data.get("error") == "missing_billboard_token":
            await send_text_fn(
                ws,
                msg_type,
                target_id,
                "❌ 未配置 billboard_token\n请在 auth.json 中添加 \"billboard_token\" 字段",
            )
            return
        if data.get("resolved_count", 0) <= 0:
            await send_text_fn(
                ws,
                msg_type,
                target_id,
                "❌ 未能识别到任何玩家省份\n" + _build_distribution_summary(data),
            )
            return

        player_distribution.generate_html_report(data, output_path=report_path)
        image = await html_to_image(report_path)
        await send_image_fn(ws, msg_type, target_id, image)
        if data.get("unresolved_count", 0) > 0:
            await send_text_fn(ws, msg_type, target_id, _build_distribution_summary(data))
        log_ok(user_id, f"发送完成  {time.time() - t0:.1f}s")
    except AuthExpiredError:
        await send_text_fn(ws, msg_type, target_id, _auth_expired_reply())
    except Exception:
        await send_text_fn(ws, msg_type, target_id, "❌ 获取玩家分布失败，请稍后重试")
        traceback.print_exc()
    finally:
        if os.path.exists(report_path):
            os.unlink(report_path)


async def handle_distribution(ws, msg_type: str, target_id, user_id,
                              send_text_fn=None, send_image_fn=None):
    if msg_type != "group":
        if send_text_fn is None:
            send_text_fn = send_text
        await send_text_fn(ws, msg_type, target_id, "❌ 本群玩家分布仅在群聊中可用，可使用 /全局分布 查看全部绑定玩家")
        return

    group_bindings = _get_group_bindings(target_id)
    await _handle_distribution_report(
        ws,
        msg_type,
        target_id,
        user_id,
        group_bindings,
        title="本群玩家分布",
        command_name="/分布",
        progress_label="⏳ 统计本群 {count} 名绑定玩家分布中...",
        empty_reply="本群暂无绑定玩家，快来第一个绑定吧！\n使用 /绑定 玩家名#数字",
        send_text_fn=send_text_fn,
        send_image_fn=send_image_fn,
    )


async def handle_global_distribution(ws, msg_type: str, target_id, user_id,
                                     send_text_fn=None, send_image_fn=None):
    await _handle_distribution_report(
        ws,
        msg_type,
        target_id,
        user_id,
        _user_bindings,
        title="全局玩家分布",
        command_name="/全局分布",
        progress_label="⏳ 统计全局 {count} 名绑定玩家分布中...",
        empty_reply="❌ 暂无绑定玩家",
        send_text_fn=send_text_fn,
        send_image_fn=send_image_fn,
    )


async def handle_billboard(ws, msg_type: str, target_id, user_id, player_name: str,
                           send_text_fn=None, send_image_fn=None):
    if send_text_fn is None:  send_text_fn  = send_text
    if send_image_fn is None: send_image_fn = send_image
    t0 = time.time()
    report_path = os.path.join(REPORTS_DIR, f".report_{uuid.uuid4().hex[:8]}.html")
    log_cmd(user_id, msg_type, f"/英雄排名 {player_name}")
    await send_text_fn(ws, msg_type, target_id, f"⏳ 查询中：{player_name}")
    try:
        data = await asyncio.get_running_loop().run_in_executor(
            None, lambda: billboard.query_hero_billboard(player_name)
        )
        if not data:
            await send_text_fn(ws, msg_type, target_id, "❌ 获取英雄排名失败，请稍后重试")
            return
        if data.get("error") == "missing_billboard_token":
            await send_text_fn(
                ws,
                msg_type,
                target_id,
                "❌ 未配置 billboard_token\n请在 auth.json 中添加 \"billboard_token\" 字段",
            )
            return
        if data.get("error") == "player_not_found":
            await send_text_fn(
                ws,
                msg_type,
                target_id,
                f"❌ 未找到玩家：{player_name}",
            )
            return
        if data.get("error") == "missing_bnet_id":
            await send_text_fn(
                ws,
                msg_type,
                target_id,
                f"❌ 玩家缺少 bnetId，无法查询英雄排名：{player_name}",
            )
            return
        if data.get("error") == "billboard_query_failed":
            source = "覆盖配置" if data.get("source") == "override" else "搜索结果"
            await send_text_fn(
                ws,
                msg_type,
                target_id,
                f"❌ 已找到玩家，但英雄排名接口未返回数据\n玩家：{data.get('player_name', player_name)}\n来源：{source}",
            )
            return
        billboard.generate_html_report(data, output_path=report_path)
        image = await html_to_image(report_path)
        await send_image_fn(ws, msg_type, target_id, image)
        log_ok(user_id, f"发送完成  {time.time() - t0:.1f}s")
    except AuthExpiredError:
        await send_text_fn(ws, msg_type, target_id, _auth_expired_reply())
    except Exception as e:
        await send_text_fn(ws, msg_type, target_id, "❌ 获取失败，请稍后重试")
        traceback.print_exc()
    finally:
        if os.path.exists(report_path):
            os.unlink(report_path)


async def handle_news(ws, msg_type: str, target_id, user_id,
                      send_text_fn=None, send_image_fn=None):
    """查询活动资讯"""
    if send_text_fn is None:  send_text_fn  = send_text
    if send_image_fn is None: send_image_fn = send_image

    t0 = time.time()
    report_path = os.path.join(REPORTS_DIR, f".report_{uuid.uuid4().hex[:8]}.html")
    log_cmd(user_id, msg_type, "/资讯")
    await send_text_fn(ws, msg_type, target_id, "⏳ 获取活动资讯中...")

    try:
        success = await asyncio.get_running_loop().run_in_executor(
            None, lambda: news_query.generate_news_report(report_path)
        )
        if not success:
            await send_text_fn(ws, msg_type, target_id, "❌ 暂无活动资讯")
            return

        image = await html_to_image(report_path)
        await send_image_fn(ws, msg_type, target_id, image)
        log_ok(user_id, f"发送完成  {time.time() - t0:.1f}s")

    except Exception as e:
        log_err(user_id, str(e))
        message = str(e)
        if "未找到可用补丁说明页面" in message:
            await send_text_fn(ws, msg_type, target_id, "❌ 官网暂时没有可用的活动资讯页面，请稍后再试")
        else:
            await send_text_fn(ws, msg_type, target_id, "❌ 获取失败，请稍后重试")
        traceback.print_exc()
    finally:
        if os.path.exists(report_path):
            os.unlink(report_path)


async def handle_patch_notes(ws, msg_type: str, target_id, user_id,
                             send_text_fn=None, send_image_fn=None):
    """查询英雄调整"""
    if send_text_fn is None:  send_text_fn  = send_text
    if send_image_fn is None: send_image_fn = send_image

    t0 = time.time()
    report_path = os.path.join(REPORTS_DIR, f".report_{uuid.uuid4().hex[:8]}.html")
    log_cmd(user_id, msg_type, "/调整")
    await send_text_fn(ws, msg_type, target_id, "⏳ 获取英雄调整中...")

    try:
        success = await asyncio.get_running_loop().run_in_executor(
            None, lambda: news_query.generate_patch_report(report_path)
        )
        if not success:
            await send_text_fn(ws, msg_type, target_id, "❌ 暂无英雄调整信息")
            return

        image = await html_to_image(report_path)
        await send_image_fn(ws, msg_type, target_id, image)
        log_ok(user_id, f"发送完成  {time.time() - t0:.1f}s")

    except Exception as e:
        log_err(user_id, str(e))
        message = str(e)
        if "未找到可用补丁说明页面" in message:
            await send_text_fn(ws, msg_type, target_id, "❌ 官网暂时没有可用的英雄调整页面，请稍后再试")
        else:
            await send_text_fn(ws, msg_type, target_id, "❌ 获取失败，请稍后重试")
        traceback.print_exc()
    finally:
        if os.path.exists(report_path):
            os.unlink(report_path)


async def handle_subscribe(ws, msg_type: str, target_id, user_id, args: str,
                           send_text_fn=None, platform: str | None = None):
    """订阅玩家战绩推送
    
    用法：
    /订阅 玩家名#数字 休闲   - 订阅休闲模式
    /订阅 玩家名#数字 竞技   - 订阅竞技模式
    /订阅 玩家名#数字 全部   - 订阅两种模式
    /订阅                  - 查看当前订阅
    """
    if send_text_fn is None: send_text_fn = send_text
    uid = str(user_id)
    
    # 私聊限制
    if msg_type != "private":
        await send_text_fn(ws, msg_type, target_id, "❌ 订阅功能仅支持私聊使用")
        return
    
    # 无参数时显示当前订阅
    if not args:
        sub = _get_user_subscription(uid)
        if not sub:
            await send_text_fn(ws, msg_type, target_id, 
                "你还没有订阅任何玩家\n\n"
                "用法：\n"
                "/订阅 玩家名#数字 休闲\n"
                "/订阅 玩家名#数字 竞技\n"
                "/订阅 玩家名#数字 全部")
            return
        modes_str = "、".join({"leisure": "休闲", "sport": "竞技"}[m] for m in sub["modes"])
        await send_text_fn(ws, msg_type, target_id, 
            f"📋 当前订阅：\n"
            f"玩家：{sub['player_name']}\n"
            f"模式：{modes_str}\n\n"
            f"发送 /取消订阅 可取消订阅")
        return
    
    # 解析参数：玩家名 模式（自动转换中文#）
    parts = args.split(maxsplit=1)
    player_name = parts[0].strip().replace("＃", "#")
    mode_arg = parts[1].strip() if len(parts) > 1 else "全部"
    
    # 解析模式（支持中英文混合）
    mode_lower = mode_arg.lower()
    if mode_arg in ("休闲",) or mode_lower == "leisure":
        modes = ["leisure"]
    elif mode_arg in ("竞技",) or mode_lower == "sport":
        modes = ["sport"]
    elif mode_arg in ("全部", "两个") or mode_lower in ("all", "both"):
        modes = ["leisure", "sport"]
    else:
        await send_text_fn(ws, msg_type, target_id, 
            f"❌ 未知的模式「{mode_arg}」\n"
            f"可选：休闲、竞技、全部")
        return
    
    # 检查是否有待确认的订阅请求
    now = time.time()
    pending = _subscribe_confirm.get(uid)
    if pending and now - pending["timestamp"] < SUBSCRIBE_CONFIRM_TIMEOUT:
        # 用户正在确认，检查是否是同一个订阅请求
        if pending["player_name"] == player_name and pending["modes"] == modes:
            # 确认订阅，设置新订阅
            del _subscribe_confirm[uid]
            _set_subscription(uid, player_name, modes, platform=platform)
            modes_str = "、".join({"leisure": "休闲", "sport": "竞技"}[m] for m in modes)
            
            # 查询最新对局ID作为基准
            await send_text_fn(ws, msg_type, target_id, f"⏳ 正在初始化订阅...")
            for mode in modes:
                try:
                    match_data = await asyncio.get_running_loop().run_in_executor(
                        None, lambda pn=player_name, m=mode: querier.query_nth_match(pn, 1, game_mode=m)
                    )
                    if match_data:
                        match_id = match_data.get("match_id") or match_data.get("id")
                        if mode == "leisure":
                            _subscriptions[uid]["leisure_last_match"] = match_id
                        else:
                            _subscriptions[uid]["sport_last_match"] = match_id
                except Exception as e:
                    log_err(user_id, f"获取{mode}最新对局失败: {e}")
            _save_subscriptions_to_disk()
            
            log_ok(user_id, f"订阅 → {player_name} ({modes_str})")
            await send_text_fn(ws, msg_type, target_id, 
                f"✅ 订阅成功\n"
                f"玩家：{player_name}\n"
                f"模式：{modes_str}\n\n"
                f"该玩家在订阅模式完成新比赛后将自动推送给你")
            return
        else:
            # 不同的订阅请求，覆盖待确认状态
            pass
    
    # 验证玩家是否存在
    await send_text_fn(ws, msg_type, target_id, f"⏳ 验证玩家：{player_name}")
    try:
        profile = await asyncio.get_running_loop().run_in_executor(
            None, lambda: profiler.query_profile(player_name)
        )
        if not profile:
            await send_text_fn(ws, msg_type, target_id, f"❌ 未找到玩家：{player_name}")
            return
    except Exception as e:
        await send_text_fn(ws, msg_type, target_id, f"❌ 验证玩家失败：{e}")
        return
    
    # 检查是否已有订阅（需要确认）
    existing = _get_user_subscription(uid)
    if existing:
        old_modes_str = "、".join({"leisure": "休闲", "sport": "竞技"}[m] for m in existing["modes"])
        modes_str = "、".join({"leisure": "休闲", "sport": "竞技"}[m] for m in modes)
        # 记录待确认的订阅请求
        _subscribe_confirm[uid] = {
            "player_name": player_name,
            "modes": modes,
            "timestamp": now
        }
        await send_text_fn(ws, msg_type, target_id, 
            f"⚠️ 你已订阅「{existing['player_name']}」（{old_modes_str}）\n"
            f"新订阅「{player_name}」（{modes_str}）将覆盖旧订阅\n"
            f"请在 {SUBSCRIBE_CONFIRM_TIMEOUT} 秒内再次发送相同命令确认")
        return
    
    # 无旧订阅，直接设置
    _set_subscription(uid, player_name, modes, platform=platform)
    modes_str = "、".join({"leisure": "休闲", "sport": "竞技"}[m] for m in modes)
    
    # 查询最新对局ID作为基准
    await send_text_fn(ws, msg_type, target_id, f"⏳ 正在初始化订阅...")
    for mode in modes:
        try:
            match_data = await asyncio.get_running_loop().run_in_executor(
                None, lambda pn=player_name, m=mode: querier.query_nth_match(pn, 1, game_mode=m)
            )
            if match_data:
                match_id = match_data.get("match_id") or match_data.get("id")
                if mode == "leisure":
                    _subscriptions[uid]["leisure_last_match"] = match_id
                else:
                    _subscriptions[uid]["sport_last_match"] = match_id
        except Exception as e:
            log_err(user_id, f"获取{mode}最新对局失败: {e}")
    _save_subscriptions_to_disk()
    
    log_ok(user_id, f"订阅 → {player_name} ({modes_str})")
    await send_text_fn(ws, msg_type, target_id, 
        f"✅ 订阅成功\n"
        f"玩家：{player_name}\n"
        f"模式：{modes_str}\n\n"
        f"该玩家在订阅模式完成新比赛后将自动推送给你")


async def handle_unsubscribe_all(ws, msg_type: str, target_id, user_id,
                                  send_text_fn=None):
    """解除所有订阅"""
    if send_text_fn is None: send_text_fn = send_text
    uid = str(user_id)
    
    # 私聊限制
    if msg_type != "private":
        await send_text_fn(ws, msg_type, target_id, "❌ 解除订阅功能仅支持私聊使用")
        return
    
    if _clear_subscription(uid):
        log_ok(user_id, "解除订阅")
        await send_text_fn(ws, msg_type, target_id, "✅ 已解除订阅")
    else:
        await send_text_fn(ws, msg_type, target_id, "❌ 你还没有订阅任何玩家")


async def dispatch(ws, data: dict):
    """NapCat / OneBot 消息分发"""
    if data.get("post_type") != "message":
        return

    text = "".join(
        seg["data"].get("text", "")
        for seg in data.get("message", [])
        if seg.get("type") == "text"
    ).strip()

    if not text.startswith("/"):
        return

    msg_type  = data.get("message_type", "private")
    user_id   = data.get("user_id")
    group_id  = data.get("group_id")
    target_id = group_id if msg_type == "group" else user_id

    await _dispatch_cmd(ws, text, msg_type, target_id, user_id, platform="qq")


async def wx_dispatch(ws, data: dict):
    """千寻微信框架消息分发（event 10008 群聊 / 10009 私聊）"""
    event = data.get("event")
    if event not in (10008, 10009):
        return

    inner = data.get("data", {}).get("data", {})
    # 只处理别人发的文本消息
    if inner.get("msgSource") != 0 or inner.get("msgType") != 1:
        return

    msg = inner.get("msg", "").strip()

    from_type = inner.get("fromType")  # 1=私聊 2=群聊
    from_wxid = inner.get("fromWxid", "")       # 群wxid 或 好友wxid
    sender_wxid = inner.get("finalFromWxid") or from_wxid  # 实际发言人
    msg_type  = "group"   if from_type == 2 else "private"
    target_id = from_wxid
    user_id   = sender_wxid

    src = "微信群" if msg_type == "group" else "微信私聊"
    log_sys(f"[{src}] {sender_wxid}  {msg[:60]}")

    if not msg.startswith("/"):
        return

    # 微信平台的发送函数包装（签名与 NapCat 版一致，忽略 msg_type 参数）
    async def _wx_send_text(ws_, _msg_type, _target_id, text):
        await wx_send_text(ws_, _target_id, text)

    async def _wx_send_image(ws_, _msg_type, _target_id, image_bytes):
        await wx_send_image(ws_, _target_id, image_bytes)

    await _dispatch_cmd(ws, msg, msg_type, target_id, user_id,
                        platform="wechat",
                        send_text_fn=_wx_send_text, send_image_fn=_wx_send_image)


def _parse_nth_arg(arg: str, user_id) -> tuple[int, str]:
    """解析 '[n] 玩家名' 格式的参数，返回 (nth, player_name)"""
    nth, player_name = 1, ""
    if arg:
        sub = arg.split(maxsplit=1)
        if sub[0].isdigit():
            nth = int(sub[0])
            player_name = sub[1].strip() if len(sub) > 1 else ""
        else:
            player_name = arg
    player_name = player_name or _get_user_binding(user_id)
    return nth, player_name


def _parse_player_mode_arg(arg: str, user_id, default_mode: str = "leisure") -> tuple[str | None, str]:
    """解析 '玩家名 [竞技/休闲]' 格式，支持仅传模式时回退到已绑定玩家。"""
    mode_aliases = {
        "休闲": "leisure",
        "竞技": "sport",
        "leisure": "leisure",
        "sport": "sport",
    }

    player_name = (arg or "").strip()
    game_mode = default_mode

    if player_name:
        parts = player_name.split()
        tail = parts[-1]
        normalized = mode_aliases.get(tail, mode_aliases.get(tail.lower()))
        if normalized:
            game_mode = normalized
            player_name = " ".join(parts[:-1]).strip()

    player_name = player_name or _get_user_binding(user_id)
    return player_name or None, game_mode


def _parse_summary_arg(arg: str, user_id) -> tuple[str | None, str, str]:
    """解析 '/总结 [玩家名#数字] [今日/昨日/上周/本周] [休闲/竞技]'。"""
    mode_aliases = {
        "休闲": "leisure",
        "竞技": "sport",
        "leisure": "leisure",
        "sport": "sport",
    }
    period_aliases = {
        "今日": "today",
        "今天": "today",
        "today": "today",
        "昨日": "yesterday",
        "昨天": "yesterday",
        "yesterday": "yesterday",
        "上周": "last_week",
        "lastweek": "last_week",
        "last_week": "last_week",
        "本周": "week",
        "这周": "week",
        "week": "week",
    }

    parts = (arg or "").split()
    game_mode = "leisure"
    period = "today"
    name_parts = []

    for part in parts:
        lower = part.lower()
        if part in mode_aliases or lower in mode_aliases:
            game_mode = mode_aliases.get(part, mode_aliases.get(lower, game_mode))
            continue
        if part in period_aliases or lower in period_aliases:
            period = period_aliases.get(part, period_aliases.get(lower, period))
            continue
        name_parts.append(part)

    player_name = " ".join(name_parts).strip() or _get_user_binding(user_id)
    return player_name or None, period, game_mode


def _parse_player_season_mode_arg(arg: str, user_id, default_mode: str = "leisure") -> tuple[str | None, str | None, str]:
    """解析 '玩家名 [赛季] [模式]' 格式，赛季在前，模式在后。
    
    支持格式：
    - 玩家名#数字 19 竞技
    - 玩家名#数字 20 休闲
    - 玩家名#数字 当前 竞技
    - 玩家名#数字 竞技（无赛季，使用默认）
    - 玩家名#数字 19（无模式，使用默认）
    
    返回：(player_name, season, game_mode)
    - season: None 表示当前赛季（不传赛季参数），"" 表示明确指定当前赛季，"19"/"20"/"21"/"22" 表示具体赛季
    """
    mode_aliases = {
        "休闲": "leisure",
        "竞技": "sport",
        "leisure": "leisure",
        "sport": "sport",
    }
    
    text = (arg or "").strip()
    player_name = text
    season = None  # None 表示不传赛季参数
    game_mode = default_mode
    
    if not text:
        return _get_user_binding(user_id) or None, season, game_mode
    
    parts = text.split()
    
    # 从后往前解析，模式在最后
    while parts:
        tail = parts[-1]
        lower_tail = tail.lower()
        
        # 检查是否是模式参数
        normalized_mode = mode_aliases.get(tail, mode_aliases.get(lower_tail))
        if normalized_mode:
            game_mode = normalized_mode
            parts = parts[:-1]
            continue
        
        # 检查是否是赛季参数
        if lower_tail in {"当前", "本赛季", "current", "now"}:
            season = ""
            parts = parts[:-1]
            continue
        
        season_match = re.fullmatch(r"[sS]?(\d+)", tail)
        if season_match:
            season = str(int(season_match.group(1)))
            parts = parts[:-1]
            continue
        
        # 不是参数，是玩家名的一部分
        break
    
    player_name = " ".join(parts).strip() or _get_user_binding(user_id)
    return player_name or None, season, game_mode


def _parse_player_season_arg(arg: str, user_id) -> tuple[str | None, str | None]:
    """解析 '玩家名 [赛季]'，赛季支持 15 / S15 / 当前 / 本赛季。"""
    text = (arg or "").strip()
    player_name = text
    season = None

    if text:
        parts = text.split()
        tail = parts[-1].strip()
        lower_tail = tail.lower()
        if lower_tail in {"当前", "本赛季", "current", "now"}:
            season = ""
            player_name = " ".join(parts[:-1]).strip()
        else:
            matched = re.fullmatch(r"[sS]?(\d+)", tail)
            if matched:
                season = str(int(matched.group(1)))
                player_name = " ".join(parts[:-1]).strip()

    player_name = player_name or _get_user_binding(user_id)
    return player_name or None, season


def _feature_for_group_command(cmd: str, msg_type: str) -> str | None:
    if msg_type != "group":
        return None
    if cmd in {"/绑定"}:
        return "binding_enabled"
    if cmd in {"/排行"}:
        return "leaderboard_enabled"
    if cmd in {"/分布", "/玩家分布"}:
        return "leaderboard_enabled"
    if cmd in {"/全局排行"}:
        return "global_leaderboard_enabled"
    if cmd in {"/全局分布"}:
        return "global_leaderboard_enabled"
    if cmd in {"/资讯", "/调整"}:
        return "news_enabled"
    if cmd in {"/查询", "/休闲查询", "/竞技查询", "/休闲", "/对局", "/竞技", "/玩家", "/竞技玩家", "/历史段位", "/赛季总结", "/总结", "/英雄", "/竞技英雄", "/英雄排名", "/对比"}:
        return "query_enabled"
    return None


async def _dispatch_cmd(ws, text: str, msg_type: str, target_id, user_id,
                        platform: str = "qq",
                        send_text_fn=None, send_image_fn=None):
    """通用指令分发，被 dispatch 和 wx_dispatch 共用"""
    # 全局消息限流检查
    rate_allowed, retry_after = check_rate_limit(user_id)
    if not rate_allowed:
        log_err(user_id, "消息过于频繁")
        rate_message = f"⚠️ 操作过于频繁，请 {retry_after} 秒后再试"
        if send_text_fn is None:
            await send_text(ws, msg_type, target_id, rate_message)
        else:
            await send_text_fn(ws, msg_type, target_id, rate_message)
        return
    
    # 把 ws / msg_type / target_id 固化进闭包，防止多群并发时串台
    _ws, _mt, _tid = ws, msg_type, target_id
    if send_text_fn is None:
        async def send_text_fn(ws_, mt_, tid_, txt):
            await send_text(_ws, _mt, _tid, txt)
    if send_image_fn is None:
        async def send_image_fn(ws_, mt_, tid_, img):
            await send_image(_ws, _mt, _tid, img)

    parts = text.split(maxsplit=1)
    cmd   = parts[0]
    arg   = parts[1].strip() if len(parts) > 1 else ""
    
    # 统一将中文#转换为英文#
    arg = arg.replace("＃", "#")

    # 群聊时 group_id 为 target_id，私聊时为 None
    group_id = target_id if msg_type == "group" else None
    if group_id is not None:
        _, changed = ensure_group_settings(_group_settings, platform, group_id)
        if changed:
            _save_group_settings_to_disk()
        _ensure_group_membership(user_id, group_id)

    group_feature = _feature_for_group_command(cmd, msg_type)
    if group_feature and not is_group_feature_enabled(_group_settings, platform, group_id, group_feature):
        feature_name = {
            "query_enabled": "查询",
            "binding_enabled": "绑定",
            "leaderboard_enabled": "排行",
            "global_leaderboard_enabled": "全局排行",
            "news_enabled": "资讯",
        }.get(group_feature, "该功能")
        await send_text_fn(ws, msg_type, target_id, f"⚠️ 本群已关闭【{feature_name}】功能，请联系管理员")
        return

    if cmd == "/绑定":
        if not arg:
            await send_text_fn(ws, msg_type, target_id, "用法：/绑定 玩家名#数字")
            return
        
        player_name = arg
        
        uid = str(user_id)
        
        # 检查用户是否已绑定
        current_binding = _get_user_binding(user_id)
        if current_binding == player_name:
            joined_group = _ensure_group_membership(user_id, group_id)
            if joined_group and group_id is not None:
                await send_text_fn(ws, msg_type, target_id, f"✅ 你已绑定「{current_binding}」，已加入本群排行榜")
            else:
                await send_text_fn(ws, msg_type, target_id, f"ℹ️ 你已绑定「{current_binding}」，无需重复绑定")
            return
        if current_binding:
            await send_text_fn(ws, msg_type, target_id, 
                f"❌ 你已绑定「{current_binding}」，请先 /解绑 后再重新绑定")
            return
        
        # 检查该玩家ID是否已被其他用户绑定
        for bound_uid, bound_name in _user_bindings.items():
            if bound_name == player_name and bound_uid != uid:
                await send_text_fn(ws, msg_type, target_id, 
                    f"❌ 该玩家ID「{player_name}」已被其他用户绑定")
                return
        
        # 查询玩家是否存在
        await send_text_fn(ws, msg_type, target_id, f"⏳ 验证玩家：{player_name}")
        try:
            player_info = await asyncio.get_running_loop().run_in_executor(
                None, lambda: profiler.search_player(player_name)
            )
            if not player_info:
                await send_text_fn(ws, msg_type, target_id, f"❌ 未找到玩家：{player_name}\n请确认名称格式为「昵称#数字」")
                return
        except AuthExpiredError:
            await send_text_fn(ws, msg_type, target_id, _auth_expired_reply())
            return
        except Exception as e:
            await send_text_fn(ws, msg_type, target_id, f"❌ 查询失败：{e}")
            return
        
        # 获取玩家段位信息
        rank_info = ""
        try:
            rank = await asyncio.get_running_loop().run_in_executor(
                None, lambda: leaderboard.get_player_rank(player_info["customerToken"])
            )
            if rank and rank.get("rankName") and rank["rankName"] != "None":
                rank_str = leaderboard._fmt_rank(rank)
                rank_info = f"\n段位：{rank_str}"
        except Exception:
            pass
        
        # 绑定时记录群成员关系
        _set_user_binding(user_id, player_name, group_id)
        log_ok(user_id, f"绑定 → {player_name}")
        await send_text_fn(ws, msg_type, target_id, f"✅ 绑定成功：{player_name}{rank_info}")

    elif cmd in {"/查询", "/休闲查询"}:
        if cmd == "/休闲查询":
            player_name = arg or _get_user_binding(user_id)
            game_mode = "leisure"
        else:
            player_name, game_mode = _parse_player_mode_arg(arg, user_id, default_mode="leisure")
        if not player_name:
            await send_text_fn(ws, msg_type, target_id, "用法：/查询 玩家名#数字 [休闲/竞技]\n已绑定后也可直接 /查询 休闲 或 /查询 竞技")
            return
        await _send_streak_reaction(
            ws,
            msg_type,
            target_id,
            user_id,
            player_name,
            game_mode=game_mode,
            send_text_fn=send_text_fn,
        )
        await handle_analyze(ws, msg_type, target_id, user_id, player_name,
                             game_mode=game_mode,
                             send_text_fn=send_text_fn, send_image_fn=send_image_fn)

    elif cmd == "/竞技查询":
        player_name = arg or _get_user_binding(user_id)
        if not player_name:
            await send_text_fn(ws, msg_type, target_id, "请提供玩家名，或先用 /绑定 昵称#数字 绑定账号")
            return
        await _send_streak_reaction(
            ws,
            msg_type,
            target_id,
            user_id,
            player_name,
            game_mode="sport",
            send_text_fn=send_text_fn,
        )
        await handle_analyze(ws, msg_type, target_id, user_id, player_name,
                             game_mode="sport",
                             send_text_fn=send_text_fn, send_image_fn=send_image_fn)

    elif cmd == "/休闲":
        player_name = arg or _get_user_binding(user_id)
        if not player_name:
            await send_text_fn(ws, msg_type, target_id, "请提供玩家名，或先用 /绑定 昵称#数字 绑定账号")
            return
        await handle_query(ws, msg_type, target_id, user_id, player_name, nth=1,
                           send_text_fn=send_text_fn, send_image_fn=send_image_fn)

    elif cmd == "/对局":
        nth, player_name = _parse_nth_arg(arg, user_id)
        if not player_name:
            await send_text_fn(ws, msg_type, target_id, "用法：/对局 [n] 玩家名#数字\n例如：/对局 3 就酱#51561")
            return
        await handle_query(ws, msg_type, target_id, user_id, player_name, nth=nth,
                           send_text_fn=send_text_fn, send_image_fn=send_image_fn)

    elif cmd == "/竞技":
        nth, player_name = _parse_nth_arg(arg, user_id)
        if not player_name:
            await send_text_fn(ws, msg_type, target_id, "用法：/竞技 [n] 玩家名#数字")
            return
        await handle_query(ws, msg_type, target_id, user_id, player_name, nth=nth,
                           game_mode="sport",
                           send_text_fn=send_text_fn, send_image_fn=send_image_fn)

    elif cmd == "/玩家":
        player_name, season, game_mode = _parse_player_season_mode_arg(arg, user_id)
        if not player_name:
            await send_text_fn(ws, msg_type, target_id, "用法：/玩家 玩家名#数字 [赛季] [竞技/休闲]\n赛季可选：19/20/21/22/当前")
            return
        await handle_profile(ws, msg_type, target_id, user_id, player_name,
                             game_mode=game_mode, season=season,
                             send_text_fn=send_text_fn, send_image_fn=send_image_fn)

    elif cmd == "/竞技玩家":
        player_name = arg or _get_user_binding(user_id)
        if not player_name:
            await send_text_fn(ws, msg_type, target_id, "用法：/竞技玩家 玩家名#数字")
            return
        await handle_profile(ws, msg_type, target_id, user_id, player_name,
                             game_mode="sport",
                             send_text_fn=send_text_fn, send_image_fn=send_image_fn)

    elif cmd == "/历史段位":
        player_name, season = _parse_player_season_arg(arg, user_id)
        if not player_name:
            await send_text_fn(ws, msg_type, target_id, "用法：/历史段位 玩家名#数字 [赛季]\n已绑定后也可直接 /历史段位 15")
            return
        await handle_rank_history(ws, msg_type, target_id, user_id, player_name,
                                  season=season,
                                  send_text_fn=send_text_fn, send_image_fn=send_image_fn)

    elif cmd == "/赛季总结":
        player_name = arg or _get_user_binding(user_id)
        if not player_name:
            await send_text_fn(ws, msg_type, target_id, "用法：/赛季总结 玩家名#数字\n已绑定后也可直接 /赛季总结")
            return
        await handle_season_summary(ws, msg_type, target_id, user_id, player_name,
                                    send_text_fn=send_text_fn, send_image_fn=send_image_fn)

    elif cmd == "/总结":
        player_name, period, game_mode = _parse_summary_arg(arg, user_id)
        if not player_name:
            await send_text_fn(ws, msg_type, target_id, "用法：/总结 玩家名#数字 [今日/昨日/上周/本周] [休闲/竞技]\n已绑定后也可直接 /总结 今日 竞技")
            return
        await handle_weekly_summary(ws, msg_type, target_id, user_id, player_name,
                                    period=period, game_mode=game_mode,
                                    send_text_fn=send_text_fn, send_image_fn=send_image_fn)

    elif cmd == "/英雄":
        player_name, game_mode = _parse_player_mode_arg(arg, user_id)
        if not player_name:
            await send_text_fn(ws, msg_type, target_id, "用法：/英雄 玩家名#数字 [竞技/休闲]")
            return
        await handle_herostats(ws, msg_type, target_id, user_id, player_name,
                               game_mode=game_mode,
                               send_text_fn=send_text_fn, send_image_fn=send_image_fn)

    elif cmd == "/竞技英雄":
        player_name = arg or _get_user_binding(user_id)
        if not player_name:
            await send_text_fn(ws, msg_type, target_id, "用法：/竞技英雄 玩家名#数字")
            return
        await handle_herostats(ws, msg_type, target_id, user_id, player_name,
                               game_mode="sport",
                               send_text_fn=send_text_fn, send_image_fn=send_image_fn)

    elif cmd == "/英雄排名":
        player_name = arg or _get_user_binding(user_id)
        if not player_name:
            await send_text_fn(ws, msg_type, target_id, "用法：/英雄排名 玩家名#数字")
            return
        await handle_billboard(ws, msg_type, target_id, user_id, player_name,
                               send_text_fn=send_text_fn, send_image_fn=send_image_fn)

    elif cmd == "/对比":
        parts = arg.split(maxsplit=1) if arg else []
        if len(parts) < 2:
            await send_text_fn(ws, msg_type, target_id, "用法：/对比 玩家A#数字 玩家B#数字")
            return
        await handle_compare(ws, msg_type, target_id, user_id,
                             parts[0].strip(), parts[1].strip(),
                             send_text_fn=send_text_fn, send_image_fn=send_image_fn)

    elif cmd == "/排行":
        await handle_leaderboard(ws, msg_type, target_id, user_id,
                                 send_text_fn=send_text_fn, send_image_fn=send_image_fn)

    elif cmd in {"/分布", "/玩家分布"}:
        await handle_distribution(ws, msg_type, target_id, user_id,
                                  send_text_fn=send_text_fn, send_image_fn=send_image_fn)

    elif cmd == "/解绑":
        uid = str(user_id)
        current_binding = _get_user_binding(user_id)
        if not current_binding:
            await send_text_fn(ws, msg_type, target_id, "❌ 你还没有绑定账号")
            return

        now = time.time()
        # 检查是否已有待确认的解绑请求
        if uid in _unbind_confirm:
            if now - _unbind_confirm[uid] < UNBIND_CONFIRM_TIMEOUT:
                # 二次确认，执行解绑
                del _unbind_confirm[uid]
                # 从全局绑定中删除
                if uid in _user_bindings:
                    del _user_bindings[uid]
                # 从所有群的成员列表中删除
                for gid in list(_group_members.keys()):
                    if uid in _group_members[gid]:
                        _group_members[gid].remove(uid)
                        # 如果群成员列表为空，删除该群
                        if not _group_members[gid]:
                            del _group_members[gid]
                _save_bindings_to_disk()
                log_ok(user_id, f"解绑 ← {current_binding}")
                await send_text_fn(ws, msg_type, target_id, f"✅ 已解绑：{current_binding}")
            else:
                # 确认已过期，重新计时
                _unbind_confirm[uid] = now
                await send_text_fn(ws, msg_type, target_id, 
                    f"⚠️ 之前的确认已过期\n"
                    f"确定要解绑「{current_binding}」吗？\n"
                    f"请在 {UNBIND_CONFIRM_TIMEOUT} 秒内再次发送 /解绑 确认")
        else:
            # 第一次请求，记录时间并提示确认
            _unbind_confirm[uid] = now
            await send_text_fn(ws, msg_type, target_id, 
                f"⚠️ 确定要解绑「{current_binding}」吗？\n"
                f"请在 {UNBIND_CONFIRM_TIMEOUT} 秒内再次发送 /解绑 确认")

    elif cmd == "/全局排行":
        await handle_global_leaderboard(ws, msg_type, target_id, user_id,
                                        send_text_fn=send_text_fn, send_image_fn=send_image_fn)

    elif cmd == "/全局分布":
        await handle_global_distribution(ws, msg_type, target_id, user_id,
                                         send_text_fn=send_text_fn, send_image_fn=send_image_fn)

    elif cmd == "/资讯":
        await handle_news(ws, msg_type, target_id, user_id,
                          send_text_fn=send_text_fn, send_image_fn=send_image_fn)

    elif cmd == "/调整":
        await handle_patch_notes(ws, msg_type, target_id, user_id,
                                 send_text_fn=send_text_fn, send_image_fn=send_image_fn)

    elif cmd == "/订阅":
        await handle_subscribe(ws, msg_type, target_id, user_id, arg,
                               send_text_fn=send_text_fn, platform=platform)

    elif cmd == "/取消订阅":
        await handle_unsubscribe_all(ws, msg_type, target_id, user_id,
                                     send_text_fn=send_text_fn)

    elif cmd == "/帮助":
        await _send_cached_menu(
            ws,
            msg_type,
            target_id,
            user_id,
            _help_image,
            "help.html",
            HELP_TEXT,
            send_text_fn,
            send_image_fn,
        )

# ── 订阅推送后台任务 ────────────────────────────────────────────────────────

# WebSocket 连接缓存（用于后台推送）
_qq_ws = None
_wx_ws = None
_qq_api_pending: dict[str, asyncio.Future] = {}


def _handle_napcat_api_response(payload: dict) -> bool:
    echo = payload.get("echo")
    if not echo:
        return False
    future = _qq_api_pending.get(str(echo))
    if future is None:
        return False
    if not future.done():
        future.set_result(payload)
    return True


async def _call_qq_api(action: str, params: dict | None = None, timeout: float = 15.0) -> dict:
    if not (_qq_ws and ENABLE_QQ):
        raise RuntimeError("QQ 机器人未连接")

    loop = asyncio.get_running_loop()
    echo = f"api_{uuid.uuid4().hex}"
    future = loop.create_future()
    _qq_api_pending[echo] = future
    payload = {
        "action": action,
        "params": params or {},
        "echo": echo,
    }
    try:
        await _qq_ws.send(json.dumps(payload, ensure_ascii=False))
        response = await asyncio.wait_for(future, timeout=timeout)
        if response.get("status") not in {None, "ok"}:
            raise RuntimeError(f"{action} 调用失败: {response}")
        return response
    finally:
        _qq_api_pending.pop(echo, None)


async def _fetch_qq_friend_ids() -> list[str]:
    response = await _call_qq_api("get_friend_list", {"no_cache": True})
    data = response.get("data") or []
    ids: list[str] = []
    for item in data:
        user_id = item.get("user_id")
        if user_id is None:
            continue
        ids.append(str(user_id))
    return ids


async def _fetch_qq_group_ids() -> list[str]:
    response = await _call_qq_api("get_group_list", {"no_cache": True})
    data = response.get("data") or []
    ids: list[str] = []
    for item in data:
        group_id = item.get("group_id")
        if group_id is None:
            continue
        ids.append(str(group_id))
    return ids

async def subscription_checker():
    """后台任务：定期检查订阅玩家的最新比赛"""
    global _qq_ws, _wx_ws
    log_sys(f"订阅推送任务启动，检查间隔：{SUBSCRIPTION_CHECK_INTERVAL}秒")
    
    while True:
        await asyncio.sleep(SUBSCRIPTION_CHECK_INTERVAL)
        
        if not _subscriptions:
            continue
        
        try:
            log_step("sub", f"检查 {len(_subscriptions)} 个用户的订阅...")
            
            for uid, sub_data in list(_subscriptions.items()):
                player_name = sub_data.get("player_name")
                modes = sub_data.get("modes", [])
                platform = _get_subscription_platform(uid, sub_data)
                
                if not player_name or not modes:
                    continue
                
                # 检查每个订阅的模式
                for mode in modes:
                    try:
                        game_mode = mode  # "leisure" or "sport"
                        last_match_key = "leisure_last_match" if mode == "leisure" else "sport_last_match"
                        last_match_id = sub_data.get(last_match_key)
                        
                        # 获取最新一场比赛
                        match_data = await asyncio.get_running_loop().run_in_executor(
                            None, lambda pn=player_name, gm=game_mode: querier.query_nth_match(pn, 1, game_mode=gm)
                        )
                        
                        if not match_data:
                            continue
                        
                        current_match_id = match_data.get("match_id") or match_data.get("id")
                        
                        # 首次检查，只记录不推送
                        if last_match_id is None:
                            _subscriptions[uid][last_match_key] = current_match_id
                            _save_subscriptions_to_disk()
                            continue
                        
                        # 有新比赛
                        if current_match_id and current_match_id != last_match_id:
                            # 生成报告并推送
                            report_path = os.path.join(REPORTS_DIR, f".report_{uuid.uuid4().hex[:8]}.html")
                            try:
                                querier.generate_html_report(match_data, output_path=report_path)
                                image = await html_to_image(report_path)
                                
                                mode_label = "竞技" if mode == "sport" else "休闲"
                                push_msg = f"🔔 {player_name} 刚完成一场{mode_label}比赛！"
                                
                                if platform == "wechat":
                                    if not (_wx_ws and ENABLE_WECHAT):
                                        continue
                                    await wx_send_text(_wx_ws, uid, push_msg)
                                    await wx_send_image(_wx_ws, uid, image)
                                else:
                                    if not uid.isdigit():
                                        log_err("sub", f"QQ subscription target is not numeric: {uid}")
                                        continue
                                    if not (_qq_ws and ENABLE_QQ):
                                        continue
                                    await send_text(_qq_ws, "private", int(uid), push_msg)
                                    await send_image(_qq_ws, "private", int(uid), image)
                                
                                _subscriptions[uid][last_match_key] = current_match_id
                                _save_subscriptions_to_disk()
                                log_ok("sub", f"推送 {player_name} {mode_label}新比赛给 {uid}")
                            finally:
                                if os.path.exists(report_path):
                                    os.unlink(report_path)
                        
                        await asyncio.sleep(1)  # 避免请求过快
                        
                    except Exception as e:
                        log_err("sub", f"检查 {player_name} {mode} 失败: {e}")
                        if isinstance(e, AuthExpiredError):
                            _schedule_admin_alert(
                                "sub.auth_expired",
                                f"订阅检查时检测到认证过期，玩家：{player_name}，模式：{mode}",
                                cooldown_seconds=1800,
                            )
                        else:
                            _schedule_admin_alert(
                                f"sub.check_failed:{player_name}:{mode}",
                                f"订阅检查失败\n玩家：{player_name}\n模式：{mode}\n错误：{e}",
                                cooldown_seconds=1800,
                            )
                        
        except Exception as e:
            log_err("sub", f"订阅检查循环异常: {e}")
            _schedule_admin_alert(
                "sub.loop_failed",
                f"订阅检查循环异常：{e}",
                cooldown_seconds=1800,
            )


# ── 主循环 ────────────────────────────────────────────────────────────────────

async def run_napcat():
    """NapCat (QQ) WebSocket 连接循环"""
    global _qq_ws
    url = f"{WS_URL}?access_token={WS_TOKEN}"
    log_sys(f"[QQ] 连接 {WS_URL} ...")
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                _qq_ws = ws
                log_sys("[QQ] 已连接，监听中")
                async for raw in ws:
                    try:
                        payload = json.loads(raw)
                        if _handle_napcat_api_response(payload):
                            continue
                        asyncio.create_task(_run_limited_message_task("qq_dispatch", dispatch(ws, payload)))
                    except Exception as e:
                        log_err("sys", str(e))
        except Exception as e:
            _qq_ws = None
            log_sys(f"[QQ] {_RD}断开：{e}{_R}，5s 后重连...")
            _schedule_admin_alert("conn.qq.down", f"QQ 连接断开：{e}", cooldown_seconds=900)
            await asyncio.sleep(5)


async def run_wechat():
    """千寻微信框架 WebSocket 连接循环"""
    global _wx_ws
    log_sys(f"[微信] 连接 {WX_WS_URL} ...")
    while True:
        try:
            async with websockets.connect(
                WX_WS_URL,
                ping_interval=WX_WS_PING_INTERVAL,
                ping_timeout=WX_WS_PING_TIMEOUT,
            ) as ws:
                _wx_ws = ws
                log_sys("[微信] 已连接，监听中")
                async for raw in ws:
                    try:
                        payload = json.loads(raw)
                        asyncio.create_task(_run_limited_message_task("wx_dispatch", wx_dispatch(ws, payload)))
                    except Exception as e:
                        log_err("sys", str(e))
        except Exception as e:
            _wx_ws = None
            log_sys(f"[微信] {_RD}断开：{e}{_R}，5s 后重连...")
            _schedule_admin_alert("conn.wx.down", f"微信连接断开：{e}", cooldown_seconds=900)
            await asyncio.sleep(5)


async def run():
    _cleanup_temp_reports()
    asyncio.create_task(_prerender_help())
    asyncio.create_task(subscription_checker())
    # 启动订阅推送后台任务
    if _subscriptions:
        pass
    tasks = []
    if ENABLE_QQ:
        tasks.append(run_napcat())
    if ENABLE_WECHAT:
        tasks.append(run_wechat())
    if not tasks:
        log_sys("未启用任何机器人，请检查 config.py 中的 ENABLE_QQ / ENABLE_WECHAT")
        return
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(run())
