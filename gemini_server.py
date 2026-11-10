"""Local-only Gemini web bridge used by gemini_hotkey.py."""
import asyncio
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright

GEMINI_URL = "https://gemini.google.com/app"
INPUT_SEL = os.getenv("GEMINI_INPUT", "rich-textarea .ql-editor[contenteditable='true'], div[contenteditable='true'][aria-label*='prompt' i]")
# message-content also appears inside the user's attachment card. Restrict this
# to Gemini's model-response components, otherwise an image filename can be
# mistaken for the answer before Gemini has generated anything.
ANSWER_SEL = os.getenv("GEMINI_ANSWER", "model-response message-content")
SEND_SEL = os.getenv("GEMINI_SEND", "button[aria-label*='Send' i], button[data-test-id='send-button']")
PORT = int(os.getenv("GEMINI_PORT", "9223"))
PROFILE = os.path.abspath("./gemini-chrome")
# The app is designed to stay out of the way. Set GEMINI_RUN_VISIBLE=1 before
# starting it if you want to inspect or configure the controlled browser tab.
RUN_VISIBLE = os.getenv("GEMINI_RUN_VISIBLE", "0") == "1"
MAX_WAIT = int(os.getenv("GEMINI_MAX_WAIT", "180"))
STABLE_SECONDS = float(os.getenv("GEMINI_STABLE_SECONDS", "0.8"))

state = {"mode": None, "proc": None, "browser": None, "context": None, "page": None, "error": None}
lock = asyncio.Lock()


def find_browser():
    if os.getenv("GEMINI_BROWSER"):
        return os.environ["GEMINI_BROWSER"]
    candidates = []
    if sys.platform.startswith("win"):
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(var)
            if base:
                candidates.extend((rf"{base}\Google\Chrome\Application\chrome.exe", rf"{base}\Microsoft\Edge\Application\msedge.exe"))
    elif sys.platform == "darwin":
        candidates = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    else:
        candidates = [shutil.which(x) for x in ("google-chrome", "chromium", "chromium-browser")]
    for browser in candidates:
        if browser and os.path.exists(browser):
            return browser
    raise RuntimeError("Google Chrome or Microsoft Edge was not found.")


def browser_alive():
    proc = state.get("proc")
    return bool(proc and proc.poll() is None)


async def close_browser():
    browser, proc = state.get("browser"), state.get("proc")
    if browser:
        try:
            session = await browser.new_browser_cdp_session()
            await session.send("Browser.close")
        except Exception:
            pass
        try:
            await browser.close()
        except Exception:
            pass
    if proc and proc.poll() is None:
        for _ in range(25):
            if proc.poll() is not None:
                break
            await asyncio.sleep(.2)
        if proc.poll() is None:
            proc.terminate()
    state.update(proc=None, browser=None, context=None, page=None)


async def launch(mode):
    await close_browser()
    args = [find_browser(), f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}", "--no-first-run", "--no-default-browser-check", GEMINI_URL]
    if mode == "run" and not RUN_VISIBLE:
        args.append("--headless=new")
    state.update(proc=subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), mode=mode, error=None)
    if mode == "login":
        return
    for _ in range(40):
        try:
            browser = await state["playwright"].chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
            break
        except Exception:
            await asyncio.sleep(.5)
    else:
        raise RuntimeError("Could not connect to the browser debugging port.")
    context = browser.contexts[0]
    page = next((p for p in context.pages if "gemini.google.com" in p.url), None) or context.pages[0]
    state.update(browser=browser, context=context, page=page)


async def screenshot(page, name):
    try:
        os.makedirs("gemini-debug", exist_ok=True)
        await page.screenshot(path=f"gemini-debug/{name}-{int(time.time())}.png")
    except Exception:
        pass


async def get_page():
    page = state.get("page")
    if state.get("mode") != "run" or page is None:
        raise HTTPException(409, "Browser is in login mode. Sign in, then choose 'I have signed in'.")
    if page.is_closed():
        page = await state["context"].new_page()
        state["page"] = page
    return page


async def upload_image(page, image_path: str):
    """Upload through either Gemini's hidden file input or its + attachment button."""
    upload = page.locator("input[type='file']")
    if await upload.count():
        await upload.last.set_input_files(image_path, timeout=10_000)
        return

    # Gemini creates its file input only after its attachment control is opened.
    candidates = page.locator(
        "button[aria-label*='Attach' i], button[aria-label*='Add' i], "
        "button[aria-label*='Vedhæft' i], button[aria-label*='Tilføj' i]"
    )
    targets = [candidates.nth(i) for i in range(await candidates.count())]

    # Some Gemini versions give the + button no accessible name. Pick the small
    # button furthest down and to the left — where the attachment control lives.
    if not targets:
        controls = page.locator("button, [role='button']")
        viewport_height = await page.evaluate("window.innerHeight")
        best, best_score = None, float("inf")
        for index in range(await controls.count()):
            control = controls.nth(index)
            box = await control.bounding_box()
            if not box or box["x"] > 220 or box["y"] < viewport_height * 0.55:
                continue
            score = abs(box["x"] - 100) + abs(box["y"] - viewport_height * 0.9)
            if score < best_score:
                best, best_score = control, score
        if best:
            targets.append(best)

    for target in targets:
        try:
            async with page.expect_file_chooser(timeout=3_000) as chooser_info:
                await target.click(force=True)
            await (await chooser_info.value).set_files(image_path)
            return
        except PlaywrightTimeout:
            # Clicking may have opened a menu instead of the native chooser.
            if await upload.count():
                await upload.last.set_input_files(image_path, timeout=10_000)
                return
        except Exception:
            continue

    # A few Gemini layouts show a secondary menu item after the + button.
    menu_items = page.locator(
        "[role='menuitem'][aria-label*='Upload' i], [role='menuitem'][aria-label*='Tilføj' i], "
        "button[aria-label*='Upload' i], button[aria-label*='Tilføj' i]"
    )
    menu_targets = [menu_items.nth(index) for index in range(await menu_items.count())]
    # In the Danish Gemini UI the first attachment-menu entry is simply
    # labelled “Filer”; English versions call it “Files”. Use exact text here:
    # role/aria data is absent in Gemini's current attachment menu.
    for label in ("Filer", "Files", "Upload file", "Upload files"):
        file_items = page.get_by_text(label, exact=True)
        menu_targets.extend(file_items.nth(index) for index in range(await file_items.count()))
    for target in menu_targets:
        try:
            async with page.expect_file_chooser(timeout=3_000) as chooser_info:
                await target.click(force=True)
            await (await chooser_info.value).set_files(image_path)
            return
        except PlaywrightTimeout:
            # Gemini often creates a hidden <input type=file> only *after*
            # selecting “Filer”, instead of firing Playwright's native
            # file-chooser event. The old code skipped this case entirely.
            for _ in range(5):
                if await upload.count():
                    await upload.last.set_input_files(image_path, timeout=10_000)
                    return
                await asyncio.sleep(.2)
            continue

    # Last-resort fallback for Gemini's compact composer: the attachment menu
    # has no stable DOM semantics in some releases, but its first “Files” row
    # is consistently positioned above the + button. Scale the click to the
    # current viewport, then let Playwright handle the native file chooser.
    try:
        width, height = await page.evaluate("[window.innerWidth, window.innerHeight]")
        async with page.expect_file_chooser(timeout=3_000) as chooser_info:
            await page.mouse.click(width * 0.185, height * 0.468)
        await (await chooser_info.value).set_files(image_path)
        return
    except PlaywrightTimeout:
        pass

    raise RuntimeError("Could not find Gemini's image attachment control.")


def clean_answer(text: str) -> str:
    """Remove Gemini UI labels or local capture filenames if the page exposes them as text."""
    text = re.sub(r"(?i)\b(?:capture|image)-\d+\.(?:png|jpe?g|webp|gif|bmp)\b", "", text)
    lines = [line.strip() for line in text.splitlines()]
    while lines and lines[0].casefold() in {"geminis svar", "gemini's answer", "gemini answer"}:
        lines.pop(0)
    return "\n".join(line for line in lines if line).strip()


async def ask_gemini(question, image_path=None):
    page = await get_page()
    # Keep the already-loaded Gemini session alive. Reloading the app for every
    # prompt was needlessly slow compared with using Gemini in a normal tab.
    if "gemini.google.com" not in page.url:
        await page.goto(GEMINI_URL, wait_until="domcontentloaded")
    elif image_path:
        # Gemini's attachment composer may retain the previous file after a
        # response. A fresh chat makes each image request independent, allowing
        # unlimited one-image questions rather than blocking the second upload.
        await page.goto(GEMINI_URL, wait_until="domcontentloaded")
    editor = page.locator(INPUT_SEL).first
    try:
        await editor.wait_for(state="visible", timeout=20_000)
    except PlaywrightTimeout:
        await screenshot(page, "no-editor")
        raise HTTPException(401, "Gemini prompt box was not found. You may be signed out, facing a Google check, or Gemini changed its page.")
    if image_path:
        if not os.path.isfile(image_path):
            raise HTTPException(400, "The selected image file no longer exists.")
        try:
            await upload_image(page, image_path)
            await asyncio.sleep(1.2)
        except Exception as exc:
            await screenshot(page, "image-upload")
            raise HTTPException(500, f"Gemini image upload failed: {exc}")
    answers = page.locator(ANSWER_SEL)
    before = await answers.count()
    before_text = "\n".join(await answers.all_inner_texts()) if before else ""
    await editor.fill(question)
    try:
        await page.locator(SEND_SEL).first.click(timeout=3_000)
    except Exception:
        await editor.press("Enter")
    # A successful send clears Gemini's contenteditable editor. If the button
    # did not send (for example while an image is still attaching), Enter is a
    # reliable fallback.
    await asyncio.sleep(.6)
    try:
        if (await editor.inner_text()).strip():
            await editor.press("Enter")
    except Exception:
        pass
    deadline = time.monotonic() + MAX_WAIT
    while True:
        count = await answers.count()
        current_text = "\n".join(await answers.all_inner_texts()) if count else ""
        if count > before or (current_text and current_text != before_text):
            break
        if time.monotonic() > deadline:
            await screenshot(page, "no-answer")
            raise HTTPException(504, "Gemini did not start an answer before the timeout.")
        await asyncio.sleep(.4)
    previous, changed_at = "", time.monotonic()
    while time.monotonic() <= deadline:
        try:
            current = (await answers.last.inner_text()).strip()
        except Exception:
            await asyncio.sleep(.25)
            continue
        if current != previous:
            previous, changed_at = current, time.monotonic()
        elif current and time.monotonic() - changed_at >= STABLE_SECONDS:
            return clean_answer(current)
        await asyncio.sleep(.35)
    await screenshot(page, "answer-timeout")
    raise HTTPException(504, "Gemini's response did not finish before the timeout.")


@asynccontextmanager
async def lifespan(app):
    state["playwright"] = await async_playwright().start()
    try:
        await launch("run" if os.path.isdir(PROFILE) else "login")
    except Exception as exc:
        state["error"] = str(exc)
    yield
    await close_browser()
    await state["playwright"].stop()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"browser": browser_alive(), "busy": lock.locked(), "mode": state.get("mode")}


@app.get("/status")
async def status():
    return {"browser": browser_alive(), "busy": lock.locked(), "mode": state.get("mode"), "error": state.get("error"), "headless": state.get("mode") == "run" and not RUN_VISIBLE}


@app.post("/admin/mode")
async def set_mode(body: dict):
    async with lock:
        try:
            await launch("run" if body.get("headless") else "login")
        except Exception as exc:
            state["error"] = str(exc)
            raise HTTPException(500, str(exc))
    return {"ok": True, "mode": state["mode"]}


@app.post("/ask")
async def ask(body: dict):
    question = (body.get("q") or "").strip()
    if not question:
        raise HTTPException(400, "Send JSON with a non-empty 'q'.")
    system_prompt = (body.get("system_prompt") or "").strip()
    image_path = (body.get("image_path") or "").strip()
    if system_prompt:
        question = f"{system_prompt}\n\n---\n\nUser request:\n{question}"
    async with lock:
        return {"answer": await ask_gemini(question, image_path or None)}


@app.post("/admin/quit")
async def quit_server():
    """Close the browser and bridge after the desktop client exits."""
    async def stop_soon():
        await asyncio.sleep(.2)
        await close_browser()
        os._exit(0)
    asyncio.create_task(stop_soon())
    return {"ok": True}

