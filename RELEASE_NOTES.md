# LocalChat 1.6.0 — Fast Chat + Explicit Long-Term Memory

## Removed
- Entire Django/site-design interface and its `/agent` page.
- Website builder APIs, component discovery endpoints, site-builder modules, and Website Component Kit package.
- Site-builder-specific tests and documentation.

## Faster chat path
- Removed the per-message `/api/capabilities/discover` preflight network request from the browser.
- Auto web search now uses a lightweight local phrase matcher; manual web search remains available.
- Browser sends only the latest 12 non-empty messages to the server.
- Server limits model context to the latest 12 messages and truncates excessively long message bodies.
- Default generation output lowered from 2048 to 1024 tokens, temperature set to 0.55, and Ollama keep-alive set to 10 minutes.
- Model picker prefers installed lightweight 7B/8B models, then 14B models; manual model selection remains available.

## Understanding
- Added a concise Persian system prompt focused on colloquial language, typos, intent, asking only essential clarifying questions, and avoiding unsupported certainty.

## Explicit persistent memory
- Added SQLite memory stored at `%LOCALAPPDATA%\LocalChat\memory.sqlite3`.
- Memory persists across chats and application restarts, and remains local to this computer.
- Only explicit user phrases save facts; no automatic extraction from ordinary conversation.
- Supported commands include:
  - `یادت باشه ...`
  - `این رو ذخیره کن: ...`
  - `به خاطر بسپار که ...`
  - `من ... هستم، اینو یادت باشه`
  - `چه چیزهایی یادت هست؟`
  - `این رو فراموش کن: ...`
  - `همه حافظه رو پاک کن`
- Added a Persian memory viewer in the sidebar with per-item deletion.

## Verification
- Pytest: 33 passed, 13 subtests passed.
- JavaScript syntax check (`node --check`): passed.
- Python compile check: passed.
- Verified `/agent` is no longer served and the homepage no longer exposes the site-builder entry.

## Notes
- Actual model speed still depends on model size, quantization, VRAM use, and Ollama configuration. 30B models may remain slow on a 12 GB GPU; choose an installed 7B/8B or 14B model for a faster chat experience.
- Conversation history remains browser-local. Long-term memory is separate and stored in the LocalChat app-data folder.


## Approved local Python tools (in progress)
- Added a restricted local project workspace layout and allowlisted operations for project creation, virtual environments, Python file creation, package installation, and script execution.
- Every operation uses a short-lived, one-time approval token and is prepared separately from execution.
- Added creator-verification checks to the system-tools API.
- Added capability metadata and documentation.
- The Ollama tool-calling loop and interactive approval controls in the chat UI are not yet connected; the API layer must not be treated as end-to-end model tool execution.
