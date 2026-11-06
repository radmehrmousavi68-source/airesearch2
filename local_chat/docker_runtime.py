"""Managed, approval-gated Docker test runtime.

Only fixed Python test workflows are exposed. No model-supplied shell, image,
Docker flags, host paths, or arbitrary commands are accepted.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

IMAGE = "python:3.12-slim"
LABEL = "localchat.managed=true"
MAX_LOG = 12000
MAX_REQUIREMENTS = 64 * 1024
MAX_PACKAGES = 80
PACKAGE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}(?:\[[A-Za-z0-9_,.-]+\])?"
    r"(?:\s*(?:===|==|~=|!=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]+"
    r"(?:\s*,\s*(?:===|==|~=|!=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]+)*)?$"
)


class DockerRuntimeError(RuntimeError):
    pass


def _docker():
    exe = shutil.which("docker")
    if not exe:
        raise DockerRuntimeError("Docker CLI پیدا نشد.")
    probe = subprocess.run(
        [exe, "info", "--format", "{{.ServerVersion}}"],
        capture_output=True, text=True, timeout=20,
    )
    if probe.returncode or not probe.stdout.strip():
        raise DockerRuntimeError("Docker Engine در دسترس نیست؛ Docker Desktop را اجرا کنید.")
    return exe


def _requirements(project: Path):
    path = project / "requirements.txt"
    if not path.exists():
        return "", []
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_REQUIREMENTS:
        raise DockerRuntimeError("requirements.txt باید فایل معمولی حداکثر ۶۴ کیلوبایت باشد.")
    raw = path.read_bytes().decode("utf-8")
    specs = []
    for row in raw.splitlines():
        line = row.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-", ".")) or not PACKAGE.fullmatch(line):
            raise DockerRuntimeError(
                "فقط نام بسته و محدودیت نسخه پذیرفته می‌شود؛ URL، مسیر محلی و optionهای pip مجاز نیستند."
            )
        specs.append(line)
    if len(specs) > MAX_PACKAGES:
        raise DockerRuntimeError("تعداد وابستگی‌ها از حد مجاز بیشتر است.")
    return raw, specs


def _run_container(docker, args, cidfile: Path, timeout: int, label: str):
    """Run a fixed docker invocation and forcibly remove its container on all exits."""
    command = [docker, "run", "--cidfile", str(cidfile), "--label", LABEL, "--label", "localchat.run=" + label, *args]
    try:
        return subprocess.run(
            command, capture_output=True, text=True, timeout=timeout,
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        raise DockerRuntimeError(
            f"کانتینر پس از {timeout} ثانیه timeout شد.\n{(out + err)[-MAX_LOG:]}"
        ) from exc
    finally:
        try:
            cid = cidfile.read_text(encoding="ascii").strip() if cidfile.exists() else ""
            if cid:
                subprocess.run(
                    [docker, "rm", "-f", cid],
                    capture_output=True, text=True, timeout=20,
                )
        except (OSError, subprocess.TimeoutExpired):
            # Caller performs a label-based orphan cleanup below.
            pass


def cleanup_managed(run_id=None):
    """Remove managed containers, optionally scoped to one run."""
    docker = _docker()
    selector = "localchat.run=" + run_id if run_id else LABEL
    listed = subprocess.run(
        [docker, "ps", "-aq", "--filter", f"label={selector}"],
        capture_output=True, text=True, timeout=20,
    )
    if listed.returncode:
        raise DockerRuntimeError((listed.stderr or "docker ps failed")[-MAX_LOG:])
    ids = [item.strip() for item in listed.stdout.splitlines() if item.strip()]
    removed = []
    for cid in ids:
        result = subprocess.run([docker, "rm", "-f", cid],
                                capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise DockerRuntimeError((result.stderr or f"Could not remove {cid}")[-MAX_LOG:])
        removed.append(cid[:12])
    return {"removed": removed, "count": len(removed)}


def list_managed():
    docker = _docker()
    result = subprocess.run(
        [docker, "ps", "-a", "--filter", f"label={LABEL}",
         "--format", "{{.ID}} {{.Status}} {{.Image}}"],
        capture_output=True, text=True, timeout=20,
    )
    if result.returncode:
        raise DockerRuntimeError((result.stderr or "docker ps failed")[-MAX_LOG:])
    return {"containers": result.stdout[-MAX_LOG:], "count": len(result.stdout.splitlines())}


def run_project_tests(project_path: str, expected_requirements_sha256: str):
    """Download wheels in a separate networked container, then test offline."""
    docker = _docker()
    project = Path(project_path).resolve(strict=True)
    if not project.is_dir() or project.is_symlink():
        raise DockerRuntimeError("مسیر پروژه معتبر نیست.")
    req_text, specs = _requirements(project)
    digest = hashlib.sha256(req_text.encode("utf-8")).hexdigest()
    if digest != expected_requirements_sha256:
        raise DockerRuntimeError("requirements.txt پس از تأیید تغییر کرده؛ دوباره پیش‌نمایش بگیرید.")

    image_check = subprocess.run([docker, "image", "inspect", IMAGE],
                                 capture_output=True, text=True, timeout=30)
    if image_check.returncode:
        pulled = subprocess.run([docker, "pull", IMAGE],
                                capture_output=True, text=True, timeout=300)
        if pulled.returncode:
            raise DockerRuntimeError("دریافت image پایهٔ Python ناموفق بود؛\n" +
                                     (pulled.stderr or pulled.stdout)[-MAX_LOG:])

    logs = []
    run_id = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="localchat-docker-") as tmp:
        temp = Path(tmp)
        wheelhouse = temp / "wheelhouse"
        wheelhouse.mkdir()
        if specs:
            req = project / "requirements.txt"
            download = _run_container(
                docker,
                [
                    "--pull=missing", "--network", "bridge", "--read-only",
                    "--env", "HOME=/tmp", "--env", "PIP_NO_CACHE_DIR=1",
                    "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                    "--pids-limit", "48", "--memory", "512m", "--cpus", "1",
                    "--tmpfs", "/tmp:rw,nosuid,size=128m",
                    "--mount", f"type=bind,src={req.resolve()},dst=/requirements.txt,readonly",
                    "--mount", f"type=bind,src={wheelhouse.resolve()},dst=/wheelhouse",
                    IMAGE, "python", "-m", "pip", "download",
                    "--disable-pip-version-check", "--only-binary=:all:",
                    "--index-url", "https://pypi.org/simple",
                    "--dest", "/wheelhouse", "-r", "/requirements.txt",
                ],
                temp / "download.cid", 300, run_id,
            )
            logs.append("Dependency download (PyPI wheels only):\n" +
                        (download.stdout + "\n" + download.stderr)[-MAX_LOG:])
            if download.returncode:
                raise DockerRuntimeError(
                    "دریافت wheelهای وابستگی از PyPI ناموفق بود؛ هیچ بسته‌ای روی ویندوز نصب نشد.\n"
                    + logs[-1][-MAX_LOG:]
                )

        # This fixed runner installs from the mounted wheelhouse only, then detects
        # Django, pytest, or unittest. The workspace remains read-only and networking
        # is disabled for the entire test phase.
        runner = (
            "import importlib.util,pathlib,subprocess,sys,os,shutil; "
            "shutil.copytree('/workspace','/tmp/project',dirs_exist_ok=True,symlinks=False,ignore=shutil.ignore_patterns('.venv','.git','__pycache__','.pytest_cache','.mypy_cache','node_modules','.airesearch_debug')); "
            "os.chdir('/tmp/project'); "
            "env=os.environ.copy(); env['PYTHONPATH']='/tmp/deps'; sys.path.insert(0,'/tmp/deps'); "
            "req=pathlib.Path('/tmp/project/requirements.txt'); "
            "lines=[x.strip() for x in req.read_text(encoding='utf-8').splitlines() if x.strip() and not x.strip().startswith('#')] if req.is_file() else []; "
            "install=[sys.executable,'-m','pip','install','--disable-pip-version-check','--no-index','--find-links','/wheelhouse','--target','/tmp/deps','-r','/tmp/project/requirements.txt']; "
            "code=subprocess.run(install,env=env).returncode if lines else 0; "
            "sys.exit(code) if code else None; "
            "run=lambda a: subprocess.run(a,env=env).returncode; "
            "sys.exit(run([sys.executable,'manage.py','check']) or run([sys.executable,'manage.py','test'])) if pathlib.Path('manage.py').is_file() else "
            "sys.exit(run([sys.executable,'-m','pytest','-q'])) if importlib.util.find_spec('pytest') else "
            "sys.exit(run([sys.executable,'-m','unittest','discover','-s','tests','-v']))"
        )
        tested = _run_container(
            docker,
            [
                "--pull=never", "--network", "none", "--read-only",
                "--env", "HOME=/tmp", "--env", "PIP_NO_CACHE_DIR=1",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--pids-limit", "64", "--memory", "768m", "--cpus", "1.5",
                "--tmpfs", "/tmp:rw,nosuid,size=256m",
                "--mount", f"type=bind,src={project},dst=/workspace,readonly",
                "--mount", f"type=bind,src={wheelhouse.resolve()},dst=/wheelhouse,readonly",
                "--workdir", "/tmp", IMAGE, "python", "-c", runner,
            ],
            temp / "tests.cid", 600, run_id,
        )
        output = (tested.stdout or "") + "\n" + (tested.stderr or "")
        logs.append("Offline test run:\n" + output[-MAX_LOG:])
        low = output.lower()
        no_tests = "ran 0 tests" in low or "no tests ran" in low
        # Remove any labelled container that survived an interrupted docker CLI.
        cleanup = cleanup_managed(run_id)
        return {
            "ok": tested.returncode == 0 and not no_tests and cleanup["count"] == 0,
            "runtime": "Docker",
            "returncode": tested.returncode,
            "tests_discovered": not no_tests,
            "dependency_count": len(specs),
            "dependency_source": "PyPI binary wheels; downloaded in separate container",
            "test_network": "disabled",
            "workspace_mount": "read-only",
            "container_cleanup": cleanup,
            "output": "\n\n".join(logs)[-MAX_LOG:],
        }
