# xAI Telegram Media Bot

基于 Telegram Bot API 与 xAI 官方 API 的媒体生成机器人，支持图片生成、视频生成、图生视频，以及基于 Web 配置后台的 Docker / Docker Compose 部署。

作者：by fuduxixi

## 功能特性

- 文生图
- 文生视频
- 图生视频
- 支持 `grok-imagine-image` 与 `grok-imagine-image-pro`
- 支持多 `XAI_API_KEYS` 轮询
- 视频审核拒绝后支持自动改写并重试
- 白名单用户控制
- FIFO 顺序任务队列
- Web 配置后台
- 日志查看与清理
- Bot 在线状态展示
- 保存配置后热重载 Bot
- Docker / Docker Compose 双容器部署

## 项目结构

```text
xai_telegram_media_bot/
├── telegram_xai_media_bot.py
├── web-config.py
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── .dockerignore
├── .gitignore
├── requirements-bot.txt
├── requirements-web.txt
├── LICENSE
├── README.md
└── scripts/
    └── run_bot_docker.sh
```

## 部署架构

项目使用两个容器：

### `telegram-bot`

负责运行 Telegram 机器人主进程，处理媒体生成请求、队列执行、日志输出、状态文件写入与热重载监听。

### `web-config`

负责运行 Flask Web 配置后台，提供登录认证、`.env` 配置修改、多 Key 管理、日志查看、状态展示和热重载触发能力。

两个容器通过共享卷共用以下运行时目录：

- `./logs`
- `./data`

## 环境要求

- Docker
- Docker Compose 插件（`docker compose`）
- Telegram Bot Token
- 至少一个 xAI API Key

## 快速开始

### 1. 获取项目

```bash
git clone <repo-url> xai_telegram_media_bot
cd xai_telegram_media_bot
```

### 2. 创建配置文件

```bash
cp .env.example .env
```

### 3. 填写关键配置

至少需要填写以下环境变量：

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
XAI_API_KEY=your_xai_api_key
TG_ALLOWED_USER_IDS=123456789
ADMIN_USER=admin
ADMIN_PASSWORD=change_this_password
WEB_SECRET_KEY=replace_with_a_long_random_secret
WEB_PORT=5000
```

### 4. 启动服务

```bash
docker compose up -d --build
```

### 5. 查看运行状态

```bash
docker compose ps
```

### 6. 查看日志

```bash
docker compose logs -f telegram-bot
docker compose logs -f web-config
```

## 访问 Web 配置后台

`docker-compose.yml` 默认端口映射为：

```yaml
ports:
  - "${WEB_PORT:-5000}:5000"
```

默认情况下，Web 配置后台可通过以下地址访问：

```text
http://<server-ip>:5000
```

如果 `.env` 中设置了其他 `WEB_PORT`，则以该值为准。

登录账号来自 `.env`：

- `ADMIN_USER`
- `ADMIN_PASSWORD`

## 运行时目录

### `logs/`

用于保存 Bot 日志文件：

- `logs/bot.log`

### `data/`

用于保存 Bot 运行状态与热重载信号：

- `data/bot-status.json`
- `data/reload-bot.signal`

## 核心环境变量

### Telegram

- `TELEGRAM_BOT_TOKEN`
  - Telegram 机器人 Token。

- `TG_ALLOWED_USER_IDS`
  - 允许使用机器人的 Telegram 用户 ID，多个用英文逗号分隔。

### xAI API

- `XAI_API_KEY`
  - 单个 xAI API Key。

- `XAI_API_KEYS`
  - 多个 xAI API Key，多个用英文逗号分隔。
  - 如果同时配置，程序优先使用 `XAI_API_KEYS`。

### 图片生成

- `XAI_IMAGE_MODEL`
  - 默认图片模型。
  - 可选值：`grok-imagine-image`、`grok-imagine-image-pro`

- `XAI_IMAGE_DEFAULT_RATIO`
  - 默认图片比例。

- `XAI_IMAGE_DEFAULT_N`
  - 默认图片生成数量。

- `XAI_IMAGE_MAX_N`
  - 最大图片生成数量。

### 视频改写策略

- `VIDEO_AUTO_REWRITE_ON_MODERATION`
  - 兼容旧开关：`0` 或 `1`。

- `VIDEO_REWRITE_MODE`
  - 视频审核拒绝后的自动改写模式。
  - 可选值：`off`、`mild`、`strong`

### 日志与状态文件

- `BOT_LOG_FILE`
  - Bot 日志文件路径。

- `BOT_STATUS_FILE`
  - Bot 状态文件路径。

- `BOT_RELOAD_SIGNAL_FILE`
  - Bot 热重载信号文件路径。

### Web 后台

- `ADMIN_USER`
  - Web 后台登录用户名。

- `ADMIN_PASSWORD`
  - Web 后台登录密码。

- `WEB_SECRET_KEY`
  - Flask Session 密钥。

- `WEB_CONFIG_PORT`
  - Web 容器内部监听端口，默认 `5000`。

- `WEB_PORT`
  - 宿主机映射端口。

## 常用运维命令

### 启动

```bash
docker compose up -d
```

### 重新构建并启动

```bash
docker compose up -d --build
```

### 重启服务

```bash
docker compose restart
```

### 停止服务

```bash
docker compose down
```

### 查看容器状态

```bash
docker compose ps
```

### 查看 Bot 日志

```bash
docker compose logs -f telegram-bot
```

### 查看 Web 日志

```bash
docker compose logs -f web-config
```

## 文件说明

### `telegram_xai_media_bot.py`

Telegram 机器人主程序。

### `web-config.py`

Web 配置后台程序。

### `Dockerfile`

多阶段构建文件，定义 `bot` 与 `web` 两个镜像目标。

### `docker-compose.yml`

Docker Compose 编排文件，定义双容器部署结构。

### `.env.example`

公开示例配置文件，用于生成本地 `.env`。

### `requirements-bot.txt`

Bot 容器依赖列表。

### `requirements-web.txt`

Web 容器依赖列表。

### `scripts/run_bot_docker.sh`

Bot 容器启动脚本。

## 安全说明

- 真实 `.env` 不应提交到 Git 仓库
- `logs/` 与 `data/` 属于运行时目录，不应提交到 Git 仓库
- `ADMIN_PASSWORD` 必须修改默认值
- `WEB_SECRET_KEY` 应使用高强度随机字符串
- 建议配置 `TG_ALLOWED_USER_IDS` 限制可访问用户范围

## 许可证

项目许可证见 [LICENSE](./LICENSE)
