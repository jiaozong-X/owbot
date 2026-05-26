import json
import os

_auth_path = os.path.join(os.path.dirname(__file__), "config", "auth.json")
try:
    with open(_auth_path, "r", encoding="utf-8") as _f:
        _auth = json.load(_f)
except FileNotFoundError:
    _auth = {}

# NapCat WebSocket 配置（QQ）
WS_URL   = "ws://127.0.0.1:3001"   # NapCat 正向 WS 地址，端口在 NapCat 配置里确认
WS_TOKEN = "1234"                    # NapCat 访问令牌

# 千寻微信框架 WebSocket 配置（微信）
WX_WS_URL  = "ws://127.0.0.1:7778"  # 千寻正向 WS 地址，默认 7778，可在框架设置中修改
WX_WS_PING_INTERVAL = None
WX_WS_PING_TIMEOUT = None
WX_WXID    = _auth.get("wechat", {}).get("wxid", "")
WX_SAFEKEY = _auth.get("wechat", {}).get("safekey", "")

# 启用开关
ENABLE_QQ     = True   # 是否启动 QQ 机器人（NapCat）
ENABLE_WECHAT = False  # 是否启动微信机器人（千寻）

# 管理员列表（QQ号或微信wxid，用于广播等管理功能）
ADMINS = [
    "2157123981",
]

# 订阅功能配置
SUBSCRIPTION_CHECK_INTERVAL = 300  # 订阅检查间隔（秒），默认5分钟

# 当前国服赛季编号
CURRENT_SEASON = "22"

# Chrome 路径（headless 渲染用）
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# 截图宽度（px）
SCREENSHOT_WIDTH = 1600

# 认证文件路径（Cookie 和自定义请求头存放于此，不要硬编码在代码中）
AUTH_FILE = os.path.join(os.path.dirname(__file__), "config", "auth.json")
