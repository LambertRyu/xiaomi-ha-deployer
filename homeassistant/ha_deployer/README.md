# XiaomiHAInstaller v1.1.0

这是一个 Windows 单文件部署器，前提是小米智能存储已经完成 ROOT，且 SSH 已经由外部工具开通。

它不会执行 ROOT、不会修改 SSH 配置，也不会依赖 NAS 上安装 `docker compose`。部署器通过 SSH 直接调用小米 NAS 上实际存在的 `/data/docker/docker`，完成：

- 检查 ARM64/aarch64 架构和 Docker CLI；
- 创建 `/data/homeassistant/config`；
- 拉取 `ghcr.io/home-assistant/home-assistant:2026.2.3`；
- 以 host 网络、自动重启、上海时区、60 秒停止时间创建 HA 容器；
- 检查容器状态、启动日志和本机 8123 响应；
- 可选安装小米 NAS 内的 Home Assistant 快捷入口插件。
- 自动扫描本机局域网的常见 SSH 端口，并自动填入发现的 NAS 地址和端口。
- 优先使用 Windows SSH Agent 或本机默认 OpenSSH 私钥登录，并兼容密码登录。
- 支持卸载 HA 容器：可以保留配置，也可以二次确认后完整清理配置、历史数据和 NAS 快捷入口。
- NAS 存在多个用户时，选择打开客户端后最近刷新的用户插件列表，避免入口写入错误 UID。
- 复用单一 SFTP 通道，兼容小米 NAS 使用的 Dropbear SSH。

快捷入口插件资源通过 PyInstaller 嵌入 EXE，运行时会根据目标 NAS IP 和 UID 动态生成并上传；部署脚本会在修改插件列表前创建带时间戳的备份。

## 构建

在有网络的 Windows 机器上执行：

```powershell
cd .\homeassistant\ha_deployer
.\build.ps1
```

输出文件：

```text
homeassistant\ha_deployer\dist\XiaomiHAInstaller.exe
```

生成的 EXE 包含 Python 运行时、Paramiko SSH 客户端、加密库和 NAS 插件资源；用户电脑不需要安装 Python。

## 使用前提

- 已运行外部的一键 ROOT 工具；
- SSH 服务已开启；
- 可以用 root 用户登录 NAS；
- NAS 能访问 GHCR 拉取 Home Assistant 镜像；
- 初次部署时不要让其他服务占用 NAS 的 8123 端口。

自动发现功能只能得到 NAS 地址和 SSH 端口，不会尝试登录。正式部署时会优先使用
Windows SSH Agent 中的密钥或用户目录 `.ssh` 下的默认 OpenSSH 私钥；因此，像只允许
`publickey` 的 NAS 也可以登录，密码输入框可以留空。如果 NAS 同时允许密码登录，也可
填写运行 ROOT/SSH 工具时设置的密码作为兼容方式。

部署器不会把私钥打包进 EXE，也不会上传、修改或记录私钥。使用前建议先在 Windows
命令行确认 `ssh root@NAS地址`（非 22 端口时加 `-p 端口`）能够成功登录。

## 快捷入口稳定性说明

小米 NAS 会在插件目录刷新时重新生成或覆盖官方插件列表。Home Assistant 快捷入口
属于第三方静态注册项，不在官方插件目录中，因此可能在某次刷新后从 NAS App 中消失。

入口消失只代表图标和跳转入口被移除，不会删除 `homeassistant` Docker 容器，也不会
删除 `/data/homeassistant/config` 中的配置、数据库和历史数据。NAS 快捷入口仅作为补充，
推荐日常优先使用 Home Assistant 官方 App、浏览器 `http://NAS地址:8123`、书签或官方
远程访问入口。关闭 NAS 固件自动更新也不能保证入口不消失，因为插件刷新可以独立发生。

如果入口消失，重新运行部署器并勾选“安装/更新快捷入口”即可；已有 HA 正常运行时不要
勾选“重建容器”。

首次连接会自动接受 SSH 主机密钥，并在日志中显示指纹。建议只在确认 NAS 地址无误的局域网环境中使用。
