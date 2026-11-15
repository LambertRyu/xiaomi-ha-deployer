#!/usr/bin/env python3
"""Recover only the existing HA shortcut; never manage HA, SSH or Docker.

Linux/NAS runtime uses only Python's standard library. --root is an offline
staging root: no host services, users or mounts are modified in this mode.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import ctypes
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import select
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid

BASE = "/data/homeassistant-entry-recovery"
UNIT = "homeassistant-entry-recovery"
FILES = ("INFO", "ui/index.html", "ui/icon.png", "ui/homeassistant.cgi", "ui/plugin.cgi")
INTERVAL = 60


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def read_json(path):
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    require(isinstance(value, dict), f"not a JSON object: {path}")
    return value


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, data, mode=None, owner=None):
    require(not path.is_symlink(), f"refusing file symlink: {path}")
    if path.is_file() and path.read_bytes() == data:
        old = path.stat()
        changed = False
        if mode is not None and stat.S_IMODE(old.st_mode) != mode:
            os.chmod(path, mode)
            changed = True
        if owner and hasattr(os, "chown") and (old.st_uid, old.st_gid) != owner:
            os.chown(path, *owner)
            changed = True
        return changed
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.stat() if path.exists() else None
    fd, temporary = tempfile.mkstemp(prefix=".ha-entry-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode if mode is not None else (stat.S_IMODE(old.st_mode) if old else 0o644))
        if hasattr(os, "chown"):
            identity = owner if owner is not None else ((old.st_uid, old.st_gid) if old else (0, 0))
            os.chown(temporary, *identity)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


class Recovery:
    def __init__(self, root=Path("/")):
        self.root = Path(root).resolve()
        self.live = self.root == Path("/").resolve()
        self.base = self.path(BASE)

    def path(self, absolute):
        return self.root / absolute.lstrip("/")

    @staticmethod
    def uid(value):
        require(re.fullmatch(r"[0-9]{1,20}", value or "") is not None, "numeric NAS UID required")
        return value

    def guard(self, path):
        """Do not follow writable plugin parents when writing as root."""
        relative = path.relative_to(self.root)
        cursor = self.root
        for part in relative.parts:
            cursor = cursor / part
            require(not cursor.is_symlink(), f"refusing symlink in write path: {cursor}")

    @contextlib.contextmanager
    def locked(self):
        self.guard(self.base)
        self.base.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.live:
            require(os.geteuid() == 0, "run as root")
            require(self.base.stat().st_uid == 0, "recovery store must be root-owned")
            require(self.base.stat().st_mode & 0o022 == 0, "recovery store must not be writable by others")
        lock = self.base / "recovery.lock"
        self.guard(lock)
        with lock.open("a+b") as handle:
            if os.name == "posix":
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX)
            else:  # Offline Windows regression tests only.
                import msvcrt
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                if os.name != "posix":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

    def record_path(self, uid):
        return self.base / "users" / self.uid(uid) / "record.json"

    def records(self):
        for path in sorted((self.base / "users").glob("*/record.json")):
            self.guard(path)
            record = read_json(path)
            require(record.get("uid") == self.uid(path.parent.name), "record UID mismatch")
            if record.get("active"):
                yield record

    def targets(self, uid):
        uid = self.uid(uid)
        return (self.path(f"/nas/pool0/u{uid}/plugin/pluginsrc/homeassistant"),
                self.path(f"/home/u{uid}/plugin/homeassistant"),
                self.path(f"/data/plugin/www/u{uid}/homeassistant"),
                self.path("/data/plugin/www/icon/homeassistant.icon"),
                self.path(f"/data/plugin/u{uid}.list"))

    def ready(self, uid):
        source, home, ui, icon, listing = self.targets(uid)
        # Never create a user list while the vendor is rebuilding it at boot.
        require(listing.is_file() and not listing.is_symlink(), f"user list not ready: {listing}")
        if self.live:
            import pwd
            user = pwd.getpwnam(f"u{uid}")
            # The vendor filesystem can confuse ismount() device-number checks.
            mounts = {line.split()[4] for line in Path("/proc/self/mountinfo").read_text().splitlines()
                      if len(line.split()) > 4}
            require("/nas/pool0" in mounts, "pool0 not mounted; recovery deferred")
            require(Path(f"/nas/pool0/u{uid}").is_dir(), "NAS user storage not ready")
            identity = (user.pw_uid, user.pw_gid)
        else:
            identity = None
        for path in (source / "ui", home, ui.parent, icon.parent, listing):
            self.guard(path)
        return identity

    def native_busy(self):
        if not self.live:
            return False
        for proc in Path("/proc").iterdir():
            if proc.name.isdigit():
                try:
                    if (proc / "comm").read_text().strip() == "plugincenter":
                        return True
                except OSError:
                    pass
        return False

    def current_lan_ip(self):
        if not self.live:
            return None
        try:
            result = subprocess.run(["ip", "-4", "route", "get", "1.1.1.1"],
                                    capture_output=True, text=True, timeout=5, check=True)
            match = re.search(r"\bsrc\s+([0-9.]+)", result.stdout)
            address = ipaddress.ip_address(match.group(1)) if match else None
            if isinstance(address, ipaddress.IPv4Address) and address.is_private and not (
                    address.is_loopback or address.is_link_local or address.is_unspecified):
                return str(address)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        return None

    def prune_snapshots(self, uid):
        parent = self.record_path(uid).parent
        generations = sorted((p for p in parent.iterdir()
                              if p.is_dir() and re.fullmatch(r"[0-9a-f]{32}", p.name)),
                             key=lambda p: p.stat().st_mtime_ns, reverse=True)
        for old in generations[2:]:
            self.guard(old)
            shutil.rmtree(old)

    def refresh_host(self, record, files):
        # Keep the original page and launch behavior, update only its baked-in
        # LAN destination when DHCP changes the NAS address. Custom DNS stays.
        host = self.current_lan_ip()
        if not host:
            return files
        html = files["ui/index.html"].decode("utf-8")
        match = re.search(r"http://([^/\s\"'<>]+):8123", html)
        if not match:
            return files
        previous = match.group(1)
        try:
            address = ipaddress.ip_address(previous)
        except ValueError:
            return files
        if not isinstance(address, ipaddress.IPv4Address) or not address.is_private or previous == host:
            return files
        files = dict(files)
        files["ui/index.html"] = html.replace(f"http://{previous}:8123", f"http://{host}:8123").encode("utf-8")
        generation = uuid.uuid4().hex
        parent = self.record_path(record["uid"]).parent / generation
        self.guard(parent)
        for name, data in files.items():
            atomic_write(parent / name, data, 0o600)
        record.update(generation=generation, host=host,
                      hashes={name: digest(data) for name, data in files.items()})
        atomic_write(self.record_path(record["uid"]), json_bytes(record), 0o600)
        self.prune_snapshots(record["uid"])
        print("updated Home Assistant shortcut LAN destination after address change", flush=True)
        return files

    def backup_list(self, uid, data):
        directory = self.base / "users" / uid / "list-backups"
        self.guard(directory)
        directory.mkdir(parents=True, exist_ok=True)
        atomic_write(directory / f"{time.time_ns()}.json", data, 0o600)
        # Bounded recovery backups, exclusively inside our root-owned store.
        for path in sorted(directory.glob("*.json"))[:-10]:
            self.guard(path)
            path.unlink()

    def merge(self, uid, entry, remove=False):
        listing = self.targets(uid)[4]
        self.guard(listing)
        before = listing.read_bytes()
        current = read_json(listing)
        old = current.get("homeassistant")
        if old is not None:
            require(isinstance(old, dict) and old.get("info", {}).get("plugin") == "homeassistant"
                    and old.get("info", {}).get("id") == entry["info"]["id"],
                    "conflicting homeassistant registration; left untouched")
        if remove:
            current.pop("homeassistant", None)
        else:
            desired = copy.deepcopy(entry)
            if old and old.get("install") is not False:
                # Respect a deliberate disable and vendor runtime state.
                for key in ("enable", "status", "changetime"):
                    if key in old:
                        desired[key] = old[key]
            if old == desired:
                return False
            current["homeassistant"] = desired
        if remove and old is None:
            return False
        require(not self.native_busy(), "vendor plugin operation active; retry later")
        require(listing.read_bytes() == before, "user list changed during recovery; retry later")
        self.backup_list(uid, before)
        return atomic_write(listing, json_bytes(current))

    def symlink(self, path, target, identity):
        self.guard(path.parent)
        if path.is_symlink() and os.readlink(path) == str(target):
            return False
        require(path.is_symlink() or not path.exists(), f"conflicting non-link path: {path}")
        self.visible_directory(path.parent)
        temporary = path.with_name(f".ha-link-{uuid.uuid4().hex}")
        try:
            temporary.symlink_to(target, target_is_directory=True)
            if identity and hasattr(os, "lchown"):
                os.lchown(temporary, *identity)
            os.replace(temporary, path)
        finally:
            if temporary.is_symlink():
                temporary.unlink()
        return True

    def visible_directory(self, path):
        self.guard(path)
        missing = []
        cursor = path
        while not cursor.exists():
            missing.append(cursor)
            cursor = cursor.parent
        for directory in reversed(missing):
            directory.mkdir(mode=0o755)
            # The daemon's private umask must not hide public UI parents.
            os.chmod(directory, 0o755)

    def restore(self, record):
        uid = record["uid"]
        identity = self.ready(uid)
        require(not self.native_busy(), "vendor plugin operation active; recovery deferred")
        generation = record["generation"]
        require(re.fullmatch(r"[0-9a-f]{32}", generation) is not None, "invalid snapshot generation")
        payload = self.record_path(uid).parent / generation
        self.guard(payload)
        # Verify every byte before the first change; never execute snapshot scripts.
        files = {}
        require(set(record["hashes"]) == set(FILES), "incomplete snapshot manifest")
        for name in FILES:
            path = payload / name
            self.guard(path)
            files[name] = path.read_bytes()
            require(digest(files[name]) == record["hashes"][name], f"damaged snapshot: {name}")
        entry = record["entry"]
        source, home, ui, icon, listing = self.targets(uid)
        # Validate list/conflicts before repairing any files.
        existing = read_json(listing).get("homeassistant")
        if existing is not None:
            require(isinstance(existing, dict) and existing.get("info", {}).get("plugin") == "homeassistant"
                    and existing.get("info", {}).get("id") == entry["info"]["id"], "registration ID conflict")
        for link in (home / "src", ui):
            require(link.is_symlink() or not link.exists(), f"conflicting non-link path: {link}")
        files = self.refresh_host(record, files)
        if existing and existing.get("install") is not False and isinstance(existing.get("enable"), bool):
            if entry.get("enable") != existing["enable"]:
                entry["enable"] = existing["enable"]
                entry["status"] = existing.get("status", "running" if existing["enable"] else "stopped")
                atomic_write(self.record_path(uid), json_bytes(record), 0o600)
        changed = False
        for directory in (source / "ui", home):
            self.guard(directory)
            self.visible_directory(directory)
            if identity:
                self.directory_permissions(directory, identity, 0o755 if directory == source / "ui" else 0o700)
        if identity:
            self.directory_permissions(source, identity, 0o755)
        for name, data in files.items():
            path = source / name
            self.guard(path)
            changed |= atomic_write(path, data, 0o755 if name.endswith(".cgi") else 0o644, identity)
        self.guard(home / "INFO")
        changed |= atomic_write(home / "INFO", files["INFO"], 0o644, identity)
        changed |= self.symlink(home / "src", source, identity)
        changed |= self.symlink(ui, home / "src/ui", identity)
        self.guard(icon)
        self.visible_directory(icon.parent)
        changed |= atomic_write(icon, files["ui/icon.png"])
        changed |= self.merge(uid, entry)
        if changed:
            print(f"restored Home Assistant shortcut for u{uid}", flush=True)
        return changed

    @staticmethod
    def directory_permissions(path, identity, mode):
        metadata = path.stat()
        if (metadata.st_uid, metadata.st_gid) != identity:
            os.chown(path, *identity)
        if stat.S_IMODE(metadata.st_mode) != mode:
            os.chmod(path, mode)

    def reconcile(self):
        failures = []
        with self.locked():
            for record in self.records():
                try:
                    self.restore(record)
                except (OSError, ValueError, KeyError, RuntimeError) as exc:
                    failures.append(f"u{record['uid']}: {exc}")
        for failure in failures:
            print(f"recovery deferred: {failure}", file=sys.stderr, flush=True)
        return not failures

    def install(self, uid, source, replace=False, apply=False):
        uid = self.uid(uid)
        self.ready(uid)
        listing = read_json(self.targets(uid)[4])
        require(replace or "homeassistant" not in listing, "entry exists; use --replace")
        entry_doc = read_json(source / "registration/homeassistant.entry.json")
        require(set(entry_doc) == {"homeassistant"}, "invalid registration template")
        entry = entry_doc["homeassistant"]
        require(entry["info"]["plugin"] == "homeassistant" and entry["info"]["id"] == 9901,
                "unexpected plugin identity")
        expected_mpk = f"/nas/pool0/u{uid}/plugin/pluginsrc/homeassistant/INFO"
        require(entry["resource"]["mpk"] == expected_mpk, "template UID mismatch")
        files = {name: (source / name).read_bytes() for name in FILES}
        require(all(b"__NAS_" not in data for name, data in files.items() if not name.endswith(".png")),
                "replace NAS host/UID placeholders before installing")
        require(json.loads(files["INFO"]) == entry["info"], "INFO and registration differ")
        if not apply:
            print(f"dry-run: install unchanged Home Assistant UI and recovery for u{uid}")
            return
        with self.locked():
            record_path = self.record_path(uid)
            self.guard(record_path)
            generation = uuid.uuid4().hex
            payload = record_path.parent / generation
            for name, data in files.items():
                atomic_write(payload / name, data)
            record = {"uid": uid, "active": True, "generation": generation,
                      "entry": entry, "hashes": {name: digest(data) for name, data in files.items()}}
            atomic_write(self.base / "entry_recovery.py", Path(__file__).read_bytes(), 0o700)
            atomic_write(record_path, json_bytes(record), 0o600)
            self.restore(record)
            # Keep current and one previous snapshot; do not grow forever on updates.
            self.prune_snapshots(uid)
        if self.live:
            self.services()
        print("shortcut installed; recovery enabled (events + 60-second fallback)")

    def services(self):
        python = sys.executable
        require(re.fullmatch(r"/[A-Za-z0-9_./-]+", python) is not None, "invalid Python executable")
        program = BASE + "/entry_recovery.py"
        units = {
            UNIT + ".service": f"""[Unit]
Description=Keep the existing Home Assistant NAS shortcut registered
After=local-fs.target
RequiresMountsFor=/data
[Service]
Type=simple
ExecStart={python} {program} watch
Restart=on-failure
RestartSec=10
UMask=0077
[Install]
WantedBy=multi-user.target
""",
            UNIT + "-check.service": f"""[Unit]
Description=Home Assistant shortcut recovery fallback
After=local-fs.target
RequiresMountsFor=/data
[Service]
Type=oneshot
ExecStart={python} {program} reconcile
UMask=0077
""",
            UNIT + "-check.timer": f"""[Unit]
Description=Fallback check for missing Home Assistant shortcuts
[Timer]
OnBootSec=120
OnUnitActiveSec={INTERVAL}
AccuracySec=5
Unit={UNIT}-check.service
[Install]
WantedBy=timers.target
"""}
        for name, content in units.items():
            path = self.path("/etc/systemd/system/" + name)
            self.guard(path)
            atomic_write(path, content.encode())
        # Some firmware exposes the writable /etc layer after systemd builds
        # its initial target graph. Enabled units can then remain inactive.
        # Cron is the independent late-boot launcher; never assume /bin/sh is
        # an unrestricted interpreter in a vendor service's mount namespace.
        shell = "/usr/bin/bash.bash"
        require(Path(shell).is_file() if self.live else True, "NAS Bash required for late-boot hook")
        cron = (f"SHELL={shell}\nPATH=/usr/sbin:/usr/bin:/sbin:/bin\nMAILTO=\"\"\n"
                f"* * * * * root {python} {program} bootstrap\n")
        cron_path = self.path("/etc/cron.d/" + UNIT)
        self.guard(cron_path)
        atomic_write(cron_path, cron.encode(), 0o644)
        self.systemctl("daemon-reload")
        self.systemctl("enable", UNIT + ".service", UNIT + "-check.timer")
        self.systemctl("restart", UNIT + ".service", UNIT + "-check.timer")
        self.systemctl("is-active", "--quiet", UNIT + ".service")
        self.systemctl("is-active", "--quiet", UNIT + "-check.timer")

    def bootstrap(self):
        require(self.live, "bootstrap requires the NAS host")
        if not list(self.records()):
            return
        result = subprocess.run(["systemctl", "is-active", UNIT + ".service", UNIT + "-check.timer"],
                                capture_output=True, text=True, timeout=15)
        states = result.stdout.splitlines()
        if result.returncode == 0 and len(states) == 2 and all(state == "active" for state in states):
            return
        self.services()
        print("late-boot Home Assistant entry services restored", flush=True)

    @staticmethod
    def systemctl(*args):
        subprocess.run(["systemctl", *args], check=True, timeout=45)

    def disable(self, uid, apply=False):
        path = self.record_path(uid)
        self.guard(path)
        if not path.exists():
            print("no managed recovery record; legacy shortcut can be removed normally")
            return
        if not apply:
            print(f"dry-run: disable recovery for u{uid}")
            return
        with self.locked():
            record = read_json(path)
            record["active"] = False
            atomic_write(path, json_bytes(record), 0o600)
            # Disable first, so a later removal failure cannot resurrect the entry.
            listing = self.targets(uid)[4]
            if listing.is_file():
                self.merge(uid, record["entry"], remove=True)
        print(f"recovery disabled for u{uid}; HA and configuration untouched")

    def watch(self):
        require(sys.platform == "linux", "watch requires Linux (NAS or an offline staging root)")
        # Prefer events; periodic reconciliation still works when inotify is unavailable.
        fd = -1
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            libc.inotify_init1.argtypes = [ctypes.c_int]
            libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
            fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        except (OSError, AttributeError):
            pass
        subscribed = set()
        boot_delay = max(0, 90 - float(Path("/proc/uptime").read_text().split()[0])) if self.live else 0
        boot_ready = time.monotonic() + boot_delay
        due = boot_ready
        first_event = None
        try:
            while True:
                if fd >= 0:
                    directories = {self.path("/data"), self.base / "users", self.path("/data/plugin"),
                                   self.path("/data/plugin/www"), self.path("/data/plugin/www/icon")}
                    for record in self.records():
                        src, home, ui, _, _ = self.targets(record["uid"])
                        directories.update((src.parent, src, src / "ui", home.parent, home, ui.parent))
                    # A replaced directory gets a different inode and needs a new watch.
                    for directory in directories:
                        try:
                            inode = (str(directory), directory.stat().st_ino)
                            if inode not in subscribed and directory.is_dir():
                                mask = 0x00000008 | 0x00000004 | 0x00000040 | 0x00000080 | 0x00000100 | 0x00000200 | 0x00000400 | 0x00000800
                                if libc.inotify_add_watch(fd, os.fsencode(directory), mask) >= 0:
                                    subscribed.add(inode)
                        except OSError:
                            pass
                now = time.monotonic()
                if now >= due:
                    self.reconcile()
                    due = time.monotonic() + INTERVAL
                    first_event = None
                timeout = max(0, min(INTERVAL, due - time.monotonic()))
                if fd >= 0 and select.select([fd], [], [], timeout)[0]:
                    os.read(fd, 256 * 1024)
                    now = time.monotonic()
                    first_event = first_event or now
                    # Bounded debounce; native_busy() is checked again before writes.
                    event_due = min(due, first_event + 30) if now - first_event >= 25 else min(first_event + 30, now + 5)
                    due = max(boot_ready, event_due)
                elif fd < 0:
                    time.sleep(timeout)
        finally:
            if fd >= 0:
                os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "reconcile", "watch", "status", "disable", "bootstrap"))
    parser.add_argument("--uid")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--root", type=Path, default=Path("/"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    recovery = Recovery(args.root)
    if recovery.live:
        require(sys.platform == "linux" and os.geteuid() == 0, "NAS root Linux shell required")
    if args.command == "install":
        require(args.source is not None, "--source required")
        recovery.install(args.uid, args.source, args.replace, args.apply)
    elif args.command == "disable":
        recovery.disable(args.uid, args.apply)
    elif args.command == "status":
        for record in recovery.records():
            print(f"u{record['uid']}: recovery enabled")
        if recovery.live:
            recovery.systemctl("is-active", UNIT + ".service", UNIT + "-check.timer")
    elif args.command == "watch":
        recovery.watch()
    elif args.command == "bootstrap":
        recovery.bootstrap()
    else:
        return 0 if recovery.reconcile() else 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"HA entry recovery failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
