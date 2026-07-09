# 机房资产盘点系统

基于 **FastAPI + Streamlit + 微信小程序** 的机房资产盘点系统。

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
├── pc_admin/
│   └── admin.py           # Streamlit PC后台管理
├── data/                  # 数据库文件存放
├── requirements.txt
└── README.md
```

## 快速开始

### 1. 安装依赖

```bash
cd equipment_inventory
pip install -r requirements.txt
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
streamlit run pc_admin/admin.py
```

打开浏览器访问显示的地址（默认 http://localhost:8501）。

## PC后台使用说明

### 上传清单
- 选择 Excel 文件（需包含：网元名称、设备型号、资产标签号）
- 输入机房名称
- 选择现场人员
- 点击"创建盘点任务"

### 任务看板
- 查看所有任务进度
- 可按状态筛选（待处理/进行中/已完成）
- 点击展开查看详情

### 盘点报告
- 选择任务查看核验结果
- 汇总展示：设备总数、一致数、不一致数、未扫数
- 可导出 Excel 报告

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
- PC后台：Streamlit
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

- 小程序：微信原生开发（18个文件，约31KB）
- 小程序：微信原生开发
