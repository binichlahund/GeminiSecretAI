# Gemini Hotkey

`Ctrl+Alt+Space` → type a prompt → `Ctrl+Alt+Space` again → the Gemini response is copied to the clipboard.

This is a local Windows app. It automates the Gemini website in a Chrome profile that belongs only to this app; it does not use, need, or store a Gemini API key.

## Speed and model choice

To configure the model, temporarily set `GEMINI_RUN_VISIBLE=1` before launching
the app. In that controlled Gemini tab, click the model
name under the message box and select **Flash-Lite** (fastest) or **Flash**, then
choose the **Low** thinking level. These choices apply to the conversation that
the hotkey uses. Do this after clicking **I have signed in — run Gemini**.

For simple, short questions, turn off Gemini's auto/smart model selection in
**Settings → Usage limits** if you want it to keep using the fast model. The exact
model names available depend on your Google plan.

## First-time setup

1. Install Python 3 for Windows and select **Add Python to PATH**.
2. Install Google Chrome (or Microsoft Edge).
3. Run `setup.bat`.
4. Run `start_hidden.vbs`. The small status window appears and a Chrome window opens.
5. Sign in to Gemini in that Chrome window. Complete any Google verification there.
6. Click **I have signed in — run Gemini**. The app now switches to its automated browser session.

## Daily use

| Action | Result |
| --- | --- |
| `Ctrl+Alt+Space` | The dot turns red; start typing a prompt. |
| `Ctrl+Alt+Space` again | The dot turns orange while Gemini responds. |
| Green dot | The answer is in the clipboard. Paste with `Ctrl+V`. |
| `Esc` while recording | Cancels the prompt. |

Keep the status window open while using the app. Closing it quits Gemini Hotkey; run
`start_hidden.vbs` again to reopen it. Use `restart.bat` after a code update if an
older local bridge is still running. `install_autostart.bat` is optional.

## Configuration

On first launch the app creates `config.json`. The useful options are `hotkey`,
`image_hotkey`, `dot`, `suppress_keys`, `bridge_port`, and `request_timeout`.

The default text hotkey is `ctrl+alt+space`. The default image hotkey is
`ctrl+alt+i`; it opens a capture overlay, where you drag over the part of the
screen Gemini should analyze.

Use the **System prompt** card in the control window to save standing instructions,
such as preferred language, tone, or output format. It is prepended to every
prompt sent through the hotkey. Keep it concise: it is sent again for each request.

The bridge creates `gemini-chrome`, a dedicated browser profile containing the Google login cookies. Treat that folder as private and do not commit or share it.

## If Gemini changes its web page

Website automation depends on page selectors. The bridge tries several current selectors, but they can change. Set any of these environment variables before starting if needed:

- `GEMINI_INPUT` — CSS selector for the prompt editor
- `GEMINI_ANSWER` — CSS selector for one model answer
- `GEMINI_SEND` — CSS selector for the send button
- `GEMINI_RUN_VISIBLE=1` — show the controlled browser; it runs headlessly by default
- `GEMINI_STABLE_SECONDS=0.8` — how long unchanged text is treated as a complete response

Errors and diagnostics are in `gemini_hotkey.log`, `gemini_bridge.log`, and `gemini-debug`.

