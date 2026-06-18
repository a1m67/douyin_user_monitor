# Detect account status and add statistics dashboard controls

## Goal
为监控用户增加账号状态识别能力，优先识别“已注销”，并在统计大盘中提供可操作的禁用入口与异常账号列表，避免异常账号继续被误判为普通监控对象。

## Requirements
- 为用户资料解析增加账号状态字段，至少区分：正常、已注销、已封禁。
- 对已确认注销的账号，从上游 profile 响应中提取状态标签与原因文案并持久化到用户数据。
- 对封禁仅在出现明确封禁关键词或显式状态字段时判定，不能用模糊信号猜测。
- 现有监控/列表/统计接口需要返回账号状态数据，兼容旧数据文件。
- 统计页用户列表增加“暂停/启用”操作，复用现有后端 PATCH /users/{id} 能力。
- 用户统计增加一个“已注销/已封禁”列表，展示状态、原因、最近检查时间、禁用按钮。
- 主监控页保留现有能力，不引入自动禁用逻辑。

## Acceptance Criteria
- [ ] 新增/已有用户在获取到明确注销信号时，其状态字段被更新为 deleted，并保留 reason 文案。
- [ ] 只有明确出现封禁信号时，状态字段才会更新为 banned；未知状态保持 normal。
- [ ] 旧 state 文件加载后可正常补齐默认状态字段，不会因缺字段崩溃。
- [ ] /api/monitor/statistics 返回 deactivated users 列表及计数。
- [ ] 统计大盘用户卡片可直接暂停/启用用户，操作后页面状态刷新。
- [ ] 相关单元测试与 UI 测试通过。

## Technical Notes
### Cross-layer contract
- User persistent fields:
  - account_status: normal | deleted | banned
  - account_status_label: 人类可读标签
  - account_status_reason: 可选，来自上游 title/content/status_msg
  - account_status_updated_at: 最近一次状态刷新时间
- Statistics summary additions:
  - abnormal_users
  - deleted_users
  - banned_users
- Statistics list additions:
  - deactivated_users

### Validation / Error Matrix
- Good: profile 明确返回 user_deleted=true -> deleted
- Base: 旧用户数据无 account_status 字段 -> 自动补齐 normal
- Bad: special_state_info 存在但无法明确判定封禁/注销 -> 保持 normal，不做猜测
