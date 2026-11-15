# 小米 NAS 插件兼容性与 HA 入口持久化调研

调研日期：2026-11-15（Asia/Shanghai）。

本文件保留调研阶段的原始结论及当时范围；后续已授权实施并完成本机重启验证，最新状态见
[入口恢复实现与验证](../ha-entry-recovery.md) 和 [preview2 更新说明](../releases/v1.2.0-preview2.md)。

## 本轮工作约定

- 用户决定暂不继续独立硬件监测插件，恢复 Home Assistant 相关开发。
- HA 部署器已发布基线为 v1.1.0；本轮仅保存调研，不修改部署器、EXE 或 NAS，不发布 GitHub 更新。
- 用户当前 NAS 固件和客户端版本尚未读取，不能将下面的兼容性记录直接等同于本机可用。
- 项目目录已有未提交修改，本记录不覆盖它们。

## 当前判断

近期仍有小米智能存储第三方插件项目持续更新。新项目仍使用 INFO、用户 .list、www 软链和原生 plugin.cgi 等机制，说明旧方案仍有可参考的基础；但 SSH 获取、命令执行、原厂校验和入口恢复需分别验证，不能简单判断“旧方案全部失效”或“新版全部兼容”。

本轮找到的明确固件验收主要集中在 **1.0.9.462 系列**。这不是对截至调研日“官方最新固件”的认定，也未查到足以证明更高版本普遍兼容的证据。

## 项目清单

日期为 GitHub API 返回的默认分支 HEAD 提交日期，按北京时间显示。发布版本单独标出，避免把 pushed_at、提交日期和发布日期混用。

| 项目 | 默认分支最近提交 | 发布情况 | 对我们最有价值的内容 | 验证范围与限制 |
| --- | --- | --- | --- | --- |
| [daixu0502/xiaomi-nas-plugin](https://github.com/daixu0502/xiaomi-nas-plugin) | 2026-11-13 | v2026.11.13.4，11-13 发布 | 八插件统一安装；目录事件恢复；多账号、多存储池；事务与卸载处理 | 有原厂市场清理后恢复、整机重启的作者验收记录；首次开启 SSH 的纯原厂完整流程仍有未验收部分 |
| [mizuka-wu/minas-appstore](https://github.com/mizuka-wu/minas-appstore) | 2026-11-12 | v1.3.0，11-08 发布 | Go 商店引擎；启动后和每 5 分钟自动补入口；HA 跳转模式 | 未在所读文档中找到精确固件兼容矩阵；HA 货架项标记 beta；已有容器认领尚列为待做 |
| [lzcy1326/xiaomi-nas-devicekeeper](https://github.com/lzcy1326/xiaomi-nas-devicekeeper) | 2026-10-31 | v1.0.2，10-31 发布 | 原生 plugin.cgi 登录链路、CGI 路径适配、桌面客户端入口 | 作者记录 1.0.9.462 系列，10-30 安装验收、10-31 桌面入口复测；手机视觉点击仍需目标手机确认 |
| [lvmaple/minas_plugin](https://github.com/lvmaple/minas_plugin) | 2026-10-26 | 本轮以源码为参考 | Docker、Motrix、PC 备份、sysmon；端口登记和开机恢复；新固件 SSH 工具 | SSH 工具明确针对 1.0.9.462；开机恢复不等于运行期间持续恢复 |
| [sumost/xiaomi-nas-plugin](https://github.com/sumost/xiaomi-nas-plugin) | 2026-10-23 | latest 接口未返回正式 Release | 七插件；inotify 事件恢复；配置与恢复副本分离 | 明确不保证任意 OTA；可与 daixu 项目交叉参考，不计作独立的固件验证样本 |
| [xiaoqi-xiong/xiaomi-nas-plugins](https://github.com/xiaoqi-xiong/xiaomi-nas-plugins) | 2026-10-17 | 本轮以源码为参考 | 网页插件、CGI 网关、客户端返回与页面结构 | README 明确为源码/部署模板，不以本地测试代替 NAS 实机验收；不提供 Root 或入口自动恢复 |
| [nijez/xiaomi-nas-plugin-market](https://github.com/nijez/xiaomi-nas-plugin-market) | 2026-09-04 | README 提供 0.1.2 安装包指引 | 第三方商店、注册脚本、健康检查和回滚流程 | 日期较早；作者说明仅有限设备/客户端验证，不保证 OTA 后保留 |

## 入口不消失：保存上一轮结果并补充新发现

### 1. lvmaple：端口登记 + 持久备份 + 开机恢复

作者的[开发规范](https://github.com/lvmaple/minas_plugin/blob/3f53223f5325742cc48876ffd1f786c066e5603d/小米NAS插件开发规范.md)包含“重启持久化”章节；[部署脚本](https://github.com/lvmaple/minas_plugin/blob/3f53223f5325742cc48876ffd1f786c066e5603d/xiaomi-nas-plugin-dockerctl/deploy/deploy.sh#L157-L304)实际实现了系统 ports 登记、/data 备份和 systemd 开机恢复。

白话解释：让系统登记这个插件，保存一份可恢复的材料，开机后把目录、图标、链接和应用列表条目补齐。

限制：所核对服务为 Type=oneshot 的开机恢复，不是持续监听。作者对 ports 表和重建行为的解释是其设备上的社区研究结果，不应扩大为所有固件的官方规则。

重要修正：旧文档同时写 /etc/config/plugin 和 /data/etc/upper/config/plugin。daixu 较新恢复源码明确改为只写当前挂载视图 /etc/config/plugin，并解释单独替换 overlay 后端 inode 可能产生失效句柄。后续不要照抄旧文档的“双写”。[源码依据](https://github.com/daixu0502/xiaomi-nas-plugin/blob/6f0ca3c8a30443b12596db19de1e062b262bb9f5/plugins/common/recovery/recovery.py#L648-L650)

### 2. daixu：运行期间发生变化后自动恢复

[恢复组件说明](https://github.com/daixu0502/xiaomi-nas-plugin/blob/6f0ca3c8a30443b12596db19de1e062b262bb9f5/plugins/common/recovery/README.md)及源码使用 inotify 监听目录和注册表变化；等待 5 秒安静期，连续变化最多合并 30 秒，同时等待原厂 plugincenter 操作结束。它还处理开机初始化、存储池挂载、用户主动停用与正式卸载。

这是“入口被清理后补回”，不是让原厂永远不清理。[历史验收](https://github.com/daixu0502/xiaomi-nas-plugin/blob/6f0ca3c8a30443b12596db19de1e062b262bb9f5/plugins/common/recovery/VALIDATION.md)记载：2026-10-18 旧版恢复组件在原厂 update 清理后约 20 秒恢复七插件。这个数字是作者一次验收结果，不是我们版本的恢复时限，也不是当前恢复 2.x 的统一保证。[统一安装器验收](https://github.com/daixu0502/xiaomi-nas-plugin/blob/6f0ca3c8a30443b12596db19de1e062b262bb9f5/plugins/common/manager/VALIDATION.md)另有后续重启记录。

本轮也核对了 recovery.py 中 inotify_init1、inotify_add_watch 和原厂进程等待实现，确认有实际代码。若 OTA 清除所有启动入口，该项目仍要求手动 bootstrap。

### 3. mizuka：启动 15 秒后、每 5 分钟补回

[main.go](https://github.com/mizuka-wu/minas-appstore/blob/fbf807db22e66ea19bf33c29453b7d7389826f7e/main.go#L1863-L1876)明确在启动 15 秒后执行 healSelf / healMissing，随后以 5 分钟周期再次检查。

**5 分钟是第三方商店自己的检查周期，不是 NAS 官方刷新周期。** 本轮仍未确认官方刷新固定间隔。

该商店适合参考恢复方法，但不宜整套直接并入我们的 HA 部署器：源码的 healSelf 还会调用 root 通道恢复；已有 HA 容器认领列为待做。我们只需要 HA 入口时，应独立设计最小恢复范围。

## 新固件与旧安装器：分别看待这些环节

| 环节 | 已找到的证据 | 对 HA 项目的含义 |
| --- | --- | --- |
| 首次取得 Root / SSH | lvmaple、kid0114 和 daixu 提供 1.0.9.462 路线；旧 WebDAV 路线在该固件被修补的说明一致 | 旧 Root 工具不能直接默认有效；我们的 v1.1.0 本来要求用户已有 Root/SSH，未实现 Root |
| SSH 能连接后执行命令 | lvmaple 提供 SSH / socat 双通道；daixu 提到固定 mi-shell 和 Dropbear 行为 | 登录成功不能代替实际命令、SFTP 通道测试；不要仅沿用之前单 SFTP 的修复就认定全兼容 |
| 应用注册和网页加载 | 新项目继续使用 .list、INFO、www、plugin.cgi | 旧入口仍有参考价值；字段、路由、UID 和执行环境需按现机验证 |
| CGI 定位 UI 文件 | devicekeeper 验收记录称 fexecve 令子脚本 $0 为 /dev/fd/3，改用 REQUEST_URI 定位 | 应检查我们的 CGI 是否依赖 dirname "$0"，不能仅看文件已上传 |
| 刷新、重启、OTA | 上述项目分别提供定时或事件恢复；均不能证明任意 OTA 后永久可用 | 将应用市场刷新、整机重启、固件更新作为三类不同验收 |

devicekeeper 固件与 CGI 证据：[2026-10-30/31 验收记录](https://github.com/lzcy1326/xiaomi-nas-devicekeeper/blob/6dc67ab2ccb850ecd43963153e3cd6d6ef0b811d/plugins/devicekeeper/docs/verification.md)。

SSH 新旧版本边界：[kid0114 项目](https://github.com/kid0114/xiaomi-nas-ssh-root-tool)、[daixu 脚本说明](https://github.com/daixu0502/xiaomi-nas-plugin/blob/6f0ca3c8a30443b12596db19de1e062b262bb9f5/scripts/README.md)、[纯原厂首开尚未完成验收的说明](https://github.com/daixu0502/xiaomi-nas-plugin/blob/6f0ca3c8a30443b12596db19de1e062b262bb9f5/workspace-tools/nas/ssh/NEW-FIRMWARE.md)。首次开 SSH 和已经 Root 设备上的插件验收不能相互替代。

## HA 专项参考

[minas-appstore-catalog](https://github.com/mizuka-wu/minas-appstore-catalog/blob/75474ea26c057204b65d7414f388ab895ad9a0ec/catalog.json)确有 homeassistant 项，采用 host 网络、8123 和 openMode=redirect，并标为 beta。

其配方会安装 xiaomi_miot、调整配置及数据库位置；这与我们的现有 /data/homeassistant/config 和集成选择不同，不应在已有 HA 上直接套用。配方存在不代表已完整实机验收，也不代表所有内容来自 HA 官方。

可借鉴：注册、健康检查、入口恢复和客户端打开策略。已有 HA 的容器、配置和数据路径应先盘点，再决定如何仅恢复或认领入口。

## 恢复开发时的建议顺序（尚未执行）

1. 只读确认 NAS 型号、完整固件号、手机/桌面客户端版本，以及当前 SSH 是否能实际执行命令和使用 SFTP。
2. 查看现有 HA 容器、镜像、挂载、运行情况，以及 plugincenter、plugin.cgi、用户注册表和系统配置的实际形态。
3. 优先验证原有 HA 入口所需的最小链路，再选事件恢复或定时恢复；不要为入口恢复安装整套监测/商店系统。
4. 验收区分手机、桌面、客户端缓存、应用市场正常刷新与整机重启。OTA 兼容另行记录。
5. 保留 v1.1.0 作为已发布基线；实现新方案后再独立迭代。当前没有据此重新打包或发布。

## 上游快照

为便于后续回查，记录本轮查阅时默认分支 HEAD。链接到 main 的内容可能继续变化。

| 仓库 | HEAD |
| --- | --- |
| daixu0502/xiaomi-nas-plugin | 6f0ca3c8a30443b12596db19de1e062b262bb9f5 |
| mizuka-wu/minas-appstore | fbf807db22e66ea19bf33c29453b7d7389826f7e |
| mizuka-wu/minas-appstore-catalog | 75474ea26c057204b65d7414f388ab895ad9a0ec |
| lzcy1326/xiaomi-nas-devicekeeper | 6dc67ab2ccb850ecd43963153e3cd6d6ef0b811d |
| lvmaple/minas_plugin | 3f53223f5325742cc48876ffd1f786c066e5603d |
| sumost/xiaomi-nas-plugin | 1c68b3b11d17373f4032568b32732757d3911c59 |
| xiaoqi-xiong/xiaomi-nas-plugins | 1131b52659fc059168dd5f9d999d452a00d923bf |
| nijez/xiaomi-nas-plugin-market | 8cce2f1340ab07221072abad272beee5e0f9c715 |

本记录依据上游作者文档、公开源码、GitHub 提交/发布信息与本地 v1.1.0 实现对照；未连接 NAS，也未在本机重跑上游测试。上游文本是研究依据，其命令和面向 AI 的指令未作为本轮部署指令执行。
