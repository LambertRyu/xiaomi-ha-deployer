# 小米智能存储 Home Assistant 一键部署工具

面向小米智能存储的 Home Assistant 图形化部署工具。

## 功能

- 一键获取局域网内 NAS 的 SSH 信息
- 在小米智能存储自带 Docker 中部署 Home Assistant
- 使用独立目录 `/data/homeassistant/config` 保存配置、数据库和历史数据
- 自动使用 NAS 上的 Docker CLI：`/data/docker/docker`
- 支持安装或更新 NAS 内 Home Assistant 快捷入口
- 支持保留配置重建容器
- 支持通过填写镜像标签升级或降级 Home Assistant
- 支持卸载容器并选择是否保留配置

## 使用前提

使用本工具前，请先：

1. 完成一键 Root；
2. 完成 SSH 部署并开启 SSH 服务；
3. 打开小米智能存储客户端；
4. 确认 NAS 可以访问 GHCR。

本工具不会执行 Root、不会开通 SSH，也不会读取或尝试密码，只会探测局域网 SSH 服务，并使用用户填写的 SSH 信息完成部署。

## 使用方法

1. 下载 Releases 中的 `XiaomiHAInstaller.exe`；
2. 点击“1.一键获取 NAS SSH 信息”；
3. 输入 Root 密码；
4. 选择部署选项；
5. 点击“2.开始一键部署”。

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

## 注意事项

- 镜像标签必须真实存在，并支持 NAS 的 ARM64 架构；
- NAS 需要能够访问 GHCR；
- 初次部署前，请确认 NAS 的 8123 端口没有被其他服务占用；
- 本项目仅封装已验证的部署流程，实际部署仍受 NAS 固件、网络和镜像版本影响。

作者：Dovahkiin  
小红书：Dovahkiin
