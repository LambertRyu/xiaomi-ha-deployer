# 小米智能存储 Home Assistant 一键部署工具

面向小米智能存储的 Home Assistant Windows 图形化部署工具。当前版本：`v1.1.0`。

## 功能

- 一键发现局域网内 NAS 的 SSH 地址和端口；
- 支持 Windows SSH Agent、本机默认 OpenSSH 私钥和密码登录；
- 在小米智能存储自带 Docker 中部署 Home Assistant；
- 使用 `/data/homeassistant/config` 持久保存配置、数据库和历史数据；
- 支持保留配置重建容器，以及通过镜像标签升级或降级；
- 支持安装、更新和移除 NAS 内的 Home Assistant 快捷入口；
- 兼容 NAS 的 Dropbear SSH，上传入口资源时复用单一 SFTP 通道；
- NAS 存在多个用户时，根据打开客户端后最近刷新的用户插件列表选择 UID。

## 重要：NAS 快捷入口可能消失

小米 NAS 固件会定期或在打开客户端、进入应用市场等场景下刷新官方插件目录。刷新时，系统可能重新生成或覆盖 `/data/plugin/server.list` 和用户对应的 `/data/plugin/u<UID>.list`。Home Assistant 快捷入口是第三方静态注册项，不在小米官方插件目录中，因此可能在某次刷新后被移除。

这只会影响小米 NAS 客户端里的图标和入口，**不会删除 Home Assistant Docker 容器，也不会删除 `/data/homeassistant/config` 中的配置、数据库和历史数据**。即使入口消失，仍可通过以下方式正常使用 HA：

- Home Assistant 官方 Android / iOS App；
- 浏览器访问 `http://NAS局域网IP:8123`；
- 自己保存的书签、桌面快捷方式或已配置的 HA 官方远程访问入口。

因此，NAS 内快捷入口仅作为方便使用的补充，不能当作唯一入口。推荐优先使用 Home Assistant 官方 App 和浏览器地址。关闭 NAS 固件自动更新也不能保证入口永远保留，因为插件目录刷新可以在当前固件内独立发生。

入口被刷新掉后，可以重新运行部署器，只勾选“安装/更新快捷入口”，不要勾选“重建容器”。

## 使用前提

使用本工具前，请先：

1. 完成一键 Root；
2. 完成 SSH 部署并开启 SSH 服务；
3. 打开小米智能存储客户端，使当前用户列表成为最近刷新的列表；
4. 确认 NAS 可以访问 GHCR。

本工具不会执行 Root，也不会修改 SSH 服务配置。自动发现阶段只探测 SSH 服务，不会尝试登录。

## 使用方法

1. 下载 Releases 中的 `XiaomiHAInstaller.exe`；
2. 保持小米智能存储客户端打开；
3. 点击“1.一键获取 NAS SSH 信息”；
4. 优先使用本机 SSH 公钥；NAS 允许密码登录时也可填写 Root 密码；
5. 选择部署选项，点击“2.开始一键部署”。

部署完成后，通过 `http://NAS局域网IP:8123` 访问 Home Assistant。

## 升级或降级

在“HA 镜像”中填写目标镜像，例如：

```text
ghcr.io/home-assistant/home-assistant:2026.2.3
```

已有容器需要同时勾选“已有 homeassistant 容器时重建容器（保留 /data/homeassistant/config）”，程序才会拉取新镜像并重建容器。

## 卸载

“卸载HA”提供两种方式：

- 卸载容器，保留配置；
- 完整卸载，删除容器、配置目录和 NAS 快捷入口。

完整卸载会二次确认。配置和历史数据删除后无法通过本工具恢复，请提前备份。

## v1.1.0 更新

- 修复 NAS 只允许 `publickey` 时无法登录的问题；
- 修复 Dropbear 在多次开关 SFTP 通道后返回 `Channel closed` 的问题；
- 修复多用户 NAS 误选第一个 UID、导致入口安装成功但当前 App 看不到的问题；
- 改进认证、临时文件清理和错误提示；
- 补充 NAS 官方插件刷新可能移除第三方入口的风险说明。

## 注意事项

- 镜像标签必须真实存在，并支持 NAS 的 ARM64 架构；
- NAS 需要能够访问 GHCR；
- 初次部署前，请确认 NAS 的 8123 端口没有被其他服务占用；
- 本项目仅封装已验证的部署流程，实际部署仍受 NAS 固件、网络和镜像版本影响。

作者：Dovahkiin  
小红书：Dovahkiin
