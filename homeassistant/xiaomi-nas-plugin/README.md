# Home Assistant 小米 NAS 静态入口插件与入口恢复

这是一个 `static-only` 插件：小米 NAS App 中显示 **Home Assistant**，点击后
跳转到 `http://<NAS-IP>:8123`。页面和打开方式保持原版。
v1.2.0 安装独立的 systemd 入口恢复服务，在原厂清理后补回入口；
每分钟独立检查服务是否启动，并在 NAS 的局域网 IP 变化后自动更新入口目的地址。
恢复程序不启动或重建 Home Assistant，不修改 Docker、`miot_central`、Nginx 或 SSH。
范围、恢复周期和验收状态见 [入口恢复说明](../../docs/ha-entry-recovery.md)。

## 运行时配置

- NAS 账号 UID：部署器从最近刷新的 `/data/plugin/u<UID>.list` 自动选择；
- NAS 局域网地址：部署器使用当前 SSH 目标地址；
- Home Assistant 地址：`http://<NAS-IP>:8123`；
- 插件 ID/目录名：`homeassistant`
- 入口：`/index.html`

源码中的 `__NAS_UID__` 和 `__NAS_HOST__` 是发布占位符，打包部署器会在上传前
自动替换。手动部署时，请先复制一份目录，并在副本的文本文件中将两个占位符分别
替换为数字 UID 和 NAS 局域网 IP；不要直接修改作为发布源的模板。

这个入口只适合局域网。小米官方中继/远程访问是否允许 WebView 跳转到局域网
HTTP 地址，取决于客户端和固件，不能从本地静态检查中保证。

## 入口不是 HA 本体

小米 NAS 自带的插件目录会独立刷新，并可能重新生成用户的 `u<UID>.list`。本入口是
第三方静态注册项，不在小米官方插件目录中，因此以后仍可能被刷新掉。关闭固件自动
更新也不能阻止所有插件目录刷新。

入口消失不会停止或删除 Home Assistant Docker 容器，也不会影响
`/data/homeassistant/config`。本入口仅作补充，日常使用推荐 Home Assistant 官方 App、
浏览器 `http://NAS地址:8123`、书签或 HA 官方远程访问方式。

## 文件

```text
homeassistant/xiaomi-nas-plugin/
├── INFO
├── registration/homeassistant.entry.json
├── scripts/
│   ├── deploy.sh
│   ├── entry_recovery.py
│   ├── merge_list.py
│   └── rollback.sh
└── ui/
    ├── homeassistant.cgi
    ├── icon.png
    ├── index.html
    └── plugin.cgi
```

`INFO` 和 `.list` 条目的字段保持一致。当前固件的正常应用使用绝对图标路径，且
`frontend.url` 必须是带 `dev_type` 的数组；本插件按这个结构注册，避免应用被归入
市场列表或打开白页。`resource.mpk` 指向真实 `INFO`，不是空值。

## 部署

将已替换占位符的 `homeassistant/xiaomi-nas-plugin` 目录复制到 NAS 的持久化路径后，
在 NAS 上执行。默认只显示计划，不写文件：

```sh
cd /path/to/homeassistant/xiaomi-nas-plugin
sh scripts/deploy.sh --dry-run
```

确认输出的 UID、路径和已有插件列表都正确后，才执行：

```sh
sudo sh scripts/deploy.sh --apply
```

脚本会：

1. 在独立恢复目录中保存 `/data/plugin/u<UID>.list` 备份及经过校验的入口快照；
2. 把 `INFO` 和 UI 放到 `/nas/pool0/u<UID>/plugin/pluginsrc/homeassistant`；
3. 创建标准用户插件入口 `/home/u<UID>/plugin/homeassistant`，复制 `INFO`，
   并让 `src` 指向插件源码；插件文件归属设为 `u<UID>`，确保系统 CGI 能读取；
4. 创建或恢复 `/data/plugin/www/u<UID>/homeassistant` UI 符号链接；
5. 复制图标到 `/data/plugin/www/icon/homeassistant.icon`；
6. 原子合并 `.list`，只新增或在显式 `--replace` 时替换 `homeassistant` 条目。

如果目标 `.list` 已经有 `homeassistant`，脚本默认拒绝覆盖；先查看条目，再按需
执行：

```sh
sudo sh scripts/deploy.sh --apply --replace
```

本版的 `deploy.sh` 改为调用 `scripts/entry_recovery.py`，上述目录和注册结构保留，
备份改存于 `/data/homeassistant-entry-recovery/users/<UID>/list-backups/`（最多 10 份）。
安装时额外建立专用恢复服务、兜底 timer 和 `/etc/cron.d/homeassistant-entry-recovery`
独立启动检查；不会触碰 Docker 或 HA。本机当前固件的整机重启验证已通过，手机端及后续 OTA 仍待验证。
同一用户重复安装时最多保留两代恢复快照。原有 `.bak-*` 不会被删除。

## 回滚

回滚只恢复注册文件，并保留插件文件以便检查：

```sh
sudo sh scripts/rollback.sh \
  --backup /data/plugin/u<UID>.list.bak-YYYYMMDD-HHMMSS \
  --apply
```

回滚脚本在覆盖前还会生成 `.rollback-*` 安全备份。
存在入口恢复记录时，会先将其停用，避免回滚后又自动补回。

## 验证

部署后应在 NAS 上验证：

```sh
plugincenter -u u<UID> list | grep -F homeassistant
su -s /bin/sh u<UID> -c \
  'test -r /data/plugin/www/u<UID>/homeassistant/index.html'
env REQUEST_METHOD=GET \
  REQUEST_URI=/<UID>/homeassistant/index.html \
  USER=u<UID> /usr/bin/plugin.cgi
curl -I http://<NAS-IP>:8123
```

App 中最终应能看到图标和 “Home Assistant”，点击后进入 HA 登录页。若插件列表
已识别但入口 404，应优先检查通用 `/plugin` 路由是否能提供插件 UI、UI 符号链接和
`homeassistant.cgi` 的执行权限；本方案没有修改 Nginx，因此不会改变固件原有路由。
