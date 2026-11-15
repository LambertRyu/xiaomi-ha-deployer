from __future__ import annotations

import ipaddress
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
from typing import Callable

import paramiko
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk


APP_VERSION = "1.2.0-preview2"
APP_TITLE = f"小米智能存储 · Home Assistant 一键部署 v{APP_VERSION}｜小红书：Dovahkiin"
DEFAULT_IMAGE = "ghcr.io/home-assistant/home-assistant:2026.2.3"
DOCKER = "/data/docker/docker"
CONFIG_DIR = "/data/homeassistant/config"
CONTAINER = "homeassistant"


class DeployError(RuntimeError):
    pass


def asset_root() -> Path:
    bundled = Path(getattr(sys, "_MEIPASS", ""))
    candidates = []
    if bundled:
        candidates.append(bundled / "xiaomi-nas-plugin")
    candidates.append(Path(__file__).resolve().parents[1] / "xiaomi-nas-plugin")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise DeployError("找不到内置的小米 NAS Home Assistant 入口插件资源。")


class SshDeployer:
    def __init__(self, log: Callable[[str], None]):
        self.log = log
        self.client: paramiko.SSHClient | None = None

    def connect(self, host: str, port: int, username: str, password: str) -> None:
        self.log(f"正在连接 SSH：{host}:{port}，用户：{username}")
        self.log("SSH 认证：优先使用 Windows SSH Agent 或本机默认 OpenSSH 私钥；密码仅作兼容。")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password or None,
                timeout=12,
                auth_timeout=12,
                banner_timeout=12,
                allow_agent=True,
                look_for_keys=True,
            )
        except paramiko.BadAuthenticationType as exc:
            client.close()
            allowed = ", ".join(exc.allowed_types) if exc.allowed_types else "未知"
            if "publickey" in exc.allowed_types:
                raise DeployError(
                    "SSH 认证失败：NAS 只允许公钥登录，但没有找到可用的本机密钥。"
                    "请先确认 Windows 命令行可以直接执行 "
                    f"ssh -p {port} {username}@{host} 登录。"
                    f"服务器允许的认证方式：{allowed}。"
                ) from exc
            raise DeployError(f"SSH 认证方式不受支持；服务器允许：{allowed}。") from exc
        except paramiko.AuthenticationException as exc:
            client.close()
            raise DeployError(
                "SSH 认证失败：本机 SSH Agent、默认 OpenSSH 私钥和填写的密码均未通过。"
                "请先确认 Windows 命令行可以登录该 NAS。"
            ) from exc
        except (socket.timeout, socket.error, paramiko.SSHException) as exc:
            client.close()
            raise DeployError(f"SSH 连接失败：{exc}") from exc
        self.client = client
        transport = client.get_transport()
        if transport and transport.get_remote_server_key():
            key = transport.get_remote_server_key()
            fingerprint = ":".join(f"{byte:02x}" for byte in key.get_fingerprint())
            self.log(f"SSH 主机指纹：{fingerprint}")
        self.log("SSH 连接成功。")

    def close(self) -> None:
        if self.client:
            self.client.close()
            self.client = None

    def run(self, command: str, check: bool = True, timeout: int = 600) -> tuple[int, str]:
        if not self.client:
            raise DeployError("SSH 尚未连接。")
        self.log(f"$ {command}")
        try:
            _, stdout, _ = self.client.exec_command(command, get_pty=True)
            channel = stdout.channel
            channel.settimeout(1.0)
            started = time.monotonic()
            output = bytearray()
            pending = ""
            while True:
                if channel.recv_ready():
                    try:
                        chunk = channel.recv(4096)
                    except socket.timeout:
                        chunk = b""
                    if chunk:
                        output.extend(chunk)
                        pending += chunk.decode("utf-8", errors="replace")
                        lines = pending.split("\n")
                        pending = lines.pop()
                        for line in lines:
                            self.log(line.rstrip("\r"))
                elif channel.exit_status_ready():
                    break
                else:
                    time.sleep(0.05)
                if time.monotonic() - started > timeout:
                    channel.close()
                    raise DeployError(f"远程命令超时（{timeout} 秒）：{command}")
            while channel.recv_ready():
                output.extend(channel.recv(4096))
            if pending:
                self.log(pending.rstrip("\r"))
            code = channel.recv_exit_status()
        except (socket.timeout, socket.error, paramiko.SSHException) as exc:
            raise DeployError(f"远程命令执行失败：{exc}") from exc
        text = output.decode("utf-8", errors="replace")
        if check and code != 0:
            raise DeployError(f"远程命令返回错误码 {code}：{command}")
        return code, text

    def open_sftp(self) -> paramiko.SFTPClient:
        if not self.client:
            raise DeployError("SSH 尚未连接。")
        return self.client.open_sftp()

    def sftp_put(
        self,
        local_data: bytes,
        remote_path: str,
        sftp: paramiko.SFTPClient | None = None,
    ) -> None:
        owns_sftp = sftp is None
        if sftp is None:
            sftp = self.open_sftp()
        try:
            with sftp.file(remote_path, "wb") as remote_file:
                remote_file.write(local_data)
        finally:
            if owns_sftp:
                sftp.close()


class DeployerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("860x680")
        self.root.minsize(760, 560)
        self.worker: threading.Thread | None = None
        self.ssh = SshDeployer(self.append_log)

        self.host_var = tk.StringVar(value="")
        self.port_var = tk.StringVar(value="22")
        self.user_var = tk.StringVar(value="root")
        self.password_var = tk.StringVar()
        self.uid_var = tk.StringVar()
        self.image_var = tk.StringVar(value=DEFAULT_IMAGE)
        self.install_plugin_var = tk.BooleanVar(value=True)
        self.replace_container_var = tk.BooleanVar(value=False)
        self.replace_plugin_var = tk.BooleanVar(value=True)
        self.uninstall_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="准备就绪：请确认 NAS 已完成 ROOT 和 SSH 开通。")

        self._build_ui()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)

        title = ttk.Label(outer, text=APP_TITLE, font=("Microsoft YaHei UI", 16, "bold"))
        title.pack(anchor="w")
        subtitle = ttk.Label(
            outer,
            text=(
                "请先 root，并打开小米智能存储客户端，再运行本工具部署 HA。\n\n"
                "本工具不会执行 ROOT、不会开通 SSH；自动发现阶段只探测 SSH 服务，不会尝试登录。"
            ),
            foreground="#555555",
        )
        subtitle.pack(anchor="w", pady=(4, 12))

        fields = ttk.LabelFrame(outer, text="NAS SSH 信息", padding=12)
        fields.pack(fill="x")
        self.discover_button = ttk.Button(fields, text="1.一键获取NAS SSH信息", command=self.start_discovery)
        self.discover_button.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        self._field(fields, 1, 0, "NAS 地址", self.host_var, 22)
        self._field(fields, 1, 2, "SSH 端口", self.port_var, 8)
        self._field(fields, 2, 0, "SSH 用户", self.user_var, 22)
        self._field(fields, 2, 2, "root 密码（可选）", self.password_var, 22, password=True)
        self._field(fields, 3, 0, "HA 镜像", self.image_var, 55)
        self._field(fields, 4, 0, "NAS UID（可选）", self.uid_var, 22)

        options = ttk.LabelFrame(outer, text="部署选项", padding=12)
        options.pack(fill="x", pady=(12, 0))
        self.install_plugin_check = ttk.Checkbutton(
            options,
            text="安装/更新 Home Assistant 快捷入口及自动恢复（保留原图标与打开方式）",
            variable=self.install_plugin_var,
        )
        self.install_plugin_check.pack(anchor="w")
        self.replace_container_check = ttk.Checkbutton(
            options,
            text="已有 homeassistant 容器时重建容器（保留 /data/homeassistant/config）",
            variable=self.replace_container_var,
        )
        self.replace_container_check.pack(anchor="w", pady=(6, 0))
        self.replace_plugin_check = ttk.Checkbutton(
            options,
            text="已有快捷入口时覆盖更新（保留带时间戳的备份）",
            variable=self.replace_plugin_var,
        )
        self.replace_plugin_check.pack(anchor="w", pady=(6, 0))
        self.uninstall_check = ttk.Checkbutton(
            options,
            text="卸载HA",
            variable=self.uninstall_var,
            command=self.on_uninstall_toggle,
        )
        self.uninstall_check.pack(anchor="w", pady=(6, 0))

        deploy_row = ttk.Frame(options)
        deploy_row.pack(fill="x", pady=(10, 0))
        self.deploy_button = ttk.Button(deploy_row, text="2.开始一键部署", command=self.start_deploy)
        self.deploy_button.pack(side="left")
        self.entry_button = ttk.Button(
            deploy_row, text="只安装/修复入口（保留 HA）",
            command=lambda: self.start_deploy(entry_only=True),
        )
        self.entry_button.pack(side="left", padx=(8, 0))

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(12, 8))
        ttk.Button(actions, text="清空日志", command=self.clear_log).pack(side="left")
        ttk.Label(actions, textvariable=self.status_var).pack(side="left", padx=(16, 0))

        log_frame = ttk.LabelFrame(outer, text="部署日志", padding=8)
        log_frame.pack(fill="both", expand=True)
        self.log_box = scrolledtext.ScrolledText(
            log_frame,
            wrap="word",
            font=("Consolas", 10),
            state="disabled",
            background="#101418",
            foreground="#d6f5d6",
            insertbackground="#ffffff",
        )
        self.log_box.pack(fill="both", expand=True)

    @staticmethod
    def _field(parent, row: int, column: int, label: str, variable: tk.StringVar, width: int, password: bool = False) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=(0, 8), pady=5)
        entry = ttk.Entry(parent, textvariable=variable, width=width, show="•" if password else "")
        entry.grid(row=row, column=column + 1, sticky="w", padx=(0, 18), pady=5)

    def append_log(self, text: str) -> None:
        self.root.after(0, self._append_log_now, text)

    def _append_log_now(self, text: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def clear_log(self) -> None:
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def on_uninstall_toggle(self) -> None:
        selected = self.uninstall_var.get()
        if selected:
            self.install_plugin_var.set(False)
            self.replace_container_var.set(False)
            self.replace_plugin_var.set(False)
            state = "disabled"
        else:
            state = "normal"
        for control in (
            self.install_plugin_check,
            self.replace_container_check,
            self.replace_plugin_check,
        ):
            control.configure(state=state)

    def show_uninstall_dialog(self, args: dict) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("选择卸载方式")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        body = ttk.Frame(dialog, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="请选择 Home Assistant 的卸载方式", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        ttk.Label(
            body,
            text="两种方式都会删除 Docker 容器；区别在于是否保留配置和历史数据。",
            foreground="#555555",
        ).pack(anchor="w", pady=(6, 14))

        ttk.Label(body, text="卸载容器，保留配置", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        ttk.Label(
            body,
            text="删除 homeassistant 容器，但保留 /data/homeassistant/config。\n配置、数据库和历史数据仍在，日后可以重新部署。",
            justify="left",
        ).pack(anchor="w", pady=(3, 12))

        ttk.Label(body, text="完整卸载", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        ttk.Label(
            body,
            text="删除 homeassistant 容器、配置目录和 NAS 内的 Home Assistant 快捷入口。\n配置和历史数据会丢失，只有保留在其他位置的备份才能恢复。",
            justify="left",
        ).pack(anchor="w", pady=(3, 16))

        buttons = ttk.Frame(body)
        buttons.pack(fill="x")

        def remove_container_only() -> None:
            dialog.destroy()
            args["uninstall_mode"] = "container"
            self.launch_worker(args, "开始卸载 HA 容器（保留配置）。")

        def remove_everything() -> None:
            confirmed = messagebox.askyesno(
                "确认完整卸载",
                "完整卸载将删除 HA 容器、配置目录和 NAS 快捷入口。\n\n"
                "配置和历史数据会丢失，确定继续吗？",
                parent=dialog,
            )
            if not confirmed:
                return
            dialog.destroy()
            args["uninstall_mode"] = "full"
            self.launch_worker(args, "开始完整卸载 HA（配置和历史数据将被删除）。")

        ttk.Button(buttons, text="卸载容器，保留配置", command=remove_container_only).pack(side="left")
        ttk.Button(buttons, text="完整卸载", command=remove_everything).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="right")

    def launch_worker(self, args: dict, message: str) -> None:
        self.deploy_button.configure(state="disabled")
        self.entry_button.configure(state="disabled")
        self.discover_button.configure(state="disabled")
        self.set_status("操作进行中……")
        self.append_log("=" * 72)
        self.append_log(message)
        self.worker = threading.Thread(target=self._worker, args=(args,), daemon=True)
        self.worker.start()

    def start_discovery(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.discover_button.configure(state="disabled")
        self.set_status("正在扫描本地网络……")
        self.append_log("开始自动发现 NAS：只探测局域网内的 SSH 服务，不尝试密码。")
        self.worker = threading.Thread(target=self._discovery_worker, daemon=True)
        self.worker.start()

    def _discovery_worker(self) -> None:
        try:
            candidates = self.discover_ssh_endpoints()
            self.root.after(0, self._apply_discovery_results, candidates)
        except Exception as exc:
            self.append_log(f"自动发现失败：{exc}")
            self.root.after(0, lambda: self.discover_button.configure(state="normal"))
            self.set_status("自动发现失败。")

    @staticmethod
    def local_ipv4_networks() -> list[ipaddress.IPv4Network]:
        addresses: set[ipaddress.IPv4Address] = set()
        try:
            output = subprocess.check_output(
                ["ipconfig"], stderr=subprocess.STDOUT, timeout=5
            ).decode("mbcs", errors="replace")
            for value in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", output):
                try:
                    address = ipaddress.ip_address(value)
                    if isinstance(address, ipaddress.IPv4Address) and address.is_private and not address.is_loopback:
                        addresses.add(address)
                except ValueError:
                    continue
        except (OSError, subprocess.SubprocessError):
            pass
        if not addresses:
            try:
                for value in socket.gethostbyname_ex(socket.gethostname())[2]:
                    address = ipaddress.ip_address(value)
                    if isinstance(address, ipaddress.IPv4Address) and address.is_private and not address.is_loopback:
                        addresses.add(address)
            except (OSError, ValueError):
                pass
        networks = {ipaddress.ip_network(f"{address}/24", strict=False) for address in addresses}
        return sorted(networks, key=str)

    @staticmethod
    def probe_ssh(ip: str, port: int) -> bool:
        try:
            with socket.create_connection((ip, port), timeout=0.35) as connection:
                connection.settimeout(0.7)
                banner = connection.recv(128)
                return banner.startswith(b"SSH-")
        except (OSError, socket.timeout):
            return False

    def discover_ssh_endpoints(self) -> list[tuple[str, int]]:
        networks = self.local_ipv4_networks()
        if not networks:
            raise DeployError("没有找到可扫描的局域网 IPv4 网段。")
        ports = {22, 2222, 8022}
        try:
            entered_port = int(self.port_var.get().strip())
            if 1 <= entered_port <= 65535:
                ports.add(entered_port)
        except ValueError:
            pass
        hosts = [str(host) for network in networks for host in network.hosts()]
        if len(hosts) > 1024:
            hosts = hosts[:1024]
        self.append_log(f"扫描网段：{', '.join(map(str, networks))}")
        self.append_log(f"扫描 SSH 端口：{', '.join(map(str, sorted(ports)))}（共 {len(hosts)} 个地址）")
        endpoints: list[tuple[str, int]] = []
        jobs = [(host, port) for host in hosts for port in sorted(ports)]
        with ThreadPoolExecutor(max_workers=96) as executor:
            futures = {executor.submit(self.probe_ssh, host, port): (host, port) for host, port in jobs}
            for future in as_completed(futures):
                host, port = futures[future]
                if future.result():
                    endpoints.append((host, port))
        return sorted(set(endpoints), key=lambda item: (item[0], item[1]))

    def _apply_discovery_results(self, candidates: list[tuple[str, int]]) -> None:
        self.discover_button.configure(state="normal")
        if not candidates:
            self.append_log("没有发现 SSH 服务。请确认一键 SSH 工具已经运行成功，并且 NAS 与本机在同一局域网。")
            self.set_status("没有发现 SSH 服务。")
            messagebox.showwarning("未发现 NAS", "没有扫描到 SSH 服务。\n\n请确认 SSH 已开启、NAS 与电脑在同一局域网。")
            return
        self.append_log("发现候选设备：" + ", ".join(f"{host}:{port}" for host, port in candidates))
        if len(candidates) == 1:
            host, port = candidates[0]
            self.host_var.set(host)
            self.port_var.set(str(port))
            self.user_var.set("root")
            self.set_status(f"已发现 {host}:{port}；可直接使用本机 SSH 密钥，密码可选。")
            messagebox.showinfo(
                "发现 NAS",
                f"已自动填入：{host}:{port}\n\n"
                "工具会优先使用 Windows SSH Agent 或本机默认 OpenSSH 私钥。\n"
                "如果 NAS 也允许密码登录，才需要填写 root 密码。",
            )
            return
        self._choose_discovered_endpoint(candidates)

    def _choose_discovered_endpoint(self, candidates: list[tuple[str, int]]) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("选择 NAS")
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text="发现多个 SSH 设备，请选择你的 NAS：").pack(anchor="w", padx=16, pady=(16, 8))
        listbox = tk.Listbox(dialog, height=min(10, len(candidates)), width=34)
        for host, port in candidates:
            listbox.insert("end", f"{host}:{port}")
        listbox.selection_set(0)
        listbox.pack(padx=16, fill="both", expand=True)

        def choose() -> None:
            selection = listbox.curselection()
            if not selection:
                return
            host, port = candidates[selection[0]]
            self.host_var.set(host)
            self.port_var.set(str(port))
            self.user_var.set("root")
            self.set_status(f"已发现 {host}:{port}；可直接使用本机 SSH 密钥，密码可选。")
            dialog.destroy()

        ttk.Button(dialog, text="使用选中设备", command=choose).pack(pady=12)

    def set_status(self, text: str) -> None:
        self.root.after(0, self.status_var.set, text)

    def start_deploy(self, entry_only: bool = False) -> None:
        if self.worker and self.worker.is_alive():
            return
        host = self.host_var.get().strip()
        user = self.user_var.get().strip()
        password = self.password_var.get()
        image = self.image_var.get().strip()
        try:
            port = int(self.port_var.get().strip())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror("输入错误", "SSH 端口必须是 1 到 65535 之间的数字。")
            return
        if not host or not user:
            messagebox.showerror("输入错误", "NAS 地址和 SSH 用户不能为空。")
            return
        args = {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "image": image,
            "install_plugin": self.install_plugin_var.get(),
            "replace_container": self.replace_container_var.get(),
            "replace_plugin": self.replace_plugin_var.get(),
            "entry_only": entry_only,
            "uid": self.uid_var.get().strip(),
        }
        if args["uid"] and not re.fullmatch(r"[0-9]{1,20}", args["uid"]):
            messagebox.showerror("输入错误", "NAS UID 必须是纯数字，留空时自动检测。")
            return
        if entry_only:
            self.launch_worker(args, "只安装/修复 NAS 入口及恢复服务，保留原 HA 容器和配置。")
            return
        if self.uninstall_var.get():
            self.show_uninstall_dialog(args)
            return
        if not re.fullmatch(r"[A-Za-z0-9._:/-]+", image):
            messagebox.showerror("输入错误", "HA 镜像地址包含不支持的字符。")
            return
        self.launch_worker(args, "开始部署。root/SSH 已由外部一键工具完成，本工具不修改这部分。")

    def _worker(self, args: dict) -> None:
        success = False
        plugin_error = None
        uninstall_mode = args.get("uninstall_mode")
        try:
            self.ssh.connect(args["host"], args["port"], args["user"], args["password"])
            self.selected_uid = args.get("uid", "")
            if args.get("entry_only"):
                self.deploy_plugin(args["host"], args["replace_plugin"])
                self.set_status("入口及自动恢复已安装；请重开 NAS 客户端验证。")
            elif uninstall_mode:
                self.uninstall_homeassistant(uninstall_mode)
                self.append_log("")
                self.append_log("HA 卸载完成。")
                self.set_status("卸载完成。")
            else:
                self.preflight(args["host"])
                self.deploy_homeassistant(args["image"], args["replace_container"])
                if args["install_plugin"]:
                    try:
                        self.deploy_plugin(args["host"], args["replace_plugin"])
                    except Exception as exc:
                        plugin_error = str(exc)
                        self.append_log(f"警告：HA 已部署，但 NAS 快捷入口安装失败：{exc}")
                url = f"http://{args['host']}:8123"
                self.append_log("")
                self.append_log(f"部署完成：{url}")
                self.set_status("HA 已部署，但入口安装失败，请查看日志。" if plugin_error else f"完成：{url}")
            success = True
        except Exception as exc:
            action = "卸载" if uninstall_mode else "部署"
            self.append_log(f"{action}失败：{exc}")
            self.set_status(f"{action}失败，请查看日志。")
        finally:
            self.ssh.close()
            self.root.after(0, lambda: self.deploy_button.configure(state="normal"))
            self.root.after(0, lambda: self.entry_button.configure(state="normal"))
            self.root.after(0, lambda: self.discover_button.configure(state="normal"))
            if success:
                if plugin_error:
                    self.root.after(0, lambda error=plugin_error: messagebox.showwarning(
                        "HA 已部署，入口安装失败", f"{error}\n\nHA 容器保持原状，可稍后只修复入口。"))
                elif args.get("entry_only"):
                    self.root.after(0, lambda: messagebox.showinfo(
                        "入口安装完成", "入口及自动恢复服务已安装。\n请完全退出并重新打开 NAS 客户端验证。"))
                elif uninstall_mode:
                    self.root.after(0, lambda: messagebox.showinfo("卸载完成", "Home Assistant 卸载完成。"))
                else:
                    self.root.after(0, lambda: messagebox.showinfo("部署完成", f"Home Assistant 已部署。\n\n访问地址：\nhttp://{args['host']}:8123"))

    def find_nas_uid(self) -> str:
        manual_uid = getattr(self, "selected_uid", "")
        if manual_uid:
            if not re.fullmatch(r"[0-9]{1,20}", manual_uid):
                raise DeployError("NAS UID 必须是纯数字。")
            self.ssh.run(f"test -f /data/plugin/u{manual_uid}.list")
            self.append_log(f"使用指定 NAS UID：{manual_uid}")
            return manual_uid
        _, uid_output = self.ssh.run(
            "for f in /data/plugin/u*.list; do "
            "[ -f \"$f\" ] || continue; "
            "stamp=$(stat -c '%Y' \"$f\" 2>/dev/null || echo 0); "
            "printf '%s %s\\n' \"$stamp\" \"$f\"; "
            "done | sort -nr",
            check=False,
        )
        candidates: list[tuple[int, str]] = []
        for line in uid_output.splitlines():
            match = re.search(r"^\s*(\d+)\s+/data/plugin/u(\d+)\.list\s*$", line)
            if match:
                candidates.append((int(match.group(1)), match.group(2)))
        if not candidates:
            raise DeployError("没有找到 /data/plugin/u*.list，无法确定 NAS 用户 UID。")
        candidates.sort(reverse=True)
        selected_uid = candidates[0][1]
        if len(candidates) > 1:
            candidate_text = "、".join(uid for _, uid in candidates)
            self.append_log(f"检测到多个 NAS 用户 UID：{candidate_text}")
            self.append_log(
                f"根据用户列表最近刷新时间选择 UID：{selected_uid}。"
                "请在部署前先打开小米智能存储客户端。"
            )
        return selected_uid

    def uninstall_homeassistant(self, mode: str) -> None:
        if mode not in {"container", "full"}:
            raise DeployError("未知的卸载方式。")
        _, state_output = self.ssh.run(
            f"{DOCKER} inspect -f '{{{{.State.Status}}}}' {CONTAINER} 2>/dev/null || true",
            check=False,
        )
        exists = bool([line.strip() for line in state_output.splitlines() if line.strip()])
        if exists:
            self.append_log(f"正在删除 {CONTAINER} Docker 容器……")
            self.ssh.run(f"{DOCKER} rm -f {CONTAINER}")
            self.append_log("Docker 容器已删除。")
        else:
            self.append_log("没有发现 homeassistant Docker 容器，继续处理其余卸载内容。")

        if mode == "container":
            self.append_log(f"已保留配置目录：{CONFIG_DIR}")
            return

        self.append_log(f"正在删除配置目录：{CONFIG_DIR}")
        self.ssh.run(f"rm -rf {shlex.quote(CONFIG_DIR)}")
        try:
            self.remove_nas_plugin()
        except Exception as exc:
            self.append_log(f"警告：NAS 快捷入口未能自动移除：{exc}")
        self.append_log("配置目录和 NAS 快捷入口清理完成。")

    def remove_nas_plugin(self) -> None:
        uid = self.find_nas_uid()
        recovery = "/data/homeassistant-entry-recovery/entry_recovery.py"
        # Serialize with the watcher and persist disabled intent before removal.
        self.ssh.run(
            f"if test -f {recovery}; then python3 {recovery} disable --uid {uid} --apply; fi",
            timeout=90,
        )
        list_path = f"/data/plugin/u{uid}.list"
        stamp = f"{int(time.time())}-{os.getpid()}"
        remote_script = f"/tmp/ha-homeassistant-uninstall-{stamp}.py"
        remove_script = '''import json
import os
import shutil
import sys
import time

path = sys.argv[1]
backup = path + ".before-ha-uninstall-" + time.strftime("%Y%m%d-%H%M%S")
shutil.copy2(path, backup)
with open(path, "r", encoding="utf-8") as handle:
    data = json.load(handle)
data.pop("homeassistant", None)
temporary = path + ".tmp-ha-uninstall"
with open(temporary, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\\n")
os.replace(temporary, path)
print("registration backup: " + backup)
'''
        sftp = self.ssh.open_sftp()
        try:
            self.ssh.sftp_put(remove_script.encode("utf-8"), remote_script, sftp=sftp)
            self.ssh.run(f"python3 {shlex.quote(remote_script)} {shlex.quote(list_path)}", timeout=60)
            targets = [
                f"/nas/pool0/u{uid}/plugin/pluginsrc/homeassistant",
                f"/home/u{uid}/plugin/homeassistant",
                f"/data/plugin/www/u{uid}/homeassistant",
                "/data/plugin/www/icon/homeassistant.icon",
            ]
            for target in targets:
                self.ssh.run(f"rm -rf {shlex.quote(target)}", check=False)
            self.append_log(f"NAS 快捷入口已移除，注册文件已备份：{list_path}.before-ha-uninstall-*")
        finally:
            try:
                self.ssh.run(f"rm -f {shlex.quote(remote_script)}", check=False)
            except Exception as cleanup_exc:
                self.append_log(f"警告：未能清理临时卸载脚本：{cleanup_exc}")
            try:
                sftp.close()
            except Exception as close_exc:
                self.append_log(f"警告：关闭 SFTP 通道时出现异常：{close_exc}")

    def preflight(self, host: str) -> None:
        self.append_log("开始检查 NAS 环境……")
        _, arch = self.ssh.run("uname -m")
        architecture = arch.strip().splitlines()[-1] if arch.strip() else "unknown"
        if architecture not in {"aarch64", "arm64"}:
            raise DeployError(f"检测到架构 {architecture}，当前镜像要求 ARM64/aarch64。")
        self.append_log(f"架构检查通过：{architecture}")
        self.ssh.run(f"test -x {DOCKER}")
        self.append_log(f"Docker CLI 检查通过：{DOCKER}")
        self.ssh.run(f"mkdir -p {CONFIG_DIR}")
        self.append_log(f"配置目录已准备：{CONFIG_DIR}")
        self.ssh.run(
            "if command -v ss >/dev/null 2>&1; then ss -ltn 2>/dev/null | grep -E '[:.]8123[[:space:]]' || true; "
            "elif command -v netstat >/dev/null 2>&1; then netstat -ltn 2>/dev/null | grep -E '[:.]8123[[:space:]]' || true; fi",
            check=False,
        )
        self.append_log("8123 端口占用情况已检查（host 网络模式不使用端口映射）。")

    def deploy_homeassistant(self, image: str, replace_existing: bool) -> None:
        self.append_log(f"目标镜像：{image}")
        _, state_output = self.ssh.run(
            f"{DOCKER} inspect -f '{{{{.State.Status}}}}' {CONTAINER} 2>/dev/null || true",
            check=False,
        )
        state_lines = [line.strip() for line in state_output.splitlines() if line.strip()]
        existing_state = state_lines[-1] if state_lines else ""
        if existing_state and not replace_existing:
            self.append_log(f"发现已有 {CONTAINER} 容器，按当前选项保留，不重建。状态：{existing_state}")
            if existing_state != "running":
                self.ssh.run(f"{DOCKER} start {CONTAINER}")
                self.append_log("已有容器已启动。")
            self.verify_container()
            return
        if existing_state and replace_existing:
            self.append_log("发现已有容器，将删除并重建；/data/homeassistant/config 不会删除。")
            self.ssh.run(f"{DOCKER} rm -f {CONTAINER}")

        self.append_log("正在拉取 Home Assistant 镜像，这一步可能需要几分钟……")
        self.ssh.run(f"{DOCKER} pull {shlex.quote(image)}", timeout=1800)
        dbus_mount = ""
        _, dbus = self.ssh.run("test -d /run/dbus && echo yes || echo no", check=False)
        if dbus.strip().splitlines()[-1:] == ["yes"]:
            dbus_mount = " -v /run/dbus:/run/dbus:ro"
            self.append_log("检测到 /run/dbus，将一并挂载以保留相关设备支持。")
        command = (
            f"{DOCKER} run -d --name {CONTAINER} --restart=unless-stopped "
            f"--network=host --privileged --stop-timeout 60 "
            f"-e TZ=Asia/Shanghai -v {CONFIG_DIR}:/config{dbus_mount} {shlex.quote(image)}"
        )
        self.ssh.run(command)
        self.append_log("Home Assistant 容器已创建并启动。")
        time.sleep(2)
        self.verify_container()

    def verify_container(self) -> None:
        _, status = self.ssh.run(
            f"{DOCKER} ps --filter name=^{CONTAINER}$ --format '{{{{.Status}}}}'",
            check=False,
        )
        if not any("Up" in line for line in status.splitlines()):
            self.ssh.run(f"{DOCKER} logs --tail 100 {CONTAINER}", check=False)
            raise DeployError("容器没有保持 Up 状态，请查看上面的启动日志。")
        self.append_log(f"容器状态正常：{next(line.strip() for line in status.splitlines() if 'Up' in line)}")
        self.ssh.run(f"{DOCKER} logs --tail 60 {CONTAINER}", check=False)
        self.ssh.run(
            "if command -v curl >/dev/null 2>&1; then "
            "curl -sS -o /dev/null -w 'HA HTTP %{http_code}\\n' --max-time 5 http://127.0.0.1:8123 || true; fi",
            check=False,
        )

    def deploy_plugin(self, host: str, replace_existing: bool) -> None:
        self.append_log("准备安装 NAS 内 Home Assistant 快捷入口……")
        uid = self.find_nas_uid()
        self.append_log(f"检测到 NAS 用户 UID：{uid}")
        self.ssh.run("test \"$(id -u)\" = 0 && command -v python3 && command -v systemctl && test -x /usr/bin/plugin.cgi")

        local_root = asset_root()
        relative_files = [
            path.relative_to(local_root)
            for path in local_root.rglob("*")
            if path.is_file() and path.name != "README.md" and "__pycache__" not in path.parts
            and path.suffix.lower() not in {".pyc", ".pyo"}
        ]
        stamp = f"{int(time.time())}-{os.getpid()}"
        remote_root = f"/tmp/ha-homeassistant-installer-{stamp}"
        remote_directories = {remote_root}
        for relative in relative_files:
            remote_path = remote_root + "/" + relative.as_posix()
            remote_directories.add(remote_path.rsplit("/", 1)[0])
        mkdir_command = "mkdir -p " + " ".join(
            shlex.quote(path) for path in sorted(remote_directories)
        )
        self.ssh.run(mkdir_command)

        sftp = self.ssh.open_sftp()
        try:
            for relative in relative_files:
                source = local_root / relative
                data = source.read_bytes()
                if source.suffix.lower() in {".html", ".json", ".sh", ".py", ".cgi", ""}:
                    text = data.decode("utf-8").replace("\r\n", "\n")
                    text = text.replace("__NAS_HOST__", host).replace("__NAS_UID__", uid)
                    data = text.encode("utf-8")
                remote_path = remote_root + "/" + relative.as_posix()
                self.ssh.sftp_put(data, remote_path, sftp=sftp)
            self.append_log(f"已将 NAS 入口资源内置包上传到临时目录：{remote_root}")
            flags = "--apply"
            if replace_existing:
                flags += " --replace"
            dry_flags = "--dry-run" + (" --replace" if replace_existing else "")
            self.ssh.run(f"sh {shlex.quote(remote_root + '/scripts/deploy.sh')} {dry_flags}")
            self.ssh.run(f"sh {shlex.quote(remote_root + '/scripts/deploy.sh')} {flags}", timeout=300)
            self.ssh.run("python3 /data/homeassistant-entry-recovery/entry_recovery.py status", timeout=60)
            self.append_log("NAS 快捷入口及自动恢复已安装，原图标、名称和打开方式保持不变。")
        finally:
            try:
                self.ssh.run(f"rm -rf {shlex.quote(remote_root)}", check=False)
            except Exception as cleanup_exc:
                self.append_log(f"警告：未能清理入口安装临时目录：{cleanup_exc}")
            try:
                sftp.close()
            except Exception as close_exc:
                self.append_log(f"警告：关闭 SFTP 通道时出现异常：{close_exc}")


def main() -> None:
    if "--self-test" in sys.argv:
        # Packaging smoke test: no SSH, Docker, or NAS writes.
        resources = asset_root()
        info = json.loads((resources / "INFO").read_text(encoding="utf-8"))
        entry = json.loads((resources / "registration/homeassistant.entry.json").read_text(encoding="utf-8"))
        if info != entry["homeassistant"]["info"]:
            raise SystemExit(1)
        if not (resources / "ui/icon.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            raise SystemExit(1)
        recovery = resources / "scripts/entry_recovery.py"
        compile(recovery.read_text(encoding="utf-8"), str(recovery), "exec")
        root = tk.Tk()
        root.withdraw()
        DeployerApp(root)
        root.update_idletasks()
        root.destroy()
        raise SystemExit(0)
    root = tk.Tk()
    try:
        root.iconname(APP_TITLE)
    except tk.TclError:
        pass
    DeployerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
