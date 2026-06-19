# 增强用户注销/封号检测功能

## Goal

增强抖音用户监控系统中账号状态检测（注销/封号）的覆盖范围和健壮性，减少漏检和无效 API 调用，并在状态变更时通知用户。

## What I already know

### 当前实现

- 检测逻辑集中在 `profile_parser.py:57-66` 的 `extract_account_status()`
- 注销检测：检查 `user.user_deleted == True` 或文本含"注销"
- 封号检测：仅匹配 `special_state_info.title` 的 4 个固定字符串：`{"账号已封禁", "该账号已封禁", "账号封禁", "已封禁"}`
- 三种状态：`normal` / `deleted` / `banned`

### 已识别的问题

1. **封号检测覆盖不全**：`status_msg` 和 `special_state_info.content` 未被检查；不同违规类型可能用不同文案
2. **无状态变更通知**：`notifier.py` 只有新作品和下载完成通知，缺少账号状态变更告警
3. **已异常用户仍拉取作品**：`user_sync._sync_user_latest()` 不检查 `account_status`，注销/封号用户仍会调用 `fetch_user_post_videos`
4. **status_msg 误判风险**：当前只检查 title 恰好避开了误判，但扩展时需注意

### 关键文件

- `douyin_user_monitor/monitor/profile_parser.py` — 状态检测核心
- `douyin_user_monitor/monitor/user_sync.py` — 同步流程
- `douyin_user_monitor/monitor/service.py` — 服务层
- `douyin_user_monitor/monitor/notifier.py` — 通知协议
- `douyin_user_monitor/monitor/telegram_notifier.py` — Telegram 通知实现
- `tests/test_user_account_status.py` — 现有测试

### 现有测试（7 个，全部通过）

- 注销优先于封号判定
- 封号关键词精确匹配
- 不误判含"封禁"的普通文本
- 同步流程正确写入注销状态
- 已注销用户不重复请求资料
- 资料补全失败记录错误

## Assumptions (temporary)

- 抖音 API 返回的封号/注销状态信息格式相对稳定
- 用户希望在状态变更时收到通知（需确认）
- 已异常用户应跳过作品拉取以节省 API 调用（需确认）

## Open Questions

- (待 brainstorm 流程填充)

## Requirements (evolving)

### R1: 扩展封号检测覆盖
- 将 `status_msg` 字段纳入封号检测
- 将 `special_state_info.content` 纳入检测
- 使用白名单精确匹配策略：维护明确的封禁关键词集合
- 现有 4 个 title 关键词保留：`{"账号已封禁", "该账号已封禁", "账号封禁", "已封禁"}`
- 新增 status_msg/content 关键词（需在实现时调研补充）
- 保持注销检测优先于封号检测的现有优先级
- 检查顺序：title -> status_msg -> content，命中即返回

### R2: 跳过已异常用户的无效拉取
- 在 `user_sync._sync_user_latest()` 中检查 `account_status`
- 当状态为 `deleted` 或 `banned` 时，跳过 `fetch_user_post_videos` 调用
- 仍需更新 `last_checked_at` 和资料快照（保持状态同步）

### R3: 状态变更通知（仅 normal -> abnormal）
- 扩展 `MonitorNotifierProtocol`，新增 `notify_account_status_changed` 方法
- 当用户状态从 `normal` 变为 `deleted` 或 `banned` 时触发通知
- `TelegramNotifier` 实现该方法，发送包含用户昵称、旧状态、新状态、原因的消息
- 仅在 `old_status == normal` 且 `new_status in (deleted, banned)` 时通知
- 其他方向的变化（abnormal->normal、deleted<->banned）不通知

## Acceptance Criteria (evolving)

- [ ] 封号检测能识别 `status_msg` 中的白名单封禁文案
- [ ] 封号检测能识别 `special_state_info.content` 中的白名单封禁文案
- [ ] 含"封禁"但非封号的普通文本仍不被误判（如"封禁申诉说明"）
- [ ] 检查顺序 title -> status_msg -> content，命中即返回，不重复检查
- [ ] 已注销/封禁用户同步时跳过作品拉取，但仍更新 checked_at 和资料快照
- [ ] 状态从 normal 变为 deleted/banned 时发送 Telegram 通知
- [ ] 其他方向的状态变化不发送通知
- [ ] 现有 7 个测试继续通过
- [ ] 新增测试覆盖所有新场景

## Decision (ADR-lite)

**Context**: 封号检测需要扩展到 status_msg 和 content 字段，但这些字段可能包含"封禁"相关的非封号文案（如申诉说明）。

**Decision**: 采用白名单精确匹配策略，维护明确的封禁关键词集合。检查顺序为 title -> status_msg -> content，命中即返回。

**Consequences**: 安全性高，不会误判；但抖音新增封禁文案时需要手动更新白名单。

## Technical Approach

### 改动文件

1. `profile_parser.py` — 扩展 `_detect_banned_reason()` 函数，新增 status_msg 和 content 检查
2. `notifier.py` — `MonitorNotifierProtocol` 新增 `notify_account_status_changed` 方法
3. `telegram_notifier.py` — 实现 `notify_account_status_changed`，构建状态变更消息
4. `user_sync.py` — `_sync_user_with_summary()` 中：检测状态变更并触发通知；`_sync_user_latest()` 中：跳过已异常用户
5. `tests/test_user_account_status.py` — 新增测试用例

### 实现要点

- `_detect_banned_reason()` 返回值从单一 title 检查扩展为 title/status_msg/content 三段检查
- 状态变更通知在 `_sync_user_with_summary()` 中触发，需要在更新 user 字段前保存旧状态
- `_sync_user_latest()` 在入口处检查 `account_status`，非 normal 时直接返回 0

## Definition of Done

- 测试添加/更新（单元测试覆盖新场景）
- 现有 7 个测试继续通过
- 代码风格与现有代码一致

## Out of Scope (explicit)

- 账号状态变更历史追踪
- 用户手动标记/覆盖自动检测结果
- 状态解封后的自动恢复监控
- 新增更多账号状态类型（限时封禁等）
- API 层暴露状态变更历史

## Technical Notes

- 项目使用 FastAPI + httpx + asyncio
- 测试框架：unittest（非 pytest）
- 依赖通过 venv 管理
