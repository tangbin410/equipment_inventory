# 脚本使用说明

更新：2026-11-03。22 个操作脚本已分类存放：`scripts/mac/` 有 9 个，`scripts/windows/` 有 13 个。各脚本头部有中文用途注释，已适配新位置并修正相互调用，根目录不再保留重复入口。

## Mac 常用入口

| 脚本 | 实际功能 | 什么时候使用 |
|---|---|---|
| `scripts/mac/start_all_mac.command` | 启动本机后端、Vue 开发后台和本地 AI Agent，复用健康的已有服务，并打开本机后台 | 日常启动 |
| `scripts/mac/switch_mode_mac.command` | 弹窗切换测试账号/本机接口与真实微信/公网接口，同步配置、重启后端、启停并检查隧道，失败时尝试恢复 | 确实要切换小程序运行模式时 |
| `scripts/mac/prepare_remote_admin_mac.command` | 构建异地后台到 `admin_web/remote_dist`，检查远程配置与本机健康；`--check-only` 只检查 | 后台代码更新后发布异地页面，或检查准备情况 |
| `scripts/mac/repair_tunnel_mac.command` | 将现有 Cloudflare 系统隧道设为 HTTP/2（TCP）并重启，检查隧道和小程序公网接口，失败时尝试恢复 | 连接故障维修备用，不需每天运行 |
| `scripts/mac/reset_admin_password_mac.command` | 在运行系统的 Mac 本机恢复现有管理员密码；隐藏输入两次，先备份数据库，修改后旧登录失效并保留审计 | 完整管理员也无法登录时使用；不会修改 `.env`、切模式或重启服务 |

上述入口建议保留。`scripts/mac/start_all_mac.command` 不负责建立域名/邮箱规则，也不负责启动已停止的 Cloudflare 隧道；退出时只停止本次由它启动的服务，复用的已有服务不会停止。若本次启动了服务，应保留对应终端窗口。

`scripts/mac/prepare_remote_admin_mac.command` 不修改 `.env`、不重启后端、不切模式、不重启隧道。“本机检查通过”不能替代真正的公网访问验证。

### 模式名称的准确含义

| 弹窗名称 / 参数 | 小程序身份 | 接口和隧道 | 对异地后台的影响 |
|---|---|---|---|
| 线上调测 / `online` | 测试人员账号 | 本机接口，停止公网隧道 | 共享隧道停止后，异地后台一起断开 |
| 线下测试 / `field` | 真实微信账号 | 公网 HTTPS，启用隧道 | 隧道和本机服务正常时可异地访问 |

切换后还需在微信开发者工具中重新编译并重新登录；手机体验版需要重新上传。脚本不会自动发布小程序。

## Mac 支撑脚本

下面 4 个脚本也在 `scripts/mac/`，会被上面的入口调用，不能因为平时不双击就当作重复文件删除。

| 脚本 | 实际功能 | 调用关系 |
|---|---|---|
| `scripts/mac/setup_mac.sh` | 用 uv 准备 Python 3.12 环境和后端依赖，用 npm 安装前端依赖；要求先有 `.env` | 一键启动缺环境时调用；首次安装或依赖更新也可运行 |
| `scripts/mac/start_backend_mac.sh` | 使用项目 `.venv` 启动 FastAPI，读取根目录 `.env`，监听 `127.0.0.1:8000` | 一键启动、模式切换都要调用 |
| `scripts/mac/start_admin_mac.sh` | 启动本机 Vue/Vite 开发后台，入口 `http://127.0.0.1:5173` | 一键启动调用；不是已发布的异地页面 |
| `scripts/mac/start_agent_mac.sh` | 使用项目 `.venv` 启动本地 AI Agent，监听 `127.0.0.1:9000` | 一键启动调用；普通三方比对的计算不依赖 AI |

```text
scripts/mac/start_all_mac.command
  ├─ scripts/mac/setup_mac.sh（缺少运行环境时）
  ├─ scripts/mac/start_backend_mac.sh
  ├─ scripts/mac/start_admin_mac.sh
  └─ scripts/mac/start_agent_mac.sh

scripts/mac/switch_mode_mac.command
  └─ scripts/mac/start_backend_mac.sh（重新加载模式配置）

scripts/mac/prepare_remote_admin_mac.command
  └─ npm 构建 + 配置及健康检查（不启动/重启服务）
```

## Windows 备用与历史脚本

以下 13 个脚本不是当前 Mac 日常运行的依赖。确认不再使用 Windows 后，可先打包备份再整组清理；要保留 Windows 支持时，入口和被调用脚本需成套保留。本次已适配从 `scripts/windows/` 向上两级定位项目根；包装脚本仍调用同目录配套文件。不要再随意移动单个脚本。

| 脚本 | 实际功能 / 调用关系 | 说明 |
|---|---|---|
| `scripts/windows/start_backend.bat` | 调用 `scripts/windows/start_backend_dev.bat` | 开发后端的兼容入口 |
| `scripts/windows/start_backend_dev.bat` | 启用测试登录和热重载，启动本机 8000；发现 cloudflared 运行会拒绝启动 | Windows 本机开发使用 |
| `scripts/windows/start_backend_prod.ps1` | 读取并验证正式配置，关闭测试登录，启动本机 8000 后端 | 不负责启动隧道 |
| `scripts/windows/start_backend_tunnel.ps1` | 调用 `scripts/windows/start_backend_prod.ps1` | 旧名称兼容入口，名称含 tunnel 但不建立隧道 |
| `scripts/windows/start_admin_vue.bat` | 检查前端环境，缺依赖时安装，启动 Vue/Vite | Windows 本机后台 |
| `scripts/windows/start_local_agent.bat` | 调用 `scripts/windows/start_local_agent.ps1` | AI Agent 双击入口 |
| `scripts/windows/start_local_agent.ps1` | 读取 `.env`，启动本机 9000 AI Agent | 可选 AI 功能 |
| `scripts/windows/configure_ai_env.bat` | 调用 `scripts/windows/configure_ai_env.ps1` | AI 配置入口，不是每日启动脚本 |
| `scripts/windows/configure_ai_env.ps1` | 交互填写 DeepSeek Key、模型、地址，写入 `.env` | 旧实现用 ASCII 重写配置，有中文时可能损失文字；本次只标注，未修复 |
| `scripts/windows/enable_local_agent.bat` | 调用 `scripts/windows/enable_local_agent.ps1` | 启用 Agent 配置入口 |
| `scripts/windows/enable_local_agent.ps1` | 写入本机 Agent 地址，缺少调用令牌时生成 | 不启动服务；配置变更后需重启后端 |
| `scripts/windows/switch_miniprogram_mode.bat` | 调用同名 `.ps1` | 历史停用候选，不建议使用 |
| `scripts/windows/switch_miniprogram_mode.ps1` | 针对旧登录页和旧启动文件切换 `dev` / `trial` | 已不兼容当前结构，可能改了一半后报错，不建议使用 |

旧 Windows 切换脚本先修改 `login.js`，随后要求 `scripts/windows/start_backend.bat` 中存在旧的配置开关，但当前该文件只是转发入口，所以不能把它当成可靠切换工具。Mac 应使用 `scripts/mac/switch_mode_mac.command`。本次没有执行或修复这些 Windows 脚本。

## `.env` 在哪里，邮箱在哪里修改

当前真实配置文件的完整路径：

```text
/Users/tangbin/equipment_inventory/.env
```

文件名是 `.env`，不是 `.evn`。它以点开头，访达默认隐藏。

1. 在访达打开项目目录，按 `Command + Shift + .` 显示隐藏文件。
2. 或在 VS Code 按 `Command + P`，输入 `.env`，选择项目根目录的这个文件。
3. 搜索 `INVENTORY_ADMIN_ALLOWED_EMAILS`。本次检查在第 25 行，编辑后行号可能变化。

多个邮箱用英文逗号分隔，例如：

```dotenv
INVENTORY_ADMIN_ALLOWED_EMAILS=owner@example.com,b@example.com
```

这是格式示例，未替你开通 `b@example.com`；保留自己的邮箱，不填写邮箱密码。

异地访问还需在 Cloudflare「Zero Trust → Access controls → Policies → 仅允许指定管理员邮箱」中加入同一邮箱，并有已启用的独立后台账号。修改 `.env` 后需要完整重启后端；仅刷新网页或再次双击一键启动（可能复用已有进程）不能保证新配置生效。只更新邮箱时，无需运行模式切换脚本。

| 配置文件 | 用途 |
|---|---|
| 根目录 `.env` | 当前真实后端配置，包含密钥，不能整份对外分享 |
| 根目录 `.env.example` | 后端配置模板，不是正在使用的配置 |
| `admin_web/.env.example` | 前端地址配置模板 |
| `deploy/server.env.example` | 独立服务器部署示例 |

详细开通与权限说明见 [协作管理员与异地访问](docs/协作管理员与异地访问.md)。目录必要性与可清理项见 [项目文件保留与清理说明](docs/项目文件保留与清理说明.md)。

新成员开通访问的逐步操作见 [协作管理员后台访问开通指引](docs/协作管理员后台访问开通指引.md)。

## 忘记后台密码时

- 协作管理员：联系完整管理员，在“账号管理 → 对应账号 → 重置密码”中处理。
- 完整管理员也无法登录：在运行本项目的 Mac 上双击 `scripts/mac/reset_admin_password_mac.command`，输入登录编码（默认为 `admin_001`），按提示隐藏输入两次新密码并确认。密码须为 12—128 位，不用发给其他人或 AI。
- 工具只处理现有、已启用的后台管理员，不新建账号、不提升权限；先保存私有数据库备份，再更新目标密码和登录版本并写入本机恢复审计。业务资料保持原样，修改后直接使用新密码正常登录，不自动登录、不需重启。
- 密码输入时终端不显示字符，这是正常的隐藏输入。取消或校验失败不会修改密码。
- `.env` 中的 `INVENTORY_ADMIN_PASSWORD` 仅用于首次初始化，修改它或重启服务不能重置已有账号。Cloudflare 邮箱验证码也不是后台密码；当前未开放未登录的邮箱自助重置接口。
