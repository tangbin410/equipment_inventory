# 资产盘点系统：运行包部署说明

版本：2026-11-12。这个目录是可分发的 Docker 构建上下文，不是完整开发工程，也不是 `docker load` 可导入的镜像文件。

包含编译后的 Python 3.12 业务字节码、构建好的管理后台、必要模板及固定依赖清单。没有开发源码、真实数据库、个人配置、密钥或真实任务资料。第三方库和 Python 标准库仍按正常方式安装，可能包含其自己的 `.py`。

## 1. 构建

进入解压后的本目录执行：

```bash
docker build -t equipment-inventory:2026.11.12 .
```

构建需要网络下载 Python 基础镜像及依赖。默认构建所在机器的架构。若最终部署在另一架构，可在原打包工程外使用 Docker Buildx 构建目标平台，例如：

```bash
docker buildx build --platform linux/amd64 --load -t equipment-inventory:2026.11.12 .
```

不要用包含开发源码的整个工程目录代替此构建上下文。Dockerfile 只复制 `app/` 运行产物，编译不在 Docker 构建器中进行。

## 2. 配置与启动

```bash
cp .env.example .env.runtime
chmod 600 .env.runtime
```

编辑 `.env.runtime`，至少填写：

- `INVENTORY_SECRET_KEY`：自己生成的固定随机字符串，至少 32 字符；可用 `openssl rand -hex 32` 生成，不用示例固定值。
- `INVENTORY_ADMIN_PASSWORD`：首次初始化管理员密码，12—128 字符。账号为 `admin_001`。不使用镜像内默认密码。

然后执行：

```bash
docker compose up -d
```

浏览器打开 **http://127.0.0.1:8080**，同一入口同时提供管理网页及 `/api`。不需要 Node、Vite 或另起前端服务。内部 AI Agent 在容器的回环 9000 端口启动，不向宿主机发布。

未配置模型密钥时，普通盘点、资料比对、拼销、导出及人工整改可使用，AI 分析提示未配置。需要 AI 时填写自己的 `DEEPSEEK_API_KEY` 和模型名。真实微信登录需要部署方自己的 `WECHAT_APPID`、`WECHAT_SECRET`、合法 HTTPS 域名及小程序发布配置；二维码公开链接需要实际的 `INVENTORY_PUBLIC_BASE_URL`。运行包不附带原作者的密钥、域名开通权限或现场小程序源工程。

## 3. 数据与访问边界

- 初次启动生成空业务数据库，初始化管理员和现有程序的基础人员账号。基础测试人员不能通过生产环境测试登录，须按实际账号流程维护。
- 命名卷 `inventory-data` 持久化 `/app/data`，包括数据库和程序备份。不要把该卷上传到公开仓库，不要执行 `docker compose down -v` 清空业务数据。
- 容器使用 UID/GID 10001 非 root 运行。改用宿主机目录挂载时，部署方需给该用户写权限；不建议使用 `chmod 777`。
- 网页和 API 同源。容器版为便于 Docker 网桥访问，采用后台账号密码及权限验证，关闭原本仅允许回环来源的 IP 限制；Compose 因此默认仅发布到宿主机回环地址。
- 不要直接将这个默认部署暴露到互联网。异地访问需增加 HTTPS 和单位认可的访问网关；原有 Cloudflare Access 代码仍保留，但必须由部署方正确配置专用域名、邮箱名单、AUD 和路由，并将 `INVENTORY_ADMIN_IP_RESTRICTION_ENABLED` 恢复为 `1`，防止通过其他主机名绕过网关。启动器会拒绝同时启用 Access、关闭 IP 保护的配置，运行包不会自动沿用作者的云端策略。
- 运行包没有修改原作者电脑的 `.env`、模式或隧道。个人电脑关机导致的原部署问题，也不会由“有了镜像”自动消失；目标运行主机仍须常开且网络可用。

## 4. 更新与排查

```bash
docker compose logs --tail=100 inventory
docker compose ps
```

健康地址：`http://127.0.0.1:8080/health`，正常返回包含 `status: ok` 的 JSON。网页能打开还需验证管理员登录和数据接口，不仅查看健康地址。

新版本先备份数据，再替换运行包、重新构建镜像和启动。数据库升级沿用程序既有增量升级机制，不把已有数据库烘焙进新镜像。更换 `.env.runtime` 后需要重建容器进程加载配置；改环境密码不会覆盖已存在管理员密码，密码重置按实际账号管理流程处理。

## 5. 再分发

可以将构建出的镜像推送到自己有权使用的镜像仓库，也可导出为镜像文件：

```bash
docker save equipment-inventory:2026.11.12 -o equipment-inventory-image.tar
```

另一台机器执行 `docker load -i equipment-inventory-image.tar`，再放置 `compose.yaml` 和自己填写的 `.env.runtime` 启动。Compose 不会自动下载作者的私人镜像；自行设置镜像仓库地址。

**可以分发：**未填写配置的本运行包、构建出的干净镜像、镜像导出文件。

**不能公开分发：**填写过的 `.env.runtime`、密钥、口令、数据库／数据卷、备份、现场导出及业务资料。

`.pyc` 仍可能被反编译，编入字节码的提示词和业务字符串也不是加密秘密。浏览器必然能接收前端 JavaScript；本运行包不附 Vue/TypeScript 源工程和 source map，但不承诺前端或后端逻辑不可分析。

## 6. 校验与验证范围

`SHA256SUMS` 记录发布文件哈希。可用 `sha256sum -c SHA256SUMS`（macOS 对应用 `shasum -a 256 -c SHA256SUMS`）检查文件是否完整。

本版已完成 `linux/amd64` 镜像构建，并在 Mac Docker Desktop 上运行独立容器，验证网页、登录、模板、健康、内部 Agent 路由及数据卷重建保留。实体 Windows、部署方网络、真实微信与模型配置仍需部署方验证。这个构建运行包本身仍不是镜像导出文件，不能直接 `docker load`；成品镜像交付见作者提供的独立镜像交付 ZIP。
