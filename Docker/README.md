# Docker 打包文件说明（作者本机）

整理日期：2026-11-12。后端字节码与后台网页一起打包，所有新增打包资料位于本文件夹。没有修改当前运行服务、真实配置或数据。

已安装 Docker，`linux/amd64` 镜像已构建并通过真实容器验收。**现在建议发送 `output/image-linux-amd64/` 中的三个文件**：镜像交付 ZIP、SHA256SUMS、使用说明。对方导入镜像后配置启动，不需再构建。原 `output` 根目录的小压缩包仍保留为“构建运行包”，两者不要混淆。

源码更新不会自动更新已有 `release/`、压缩包和镜像。每次交付新版本前，应重新生成运行包、构建并验收目标镜像；本次代码检查没有重建此前镜像。

## 目录与上传清单

| 路径 | 作用 | 是否对外上传 |
|---|---|---|
| `output/image-linux-amd64/` 中的三个交付文件 | 真正的 amd64 镜像、无构建启动配置、接收方说明及校验 | 可以，当前优先发送这一组 |
| `output/equipment-inventory-runtime-py312.tar.gz` | 已整理的无应用源码运行包，解压后可在 Docker 主机构建 | 可以，这是建议发送的文件 |
| `output/SHA256SUMS` | 上述压缩包的完整性校验值 | 可以，建议一起发送 |
| `output/使用说明-请先阅读.md` | 面向接收方的环境准备、校验、解压、构建、配置及登录说明 | 可以，与前两项一起发送 |
| `release/` | 与压缩包相同的发布目录，含 Dockerfile、网页、字节码及配置空模板 | 可以，但不要再往里面放真实配置和数据 |
| 构建后的镜像／`docker save` 文件 | 实际可运行镜像，不含创建容器后产生的数据卷 | 可以，本次已构建并验收 |
| `prepare_release.py`、`prepare_release_mac.command` | 本机读取白名单源码并编译，生成发布目录 | 留本机，不需要分发 |
| `runtime/*.py` | 容器启动适配的开发源文件，发布时只编入对应 `.pyc` | 留本机，不放入对外运行包 |
| `requirements.in`、`requirements.lock.txt` 等母版 | 维护依赖和发布配置 | 干净模板可分享，通常只发运行包即可 |
| `private/`、真实 `.env.runtime` | 本机隔离测试环境／部署者私有配置 | 不上传 |

不要为了方便上传整个 `equipment_inventory`，也不要上传整个 `Docker` 文件夹。直接使用镜像时发送 `output/image-linux-amd64/` 内的三个文件；让对方自行构建时，才发送 `output` 根目录的原三个文件。

## 一键生成运行包

访达双击 `Docker/prepare_release_mac.command`，或从项目根目录运行：

```bash
./Docker/prepare_release_mac.command
```

需要本机 CPython 3.12、前端 npm 依赖及当前后端依赖。默认用项目 `.venv/bin/python`，也可设置 `INVENTORY_PACK_PYTHON` 指向其他已装依赖的 CPython 3.12。

脚本仅执行这些操作：

1. 按白名单读取 `backend`、`local_agent` 的 Python，移除注释和 docstring，保留运行校验，输出相邻布局 `.pyc`。
2. 将三份运行提示词编入字节码，运行包不保留原始 Markdown；不触碰原文件。
3. 在本机用 `VITE_API_BASE=/api` 构建后台，不输出 source map，不带前端源码或 node_modules。
4. 保留必须的 `templates/inventory_template.xlsx`，复制干净的 Docker 配置和空配置模板。
5. 检查产物、压缩 `release/`，自动将接收方说明放入 `output/`，并记录压缩包和说明的 SHA256。不读取根目录 `.env`，不复制真实数据库、文档、草稿、测试或编辑器配置。

临时编译源码只存在本机临时目录，不进入 Docker 上下文、构建缓存或任何镜像层。应用代码异常回溯使用 `/app/...`，不包含作者本机绝对路径。

## 一条镜像构建命令

在已装好并运行 Docker 的机器上，从项目根目录执行：

```bash
docker build -t equipment-inventory:2026.11.12 ./Docker/release
```

收到压缩包的人，解压后进入其中目录执行 `docker build -t equipment-inventory:2026.11.12 .`。**最后的上下文必须是发布目录，不能改成项目根目录。**

`prepare_release_mac.command` 只生成“构建运行包”。真实镜像可按上面命令构建；本次按用户要求使用 `linux/amd64` 完成构建及容器验收。配置、启动、持久化和镜像导出步骤见 `release/README.md`（母版为 [PUBLISH_README.md](PUBLISH_README.md)）。

本机可执行 `.venv/bin/python Docker/test_release.py` 检查字节码包、同源网页和临时数据库业务流程。该验证不连接真实模型、不修改现有数据库，不能代替目标 Linux Docker 容器验收。

真实镜像验收使用 `Docker/test_image.py`；通过后用 `Docker/export_image.py` 导出镜像交付 ZIP。Mac 的 Docker 命令尚未配置进 PATH 时，可通过 `--docker /Applications/Docker.app/Contents/Resources/bin/docker` 指定程序；本机 Docker socket 连接只在本次命令中指定，不修改全局环境。

## 本机哪些内容不会进镜像

原始 `backend/**/*.py`、`local_agent/**/*.py` 和 `local_agent/prompts/*.md`，前端 `admin_web/src/`、测试、`miniprogram/` 源工程、`docs/`、Mac/Windows 脚本、`.git`、`.vscode`、真实 `.env`、`data/`、日志及备份均留在原位置。编译不删除、改写这些原件。

如果要迁移真实数据，须另行做有权限控制的备份迁移，不能把数据库打入公开镜像。真实微信、AI、二维码域名与异地访问要在新部署单独配置，不能向别人发送原作者的密钥。

## 保护范围

“无 `.py`”针对 `/app` 下自研业务代码。Python 标准库及第三方依赖仍正常安装，通常保留它们的 `.py`。字节码和压缩后的网页并非不可还原；本方案的目标是不给出完整、整洁、带注释的源工程。

Dockerfile 使用双阶段依赖安装，但两个阶段都没有应用源码；最终只复制编译后的 `app/`。`.dockerignore` 再按白名单限制输入。本方案不通过“先 COPY 源码再删除”隐藏源文件。

官方说明：[Docker 构建上下文](https://docs.docker.com/build/concepts/context/)、[Python 字节码编译](https://docs.python.org/3.12/library/py_compile.html)、[Python 无源码模块加载](https://docs.python.org/3.12/library/importlib.html#importlib.machinery.SourcelessFileLoader)。
