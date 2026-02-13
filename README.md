# Stay Ledger

一个简洁的 BS 架构记账系统，后端 `Flask + SQLite`，支持桌面和手机访问，支持 Telegram Bot 记账。

## 功能清单

- 密码登录（默认密码 `P@ssw0rd`）
- 账单记录新增、删除、修改
- 首页展示总收入、总支出、结余
- 首页账单只显示最近 100 条（固定高度滚动）
- 月度统计页（按月份查看）
- 月度收入/支出/结余汇总
- 分类支出占比、分类收入占比
- 当月每笔支出详情、当月每笔收入详情
- Telegram Bot 自动记账（后台轮询）

## 技术栈

- 后端：Flask
- 数据库：SQLite
- 前端：Jinja2 + 原生 CSS（响应式）
- 部署：Docker + Docker Compose

## 快速开始

### 1. 启动

```bash
docker compose up -d --build
```

访问地址：

- `http://localhost:8000`

默认登录密码：

- `P@ssw0rd`

### 2. 停止

```bash
docker compose down
```

## 配置说明

`docker-compose.yml` 中可配置：

- `SECRET_KEY`：Flask Session 密钥，建议改成随机值
- `APP_PASSWORD`：网页登录明文密码
- `APP_PASSWORD_HASH`：网页登录哈希密码（优先级高于 `APP_PASSWORD`）

生成密码哈希：

```bash
docker compose run --rm ledger flask hash-password
```

## Telegram Bot 记账

在网页 `配置` 页面设置：

- 启用 Telegram Bot 自动记账
- Bot Token
- 允许的 Chat ID（可选，建议配置）

支持命令：

- `/expense 32.5 餐饮 午饭`
- `/income 12000 工资 2月工资`
- `/help`
- `/myid`（返回当前 chat id）

说明：

- 分类必须使用系统内置分类
- 未授权 chat（当你配置了允许的 Chat ID）会被拒绝

## 页面说明

- `/` 首页：新增记录 + 最近 100 条账单
- `/monthly-report` 月度统计页：月份筛选、占比统计、当月明细
- `/settings` 配置页：Telegram 配置

## 数据持久化

- SQLite 文件位置：`data/ledger.db`
- `docker-compose.yml` 已将 `./data` 挂载到容器 `/app/data`

## 项目结构

```text
.
├─ app.py
├─ Dockerfile
├─ docker-compose.yml
├─ requirements.txt
├─ static/
│  └─ style.css
├─ templates/
│  ├─ base.html
│  ├─ login.html
│  ├─ index.html
│  ├─ monthly_report.html
│  ├─ settings.html
│  └─ edit_transaction.html
└─ data/
```

## 常见问题

1. `docker compose up` 拉取镜像超时
   - 可能是网络访问 Docker Hub 失败，可更换镜像源后重试
2. Telegram 没有自动记账
   - 检查配置页是否启用
   - 检查 Bot Token 是否正确
   - 检查 Chat ID 白名单是否匹配当前会话
