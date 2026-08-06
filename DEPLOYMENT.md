# 轻量云服务器试用部署指南

目标：用轻量云服务器承载后端 API 和 PC 管理后台，小程序体验版给现场人员跨网络试用。

如果不买云服务器、改用电脑主机内网穿透，请看 `TUNNEL_DEPLOYMENT.md`。

## 1. 购买与开放端口

建议配置：

- 1 台 Linux 轻量云服务器，Ubuntu 22.04 LTS。
- 1 核 2G 起步，少量机房功能试用足够；照片较多时磁盘建议 40G 以上。
- 安全组只开放 `22`、`80`、`443`。
- 不要直接开放 `8000`，它只给本机 Nginx 反代访问。

## 2. 域名规划

建议使用两个子域名：

- `api.inventory.example.com`：小程序和后端 API。
- `admin.inventory.example.com`：PC 管理后台，仅管理员使用。

在域名 DNS 中添加两条 A 记录，指向云服务器公网 IP。

## 3. 上传代码

登录服务器后执行：

```bash
sudo useradd -r -m -d /opt/equipment_inventory inventory
sudo apt update
sudo apt install -y python3-venv python3-pip nginx certbot python3-certbot-nginx
sudo mkdir -p /opt/equipment_inventory
sudo chown -R inventory:inventory /opt/equipment_inventory
```

把本项目上传到 `/opt/equipment_inventory`。如果使用 git，可在服务器上拉取代码；如果手工上传，保证目录内有 `backend/`、`admin_web/`、`miniprogram/`、`requirements.txt`。

初始化 Python 环境：

```bash
cd /opt/equipment_inventory
sudo -u inventory python3 -m venv .venv
sudo -u inventory .venv/bin/pip install -r requirements.txt
```

构建 Vue PC 后台：

```bash
cd /opt/equipment_inventory/admin_web
sudo -u inventory npm install
sudo -u inventory npm run build
```

## 4. 生产环境变量

复制模板：

```bash
cp deploy/server.env.example /opt/equipment_inventory/.env
sudo chown inventory:inventory /opt/equipment_inventory/.env
sudo chmod 600 /opt/equipment_inventory/.env
nano /opt/equipment_inventory/.env
```

至少修改：

```bash
INVENTORY_SECRET_KEY=至少32位随机字符串
INVENTORY_ADMIN_PASSWORD=你的管理员强密码
INVENTORY_DEV_LOGIN=0
INVENTORY_ALLOW_LOGIN_REQUESTS=1
WECHAT_APPID=你的小程序AppID
WECHAT_SECRET=你的小程序AppSecret
```

生产试用建议关闭测试登录，即 `INVENTORY_DEV_LOGIN=0`。现场人员账号需要使用真实微信 `openid` 绑定。
`INVENTORY_ALLOW_LOGIN_REQUESTS=1` 表示未知微信用户首次登录时自动生成一个“待绑定用户”的停用账号；你在 PC 后台把姓名、片区改好并启用后，该人员即可登录。

## 5. systemd 服务

```bash
sudo cp deploy/systemd/equipment-inventory-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now equipment-inventory-api
sudo systemctl status equipment-inventory-api
```

本机验证：

```bash
curl http://127.0.0.1:8000/health
```

应返回：

```json
{"status":"ok"}
```

## 6. Nginx 与 HTTPS

复制 Nginx 模板：

```bash
sudo cp deploy/nginx/equipment-inventory.conf /etc/nginx/sites-available/equipment-inventory.conf
sudo nano /etc/nginx/sites-available/equipment-inventory.conf
```

把模板中的：

- `api.inventory.example.com` 改成你的 API 域名。
- `admin.inventory.example.com` 改成你的管理端域名。

启用配置：

```bash
sudo ln -s /etc/nginx/sites-available/equipment-inventory.conf /etc/nginx/sites-enabled/equipment-inventory.conf
sudo nginx -t
sudo systemctl reload nginx
```

签发 HTTPS 证书：

```bash
sudo certbot --nginx -d api.inventory.example.com -d admin.inventory.example.com
```

外网验证：

```bash
curl https://api.inventory.example.com/health
```

## 7. 修改小程序生产 API 地址

编辑：

```text
miniprogram/env.js
```

把：

```javascript
prodApiBase: "https://inventory.example.com/api",
```

改成：

```javascript
prodApiBase: "https://api.inventory.example.com/api",
```

开发版会继续走 `devApiBase`；体验版和正式版会自动走 `prodApiBase`。

## 8. 微信公众平台配置

进入微信公众平台的小程序后台：

- 开发管理
- 开发设置
- 服务器域名

至少配置：

```text
request 合法域名：https://api.inventory.example.com
```


## 9. 上传体验版

微信开发者工具中：

1. 打开 `miniprogram/` 项目。
2. 确认 `miniprogram/env.js` 的 `prodApiBase` 是 HTTPS API 域名。
3. 点击“上传”。
4. 在微信公众平台设置体验成员。
5. 生成体验版二维码给现场人员试用。

## 10. 试用前检查

- `https://api.inventory.example.com/health` 能访问。
- `https://admin.inventory.example.com` 能打开后台登录页。
- 管理员密码不是默认密码。
- 微信后台已配置 request 合法域名。
- 服务器安全组未开放 `8000`。
- `data/backups/` 已能自动生成 SQLite 备份。
- 只导入 1 到 3 个机房先试跑。

## 11. 现场人员首次绑定流程

1. 现场人员打开体验版，点击“微信一键登录”。
2. 如果后台没有这个微信用户，会提示“账号待管理员启用”。
3. 管理员打开 `https://admin.inventory.example.com`。
4. 进入“账号管理”，找到“待绑定用户”或停用账号。
5. 修改姓名、负责片区、手机号/工号，并勾选“账号启用”。
6. 现场人员重新登录，即可看到对应片区任务。
