import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_chat import docker_runtime


class DockerRuntimeTests(unittest.TestCase):
    def test_requirements_reject_pip_options_urls_and_local_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            (project / "requirements.txt").write_text("--index-url https://evil.invalid\n", encoding="utf-8")
            with self.assertRaises(docker_runtime.DockerRuntimeError):
                docker_runtime._requirements(project)
            (project / "requirements.txt").write_text("../evil.whl\n", encoding="utf-8")
            with self.assertRaises(docker_runtime.DockerRuntimeError):
                docker_runtime._requirements(project)

    def test_requirements_accept_simple_pinned_packages(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            (project / "requirements.txt").write_text("Django>=5.2,<6\npytest==8.3.5\n", encoding="utf-8")
            raw, packages = docker_runtime._requirements(project)
        self.assertEqual(packages, ["Django>=5.2,<6", "pytest==8.3.5"])
        self.assertIn("Django", raw)

    def test_download_and_test_are_separate_containers_and_scoped_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            (project / "requirements.txt").write_text("pytest==8.3.5\n", encoding="utf-8")
            download = type("Result", (), {"returncode": 0, "stdout": "wheel downloaded", "stderr": ""})()
            tested = type("Result", (), {"returncode": 0, "stdout": "2 passed", "stderr": ""})()
            with patch.object(docker_runtime, "_docker", return_value="docker"), \
                 patch.object(docker_runtime.subprocess, "run", return_value=type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()), \
                 patch.object(docker_runtime, "_run_container", side_effect=[download, tested]) as run_container, \
                 patch.object(docker_runtime, "cleanup_managed", return_value={"count": 0, "removed": []}) as cleanup:
                result = docker_runtime.run_project_tests(
                    str(project),
                    docker_runtime.hashlib.sha256((project / "requirements.txt").read_bytes()).hexdigest(),
                )
        self.assertTrue(result["ok"])
        self.assertEqual(run_container.call_count, 2)
        download_args = run_container.call_args_list[0].args[1]
        test_args = run_container.call_args_list[1].args[1]
        self.assertIn("bridge", download_args)
        self.assertNotIn("/workspace", " ".join(download_args))
        self.assertIn("none", test_args)
        self.assertIn("readonly", " ".join(test_args))
        cleanup.assert_called_once()
        self.assertTrue(cleanup.call_args.args[0])

    def test_requirements_change_after_approval_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            (project / "requirements.txt").write_text("pytest==8.3.5\n", encoding="utf-8")
            with patch.object(docker_runtime, "_docker", return_value="docker"):
                with self.assertRaisesRegex(docker_runtime.DockerRuntimeError, "تغییر کرده"):
                    docker_runtime.run_project_tests(str(project), "wrong-digest")

    def test_timeout_forces_container_removal(self):
        with tempfile.TemporaryDirectory() as folder:
            cidfile = Path(folder) / "run.cid"
            cidfile.write_text("container123\n", encoding="ascii")
            timeout = subprocess.TimeoutExpired(["docker", "run"], 2, output=b"", stderr=b"timed out")
            with patch.object(docker_runtime.subprocess, "run", side_effect=[
                timeout,
                type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
            ]) as run:
                with self.assertRaisesRegex(docker_runtime.DockerRuntimeError, "timeout"):
                    docker_runtime._run_container("docker", [], cidfile, 2, "run-id")
            self.assertEqual(run.call_args_list[-1].args[0], ["docker", "rm", "-f", "container123"])


if __name__ == "__main__":
    unittest.main()
