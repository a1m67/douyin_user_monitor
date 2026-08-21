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
- 账号头像会随同步刷新；短剧封面从已收录剧集来源自动派生，短剧卡片和详情页可直接继续观看当前季下一条已收录正片，并自动跳过缺集。
- Telegram 和飞书 Webhook 通知；每个渠道的成功或失败都会记录，失败不会回滚 Video / Episode。
- 通知先写入持久化 outbox，再由后台 worker 投递；同一剧集、事件和渠道只创建一个任务，失败按指数退避重试，服务重启可恢复超时任务。
- 按账号的 `next_check_at` 错峰巡检，有限并发和指数退避避免单个账号错误影响其他账号。
- 可选自适应调度只在成功巡检后根据近期更新节奏和静默时长放宽间隔；每个账号可选择跟随系统、固定或自适应，并可覆盖自适应最短/最长间隔，失败继续使用独立指数退避。
- Dashboard：`/shows`、`/shows/{id}`、`/accounts`、`/videos`、`/review`、`/status`，以及 JSON 健康检查 `/health`。
- Web 层按 API 模型/序列化、页面资源和浏览器功能域拆分；静态模块保持无构建步骤，便于 VPS 直接部署和调试。
- `/version` 返回非敏感应用版本和资源 build ID；HTML 与 service worker 自动引用带内容版本的静态资源，解决 PWA 升级时新旧 JS 混用。
- 作品 API 使用分页返回，并可组合作者、短剧、分类状态、解析方式、内容类型、关键词和发布日期筛选。
- 人工审核可选择把候选剧名学习为 Show alias；冲突会明确拒绝，已有 alias 由规则层优先命中以减少 LLM 调用。

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

### 生产部署安全

Compose 默认只将服务绑定到 `127.0.0.1:8900`。不要将 8900 端口直接暴露到公网。推荐部署链路：

```text
Internet
  -> Cloudflare Access / Nginx / Caddy
  -> HTTPS + Access policy or Basic Auth
  -> 127.0.0.1:8900
```

面向公网浏览器使用时，可启用内置单用户登录：设置 `APP_AUTH_ENABLED=true`、非空 `APP_AUTH_PASSWORD` 和至少 32 字节的 `APP_SESSION_SECRET`。浏览器使用签名、限时、HttpOnly Session Cookie；修改请求由前端自动携带 session-bound CSRF token。HTTPS 反代下 `APP_COOKIE_SECURE=auto` 会设置 Secure Cookie。`ADMIN_API_TOKEN` 继续用于 curl、脚本和自动化 Bearer 调用，并可绕过浏览器 Session/CSRF；所有 Secret 均不得写入源码或日志。

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
| `ADMIN_API_TOKEN` | 空 | 可选；保护修改型短剧 API。空值保持现有兼容行为。 |
| `APP_AUTH_ENABLED` | `false` | 启用单用户 Dashboard 登录和全部短剧 API 认证。 |
| `APP_AUTH_PASSWORD` | 空 | 单用户登录密码；只从运行环境读取。 |
| `APP_SESSION_SECRET` | 空 | Session HMAC 密钥；启用认证时至少 32 字节。 |
| `APP_SESSION_TTL_HOURS` | `168` | 登录 Session 有效小时数。 |
| `APP_COOKIE_SECURE` | `auto` | `auto` 根据 HTTPS/反代协议设置 Secure，也可显式设为 true/false。 |
| `LEGACY_MONITOR_ENABLED` | `false` | 仅迁移兼容时启动旧 JSON Monitor；新 SQLite 短剧调度器始终启用。 |
| `SCAN_RUN_RETENTION_DAYS` | `30` | 巡检历史保留天数；启动时清理过期记录。 |
| `BACKUP_RETENTION_COUNT` | `14` | `data/backups/app-*.db` 在线备份保留数量。 |
| `AUTO_MAINTENANCE_ENABLED` | `true` | 启用自动备份和轻量 SQLite 维护 worker。 |
| `AUTO_BACKUP_INTERVAL_HOURS` | `24` | 自动在线备份间隔。 |
| `MAINTENANCE_POLL_SECONDS` | `300` | 维护任务到期检查间隔。 |
| `WAL_CHECKPOINT_INTERVAL_HOURS` | `6` | PASSIVE WAL checkpoint 最小间隔。 |
| `RAW_JSON_PRUNE_BATCH_SIZE` | `500` | 每轮维护最多压缩的历史 Douyin raw payload 数量。 |
| `MEDIA_CACHE_ENABLED` | `true` | 通过受保护的实体 ID 路由缓存作者头像和作品/短剧封面；不缓存视频。 |
| `MEDIA_CACHE_DIR` | `data/media-cache` | 图片缓存目录。 |
| `MEDIA_CACHE_MAX_MB` | `512` | 维护 Worker 按最近访问时间淘汰后的缓存总量上限。 |
| `MEDIA_CACHE_TTL_HOURS` | `168` | 图片刷新周期；刷新失败时继续提供已有旧缓存。 |
| `MEDIA_CACHE_TIMEOUT_SECONDS` | `10` | 单次远程图片请求超时。 |
| `MEDIA_CACHE_MAX_FILE_MB` | `5` | 单个头像或封面的最大下载大小。 |
| `OCR_ENABLED` | `false` | 是否为仍需审核且有封面的作品启用 OCR fallback。 |
| `OCR_TIMEOUT_SECONDS` | `15` | 单次封面 OCR 超时。 |
| `OCR_API_URL` / `OCR_API_KEY` | 空 | HTTP-compatible OCR 服务地址及可选密钥；密钥不记录。 |
| `OCR_MAX_CONCURRENT_REQUESTS` | `2` | OCR 独立并发上限。 |
| `OCR_DAILY_CALL_LIMIT` | `0` | UTC 日 OCR 调用额度；`0` 表示不限额。 |
| `AI_FAILURE_THRESHOLD` | `5` | LLM/OCR 连续失败后进入冷却的阈值。 |
| `AI_COOLDOWN_MINUTES` | `10` | AI 服务熔断后的冷却分钟数。 |
| `CHECK_INTERVAL_MINUTES` | `10` | 新账号默认检查间隔，可按账号覆盖。 |
| `MAX_CONCURRENT_CHECKS` | `3` | 同时请求的账号上限。 |
| `MAX_BACKOFF_MINUTES` | `60` | 连续失败时的退避上限。 |
| `ADAPTIVE_SCHEDULER_ENABLED` | `false` | 可选；按成功巡检历史动态放宽账号检查间隔。 |
| `ADAPTIVE_MIN_INTERVAL_MINUTES` | `5` | 自适应调度的全局最小间隔；不会突破账号手工基线去增加流量。 |
| `ADAPTIVE_MAX_INTERVAL_MINUTES` | `240` | 自适应放宽上限；若账号手工基线更慢，则保留手工基线。 |
| `CRAWLER_CIRCUIT_BREAKER_ENABLED` | `true` | 是否启用全局抖音抓取熔断。 |
| `CRAWLER_CIRCUIT_FAILURE_THRESHOLD` | `3` | 同类全局错误触发熔断所需的不同账号数。 |
| `CRAWLER_CIRCUIT_OPEN_MINUTES` | `20` | OPEN 状态的冷却分钟数。 |
| `DOUYIN_MAX_CONCURRENT_REQUESTS` | `3` | 所有 Douyin crawler 请求共享的全局并发上限。 |
| `DOUYIN_MIN_REQUEST_INTERVAL_SECONDS` | `0.5` | 所有 Douyin crawler 请求之间的最小间隔。 |
| `INITIAL_SYNC_LIMIT` | `20` | 首次同步最近作品数。 |
| `INCREMENTAL_FETCH_LIMIT` | `30` | 首次同步之后每次日常巡检抓取的最新作品数。 |
| `HISTORY_BACKFILL_PAGE_SIZE` | `20` | 后台历史补全每页扫描作品数。 |
| `HISTORY_BACKFILL_DELAY_MIN_SECONDS` | `3` | 历史补全页间随机延迟下限（秒）。 |
| `HISTORY_BACKFILL_DELAY_MAX_SECONDS` | `6` | 历史补全页间随机延迟上限（秒）。 |
| `MAX_CONCURRENT_HISTORY_BACKFILLS` | `1` | 同时运行的历史补全账号上限，与日常巡检并发限制分开。 |
| `NOTIFY_ON_INITIAL_SYNC` | `false` | 兼容保留；初始历史基线始终不发送通知。 |
| `AUTO_ACCEPT_CONFIDENCE` | `0.8` | 自动归档最低解析置信度。 |
| `LLM_ENABLED` | `false` | 是否启用 OpenAI-compatible AI fallback。 |
| `LLM_API_KEY` | 空 | LLM API key；不会写入数据库或日志。 |
| `LLM_BASE_URL` | 空 | OpenAI-compatible API 根地址。 |
| `LLM_MODEL` | 空 | 模型名称，不绑定具体厂商。 |
| `LLM_TIMEOUT_SECONDS` | `20` | 单次 LLM 请求超时秒数。 |
| `LLM_AUTO_ACCEPT_CONFIDENCE` | `0.90` | AI 建议自动归档最低置信度。 |
| `LLM_MAX_CONCURRENT_REQUESTS` | `2` | LLM 独立并发上限。 |
| `LLM_DAILY_CALL_LIMIT` | `0` | UTC 日 LLM 调用额度；`0` 表示不限额。 |
| `DOUYIN_COOKIE_FILE` | `data/cookies.json` | Cookie 文件路径。 |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | 空 | 两者都配置后启用 Telegram。 |
| `FEISHU_WEBHOOK_URL` | 空 | 配置后启用飞书通知。 |
| `NOTIFICATION_POLL_SECONDS` | `5` | 通知 outbox worker 轮询间隔。 |
| `NOTIFICATION_MAX_ATTEMPTS` | `8` | 单个通知任务最大投递次数。 |
| `NOTIFICATION_MAX_BACKOFF_SECONDS` | `3600` | 通知失败退避上限。 |
| `NOTIFICATION_CLAIM_TIMEOUT_SECONDS` | `300` | processing 任务被视为失联并重新领取的秒数。 |

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
- `ScanRun` 记录 scheduler、manual、initial_sync 和 history 巡检结果，账号 API 返回最近 20 条，状态 API 汇总最近 24 小时。

已有 `data/monitor_users.json` 会在新 SQLite 数据库首次创建时自动迁移账号及已下载 aweme ID 基线，避免升级后重复解析和通知旧作品。SQLite 会原地增量迁移历史补全、Episode 0、多季、LLM 解析证据和短剧库管理字段，不需要删除数据库。

旧 `/api/monitor` 路由通过独立的惰性兼容子应用保留：默认启动不会导入旧 crawler、JSON storage 或 notifier，只有实际访问兼容路由时才加载。旧 Monitor 的后台循环仍只有显式设置 `LEGACY_MONITOR_ENABLED=true` 才会启动；生产追更默认只运行新 SQLite 短剧 scheduler。

## Provider 和解析器

`BuiltinDouyinProvider` 包装现有进程内 crawler。现有 `get_latest_videos()` 仍负责日常最新一页；新增 `get_video_page()` 只为用户主动历史补全提供 cursor 分页。以后可新增 `PlaywrightDouyinProvider`、`ApiDouyinProvider` 或第三方 Provider，而不用更改短剧业务服务。

`EpisodeParser` 先运行 `RegexParser`；支持“第二季 / 第2季 / S2 / Season 2”以及 `S2E12` 等组合格式。仅在规则结果需要审核或置信度不足等受控条件下，才调用 OpenAI-compatible LLM fallback。若仍需审核且存在封面，可选 OCR backend 只识别封面并把文本重新送入现有 parser；结果会缓存，不下载完整视频、不抽帧、不做 Whisper/ASR。

每条已处理作品会记录人工维护的 `PARSER_VERSION`、稳定输入 SHA256 和处理时 build SHA。`/videos` 可使用 `parser_outdated=true` 筛选旧版本结果；账号页的“旧 Parser”批量重新解析仅处理 ignored/review，历史 matched 记录不会被自动改写。

Parser 规则回归使用提交到仓库的离线 golden corpus。运行 `python -m douyin_user_monitor parser-eval` 查看精确匹配结果，或添加 `--json` 输出 CI 可读报告；该命令不构造 LLM/OCR backend，也不会访问网络。

## 全局搜索与短剧分页

顶部搜索入口或 `Ctrl+K` / `Cmd+K` 可统一搜索短剧标题与别名、作者昵称、作品标题/描述、解析剧名和 `aweme_id`；移动端底部导航也提供搜索按钮。搜索结果只返回展示所需字段，不返回 `raw_json`、LLM 原始响应或运行配置。短剧列表和“我的追更”使用 `page/page_size` 分页，旧调用仍可暂时使用 `limit`。

SQLite 支持 FTS5 时，schema v25 会建立由触发器同步的三个轻量搜索索引；不支持 FTS5 时应用继续启动并自动使用 LIKE fallback。需要人工重建或核对索引时运行：

```bash
python -m douyin_user_monitor search-rebuild
```

## 测试

生产维护命令使用 SQLite 在线备份 API，`doctor` 默认只读；schema 升级前也会自动在数据库同级 `backups/` 创建快照：

```bash
python -m douyin_user_monitor backup
python -m douyin_user_monitor backup-verify
python -m douyin_user_monitor backup-verify --file data/backups/app-YYYYMMDD-HHMMSS.db
python -m douyin_user_monitor doctor
python -m douyin_user_monitor doctor --repair
python -m douyin_user_monitor db-stats
python -m douyin_user_monitor db-stats --checkpoint
python -m douyin_user_monitor search-rebuild
```

`db-stats` 只输出数据库/WAL 大小、页统计、业务表行数和索引定义，不读取用户数据内容。`--checkpoint` 执行显式 TRUNCATE checkpoint；后台维护只使用低频 PASSIVE checkpoint。系统不会自动频繁 `VACUUM`，也不会自动删除更新动态。

每份新备份旁边会生成同名 `.json` manifest，记录文件名、UTC 创建时间、大小、SHA256 和 schema 版本；旧备份即使没有 manifest，`backup-verify` 仍会检查 SQLite 完整性、外键和 schema。恢复只用于事故处理，正常升级不要执行 restore。恢复前必须停止应用，并先 dry-run：

```bash
docker compose stop
python -m douyin_user_monitor restore --from data/backups/app-YYYYMMDD-HHMMSS.db --dry-run
python -m douyin_user_monitor restore --from data/backups/app-YYYYMMDD-HHMMSS.db --yes
docker compose up -d
```

真实恢复必须显式提供 `--yes`。命令会拒绝高于当前程序版本的 schema 和无法取得独占写锁的数据库，先备份当前库，再使用 fsync 临时文件原子替换；恢复后校验失败会自动回滚到恢复前备份。

默认启用轻量维护 worker：每 24 小时按 SQLite online backup API 创建一次备份、保留最近 14 份，按既有保留期清理巡检运行记录，并以 PASSIVE 模式低频回收 WAL frame。它不会自动 `VACUUM`，也不会删除 Episode、UpdateEvent 或用户数据。

诊断页只读取文件/WAL 大小、轻量 `SELECT 1` 延迟、持久化的最近 doctor/备份/维护时间、worker 状态和队列计数；不会在 GET 请求中执行 `integrity_check` 或 `foreign_key_check`。完整检查只由 `POST /api/short-drama/diagnostics/doctor` 或上述 CLI 命令触发。

```bash
.venv/Scripts/python -m unittest discover -s tests -v
```

测试覆盖规则解析、中文数字、`aweme_id` 去重、同剧同集多来源、首次同步、审核、通知失败、Dashboard API、错峰并发、退避和旧 JSON 迁移。

## 抖音限制与排障

抖音页面、接口、Cookie 和风控规则会变化，真实抓取可能需要维护 Provider。系统不会实现验证码识别、验证码破解、登录绕过或反风控绕过。

常见检查项：

- Cookie 失效、403、429、登录要求或页面异常会写入账号 `last_error`，并按 10、20、40、60 分钟等指数退避。短时间内三个不同账号出现同类全局错误时，crawler 会全局退避；冷却后只放行一个 HALF_OPEN 探针。
- `/status` 可查看最近错误和待审核数量；`/health` 用于容器健康检查。
- 使用无 Cookie 或失效 Cookie 的开发环境时，业务链路应通过 `FakeDouyinProvider` 测试；不要把真实抓取失败表述为测试通过。
