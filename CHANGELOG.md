# 更新记录

## v1.1.0 - 2026-09-16

- 支持 Windows SSH Agent 和本机默认 OpenSSH 私钥，兼容仅允许 `publickey` 的 NAS；
- 修复 Dropbear 环境下重复开关 SFTP 通道导致 `Channel closed` 的问题；
- 支持多用户 NAS，根据客户端最近刷新的插件列表选择正确 UID；
- 改进认证失败、临时目录清理和 SFTP 关闭异常提示；
- 明确 NAS 官方插件刷新可能移除第三方快捷入口，但不会影响 HA 容器和配置；
- 推荐将 NAS 快捷入口仅作为补充，优先使用 Home Assistant 官方 App 或浏览器入口。

## v1.0.0

- 首个公开版本；
- 支持部署、更新、重建、卸载 Home Assistant；
- 支持安装小米 NAS 内的 Home Assistant 静态快捷入口。
