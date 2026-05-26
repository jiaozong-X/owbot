# OW2 Bot — 守望先锋 2 战绩查询机器人

基于 NapCat（QQ）和千寻（微信）框架的 **守望先锋 2 国服** 战绩查询机器人。通过网易数据 API 拉取游戏数据，自动渲染图文卡片推送到 QQ/微信。

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## 功能一览

### 玩家查询

| 命令 | 说明 |
|---|---|
| `/战绩 玩家名` | 最新对局详情（禁用英雄、威能、段位区间） |
| `/查询 玩家名 N` | 第 N 场对局详情 |
| `/资料 玩家名` | 玩家资料总览（段位、KDA、常用英雄） |
| `/强度 玩家名` | 最近 12 场对局强度分析（S～F 评级） |
| `/对比 玩家A 玩家B` | 两名玩家数据对比 |

### 统计分析

| 命令 | 说明 |
|---|---|
| `/英雄统计 玩家名` | 英雄详细统计（按职责分组） |
| `/地图统计 玩家名` | 地图胜率统计 |
| `/英雄榜 玩家名` | 英雄天梯排名 |
| `/段位历史 玩家名` | 段位历史趋势 |
| `/连胜 玩家名` | 当前连胜 / 连败 |
| `/周报 玩家名` | 自然周周报（高光时刻、每日日历、队友分析） |
| `/段位分布` | 本群绑定玩家的段位分布 |
| `/全局分布` | 所有绑定玩家的段位分布 |

### 群排行

| 命令 | 说明 |
|---|---|
| `/绑定 玩家名#数字` | 绑定你的游戏 ID |
| `/解绑` | 解除绑定 |
| `/排行` | 群内按段位排名 |
| `/全局排行` | 全部绑定玩家排名 |

### 资讯

| 命令 | 说明 |
|---|---|
| `/新闻` | 暴雪官方活动资讯 |
| `/调整` | 英雄平衡性调整详情 |
| `/订阅 玩家名` | 订阅战绩推送（新对局自动通知） |
| `/取消订阅` | 取消订阅 |

---

## 环境要求

- **Python 3.10+**
- **Google Chrome**（用于 HTML → 图片渲染，必须安装）
- **NapCat QQ**（QQ 机器人框架）或 **千寻微信框架**

---

## 详细配置教程

### 第一步：抓取 API 认证信息（最关键的一步）

机器人通过调用网易 OW2 数据服务来获取玩家战绩，**需要从网易 DD 桌面客户端中抓取请求头**。

#### 需要的工具

- **Fiddler Everywhere** 或 **Charles**（推荐，界面友好）
- 或 **Proxifier** + **Wireshark**（进阶）
- 或 **HTTP Toolkit**（免费开源）

> 网易 DD 是桌面软件（不是网页），所以不能用浏览器 F12 抓包，必须用系统级的抓包工具拦截它的 HTTP/HTTPS 流量。

#### 1.1 配置抓包工具（以 Fiddler Everywhere 为例）

**① 安装并启动 Fiddler Everywhere**

下载地址：https://www.telerik.com/download/fiddler-everywhere

**② 开启 HTTPS 解密**

- 菜单 → **Settings** → **HTTPS**
- 勾选 **Decrypt HTTPS traffic**
- 点击 **Install Root Certificate**，安装根证书（让 Fiddler 能解密 HTTPS 流量）

**③ 配置过滤**

在过滤器中输入：`datamsapi.ds.163.com`
这样只显示网易 OW2 API 的请求，不会被其他流量刷屏。

#### 1.2 启动网易 DD 并触发 API 请求

- 打开 **网易 DD** 桌面客户端
- 登录你的网易通行证账号
- 进入 **守望先锋** 相关页面（我的战绩 / 英雄榜 / 搜索玩家等）
- 此时 Fiddler 中会捕获到发往 `datamsapi.ds.163.com` 的网络请求

#### 1.3 找到目标 API 请求

在 Fiddler 中找到以下请求（按名称过滤）：

| 请求路径 | 说明 |
|---|---|
| `searchBnetAccount` | 搜索玩家（最常用，优先抓这个） |
| `queryMatchList` | 获取对战列表 |
| `queryCountInfo` | 获取玩家统计数据 |
| `getUserHeroBillboard` | 获取英雄榜（可选） |

**双击任意一个请求**，在右侧面板查看 **Headers（请求头）**。

#### 1.4 提取关键字段

从请求头中复制以下内容：

**① Cookies**
在 `Cookie:` 字段中，复制所有键值对。
示例：
```
GOD_UUID=5a11ae946f0...; NTES_SESS=gQzzzh...; S_INFO=1777...; P_INFO=...;
```

**② `gl-bigdata-auth-token`（搜索 Token）**

在请求头中找到 `gl-bigdata-auth-token`，复制它的值。

**③ 以 `gl-` 开头的自定义请求头**

| 请求头 | 示例值 |
|---|---|
| `gl-clienttype` | `60` |
| `gl-deviceid` | `3fee64e3-...` |
| `gl-uid` | `5a11ae946f0...` |
| `gl-x-xsrf-token` | `0a186d48-...` |
| `gl-checksum` | `876c3c29...` |
| `gl-bigdata-role-id` | `687769480` |
| `gl-bigdata-server` | `1` |
| `gl-bigdata-dts` | `2026` |
| `gl-bigdata-auth-token` | `dfd709b4...`（与上面的搜索 Token 相同） |

**④ `billboard_token`（可选，用于英雄榜功能）**

如果在 `getUserHeroBillboard` 请求的 Query String 中看到 `token` 参数，复制其值。
如果没有这个功能可以忽略，英雄榜功能将被禁用。

#### 1.5 创建 auth.json

将提取的信息填入 `config/auth.json`：

```json
{
    "cookies": {
        "GOD_UUID": "从抓包中复制的GOD_UUID",
        "NTES_SESS": "从抓包中复制的NTES_SESS",
        "S_INFO": "从抓包中复制的S_INFO",
        "P_INFO": "从抓包中复制的P_INFO",
        "JSESSIONID": "从抓包中复制的JSESSIONID",
        "GL-XSRF-TOKEN": "从抓包中复制的GL-XSRF-TOKEN",
        "NTES_P_UTID": "从抓包中复制的NTES_P_UTID",
        "_ns": "从抓包中复制的_ns"
    },
    "custom_headers": {
        "gl-clienttype": "60",
        "gl-deviceid": "从抓包中复制的gl-deviceid",
        "gl-uid": "从抓包中复制的gl-uid",
        "gl-x-xsrf-token": "从抓包中复制的gl-x-xsrf-token",
        "gl-checksum": "从抓包中复制的gl-checksum",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36 app/df_client dfVersion/100109",
        "gl-bigdata-role-id": "从抓包中复制的gl-bigdata-role-id",
        "gl-bigdata-auth-token": "从抓包中复制的gl-bigdata-auth-token",
        "gl-bigdata-server": "1",
        "gl-bigdata-dts": "2026"
    },
    "search_token": "gl-bigdata-auth-token 的值",
    "search_role_id": 687769480,
    "search_dts": "2026",
    "search_server": "1",
    "billboard_token": "（可选）base64编码的billboard token"
}
```

> ⚠️ **重要提示**：
> - `auth.json` **不会被提交到 GitHub**（已在 `.gitignore` 中排除）
> - Cookie 和 Token 有过期时间，通常每隔几周需要重新抓取一次
> - 代码中自动检测认证过期，当 API 返回 `10001/10002/10003/401/403` 错误码时会抛出 `AuthExpiredError`，这时按上述步骤重新抓取即可

#### 抓包示意图

```
Fiddler Everywhere
┌──────────────────────────────────────────────┐
│  Filters: datamsapi.ds.163.com                │
│                                               │
│  #  Result  Protocol  Host                    │
│  1  200      HTTPS     datamsapi.ds.163.com   │  ← 双击
│  2  200      HTTPS     datamsapi.ds.163.com   │
│  3  200      HTTPS     datamsapi.ds.163.com   │
│                                               │
├──────────────────────────────────────────────┤
│  Request Headers                     [JSON]   │
│                                               │
│  Cookie: GOD_UUID=...; NTES_SESS=...  ← 复制  │
│  gl-bigdata-auth-token: xxxxxxxx       ← 复制  │
│  gl-clienttype: 60                      ← 复制  │
│  gl-deviceid: 3fee64e3-...             ← 复制  │
│  ...                                            │
└──────────────────────────────────────────────┘
```

---

### 第二步：配置机器人框架

选择以下两种方式之一：

#### 方式 A：QQ 机器人（NapCat）

1. 安装 [NapCat](https://napcat.napneko.icu/)（QQ 机器人框架）
2. 启动 NapCat，进入 WebUI 管理界面
3. 开启**正向 WebSocket 服务**，默认端口 `3001`
4. 如果设置了访问令牌，修改 `config.py` 中的 `WS_TOKEN`
5. 确保 `config.py` 中 `ENABLE_QQ = True`

#### 方式 B：微信机器人（千寻）

1. 安装千寻微信框架
2. 开启 WebSocket 服务（默认端口 `7778`）
3. 在 `config/auth.json` 的 `wechat` 字段中填入 `wxid` 和 `safekey`：

```json
{
    "wechat": {
        "wxid": "你的机器人wxid",
        "safekey": "你的safekey"
    }
}
```

4. 确保 `config.py` 中 `ENABLE_WECHAT = True`

---

### 第三步：修改基础配置

编辑 `config.py`：

```python
WS_URL = "ws://127.0.0.1:3001"     # NapCat WebSocket 地址（默认不用改）
WS_TOKEN = "1234"                    # NapCat 访问令牌（需与 NapCat 配置一致）

ENABLE_QQ = True                     # 启用 QQ 机器人
ENABLE_WECHAT = False                # 启用微信机器人（如果不用则关闭）

CURRENT_SEASON = "22"                # 守望先锋当前赛季编号
SUBSCRIPTION_CHECK_INTERVAL = 300    # 订阅推送检查间隔（秒）

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
                                     # Chrome 浏览器安装路径（必须正确）
```

---

### 第四步：安装依赖并启动

```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 启动机器人
python bot.py
```

如果一切正常，你会看到控制台输出：
```
[HH:MM:SS] SYS      系统    正在连接 QQ WebSocket... ws://127.0.0.1:3001
[HH:MM:SS] SYS      系统    帮助图预渲染完成
[HH:MM:SS] SYS      系统    QQ WebSocket 已连接
```

---

## 项目结构

```
ow2-bot/
├── bot.py                  # 主入口：WebSocket 连接、消息路由、主循环
├── bot_state.py            # 全局状态管理：绑定、订阅、群设置、限流
├── config.py               # 基础配置参数
├── renderer.py             # Chrome Headless 截图渲染
├── requirements.txt        # Python 依赖
├── run.bat                 # Windows 启动脚本
│
├── ow2/                    # OW2 数据查询模块
│   ├── client.py           # API 客户端：认证、会话池、缓存
│   ├── query.py            # 最新/第N场对局查询
│   ├── profile.py          # 玩家资料总览
│   ├── analyzer.py         # 对局强度分析（12场 S~F评级）
│   ├── compare.py          # 玩家对比
│   ├── leaderboard.py      # 群排行
│   ├── herobillboard.py    # 英雄天梯排名
│   ├── herostats.py        # 英雄详细统计
│   ├── mapstats.py         # 地图胜率统计
│   ├── streak.py           # 连胜/连败追踪
│   ├── weeklysummary.py    # 自然周周报
│   ├── rankhistory.py      # 段位历史趋势
│   ├── playerdistribution.py  # 段位分布
│   ├── news.py             # 暴雪补丁/资讯解析
│   └── __init__.py
│
├── templates/              # HTML 渲染模板
│   ├── match.html          # 对局报告模板
│   ├── profile.html        # 玩家资料模板
│   ├── strength.html       # 强度分析模板（含 Chart.js 图表）
│   ├── herostats.html      # 英雄统计模板
│   ├── mapstats.html       # 地图统计模板
│   ├── help.html           # 帮助菜单模板
│   └── ...
│
├── assets/                 # 英雄/威能图标（自动从 CDN 缓存到本地）
│   ├── heroes/             # 英雄头像
│   └── perks/              # 威能图标
│
└── config/                 # 运行时配置（.gitignore 排除，不提交）
    ├── auth.json           # API 认证信息（Cookie/Token）
    ├── bindings.json       # 玩家绑定记录
    ├── subscriptions.json  # 订阅记录
    └── group_settings.json # 群功能开关设置
```

---

## 常见问题

### Q：如何更新 Cookie？

Cookie 通常几周后会过期，当机器人提示 `认证已过期` 或 API 返回错误时，重复 **第一步** 的抓包流程，替换 `auth.json` 中的内容即可。

### Q：Chrome 截图失败怎么办？

- 确认 `config.py` 中的 `CHROME_PATH` 指向正确的 Chrome 安装路径
- 如果你用的是 Chromium / Edge，也需要修改路径
- 首次运行会启动 Chrome Headless，需要几秒加载，请耐心等待

### Q：支持竞技模式和休闲模式同时查询吗？

支持。命令中可指定模式：
- `/战绩 玩家名` 默认查询休闲模式
- `/竞技 玩家名` 查询竞技模式
- `/资料 玩家名` 同时显示所有模式

### Q：为什么查询有些玩家显示"段位：—"？

如果对方隐私设置不允许查看战绩，API 无法获取其段位信息。这是网易 OW2 数据服务的限制。

---

## 技术要点

- **数据源**：网易 OW2 数据服务 API（`datamsapi.ds.163.com`）
- **认证**：基于 Cookie + Token，自动检测过期并抛出 `AuthExpiredError`
- **并发控制**：全局连接池（8 并发限制）+ 请求限流（每分钟 5 次/用户）
- **渲染方案**：HTML 模板 → Chrome Headless 截图 → Pillow 裁剪 → base64 发送到 QQ/微信
- **缓存策略**：英雄/威能图标自动下载到本地，manifest 文件追踪版本，避免重复渲染时请求外网
- **数据安全**：所有状态文件（绑定/订阅/群设置）采用原子写入（先写临时文件 → `os.replace`），崩溃不丢数据
- **双平台**：同一套代码同时支持 QQ（NapCat OneBot 协议）和微信（千寻框架）

---

## 免责声明

1. 本项目**仅供学习交流使用**，不得用于商业用途。
2. 使用网易 OW2 数据服务需遵守其**服务条款**。
3. 守望先锋 2 数据版权归 **Blizzard Entertainment** / **网易** 所有。
4. 不得将本项目用于任何违反游戏用户协议的行为。

---

## License

[MIT](LICENSE)
