# 机房资产盘点系统

基于 **FastAPI + Vue3 + Element Plus + 微信小程序** 的机房资产盘点系统。

管理员在PC后台上传待盘点清单、指派任务、查看盘点结果；
现场人员通过微信小程序扫码盘点。

## 目录结构

```
equipment_inventory/
├── backend/               # FastAPI 后端
│   ├── __init__.py
│   ├── database.py        # SQLite 数据库操作
│   ├── main.py            # API 路由入口
│   └── models.py          # Pydantic 数据模型
├── admin_web/             # Vue3 PC后台
├── data/                  # 数据库文件存放
├── requirements.txt
└── README.md
```

## 快速开始

### 1. 安装依赖

```bash
cd equipment_inventory
pip install -r requirements.txt
cd admin_web
npm install
```

### 2. 启动后端

```bash
cd equipment_inventory
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

启动后访问 http://localhost:8000/health 验证。

### 3. 启动PC后台

```bash
cd equipment_inventory
start_admin_vue.bat
```

打开浏览器访问显示的地址（默认 http://127.0.0.1:5173）。

## Vue PC后台使用说明

### 上传清单
- 选择 Excel 文件（需包含：网元名称、设备型号、资产标签号）
- 下载模板、选择 Excel 文件、预检清单
- 点击“导入并生成待盘点机房”

### 任务看板
- 查看所有任务进度
- 查看任务统计图、任务进度和超时任务
- 支持查询并作废任务

### 资产盘点数据
- 查询并选择任务
- 汇总展示：设备总数、已盘、未盘、进度
- 可导出任务清单与现场盘点合并后的原始数据

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/upload | 上传Excel，创建盘点任务 |
| GET | /api/workers | 查询所有现场人员 |
| GET | /api/tasks | 查询任务列表 |
| GET | /api/tasks/{id}/items | 查询任务下的设备清单 |
| POST | /api/scan | 提交盘点结果 |
| GET | /api/report/{id} | 获取盘点报告 |
| GET | /api/export/{id} | 导出结果Excel |
| POST | /api/login | 小程序登录 |
| GET | /health | 健康检查 |

## 小程序开发

小程序源码使用微信原生开发，后续补充。

开发阶段登录测试：POST /api/login 传 code = "worker_001" 或 "worker_002"。

## 技术栈

- 后端：Python + FastAPI + SQLite
- PC后台：Vue3 + Element Plus
- 小程序：微信原生
- Excel：openpyxl
## 微信小程序

代码在 `miniprogram/` 目录下，使用微信开发者工具打开即可。

### 小程序页面

- **登录页**：微信一键登录，开发模式可选测试账号
- **任务列表页**：展示本人任务、进度条、支持下拉刷新
- **设备盘点页**（核心）：逐设备扫码（资产条码+机柜条码）、勾选型号、填写备注、提交

### 开发配置

1. 打开微信开发者工具，导入 `equipment_inventory/miniprogram` 目录
2. 在 `app.js` 中修改 `apiBase` 为你的后端地址（默认 `http://localhost:8000/api`）
3. 模拟器中点击"微信一键登录"或选择测试账号
4. 开发测试阶段，后端代码用 `POST /api/login` 传入 `worker_001` / `worker_002` 模拟登录

### 小程序姓名和手机号同步

- 现场人员登录后，可在小程序首页填写真实姓名，并点击“授权手机号并同步”。
- 姓名由现场人员手工填写；手机号通过微信 `getPhoneNumber` 授权后，由后端调用微信接口换取并写入账号资料。
- 后端需配置 `WECHAT_APPID` 和 `WECHAT_SECRET`，可写入项目根目录 `.env`，`start_backend.bat` 会自动读取。

- 小程序：微信原生开发（18个文件，约31KB）
- 小程序：微信原生开发
