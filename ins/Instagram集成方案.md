# Instagram 监控集成方案

> 将 Instagram 作品监控集成到 douyin_user_monitor 项目，统一管理面板。

## 一、项目现状分析

### 1.1 现有架构

```
douyin_user_monitor/
├── FastAPI (port 8900)                    # Web 服务
├── data/monitor_users.json                # JSON 文件存储（无数据库）
├── download/                              # 媒体下载目录
├── 上游: Douyin_TikTok_Download_API (port 8899)
├── Telegram 通知
├── 监控面板: /api/monitor/dashboard
└── 统计面板: /api/monitor/statistics/dashboard
```

### 1.2 核心模块

| 模块 | 文件 | 职责 |
|------|------|------|
| `MonitorCrawlerProtocol` | `monitor/crawler_protocol.py` | 爬虫抽象接口（仅抖音实现） |
| `MonitorService` | `monitor/service.py` | 统一调度：轮询、下载、通知 |
| `MonitorStorage` | `monitor/storage.py` | JSON 文件持久化 |
| `AwemeAssetDownloader` | `monitor/downloader.py` | 下载器（硬耦合抖音 aweme 结构） |
| `UserSyncService` | `monitor/user_sync.py` | 同步逻辑：拉取作品、增量检测、下载 |
| `TelegramNotifier` | `monitor/telegram_notifier.py` | Telegram 通知 |
| `dashboard.html` | `web/dashboard.html` | 监控管理面板（Mosaic 风格） |

### 1.3 数据模型（monitor_users.json）

```json
{
  "users": [
    {
      "id": "uuid",
      "profile_url": "https://www.douyin.com/user/...",
      "sec_user_id": "MS4wLjABAAAA...",
      "nickname": "用户昵称",
      "avatar_url": "https://...",
      "account_status": "normal|deleted|banned",
      "enabled": true,
      "created_at": "ISO8601",
      "last_checked_at": "ISO8601",
      "last_download_at": "ISO8601",
      "last_aweme_id": "7653088509251760563",
      "downloaded_count": 44,
      "downloaded_aweme_ids": ["..."],
      "download_records": [{ "aweme_id": "...", "desc": "...", ... }],
      "history_sync": { "status": "pending|completed|paused", ... },
      "last_error": null
    }
  ],
  "monitoring": {
    "is_running": false,
    "mode": "interval|coverage",
    "interval_hours": 0.05,
    "coverage_hours": 24.0,
    "last_run_at": "ISO8601",
    "last_run_result": {}
  }
}
```

### 1.4 接口一览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/monitor/dashboard` | 监控管理页面 |
| GET | `/api/monitor/statistics/dashboard` | 统计页面 |
| GET | `/api/monitor/users` | 获取用户列表 |
| POST | `/api/monitor/users` | 添加监控用户 |
| PATCH | `/api/monitor/users/{id}` | 启用/禁用用户 |
| DELETE | `/api/monitor/users/{id}` | 删除用户 |
| POST | `/api/monitor/users/{id}/run_once` | 手动执行单用户 |
| POST | `/api/monitor/start` | 启动监控循环 |
| POST | `/api/monitor/stop` | 停止监控循环 |
| POST | `/api/monitor/run_once` | 手动执行一次全部 |
| GET | `/api/monitor/status` | 获取监控状态 |
| GET | `/api/monitor/users/{id}/avatar` | 代理头像 |
| POST | `/api/monitor/users/{id}/backfill/start` | 启动历史回填 |
| POST | `/api/monitor/users/{id}/backfill/pause` | 暂停历史回填 |
| POST | `/api/monitor/users/{id}/backfill/resume` | 恢复历史回填 |

---

## 二、Instagram 技术选型

### 2.1 采集工具对比

| 工具 | 稳定性 | Python API | 不登录 | Stars | 维护状态 |
|------|--------|-----------|--------|-------|---------|
| **Instaloader** | ★★★★★ | 有 | 极不稳定 | 9k+ | 持续维护 |
| gallery-dl | ★★★★ | 无（CLI） | 不可用 | 12k+ | 持续维护 |
| instagram-scraper | ★★★ | 无（CLI） | 不可用 | 7k+ | 维护较少 |

**选择：Instaloader**，原因：
- 有完整的 Python API，可直接嵌入 FastAPI 服务
- 社区最活跃，Instagram API 变化后修复最快
- 支持 `--fast-update` 增量下载
- 支持 session 持久化，避免重复登录

### 2.2 登录策略

**必须登录**。Instagram 从 2022 年起逐步封死匿名访问，2025-2026 年匿名模式基本不可用（仅能获取约 12 个帖子后被 401 阻断）。

**推荐方案：专用小号**
- 注册一个仅用于监控的 Instagram 小号
- 只关注目标用户
- 即使被限流也不影响主账号
- session 文件持久化后可非交互式运行

### 2.3 防限流策略

| 参数 | 值 | 说明 |
|------|------|------|
| 请求间隔 | 8-12 秒 | Instagram 严格限流，低于 6 秒易触发 429 |
| 每轮检查数量 | 最新 20 个帖子 | 与抖音模块保持一致 |
| 增量模式 | `--fast-update` 语义 | 遇到已下载内容停止 |
| 错误重试 | 3 次，指数退避 | 401/429 时等待后重试 |

---

## 三、集成方案设计

### 3.1 设计原则

1. **并行独立模块** — Instagram 数据流完全独立，不改动现有抖音代码
2. **共用 FastAPI 实例** — 同一个 uvicorn 进程，不同路由前缀
3. **共用 Telegram 通知** — 复用现有 bot_token 和 chat_id
4. **独立数据文件** — Instagram 状态存储在独立 JSON 文件
5. **独立下载目录** — `download/instagram/` 与 `download/` 平级
6. **面板风格统一** — 复用 dashboard.html 的 Mosaic 风格和 CSS 变量

### 3.2 目录结构

```
douyin_user_monitor/
├── douyin_user_monitor/
│   ├── main.py                     # [改动] 注册 Instagram 路由
│   ├── settings.py                 # [改动] 新增 Instagram 配置段
│   ├── api/
│   │   ├── monitor.py              # 现有抖音路由（不动）
│   │   └── ig_monitor.py           # [新增] Instagram API 路由
│   ├── monitor/
│   │   ├── service.py              # 现有抖音服务（不动）
│   │   ├── ig_service.py           # [新增] Instagram 监控服务
│   │   ├── ig_crawler.py           # [新增] Instaloader 封装
│   │   ├── ig_downloader.py        # [新增] Instagram 下载器
│   │   └── ig_storage.py           # [新增] Instagram 状态存储
│   └── web/
│       ├── dashboard.html          # 现有抖音面板（不动）
│       ├── statistics.html         # 现有统计面板（不动）
│       └── ig_dashboard.html       # [新增] Instagram 面板
├── config.yaml                     # [改动] 新增 instagram 配置段
├── data/
│   ├── monitor_users.json          # 现有抖音数据（不动）
│   └── ig_monitor_users.json       # [新增] Instagram 数据
├── download/
│   ├── (抖音下载目录)              # 现有（不动）
│   └── instagram/                  # [新增] Instagram 下载目录
└── ins/                            # 本报告目录
```

### 3.3 数据流

```
┌─────────────────────────────────────────────────────────┐
│                    FastAPI (port 8900)                    │
│                                                         │
│  /api/monitor/*          /api/instagram/*                │
│  ┌─────────────┐        ┌──────────────┐                │
│  │ 抖音路由     │        │ Instagram 路由│                │
│  └──────┬──────┘        └──────┬───────┘                │
│         │                      │                        │
│  ┌──────▼──────┐        ┌──────▼───────┐                │
│  │ MonitorService│       │ IgService    │                │
│  │ (抖音调度)   │        │ (IG 调度)    │                │
│  └──────┬──────┘        └──────┬───────┘                │
│         │                      │                        │
│  ┌──────▼──────┐        ┌──────▼───────┐                │
│  │ DouyinClient │       │ IgCrawler    │                │
│  │ (上游 8899)  │       │ (Instaloader)│                │
│  └──────┬──────┘        └──────┬───────┘                │
│         │                      │                        │
│  ┌──────▼──────┐        ┌──────▼───────┐                │
│  │ AwemeDownloader│     │ IgDownloader │                │
│  └──────┬──────┘        └──────┬───────┘                │
│         │                      │                        │
│  ┌──────▼──────┐        ┌──────▼───────┐                │
│  │ monitor_users│       │ ig_monitor   │                │
│  │ .json        │       │ _users.json  │                │
│  └─────────────┘        └──────────────┘                │
│                                                         │
│         ┌────────────────────┐                          │
│         │  TelegramNotifier  │  ← 两个模块共用           │
│         └────────────────────┘                          │
└─────────────────────────────────────────────────────────┘
```

---

## 四、各模块详细设计

### 4.1 IgCrawler（Instaloader 封装）

**文件**: `monitor/ig_crawler.py`

**职责**: 封装 instaloader Python API，提供统一的爬虫接口。

```python
class IgCrawler:
    """Instagram 爬虫，基于 instaloader Python API。"""

    def __init__(self, username: str, session_file: str):
        self.L = instaloader.Instaloader(
            download_videos=True,
            download_video_thumbnails=False,
            download_comments=False,
            save_metadata=False,
            compress_json=False,
        )
        self._load_session(username, session_file)

    def _load_session(self, username, session_file):
        """加载已有 session 或交互式登录。"""
        ...

    def get_user_profile(self, username: str) -> dict:
        """获取用户资料（昵称、头像、粉丝数、作品数）。"""
        ...

    def fetch_latest_posts(self, username: str, count: int = 20) -> list:
        """获取最新 N 个帖子。"""
        ...

    def fetch_post_detail(self, post) -> dict:
        """获取单个帖子详情（用于下载）。"""
        ...
```

**关键实现**:
- Session 文件路径: `data/ig_session-{username}`
- 首次登录需交互式输入密码，之后自动复用 session
- 每次请求间隔 8 秒（`time.sleep(8)`）
- Profile 信息包含: `username`, `full_name`, `biography`, `followers`, `mediacount`, `profile_pic_url`

### 4.2 IgDownloader（下载器）

**文件**: `monitor/ig_downloader.py`

**职责**: 下载 Instagram 帖子的图片/视频到本地。

```python
class IgDownloader:
    """Instagram 媒体下载器。"""

    def __init__(self, download_root: Path):
        self._download_root = download_root

    async def download_post(self, post, username: str) -> dict:
        """下载单个帖子的所有媒体文件。
        返回: { "media_type": "image|video", "files": [...], "total_size_bytes": int }
        """
        ...
```

**下载目录结构**:
```
download/instagram/
├── target_user1/
│   ├── 2026-06-29_abc123.jpg
│   ├── 2026-06-28_def456/
│   │   ├── 1.jpg
│   │   ├── 2.jpg
│   │   └── 3.jpg
│   └── 2026-06-27_ghi789.mp4
└── target_user2/
    └── ...
```

### 4.3 IgStorage（状态存储）

**文件**: `monitor/ig_storage.py`

**职责**: 管理 Instagram 监控状态的 JSON 持久化。

**数据模型** (`data/ig_monitor_users.json`):

```json
{
  "users": [
    {
      "id": "uuid",
      "platform": "instagram",
      "username": "target_user",
      "full_name": "Display Name",
      "avatar_url": "https://...",
      "bio": "User bio text",
      "follower_count": 12345,
      "post_count": 567,
      "enabled": true,
      "created_at": "ISO8601",
      "last_checked_at": "ISO8601",
      "last_download_at": "ISO8601",
      "downloaded_count": 42,
      "downloaded_post_ids": ["mediaid_1", "mediaid_2"],
      "download_records": [
        {
          "post_id": "mediaid_1",
          "shortcode": "abc123",
          "caption": "...",
          "media_type": "image|video",
          "is_video": false,
          "like_count": 100,
          "comment_count": 20,
          "posted_at": "ISO8601",
          "downloaded_at": "ISO8601",
          "files": ["target_user/2026-06-29_abc123.jpg"],
          "total_size_bytes": 1048576
        }
      ],
      "last_error": null
    }
  ],
  "monitoring": {
    "is_running": false,
    "interval_hours": 6.0,
    "last_run_at": null,
    "last_run_result": {}
  }
}
```

### 4.4 IgService（监控服务）

**文件**: `monitor/ig_service.py`

**职责**: Instagram 监控的核心调度逻辑。

```python
class IgService:
    """Instagram 监控服务。"""

    def __init__(self, crawler, downloader, storage, notifier):
        ...

    async def add_user(self, username: str) -> dict:
        """添加 Instagram 监控用户。"""
        ...

    async def sync_one_user(self, user: dict, summary: dict) -> None:
        """同步单个用户：检查新帖子并下载。"""
        ...

    async def start_monitoring(self, interval_hours: float) -> dict:
        """启动定时监控循环。"""
        ...

    async def stop_monitoring(self) -> dict:
        """停止监控循环。"""
        ...
```

**同步逻辑**（与抖音模块保持一致的模式）:
1. 获取用户最新 20 个帖子
2. 对比 `downloaded_post_ids`，筛选新帖子
3. 逐个下载新帖子（间隔 8 秒）
4. 更新 `downloaded_post_ids` 和 `download_records`
5. 发送 Telegram 通知

### 4.5 IgMonitorRouter（API 路由）

**文件**: `api/ig_monitor.py`

**API 设计**（与抖音模块保持一致的风格）:

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/instagram/dashboard` | Instagram 监控面板 |
| GET | `/api/instagram/users` | 获取监控用户列表 |
| POST | `/api/instagram/users` | 添加监控用户（用户名） |
| PATCH | `/api/instagram/users/{id}` | 启用/禁用用户 |
| DELETE | `/api/instagram/users/{id}` | 删除用户 |
| POST | `/api/instagram/users/{id}/run_once` | 手动同步单用户 |
| POST | `/api/instagram/start` | 启动监控 |
| POST | `/api/instagram/stop` | 停止监控 |
| POST | `/api/instagram/run_once` | 手动执行一次全部 |
| GET | `/api/instagram/status` | 获取监控状态 |
| GET | `/api/instagram/users/{id}/avatar` | 代理头像 |

### 4.6 ig_dashboard.html（监控面板）

**复用现有风格**:
- 同样的 Mosaic 配色方案（CSS 变量）
- 同样的侧边栏布局
- 同样的卡片、表格、按钮样式

**新增内容**:
- Instagram 专属图标（相机/IG logo SVG）
- 用户信息显示: username、follower_count、post_count
- 作品类型标签: 图片集 / 视频 / Reels

### 4.7 配置扩展

**config.yaml 新增段**:

```yaml
instagram:
  enabled: true
  login_user: "your_bot_username"
  session_file: "data/ig_session"
  state_path: "data/ig_monitor_users.json"
  download_root: "download/instagram"
  check_interval_hours: 6
  request_delay_seconds: 8
```

**settings.py 新增**:

```python
@dataclass(frozen=True)
class InstagramSettings:
    enabled: bool
    login_user: str
    session_file: Path
    state_path: Path
    download_root: Path
    check_interval_hours: float
    request_delay_seconds: float
```

### 4.8 main.py 改动

仅新增 2 行：

```python
from douyin_user_monitor.api.ig_monitor import router as ig_router
app.include_router(ig_router, prefix="/api/instagram", tags=["Instagram"])
```

---

## 五、Telegram 通知格式

### 5.1 发现新帖子

```
📸 Instagram 新作品

👤 用户: target_user
📝 描述: Beach day vibes
📊 类型: 图片集 (5张)
❤️ 点赞: 1234
🔗 链接: https://www.instagram.com/p/abc123/
⏰ 时间: 2026-06-29 15:30:00
```

### 5.2 下载完成

```
✅ Instagram 下载完成

👤 用户: target_user
📝 描述: Beach day vibes
📊 类型: 图片集 (5张)
💾 大小: 12.34 MB
📁 路径: target_user/20260629_153000_Beach_day_vibes/
⏰ 时间: 2026-06-29 15:30:08
```

---

## 六、改动影响评估

### 6.1 现有代码改动

| 文件 | 改动 | 风险 |
|------|------|------|
| `main.py` | 新增 2 行路由注册 | 零风险 |
| `settings.py` | 新增 InstagramSettings 数据类 | 零风险（纯新增） |
| `config.yaml` | 新增 instagram 配置段 | 零风险（可选配置） |
| `web/dashboard.html` | 侧边栏加一个链接 | 极低风险 |

### 6.2 新增文件

| 文件 | 行数估算 | 说明 |
|------|---------|------|
| `monitor/ig_crawler.py` | ~120 行 | Instaloader 封装 |
| `monitor/ig_downloader.py` | ~100 行 | 下载逻辑 |
| `monitor/ig_storage.py` | ~80 行 | JSON 存储 |
| `monitor/ig_service.py` | ~200 行 | 调度服务 |
| `api/ig_monitor.py` | ~180 行 | API 路由 |
| `web/ig_dashboard.html` | ~600 行 | 监控面板 |
| **合计** | **~1280 行** | |

### 6.3 依赖新增

```
# requirements.txt 新增
instaloader>=4.10
```

### 6.4 不影响的部分

- 抖音监控逻辑完全不动
- 现有 JSON 数据文件不动
- 现有下载目录不动
- Telegram bot_token/chat_id 复用，不新增配置
- systemd 服务配置不动（同一个 uvicorn 进程）

---

## 七、部署步骤

### 7.1 安装依赖

```bash
cd /root/douyin_user_monitor
source .venv/bin/activate
pip install instaloader>=4.10
```

### 7.2 首次登录 Instagram（创建 session）

```bash
python3 -c "
import instaloader
L = instaloader.Instaloader()
L.login('your_bot_username', 'your_bot_password')
L.save_session_to_file('data/ig_session')
print('Session saved.')
"
```

### 7.3 更新配置

```yaml
# config.yaml 新增
instagram:
  enabled: true
  login_user: "your_bot_username"
  session_file: "data/ig_session"
  state_path: "data/ig_monitor_users.json"
  download_root: "download/instagram"
  check_interval_hours: 6
  request_delay_seconds: 8
```

### 7.4 重启服务

```bash
systemctl restart douyin-monitor-8900
```

### 7.5 验证

```bash
# 检查面板
curl http://localhost:8900/api/instagram/dashboard

# 添加用户
curl -X POST http://localhost:8900/api/instagram/users \
  -H "Content-Type: application/json" \
  -d '{"username": "target_user"}'

# 手动同步
curl -X POST http://localhost:8900/api/instagram/users/{id}/run_once
```

---

## 八、风险与注意事项

### 8.1 Instagram 限流

- Instagram 对自动化请求非常敏感
- 请求间隔建议不低于 8 秒
- 每日总请求量建议不超过 500 次
- 遇到 401/429 错误时需等待 5-10 分钟后重试

### 8.2 Session 过期

- Instagram session 通常有效期 1-3 个月
- 过期后需重新登录（交互式输入密码）
- 建议在 config.yaml 中配置 TG 通知，session 失效时收到提醒

### 8.3 账号风险

- 专用小号不要关注太多人（建议 < 50）
- 不要频繁添加/删除监控用户
- 小号不要发布内容，纯用于监控

### 8.4 存储空间

- Instagram 图片/视频体积较大
- 建议定期检查 `download/instagram/` 目录大小
- 可在 config.yaml 中配置 `max_posts_per_user` 限制历史回填深度

---

## 九、后续扩展

| 优先级 | 功能 | 说明 |
|--------|------|------|
| P1 | Stories 监控 | 定时下载目标用户的 24h Stories |
| P1 | Reels 监控 | 支持下载 Reels 短视频 |
| P2 | 统计面板 | 与抖音统计页面合并，统一展示 |
| P2 | Highlights 下载 | 下载用户精选内容 |
| P3 | 多账号轮换 | 多个小号轮换使用，降低单号风险 |
| P3 | 代理支持 | 配置 HTTP 代理，避免 IP 被封 |

---

## 十、总结

| 项目 | 说明 |
|------|------|
| **集成方式** | 并行独立模块，共用 FastAPI 和面板入口 |
| **采集工具** | Instaloader（Python API），最稳定 |
| **登录策略** | 专用小号 + session 持久化 |
| **改动范围** | 现有代码仅改 main.py 2 行 + settings.py 新增数据类 |
| **新增代码** | 约 1280 行，6 个文件 |
| **数据隔离** | 独立 JSON 文件，不影响抖音数据 |
| **面板** | 新增独立 Instagram 面板，侧边栏切换 |
| **通知** | 复用现有 Telegram 配置 |
| **定时** | 内置 asyncio 循环，与抖音监控并行运行 |
| **风险** | 低，完全不影响现有功能 |
