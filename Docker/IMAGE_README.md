# 资产盘点系统镜像使用说明——请先阅读

版本：2026-11-12。适用于收到 **Linux x86_64（amd64）成品镜像**的部署人员。构建与容器基础验收已完成，具体范围见文末及交付记录。

## 1. 收到什么，适用什么环境

请将以下三个交付文件保存在同一目录：

| 文件 | 用途 |
|---|---|
| `equipment-inventory-docker-linux-amd64.zip` | 已构建镜像、启动配置及空配置模板 |
| `SHA256SUMS` | 交付文件完整性校验值 |
| `使用说明-请先阅读.md` | 本说明 |

运行环境：**Linux amd64 主机**，或 **Windows x86_64 上的 Docker Desktop，使用 Linux 容器模式**。先启动 Docker，确认 `docker version` 和 `docker compose version` 能正常执行。Windows 容器模式不能运行本镜像。Apple M 系列及其他 ARM 主机不是本次原生目标平台，不建议未经适配、验证直接用于正式运行。

镜像已包含 Python、第三方依赖、编译后的业务程序、管理后台和必要模板；接收方**不需要旧版 runtime 运行包，不需要另装 Python／Node，也不需要联网构建或下载应用依赖**。首次安装 Docker 本身、AI 模型调用、微信登录及现场手机访问，仍可能需要相应网络和部署配置，并非所有功能都能断网使用。

交付物不含原作者的真实业务数据、密钥、个人配置、后端／前端开发源工程或小程序发布包。字节码及前端 JavaScript 不是不可分析的加密程序。

## 2. 先校验，再解压

在收到三个文件的目录执行适合本机的一种校验：

```bash
# Linux
sha256sum -c SHA256SUMS
```

```bash
# macOS（仅校验文件，不代表本机架构适合运行）
shasum -a 256 -c SHA256SUMS
```

Windows PowerShell 使用：

```powershell
Get-FileHash .\equipment-inventory-docker-linux-amd64.zip -Algorithm SHA256
Get-FileHash .\使用说明-请先阅读.md -Algorithm SHA256
```

将输出分别与外部 `SHA256SUMS` 对应文件的值比较，忽略十六进制字母大小写。校验不符时停止部署，重新获取文件。校验用于确认传输完整性，文件本身应从认可的交付来源获取。

使用解压工具解压 ZIP，或在 Windows PowerShell 执行：

```powershell
Expand-Archive .\equipment-inventory-docker-linux-amd64.zip -DestinationPath .
```

进入解压后的目录：

```bash
cd equipment-inventory-docker-linux-amd64
```

目录应包含以下内容，后续命令均在此目录执行：

```text
equipment-inventory-image-linux-amd64.tar.gz
compose.yaml
.env.example
README.md
SHA256SUMS
```

内部 `SHA256SUMS` 可用同样方法校验镜像等文件；Windows 可用 `Get-FileHash` 逐项比较。

## 3. 导入成品镜像

```bash
docker load -i equipment-inventory-image-linux-amd64.tar.gz
```

等待显示镜像导入成功。**不要把外层 ZIP 传给 `docker load`，不要执行 `docker build`。**内部 `.tar.gz` 是 `docker save` 产生并压缩的镜像文件，可直接用上述命令导入。

此版 `compose.yaml` 使用 `image`、`platform: linux/amd64` 和 `pull_policy: never`，不含 `build`，不会自动从镜像仓库拉取应用镜像。没有成功导入时，应修正导入步骤，而不是改用其他来源的同名镜像。

## 4. 填写自己的密钥和初始密码

Linux 执行：

```bash
cp .env.example .env.runtime
chmod 600 .env.runtime
```

Windows PowerShell 执行：

```powershell
Copy-Item .env.example .env.runtime
```

使用文本编辑器打开 `.env.runtime`，至少填写：

| 配置项 | 填写要求 |
|---|---|
| `INVENTORY_SECRET_KEY` | 自己生成并妥善保存的固定随机密钥，至少 32 字符 |
| `INVENTORY_ADMIN_PASSWORD` | 首次启动的管理员密码，12—128 字符 |

密钥与登录密码是两项不同配置，不要填成示例说明文字或沿用原作者的值。随机密钥可通过密码管理器生成；安装 OpenSSL 的环境也可执行 `openssl rand -hex 32`。

保留其他默认配置即可先使用本机后台。文件不要命名成 `.env.runtime.txt`，也不要把填写后的配置对外发送。重新复制模板前确认不会覆盖现有配置。

## 5. 启动、登录与基础验证

```bash
docker compose up -d --no-build
docker compose ps
```

在**运行 Docker 的那台机器**用浏览器打开：

**http://127.0.0.1:8080**

- 登录账号：`admin_001`
- 登录密码：刚才填写的 `INVENTORY_ADMIN_PASSWORD`

同一入口同时提供管理网页和后端接口，不需要单独启动前端服务。首次启动为独立、空白业务数据，需要管理员自行导入任务清单与资产信息表，没有原作者的任务、台账和盘点记录。

健康地址为 `http://127.0.0.1:8080/health`，正常应返回含 `status: ok` 的 JSON。还需实际完成管理员登录、导入测试资料及查询，健康接口正常不等于全部业务已经验收。

默认映射为 `127.0.0.1:8080:8000`，仅部署主机本机访问。其他人的电脑不能使用其自己的 `127.0.0.1` 访问这台主机。若需要局域网、手机或互联网访问，由部署人员另行配置单位认可的 HTTPS 与访问网关；不要直接把默认端口开放到互联网。

## 6. 数据保存、日常启停与更新

数据库及程序备份保存在独立的 Docker 命名卷中，挂载至容器 `/app/data`。不是保存在镜像压缩包里。保留同一部署目录和 Compose 项目名称，避免误连到另一个新建空卷。

```bash
# 暂停与恢复，保留数据
docker compose stop
docker compose start
```

**不要执行 `docker compose down -v`，这会删除此部署的数据卷。**清理 Docker 资源时也不要删除正在使用的卷。真实使用前安排数据备份，并验证恢复方法。

更新应用前先备份数据，再按新版本交付说明导入镜像、更新配置并启动，继续使用原数据卷。更换 `.env.runtime` 后需重建容器进程加载配置，执行 `docker compose up -d --no-build --force-recreate`；此命令保留命名卷。

`INVENTORY_ADMIN_PASSWORD` 只用于首次初始化，修改它不会重置已有数据库中的账号密码。已能登录时由完整管理员通过账号管理重置；无法登录时联系部署维护人员，不要删卷或删库来恢复密码。密钥应固定保存，不随意更换。

## 7. AI、微信及安全边界

| 功能 | 部署方还需准备 |
|---|---|
| 后台任务、资料、规则比对、拼销与人工整改 | 登录后台后导入自己的资料，按操作流程使用 |
| AI 辅助分析与对话 | 自己的 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL` 及模型服务网络；不配置不影响普通业务和规则核对 |
| 真实微信小程序现场盘点 | 自己的小程序 AppID／Secret、合法 HTTPS 域名、账号与发布配置；镜像不含小程序发布包 |
| 二维码公开链接 | 自己的实际公开地址，配置 `INVENTORY_PUBLIC_BASE_URL`，并确认允许公开的资料范围 |

容器内可选 Agent 不代表模型全部在本地离线运行，配置外部模型后仍会发送模型请求。真实资料能否用于外部服务，应遵循单位规定。

默认容器部署只对本机开放，采用后台账号密码和角色权限；不会继承原作者的 Cloudflare 邮箱、隧道、域名或 Access 策略。若启用 Cloudflare Access，除专用域名、邮箱白名单、AUD 等配置外，还需将 `INVENTORY_ADMIN_IP_RESTRICTION_ENABLED` 恢复为 `1`，防止通过其他主机名绕过入口保护。

部署主机仍需持续运行。使用手机、异地后台、微信或 AI 等联网功能时，也需保持相应网络可用。

## 8. 常见问题与反馈日志

| 现象 | 检查与处理 |
|---|---|
| `docker` 或 `docker compose` 不可用 | 安装并启动 Docker；Windows 确认处于 Linux 容器模式 |
| 镜像导入失败 | 确认使用内部 `.tar.gz` 文件，校验完整性，检查磁盘空间及 Docker 状态 |
| 提示镜像不存在、试图拉取 | 先成功执行 `docker load`；不要改掉 `pull_policy: never`，核对使用的是同一交付包的 Compose 文件 |
| `exec format error` 或平台不匹配 | 核对目标为 Linux amd64；ARM／Apple M 系列需另行适配验证 |
| 容器启动后退出 | 查看日志，确认密钥及首次密码已填写并符合长度要求 |
| 8080 端口被占用 | 检查本机已有服务；如需换端口，仅调整映射的宿主端口，并同步访问地址，保留 `127.0.0.1` 绑定 |
| 网页打不开 | 查看 `docker compose ps` 和日志，在部署主机浏览器访问，检查 Docker 运行状态 |
| 没有以前的数据 | 首次部署不带业务数据；已有部署则核实 Compose 项目及数据卷是否改变，勿直接删除重建 |
| 修改环境密码后仍不能登录 | 环境密码不覆盖已有账号密码，按账号管理或维护恢复流程处理 |
| AI／微信不可用 | 检查部署方自己的配置、域名及网络；重新导入镜像不会自动开通对应服务 |

查看状态与近期日志：

```bash
docker compose ps
docker compose logs --no-color --tail=100 inventory
```

需要保存排查材料时：

```bash
docker compose logs --no-color --tail=100 inventory > inventory-log.txt
```

**日志不会因上述命令自动脱敏。**发送前自行检查并遮盖账号、邮箱、IP、机房、设备名、条码，以及可能出现的密钥、Token、口令；提供去除敏感信息后的报错、发生时间、操作步骤和容器状态即可。不要发送 `.env.runtime`、数据库、数据卷、备份或真实业务导出文件。

## 9. 本次验收范围

已构建 `linux/amd64` 镜像，并在 Mac 的 Docker Desktop 中以该平台仿真运行，完成健康接口、后台网页、管理员登录、模板接口、源文件访问限制、内部 Agent 路由鉴权，以及保留同一数据卷重建后账号密码和数据保留的检查。

尚未在实体 Windows 主机验证；目标主机仍需按本文完成启动、登录和自身业务流程验收。真实微信与外部模型未配置、未验证，镜像验收不代表这些外部功能已开通或可用。
