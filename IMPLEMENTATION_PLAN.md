# AI 短剧追更系统实施计划

## 当前架构审查

现有项目是一个 FastAPI 服务，主要组成如下：

- `douyin_user_monitor/main.py` 注册旧的 `/api/monitor` 路由并在启动时恢复监控循环。
- `douyin_user_monitor/crawler/inprocess_client.py` 将仓库内置的 `crawlers/douyin/web` Web crawler 包装为进程内客户端。
- `douyin_user_monitor/monitor/service.py` 提供 interval / coverage 两种轮询调度模式、运行状态恢复和 Cookie 测活集成。
- `douyin_user_monitor/monitor/user_sync.py` 获取最新作品、以 JSON 中的 `downloaded_aweme_ids` 去重、按需拉取作品详情、下载资源，并触发旧通知。
- `douyin_user_monitor/monitor/storage.py` 使用 `data/monitor_users.json` 保存账号、下载记录和监控状态。
- `douyin_user_monitor/monitor/telegram_notifier.py` 提供旧的“发现新作品”和“下载完成”Telegram 消息。
- `douyin_user_monitor/web/` 是静态 HTML 监控和统计页面。

可直接复用的部分是内置抓取器、`InProcessDouyinClient`、账号资料解析、下载器、Cookie 测活和旧接口。短剧业务不会直接访问 crawler；它会经过新的 Provider 边界。旧接口会保留，已有 JSON 数据将迁移到 SQLite，已有的 aweme ID 会作为已处理基线导入。

## Phase 1 - 现有代码理解 + Provider 抽象

- [x] 审查抓取入口、调度、JSON 存储、通知和 Dashboard。
- [x] 定义 `DouyinProvider` 与标准化的账号 / 作品数据。
- [x] 用 `BuiltinDouyinProvider` 包装现有 `InProcessDouyinClient`。

验证：Provider 单元测试，现有 crawler 配置测试。

## Phase 2 - Video / Show / Episode 数据模型

- [ ] 引入 SQLite 数据库和可重复执行的 schema migration。
- [ ] 实现 Account、Video、Show、Episode、EpisodeSource、Notification 数据访问层。
- [ ] 迁移已有 JSON 账号及旧 `downloaded_aweme_ids` 基线，保证 `aweme_id` 唯一。

验证：数据库唯一约束、同剧同集来源去重和迁移测试。

## Phase 3 - EpisodeParser

- [ ] 定义 `EpisodeParserBackend` 扩展点。
- [ ] 实现仅规则驱动的 `RegexParser` 与置信度评分。
- [ ] 支持中文数字、常见集数格式、书名号、已知别名和 hashtag。

验证：标题解析、低置信度与人工审核分流测试。

## Phase 4 - 新作品处理 Pipeline

- [ ] 仅通过 `DouyinProvider` 拉取最近作品。
- [ ] 按 `aweme_id` 落库后再解析，重复作品不重复解析。
- [ ] 创建或关联 Show、Episode、EpisodeSource，并在事务后通知。
- [ ] 实现首次同步历史基线且默认不通知。

验证：验收场景 A-E、首次同步和重复抓取测试。

## Phase 5 - 通知系统

- [ ] 定义统一 `Notifier.send_episode_update` 接口。
- [ ] 实现 Telegram 和飞书 Webhook 通知，未配置时自动禁用。
- [ ] 持久化每个通知成功或失败结果，通知错误不回滚业务数据。

验证：通知格式、失败记录及单次新集通知测试。

## Phase 6 - Dashboard + Review

- [ ] 将首页改为“最近更新短剧”。
- [ ] 增加 `/shows/{id}`、`/accounts`、`/videos`、`/review`、`/status` 和 API。
- [ ] 支持人工确认已有 / 新建 Show 及集数。

验证：路由、页面内容和审核确认测试。

## Phase 7 - 调度、失败退避、首次同步

- [ ] 基于每个账号的 `next_check_at` 做错峰调度与有限并发。
- [ ] 实现带上限的指数退避、账号级状态和手动检查。
- [ ] 在 `/health` 暴露数据库与调度状态。

验证：到期选择、jitter、并发上限、退避和健康检查测试。

## Phase 8 - 测试、Docker、README

- [ ] 补足单元和集成测试，运行既有测试集。
- [ ] 增加 `.env.example`、Dockerfile、Compose 持久化配置和 `.gitignore` 规则。
- [ ] 更新 README 与 AGENTS.md，记录运行、部署、Cookie、通知和已知限制。

验证：测试、语法检查、应用启动和容器构建检查。
