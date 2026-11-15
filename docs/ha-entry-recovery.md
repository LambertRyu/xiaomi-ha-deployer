# HA 入口恢复：v1.2.0

## 目标和实际机制

保留 v1.1.0 的插件 ID、名称、图标、注册前端、启动页以及独立窗口打开行为。
INFO、注册模板、index.html 和 icon.png 的源模板未修改；CGI 增加页面定位兼容性。
运行时只替换启动页里的 NAS 地址，不改变页面样式、按钮或独立窗口打开方式。

本版在 NAS 上保存独立恢复材料，通过 Linux inotify 监听变化。目录被清理或应用列表
被替换后，等待约 5 秒安静期再补回；连续事件最多合并 30 秒。原厂 plugincenter 正在
运行时推迟处理；同时每 60 秒检查一次作为兜底。systemd timer 也能独立触发兜底检查。
刚开机时 watcher 等待系统启动至少 90 秒，timer 在开机 120 秒后开始。

另有每分钟执行的独立启动检查：如果监听服务或 timer 没有运行，就重新加载并启动
本项目的专用单元；两者都正常时直接退出。这样可以覆盖本机实测中“单元已启用，
重启后却没有启动”的情况。每分钟检查并不等于每分钟重写入口。

如果路由器重新分配 NAS 的局域网 IPv4 地址，程序会更新启动页内原有的
`http://旧地址:8123`，然后恢复入口。地址通过本机路由查询取得，不向公网发送请求；
用户自定义的域名地址保持不变。

这能处理原厂刷新后入口缺失，但无法保证客户端里一秒都不闪失。官方仍可能先清理，
程序随后恢复；客户端自身缓存可能需要重开客户端。60 秒也不是官方刷新周期。
固件升级如果清除了整个恢复程序或所有启动配置，仍需重新安装；不能承诺任意 OTA 通用。

## 安装范围

- 恢复程序、每用户登记、最多两代校验快照和最多十份应用列表备份：
  `/data/homeassistant-entry-recovery/`。
- 恢复的入口目录与旧版一致：`/nas/pool0/u<UID>/plugin/pluginsrc/homeassistant`、
  `/home/u<UID>/plugin/homeassistant`、`/data/plugin/www/u<UID>/homeassistant` 和对应图标。
- 专用 systemd 单元：`homeassistant-entry-recovery.service`、
  `homeassistant-entry-recovery-check.service`、`homeassistant-entry-recovery-check.timer`。
- 独立启动检查：`/etc/cron.d/homeassistant-entry-recovery`，每分钟检查上述服务状态。
- 不修改 `/etc/config/plugin` 或 overlay 后端，不调用 `plugincenter update`。
- 不修改 HA 容器、镜像、配置、数据库或 SSH，不安装第三方商店或 root 通道守护。

程序只恢复明确登记的用户与 homeassistant 条目；原子写入时保留官方插件和原文件归属。
用户列表尚未生成、JSON 损坏、存储池未挂载、快照摘要错误、插件 ID 冲突或写入路径存在
非预期符号链接时，停止该次恢复。官方程序不使用我们的锁，因此写入前也重读列表并
检查变化；若原厂恰好在最后一次检查后覆盖，后续检查会再次补回。

正式卸载或回滚前先停用该 UID 的恢复记录。仅在原厂应用市场移除入口时，程序可能
将其补回；需要停止恢复请使用部署器完整卸载、回滚，或下述 disable 命令。
在正常列表中观察到的 enable=false 会记录下来，后续补回不强制启用。

当前沿用 pool0 的原入口布局。若目标设备使用其他存储池或目录形态，本版会拒绝安装，
应先适配后再部署，不创建空池目录顶替挂载。

## 使用方式

需要已经能够以 root 通过 SSH 执行命令并使用 SFTP。恢复程序使用 NAS 自带 Python 3，
只依赖标准库、systemd 和原有插件环境。部署器不负责首次开启 Root/SSH。

已有 HA 时点击部署器的“只安装/修复入口（保留 HA）”。多用户设备建议填写当前客户端
显示的数字 NAS UID；留空时仍按原版的列表时间自动选择，并输出候选值供核对。

手动部署需先在资源副本中替换 __NAS_UID__ 和 __NAS_HOST__，再运行：

```sh
sh scripts/deploy.sh --dry-run --replace
sh scripts/deploy.sh --apply --replace
```

安装器只有在恢复服务及 timer 均 active 后才报告入口安装完成；失败会单独提示。

```sh
# 查看登记和服务状态
python3 /data/homeassistant-entry-recovery/entry_recovery.py status

# 手动检查并补回入口（会写入入口资源）
python3 /data/homeassistant-entry-recovery/entry_recovery.py reconcile

# 停用指定用户的恢复，同时移除该用户的注册条目；保留 HA 和其数据
python3 /data/homeassistant-entry-recovery/entry_recovery.py disable --uid <数字UID> --apply

journalctl -u homeassistant-entry-recovery.service --no-pager -n 50
```

## 当前验证状态

2026-11-15 已通过用户授权在客户端读取到 RP05、固件
`1.0.9.462.F24C26D24-REL`、桌面客户端 `1.0.9.503`。
用户随后明确授权恢复 SSH 并继续安装验证。已复用原来的电脑 SSH 密钥恢复 root SSH，
通过本次随机回执及真实 UID 0 验证；没有开启无认证网络 shell。
临时共享、任务与触发文件已清理，原有 SMB 账户状态保持不变。
NAS 已安装本版入口恢复组件，监听服务与独立 timer 均 active/enabled。

本地测试命令：

```text
python -m unittest discover -s homeassistant/ha_deployer/tests -bv
```

Windows 本地已通过 27 项测试，覆盖注册丢失及被标记卸载、源码/图标删除、其他用户和官方条目保留、
重复安装、缺失/损坏列表、损坏快照、主动禁用、卸载不复活、备份上限、服务配置和
写入期间列表变化，以及地址变化、自定义域名保留和独立启动检查。Windows 无目录符号链接权限，相关文件恢复测试仅替代符号链接
操作，其余文件和 JSON 操作为实际实现。三项 Linux 专用测试在 Windows 上跳过。

随后在 NAS 的隔离暂存目录运行 **30 项 Linux 回归，全部通过**，包括实际软链、
文件描述符 CGI、真实 inotify 以及严格 umask 下的恢复。测试通过后已清理隔离目录。

对实际已安装入口另作控制性验收，未调用会清除其他插件的 plugincenter update：

| 检查 | 结果 |
| --- | --- |
| 仅移除实际 HA 注册项，等待后台自动恢复 | 5.23 秒补回 |
| 移除 HA 的 UI 链接、图标及启动页，等待后台自动恢复 | 1.60 秒补回，页面摘要与快照一致 |
| 重启专用恢复服务 | 正常启动 |
| 停止事件监听后仅靠独立 timer 恢复注册项 | 63.63 秒补回（60 秒周期含调度延迟） |
| 官方应用注册条目 | 与验收前完全一致 |
| HA 容器、镜像与运行状态 | 原容器及镜像保持不变，持续 running |
| 桌面客户端应用市场 | 显示原 Home Assistant 图标和名称 |
| 重启前实际点击“打开 Home Assistant” | 原启动页正常，独立窗口显示现有 HA 概览 |
| 停止监听服务和 timer，等待独立启动检查 | 32.07 秒后两者自动恢复 |
| 本版整机重启后的后台启动 | 开机约 39.48 秒监听服务已启动，未重新安装 |
| 重启后路由器分配新地址 | 自动更新启动页地址并补回入口 |
| 重启后再次移除 HA 注册项 | 5.24 秒自动补回 |
| 重启后再次移除 UI 链接、图标及启动页 | 3.41 秒自动补回，官方条目未变 |
| 重启后桌面客户端点击入口 | 原图标和启动页可见，按钮打开独立 HA 登录窗口 |

这些时长是本机一次验收样本，并非所有固件下的恢复时限承诺。
已完成本机整机重启及桌面客户端打开验证；未触发整机 OTA，手机端仍需目标手机复验。
SSH 的开机持久化未在本轮安装，入口恢复运行本身不依赖 SSH 常开。

第一次整机重启发现两个问题：专用单元处于 enabled 但 inactive，且 NAS 的 DHCP 地址
发生变化。本版因此增加独立启动检查和地址跟随；原先未启动的具体固件内部原因
没有足够日志证实，不能直接认定由某个挂载阶段造成。

第二次整机重启在 2026-11-15 17:14:17 发起（北京时间），NAS 再次获得新地址。
恢复日志记录服务于 17:15:06 启动，17:15:55 更新入口地址，17:15:56 补回入口。
这些记录均早于随后恢复 SSH 和读取验证结果，未靠再次运行安装器完成恢复。
HA 的原容器 ID、镜像、配置挂载及自动重启策略均保持不变，重启后正常运行。

真机发现并修复：服务 UMask=0077 会令新建的公共 UI 父目录不可读。
现在只对程序新建的入口父目录显式设置 0755，不放宽已有系统目录。
裸 mTLS 请求插件 URL 仍需原生客户端的登录环境；其返回 403 不等于客户端入口失效，
本轮最终以真实桌面客户端显示和打开结果验收，没有为此绕过认证或改动 Nginx。

EXE 的 `--self-test` 只验证内置资源、Python 代码和隐藏的部署器窗口构建，不连接 NAS。
正式安装包输出到 `homeassistant/ha_deployer/dist/v1.2.0/`，已发布 v1.1.0 不被覆盖。

正式版 EXE 已用 v1.2.0 版本号重新构建，并实际执行 `--self-test`，退出码为 0。
安装包包含权限、重启启动和地址跟随修复。
正式安装包 SHA-256：`6B8E586A8214E75D5D19A36F45776E1B0F026C18862AC491CBD5BF4D470AE622`。
原版 EXE SHA-256 仍为 `A122BBFC5A18DE0CA7E8502ECBA3E2CE204760CA68691A90101B68F31FB96BB5`。

## 研究依据

实现为本项目新增代码，参考了[调研记录](research/2026-11-15-xiaomi-nas-plugin-compatibility.md)
中的事件恢复与周期检查思路，没有复制整个上游安装器或加入其 SSH/root 守护逻辑。
