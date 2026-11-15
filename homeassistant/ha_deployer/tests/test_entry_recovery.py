"""Real staged files/JSON; Windows substitutes only unavailable directory links.

Linux also runs actual link and CGI tests. No NAS, Docker or systemd is touched.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[2] / "xiaomi-nas-plugin"
spec = importlib.util.spec_from_file_location("entry_recovery", PLUGIN / "scripts/entry_recovery.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ha-entry-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "nas"
        self.root.mkdir()
        self.source = Path(self.temp.name) / "source"
        shutil.copytree(PLUGIN, self.source, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.uid = "123456789"
        for path in self.source.rglob("*"):
            if path.is_file() and path.suffix != ".png":
                data = path.read_text(encoding="utf-8")
                path.write_text(data.replace("__NAS_UID__", self.uid).replace("__NAS_HOST__", "192.0.2.10"), encoding="utf-8")
        self.recovery = module.Recovery(self.root)
        self.listing = self.recovery.targets(self.uid)[4]
        self.listing.parent.mkdir(parents=True)
        self.official = {"xunlei": {"status": "running", "opaque": [1, "迅雷", {"new": True}]} }
        self.listing.write_bytes(module.json_bytes(self.official))
        # Windows cannot create Linux links without extra system privileges.
        # Files, snapshots, locking, registry and validation remain the real code.
        if os.name == "nt":
            self.link_patch = patch.object(self.recovery, "symlink", return_value=False)
            self.link_patch.start()
            self.addCleanup(self.link_patch.stop)

    def install(self, replace=False):
        self.recovery.install(self.uid, self.source, replace=replace, apply=True)

    def current(self):
        return module.read_json(self.listing)

    def record(self):
        return module.read_json(self.recovery.record_path(self.uid))

    def clear_entry(self):
        self.listing.write_bytes(module.json_bytes(self.official))

    def test_dry_run_has_no_writes(self):
        before = self.listing.read_bytes()
        self.recovery.install(self.uid, self.source)
        self.assertEqual(before, self.listing.read_bytes())
        self.assertFalse(self.recovery.base.exists())

    def test_install_preserves_visual_assets_and_official_plugins(self):
        self.install()
        src = self.recovery.targets(self.uid)[0]
        self.assertEqual((src / "ui/index.html").read_bytes(), (self.source / "ui/index.html").read_bytes())
        self.assertEqual((src / "ui/icon.png").read_bytes(), (PLUGIN / "ui/icon.png").read_bytes())
        self.assertEqual(self.current()["xunlei"], self.official["xunlei"])
        template = module.read_json(self.source / "registration/homeassistant.entry.json")
        self.assertEqual(self.current()["homeassistant"], template["homeassistant"])

    def test_refresh_and_reboot_style_wipe_recover(self):
        self.install()
        self.clear_entry()
        src, home, ui, icon, _ = self.recovery.targets(self.uid)
        for link in (ui, home / "src"):
            if link.is_symlink():
                link.unlink()
        shutil.rmtree(src)
        shutil.rmtree(home)
        icon.unlink()
        self.assertTrue(self.recovery.reconcile())
        self.assertIn("homeassistant", self.current())
        self.assertEqual((src / "INFO").read_bytes(), (self.source / "INFO").read_bytes())
        self.assertTrue(icon.is_file())
        if os.name == "posix":
            self.assertEqual((ui / "index.html").read_bytes(), (self.source / "ui/index.html").read_bytes())

    def test_idle_check_does_not_rewrite_files_or_registry(self):
        self.install()
        record = self.recovery.record_path(self.uid)
        mtimes = (self.listing.stat().st_mtime_ns, record.stat().st_mtime_ns)
        backups = len(list((record.parent / "list-backups").glob("*.json")))
        self.assertTrue(self.recovery.reconcile())
        self.assertEqual(mtimes, (self.listing.stat().st_mtime_ns, record.stat().st_mtime_ns))
        self.assertEqual(backups, len(list((record.parent / "list-backups").glob("*.json"))))

    def test_native_operation_defers_without_changing_registry(self):
        self.install()
        self.clear_entry()
        before = self.listing.read_bytes()
        with patch.object(self.recovery, "native_busy", return_value=True):
            self.assertFalse(self.recovery.reconcile())
        self.assertEqual(self.listing.read_bytes(), before)
        self.assertTrue(self.recovery.reconcile())

    def test_malformed_registry_is_not_overwritten(self):
        self.install()
        self.listing.write_bytes(b'{"unfinished":')
        self.assertFalse(self.recovery.reconcile())
        self.assertEqual(self.listing.read_bytes(), b'{"unfinished":')

    def test_missing_registry_is_not_created_during_boot(self):
        self.install()
        self.listing.unlink()
        self.assertFalse(self.recovery.reconcile())
        self.assertFalse(self.listing.exists())

    def test_damaged_snapshot_prevents_all_repairs(self):
        self.install()
        record = self.record()
        payload = self.recovery.record_path(self.uid).parent / record["generation"]
        (payload / "ui/index.html").write_bytes(b"corrupted")
        self.clear_entry()
        before = self.listing.read_bytes()
        self.assertFalse(self.recovery.reconcile())
        self.assertEqual(self.listing.read_bytes(), before)

    def test_uid_rejects_traversal(self):
        for uid in ("../1", "1/../2", "u123", "", "1;id", "1\n"):
            with self.assertRaises(RuntimeError):
                self.recovery.record_path(uid)

    def test_explicit_disable_survives_subsequent_refresh(self):
        self.install()
        current = self.current()
        current["homeassistant"].update(enable=False, status="stopped")
        self.listing.write_bytes(module.json_bytes(current))
        self.assertTrue(self.recovery.reconcile())
        self.clear_entry()
        self.assertTrue(self.recovery.reconcile())
        self.assertFalse(self.current()["homeassistant"]["enable"])

    def test_uninstall_does_not_resurrect_entry_or_touch_ha(self):
        self.install()
        config = self.recovery.path("/data/homeassistant/config/configuration.yaml")
        config.parent.mkdir(parents=True)
        config.write_bytes(b"user configuration\n")
        self.recovery.disable(self.uid, apply=True)
        self.assertTrue(self.recovery.reconcile())
        self.assertNotIn("homeassistant", self.current())
        self.assertFalse(self.record()["active"])
        self.assertEqual(config.read_bytes(), b"user configuration\n")

    def test_vendor_uninstalled_flag_is_recovered(self):
        self.install()
        value = self.current()
        value["homeassistant"].update(install=False, enable=False, status="uninstalled")
        self.listing.write_bytes(module.json_bytes(value))
        self.assertTrue(self.recovery.reconcile())
        entry = self.current()["homeassistant"]
        self.assertTrue(entry["install"])
        self.assertTrue(entry["enable"])

    def test_duplicate_install_requires_replace(self):
        self.install()
        with self.assertRaises(RuntimeError):
            self.install()
        self.install(replace=True)

    def test_conflicting_registration_is_left_untouched(self):
        self.install()
        value = self.current()
        value["homeassistant"]["info"]["id"] = 123
        self.listing.write_bytes(module.json_bytes(value))
        before = self.listing.read_bytes()
        self.assertFalse(self.recovery.reconcile())
        self.assertEqual(before, self.listing.read_bytes())

    def test_recovery_preserves_new_vendor_entries(self):
        self.install()
        self.clear_entry()
        current = self.current()
        current["new_official_app"] = {"unknown": [1, 2, 3]}
        self.listing.write_bytes(module.json_bytes(current))
        self.assertTrue(self.recovery.reconcile())
        self.assertEqual(self.current()["new_official_app"], {"unknown": [1, 2, 3]})

    def test_other_users_are_not_changed(self):
        other = self.recovery.targets("987654321")[4]
        other.write_bytes(module.json_bytes({"another": {"enable": False}}))
        before = other.read_bytes()
        self.install()
        self.clear_entry()
        self.assertTrue(self.recovery.reconcile())
        self.assertEqual(before, other.read_bytes())

    def test_backups_and_snapshots_are_bounded(self):
        self.install()
        for _ in range(12):
            self.clear_entry()
            self.recovery.reconcile()
        parent = self.recovery.record_path(self.uid).parent
        self.assertLessEqual(len(list((parent / "list-backups").glob("*.json"))), 10)
        for _ in range(3):
            self.install(replace=True)
        generations = [p for p in parent.iterdir() if len(p.name) == 32]
        self.assertEqual(len(generations), 2)

    def test_unsubstituted_template_is_rejected(self):
        with self.assertRaises(RuntimeError):
            self.recovery.install(self.uid, PLUGIN, apply=True)
        self.assertFalse(self.recovery.base.exists())

    def test_offline_install_never_calls_systemctl(self):
        with patch.object(self.recovery, "systemctl", side_effect=AssertionError("host service touched")):
            self.install()
            self.recovery.reconcile()

    @unittest.skipUnless(os.name == "posix", "requires actual Linux symbolic links")
    def test_symlink_escape_is_rejected(self):
        self.install()
        src = self.recovery.targets(self.uid)[0]
        (src / "ui/index.html").unlink()
        outside = Path(self.temp.name) / "outside"
        outside.write_bytes(b"do not overwrite")
        (src / "ui/index.html").symlink_to(outside)
        self.assertFalse(self.recovery.reconcile())
        self.assertEqual(outside.read_bytes(), b"do not overwrite")

    @unittest.skipUnless(os.name == "posix", "requires Linux sh and file-descriptor invocation")
    def test_cgi_fd_launch_and_path_escape(self):
        self.install()
        script = self.recovery.targets(self.uid)[0] / "ui/homeassistant.cgi"
        # Standard shell invocation verifies static output and containment.
        result = subprocess.run(["sh", str(script)], env={**os.environ, "FILE_URI": "index.html"}, capture_output=True)
        self.assertIn(b"200 OK", result.stdout)
        self.assertIn(b"window.open", result.stdout)
        result = subprocess.run(["sh", str(script)], env={**os.environ, "FILE_URI": "../INFO"}, capture_output=True)
        self.assertIn(b"400 Bad Request", result.stdout)
        # Point the fixed deployed path at our staging tree, then pass an actual
        # script fd, reproducing the vendor's /dev/fd/<n> script-name behavior.
        rendered = Path(self.temp.name) / "fd-cgi.sh"
        rendered.write_text(script.read_text().replace(
            f"ROOT=/data/plugin/www/u{self.uid}/homeassistant",
            "ROOT=" + shlex.quote(str(script.parent))))
        with rendered.open("rb") as fd:
            result = subprocess.run(["sh", f"/dev/fd/{fd.fileno()}"], pass_fds=(fd.fileno(),),
                                    env={**os.environ, "FILE_URI": "index.html"}, capture_output=True)
        self.assertIn(b"200 OK", result.stdout)

    def test_install_writes_only_dedicated_recovery_units(self):
        with patch.object(self.recovery, "systemctl") as systemctl, patch.object(module.sys, "executable", "/usr/bin/python3"):
            self.recovery.services()
        units = self.root / "etc/systemd/system"
        self.assertEqual(len(list(units.iterdir())), 3)
        self.assertIn("OnUnitActiveSec=60", (units / (module.UNIT + "-check.timer")).read_text())
        self.assertIn("RequiresMountsFor=/data\n", (units / (module.UNIT + ".service")).read_text())
        self.assertEqual(systemctl.call_count, 5)
        cron = self.root / "etc/cron.d" / module.UNIT
        self.assertIn("SHELL=/usr/bin/bash.bash", cron.read_text())
        self.assertIn("entry_recovery.py bootstrap", cron.read_text())

    def test_registry_changed_during_merge_is_not_overwritten(self):
        self.install()
        self.clear_entry()
        original = self.listing.read_bytes()
        replacement = module.json_bytes({"official_changed": {"keep": 1}})
        real_read = Path.read_bytes
        calls = 0
        def changing_read(path):
            nonlocal calls
            if path == self.listing:
                calls += 1
                if calls == 2:
                    path.write_bytes(replacement)
            return real_read(path)
        with patch.object(Path, "read_bytes", changing_read):
            with self.assertRaisesRegex(RuntimeError, "changed during recovery"):
                self.recovery.merge(self.uid, self.record()["entry"])
        self.assertNotEqual(original, self.listing.read_bytes())
        self.assertEqual(replacement, self.listing.read_bytes())

    def test_dhcp_address_change_updates_only_launch_destination(self):
        self.install()
        before = (self.source / "ui/index.html").read_bytes()
        with patch.object(self.recovery, "current_lan_ip", return_value="192.168.50.20"):
            self.assertTrue(self.recovery.reconcile())
        page = (self.recovery.targets(self.uid)[0] / "ui/index.html").read_bytes()
        self.assertEqual(page, before.replace(b"192.0.2.10:8123", b"192.168.50.20:8123"))
        self.assertEqual(self.record()["hashes"]["ui/index.html"], module.digest(page))
        self.assertTrue(self.recovery.reconcile())

    def test_custom_dns_destination_is_preserved(self):
        path = self.source / "ui/index.html"
        path.write_bytes(path.read_bytes().replace(b"192.0.2.10:8123", b"nas.example.test:8123"))
        self.install()
        with patch.object(self.recovery, "current_lan_ip", return_value="192.168.50.20"):
            self.assertTrue(self.recovery.reconcile())
        page = (self.recovery.targets(self.uid)[0] / "ui/index.html").read_bytes()
        self.assertIn(b"http://nas.example.test:8123", page)
        self.assertNotIn(b"192.168.50.20", page)

    def test_lan_detection_uses_route_source_without_network_request(self):
        self.recovery.live = True
        result = subprocess.CompletedProcess([], 0, stdout="1.1.1.1 via 192.168.50.1 dev eth0 src 192.168.50.20 uid 0\n")
        with patch.object(module.subprocess, "run", return_value=result):
            self.assertEqual(self.recovery.current_lan_ip(), "192.168.50.20")
        self.recovery.live = False

    def test_invalid_or_missing_route_does_not_rewrite_page(self):
        self.install()
        self.recovery.live = True
        result = subprocess.CompletedProcess([], 0, stdout="1.1.1.1 dev lo src 127.0.0.1\n")
        with patch.object(module.subprocess, "run", return_value=result):
            self.assertIsNone(self.recovery.current_lan_ip())
        self.recovery.live = False

    def test_bootstrap_repairs_mixed_unit_states(self):
        self.install()
        self.recovery.live = True
        result = subprocess.CompletedProcess([], 0, stdout="inactive\nactive\n")
        with patch.object(module.subprocess, "run", return_value=result), patch.object(self.recovery, "services") as services:
            self.recovery.bootstrap()
            services.assert_called_once()
        self.recovery.live = False

    def test_bootstrap_is_idle_when_both_units_are_active(self):
        self.install()
        self.recovery.live = True
        result = subprocess.CompletedProcess([], 0, stdout="active\nactive\n")
        with patch.object(module.subprocess, "run", return_value=result), patch.object(self.recovery, "services") as services:
            self.recovery.bootstrap()
            services.assert_not_called()
        self.recovery.live = False

    @unittest.skipUnless(os.name == "posix", "requires Linux permissions and inotify")
    def test_umask_and_real_event_recovery(self):
        self.install()
        src, home, ui, icon, _ = self.recovery.targets(self.uid)
        ui.unlink()
        shutil.rmtree(ui.parent)
        icon.unlink()
        icon.parent.rmdir()
        previous = os.umask(0o077)
        try:
            self.assertTrue(self.recovery.reconcile())
        finally:
            os.umask(previous)
        self.assertEqual(ui.parent.stat().st_mode & 0o777, 0o755)
        self.assertEqual(icon.parent.stat().st_mode & 0o777, 0o755)
        process = subprocess.Popen([__import__("sys").executable, str(PLUGIN / "scripts/entry_recovery.py"),
                                    "watch", "--root", str(self.root)], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        try:
            time.sleep(1)
            self.clear_entry()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if "homeassistant" in self.current():
                    break
                time.sleep(0.2)
            self.assertIn("homeassistant", self.current(), "event repair must beat the 60-second fallback")
            self.assertIsNone(process.poll())
        finally:
            process.terminate()
            process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
