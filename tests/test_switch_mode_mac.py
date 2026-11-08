"""Exercise the actual shell script with isolated configuration and mocked OS services."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MOCK = r'''#!PYTHON
import json, os, pathlib, subprocess, sys
state_path = pathlib.Path(os.environ["MODE_TEST_STATE"])
state = json.loads(state_path.read_text())
command = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
def save():
    state_path.write_text(json.dumps(state))
if command == "osascript":
    source = sys.stdin.read()
    if "choose from list" in source:
        print(os.environ.get("MOCK_CHOICE", "field"))
    elif "with administrator privileges" in source:
        state["authorizations"] += 1
        save()
        if os.environ.get("MOCK_DENY_AUTH"):
            sys.exit(1)
        sys.exit(subprocess.call(args[1:]))
    else:
        state["dialogs"].append(args[1])
        save()
elif command == "launchctl":
    if args[0] == "print":
        if not state["tunnel"]:
            sys.exit(1)
        if state.get("tunnel_pid", True):
            print("    pid = 99999999")
    else:
        state["tunnel"] = args[0] != "bootout"
        state["tunnel_pid"] = True
        save()
elif command == "pkill":
    state["backend"] = None
    save()
elif command == "pgrep":
    sys.exit(0 if state["backend"] else 1)
elif command == "python":
    source = sys.stdin.read()
    def capture_backend(command, **kwargs):
        start_script = pathlib.Path(command[0])
        if not start_script.is_file():
            print("backend launcher path does not exist", file=sys.stderr)
            sys.exit(99)
        state["backend_start_script"] = str(start_script)
        state["backend_start_cwd"] = str(kwargs["cwd"])
    subprocess.Popen = capture_backend
    sys.argv = ["-", *args[1:]]
    exec(compile(source, "isolated backend restart", "exec"), {"__name__": "__main__"})
    config = (pathlib.Path(args[1]) / ".env").read_text()
    state["backend"] = "field" if "INVENTORY_DEV_LOGIN=0" in config else "online"
    state["starts"] += 1
    save()
elif command == "curl":
    public = args[-1].startswith("https:")
    if not state["backend"] or os.environ.get("MOCK_BACKEND_FAIL_MODE") in [state["backend"], "all"]:
        sys.exit(22)
    if public and (not state["tunnel"] or os.environ.get("MOCK_PUBLIC_FAIL")):
        sys.exit(22)
    print('<html>unavailable</html>' if public and os.environ.get("MOCK_PUBLIC_HTML") else '{"status":"ok"}')
elif command == "sleep":
    import time
    time.sleep(0.02)
'''


class SwitchModeMacTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="盘点 mode ")
        self.project = Path(self.temp.name)
        self.script_dir = self.project / "scripts/mac"
        self.script_dir.mkdir(parents=True)
        self.run_cwd = self.project / "unrelated working directory"
        self.run_cwd.mkdir()
        self.bin = self.project / "bin"
        self.bin.mkdir()
        self.state_path = self.project / "state.json"
        self.env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}",
                    "MODE_TEST_STATE": str(self.state_path), "TMPDIR": str(self.project)}
        for name in ["osascript", "launchctl", "curl", "pgrep", "pkill", "sleep", "python"]:
            target = self.bin / name
            target.write_text(MOCK.replace("PYTHON", sys.executable, 1))
            target.chmod(0o755)
        (self.project / ".venv/bin").mkdir(parents=True)
        (self.project / ".venv/bin/python").symlink_to(self.bin / "python")
        (self.project / "miniprogram/pages/login").mkdir(parents=True)
        (self.script_dir / "start_backend_mac.sh").write_text("#!/bin/bash\nexit 0\n")
        (self.script_dir / "start_backend_mac.sh").chmod(0o755)
        self.plist = self.project / "tunnel.plist"
        self.plist.touch()
        script = (ROOT / "scripts/mac/switch_mode_mac.command").read_text()
        script = script.replace("/usr/bin/osascript", f'"{self.bin / "osascript"}"')
        script = script.replace("/bin/launchctl", f'"{self.bin / "launchctl"}"')
        script = script.replace("/Library/LaunchDaemons/com.cloudflare.cloudflared.plist", str(self.plist))
        for deadline in [10, 15, 30, 35]:
            script = script.replace(f"SECONDS + {deadline}", "SECONDS + 1")
        self.script = self.script_dir / "switch_mode_mac.command"
        self.script.write_text(script)
        self.configure("online")

    def tearDown(self):
        self.temp.cleanup()

    def configure(self, mode, tunnel=None):
        field = mode == "field"
        (self.project / ".env").write_text(
            f"INVENTORY_DEV_LOGIN={0 if field else 1}\nINVENTORY_ALLOW_LOGIN_REQUESTS={1 if field else 0}\nOTHER_SECRET=preserve-me\n")
        api = "https://api.equip-inventory.xyz/api" if field else "http://localhost:8000/api"
        (self.project / "miniprogram/env.js").write_text(f'module.exports = {{\n  devApiBase: "{api}",\n}};\n')
        (self.project / "miniprogram/pages/login/login.js").write_text(f'const ENABLE_DEV_LOGIN = {"false" if field else "true"};\n')
        self.state_path.write_text(json.dumps({"tunnel": field if tunnel is None else tunnel,
                                              "backend": mode, "starts": 0, "authorizations": 0, "dialogs": []}))

    def configs(self):
        return [(self.project / path).read_bytes() for path in [".env", "miniprogram/env.js", "miniprogram/pages/login/login.js"]]

    def state(self):
        return json.loads(self.state_path.read_text())

    def run_mode(self, mode=None, **options):
        return subprocess.run(["/bin/bash", str(self.script), *([mode] if mode else [])],
                              cwd=self.run_cwd, env={**self.env, **options}, capture_output=True, text=True, timeout=15)

    def assert_restored(self, before, mode):
        self.assertEqual(self.configs(), before)
        self.assertEqual(self.state()["backend"], mode)
        self.assertEqual(self.state()["tunnel"], mode == "field")

    def test_both_modes_and_existing_tunnel_reuse(self):
        for mode in ["field", "field", "online"]:
            result = self.run_mode(mode)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.state()["backend"], mode)
            self.assertEqual(self.state()["tunnel"], mode == "field")
            self.assertIn("OTHER_SECRET=preserve-me", self.configs()[0].decode())
            self.assertIn(f"INVENTORY_DEV_LOGIN={0 if mode == 'field' else 1}", self.configs()[0].decode())
            self.assertIn(f"ENABLE_DEV_LOGIN = {'false' if mode == 'field' else 'true'}", self.configs()[2].decode())
            self.assertEqual(self.state()["authorizations"], 2 if mode == "online" else 1)
        self.assertEqual(list(self.project.glob("inventory-mode.*")), [])

    def test_moved_launcher_resolves_project_and_backend_from_unrelated_directory(self):
        result = self.run_mode("field")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(" ", str(self.project))
        self.assertEqual(self.state()["backend_start_script"], str(self.script_dir / "start_backend_mac.sh"))
        self.assertEqual(self.state()["backend_start_cwd"], str(self.project))
        self.assertTrue((self.project / "backend_mac.log").exists())
        self.assertEqual(list(self.run_cwd.iterdir()), [])

    def test_authorization_cancel_does_not_change_configs_or_restart(self):
        for initial, target in [("online", "field"), ("field", "online")]:
            self.configure(initial)
            before = self.configs()
            result = self.run_mode(target, MOCK_DENY_AUTH="1")
            self.assertNotEqual(result.returncode, 0)
            self.assert_restored(before, initial)
            self.assertEqual(self.state()["starts"], 0)

    def test_failed_backend_restores_old_mode_and_tunnel(self):
        for initial, target in [("online", "field"), ("field", "online")]:
            self.configure(initial)
            before = self.configs()
            result = self.run_mode(target, MOCK_BACKEND_FAIL_MODE=target)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("后端启动失败", result.stdout)
            self.assert_restored(before, initial)

    def test_unreachable_or_wrong_public_response_rolls_back(self):
        for failure in ["MOCK_PUBLIC_FAIL", "MOCK_PUBLIC_HTML"]:
            self.configure("online")
            before = self.configs()
            result = self.run_mode("field", **{failure: "1"})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("公网接口检查未通过", result.stdout)
            self.assert_restored(before, "online")

    def test_gui_selection_result_and_cancel(self):
        before = self.configs()
        result = self.run_mode(MOCK_CHOICE="cancel")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_restored(before, "online")
        self.assertEqual(self.state()["starts"], 0)
        result = self.run_mode(MOCK_CHOICE="field")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("已切换到线下测试模式", self.state()["dialogs"][-1])

    def test_missing_configuration_fails_before_service_changes(self):
        (self.project / ".env").write_text("INVENTORY_DEV_LOGIN=1\n")
        before = self.configs()
        result = self.run_mode("field")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.configs(), before)
        self.assertEqual(self.state()["authorizations"], 0)
        self.assertEqual(self.state()["starts"], 0)

    def test_loaded_but_inactive_tunnel_is_started(self):
        self.configure("field")
        state = self.state()
        state["tunnel_pid"] = False
        self.state_path.write_text(json.dumps(state))
        result = self.run_mode("field")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.state()["tunnel_pid"])
        self.assertEqual(self.state()["authorizations"], 1)

    def test_cold_start_and_failed_switch_restore_stopped_backend(self):
        for fail_public in [False, True]:
            self.configure("online")
            state = self.state()
            state["backend"] = None
            self.state_path.write_text(json.dumps(state))
            before = self.configs()
            result = self.run_mode("field", **({"MOCK_PUBLIC_FAIL": "1"} if fail_public else {}))
            if fail_public:
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.configs(), before)
                self.assertIsNone(self.state()["backend"])
                self.assertFalse(self.state()["tunnel"])
            else:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.state()["backend"], "field")
                self.assertTrue(self.state()["tunnel"])

    def test_failed_service_recovery_keeps_private_config_backups(self):
        before = self.configs()
        result = self.run_mode("field", MOCK_BACKEND_FAIL_MODE="all")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("服务恢复未成功", result.stdout)
        self.assertEqual(self.configs(), before)
        backups = list(self.project.glob("inventory-mode.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o700)
        self.assertEqual((backups[0] / "backend.env").read_bytes(), before[0])


if __name__ == "__main__":
    unittest.main()
