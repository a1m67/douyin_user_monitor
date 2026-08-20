# AI 短剧追更系统

将原有的抖音作者作品监控器升级为面向短剧的追更后台：关注多个抖音作者后，系统定时发现新作品、按 `aweme_id` 去重、识别剧名和集数、合并同剧同集的多个来源，并在首次发现新集时发送通知。

核心体验：

```text
《重生后我成了首富》
最新：第 27 集
更新时间：12 分钟前
来源：AI剧场
```

## 功能

- 保留仓库内置的抖音 Web 抓取能力，业务层通过可替换的 `DouyinProvider` 调用。
- SQLite 数据模型：`Account`、`Video`、`Show`、`Episode`、`EpisodeSource`、`Notification`；Episode 支持多季。
- 数据库级 `UNIQUE(aweme_id)`；同一短剧同一集只创建一个 Episode，多账号发布保存为多个 EpisodeSource。
- 规则解析器支持书名号、`第 27 集`、`27集`、`EP27`、`EP.27`、`Episode 27`、`27/100`、`27-100` 和中文数字。
- 三态分类：明确短剧名和集数为 `matched`；没有短剧 / 剧集信号的普通视频为 `ignored`；仅有短剧或集数线索但无法可靠归档的作品才进入 `/review`。
- 首次添加账号同步最近作品作为历史基线，默认不通知；后续新剧集才通知。
- 历史补全由独立后台 worker 连续分页，使用持久化 opaque cursor，支持暂停、继续、失败重试、服务重启恢复和重复页幂等；历史补全不发送旧集通知。
- 短剧库展示实际收录数、正片/特殊集、缺集、来源作者，可按作者、剧名和忽略状态筛选；支持设置预计总集数、永久忽略错误 Show，以及移除误归档 Episode 或单个来源。
- Telegram 和飞书 Webhook 通知；每个渠道的成功或失败都会记录，失败不会回滚 Video / Episode。
- 按账号的 `next_check_at` 错峰巡检，有限并发和指数退避避免单个账号错误影响其他账号。
- Dashboard：`/shows`、`/shows/{id}`、`/accounts`、`/videos`、`/review`、`/status`，以及 JSON 健康检查 `/health`。

## 快速部署

### Docker Compose

```bash
cp .env.example .env
cp config/douyin_web.example.yaml config/douyin_web.yaml
docker compose up -d --build
```

打开：<http://localhost:8900/shows>

查看日志：

```bash
docker compose logs -f short-drama-tracker
```

检查容器和健康状态：

```bash
docker compose ps
curl http://localhost:8900/health
```

升级：

```bash
git pull
docker compose up -d --build
```

`./data` 挂载到容器的 `/app/data`。数据库、账号、视频、短剧、剧集、通知记录和 Cookie 文件会在容器重启后保留。

### 本地运行

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m pip install pytest
cp .env.example .env
uvicorn douyin_user_monitor.main:app --host 0.0.0.0 --port 8900
```

PowerShell 下可将 `.venv/Scripts/python` 替换为 `.venv\Scripts\python.exe`。

## Cookie 配置

新系统默认读取 `.env` 中的 `DOUYIN_COOKIE_FILE=data/cookies.json`。该文件可以是：

- 浏览器导出的 JSON cookie 数组，例如 `[ {"name":"sid","value":"..."} ]`；
- 包含 `cookies`、`cookie` 或 `Cookie` 字段的 JSON 对象；
- 单行标准 `Cookie` 请求头。

也可编辑 `config/douyin_web.yaml` 的 Cookie。Cookie、Token 和 Webhook 都受 `.gitignore` 保护，不能提交。没有有效 Cookie 时，应用仍可启动并显示管理页面，但真实抖音抓取会失败并按账号退避。

## 日常使用

1. 打开 `/accounts`，添加抖音作者主页并设置检查间隔。
2. 首次“立即检查”会保存最近 `INITIAL_SYNC_LIMIT` 个作品作为历史基线，默认不发送通知。
3. 如果需要补全更早作品，在账号行点击“开始补全历史”。后台会按持久化 cursor 自动连续扫描，可随时暂停、继续或在失败后重试，关闭浏览器不影响任务。
4. 后续巡检只抓取最新一页作品，系统根据规则识别短剧和集数。
5. 无法可靠识别的作品在 `/review` 中选择已有短剧或新建短剧，再确认集数。
6. 在 `/shows` 管理短剧库：查看实际收录进度，按作者或剧名筛选，永久忽略误识别 Show，并在详情页维护预计总集数或移除错误归档。

## 配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///data/app.db` | 当前仅支持 SQLite URL。 |
| `CHECK_INTERVAL_MINUTES` | `10` | 新账号默认检查间隔，可按账号覆盖。 |
| `MAX_CONCURRENT_CHECKS` | `3` | 同时请求的账号上限。 |
| `MAX_BACKOFF_MINUTES` | `60` | 连续失败时的退避上限。 |
| `INITIAL_SYNC_LIMIT` | `20` | 首次同步最近作品数。 |
| `INCREMENTAL_FETCH_LIMIT` | `30` | 首次同步之后每次日常巡检抓取的最新作品数。 |
| `HISTORY_BACKFILL_PAGE_SIZE` | `20` | 后台历史补全每页扫描作品数。 |
| `HISTORY_BACKFILL_DELAY_MIN_SECONDS` | `3` | 历史补全页间随机延迟下限（秒）。 |
| `HISTORY_BACKFILL_DELAY_MAX_SECONDS` | `6` | 历史补全页间随机延迟上限（秒）。 |
| `MAX_CONCURRENT_HISTORY_BACKFILLS` | `1` | 同时运行的历史补全账号上限，与日常巡检并发限制分开。 |
| `NOTIFY_ON_INITIAL_SYNC` | `false` | 是否为历史基线发送通知。 |
| `AUTO_ACCEPT_CONFIDENCE` | `0.8` | 自动归档最低解析置信度。 |
| `LLM_ENABLED` | `false` | 是否启用 OpenAI-compatible AI fallback。 |
| `LLM_API_KEY` | 空 | LLM API key；不会写入数据库或日志。 |
| `LLM_BASE_URL` | 空 | OpenAI-compatible API 根地址。 |
| `LLM_MODEL` | 空 | 模型名称，不绑定具体厂商。 |
| `LLM_TIMEOUT_SECONDS` | `20` | 单次 LLM 请求超时秒数。 |
| `LLM_AUTO_ACCEPT_CONFIDENCE` | `0.90` | AI 建议自动归档最低置信度。 |
| `DOUYIN_COOKIE_FILE` | `data/cookies.json` | Cookie 文件路径。 |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | 空 | 两者都配置后启用 Telegram。 |
| `FEISHU_WEBHOOK_URL` | 空 | 配置后启用飞书通知。 |

## 数据与架构

```text
Account 1 --- * Video
Show    1 --- * Episode
Episode 1 --- * EpisodeSource --- 1 Video
Episode 1 --- * Notification
```

- `Account` 是抖音作者，保存启用状态、单账号检查间隔、最近检查、错误和退避状态。
- `Account` 还保存历史补全状态、opaque cursor、已访问 cursor、扫描页数、扫描数量、新增数量及失败恢复时间点。服务重启会从已保存 cursor 自动恢复未完成任务。
- `Video` 以 `aweme_id` 唯一保存原始作品和解析结果。
- `Show` 表示一部短剧，`normalized_title` 和 aliases 用于匹配；可保存人工确认的预计总集数和可恢复的永久忽略状态。
- `Episode` 以 `(show_id, season_number, episode_number)` 唯一表示某一季某一集；`EpisodeSource` 记录不同账号的同集来源。旧数据升级时自动归入第一季。
- `Notification` 记录每次渠道发送的结果。

已有 `data/monitor_users.json` 会在新 SQLite 数据库首次创建时自动迁移账号及已下载 aweme ID 基线，避免升级后重复解析和通知旧作品。SQLite 会原地增量迁移历史补全、Episode 0、多季、LLM 解析证据和短剧库管理字段，不需要删除数据库。

## Provider 和解析器

`BuiltinDouyinProvider` 包装现有进程内 crawler。现有 `get_latest_videos()` 仍负责日常最新一页；新增 `get_video_page()` 只为用户主动历史补全提供 cursor 分页。以后可新增 `PlaywrightDouyinProvider`、`ApiDouyinProvider` 或第三方 Provider，而不用更改短剧业务服务。

`EpisodeParser` 先运行 `RegexParser`；支持“第二季 / 第2季 / S2 / Season 2”以及 `S2E12` 等组合格式。仅在规则结果需要审核或置信度不足等受控条件下，才调用 OpenAI-compatible LLM fallback。高置信度且能匹配已有 Show 的完整集数建议可自动归档，其余进入人工审核。当前不会下载视频、抽帧、OCR、Whisper 或绕过验证码。

## 测试

```bash
.venv/Scripts/python -m unittest discover -s tests -v
```

测试覆盖规则解析、中文数字、`aweme_id` 去重、同剧同集多来源、首次同步、审核、通知失败、Dashboard API、错峰并发、退避和旧 JSON 迁移。

## 抖音限制与排障

抖音页面、接口、Cookie 和风控规则会变化，真实抓取可能需要维护 Provider。系统不会实现验证码识别、验证码破解、登录绕过或反风控绕过。

常见检查项：

- Cookie 失效、403、429、登录要求或页面异常会写入账号 `last_error`，并按 10、20、40、60 分钟等指数退避。
- `/status` 可查看最近错误和待审核数量；`/health` 用于容器健康检查。
- 使用无 Cookie 或失效 Cookie 的开发环境时，业务链路应通过 `FakeDouyinProvider` 测试；不要把真实抓取失败表述为测试通过。
