# GeminiSecretAI

A Windows utility for asking Gemini about text, screenshots, and image files. Answers go straight to your clipboard. It uses the Gemini website in a dedicated Chrome or Edge session; no API key is required.

## Install and start

1. Extract the project into a permanent folder you can write to.
2. Install **Python 3.11 or later** with Python added to PATH, and **Google Chrome or Microsoft Edge**.
3. Double-click **Install.bat** and wait for installation to finish.
4. Double-click **Start GeminiSecretAI.vbs**. This is the everyday launcher; it opens the controls without a console.
5. On **Home**, choose **Open login**, sign in to Google in the dedicated browser, then choose **Run headlessly**. Let the app switch browser modes rather than closing the login browser yourself.

Ordinary startup always shows the controls. Subsequent launches reuse your saved login and run Gemini headlessly by default. **Ready** means the Gemini page has a usable prompt editor; a responding local service alone does not count as ready.

Keep the installation folder in place. The installer registers the local Python package in editable mode; rerun **Install.bat** and recreate autostart if you move the folder. The launcher's location is resolved automatically, including paths with spaces and shortcuts launched from another working directory.

## Everyday use

| Default shortcut | Action |
| --- | --- |
| Ctrl+Alt+Space | Begin typing a text question; press again to send |
| Ctrl+Alt+I | Select a screen area and send it as an image |
| Ctrl+Alt+F12 | Exit the app, including while hidden |

Your existing configured shortcuts take precedence over these defaults.

For text questions, type after the first shortcut press. Backspace edits, Enter adds a line break, and Esc cancels. Recorded typing is kept out of the active app by default. After the answer arrives, paste it with **Ctrl+V**. The app handles one question at a time.

On dead-key layouts, use **^ then Space** for a literal caret, as in `2^3` or `SO4^2-`. An accent followed by a compatible letter produces an accented character, such as `^` then `e` → `ê`. Backspace cancels a pending accent before deleting recorded text; cancellation or a new recording clears pending accents. Translation leaves Windows' dead-key state unchanged. Physical keyboard checks are still needed for your layout, particularly custom layouts and IMEs.

For screen questions, drag around the question and release to send. **Esc** cancels selection. Home also offers **Capture screenshot** and **Upload image**. Temporary captures are removed after completion or failure; uploaded originals are left untouched. The file picker offers PNG, JPEG, WebP, GIF, and BMP; Gemini's own upload support still applies.

**Cancel question** stops recording, selection, or an active request. Cancelled and late results are prevented from replacing your clipboard. Uncertain sends are not automatically retried.

## Find your way around

- **Home:** separate connection and request status, capture/upload actions, login/reconnect, current shortcuts, and Hide app.
- **Settings → Shortcuts:** edit all three shortcuts and save them immediately. Conflicting, unsupported, and Windows-reserved combinations are rejected with an explanation. Show status dot applies and saves immediately.
- **Settings → Instructions:** edit the instructions included with text and images, enable/disable them, save, or restore the built-in answer-only prompt. Custom instructions are retained when disabled. Saving an empty prompt restores the default.
- **Settings → Advanced:** local port, request timeout, and typing behavior. Connection changes require a restart.
- **Help:** usage, first sign-in, hidden-mode guidance, and buttons for logs, the project folder, and this README.

The interface uses a matte dark palette, compact navigation, keyboard focus indicators, and scrollable pages. Narrow windows stack the Home actions vertically. Settings display unsaved changes, validation failures, and save confirmation inline.

Saved instructions are prepended to the website's composer; they are not a separate API system-message role. The default asks for one plain answer without greetings, explanations, attachment names, or image IDs. Math and chemistry use notation such as `x^2` and `CH4`.

## Hidden mode, the dot, and exiting

Run Gemini headlessly, then choose **Hide app** on Home. The controls and taskbar entry disappear. Text/image shortcuts and clipboard delivery keep working. A screenshot selection briefly shows its overlay; capture or cancellation removes it. There is no tray icon or replacement window. Runtime errors are logged without reopening the controls.

The current exit shortcut is explained beside Hide app. It works when visible, minimized, or hidden and fires once per press. Exiting cancels requests/captures, blocks late clipboard writes, removes the dot, closes the owned bridge/browser, stops the keyboard hook, and releases the instance guard. Exit and relaunch to see settings again. Closing the normal window or choosing **Quit** also exits. The process remains visible in Task Manager, which can terminate it as usual; the bridge retains its client-loss cleanup.

**Show status dot** can be changed at any time, including during a request. Off hides it immediately; on shows the current active state. Expired result indicators do not return. Muted red indicates recording or an error, amber indicates waiting, and green indicates a copied answer; Home/logs provide the detailed state.

Capture and dot placement retain physical-pixel, DPI-aware geometry across the Windows virtual desktop, including monitors above or left of the main display. Fully restart after updating display-related code.

## Files and personal data

```text
GeminiHotkey/                     # The folder can keep its existing name
  Start GeminiSecretAI.vbs       # Run this every day
  Install.bat                   # Install/update dependencies
  README.md
  requirements.txt
  pyproject.toml
  src/geminisecretai/            # Application source and answer_dom.js asset
  tests/                        # Regression tests and local browser fixtures
  scripts/                      # Optional restart/autostart and developer launcher
  data/                         # Personal runtime data; excluded from source control
  gemini-chrome/                # Existing login profile, retained if present
  gemini_hotkey.py               # Legacy compatibility wrapper
  start_hidden.vbs               # Legacy compatibility wrapper
```

Runtime locations:

| Location | Contents |
| --- | --- |
| `data/config.json` | Shortcuts, prompt, dot preference, and advanced settings |
| `data/profile.json` | Pointer to the browser profile, without credentials |
| `data/browser/` | Browser profile for a fresh installation |
| `gemini-chrome/` | Existing installations' browser profile; never moved by migration |
| `data/logs/` | Desktop and bridge logs |
| `data/captures/` | Temporary screenshots |
| `data/debug/` | Browser failure diagnostics |
| `data/legacy/` | Preserved originals and legacy diagnostic files |

On upgrade, quit the old version before replacing source files. Keep all personal data, run **Install.bat**, then launch the new main VBS file. Startup copies a legacy root `config.json` byte for byte, verifies it, and archives the original under `data/legacy/`. If a new config already exists, neither config is overwritten. Normal settings loading adds missing defaults while preserving configured values and nonempty custom prompts. An unreadable legacy config is left intact and reported.

Existing `gemini-chrome/` login data stays exactly where it is, and its location is pinned. Old logs/captures/debug files are archived without replacing existing files. A locked legacy log remains in place until a later startup can archive it. Nothing deletes or resets your login. Sign in separately on another PC rather than copying browser cookies.

Prompts and selected images are sent to Google through Gemini's website. Browser profiles, settings, logs, screenshots, and captures are ignored by source control. Diagnostics can contain question or page content.

## Optional startup and maintenance

- **scripts/Enable autostart.bat:** creates a Windows Startup shortcut to the main launcher. It updates an owned legacy Gemini Hotkey shortcut rather than adding a duplicate. Remove that shortcut from `shell:startup` to disable autostart.
- **scripts/Restart.bat:** requests a clean restart of this installation. It does not kill an unrelated process using the configured port.
- `start_hidden.vbs` and `gemini_hotkey.py` remain simple compatibility wrappers for existing shortcuts. Use **Start GeminiSecretAI.vbs** for new shortcuts.

A single-instance guard prevents duplicate clients. The bridge listens on `127.0.0.1`, requires the client's ownership token for mutations, and closes its owned browser after client loss. An occupied port is reported rather than terminating its owner.

## Development

Install with **Install.bat** or `python -m pip install -e .`. Normal package imports work without changing `sys.path` or relying on the current directory. `python -m geminisecretai` starts the app.

| Module under `src/geminisecretai/` | Responsibility |
| --- | --- |
| `ui.py` | Presentation, navigation, responsive layout, keyboard access |
| `client.py` | Application controller, clipboard delivery, shutdown |
| `settings.py`, `paths.py` | Defaults, shortcut validation, persistence, migration |
| `hotkeys.py` | Windows keyboard hook and key translation |
| `capture.py`, `display.py` | File/screen selection and DPI-aware geometry |
| `bridge.py` | Owned service, browser/session lifecycle |
| `browser.py`, `answer_dom.js` | Upload, generation completion, answer extraction |
| `requests.py`, `instance.py` | Cancellation/deadlines and instance/restart guards |
| `preview.py` | UI preview without Gemini or global hooks |

Run regression tests from the project folder:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

Browser tests use installed Chrome/Edge, local fixtures, and temporary profiles. They do not contact Gemini or use your login. Coverage includes repeated uploads, Danish/English menus, formula extraction, delayed generation, cancellation/disconnects, hidden/minimized windows, hotkey debounce/conflicts, dot toggling, late-answer suppression, cleanup, data migration, and package imports from another directory.

For a visual preview without a browser or global hooks:

```powershell
python -m geminisecretai.preview --size 700x700
python -m geminisecretai.preview --scale 2 --size 440x440
```

The preview uses disposable settings. Its save actions do not affect your configuration. Scale arguments simulate application scaling; they do not change Windows display settings.

`python -u tests/live_check.py` is an optional live integration check. It uses the dedicated Gemini login and sends arithmetic, chemistry, and image questions. Close the app first.

Manual release checks should include live text/image answers, five consecutive uploads, cancellation followed by another question, expired login/reconnection, native global shortcuts in visible/minimized/hidden states, held exit keys, Task Manager termination, clean relaunch, and capture/dot placement on real mixed-DPI monitors at 125%, 150%, and 200%. Automated mocks and application-scale previews do not replace those hardware checks.

## Troubleshooting

If disconnected, choose **Run headlessly**. If sign-in expired, choose **Open login**, complete sign-in, then **Run headlessly**. If a question stalls, cancel it before trying again. Open the logs from Help for details.

The app automates Gemini's web UI. Website changes can require selector updates; Gemini generation and network time also affect speed. Answer extraction selects one canonical representation of mathematical expressions to avoid duplicated rendered/accessible formulas. The bridge logs request-stage timings for diagnosing slow requests.

Advanced environment overrides, read when the bridge starts:

| Variable | Default / purpose |
| --- | --- |
| `GEMINI_RUN_VISIBLE` | `0`; `1` shows the controlled browser for diagnosis |
| `GEMINI_BROWSER` | Auto-detected; optional Chrome/Edge executable path |
| `GEMINI_PORT` | Auto-allocated browser debugging port |
| `GEMINI_MAX_WAIT` | `180` seconds for the complete browser request |
| `GEMINI_STABLE_SECONDS` | `4`; fallback quiet period, minimum 2 seconds |
| `GEMINI_INPUT`, `GEMINI_ANSWER`, `GEMINI_SEND` | Optional composer/answer/send CSS selector overrides |

Inspired by [Thyxo/SecretAI](https://github.com/Thyxo/SecretAI/).
