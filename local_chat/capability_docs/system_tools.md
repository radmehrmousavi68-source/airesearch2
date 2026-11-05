# Local development and Docker tools

Project creation, virtual-environment creation, and new Python file creation require separate user approval.

## Docker test runner

In programming mode the agent can call the fixed `docker_test` tool. It does not accept arbitrary Docker arguments, image names, shell commands, or host paths. The server checks that Docker CLI and the Docker Engine are available both before approval and again immediately before execution.

After explicit approval, the tool starts a temporary `python:3.12-slim` container and automatically removes it. The project is mounted read-only; container networking is disabled; the root filesystem is read-only; Linux capabilities are dropped; no-new-privileges is enabled; PID, memory, CPU, and temporary-filesystem limits are applied. The only command currently exposed is Python's standard-library `unittest discover -s tests -v`. Output and exit code are returned to the model for diagnosis. There is no fallback to host execution if Docker is unavailable.

This first Docker runner does not yet install project dependencies, run pytest, or execute Django-specific checks. Those require a separate reviewed dependency/build workflow; do not claim those tests ran. Dependency installation must never be done on the Windows host.

## Approval and session security

Every browser POST must include a valid same-origin Origin header, an opaque HttpOnly SameSite=Strict session cookie, and the matching CSRF header. Prepared operations are bound to the browser session that created them. A different session cannot approve or reject the plan. The in-memory session and browser cookie expire after 12 hours; approval plans remain one-use and expire after five minutes.

Rejecting a proposed operation rejects only that operation, not the user's original goal. The agent must preserve the original request and any newer user-specified project path/name, and must never silently substitute a different project or path.

General chat never receives programming tool schemas. No generic shell, PowerShell, CMD, privileged container, Docker socket, or broad host-directory mount is exposed.
