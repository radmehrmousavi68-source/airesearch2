# Local Python tools

Project creation, venv creation, and new Python file creation require separate user approval.

Package installation and arbitrary Python execution are disabled until a real operating-system sandbox is implemented and security-tested. A venv is not a sandbox: code running inside it can access files and networks available to the Windows account. No generic shell, PowerShell, or CMD command is exposed.

The API is not yet connected to a complete Ollama tool-calling loop or interactive approval UI. The model must not claim an operation succeeded without a successful tool result.
