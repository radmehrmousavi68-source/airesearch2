import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_chat import system_tools


class SystemToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "projects"
        self.root.mkdir()
        self.root_patch = patch.object(system_tools, "PROJECTS_ROOT", self.root)
        self.root_patch.start()
        self.pending_patch = patch.dict(system_tools._pending, {}, clear=True)
        self.pending_patch.start()

    def tearDown(self):
        self.pending_patch.stop()
        self.root_patch.stop()
        self.temp.cleanup()

    SESSION = "test-session-" + ("x" * 32)
    OTHER_SESSION = "other-session-" + ("y" * 32)

    def approve(self, action, args):
        prepared = system_tools.prepare_action(action, args, self.SESSION)
        return system_tools.approve_action(prepared["approval_id"], self.SESSION)

    def test_create_project_builds_clean_structure(self):
        result = self.approve("create_project", {"name": "demo_app"})
        project = Path(result["path"])
        for folder in ("src", "tests", "data", "output", "logs"):
            self.assertTrue((project / folder).is_dir())
        self.assertTrue((project / "requirements.txt").is_file())

    def test_existing_project_is_not_overwritten(self):
        (self.root / "existing").mkdir()
        with self.assertRaises(system_tools.SystemToolError):
            system_tools.prepare_action("create_project", {"name": "existing"}, self.SESSION)

    def test_python_file_must_be_new(self):
        self.approve("create_project", {"name": "demo"})
        self.approve("write_python", {
            "project": "demo", "filename": "hello.py", "content": "print('hello')"
        })
        with self.assertRaises(system_tools.SystemToolError):
            system_tools.prepare_action("write_python", {
                "project": "demo", "filename": "hello.py", "content": "print('overwrite')"
            }, self.SESSION)

    def test_approval_is_one_time(self):
        prepared = system_tools.prepare_action("create_project", {"name": "once"}, self.SESSION)
        system_tools.approve_action(prepared["approval_id"], self.SESSION)
        with self.assertRaises(system_tools.SystemToolError):
            system_tools.approve_action(prepared["approval_id"])

    def test_approval_is_bound_to_the_preparing_session(self):
        prepared = system_tools.prepare_action("create_project", {"name": "session_bound"}, self.SESSION)
        with self.assertRaisesRegex(system_tools.SystemToolError, "نشست"):
            system_tools.approve_action(prepared["approval_id"], self.OTHER_SESSION)
        self.assertFalse((self.root / "session_bound").exists())
        system_tools.approve_action(prepared["approval_id"], self.SESSION)

    def test_reject_is_bound_to_the_preparing_session(self):
        prepared = system_tools.prepare_action("create_project", {"name": "session_reject"}, self.SESSION)
        self.assertFalse(system_tools.reject_action(prepared["approval_id"], self.OTHER_SESSION))
        self.assertTrue(system_tools.reject_action(prepared["approval_id"], self.SESSION))

    def test_session_is_required_to_prepare(self):
        with self.assertRaises(system_tools.SystemToolError):
            system_tools.prepare_action("create_project", {"name": "no_session"}, None)

    def test_invalid_package_name_is_rejected(self):
        self.approve("create_project", {"name": "demo"})
        with self.assertRaises(system_tools.SystemToolError):
            system_tools.prepare_action("install_packages", {
                "project": "demo", "packages": ["requests;invalid"]
            }, self.SESSION)

    def test_run_python_is_blocked_until_sandbox_exists(self):
        self.approve("create_project", {"name": "blocked_run"})
        self.approve("create_venv", {"project": "blocked_run"})
        self.approve("write_python", {
            "project": "blocked_run", "filename": "hello.py", "content": "print('hello')"
        })
        with self.assertRaisesRegex(system_tools.SystemToolError, "sandbox"):
            system_tools.prepare_action("run_python", {
                "project": "blocked_run", "filename": "hello.py"
            }, self.SESSION)

    def test_install_packages_is_blocked_until_sandbox_exists(self):
        self.approve("create_project", {"name": "blocked_install"})
        self.approve("create_venv", {"project": "blocked_install"})
        with self.assertRaisesRegex(system_tools.SystemToolError, "sandbox"):
            system_tools.prepare_action("install_packages", {
                "project": "blocked_install", "packages": ["requests==2.32.3"]
            }, self.SESSION)


if __name__ == "__main__":
    unittest.main()
