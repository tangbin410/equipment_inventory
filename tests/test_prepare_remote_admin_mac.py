"""在临时项目运行实际准备脚本；npm、HTTP 探测均隔离，不操作真实服务。"""

import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
REMOTE_SETTINGS = {
    "INVENTORY_REMOTE_ADMIN_ENABLED": "1",
    "INVENTORY_ADMIN_HOST": "admin.example.test",
    "CLOUDFLARE_ACCESS_TEAM_DOMAIN": "https://unit-team.cloudflareaccess.com",
    "CLOUDFLARE_ACCESS_AUD": "private-audience-do-not-print",
    "INVENTORY_ADMIN_ALLOWED_EMAILS": "private-person@example.test",
}
MOCK = r'''#!PYTHON
import json,os,pathlib,shutil,subprocess,sys
name=pathlib.Path(sys.argv[0]).name
state_path=pathlib.Path(os.environ["REMOTE_TEST_STATE"])
state=json.loads(state_path.read_text())
state.append({"command":name,"args":sys.argv[1:],"api_base":os.getenv("VITE_API_BASE"),"cwd":os.getcwd()})
state_path.write_text(json.dumps(state))
if name=="npm":
    output=pathlib.Path(sys.argv[sys.argv.index("--outDir")+1])
    shutil.rmtree(output,ignore_errors=True)
    output.mkdir(parents=True,exist_ok=True)
    if os.getenv("REMOTE_TEST_BUILD_FAIL"):
        (output/"incomplete.txt").write_text("broken output")
        sys.exit(1)
    if os.getenv("REMOTE_TEST_NO_INDEX"): sys.exit(0)
    (output/"index.html").write_text("isolated remote build")
elif name=="mv":
    if os.getenv("REMOTE_TEST_SWAP_FAIL") and pathlib.Path(sys.argv[1]).name.startswith(".remote-admin-build."):
        sys.exit(1)
    sys.exit(subprocess.call(["/bin/mv",*sys.argv[1:]]))
elif name=="curl":
    if os.getenv("REMOTE_TEST_HTTP_DOWN"): sys.exit(22)
    print(os.getenv("REMOTE_TEST_HTTP_BODY",'{"status":"ok"}'))
else:
    print("unexpected process management",file=sys.stderr)
    sys.exit(99)
'''


class PrepareRemoteAdminMacTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="异地后台 prepare ")
        self.project = Path(self.temp.name)
        self.script_dir = self.project / "scripts/mac"
        self.script_dir.mkdir(parents=True)
        self.run_cwd = self.project / "unrelated working directory"
        self.run_cwd.mkdir()
        self.bin = self.project / "bin"
        self.bin.mkdir()
        self.state_path = self.project / "state.json"
        self.state_path.write_text("[]")
        self.script = self.script_dir / "prepare_remote_admin_mac.command"
        shutil.copy2(ROOT / "scripts/mac" / self.script.name, self.script)
        (self.project / "backend").mkdir()
        (self.project / "backend/__init__.py").write_text("")
        shutil.copy2(ROOT / "backend/admin_access.py", self.project / "backend/admin_access.py")
        (self.project / ".venv/bin").mkdir(parents=True)
        python = self.project / ".venv/bin/python"
        python.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
        python.chmod(0o755)
        for name in ("npm", "curl", "mv", "pkill", "killall", "launchctl", "sudo"):
            target = self.bin / name
            target.write_text(MOCK.replace("PYTHON", sys.executable, 1))
            target.chmod(0o755)
        (self.project / "admin_web").mkdir()
        (self.project / "admin_web/package.json").write_text('{"scripts":{"build":"mock"}}')
        (self.script_dir / "start_backend_mac.sh").write_text('#!/bin/bash\ntouch "unexpected-backend-start"\n')
        (self.script_dir / "start_backend_mac.sh").chmod(0o755)
        self.env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "REMOTE_TEST_STATE": str(self.state_path)}
        for name in REMOTE_SETTINGS:
            self.env.pop(name, None)
        self.configure()

    def tearDown(self):
        self.temp.cleanup()

    def configure(self, values=None):
        self.config = self.project / ".env"
        values = REMOTE_SETTINGS if values is None else values
        self.config.write_text("\n".join(f"{name}={value}" for name, value in values.items()) + "\n")
        self.config_before = self.config.read_bytes()

    def run_script(self, *args):
        result = subprocess.run(["/bin/bash", str(self.script), *args], cwd=self.run_cwd,
                                env=self.env, input="", capture_output=True, text=True, timeout=20)
        self.assertEqual(self.config.read_bytes(), self.config_before, "准备、自检不得修改真实运行配置")
        self.assertFalse((self.project / "unexpected-backend-start").exists())
        self.assertTrue(all(row["command"] in {"npm", "curl", "mv"} for row in self.commands()), "不能管理已有进程或隧道")
        self.assertEqual(list((self.project / "admin_web").glob(".remote-admin-build.*")), [], "退出时清理本次临时构建目录")
        self.assertEqual(list((self.project / "admin_web").glob(".remote-admin-previous.*")), [], "成功替换或恢复后清理本次临时备份")
        return result

    def commands(self):
        return json.loads(self.state_path.read_text())

    def existing_build(self):
        output = self.project / "admin_web/remote_dist"
        output.mkdir()
        (output / "index.html").write_text("existing build")

    def test_build_uses_relative_api_and_reports_local_readiness_without_leaking_values(self):
        self.existing_build()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        build = next(row for row in self.commands() if row["command"] == "npm")
        probe = next(row for row in self.commands() if row["command"] == "curl")
        self.assertEqual(build["args"][:-1], ["--prefix", "admin_web", "run", "build", "--", "--outDir"])
        stage = Path(build["args"][-1])
        self.assertEqual(stage.parent, self.project / "admin_web")
        self.assertTrue(stage.name.startswith(".remote-admin-build."), "npm只能清理临时构建目录，不能清空正在使用的旧页面")
        self.assertEqual(build["api_base"], "/api")
        self.assertEqual(Path(build["cwd"]).resolve(), self.project.resolve())
        self.assertEqual((self.project / "admin_web/remote_dist/index.html").read_text(), "isolated remote build")
        self.assertEqual(probe["args"][-1], "http://127.0.0.1:8000/health")
        self.assertIn("本机检查不代表公网已可访问", result.stdout)
        self.assertIn("不据此识别或管理", result.stdout)
        for secret in (REMOTE_SETTINGS["CLOUDFLARE_ACCESS_AUD"], REMOTE_SETTINGS["INVENTORY_ADMIN_ALLOWED_EMAILS"]):
            self.assertNotIn(secret, result.stdout + result.stderr)

    def test_check_only_from_unrelated_directory_resolves_project_with_spaces(self):
        self.existing_build()
        result = self.run_script("--check-only")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(" ", str(self.project))
        self.assertEqual([Path(row["cwd"]).resolve() for row in self.commands()], [self.project.resolve()])
        self.assertEqual(list(self.run_cwd.iterdir()), [])

    def test_disabled_or_missing_settings_do_not_prevent_build_completion(self):
        self.configure({"INVENTORY_REMOTE_ADMIN_ENABLED": "0"})
        result = self.run_script()
        self.assertEqual(result.returncode, 2)
        self.assertTrue((self.project / "admin_web/remote_dist/index.html").exists())
        self.assertEqual([row["command"] for row in self.commands()], ["npm", "mv", "curl"])
        for name in REMOTE_SETTINGS:
            self.assertIn(name, result.stdout)
        self.assertIn("异地后台尚未启用", result.stdout)

    def test_check_only_honors_explicit_environment_without_executing_dotenv_content(self):
        marker = self.project / "must-not-run"
        self.configure({"INVENTORY_REMOTE_ADMIN_ENABLED": "0", "DO_NOT_EXECUTE": f'$(touch "{marker}")'})
        self.env.update(REMOTE_SETTINGS)
        self.existing_build()
        result = self.run_script("--check-only")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(marker.exists(), ".env只作为数据解析，不能source执行")
        self.assertEqual([row["command"] for row in self.commands()], ["curl"])
        self.assertEqual((self.project / "admin_web/remote_dist/index.html").read_text(), "existing build")

    def test_unhealthy_or_unrelated_http_response_never_starts_or_kills_processes(self):
        self.existing_build()
        for body in ("<html>other service</html>", '{"status":"other"}'):
            with self.subTest(body=body):
                self.env["REMOTE_TEST_HTTP_BODY"] = body
                self.assertEqual(self.run_script("--check-only").returncode, 2)
        self.env["REMOTE_TEST_HTTP_DOWN"] = "1"
        result = self.run_script("--check-only")
        self.assertEqual(result.returncode, 2)
        self.assertIn("不会终止任何进程", result.stdout)
        self.assertTrue(all(row["command"] == "curl" for row in self.commands()))

    def test_check_only_requires_existing_bundle_without_building_it(self):
        result = self.run_script("--check-only")
        self.assertEqual(result.returncode, 2)
        self.assertIn("尚未生成异地后台页面", result.stdout)
        self.assertEqual([row["command"] for row in self.commands()], ["curl"])
        self.assertFalse((self.project / "admin_web/remote_dist").exists())

    def test_invalid_nonempty_config_is_rejected_without_echoing_input(self):
        self.configure({**REMOTE_SETTINGS, "CLOUDFLARE_ACCESS_TEAM_DOMAIN": "http://private-invalid-config.test/secret"})
        self.existing_build()
        result = self.run_script("--check-only")
        self.assertEqual(result.returncode, 2)
        self.assertIn("CLOUDFLARE_ACCESS_TEAM_DOMAIN", result.stdout)
        self.assertNotIn("private-invalid-config", result.stdout + result.stderr)
        self.assertNotIn("private-audience-do-not-print", result.stdout + result.stderr)

    def test_failed_build_stops_before_http_checks_without_changing_configuration(self):
        self.existing_build()
        self.env["REMOTE_TEST_BUILD_FAIL"] = "1"
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("页面构建未完成", result.stdout)
        self.assertEqual([row["command"] for row in self.commands()], ["npm"])
        self.assertEqual((self.project / "admin_web/remote_dist/index.html").read_text(), "existing build", "即使npm先清空输出再失败，旧页面也不受影响")

    def test_missing_generated_index_never_replaces_existing_site(self):
        self.existing_build()
        self.env["REMOTE_TEST_NO_INDEX"] = "1"
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("缺少 index.html", result.stdout)
        self.assertEqual([row["command"] for row in self.commands()], ["npm"])
        self.assertEqual((self.project / "admin_web/remote_dist/index.html").read_text(), "existing build")

    def test_failed_swap_restores_old_site_and_cleans_generated_staging(self):
        self.existing_build()
        self.env["REMOTE_TEST_SWAP_FAIL"] = "1"
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("原有页面已恢复", result.stdout)
        self.assertEqual([row["command"] for row in self.commands()], ["npm", "mv", "mv", "mv"])
        self.assertEqual((self.project / "admin_web/remote_dist/index.html").read_text(), "existing build")

    def test_symlink_output_is_rejected_without_touching_its_target(self):
        target = self.project / "untouched-output"
        target.mkdir()
        (target / "index.html").write_text("must remain")
        (self.project / "admin_web/remote_dist").symlink_to(target, target_is_directory=True)
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("符号链接", result.stdout)
        self.assertEqual(self.commands(), [])
        self.assertEqual((target / "index.html").read_text(), "must remain")


if __name__ == "__main__":
    unittest.main()
