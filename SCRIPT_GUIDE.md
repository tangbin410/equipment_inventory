# 脚本使用说明

本文档用于快速查阅项目根目录下各类启动、配置、切换脚本的用途和日常操作方式。

## 一、日常启动脚本

### 1. start_backend.bat

用途：启动 FastAPI 后端服务。

服务地址：

```text
http://localhost:8000
```

接口文档：

```text
http://localhost:8000/docs
```

主要功能：

- 小程序接口
- PC 后台接口
- 资产盘点数据接口
- AI 模型分析接口
- 读取 `.env` 中的 DeepSeek 和本地 agent 配置

日常使用：需要启动。

停止方式：在启动窗口中按 `Ctrl + C`，或直接关闭窗口。

### 2. start_admin_vue.bat

用途：启动 Vue3 + Element Plus PC 后台。

默认访问地址：

```text
http://127.0.0.1:5173
```

主要功能：

- 上传任务清单
- 查看任务看板
- 查看和导出资产盘点数据
- 使用 AI 模型分析
- 账号管理

说明：

- 这是当前唯一的 PC 后台入口。
- 启动前建议先启动 `start_backend.bat`。

停止方式：在启动窗口中按 `Ctrl + C`，或直接关闭窗口。

### 3. start_local_agent.bat / start_local_agent.ps1

用途：启动本地 AI agent 服务。

服务地址：

```text
http://127.0.0.1:9000/analyze
```

健康检查地址：

```text
http://127.0.0.1:9000/health
```

主要功能：

- 接收后端传来的脱敏盘点数据
- 执行本地规则分析
- 调用 DeepSeek 模型补充分析
- 返回清单式问题和整改建议

日常使用：需要使用“AI模型分析”时启动。

停止方式：在启动窗口中按 `Ctrl + C`，或直接关闭窗口。

## 二、AI 配置脚本

### 1. configure_ai_env.bat / configure_ai_env.ps1

用途：配置 DeepSeek API 信息，写入项目根目录的 `.env` 文件。

会写入：

```env
DEEPSEEK_API_KEY=你的Key
DEEPSEEK_MODEL=模型名称
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

使用场景：

- 第一次配置 AI 模型
- 更换 API Key
- 更换模型名称
- 更换 API 地址

注意：

- 不需要每天运行。
- 运行后需要重启后端。
- `.env` 中包含密钥，不要提交或发给别人。

### 2. enable_local_agent.bat / enable_local_agent.ps1

用途：启用本地 agent 路由，让后端优先调用本地 agent。

会向 `.env` 写入或更新：

```env
INVENTORY_AI_AGENT_URL=http://127.0.0.1:9000/analyze
```

使用场景：

- 第一次接入本地 agent
- `.env` 被重建后，需要重新启用本地 agent
- 从 DeepSeek 直连切回本地 agent

注意：

- 不需要每天运行。
- 运行后需要重启后端。
- 使用 AI 分析前，需要先启动 `start_local_agent.bat`。

## 三、小程序模式切换脚本

### switch_miniprogram_mode.bat / switch_miniprogram_mode.ps1

用途：切换小程序登录模式。

支持两种模式：

```text
dev   开发调测模式
trial 体验版/真实微信登录模式
```

开发调测模式：

- 适合微信开发者工具本地调试
- 启用测试账号
- 后端允许 `worker_001`、`worker_002`、`worker_005`

体验版模式：

- 适合微信体验版或真实登录测试
- 关闭测试账号入口
- 后端启动时需要输入 WeChat AppSecret
- 通常需要配合外网隧道

注意：

- 切换后需要重启后端。
- 切换后需要在微信开发者工具中重新编译或重新上传体验版。

## 四、隧道/外网相关脚本

### start_backend_tunnel.ps1

用途：以体验版/外网访问场景启动后端。

特点：

- 后端监听 `127.0.0.1:8000`
- 关闭开发测试登录
- 启用真实微信登录
- 启动时要求输入 WeChat AppSecret
- 适合配合 Cloudflare Tunnel 等工具使用

日常本机开发不一定需要使用。

## 五、推荐启动顺序

### 只使用 PC 后台，不用 AI

```text
1. start_backend.bat
2. start_admin_vue.bat
```

### 使用 PC 后台 + AI 模型分析

```text
1. start_local_agent.bat
2. start_backend.bat
3. start_admin_vue.bat
```

### 微信开发者工具调试小程序

```text
1. switch_miniprogram_mode.bat 选择 dev
2. start_backend.bat
3. 微信开发者工具重新编译
```

### 小程序体验版/真实微信登录

```text
1. switch_miniprogram_mode.bat 选择 trial
2. start_backend_tunnel.ps1
3. 启动外网隧道
4. 上传或打开体验版小程序
```

## 六、如何停止服务

### 方法一：关闭对应窗口

哪个窗口启动的服务，就关闭哪个窗口。

常见窗口：

- 后端窗口：`start_backend.bat`
- PC 后台窗口：`start_admin_vue.bat`
- 本地 agent 窗口：`start_local_agent.bat`

### 方法二：在窗口里按 Ctrl + C

在对应窗口中按：

```text
Ctrl + C
```

如果提示是否终止批处理，输入：

```text
Y
```

### 方法三：按端口强制停止

查看当前监听端口：

```powershell
Get-NetTCPConnection -LocalPort 8000,5173,9000 -State Listen
```

按进程 ID 停止：

```powershell
Stop-Process -Id 进程ID -Force
```

一次停止三个服务：

```powershell
Get-NetTCPConnection -LocalPort 8000,5173,9000 -State Listen -ErrorAction SilentlyContinue |
ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

端口对应关系：

```text
8000  后端 FastAPI
5173  PC 后台 Vue3
9000  本地 AI agent
```

## 七、日常维护建议

- `.bat` 文件优先用于双击启动，适合日常使用。
- `.ps1` 文件是具体逻辑，适合排查或高级操作。
- `.env` 是本地配置文件，保存 AI Key 和本地 agent 地址。
- 修改 `.env` 后，需要重启后端。
- 使用 AI 分析前，先确认本地 agent 和后端都已启动。
- 如果 AI 页面显示模型不可用，优先检查 `start_local_agent.bat` 是否已启动，再点击“检测模型是否可用”。
