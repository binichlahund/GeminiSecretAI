"""Local, owned Gemini browser service with bounded requests and lifecycle."""
import asyncio
from contextlib import asynccontextmanager
import hmac
import logging
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from playwright.async_api import async_playwright

from .browser import GeminiPage, INPUT, URL, LoginRequired, first_visible
from .requests import RequestManager, Timing

from .paths import PATHS
HERE = PATHS.root
PROFILE = PATHS.profile
RUN_VISIBLE = os.getenv("GEMINI_RUN_VISIBLE", "0") == "1"
MAX_WAIT = int(os.getenv("GEMINI_MAX_WAIT", "180"))
OWNER = os.getenv("GEMINI_OWNER_TOKEN", "")
PROTOCOL = 2
log = logging.getLogger("gemini_bridge")
manager = RequestManager(queue_timeout=5)
state = {"mode": None, "proc": None, "browser": None, "context": None,
         "page": None, "session": None, "error": None, "phase": "starting",
         "last_owner": time.monotonic(), "request_id": None, "timings": {}}
server = None


def find_browser():
    if os.getenv("GEMINI_BROWSER"):
        path = os.environ["GEMINI_BROWSER"]
        if Path(path).is_file():
            return path
        raise RuntimeError("GEMINI_BROWSER does not point to an installed browser.")
    candidates = []
    if sys.platform.startswith("win"):
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(var)
            if base:
                candidates += [str(Path(base) / name) for name in (
                    "Google/Chrome/Application/chrome.exe",
                    "Microsoft/Edge/Application/msedge.exe")]
    else:
        candidates = [shutil.which(n) for n in ("google-chrome", "chromium")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("Install Chrome or Edge, or set GEMINI_BROWSER.")


def browser_alive():
    proc = state.get("proc")
    return bool(proc and proc.poll() is None)


def authorize(request):
    token = request.headers.get("x-gemini-owner", "")
    if not OWNER or not hmac.compare_digest(token, OWNER):
        raise HTTPException(403, "This bridge belongs to a different app instance.")
    state["last_owner"] = time.monotonic()


async def close_browser():
    browser, proc = state.get("browser"), state.get("proc")
    if browser is None and proc and proc.poll() is None:
        try:
            browser = await state["pw"].chromium.connect_over_cdp(
                f"http://127.0.0.1:{state['debug_port']}", timeout=1500)
        except Exception as exc:
            log.info("browser_close_connect=%s", type(exc).__name__)
    if browser:
        try:
            async with asyncio.timeout(2):
                cdp = await browser.new_browser_cdp_session()
                await cdp.send("Browser.close")
        except Exception as exc:
            log.info("browser_close=%s", type(exc).__name__)
    if proc and proc.poll() is None:
        try:
            await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=3)
        except TimeoutError:
            # Only terminate the Popen handle created by this service.
            proc.terminate()
            await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=3)
    state.update(proc=None, browser=None, context=None, page=None, session=None)


async def connect_browser():
    browser = await state["pw"].chromium.connect_over_cdp(
        f"http://127.0.0.1:{state['debug_port']}", timeout=1500)
    context = browser.contexts[0]
    page = next((p for p in context.pages if "gemini.google.com" in p.url), None)
    if page is None:
        page = await context.new_page()
    if not RUN_VISIBLE:
        await page.set_viewport_size({"width": 1280, "height": 900})
    state.update(browser=browser, context=context, page=page,
                 session=GeminiPage(page, lambda phase: state.update(phase=phase)))


async def launch(mode):
    state.update(phase="starting", error=None)
    await close_browser()
    # A fresh available port prevents attaching to another app's CDP session.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", int(os.getenv("GEMINI_PORT", "0"))))
        port = probe.getsockname()[1]
    state["debug_port"] = port
    args = [find_browser(), f"--remote-debugging-port={port}",
            f"--user-data-dir={PROFILE}", "--no-first-run", "--no-default-browser-check"]
    if mode == "run" and not RUN_VISIBLE:
        args.append("--headless=new")
    args.append(URL)
    state.update(proc=subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
                 mode=mode)
    if mode == "login":
        state["phase"] = "login_required"
        return
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if not browser_alive():
            raise RuntimeError("The dedicated browser exited during startup.")
        try:
            await connect_browser()
            state["phase"] = "checking_login"
            return
        except Exception:
            await asyncio.sleep(.2)
    raise RuntimeError("Could not connect to the dedicated browser.")


async def get_session():
    if state["mode"] != "run":
        raise HTTPException(409, "Open login window, sign in, then run headlessly.")
    if not browser_alive():
        # Recovery is allowed before sending a new question only.
        await launch("run")
    elif not state.get("browser") or not state["browser"].is_connected():
        await connect_browser()
    page = state.get("page")
    if page is None or page.is_closed():
        page = await state["context"].new_page()
        if not RUN_VISIBLE:
            await page.set_viewport_size({"width": 1280, "height": 900})
        state.update(page=page, session=GeminiPage(page, lambda phase: state.update(phase=phase)))
    return state["session"]


async def discard_request_page():
    page = state.get("page")
    if page and not page.is_closed():
        try:
            async with asyncio.timeout(2):
                await page.close()
        except Exception as exc:
            log.info("discard_page=%s", type(exc).__name__)
    state.update(page=None, session=None)


async def diagnostic(request_id):
    page = state.get("page")
    if page and not page.is_closed():
        try:
            folder = PATHS.debug
            folder.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(folder / f"error-{request_id}.png"), timeout=2000)
        except Exception as exc:
            log.info("diagnostic=%s", type(exc).__name__)


async def watchdog():
    while True:
        await asyncio.sleep(2)
        if OWNER and time.monotonic() - state["last_owner"] > 20:
            log.info("Owner heartbeat expired; closing owned service.")
            await manager.cancel_all()
            if server:
                server.should_exit = True
            return


@asynccontextmanager
async def lifespan(app):
    guard = None
    if sys.platform.startswith("win"):
        from .instance import InstanceGuard
        guard = InstanceGuard(PROFILE, "browser")
        if not guard.acquire():
            guard.close()
            raise RuntimeError("Another bridge already owns this Gemini profile.")
    state["pw"] = await async_playwright().start()
    lease = asyncio.create_task(watchdog())
    try:
        try:
            await launch("run" if PROFILE.is_dir() else "login")
        except Exception as exc:
            log.exception("Browser startup failed")
            state.update(error=str(exc), phase="failed")
        state["last_owner"] = time.monotonic()
        yield
    finally:
        lease.cancel()
        await asyncio.gather(lease, return_exceptions=True)
        await manager.cancel_all()
        try:
            await close_browser()
            await state["pw"].stop()
        finally:
            if guard:
                guard.close()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"app": "GeminiSecretAI", "root": str(HERE), "protocol": PROTOCOL,
            "browser": browser_alive(), "mode": state["mode"]}


@app.get("/status")
async def status(request: Request):
    authorize(request)
    phase = state["phase"]
    if not manager.active and phase not in ("failed", "starting"):
        if state["mode"] == "login":
            phase = "login_required"
        elif not browser_alive() or not state.get("browser") or not state["browser"].is_connected():
            phase = "disconnected"
        else:
            page = state.get("page")
            try:
                async with asyncio.timeout(.8):
                    if page is None or page.is_closed():
                        phase = "disconnected"  # Recreated and verified before use.
                    elif await first_visible(page.locator(INPUT)):
                        phase = "ready"
                    elif "accounts.google.com" in page.url or "signin" in page.url:
                        phase = "login_required"
                    else:
                        phase = "checking_login"
            except TimeoutError:
                phase = "checking_login"
    return {"phase": phase, "browser": browser_alive(), "busy": bool(manager.active),
            "mode": state["mode"], "error": state["error"],
            "headless": state["mode"] == "run" and not RUN_VISIBLE,
            "request_id": state["request_id"], "timings": state["timings"]}


@app.post("/admin/mode")
async def set_mode(body: dict, request: Request):
    authorize(request)
    if manager.active:
        raise HTTPException(409, "Cancel the current question before switching browser mode.")
    try:
        async with asyncio.timeout(30):
            async with manager.lock:
                await launch("run" if body.get("headless") else "login")
    except Exception as exc:
        state.update(error=str(exc), phase="failed")
        raise HTTPException(500, str(exc)) from exc
    return {"ok": True}


@app.post("/ask")
async def ask(body: dict, request: Request):
    authorize(request)
    question = body.get("q")
    if not isinstance(question, str) or not question.strip():
        raise HTTPException(400, "A non-empty question is required.")
    request_id = body.get("request_id", "")
    try:
        uuid.UUID(request_id)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, "A UUID request_id is required.")
    prompt = body.get("system_prompt", "")
    if prompt:
        if not isinstance(prompt, str):
            raise HTTPException(400, "system_prompt must be text.")
        question = f"{prompt}\n\n---\n\nUser request:\n{question}"
    image = body.get("image_path")
    if image and (not isinstance(image, str) or not Path(image).is_file()):
        raise HTTPException(400, "The image file is unavailable.")
    try:
        budget = min(MAX_WAIT, max(5, float(body.get("timeout_seconds", MAX_WAIT))))
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid request timeout.")
    timing = Timing(request_id)

    async def operation():
        state.update(request_id=request_id, phase="preparing", error=None)
        try:
            session = await get_session()
            answer = await session.ask(question, image, timing)
            if not answer.strip():
                raise RuntimeError("Gemini returned an empty answer.")
            state["phase"] = "ready"
            return {"answer": answer, "request_id": request_id}
        except BaseException as exc:
            # Close the uncertain page before another request can acquire the lock.
            if isinstance(exc, Exception):
                await diagnostic(request_id)
            await discard_request_page()
            raise

    task = asyncio.create_task(manager.execute(request_id, operation, budget))
    try:
        while not task.done():
            if await request.is_disconnected():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise HTTPException(499, "Client disconnected; request cancelled.")
            await asyncio.wait({task}, timeout=.2)
        result = await task
        result["timings"] = timing.finish()
        state["timings"] = result["timings"]
        return result
    except asyncio.CancelledError:
        state.update(phase="cancelled", error=None)
        if task.cancelled():
            raise HTTPException(409, "Request cancelled.")
        raise
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except LoginRequired as exc:
        state.update(phase="login_required", error=str(exc))
        timing.finish()
        raise HTTPException(401, str(exc)) from exc
    except TimeoutError as exc:
        state.update(phase="failed", error="Question timed out. You can submit a new question.")
        timing.finish()
        raise HTTPException(504, state["error"]) from exc
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("request=%s failed phase=%s", request_id, state["phase"])
        state.update(phase="failed", error=str(exc))
        timing.finish()
        raise HTTPException(500, str(exc)) from exc
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@app.post("/cancel")
async def cancel(body: dict, request: Request):
    authorize(request)
    return {"cancelled": await manager.cancel(body.get("request_id"))}


@app.post("/admin/quit")
async def quit_server(request: Request):
    authorize(request)
    await manager.cancel_all()
    if server:
        server.should_exit = True
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=int(os.environ.get("GEMINI_BRIDGE_PORT", "8001")),
        access_log=False,
    ))
    server.run()
