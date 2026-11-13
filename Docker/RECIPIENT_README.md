# 资产盘点系统使用说明——请先阅读

版本：2026-11-12。本文面向收到发布包的部署人员和使用者。

## 1. 您收到的文件

请将以下三个文件放在同一文件夹：

| 文件 | 用途 |
|---|---|
| `equipment-inventory-runtime-py312.tar.gz` | 后台网页、后端字节码、必要模板和 Docker 构建文件 |
| `SHA256SUMS` | 压缩包和本说明的完整性校验值 |
| `使用说明-请先阅读.md` | 当前这份部署与使用说明 |

**这个压缩包不是已经构建好的 Docker 镜像，不能使用 `docker load` 导入。**需要解压后构建镜像，再启动。文件约 0.75 MB 是正常的：Python 运行环境和第三方依赖会在构建时下载，不在压缩包内。

## 2. 准备环境

- 已安装并启动 Docker，支持 Linux 容器。
- 已安装 Docker Compose，可执行 `docker compose version`。Docker Desktop 通常已包含此功能。
- 构建机器能联网下载 Python 基础镜像和 Python 第三方依赖。
- 无需在宿主机另装 Python、Node.js 或前端开发工具。

可先执行：

```bash
docker version
docker compose version
```

本版已通过本机字节码测试、`linux/amd64` 镜像构建及 Mac Docker Desktop 上的独立容器验收。实体 Windows、部署方网络与真实微信／模型配置仍需自行验证。本文件介绍的是“自行构建包”；如果收到另一个成品镜像交付 ZIP，请按该 ZIP 内的镜像导入说明操作，不需要重复构建。

## 3. 校验与解压

在收到文件的目录校验，任选适合当前系统的一种：

```bash
# Linux
sha256sum -c SHA256SUMS
```

```bash
# macOS
shasum -a 256 -c SHA256SUMS
```

Windows PowerShell 可执行 `Get-FileHash .\equipment-inventory-runtime-py312.tar.gz -Algorithm SHA256`，将结果与 `SHA256SUMS` 中对应文件的值比较。

校验不符时不要继续，重新传输文件。校验通过后解压：

```bash
tar -xzf equipment-inventory-runtime-py312.tar.gz
cd equipment-inventory-runtime
```

也可使用解压工具。请确认进入的目录中能看到 `Dockerfile`、`compose.yaml`、`.env.example` 和 `app`。**后续命令均在这个目录执行。**

## 4. 构建镜像

```bash
docker build -t equipment-inventory:2026.11.12 .
```

首次构建会下载环境与依赖，最终镜像会明显大于压缩包。构建失败时保留终端报错，先检查 Docker 状态和网络；不要把压缩包当成镜像导入。

## 5. 设置自己的密码和密钥

复制空配置模板：

```bash
# Linux / macOS
cp .env.example .env.runtime
chmod 600 .env.runtime
```

Windows PowerShell 使用：

```powershell
Copy-Item .env.example .env.runtime
```

用文本编辑器打开 `.env.runtime`，至少填写以下两项，等号后直接填写值：

| 配置项 | 要求 |
|---|---|
| `INVENTORY_SECRET_KEY` | 自己生成并妥善保存的固定随机密钥，至少 32 字符；不是管理员登录密码 |
| `INVENTORY_ADMIN_PASSWORD` | 首次启动的管理员密码，12—128 字符 |

随机密钥可用密码管理器生成；有 OpenSSL 的机器也可执行 `openssl rand -hex 32`。不要将占位说明当成密码，不要向原作者索取其现有密钥。文件保存后不要再次对外发送。

macOS 访达默认隐藏点开头的文件，可按 `Command + Shift + .` 显示。

## 6. 启动和登录

```bash
docker compose up -d
docker compose ps
```

在**运行 Docker 的那台机器**上用浏览器打开：

**http://127.0.0.1:8080**

- 管理员账号：`admin_001`
- 密码：刚才设置的 `INVENTORY_ADMIN_PASSWORD`

后台网页和接口使用同一入口，不需要另起前端服务。首次启动没有原作者的任务、资产台账或盘点记录，需要管理员自行导入。

默认只允许在部署机器本机访问；其他电脑不能通过它们自己的 `127.0.0.1` 访问此服务。若需多人异地访问，应由部署人员配置 HTTPS 和访问网关，不要直接将默认端口暴露到互联网。

## 7. 功能使用前的配置

| 使用内容 | 需要准备 |
|---|---|
| 后台任务、资产资料、规则比对和数据导出 | 管理员登录后导入自己的任务清单和资产表 |
| 真实现场小程序盘点 | 自己的微信小程序、AppID／Secret、合法 HTTPS 接口域名及发布配置；此包不含小程序发布包 |
| AI 分析 | 自己的模型密钥和模型名称；不配置不影响普通盘点及规则核对 |
| 二维码公开链接 | 自己部署的实际公开地址，填写 `INVENTORY_PUBLIC_BASE_URL` |

不要在配置中沿用原作者电脑的本机地址或私人密钥。更详细的配置和运行边界见解压后的 `README.md`。

## 8. 数据保存和常见问题

数据保存在 Docker 命名卷中，停止或重建容器时应保留该卷并按需备份。**不要执行 `docker compose down -v`，这会删除当前部署的数据卷。**

| 问题 | 检查方法 |
|---|---|
| 构建下载失败 | 检查 Docker 是否运行、镜像及依赖源网络是否可达 |
| 容器启动后退出 | 查看日志，确认密钥、密码已经填写且长度符合要求 |
| 网页打不开 | 查看容器状态，确认 8080 端口未被占用，并在部署机器上访问 |
| 能打开但没有原来的数据 | 本包不携带原作者业务数据，需要自行导入 |
| AI 或微信登录不可用 | 检查部署方自己的对应配置，不是重新构建网页就能解决 |

查看日志和健康状态：

```bash
docker compose logs --tail=100 inventory
docker compose ps
```

健康地址为 `http://127.0.0.1:8080/health`。正常返回包含 `status: ok` 的 JSON 后，还应实际检查管理员登录、清单导入和数据查询。

反馈问题时提供操作步骤、发生时间及去除敏感信息后的报错。不要发送真实 `.env.runtime`、密码、模型密钥、数据库、数据卷或业务导出文件。
