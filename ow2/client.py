import base64
import json
import logging
import mimetypes
import os
import time
import threading
import requests
from functools import lru_cache
from requests.adapters import HTTPAdapter
from urllib.parse import urlparse
from urllib3.util.retry import Retry


HERO_ATTR_URL = "https://s.166.net/config/ds_ow/ow_hero_attr.json"
HERO_CONFIG_URL = "https://s.166.net/config/ds_ow/ow_hero_config.json"
PERK_LIST_URL = "https://s.166.net/config/ds_ow/ow_perk_list.json"
ASSET_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assets"))
HERO_ASSET_DIR = os.path.join(ASSET_ROOT, "heroes")
PERK_ASSET_DIR = os.path.join(ASSET_ROOT, "perks")
CACHE_MANIFEST_NAME = ".source_manifest.json"
logger = logging.getLogger(__name__)

# ── 全局共享 Session（连接池复用）────────────────────────────────────────────
_session_lock = threading.Lock()
_shared_session: requests.Session | None = None

def _get_session() -> requests.Session:
    global _shared_session
    with _session_lock:
        if _shared_session is None:
            s = requests.Session()
            retry = Retry(
                total=3,
                backoff_factor=0.5,          # 0.5s, 1s, 2s
                status_forcelist={429, 500, 502, 503, 504},
                allowed_methods={"GET", "POST"},
                raise_on_status=False,
            )
            adapter = HTTPAdapter(
                max_retries=retry,
                pool_connections=8,          # 最多 8 个连接到同一 host
                pool_maxsize=8,
            )
            s.mount("https://", adapter)
            s.mount("http://",  adapter)
            _shared_session = s
        return _shared_session

# ── 全局并发信号量：限制同时发出的 API 请求数 ─────────────────────────────────
# 8 个并发足够快，又不会触发服务端限速
_api_semaphore = threading.Semaphore(8)

def _api_get(url, **kwargs) -> requests.Response:
    """带信号量 + 限速保护的 GET"""
    with _api_semaphore:
        return _get_session().get(url, **kwargs)

def _api_post(url, **kwargs) -> requests.Response:
    """带信号量 + 限速保护的 POST"""
    with _api_semaphore:
        return _get_session().post(url, **kwargs)


def clear_image_caches() -> None:
    _file_to_data_uri.cache_clear()
    _remote_to_data_uri.cache_clear()


@lru_cache(maxsize=512)
def _file_to_data_uri(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    if not raw:
        return ""
    mime_type = mimetypes.guess_type(path)[0] or "image/png"
    b64 = __import__("base64").b64encode(raw).decode()
    return f"data:{mime_type};base64,{b64}"


@lru_cache(maxsize=512)
def _remote_to_data_uri(url: str, timeout: int) -> str:
    resp = _api_get(url, timeout=timeout)
    if resp.status_code != 200:
        return ""
    content_type = resp.headers.get("Content-Type", "image/png").split(";")[0].strip()
    b64 = __import__("base64").b64encode(resp.content).decode()
    return f"data:{content_type};base64,{b64}"


def url_to_data_uri(url: str, timeout: int = 3) -> str:
    """把图片 URL 下载并转成 base64 data URI，失败则返回空字符串"""
    if not url:
        return ""
    if os.path.isfile(url):
        try:
            return _file_to_data_uri(url)
        except Exception:
            logger.debug("failed to inline local image: %s", url, exc_info=True)
            return ""
    try:
        return _remote_to_data_uri(url, timeout)
    except Exception:
        logger.debug("failed to inline remote image: %s", url, exc_info=True)
    return ""


def _image_extension_from_url(url: str) -> str:
    ext = os.path.splitext(urlparse(url).path)[1].lower()
    if ext in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}:
        return ext
    return ".png"


def _download_image_to_path(url: str, target_path: str, timeout: int = 10) -> bool:
    try:
        resp = _api_get(url, timeout=timeout)
        if resp.status_code != 200 or not resp.content:
            return False
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        tmp_path = f"{target_path}.tmp"
        with open(tmp_path, "wb") as f:
            f.write(resp.content)
        os.replace(tmp_path, target_path)
        return True
    except Exception:
        logger.debug("failed to cache remote image: %s -> %s", url, target_path, exc_info=True)
        return False


def _manifest_path(asset_dir: str) -> str:
    return os.path.join(asset_dir, CACHE_MANIFEST_NAME)


def _load_cache_manifest(asset_dir: str) -> dict[str, str]:
    try:
        with open(_manifest_path(asset_dir), "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        logger.debug("failed to read asset manifest: %s", asset_dir, exc_info=True)
        return {}


def _save_cache_manifest(asset_dir: str, manifest: dict[str, str]) -> None:
    os.makedirs(asset_dir, exist_ok=True)
    with open(_manifest_path(asset_dir), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)


def _cache_config_icons(config: dict, asset_dir: str) -> dict:
    if not config:
        return config
    os.makedirs(asset_dir, exist_ok=True)
    manifest = _load_cache_manifest(asset_dir)
    manifest_changed = False
    cache_changed = False
    for entry_id, entry_info in config.items():
        icon_url = str(entry_info.get("icon", "") or "").strip()
        if not icon_url or os.path.isfile(icon_url):
            continue
        if urlparse(icon_url).scheme not in {"http", "https"}:
            continue
        local_path = os.path.join(asset_dir, f"{entry_id}{_image_extension_from_url(icon_url)}")
        cached_url = manifest.get(str(entry_id))
        needs_refresh = cached_url != icon_url or (not os.path.exists(local_path)) or os.path.getsize(local_path) == 0
        if needs_refresh:
            if not _download_image_to_path(icon_url, local_path):
                continue
            manifest[str(entry_id)] = icon_url
            manifest_changed = True
            cache_changed = True
        entry_info["icon"] = local_path
    if manifest_changed:
        _save_cache_manifest(asset_dir, manifest)
    if cache_changed:
        clear_image_caches()
    return config


def cache_perk_icons(config: dict) -> dict:
    """灏嗗▉鑳藉浘鏍囩紦瀛樺埌鏈湴锛岄伩鍏?HTML 娓叉煋闃舵渚濊禆澶栫綉璧勬簮銆?"""
    return _cache_config_icons(config, PERK_ASSET_DIR)


def cache_hero_icons(config: dict) -> dict:
    return _cache_config_icons(config, HERO_ASSET_DIR)


def fetch_hero_guid_names() -> dict:
    """从 166.net 拉取最新英雄名映射，返回 {heroGuid: heroName}，失败返回空 dict"""
    try:
        resp = _api_get(HERO_ATTR_URL, timeout=5)
        if resp.status_code == 200:
            mapping = {}
            for item in resp.json():
                guid = item.get("heroGuid")
                name = item.get("heroName")
                if guid and name and guid not in mapping:
                    mapping[guid] = name
            return mapping
    except Exception:
        logger.debug("failed to fetch hero guid names", exc_info=True)
    return {}


def fetch_hero_config() -> dict:
    """从 166.net 拉取英雄配置，返回 {heroGuid: {"roleType": "tank/dps/healer", "icon": "...", "name": "..."}}，失败返回空 dict"""
    try:
        resp = _api_get(HERO_CONFIG_URL, timeout=5)
        if resp.status_code == 200:
            config = {}
            for item in resp.json():
                guid = str(item.get("heroGuid", ""))
                if guid:
                    config[guid] = {
                        "roleType": item.get("roleType", ""),
                        "icon": item.get("icon", ""),
                        "name": item.get("name", "")
                    }
            return cache_hero_icons(config)
    except Exception:
        logger.debug("failed to fetch hero config", exc_info=True)
    return {}


def fetch_perk_config() -> dict:
    """拉取威能配置，返回 {perkGuid: {heroGuid, icon, name, description}}。"""
    try:
        resp = _api_get(PERK_LIST_URL, timeout=5)
        if resp.status_code == 200:
            config = {}
            for hero_guid_hex, perks in resp.json().items():
                try:
                    hero_guid = str(int(str(hero_guid_hex), 16))
                except Exception:
                    hero_guid = str(hero_guid_hex)
                for perk in perks or []:
                    perk_id = str(perk.get("id", ""))
                    if not perk_id:
                        continue
                    try:
                        perk_guid = str(int(perk_id, 16))
                    except Exception:
                        perk_guid = perk_id
                    name_html = str(perk.get("name", "") or "")
                    normalized_name = name_html.replace("<br />", "<br/>").replace("<br/>", "\n")
                    title, _, description = normalized_name.partition("\n")
                    config[perk_guid] = {
                        "heroGuid": hero_guid,
                        "icon": perk.get("icon", ""),
                        "name": title.strip(),
                        "description": description.strip(),
                    }
            return cache_perk_icons(config)
    except Exception:
        logger.debug("failed to fetch perk config", exc_info=True)
    return {}


class AuthExpiredError(Exception):
    """Cookie 已过期或认证失败"""
    pass


class OW2Client:
    """共享基类：封装认证、API 请求和公用数据"""

    AUTH_ERROR_CODES = {10001, 10002, 10003, 401, 403}

    def __init__(self):
        self.base_url = "https://datamsapi.ds.163.com"

        # 从 auth.json 加载认证信息
        auth_path = os.path.join(os.path.dirname(__file__), "..", "config", "auth.json")
        try:
            with open(auth_path, "r", encoding="utf-8") as f:
                auth = json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(
                f"认证文件不存在：{auth_path}\n请创建 auth.json 并填入有效的 Cookie 和自定义请求头。"
            )

        self.cookies = auth.get("cookies", {})
        self._auth = auth
        custom_headers = auth.get("custom_headers", {})

        # 通用请求头（可被 custom_headers 覆盖）
        self.headers = {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json;charset=UTF-8",
            "origin": "https://act.ds.163.com",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36 "
                "app/df_client dfVersion/100109"
            ),
            "sec-ch-ua": '"Chromium";v="108"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-site": "same-site",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
            "referer": "https://act.ds.163.com/",
            "accept-encoding": "gzip, deflate, br",
            "accept-language": "zh-CN,zh;q=0.9",
        }
        self.headers.update(custom_headers)
        if self._auth.get("search_token"):
            self.headers.setdefault("gl-bigdata-auth-token", self._auth.get("search_token"))
            self.headers.setdefault("gl-bigdata-dts", str(self._auth.get("search_dts") or "2026"))
            self.headers.setdefault("gl-bigdata-role-id", str(self._auth.get("search_role_id") or ""))
            self.headers.setdefault("gl-bigdata-server", str(self._auth.get("search_server") or "1"))

        self.map_names = {
            "576460752303423579": "阿努比斯神殿",
            "576460752303423700": "国王大道",
            "576460752303423845": "花村",
            "576460752303423876": "监测站：直布罗陀",
            "576460752303423956": "努巴尼",
            "576460752303423963": "沃斯卡娅工业区",
            "576460752303424175": "好莱坞",
            "576460752303424195": "多拉多",
            "576460752303424695": "尼泊尔",
            "576460752303424955": "66号公路",
            "576460752303425121": "教学模式",
            "576460752303425122": "漓江塔",
            "576460752303425133": "伊利奥斯",
            "576460752303425160": "训练靶场",
            "576460752303425165": "艾兴瓦尔德",
            "576460752303425182": "绿洲城",
            "576460752303425195": "好莱坞",
            "576460752303425201": "国王大道",
            "576460752303425203": "弗格体育场",
            "576460752303425205": "花村",
            "576460752303425207": "漓江塔",
            "576460752303425223": "VPP Green Room",
            "576460752303425225": "怪鼠复仇",
            "576460752303425233": "生态监测站：南极洲",
            "576460752303425235": "\u201c地平线\u201d月球基地",
            "576460752303425285": "墓园",
            "576460752303425292": "黑森林",
            "576460752303425293": "生态监测站：南极洲",
            "576460752303425297": "漓江塔庭院",
            "576460752303425298": "漓江塔夜市",
            "576460752303425303": "尼泊尔圣所",
            "576460752303425306": "漓江塔控制中心",
            "576460752303425308": "城堡",
            "576460752303425334": "尼泊尔村庄",
            "576460752303425336": "尼泊尔圣坛",
            "576460752303425338": "伊利奥斯深井",
            "576460752303425341": "伊利奥斯灯塔",
            "576460752303425342": "伊利奥斯废墟",
            "576460752303425348": "漓江塔控制中心",
            "576460752303425349": "漓江塔庭院",
            "576460752303425350": "漓江塔夜市",
            "576460752303425354": "绿洲城中心",
            "576460752303425356": "绿洲城花园",
            "576460752303425357": "绿洲城大学",
            "576460752303425366": "渣客镇",
            "576460752303425374": "暴雪世界",
            "576460752303425441": "阿育陀耶",
            "576460752303425444": "吉拉德堡",
            "576460752303425506": "釜山",
            "576460752303425524": "阿德勒斯布鲁恩",
            "576460752303425527": "黑森林",
            "576460752303425533": "圣诞节尼泊尔村庄",
            "576460752303425575": "皇家赛道",
            "576460752303425590": "吉拉德堡",
            "576460752303425649": "里阿尔托",
            "576460752303425680": "佩特拉",
            "576460752303425681": "巴黎",
            "576460752303425848": "帕拉伊苏",
            "576460752303426116": "哈瓦那",
            "576460752303426139": "暴雪世界",
            "576460752303426170": "釜山寺院",
            "576460752303426182": "釜山城区",
            "576460752303426283": "新皇后街",
            "576460752303426300": "里约热内卢",
            "576460752303426356": "斗兽场",
            "576460752303426380": "中城",
            "576460752303426496": "多伦多",
            "576460752303426518": "哥德堡",
            "576460752303426624": "地图工坊岛屿",
            "576460752303426628": "地图工坊空地",
            "576460752303426632": "地图工坊室内",
            "576460752303426693": "香巴里寺院",
            "576460752303426734": "巴黎",
            "576460752303426768": "地图工坊空地（夜间）",
            "576460752303426769": "地图工坊岛屿（夜间）",
            "576460752303426771": "铁坂",
            "576460752303426775": "马莱温多",
            "576460752303426780": "釜山体育场",
            "576460752303426795": "悉尼海港竞技场",
            "576460752303426802": "南极半岛",
            "576460752303426878": "苏拉瓦萨",
            "576460752303426892": "地图工坊绿幕",
            "576460752303426899": "埃斯佩兰萨",
            "576460752303427091": "新渣客城",
            "576460752303427250": "鲁纳塞彼",
            "576460752303427264": "萨摩亚",
            "576460752303427312": "教学模式",
            "576460752303427322": "铁坂",
            "576460752303427345": "铁坂",
            "576460752303427346": "铁坂",
            "576460752303427381": "阿特利斯",
            "576460752303427608": "国王大道",
            "576460752303427634": "斗兽场",
            "576460752303427798": "南极半岛",
            "576460752303427844": "哥德堡",
            "576460752303427880": "寺院",
            "576460752303427927": "花冈",
            "576460752303427936": "阿努比斯王座",
            "576460752303428110": "监测站：直布罗陀",
            "576460752303428170": "黑森林",
            "576460752303428186": "高架车站",
            "576460752303428190": "拉克瓦广场",
            "576460752303428192": "红杉水坝",
            "576460752303428219": "伊利奥斯 - 废墟",
            "576460752303428233": "阿努比斯王座",
            "576460752303428266": "维多利亚竞技场",
            "576460752303428277": "阿努比斯王座",
            "576460752303428278": "花冈",
            "576460752303428279": "斗兽场",
            "576460752303428286": "阿德勒斯布鲁恩",
            "576460752303428299": "巴黎",
            "576460752303428337": "尼泊尔 - 圣坛",
            "576460752303428397": "哈瓦那",
            "576460752303428426": "暴雪世界",
        }

        self.rank_scores = {
            "Bronze": 1, "Silver": 2, "Gold": 3, "Platinum": 4,
            "Diamond": 5, "Master": 6, "Grandmaster": 7, "Champion": 8, "Apex": 9, "None": 0,
        }
        self.rank_cn = {
            "Bronze": "青铜", "Silver": "白银", "Gold": "黄金",
            "Platinum": "铂金", "Diamond": "钻石", "Master": "大师",
            "Grandmaster": "宗师", "Champion": "英杰", "Apex": "英杰", "None": "无段位",
        }

        # 英雄名映射（子类直接继承，无需重复请求）
        self.hero_guid_names: dict = fetch_hero_guid_names()
        # 英雄配置（角色类型、头像等）
        self.hero_config: dict = fetch_hero_config()

        self.perk_config: dict = fetch_perk_config()
        self._count_info_cache: dict[tuple[str, str, str], dict] = {}

    def _make_customer_query_token(self, bnet_id) -> str:
        token_template = self._auth.get("customer_token") or self._auth.get("billboard_token", "")
        if not token_template or not bnet_id:
            return ""
        try:
            decoded = base64.b64decode(token_template).decode()
            parts = dict(part.split("=", 1) for part in decoded.split("&") if "=" in part)
            parts["bnetId"] = str(bnet_id)
            parts["timestamp"] = str(int(time.time() * 1000))
            return base64.b64encode("&".join(f"{k}={v}" for k, v in parts.items()).encode()).decode()
        except Exception:
            return ""

    def _check_auth(self, result: dict):
        """检查 API 返回的错误码，auth 失败则抛出 AuthExpiredError"""
        code = result.get("code")
        if code in self.AUTH_ERROR_CODES:
            raise AuthExpiredError(
                f"认证已过期（API code={code}），请更新 auth.json 中的 Cookie"
            )

    def get_billboard_override(self, player_name: str) -> dict | None:
        overrides = self._auth.get("billboard_overrides", {})
        if not isinstance(overrides, dict):
            return None

        raw = overrides.get(player_name)
        if raw is None:
            lowered = player_name.casefold()
            for key, value in overrides.items():
                if isinstance(key, str) and key.casefold() == lowered:
                    raw = value
                    break
        if not isinstance(raw, dict):
            return None

        bnet_id = raw.get("bnet_id") or raw.get("bnetId")
        if not bnet_id:
            return None

        return {
            "customerToken": "",
            "rawCustomerToken": "",
            "bnetId": bnet_id,
            "roleId": raw.get("role_id") or raw.get("roleId") or self._auth.get("search_role_id") or "",
            "billboardToken": raw.get("billboard_token") or raw.get("billboardToken") or "",
            "billboardDts": str(raw.get("billboard_dts") or raw.get("billboardDts") or self._auth.get("search_dts") or "2026"),
            "billboardServer": str(raw.get("billboard_server") or raw.get("billboardServer") or self._auth.get("search_server") or "1"),
            "name": raw.get("display_name") or raw.get("name") or player_name,
            "icon": raw.get("icon", ""),
            "title": raw.get("title", ""),
            "level": raw.get("level", 0),
            "gameTime": raw.get("gameTime", "0"),
            "_source": "override",
        }

    def search_player(self, player_name, server="1", dts="2026", role_id=None):
        """搜索玩家获取 customerToken"""
        url = f"{self.base_url}/v1/a19ld5tool/searchBnetAccount"
        search_token = self._auth.get("search_token") or self._auth.get("billboard_token", "")
        role_id = role_id or self._auth.get("search_role_id") or 684035116
        payload = {
            "token": search_token,
            "roleId": role_id,
            "dts": dts,
            "server": server,
            "name": player_name,
        }
        try:
            response = _api_post(
                url, headers=self.headers, cookies=self.cookies, json=payload
            )
            if response.status_code == 200:
                result = response.json()
                self._check_auth(result)
                if result.get("code") == 0 and "data" in result:
                    resp_data = result["data"]
                    bnet_id = resp_data.get("bnetId")
                    raw_customer_token = resp_data.get("customerToken")
                    customer_query_token = self._make_customer_query_token(bnet_id) or raw_customer_token
                    return {
                        "customerToken": customer_query_token,
                        "rawCustomerToken": raw_customer_token,
                        "bnetId": bnet_id,
                        "roleId": resp_data.get("roleId") or resp_data.get("roleid") or resp_data.get("bnetId"),
                        "billboardToken": resp_data.get("token") or resp_data.get("billboardToken"),
                        "billboardDts": str(resp_data.get("dts") or resp_data.get("timestamp") or "2026"),
                        "billboardServer": str(resp_data.get("server") or "1"),
                        "name": resp_data.get("name"),
                        "icon": resp_data.get("icon", ""),
                        "title": resp_data.get("title", ""),
                        "level": resp_data.get("level", 0),
                        "gameTime": resp_data.get("gameTime", "0"),
                    }
        except AuthExpiredError:
            raise
        except Exception as e:
            print(f"[错误] 搜索玩家失败: {e}")
        return None

    def get_match_list(self, customer_token, game_mode="leisure", page: int = 1, max_pages: int = 1):
        """获取比赛列表"""
        url = f"{self.base_url}/v1/a19ld5tool/customer/queryMatchList"
        try:
            page = max(1, int(page or 1))
            max_pages = max(1, int(max_pages or 1))
            all_matches = []
            seen_match_ids = set()

            for current_page in range(page, page + max_pages):
                params = {
                    "token": customer_token,
                    "gameMode": game_mode,
                    "page": current_page,
                    "season": "",
                }
                response = _api_get(
                    url, params=params, headers=self.headers, cookies=self.cookies
                )
                result = response.json()
                self._check_auth(result)
                if response.status_code != 200 or result.get("code") != 0:
                    break

                items = result.get("data", []) or []
                if not items:
                    break

                for item in items:
                    match_id = item.get("matchId")
                    if match_id and match_id in seen_match_ids:
                        continue
                    if match_id:
                        seen_match_ids.add(match_id)
                    all_matches.append(item)

                if len(items) < 12:
                    break

            return all_matches
        except AuthExpiredError:
            raise
        except Exception as e:
            print(f"[错误] 获取比赛列表失败: {e}")
        return []

    def get_match_info(self, match_id, customer_token):
        """获取对战详情"""
        url = f"{self.base_url}/v1/a19ld5tool/customer/queryMatchInfo"
        params = {"matchId": match_id, "token": customer_token, "season": ""}
        try:
            response = _api_get(
                url, params=params, headers=self.headers, cookies=self.cookies
            )
            result = response.json()
            self._check_auth(result)
            if response.status_code == 200:
                if result.get("code") == 0:
                    return result.get("data", {})
        except AuthExpiredError:
            raise
        except Exception as e:
            print(f"[错误] 获取对战详情失败: {e}")
        return None

    def calculate_kda(self, kill, assist, death):
        """计算 KDA"""
        if death == 0:
            return (kill + assist) if (kill + assist) > 0 else 0
        return (kill + assist) / death

    def _fmt_rank(self, rank_info: dict) -> str:
        """格式化段位 dict 为中文字符串，如 '宗师2' 或 '—'"""
        if not rank_info:
            return "—"
        name = rank_info.get("rankName", "None")
        if not name or name == "None":
            return "—"
        cn = self.rank_cn.get(name, name)
        sub = rank_info.get("rankSubTier", 0)
        return f"{cn}{sub}" if sub > 0 else cn

    def _empty_rank_info(self) -> dict:
        return {"rankName": "None", "rankSubTier": 0, "rankScore": 0, "totalScore": 0}

    def _pick_rank_from_count_info(self, data: dict, role_type=None) -> dict:
        guide_count_data = (data or {}).get("guideCountData", []) or []
        best_rank = None
        best_score = -1
        for role_data in guide_count_data:
            if role_type and role_data.get("roleType") != role_type:
                continue
            current_info = role_data.get("currentRankInfo") or {}
            max_info = role_data.get("maxRankInfo") or {}
            info = current_info if current_info.get("rankName") not in (None, "", "None") else max_info
            rank_name = info.get("rankName", "None")
            rank_sub_tier = int(info.get("rankSubTier", 0) or 0)
            rank_score = int(info.get("rankScore", 0) or 0)
            total = self.rank_scores.get(rank_name, 0) * 1000 + (6 - rank_sub_tier) * 100 + rank_score
            if total > best_score:
                best_score = total
                best_rank = {
                    "rankName": rank_name,
                    "rankSubTier": rank_sub_tier,
                    "rankScore": rank_score,
                    "totalScore": total,
                }
        return best_rank or self._empty_rank_info()

    def get_count_info(self, customer_token: str, game_mode: str = "sport", season: str = "") -> dict:
        """获取玩家完整统计数据（段位/战绩/英雄使用等）

        Args:
            customer_token: 玩家token
            game_mode: 游戏模式，"sport"=竞技(5v5), "sport6v6"=竞技6v6, "leisure"=休闲
            season: 赛季编号，传空字符串表示当前赛季；历史赛季需传纯数字字符串，如 "15"
        """
        url = f"{self.base_url}/v1/a19ld5tool/customer/queryCountInfo"
        params = {"gameMode": game_mode, "token": customer_token, "season": season}
        cache_key = (str(customer_token), str(game_mode), str(season))
        cached = self._count_info_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            response = _api_get(
                url, params=params, headers=self.headers, cookies=self.cookies
            )
            result = response.json()
            self._check_auth(result)
            if response.status_code == 200:
                if result.get("code") == 0:
                    data = result.get("data", {})
                    self._count_info_cache[cache_key] = data
                    return data
        except AuthExpiredError:
            raise
        except Exception as e:
            print(f"[错误] 获取玩家统计失败: {e}")
        return {}

    def get_player_rank(self, customer_token, game_mode="sport", role_type=None, season: str = ""):
        """获取玩家段位，返回 {rankName, rankSubTier, rankScore, totalScore}

        Args:
            customer_token: 玩家token
            game_mode: 游戏模式，"sport"=竞技5v5, "sport6v6"=竞技6v6
            role_type: 职责类型，"tank"/"dps"/"healer"，如果指定则返回该职责的当前段位，否则返回当前最高职责段位
            season: 赛季编号，传空字符串表示当前赛季；历史赛季需传纯数字字符串，如 "15"
        """
        try:
            data = self.get_count_info(customer_token, game_mode=game_mode, season=season)
            return self._pick_rank_from_count_info(data, role_type=role_type)

            # 优先使用当前段位；如果接口没回 currentRankInfo，则回退到 maxRankInfo，
            # 兼容部分玩家/模式下只返回历史最高段位的情况。
            max_rank, max_score = None, 0

            for role_data in guide_count_data:
                # 如果指定了职责类型，只获取该职责的段位
                if role_type and role_data.get("roleType") != role_type:
                    continue

                current_info = role_data.get("currentRankInfo") or {}
                max_info = role_data.get("maxRankInfo") or {}
                info = current_info

                # currentRankInfo 可能存在但没有有效 rankName，这时继续回退。
                if info.get("rankName") in (None, "", "None"):
                    info = max_info

                rank_name = info.get("rankName", "None")
                rank_sub_tier = info.get("rankSubTier", 0)
                rank_score = info.get("rankScore", 0)

                total = self.rank_scores.get(rank_name, 0) * 1000 + (6 - rank_sub_tier) * 100 + rank_score
                if total > max_score:
                    max_score = total
                    max_rank = {"rankName": rank_name, "rankSubTier": rank_sub_tier,
                                "rankScore": rank_score, "totalScore": total}

            return max_rank or {"rankName": "None", "rankSubTier": 0, "rankScore": 0, "totalScore": 0}
        except Exception as e:
            print(f"[警告] 获取段位失败: {e}")
        return {"rankName": "None", "rankSubTier": 0, "rankScore": 0, "totalScore": 0}

    def get_banned_heroes(self) -> list:
        """获取当前赛季/周的禁用英雄列表

        Returns:
            list: 禁用英雄的 heroGuid 列表，如 ["207165582859043299", ...]
        """
        url = f"{self.base_url}/v1/a19ld5tool/getBannedHeroes"
        try:
            response = _api_get(url, headers=self.headers, cookies=self.cookies, timeout=5)
            if response.status_code == 200:
                result = response.json()
                self._check_auth(result)
                if result.get("code") == 0:
                    data = result.get("data", {})
                    # 可能的数据结构：{"bannedHeroes": [...]} 或 {"currentWeek": [...]}
                    banned = data.get("bannedHeroes") or data.get("currentWeek") or data.get("heroes", [])
                    return banned if isinstance(banned, list) else []
        except AuthExpiredError:
            raise
        except Exception as e:
            print(f"[警告] 获取禁用英雄失败: {e}")
        return []

    def get_hero_billboard(
        self,
        bnet_id: int,
        role_id: int | str | None = None,
        billboard_token: str | None = None,
        dts: str | None = None,
        server: str | None = None,
    ) -> dict:
        """获取玩家各英雄全服排名。"""
        billboard_token = billboard_token or self._auth.get("billboard_token", "")
        if not billboard_token:
            return {}

        import base64 as _b64
        try:
            decoded = _b64.b64decode(billboard_token).decode()
            parts = dict(p.split("=", 1) for p in decoded.split("&") if "=" in p)
            parts["bnetId"] = str(bnet_id)
            new_token = _b64.b64encode("&".join(f"{k}={v}" for k, v in parts.items()).encode()).decode()
        except Exception:
            new_token = billboard_token

        url = f"{self.base_url}/v1/a19ld5tool/billboard/customGetUserHeroBillboard"
        params = {"token": new_token}
        try:
            response = _api_get(url, params=params, headers=self.headers, cookies=self.cookies)
            if response.status_code == 200:
                result = response.json()
                self._check_auth(result)
                if result.get("code") == 0:
                    return result.get("data", {})
        except AuthExpiredError:
            raise
        except Exception as e:
            print(f"[错误] 获取英雄排名失败: {e}")
        return {}
