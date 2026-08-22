# pixel-gemini

**Pixel 10 Pro Google One Gemini Offer Bot —— Web 界面 + Telegram 机器人**

模拟一台 Google Pixel 10 Pro（Android 16）设备，登录用户提供的 Gmail
账号，在 Google One 中检索 **12 个月免费 Gemini Pro** 激活链接。

> ✅ 容器化运行（Docker），提供 **Web 管理界面**，默认管理员账号密码：
> **`admin` / `admin`**
> ✅ 原 Telegram 机器人接口保留，可通过环境变量选择性启用

---

## 目录

- [功能特性](#功能特性)
- [快速开始（Docker 容器运行）](#快速开始docker-容器运行)
- [Web 界面使用说明](#web-界面使用说明)
- [环境变量说明](#环境变量说明)
- [本地开发（不依赖 Docker）](#本地开发不依赖-docker)
- [Telegram 机器人（可选）](#telegram-机器人可选)
- [项目结构](#项目结构)
- [常见问题 FAQ](#常见问题-faq)
- [风控与免责声明](#风控与免责声明)

---

## 功能特性

| 功能 | 说明 |
|---|---|
| 📱 设备模拟 | Pixel 10 Pro（Android 16）移动端 UA、触摸/语言/时区一致化、反自动化指纹补丁（WebGL/Canvas/Client-Hints） |
| 🆔 唯一标识 | 每会话生成**永不重复**的 IMEI（Luhn 合法）、Android ID、Chrome 补丁号（密码学安全随机源） |
| 🌐 Web 界面 | 管理员登录、Gmail 账号管理、一键运行检测、实时进度、运行历史、日志查看 |
| 🔐 登录自动化 | Selenium 无头 Chrome 登录 Google 账号，识别并报告风控/验证码/2FA 拦截原因 |
| 💳 Offer 检测 | 扫描 Google One 中 Gemini Pro 12 个月免费套餐，提取激活链接 |
| 🐳 容器化 | 一键 `docker compose up`，数据持久化到卷，内置健康检查 |
| 🤖 Telegram | 可选启用原 `/login`、`/check_offer`、`/get_link`、`/status` 机器人 |

---

## 快速开始（Docker 容器运行，从拉取代码到全部启动）

### 1. 前置要求

- 已安装 **Docker** 与 **Docker Compose v2**（`docker compose version` 可验证）
- 端口 `8910` 空闲
- 网络可访问 dl.google.com（首次构建会下载 Google Chrome）

### 2. 拉取代码

```bash
git clone https://github.com/yhw5231/pixel-gemini.git
cd pixel-gemini
```

### 3. 构建并启动（首次约 3–8 分钟）

```bash
docker compose up -d --build
```

构建过程中自动完成：

| 步骤 | 说明 |
|---|---|
| Python 3.12 基础镜像 | 拉取 `python:3.12-slim-bookworm` |
| 安装 Google Chrome Stable | 下载 .deb 并安装 Chrome 137+（与模拟 UA 同代） |
| 安装 Python 依赖 | `pip install -r requirements.txt`（含 selenium / flask 等） |
| **下载 chromedriver** | 通过 Selenium Manager 自动下载**与 Chrome 匹配的 chromedriver** |
| **内置 chromedriver** | 复制到 `/usr/local/bin/chromedriver`，**无需手动安装** |
| **冒烟验证** | 构建期自动启动一次无头 Chrome，确保浏览器与驱动都能正常工作 |
| 复制应用代码 | 源码、模板、静态文件 |

> 构建只需一次，后续启动直接 `docker compose up -d`（无 `--build`）即用缓存。

### 4. 验证部署

```bash
docker compose ps            # STATUS 应为 healthy（健康检查访问 /healthz）
```

浏览器打开 **http://localhost:8910**，使用默认管理员账号登录：

| 字段 | 值 |
|---|---|
| 用户名 | `admin` |
| 密码 | `admin` |

> ⚠️ **安全提示**：默认账号密码仅用于本地/内网测试。对外暴露前请务必
> 修改密码（环境变量 `ADMIN_PASSWORD`）并设置固定 `SECRET_KEY`。

### 5. chromedriver 说明（容器运行时）

需要 chromedriver 吗？——**需要**。Selenium 驱动 Chrome 必须要有与 Chrome 版本匹配的 chromedriver。

**不需要自己安装，且"没匹配到会自动安装"**：

- 默认（`CHROMEDRIVER_PATH` 留空）：**Selenium Manager**（Selenium 4.6+ 内置的驱动管理器）
  **自动检测**容器内 Chrome 的版本 → 缓存里有**匹配版本**就直接用 → **没有匹配版本就自动联网下载并安装**。
  所以即使 Chrome 升级导致旧驱动失配，也无需任何人工干预。
- 镜像构建期已经通过 Selenium Manager 预先下载过一份**与当时 Chrome 匹配**的
  chromedriver 并做了无头启动冒烟验证，所以**容器离线也能跑**（缓存已在镜像里）。
- 若你显式设置 `CHROMEDRIVER_PATH`：代码会先校验该驱动的版本——
  **与 Chrome 版本不匹配时自动回退到 Selenium Manager 自动安装**；启动失败的极端情况也会自动回退重试。
- 本地开发同理：本机装好 Chrome 后 `CHROMEDRIVER_PATH` 留空即可，Selenium Manager 自动搞定。

### 6. 升级到新版本

```bash
git pull                          # 拉取最新代码
docker compose up -d --build      # 重建镜像并重启（保留数据卷）
```

### 7. 停止 / 运维

```bash
docker compose down          # 停止容器（保留数据卷与镜像）
docker compose logs -f       # 查看实时日志
docker compose down -v       # 停止并删除数据卷（不可恢复！）
```

数据（SQLite 数据库）保存在 Docker 数据卷 `pixel-gemini-data` 中，
删除容器不会丢失；如需彻底清理：`docker compose down -v`。

---

## Web 界面使用说明

界面包含 5 个页面，顶部导航栏切换：

### 1. 登录（/login）

输入管理员用户名密码（默认 `admin` / `admin`）。登录状态保存在会话
Cookie 中，所有页面均需要登录后才能访问。

### 2. 仪表盘（/）

- 运行统计：完成数、错误数、进行中数
- 最近 10 次运行记录（点击编号可查看详情）

### 3. 账号管理（/accounts）

- **添加账号**：填写 Gmail 邮箱、密码、备注，点击「Add account」
- **列表**：显示已保存账号，每个账号有操作按钮
  - 「▶ Check offer」：为该账号启动一次 Gemini Offer 检测
  - 「Delete」：删除账号（会二次确认）
- 凭据保存于本地 SQLite（容器内 `/data/pixel_gemini.db`），
  请自行保证运行环境安全

### 4. 运行详情（/run/<id>）

点击任一运行的编号进入详情页：

- 实时状态：`queued`（排队）→ `running`（运行中）→ `done`（完成）/
  `error`（失败），页面每 2 秒自动刷新
- 检测完成后显示 **Offer 激活链接**（或失败原因）
- 显示本次生成的设备摘要（型号、Android 版本、IMEI、会话号）

### 5. 运行历史（/runs）

最近 100 次运行的完整列表：账号、状态、结果链接、开始/结束时间。

### 6. 日志（/logs）

最近 2000 行应用日志（内存环形缓冲），用于排查自动化问题。

### 典型使用流程

```
1. 浏览器打开 http://localhost:8910，用 admin/admin 登录
2. 进入「Accounts」→ 添加你的 Gmail 账号（邮箱 + 密码）
3. 点击该账号的「▶ Check offer」
4. 自动跳转到运行详情页，等待进度（约 30~60 秒）
5. 完成后复制页面上的 Gemini Pro 激活链接（或查看失败原因）
```

---

## 环境变量说明

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ADMIN_USERNAME` | `admin` | Web 界面管理员用户名 |
| `ADMIN_PASSWORD` | `admin` | Web 界面管理员密码 |
| `SECRET_KEY` | 随机生成 | Flask 会话签名密钥（生产环境务必固定） |
| `WEB_HOST` | `0.0.0.0` | Web 服务监听地址 |
| `WEB_PORT` | `8910` | Web 服务端口 |
| `DATA_DIR` | `/data` | SQLite 数据库与数据文件目录 |
| `TELEGRAM_BOT_TOKEN` | 空 | 设置后同时启动 Telegram 机器人 |
| `CHROMEDRIVER_PATH` | 空（推荐） | chromedriver 路径；默认由 Selenium Manager 自动匹配 Chrome 版本，**不匹配/缺失时自动联网安装**；也可显式指定（例：`/usr/local/bin/chromedriver`，镜像内置的离线兜底） |
| `PROXY_URL` | 空 | 出口代理（强烈建议），如 `http://user:pass@host:port` |
| `DEVICE_TIMEZONE` | `America/Los_Angeles` | 模拟设备时区（应与代理地区一致） |
| `DEVICE_LANGUAGE` | `en-US` | 模拟设备语言 |
| `DEVICE_PLATFORM` | `Android` | Client-Hints 平台字段 |
| `DEVICE_VIEWPORT_WIDTH` | `427` | CSS 视口宽（Pixel 10 Pro 1280×2856 @3.0 的真实比例） |
| `DEVICE_VIEWPORT_HEIGHT` | `952` | CSS 视口高 |
| `DEVICE_PIXEL_RATIO` | `3.0` | 设备像素比 |
| `DEVICE_CORES` | `8` | 暴露的 CPU 核心数 |
| `DEVICE_MEMORY_GB` | `8` | 暴露的内存大小（GB） |
| `DEVICE_TOUCH_POINTS` | `5` | 触控点数 |
| `DEVICE_GPU_VENDOR` | `Imagination Technologies` | WebGL 指纹上报的 GPU 厂商（隐藏 SwiftShader） |
| `DEVICE_GPU_RENDERER` | `PowerVR DXT-48-1536` | WebGL 指纹上报的 GPU 型号（Pixel 10 Pro Tensor G5 真实 GPU） |

---

## 本地开发（不依赖 Docker）

需要 Python 3.10+，且本机装有 Google Chrome/Chromium 及匹配的
chromedriver（或用 `CHROMEDRIVER_PATH` 指定）。

```bash
# 创建虚拟环境并安装依赖
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # Linux / macOS

pip install -r requirements.txt

# 启动 Web 界面（http://127.0.0.1:8910）
python webapp.py

# 另开终端启动 Telegram 机器人（需设置 TELEGRAM_BOT_TOKEN）
# set TELEGRAM_BOT_TOKEN=123456:ABC-DEF   （Windows）
python main.py
```

运行测试脚本：

```bash
python test_smoke.py            # 页面渲染/登录/鉴权冒烟测试
python test_success_path.py     # 模拟成功路径（mock 检测器）
```

---

## Telegram 机器人（可选）

Web 界面之外的原有 Telegram 接口：

1. 在 Telegram 中通过 **@BotFather** 创建机器人，获取 Token
2. 在 `docker-compose.yml` 中设置 `TELEGRAM_BOT_TOKEN` 后重启，
   或临时运行：`docker compose run --rm -e TELEGRAM_BOT_TOKEN=xxx pixel-gemini`

命令一览：

| 命令 | 说明 |
|---|---|
| `/start` | 欢迎信息与命令列表 |
| `/login` | 输入 Gmail 邮箱和密码（两步对话，密码消息自动删除） |
| `/check_offer` | 模拟设备、登录并检测 Gemini Pro Offer |
| `/get_link` | 再次获取最近一次捕获的 Offer 链接 |
| `/status` | 查看当前会话与设备信息 |

---

## 项目结构

```
pixel-gemini/
├── webapp.py               # Flask Web 界面（登录/账号/运行/日志）
├── main.py                 # Telegram 机器人入口
├── run_bot.py              # Telegram 机器人启动器（容器内使用）
├── device_simulator.py     # Pixel 10 Pro 设备模拟
├── google_automation.py    # Google 登录 + Offer 检测自动化
├── config.py               # 配置与常量
├── requirements.txt        # Python 依赖
├── Dockerfile              # 容器镜像（Python 3.12 + Chromium）
├── docker-compose.yml      # 一键容器编排
├── docker/entrypoint.sh    # 容器启动脚本
├── templates/              # Web 页面模板（Jinja2）
├── static/                 # 样式表
├── docs/设备模拟与风控分析.md  # 风控问题分析与修复记录（中文）
├── test_smoke.py           # 页面冒烟测试
└── test_success_path.py    # 成功路径模拟测试
```

---

## 常见问题 FAQ

**Q1：登录提示"Google blocked the login (challenge page…)"？**
账号触发了 Google 风控。可能原因与对策：
- 出口 IP 是数据中心 IP → 配置 `PROXY_URL`（住宅/移动代理）
- 该账号近期登录过于频繁 → 降低频率，先人工登录"养熟"
- 账号开启 2FA / 有安全验证 → 需人工处理，或更换无 2FA 的账号

**Q2：报错 "session not created: Failed to create Chrome process"？**
容器内 Chromium/Chrome 未正常安装或启动。本机开发则需安装 Chrome 或设置
`CHROMEDRIVER_PATH` 指向匹配的 chromedriver（参考上方"chromedriver 说明"）。

**Q3：容器里 chromedriver 没匹配到 Chrome 版本会自动安装吗？**
**会。**默认 `CHROMEDRIVER_PATH` 留空，由 Selenium Manager 自动检测 Chrome
版本并自动下载/复用**匹配版本**（有缓存直接复用，无缓存自动联网安装）。
镜像内置的 `/usr/local/bin/chromedriver` 只是离线兜底；即使显式指定了它，
代码也会先做版本校验，**不匹配时自动回退 Selenium Manager 重新匹配**。

**Q4：检测结果是"没有发现 Offer"？**
Offer 可能不适用于该账号所属地区/已激活/活动结束。可稍后重试。

**Q5：修改管理员密码？**
设置环境变量 `ADMIN_PASSWORD` 并重启容器（`docker compose up -d`），
重启时会同步更新数据库中的密码。

**Q6：数据存在哪里？**
容器内 `/data/pixel_gemini.db`（对应 Docker 卷 `pixel-gemini-data`）。
本机开发时为 `./data/pixel_gemini.db`。

**Q7：如何彻底清空数据？**
`docker compose down -v` 删除容器与数据卷（不可恢复）。

---

## 风控与免责声明

- 详细的检测/风控问题分析与修复记录见
  [docs/设备模拟与风控分析.md](docs/设备模拟与风控分析.md)（中文）。
- **重要**：本项目只能模拟浏览器层面的 UA/指纹，**无法通过 Google 的
  Play Integrity / 设备 attestation 校验**；自动化 Google 账号访问可能
  违反 Google 服务条款，触发风控属正常现象。
- 本项目仅供**教育学习与个人合法用途**，请仅使用你拥有且不重要的账号，
  并自行承担使用风险。
