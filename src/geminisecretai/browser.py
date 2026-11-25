"""Observable Gemini composer stages; no positional clicks or blind resend."""
import asyncio
import logging
import os
from pathlib import Path
import re
import time
import uuid

from playwright.async_api import Error as PlaywrightError

log = logging.getLogger("gemini_bridge")
URL = "https://gemini.google.com/app"
INPUT = os.getenv("GEMINI_INPUT", "rich-textarea .ql-editor[contenteditable='true'], div[contenteditable='true'][role='textbox'], textarea")
ANSWERS = os.getenv("GEMINI_ANSWER", "model-response message-content, [data-message-author-role='model'] .markdown")
SEND = os.getenv("GEMINI_SEND", "button[data-test-id='send-button'], button[aria-label*='Send' i], button[aria-label*='Send besked' i]")
STOP = "button[aria-label*='Stop' i], button[data-test-id='stop-button'], button[aria-label*='Afbryd' i]"
BUSY = "[role='progressbar'], [aria-busy='true'], .upload-progress, .loading-indicator"
COPY_NAME = re.compile(r"^(?:copy(?: response| answer)?|kopi[eé]r(?: svar(?:et)?)?)$", re.I)
ATTACH_NAME = re.compile(r"(?:upload files?|add files?|attach files?|tilføj filer|vedhæft filer|upload fil|upload og værktøjer|upload and tools|add files and tools)", re.I)
FILES_NAME = re.compile(r"^(?:filer|files|upload files?|upload fra computer|upload from computer)$", re.I)
NEW_NAME = re.compile(r"^(?:new chat|ny chat|start new chat|start ny chat)(?:\s*\(.*\))?$", re.I)
EXTRACT = Path(__file__).with_name("answer_dom.js").read_text(encoding="utf-8")


class LoginRequired(RuntimeError):
    pass


async def first_visible(locator):
    for index in range(await locator.count()):
        item = locator.nth(index)
        if await item.is_visible():
            return item
    return None


async def wait_until(check, message, timeout=15, interval=.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = await check()
        if result:
            return result
        await asyncio.sleep(interval)
    raise TimeoutError(message)


class Completion:
    def __init__(self, fallback=4):
        self.previous = ""
        self.changed = time.monotonic()
        self.idle_since = None
        self.fallback = max(2, fallback)

    def update(self, text, busy, done, now=None):
        now = time.monotonic() if now is None else now
        if text != self.previous:
            self.previous, self.changed = text, now
        if busy or not text:
            self.idle_since = None
            return False
        if self.idle_since is None:
            self.idle_since = now
        # A copy toolbar is a completion signal; otherwise use a conservative
        # quiet period. Never accept even a long text pause while Stop is visible.
        quiet = .4 if done else self.fallback
        return now - self.changed >= quiet and now - self.idle_since >= quiet


class GeminiPage:
    def __init__(self, page, stage=lambda value: None):
        self.page = page
        self.stage = stage
        self.dirty = False
        self.last_image = False
        self.copy_supported = True
        self.new_chat_supported = True

    async def editor(self):
        if "accounts.google.com" in self.page.url or "signin" in self.page.url:
            raise LoginRequired("Sign in to Gemini using Open login window.")
        sign_in = await first_visible(self.page.get_by_role("link", name=re.compile(r"^(sign in|log ind)$", re.I)))
        if sign_in is not None:
            raise LoginRequired("Sign in to Gemini using Open login window.")
        return await wait_until(
            lambda: first_visible(self.page.locator(INPUT)),
            "Gemini's prompt box is unavailable. Open login window, or inspect Gemini's changed layout.",
            timeout=20,
        )

    async def reset_chat(self):
        page = self.page
        reset = await first_visible(page.get_by_role("button", name=NEW_NAME)) if self.new_chat_supported else None
        if reset is None and self.new_chat_supported:
            reset = await first_visible(page.get_by_role("link", name=NEW_NAME))
        if reset is None and self.new_chat_supported:
            reset = await first_visible(page.locator("[data-test-id='new-chat-button']"))
        if reset:
            await reset.click(timeout=3000)
            async def cleared():
                editor = await first_visible(page.locator(INPUT))
                return editor and not await page.locator(ANSWERS).filter(visible=True).count() and not (await editor.inner_text()).strip()
            try:
                await wait_until(cleared, "New-chat action did not reset the composer.", timeout=3)
                return
            except TimeoutError:
                log.info("new_chat_fallback=navigation")
                # Do not pay the same failed UI wait on every later image.
                self.new_chat_supported = False
        # A verified reset is required between image questions. Navigation is a
        # fallback, not an unconditionally paid cost before every upload.
        await page.goto(URL, wait_until="domcontentloaded", timeout=20000)
        await self.editor()
        if await page.locator(ANSWERS).filter(visible=True).count():
            raise RuntimeError("Could not isolate the next image in a fresh chat.")

    async def prepare(self, image):
        self.stage("preparing")
        if not self.page.url.startswith("https://gemini.google.com/"):
            await self.page.goto(URL, wait_until="domcontentloaded", timeout=20000)
        if self.dirty or image and (self.last_image or await self.page.locator(ANSWERS).filter(visible=True).count()):
            await self.reset_chat()
        editor = await self.editor()
        if (await editor.inner_text()).strip():
            # Never submit a stale draft or a request left behind by a timeout.
            await self.reset_chat()
            editor = await self.editor()
        self.dirty = False
        return editor

    async def upload(self, path, timing):
        self.stage("uploading")
        self.dirty = True
        page = self.page
        async def available_input():
            inputs = page.locator("input[type=file]")
            for index in range(await inputs.count()):
                item = inputs.nth(index)
                accept = (await item.get_attribute("accept") or "").lower()
                if not accept or "image" in accept or any(x in accept for x in (".png", ".jpg", ".webp")):
                    return item
            return None

        async with timing.stage("attachment_menu"):
            target = await available_input()
            kind = "input"
            if target is None:
                button = await first_visible(page.get_by_role("button", name=ATTACH_NAME))
                if button is None:
                    button = await first_visible(page.locator(
                        "button[data-test-id='upload-file-button'], button[aria-haspopup='menu'][aria-label*='Tilføj' i], "
                        "button[aria-haspopup='menu'][aria-label*='Add' i]"
                    ))
                if button is None:
                    raise RuntimeError("Attachment button not found. Diagnostic step: attachment_menu.")

                # Register before the click, but race observed menu/input states
                # against the event instead of always waiting out a chooser timeout.
                chooser = asyncio.create_task(page.wait_for_event("filechooser", timeout=12000))
                try:
                    await button.click(timeout=3000)
                    async def route():
                        if chooser.done():
                            return "chooser", chooser.result()
                        field = await available_input()
                        if field is not None:
                            return "input", field
                        item = await first_visible(page.get_by_role("menuitem", name=FILES_NAME))
                        if item is None:
                            item = await first_visible(page.get_by_text(FILES_NAME, exact=True))
                        return ("menu", item) if item is not None else None
                    kind, target = await wait_until(route, "Attachment menu did not open.", timeout=6)
                    if kind == "menu":
                        # Text can be a span inside the interactive menu item.
                        parent = target.locator("xpath=ancestor-or-self::*[self::button or @role='menuitem'][1]")
                        if await parent.count():
                            target = parent
                        await target.click(timeout=3000)
                        async def selected():
                            if chooser.done():
                                return "chooser", chooser.result()
                            field = await available_input()
                            return ("input", field) if field is not None else None
                        kind, target = await wait_until(selected, "Files item did not expose a file chooser/input.", timeout=6)
                finally:
                    if not chooser.done():
                        chooser.cancel()
                    await asyncio.gather(chooser, return_exceptions=True)

        async with timing.stage("upload"):
            if kind == "chooser":
                await target.set_files(str(path), timeout=10000)
            else:
                await target.set_input_files(str(path), timeout=10000)

        async with timing.stage("attachment_ready"):
            filename = Path(path).name
            async def attached():
                # Require a composer attachment, never a filename in old history.
                editor = await first_visible(page.locator(INPUT))
                if editor is None:
                    return False
                composer = editor.locator("xpath=ancestor::*[.//input[@type='file'] or .//button[contains(@data-test-id,'send')]][1]")
                scope = composer if await composer.count() else page.locator(
                    "input-area-v2, input-area, .input-area, .text-input-field"
                )
                if not await scope.count():
                    return False
                image = scope.locator("img[src^='blob:'], img[src^='data:image'], .attachment-thumbnail, [data-test-id='attachment-preview']")
                named = scope.get_by_text(filename, exact=True)
                preview = await first_visible(image) or await first_visible(named)
                if preview is None or await first_visible(scope.locator(BUSY)):
                    return False
                return True
            await wait_until(attached, "Image was selected but Gemini has not confirmed an attachment preview.", timeout=25)

    async def send(self, editor, question):
        self.stage("sending")
        await editor.fill(question, timeout=5000)
        # Tag existing answers so count changes, old toolbar updates, and
        # identical consecutive answers cannot be mistaken for this response.
        marker = uuid.uuid4().hex
        await self.page.locator(ANSWERS).evaluate_all(
            "(nodes, marker) => nodes.forEach(n => n.dataset.secretaiPrevious = marker)", marker,
        )
        target = await first_visible(self.page.locator(SEND))
        self.dirty = True
        # One submission action only. An ambiguous click timeout is never retried.
        if target is not None:
            await wait_until(target.is_enabled, "Send is still disabled; attachment may be processing.", timeout=15)
            await target.click(timeout=5000)
        else:
            await editor.press("Enter", timeout=5000)
        async def submitted():
            if await self.new_answer(marker):
                return True
            return not (await editor.inner_text()).strip()
        await wait_until(submitted, "Submission was not confirmed. It was not sent a second time.", timeout=10)
        return marker

    async def new_answer(self, marker):
        nodes = self.page.locator(ANSWERS).filter(visible=True)
        for index in reversed(range(await nodes.count())):
            item = nodes.nth(index)
            if await item.get_attribute("data-secretai-previous") != marker:
                return item
        return None

    async def response_root(self, body):
        parents = body.locator("xpath=ancestor::*[self::model-response or @data-message-author-role='model'][1]")
        return parents if await parents.count() else body

    async def wait_answer(self, marker):
        self.stage("generating")
        async def started():
            body = await self.new_answer(marker)
            return body if body is not None and (await body.evaluate(EXTRACT)).strip() else None
        body = await wait_until(started, "No new Gemini answer appeared.", timeout=120)
        return body

    async def complete(self, marker):
        completion = Completion(float(os.getenv("GEMINI_STABLE_SECONDS", "4")))
        while True:
            body = await self.new_answer(marker)
            if body is not None:
                text = await body.evaluate(EXTRACT)
                root = await self.response_root(body)
                done = await first_visible(root.get_by_role("button", name=COPY_NAME))
                busy = await first_visible(self.page.locator(STOP)) is not None
                busy = busy or await first_visible(root.locator(BUSY)) is not None
                if completion.update(text, busy, done is not None):
                    return body
            await asyncio.sleep(.15)

    async def extract(self, body):
        structured = (await body.evaluate(EXTRACT)).strip()
        if not structured:
            raise RuntimeError("Gemini returned no extractable answer.")
        root = await self.response_root(body)
        copy_button = await first_visible(root.get_by_role("button", name=COPY_NAME))
        method = "structured"
        if copy_button is not None and self.copy_supported:
            # Capture the site's own writeText payload without changing the OS
            # clipboard. No browser clipboard permissions are required.
            token = uuid.uuid4().hex
            installed = await self.page.evaluate("""token => {
                if (!navigator.clipboard || !navigator.clipboard.writeText) return false;
                const original = navigator.clipboard.writeText;
                const originalWrite = navigator.clipboard.write;
                const slot = {token, original, originalWrite, text: null};
                window.__secretaiCopy = slot;
                try {
                    navigator.clipboard.writeText = async text => { slot.text = String(text); };
                    if (originalWrite) navigator.clipboard.write = async items => {
                        for (const item of items) if (item.types.includes('text/plain')) {
                            slot.text = await (await item.getType('text/plain')).text();
                            break;
                        }
                    };
                    return navigator.clipboard.writeText !== original;
                } catch (_) { return false; }
            }""", token)
            if installed:
                try:
                    await copy_button.click(timeout=1500)
                    async def copied():
                        return await self.page.evaluate(
                            "token => window.__secretaiCopy?.token === token ? window.__secretaiCopy.text : null", token,
                        )
                    native = await wait_until(copied, "Native copy did not provide text.", timeout=.8, interval=.05)
                    # The response's own Copy action is preferred for plain
                    # answers. For rendered math, require agreement with the
                    # canonical expression so duplicate renderings/raw TeX
                    # cannot replace the structured result.
                    normalize = lambda s: re.sub(r"\s+", "", s)
                    math_count = await body.locator(".katex, math, mjx-container").count()
                    formatted = re.search(r"\\(?:ce|frac|mathrm|begin|text)\b|\$\$|\x60\x60\x60", native)
                    if not formatted and (not math_count or normalize(native) == normalize(structured)):
                        structured, method = native.strip(), "native-copy"
                    else:
                        log.info("copy_comparison=differs selected=structured")
                except (TimeoutError, PlaywrightError) as exc:
                    log.info("native_copy_fallback=%s", type(exc).__name__)
                    self.copy_supported = False
                finally:
                    await self.page.evaluate("""token => {
                        const slot = window.__secretaiCopy;
                        if (slot?.token === token) {
                            navigator.clipboard.writeText = slot.original;
                            if (slot.originalWrite) navigator.clipboard.write = slot.originalWrite;
                            delete window.__secretaiCopy;
                        }
                    }""", token)
        log.info("answer_extraction=%s", method)
        return structured

    async def ask(self, question, image_path, timing):
        async with timing.stage("prepare"):
            editor = await self.prepare(bool(image_path))
        if image_path:
            await self.upload(image_path, timing)
        async with timing.stage("send"):
            marker = await self.send(editor, question)
        async with timing.stage("first_response"):
            await self.wait_answer(marker)
        async with timing.stage("generation_remaining"):
            body = await self.complete(marker)
        self.stage("extracting")
        async with timing.stage("extract"):
            answer = await self.extract(body)
        self.dirty = False
        self.last_image = bool(image_path)
        return answer
