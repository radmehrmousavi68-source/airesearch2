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

    def approve(self, action, args):
        prepared = system_tools.prepare_action(action, args)
        return system_tools.approve_action(prepared["approval_id"])

    def test_create_project_builds_clean_structure(self):
        result = self.approve("create_project", {"name": "demo_app"})
        project = Path(result["path"])
        for folder in ("src", "tests", "data", "output", "logs"):
            self.assertTrue((project / folder).is_dir())
        self.assertTrue((project / "requirements.txt").is_file())

    def test_existing_project_is_not_overwritten(self):
        (self.root / "existing").mkdir()
        with self.assertRaises(system_tools.SystemToolError):
            system_tools.prepare_action("create_project", {"name": "existing"})

    def test_python_file_must_be_new(self):
        self.approve("create_project", {"name": "demo"})
        self.approve("write_python", {
            "project": "demo", "filename": "hello.py", "content": "print('hello')"
        })
        with self.assertRaises(system_tools.SystemToolError):
            system_tools.prepare_action("write_python", {
                "project": "demo", "filename": "hello.py", "content": "print('overwrite')"
            })

    def test_approval_is_one_time(self):
        prepared = system_tools.prepare_action("create_project", {"name": "once"})
        system_tools.approve_action(prepared["approval_id"])
        with self.assertRaises(system_tools.SystemToolError):
            system_tools.approve_action(prepared["approval_id"])

    def test_invalid_package_name_is_rejected(self):
        self.approve("create_project", {"name": "demo"})
        with self.assertRaises(system_tools.SystemToolError):
            system_tools.prepare_action("install_packages", {
                "project": "demo", "packages": ["requests;invalid"]
            })


if __name__ == "__main__":
    unittest.main()
