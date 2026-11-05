# Local Python tools

Project creation, venv creation, and new Python file creation require separate user approval.

Package installation and arbitrary Python execution are disabled until a real operating-system sandbox is implemented and security-tested. A venv is not a sandbox: code running inside it can access files and networks available to the Windows account. No generic shell, PowerShell, or CMD command is exposed.

The API and browser-side preview/approve/reject controls are implemented, but the Ollama tool-calling loop is not connected yet. The model must not claim an operation succeeded without a successful tool result.


## Approval session security

Every browser POST must include a valid same-origin Origin header, an opaque
HttpOnly SameSite=Strict session cookie, and the matching CSRF header. Prepared
operations are bound to the browser session that created them. A different
session cannot approve or reject the plan. The in-memory session and browser cookie expire after 12 hours; approval plans remain one-use and expire after
five minutes. The browser preview/approval UI is available as a client helper,
but it is not yet connected to Ollama tool calling.
