"""
bot_state.py — 全局状态管理模块
管理用户绑定、订阅、群设置、限流等所有持久化状态。
"""
import json
import os
import threading
import time
import uuid
# ensure_group_settings / make_group_key / is_admin / is_group_feature_enabled
# (inlined from deleted admin module)

# ── 路径常量 ──────────────────────────────────────────────────────────────────
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR    = os.path.join(BASE_DIR, "config")
LOGS_DIR      = os.path.join(BASE_DIR, "logs")
REPORTS_DIR   = os.path.join(BASE_DIR, "reports")
os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)
BINDINGS_FILE      = os.path.join(CONFIG_DIR, "bindings.json")
SUBSCRIPTIONS_FILE = os.path.join(CONFIG_DIR, "subscriptions.json")
GROUP_SETTINGS_FILE = os.path.join(CONFIG_DIR, "group_settings.json")
_json_io_lock = threading.RLock()

# ── JSON 文件读写 ─────────────────────────────────────────────────────────────

def _json_backup_path(path: str) -> str:
    return f"{path}.bak"


def _load_json_file(path: str, default):
    with _json_io_lock:
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            backup_path = _json_backup_path(path)
            try:
                with open(backup_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError, FileNotFoundError):
                return default


def _atomic_save_json(path: str, data) -> None:
    tmp_path = f"{path}.{uuid.uuid4().hex}.tmp"
    backup_path = _json_backup_path(path)
    with _json_io_lock:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            if os.path.exists(path):
                backup_tmp_path = f"{backup_path}.{uuid.uuid4().hex}.tmp"
                try:
                    with open(path, "rb") as src, open(backup_tmp_path, "wb") as dst:
                        dst.write(src.read())
                        dst.flush()
                        os.fsync(dst.fileno())
                    os.replace(backup_tmp_path, backup_path)
                finally:
                    if os.path.exists(backup_tmp_path):
                        os.unlink(backup_tmp_path)
            os.replace(tmp_path, path)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

# ── 临时报告清理 ──────────────────────────────────────────────────────────────

def _cleanup_temp_reports(log_err=None) -> None:
    removed = 0
    for name in os.listdir(REPORTS_DIR):
        if not (name.startswith(".report_") and name.endswith(".html")):
            continue
        path = os.path.join(REPORTS_DIR, name)
        try:
            os.unlink(path)
            removed += 1
        except FileNotFoundError:
            continue
        except Exception as e:
            if log_err:
                log_err("sys", f"cleanup temp report failed: {name} {e}")
    if removed and log_err is None:
        print(f"[sys] cleaned {removed} stale temp reports")

# ── 用户绑定 ──────────────────────────────────────────────────────────────────

_user_bindings: dict = {}   # {user_id: player_name}
_group_members: dict = {}   # {group_id: [user_id, ...]}


def _migrate_old_bindings(data: dict) -> tuple[dict, dict]:
    if not data:
        return {}, {}
    if "_user_bindings" in data:
        return data.get("_user_bindings", {}), data.get("_group_members", {})
    return data, {}


def _load_bindings_from_disk() -> tuple[dict, dict]:
    return _migrate_old_bindings(_load_json_file(BINDINGS_FILE, {}))


def _save_bindings_to_disk():
    data = {
        "_user_bindings": _user_bindings,
        "_group_members": _group_members
    }
    _atomic_save_json(BINDINGS_FILE, data)


def _get_user_binding(user_id) -> str | None:
    return _user_bindings.get(str(user_id))


def _set_user_binding(user_id, player_name, group_id=None):
    uid = str(user_id)
    _user_bindings[uid] = player_name
    if group_id:
        gid = str(group_id)
        if gid not in _group_members:
            _group_members[gid] = []
        if uid not in _group_members[gid]:
            _group_members[gid].append(uid)
    _save_bindings_to_disk()


def _ensure_group_membership(user_id, group_id) -> bool:
    if group_id is None:
        return False
    uid = str(user_id)
    if uid not in _user_bindings:
        return False
    gid = str(group_id)
    members = _group_members.setdefault(gid, [])
    if uid in members:
        return False
    members.append(uid)
    _save_bindings_to_disk()
    return True


def _get_group_bindings(group_id) -> dict:
    gid = str(group_id)
    member_ids = _group_members.get(gid, [])
    return {uid: _user_bindings[uid] for uid in dict.fromkeys(member_ids) if uid in _user_bindings}


# 启动时加载
_user_bindings, _group_members = _load_bindings_from_disk()

# ── 解绑确认 ──────────────────────────────────────────────────────────────────

_unbind_confirm: dict = {}
UNBIND_CONFIRM_TIMEOUT = 60

# ── 订阅确认 ──────────────────────────────────────────────────────────────────

_subscribe_confirm: dict = {}
SUBSCRIBE_CONFIRM_TIMEOUT = 60

# ── 订阅数据 ──────────────────────────────────────────────────────────────────

_subscriptions: dict = {}


def _load_subscriptions_from_disk() -> dict:
    data = _load_json_file(SUBSCRIPTIONS_FILE, {})
    return data.get("_subscriptions", {}) if isinstance(data, dict) else {}


def _save_subscriptions_to_disk():
    data = {"_subscriptions": _subscriptions}
    _atomic_save_json(SUBSCRIPTIONS_FILE, data)


def _infer_subscription_platform(user_id: str) -> str:
    return "qq" if str(user_id).isdigit() else "wechat"


def _get_subscription_platform(user_id: str, sub_data: dict) -> str:
    platform = sub_data.get("platform")
    if platform in {"qq", "wechat"}:
        return platform
    return _infer_subscription_platform(user_id)


def _set_subscription(user_id: str, player_name: str, modes: list, platform: str | None = None):
    uid = str(user_id)
    _subscriptions[uid] = {
        "player_name": player_name,
        "modes": modes,
        "platform": platform or _infer_subscription_platform(uid),
        "leisure_last_match": None,
        "sport_last_match": None
    }
    _save_subscriptions_to_disk()


def _clear_subscription(user_id: str) -> bool:
    uid = str(user_id)
    if uid not in _subscriptions:
        return False
    del _subscriptions[uid]
    _save_subscriptions_to_disk()
    return True


def _get_user_subscription(user_id: str) -> dict | None:
    uid = str(user_id)
    return _subscriptions.get(uid)


# 启动时加载
_subscriptions = _load_subscriptions_from_disk()

# ── 群功能开关 ────────────────────────────────────────────────────────────────

def _load_group_settings_from_disk() -> dict:
    data = _load_json_file(GROUP_SETTINGS_FILE, {})
    return data.get("_groups", {}) if isinstance(data, dict) else {}


def _save_group_settings_to_disk():
    data = {"_groups": _group_settings}
    _atomic_save_json(GROUP_SETTINGS_FILE, data)
DEFAULT_GROUP_SETTINGS = {
    "query_enabled": True,
    "binding_enabled": True,
    "leaderboard_enabled": True,
    "global_leaderboard_enabled": True,
    "news_enabled": True,
}


def make_group_key(platform: str, group_id) -> str:
    return f"{platform}:{group_id}"


def ensure_group_settings(store: dict, platform: str, group_id) -> tuple[dict, bool]:
    key = make_group_key(platform, group_id)
    current = store.get(key)
    if current is None:
        store[key] = DEFAULT_GROUP_SETTINGS.copy()
        return store[key], True
    merged = DEFAULT_GROUP_SETTINGS.copy()
    merged.update(current)
    changed = merged != current
    store[key] = merged
    return merged, changed


def is_admin(user_id, admins: list[str] | tuple[str, ...]) -> bool:
    return str(user_id) in {str(item) for item in admins}


def is_group_feature_enabled(store: dict, platform: str, group_id, feature_key: str) -> bool:
    key = make_group_key(platform, group_id)
    settings = store.get(key)
    if settings is None:
        return True
    return bool(settings.get(feature_key, True))



_group_settings: dict = _load_group_settings_from_disk()
for _gid in list(_group_members.keys()):
    _platform = "qq" if str(_gid).isdigit() else "wechat"
    _, _changed = ensure_group_settings(_group_settings, _platform, _gid)
    if _changed:
        _save_group_settings_to_disk()

# ── 限流 ──────────────────────────────────────────────────────────────────────

_rate_limits: dict = {}
MAX_QUERIES_PER_MINUTE = 5
_RATE_CLEANUP_INTERVAL = 300
_last_rate_cleanup = time.time()


def check_rate_limit(user_id: int) -> tuple[bool, int]:
    global _last_rate_cleanup
    now = time.time()
    uid = str(user_id)

    if now - _last_rate_cleanup > _RATE_CLEANUP_INTERVAL:
        expired = [k for k, ts_list in _rate_limits.items()
                   if not any(now - ts < 60 for ts in ts_list)]
        for k in expired:
            del _rate_limits[k]
        _last_rate_cleanup = now

    if uid not in _rate_limits:
        _rate_limits[uid] = []

    _rate_limits[uid] = [ts for ts in _rate_limits[uid] if now - ts < 60]

    if len(_rate_limits[uid]) >= MAX_QUERIES_PER_MINUTE:
        oldest = min(_rate_limits[uid])
        retry_after = max(1, int(60 - (now - oldest)) + 1)
        return False, retry_after

    _rate_limits[uid].append(now)
    return True, 0


def _player_name_in_use(player_name: str, exclude_user_id=None) -> str | None:
    """检查玩家名是否已被其他用户绑定，返回已绑定的 user_id"""
    target = player_name.strip()
    for uid, name in _user_bindings.items():
        if str(uid) == str(exclude_user_id or ""):
            continue
        if name.strip() == target:
            return uid
    return None
