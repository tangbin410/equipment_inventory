# 机房资产盘点系统 - 完整使用指南

## 第一步：安装依赖

```bash
cd equipment_inventory
pip install -r requirements.txt
```

需要安装：fastapi, uvicorn, openpyxl, streamlit, python-multipart, pydantic, requests

## 第二步：启动后端

打开第一个终端窗口，执行：

```bash
cd equipment_inventory
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

看到以下输出说明启动成功：
```
Uvicorn running on http://0.0.0.0:8000
```

验证：浏览器打开 http://localhost:8000/health ，返回 `{"status":"ok"}` 即正常。

启动时自动完成：
- 创建 SQLite 数据库文件 `data/inventory.db`
- 创建 3 个默认用户：管理员、张三、李四

## 第三步：启动PC后台

打开第二个终端窗口，执行：

```bash
cd equipment_inventory
streamlit run pc_admin/admin.py
```

浏览器自动打开 PC 后台页面。

## 第四步：PC后台操作流程

### 4.1 上传清单

1. 在侧边栏点击「上传清单」
2. 点击「Browse files」选择你的 Excel 文件
3. 输入机房名称（如："斗门雷蛛公园"）
4. 指派给现场人员（选择"张三"或"李四"）
5. 点击「创建盘点任务」
6. 看到绿色提示"成功导入 X 台设备"即成功

### 4.2 查看任务

1. 侧边栏点击「任务看板」
2. 可看到刚创建的任务，显示进度（0/5 等）
3. 点击展开可查看详情

### 4.3 查看报告

1. 侧边栏点击「盘点报告」
2. 选择任务查看盘点结果
3. 可导出 Excel 报告

## 第五步：微信小程序

### 5.1 导入开发者工具

1. 下载安装微信开发者工具：https://developers.weixin.qq.com/miniprogram/dev/devtools/download.html
2. 打开开发者工具，点击「导入项目」
3. 项目目录选择：`equipment_inventory/miniprogram`
4. AppID 先填「测试号」（在开发者工具中可以申请），后续换成真实的
5. 点击「导入」

### 5.2 修改后端地址

打开 `miniprogram/app.js`，找到第 7 行：

```javascript
apiBase: "http://localhost:8000/api",
```

**用手机真机测试时**，将 `localhost` 改为你电脑的局域网 IP（如 `http://192.168.1.100:8000/api`）：

```javascript
apiBase: "http://192.168.1.100:8000/api",
```

如何查看本机 IP：
- Windows：打开命令提示符，输入 `ipconfig`，找到 IPv4 地址

### 5.3 使用小程序

1. 开发者工具中点击「编译」
2. 模拟器中显示登录页
3. 点击「微信一键登录」（测试环境可能弹失败，用下方的测试账号）
4. 点击「张三」或「李四」登录
5. 进入任务列表页 → 点击任务 → 进入设备盘点页
6. 点击设备展开扫描表单，填写后提交

> 在开发者工具中扫码功能使用「扫码」按钮旁边的下拉菜单选择图片模拟

## 第六步：端到端测试（完整验证）

### 手动走一遍流程

```
1. PC后台 → 上传清单 → 创建任务
2. 小程序 → 登录（选张三） → 看到任务
3. 小程序 → 点击任务 → 看到设备清单
4. 小程序 → 点击设备 → 填4个字段 → 提交
5. 小程序 → 看到 ✅ 一致 或 ❌ 不一致
6. PC后台 → 盘点报告 → 查看结果
7. PC后台 → 导出 Excel
```

### 自动测试（确认后端正常）

```bash
cd equipment_inventory
python -c "
import requests
r = requests.get('http://localhost:8000/health')
print('后端状态:', r.json())
r = requests.get('http://localhost:8000/api/workers')
print('现场人员:', len(r.json()), '人')
"
```

## 第七步：生产部署准备

### 后端部署到服务器

如果需要在服务器上长期运行，用 gunicorn 代替 uvicorn：

```bash
pip install gunicorn
gunicorn -w 2 -k uvicorn.workers.UvicornWorker backend.main:app --bind 0.0.0.0:8000
```

### 小程序上线前配置

| 事项 | 说明 |
|------|------|
| 申请 AppID | 微信公众平台注册小程序，获取真实 AppID |
| 配置服务器域名 | 小程序后台 → 开发 → 开发设置 → 服务器域名，添加后端地址 |
| 修改 `project.config.json` | 将 `appid` 改为真实 AppID |
| 关闭调试模式 | 上线前将开发者工具的「详情 → 本地设置 → 不校验合法域名」取消勾选 |

### SQLite 生产环境说明

当前使用 SQLite，适合中小规模使用（单机房几百台设备没问题）。
如果需要多人同时访问、大量并发，建议升级为 PostgreSQL。
切换时只需修改 `backend/database.py` 中的数据库连接。

## 常见问题

**Q: 启动后端报端口被占用**
```
A: 改端口：python -m uvicorn backend.main:app --host 0.0.0.0 --port 8001
   同时修改 miniprogram/app.js 中的 apiBase
```

**Q: Excel 上传后报"缺少必要列"**
```
A: Excel 表头必须包含：网元名称、设备型号、资产标签号
```

**Q: 小程序扫码后没反应**
```
A: 开发者工具中扫码需要点击「扫码」按钮旁的下拉选择图片模拟。
   真机测试需要后端在小程序白名单内。
```

**Q: 数据显示乱码**
```
A: 确保 Excel 文件保存为 UTF-8 编码。
   后端代码已使用 UTF-8 编码读取和写入。
```

## 项目文件索引

| 文件 | 用途 | 启动方式 |
|------|------|---------|
| `backend/main.py` | FastAPI 后端 | `python -m uvicorn backend.main:app` |
| `pc_admin/admin.py` | PC后台管理 | `streamlit run pc_admin/admin.py` |
| `miniprogram/` | 微信小程序 | 用微信开发者工具打开 |
| `backend/database.py` | 数据库操作 | 自动调用，无需手动运行 |
| `test_api.py` | 接口测试 | `python test_api.py` |
