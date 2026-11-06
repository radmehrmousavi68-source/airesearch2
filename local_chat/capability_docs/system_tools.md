# Local development and Docker tools

Project creation, virtual-environment creation, and new Python file creation require separate user approval.

## Managed Docker lifecycle

In programming mode the agent can call the fixed `docker_test` tool. It accepts only a managed project slug; it never accepts arbitrary Docker arguments, image names, shell commands, host paths, or model-generated command strings.

Before approval, the server validates the project and `requirements.txt`, checks Docker CLI/Engine availability, and displays the fixed workflow and dependency count. After approval:
1. The trusted `python:3.12-slim` image is pulled if it is not already present.
2. If requirements exist, a separate restricted container downloads binary wheels from the official PyPI simple index. It sees only the requirements file and a temporary wheelhouse; it does not mount project source.
3. A second container runs with networking disabled. The project is mounted read-only and copied to container-local temporary storage so test databases and generated files can be written without changing host files.
4. Dependencies are installed offline from the wheelhouse into container-local temporary storage. No package is installed into Windows or the project's host venv.
5. The runner detects Django (`manage.py check` and `manage.py test`), pytest, or standard-library unittest.
6. Output and exit status are returned to the model. Containers carry an application label and a per-run label; each is forcibly removed after completion or timeout, and cleanup is scoped to the current run so concurrent runs are not stopped.

Both containers have CPU, memory, PID, and temporary storage limits, read-only root filesystems, dropped Linux capabilities, and no-new-privileges. The dependency-download container has network access only for fixed PyPI wheel downloads; the test container has no network. Only binary wheels are accepted, so packages that require source builds are reported as unsupported rather than built with arbitrary code. Requirements entries containing URLs, local paths, pip options, or other directives are rejected. No fallback to host execution is permitted.

The `docker_runtime` module also exposes listing and cleanup functions for containers carrying the application's management label. Cleanup can be scoped to one run; do not use global cleanup during normal concurrent test runs.

## Approval and session security

Every browser POST must include a valid same-origin Origin header, an opaque HttpOnly SameSite=Strict session cookie, and the matching CSRF header. Prepared operations are bound to the browser session that created them. A different session cannot approve or reject the plan. The in-memory session and browser cookie expire after 12 hours; approval plans remain one-use and expire after five minutes.

Rejecting a proposed operation rejects only that operation, not the user's original goal. The agent must preserve the original request and any newer user-specified project path/name, and must never silently substitute a different project or path.

General chat never receives programming tool schemas. No generic shell, PowerShell, CMD, privileged container, Docker socket, or broad host-directory mount is exposed.
