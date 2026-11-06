"""Approval-gated, allowlisted local Python project tools for LocalChat.

No shell is used. Every mutating or executing operation requires a short-lived
approval token returned by prepare_action().
"""
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

if __package__:
    from .capabilities import register_capability
else:
    from capabilities import register_capability

APP_DATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "LocalChat"
PROJECTS_ROOT = APP_DATA / "projects"
APPROVAL_TTL = 300
MAX_SCRIPT_BYTES = 256 * 1024
MAX_OUTPUT_CHARS = 12000
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}(?:==[A-Za-z0-9.*+!-]{1,40})?$")
_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\.py$")
_pending = {}
_lock = threading.RLock()


class SystemToolError(ValueError):
    pass


def _project_path(name):
    if not isinstance(name, str) or not _SLUG.fullmatch(name):
        raise SystemToolError("نام پروژه باید فقط شامل حروف کوچک انگلیسی، عدد، خط تیره یا زیرخط باشد.")
    root = PROJECTS_ROOT.resolve()
    target = (root / name).resolve()
    if target.parent != root:
        raise SystemToolError("مسیر پروژه معتبر نیست.")
    return target


def _python(project):
    candidate = project / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if (project / ".venv").is_symlink() or not candidate.is_file():
        raise SystemToolError("محیط مجازی پروژه وجود ندارد یا مسیر آن امن نیست؛ ابتدا ساخت محیط مجازی را تأیید کنید.")
    return candidate


def _plan(action, args):
    if action in {"run_python", "install_packages"}:
        raise SystemToolError("اجرای مستقیم روی میزبان غیرفعال است؛ از چرخهٔ تست ایزوله Docker استفاده کنید.")
    if not isinstance(args, dict):
        raise SystemToolError("پارامترهای عملیات معتبر نیستند.")
    if action == "create_project":
        name = args.get("name")
        project = _project_path(name)
        if project.exists():
            raise SystemToolError("این پروژه از قبل وجود دارد؛ برای جلوگیری از بازنویسی متوقف شدم.")
        return {"action": action, "project": name, "path": str(project),
                "command": "ساخت پوشه‌های src، tests، data، output، logs و فایل‌های اولیه"}
    if action == "create_venv":
        name = args.get("project")
        project = _project_path(name)
        if not project.is_dir():
            raise SystemToolError("پروژه پیدا نشد.")
        if (project / ".venv").exists():
            raise SystemToolError("محیط مجازی از قبل وجود دارد؛ چیزی بازنویسی نمی‌شود.")
        return {"action": action, "project": name, "path": str(project / ".venv"),
                "command": f'"{sys.executable}" -m venv "{project / ".venv"}"'}
    if action == "docker_test":
        name = args.get("project")
        project = _project_path(name)
        if not project.is_dir() or project.is_symlink():
            raise SystemToolError("پروژه پیدا نشد یا مسیر آن امن نیست.")
        if shutil.which("docker") is None:
            raise SystemToolError("Docker CLI پیدا نشد؛ Docker Desktop را اجرا و PATH را بررسی کنید.")
        probe = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                               capture_output=True, text=True, timeout=15)
        if probe.returncode != 0 or not probe.stdout.strip():
            raise SystemToolError("Docker Engine در دسترس نیست؛ Docker Desktop را اجرا کنید.")
        if __package__:
            from . import docker_runtime
        else:
            import docker_runtime
        try:
            requirements_text, _ = docker_runtime._requirements(project)
        except docker_runtime.DockerRuntimeError as exc:
            raise SystemToolError(str(exc)) from exc
        return {"action": action, "project": name, "path": str(project),
                "image": docker_runtime.IMAGE,
                "requirements_sha256": __import__("hashlib").sha256(requirements_text.encode("utf-8")).hexdigest(),
                "dependency_count": len(docker_runtime._requirements(project)[1]),
                "command": "دریافت wheelهای باینری از PyPI در کانتینر جداگانه؛ نصب آفلاین و اجرای تست Django/pytest/unittest در کانتینر بدون شبکه؛ حذف خودکار کانتینرها"}
    if action == "write_python":
        name, filename, content = args.get("project"), args.get("filename"), args.get("content")
        project = _project_path(name)
        if not project.is_dir() or not isinstance(filename, str) or not _FILE.fullmatch(filename):
            raise SystemToolError("نام پروژه یا فایل پایتون معتبر نیست.")
        if not isinstance(content, str) or not content.strip() or len(content.encode("utf-8")) > MAX_SCRIPT_BYTES:
            raise SystemToolError("محتوای فایل خالی یا بیش از ۲۵۶ کیلوبایت است.")
        if (project / "src").is_symlink():
            raise SystemToolError("پوشهٔ src نباید پیوند نمادین باشد.")
        target = (project / "src" / filename).resolve()
        if target.parent != (project / "src").resolve():
            raise SystemToolError("مسیر فایل خارج از src است.")
        if target.exists():
            raise SystemToolError("فایل از قبل وجود دارد؛ برای جلوگیری از حذف یا بازنویسی متوقف شدم.")
        return {"action": action, "project": name, "filename": filename,
                "content": content, "path": str(target),
                "command": f"ایجاد فایل جدید {target} (بدون بازنویسی فایل موجود)"}
    if action == "install_packages":
        name, packages = args.get("project"), args.get("packages")
        project = _project_path(name)
        python = _python(project)
        if not isinstance(packages, list) or not 1 <= len(packages) <= 20:
            raise SystemToolError("فهرست نصب باید بین ۱ تا ۲۰ بسته داشته باشد.")
        if any(not isinstance(p, str) or not _PACKAGE.fullmatch(p) for p in packages):
            raise SystemToolError("نام یا نسخهٔ یکی از بسته‌ها معتبر نیست.")
        return {"action": action, "project": name, "packages": packages,
                "command": f'"{python}" -m pip install ' + " ".join(packages)}
    if action == "run_python":
        name, filename = args.get("project"), args.get("filename")
        project = _project_path(name)
        python = _python(project)
        if not isinstance(filename, str) or not _FILE.fullmatch(filename):
            raise SystemToolError("فقط فایل .py با نام ساده در پوشه src قابل اجراست.")
        if (project / "src").is_symlink():
            raise SystemToolError("پوشهٔ src نباید پیوند نمادین باشد.")
        script = (project / "src" / filename).resolve()
        if script.parent != (project / "src").resolve() or not script.is_file():
            raise SystemToolError("فایل پایتون در پوشه src پیدا نشد.")
        argv = args.get("args", [])
        if not isinstance(argv, list) or len(argv) > 20 or any(not isinstance(x, str) or len(x) > 300 for x in argv):
            raise SystemToolError("آرگومان‌های اجرا معتبر نیستند.")
        return {"action": action, "project": name, "filename": filename,
                "args": argv, "command": f'"{python}" "{script}"' + (" " + " ".join(argv) if argv else "")}
    raise SystemToolError("این نوع عملیات در فهرست مجاز نیست.")


def prepare_action(action, args, session_id):
    if not isinstance(session_id, str) or len(session_id) < 32:
        raise SystemToolError("نشست معتبر برای آماده‌سازی عملیات لازم است.")
    plan = _plan(action, args)
    token = secrets.token_urlsafe(24)
    now = time.monotonic()
    with _lock:
        for key, item in list(_pending.items()):
            if item["expires"] < now:
                _pending.pop(key, None)
        _pending[token] = {"plan": plan, "expires": now + APPROVAL_TTL, "session_id": session_id}
    preview = {key: value for key, value in plan.items() if key != "content"}
    if "content" in plan:
        preview["content_preview"] = plan["content"]
        preview["content_bytes"] = len(plan["content"].encode("utf-8"))
    return {"approval_id": token, "expires_in": APPROVAL_TTL, "plan": preview,
            "message": "این عملیات هنوز اجرا نشده است؛ فقط پس از تأیید صریح کاربر اجرا می‌شود."}


def reject_action(token, session_id):
    with _lock:
        item = _pending.get(token)
        if not item or not secrets.compare_digest(item["session_id"], str(session_id or "")):
            return False
        _pending.pop(token, None)
        return item["expires"] >= time.monotonic()


def approve_action(token, session_id):
    with _lock:
        item = _pending.get(token)
        if not item or not secrets.compare_digest(item["session_id"], str(session_id or "")):
            raise SystemToolError("این درخواست متعلق به نشست فعلی نیست یا وجود ندارد.")
        _pending.pop(token, None)
    if item["expires"] < time.monotonic():
        raise SystemToolError("درخواست تأیید وجود ندارد یا منقضی شده است؛ دوباره درخواست بدهید.")
    plan = item["plan"]
    action = plan["action"]
    project = _project_path(plan.get("project", ""))
    if action == "create_project":
        project.mkdir(parents=True, exist_ok=False)
        for folder in ("src", "tests", "data", "output", "logs"):
            (project / folder).mkdir()
        (project / "requirements.txt").write_text("# Project dependencies\n", encoding="utf-8")
        (project / "README.md").write_text(
            f"# {plan['project']}\n\nPython project managed by LocalChat.\n\n"
            "Source: src/ | Tests: tests/ | Data: data/ | Output: output/ | Logs: logs/\n",
            encoding="utf-8")
        return {"ok": True, "action": action, "project": plan["project"], "path": str(project)}
    if action == "create_venv":
        venv_path = project / ".venv"
        if venv_path.exists() or venv_path.is_symlink():
            raise SystemToolError("مسیر محیط مجازی پس از پیش‌نمایش ایجاد شده است؛ برای جلوگیری از دست‌کاری متوقف شدم.")
        subprocess.run([sys.executable, "-m", "venv", str(venv_path)],
                       cwd=str(project), check=True, capture_output=True, text=True, timeout=180)
        return {"ok": True, "action": action, "project": plan["project"],
                "python": str(_python(project))}
    if action == "write_python":
        src = project / "src"
        project_root = project.resolve()
        if src.is_symlink():
            raise SystemToolError("پوشهٔ src پس از پیش‌نمایش به پیوند نمادین تبدیل شده است.")
        src_root = src.resolve()
        if src_root.parent != project_root or not src_root.is_dir():
            raise SystemToolError("مسیر src پس از پیش‌نمایش تغییر کرده یا معتبر نیست.")
        target = src_root / plan["filename"]
        try:
            with target.open("x", encoding="utf-8", newline="\n") as output:
                output.write(plan["content"])
        except FileExistsError as exc:
            raise SystemToolError("فایل پس از نمایش تأیید ایجاد شده است؛ هیچ فایلی بازنویسی نشد.") from exc
        return {"ok": True, "action": action, "path": str(target)}
    if action == "docker_test":
        if project.is_symlink() or not project.is_dir():
            raise SystemToolError("مسیر پروژه پس از تأیید تغییر کرده است.")
        if __package__:
            from . import docker_runtime
        else:
            import docker_runtime
        try:
            result = docker_runtime.run_project_tests(
                str(project), plan["requirements_sha256"]
            )
        except docker_runtime.DockerRuntimeError as exc:
            raise SystemToolError(str(exc)) from exc
        return {"ok": result["ok"], "action": action,
                "project": plan["project"], **result}
    if action == "install_packages":
        python = _python(project)
        result = subprocess.run([str(python), "-m", "pip", "install", *plan["packages"]],
                                cwd=str(project), capture_output=True, text=True, timeout=600)
        if result.returncode:
            raise SystemToolError((result.stderr or result.stdout)[-MAX_OUTPUT_CHARS:])
        req = project / "requirements.txt"
        existing = req.read_text(encoding="utf-8") if req.exists() else ""
        lines = set(existing.splitlines())
        lines.update(plan["packages"])
        req.write_text("\n".join(sorted(x for x in lines if x.strip())) + "\n", encoding="utf-8")
        return {"ok": True, "action": action, "packages": plan["packages"],
                "output": result.stdout[-MAX_OUTPUT_CHARS:]}
    if action == "run_python":
        python = _python(project)
        if (project / "src").is_symlink():
            raise SystemToolError("پوشهٔ src نباید پیوند نمادین باشد.")
        script = (project / "src" / plan["filename"]).resolve()
        if script.parent != (project / "src").resolve() or not script.is_file():
            raise SystemToolError("فایل پیش از اجرا تغییر کرده یا پیدا نشد.")
        result = subprocess.run([str(python), "-I", str(script), *plan["args"]],
                                cwd=str(project), capture_output=True, text=True, timeout=30)
        return {"ok": result.returncode == 0, "action": action,
                "returncode": result.returncode,
                "stdout": result.stdout[-MAX_OUTPUT_CHARS:],
                "stderr": result.stderr[-MAX_OUTPUT_CHARS:]}
    raise SystemToolError("عملیات مجاز نیست.")


@register_capability(
    id="approved_system_tools",
    name="ابزارهای محلی پایتون با تأیید کاربر",
    description="ساخت پروژه و فایل Python؛ تست و دریافت وابستگی‌ها در کانتینرهای موقت Docker.",
    purpose="انجام کارهای برنامه‌نویسی محلی در پوشه‌های مستقل و بدون اجرای فرمان دلخواه shell.",
    activation_conditions=("محیط مجازی", "venv", "virtualenv", "نصب کتابخانه", "pip install",
                          "فایل پایتون", "اجرای پایتون", "ساخت پروژه پایتون",
                          "create python file", "run python", "install package"),
    inputs=("نام پروژه", "نام فایل و محتوای Python", "فهرست بسته‌ها", "تأیید صریح هر عملیات"),
    outputs=("پروژهٔ مرتب", "محیط مجازی", "فایل Python", "گزارش نصب یا اجرای برنامه"),
    dependencies=("Python نصب‌شده روی سیستم", "فضای دیسک محلی", "دسترسی اینترنت برای pip در صورت نیاز"),
    limitations=("هیچ فرمان shell دلخواهی اجرا نمی‌شود.", "عملیات تغییردهنده نیازمند تأیید جداگانه است.",
                 "وابستگی‌ها فقط پس از تأیید، به‌صورت wheel باینری از PyPI در کانتینر جدا دریافت می‌شوند؛ نصب و تست آفلاین در کانتینر محدود انجام می‌شود.",
                 "برای پروژه‌های Django، pytest و unittest مسیر تست مناسب به‌صورت خودکار انتخاب می‌شود.",
                 "venv به‌تنهایی sandbox امنیتی نیست."),
    documentation="system_tools.md",
)
def _registered_system_tools_capability():
    return True
