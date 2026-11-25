# GeminiSecretAI

A Windows desktop shortcut for asking Gemini about text or a selected part of
your screen. Answers are copied to your clipboard, ready to paste into another app.

The app uses the Gemini website through a separate Chrome or Edge profile.
No Gemini API key is required. After signing in, the browser runs headlessly by
default. Minimize the control window to keep both global hotkeys active.

## Features

- Text questions from any app using a configurable global hotkey.
- Image questions using a second hotkey to select a screen area.
- Existing image uploads from the control window.
- Saved instructions included with every text or image question.
- CustomTkinter control window for keybinds, instructions, and login.
- Colored status dot at the bottom-right of the active monitor.
- Multi-monitor capture with physical-pixel positioning for Windows display scaling.
- Optional startup with Windows.

## Installation and first login

1. Download this repository using **Code → Download ZIP**, then extract it.
2. Install Python 3 for Windows with Python added to PATH.
3. Install Google Chrome or Microsoft Edge.
4. Run `setup.bat` to install Python dependencies and Playwright browser files.
5. Run `start_hidden.vbs` to launch without a terminal window.
6. Sign in to Gemini in the browser window that opens on first launch.
   Complete any Google verification yourself.
7. In the control window, click **Run Gemini headlessly**.

For later sign-ins, click **Open login window**, sign in, and then click
**Run Gemini headlessly**. Let the app switch modes rather than closing
the login browser manually.

## Text questions

The default text hotkey is **Ctrl+Alt+Space**.

1. Press the text hotkey to begin recording.
2. Type your question. By default, those keystrokes stay out of the active app.
3. Press the same hotkey again to send the question.
4. Wait for the green dot, then paste the answer with **Ctrl+V**.

**Backspace** edits the recorded prompt, **Enter** adds a line break, and **Esc**
cancels recording. The app handles one question at a time.

## Screenshots and image questions

The default image hotkey is **Ctrl+Alt+I**.

1. Press the image hotkey from any app.
2. Drag across the area you want Gemini to read.
3. Release the mouse to capture and submit it.
4. Wait for the green dot, then paste the answer with **Ctrl+V**.

For example, capturing an image of `2 + 2` asks Gemini to solve the visible task.
Press **Esc** to cancel selection.

You can also use **Capture area** or **Upload image** in the control window.
The upload dialog offers PNG, JPG, JPEG, WebP, GIF, and BMP files; acceptance
also depends on Gemini's current upload support.

Each image request opens a fresh Gemini chat. Text requests reuse the loaded
conversation. Saved instructions accompany both request types.

Temporary screen captures are removed after the request finishes or fails.
Images selected from existing files are left in place.

## Changing the keybinds

In **KEYBINDS**, edit **TEXT ANSWER** and **IMAGE ANSWER**, then click
**Save keybinds**. Both take effect immediately and are saved for future launches.

Supported examples: `ctrl+alt+space`, `ctrl+shift+f9`, `ctrl+alt+i`, and `f9`.
Use different shortcuts for text and images.

The control window can stay minimized. Closing it or clicking **Quit app** exits
the app; there is currently no system-tray icon.

## System prompt

Write your standing instructions in **SYSTEM PROMPT** and click
**Save system prompt**. For example:

```text
Answer concisely. For a simple calculation, return only the result.
```

These instructions are prepended to each question as text in the Gemini web
composer. They do not use a separate API system-message role. Clear and save the
field to disable them.

## Status dot

| Color | Meaning |
| --- | --- |
| Red | Recording a text question |
| Orange | Waiting for Gemini, including image analysis |
| Green | Answer copied to the clipboard |
| Purple | Request failed; see the control window or logs |

The dot appears near the bottom-right of the active monitor's work area,
above the taskbar. Set `"dot": false` in `config.json` to hide it.

## Display scaling and multiple monitors

The capture overlay covers the Windows virtual desktop, including screens to
the left or above the main display. Overlay placement, cursor selection, and
screenshot bounds use physical pixels to avoid a partial overlay or misplaced
dot on scaled displays.

The display-scaling fix requires `display_geometry.py` beside
`gemini_hotkey.py`. Fully quit and relaunch after an update: DPI awareness
is configured when the Python process starts.

## Updating or using another PC

1. Quit the running app using **Quit app**.
2. Download and extract the latest repository.
3. Replace the source files in your existing app folder, including new files
   such as `display_geometry.py`.
4. Run `setup.bat` if dependencies have changed.
5. Start `start_hidden.vbs` again.

Keep your existing `config.json` and `gemini-chrome` folder when updating the
same PC. On another PC, sign in separately rather than copying browser cookies.

`restart.bat` stops the bridge listening on the default port **8001** and
launches the app. Quit the existing control window first to avoid duplicate
hotkey listeners. If you changed `bridge_port`, the script still targets 8001.

To launch automatically at Windows sign-in, run `install_autostart.bat`.
To disable it, remove the **Gemini Hotkey** shortcut from the Windows Startup folder.

## Configuration

`config.json` is created automatically. Edit it while the app is closed, or use
the UI for keybinds and the system prompt.

| Setting | Default | Purpose |
| --- | --- | --- |
| `hotkey` | `ctrl+alt+space` | Start/send text recording |
| `image_hotkey` | `ctrl+alt+i` | Start screen-area selection |
| `system_prompt` | Empty | Instructions included with every question |
| `dot` | `true` | Show the status dot |
| `suppress_keys` | `true` | Keep recorded typing out of the active app |
| `bridge_port` | `8001` | Local HTTP bridge port |
| `request_timeout` | `300` | Client request timeout, in seconds |

The legacy `show_window_on_start` setting is still written to the config but
is not currently used; the control window opens on launch.

## Troubleshooting

**Overlay does not reach the edges or the dot is misplaced:** update all source
files, fully quit the old app, and relaunch. Geometry checks can be run with
`python -m unittest -v test_display_geometry`; mixed-DPI hardware should also
be checked manually.

**Login expired:** use **Open login window**, complete sign-in, then click
**Run Gemini headlessly**.

**A request fails, upload stops, or an answer is incomplete:** inspect
`gemini_hotkey.log`, `gemini_bridge.log`, and screenshots in `gemini-debug`.
The app automates Gemini's web UI, so site changes can break selectors.
Response completion uses a text-stability heuristic and may mistake a pause
in generation for the end of an answer.

**Slow answers:** response time includes Gemini's generation and web-page
handling. To inspect the session and its model settings, launch with
`GEMINI_RUN_VISIBLE=1`. Available model controls depend on the Gemini account.

These environment variables are read when the bridge starts:

| Variable | Default | Purpose |
| --- | --- | --- |
| `GEMINI_RUN_VISIBLE` | `0` | Set to `1` to show the controlled browser |
| `GEMINI_BROWSER` | Auto-detect | Path to Chrome or Edge |
| `GEMINI_PORT` | `9223` | Browser debugging port, separate from the HTTP bridge |
| `GEMINI_MAX_WAIT` | `180` | Response timeout in seconds |
| `GEMINI_STABLE_SECONDS` | `0.8` | Unchanged-text interval used to detect completion |
| `GEMINI_INPUT` | Built-in selector | Override the prompt-editor CSS selector |
| `GEMINI_ANSWER` | `model-response message-content` | Override the answer CSS selector |
| `GEMINI_SEND` | Built-in selector | Override the send-button CSS selector |

## Local data

Prompts and selected images are sent to Google Gemini through its website.
The local HTTP bridge listens on `127.0.0.1`.

The dedicated `gemini-chrome` folder holds the Google browser session.
`config.json` holds personal settings and saved instructions. Logs and debug
screenshots can contain request details or page content. These files, captures,
and Python caches are excluded by `.gitignore`.

## Source files

| File | Role |
| --- | --- |
| `gemini_hotkey.py` | UI, global keybinds, screen capture, clipboard |
| `display_geometry.py` | Windows DPI awareness and physical display coordinates |
| `gemini_server.py` | FastAPI bridge and Playwright Gemini automation |
| `test_display_geometry.py` | Display-coordinate regression checks |
| `requirements.txt` | Python dependencies |
| `setup.bat` | Dependency installation |
| `start_hidden.vbs` | Launch without a console |
| `restart.bat` | Restart the default local bridge and launch the app |
| `install_autostart.bat` | Optional Windows Startup shortcut |

Inspired by [Thyxo/SecretAI](https://github.com/Thyxo/SecretAI/).
