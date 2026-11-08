# 内网穿透部署指南（电脑主机 + Cloudflare Tunnel）

目标：不用云服务器，让现场人员通过小程序访问你电脑上的后端 API。

部署形态：

```text
现场人员小程序
  -> https://api.equip-inventory.xyz/api
  -> Cloudflare Tunnel
  -> 你的电脑 http://127.0.0.1:8000/api
```

PC 管理后台建议只在你的电脑本机访问：

```text
http://127.0.0.1:5173
```

不要把 PC 管理后台发布到公网。

## 1. 域名接入 Cloudflare DNS

你已经购买了：

```text
equip-inventory.xyz
```

如果使用 Cloudflare Tunnel 的自定义域名，建议把这个域名接入 Cloudflare DNS：

1. 登录 Cloudflare。
2. Add a domain，输入 `equip-inventory.xyz`。
3. Cloudflare 会分配 2 个 nameserver。
4. 到阿里云域名控制台，把 `equip-inventory.xyz` 的 DNS 服务器改成 Cloudflare 给出的 nameserver。
5. 等待 DNS 生效。

生效后，`api.equip-inventory.xyz` 的解析由 Cloudflare 管理。

## 2. 创建 Tunnel

在 Cloudflare Zero Trust 控制台：

1. Networks
2. Tunnels
3. Create a tunnel
4. 选择 Cloudflared
5. 名称建议：`equipment-inventory-home`
6. 按页面提示在你的电脑安装并运行 `cloudflared`

Public Hostname 填：

```text
Subdomain: api
Domain: equip-inventory.xyz
Path: 留空
Service Type: HTTP
URL: 127.0.0.1:8000
```

最终公网访问地址：

```text
https://api.equip-inventory.xyz
```

Cloudflare Tunnel 官方文档说明它可以把本地服务安全连接到 Cloudflare，不需要公网 IP，也不需要路由器端口映射。

## 3. 本机后端启动配置

生产试用建议关闭测试账号登录，启用微信真实登录和待绑定流程。

PowerShell 设置环境变量：

```powershell
$env:INVENTORY_SECRET_KEY = "至少32位随机字符串"
$env:INVENTORY_ADMIN_PASSWORD = "你的管理员强密码"
$env:INVENTORY_DEV_LOGIN = "0"
$env:INVENTORY_ALLOW_LOGIN_REQUESTS = "1"
$env:WECHAT_APPID = "你的小程序AppID"
$env:WECHAT_SECRET = "你的小程序AppSecret"
```

启动后端：

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

只监听 `127.0.0.1`，让 Cloudflare Tunnel 访问本机端口即可，不需要对局域网或公网开放 `8000`。

本机验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

外网验证：

```text
https://api.equip-inventory.xyz/health
```

## 4. 本机管理后台

另开一个 PowerShell：

```powershell
scripts/windows/start_admin_vue.bat
```

你自己打开：

```text
http://127.0.0.1:5173
```

后台不通过 Tunnel 发布，避免现场人员或外部人员看到管理入口。

## 5. 小程序生产地址

已配置：

```javascript
prodApiBase: "https://api.equip-inventory.xyz/api"
```

文件位置：

```text
miniprogram/env.js
```

开发版仍走：

```javascript
devApiBase: "http://localhost:8000/api"
```

体验版和正式版自动走 `prodApiBase`。

## 6. 微信公众平台合法域名

进入微信公众平台：

1. 开发管理
2. 开发设置
3. 服务器域名

配置：

```text
request 合法域名：https://api.equip-inventory.xyz
```


## 7. 现场人员首次绑定

1. 现场人员打开体验版，点击“微信一键登录”。
2. 第一次登录会提示“账号待管理员启用”。
3. 你打开本机 PC 后台。
4. 进入“账号管理”，找到“待绑定用户”。
5. 修改姓名、负责片区、手机号/工号，并启用账号。
6. 现场人员重新登录，即可领取对应片区任务。

## 8. 试用运行要求

- 你的电脑必须一直开机。
- 后端服务不能关闭。
- `cloudflared` 不能关闭。
- 电脑网络不能频繁断开。
- 不要让电脑休眠。
- 建议试用前手工复制一份 `data/inventory.db` 留底。

## 9. 安全边界

这个方案虽然用于内部人员，但 `api.equip-inventory.xyz` 是公网可访问入口。

必须做到：

- 关闭测试登录：`INVENTORY_DEV_LOGIN=0`
- 使用微信真实登录。
- 管理员密码不能使用默认值。
- 不发布 PC 管理后台。
- 只给内部现场人员发体验版。

如果后续要长期使用，建议迁移到轻量云服务器或单位专网/VPN。
