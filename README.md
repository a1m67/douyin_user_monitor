# douyin_user_monitor

抖音用户作品监控服务（监控调度 + 进程内抖音 Web 爬虫）。

- 本项目负责：监控用户管理、轮询调度、下载落盘、监控面板
- 抖音接口解析：进程内 vendored `crawlers/`（源自 `Douyin_TikTok_Download_API` 精简子集），不再依赖独立 8899 上游进程

## 启动

1. 进入目录
```bash
cd /root/douyin_user_monitor
```

2. 安装依赖（建议 venv）
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3. 配置
```bash
cp config.example.yaml config.yaml
```

- `crawler.config_path`: 独立抖音爬虫配置（Cookie/headers），默认 `config/douyin_web.yaml`
- `crawler.timeout_seconds`: 头像等直链下载超时
- `monitor.state_path`: 本地监控状态文件
- `monitor.download_root`: 本地下载目录
- `notifications.telegram`: 新作品 Telegram 通知（支持发现新作品 + 下载完成两条消息）

Cookie 配置：

```bash
cp config/douyin_web.example.yaml config/douyin_web.yaml
# 编辑 config/douyin_web.yaml，填入浏览器获取的抖音 Cookie
```

4. 启动
```bash
uvicorn douyin_user_monitor.main:app --host 0.0.0.0 --port 8900
```

## 历史回填

- 日常巡检默认只检查最新 20 个作品。
- 新用户会自动进入“历史回填”状态：每次同步额外补一页历史作品，每页 50 个。
- 历史回填不会对旧作品逐条发送 Telegram“发现新作品/下载完成”通知，只更新本地状态与下载记录。
- 可通过以下接口手动控制：
  - `POST /api/monitor/users/{user_id}/backfill/start`
  - `POST /api/monitor/users/{user_id}/backfill/pause`
  - `POST /api/monitor/users/{user_id}/backfill/resume`
  - `POST /api/monitor/users/{user_id}/backfill/run_once`

## 访问

- 监控管理页：`/api/monitor/dashboard`
- API 文档：`/docs`
