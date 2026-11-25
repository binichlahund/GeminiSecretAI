"""Explicit live smoke check using this installation's existing Gemini profile.

Run from the project folder: python tests/live_check.py
Only synthetic questions/images are sent. Stop at the first failure.
"""
import asyncio
import logging
from pathlib import Path
import sys
import tempfile
import uuid

from geminisecretai.settings import DEFAULT_PROMPT
from geminisecretai.browser import INPUT, first_visible
from geminisecretai import bridge as service
from geminisecretai.requests import Timing


async def main():
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not service.PROFILE.is_dir():
        print("LIVE_UNAVAILABLE: no saved Gemini login profile")
        return
    async with service.lifespan(service.app):
        if service.state["error"]:
            print("LIVE_UNAVAILABLE:", service.state["error"])
            return
        page = service.state["page"]
        try:
            async with asyncio.timeout(25):
                await service.state["session"].editor()
        except Exception as exc:
            print("LIVE_UNAVAILABLE:", type(exc).__name__, str(exc))
            print("PAGE_HOST:", page.url.split("/")[2])
            print("BUTTONS:", await page.get_by_role("button").evaluate_all(
                "nodes => nodes.map(n => ({name:n.getAttribute('aria-label'), test:n.getAttribute('data-test-id')}))"))
            return
        # Read only DOM structure to establish whether math has two renderings.
        print("MATH_STRUCTURE:", await page.evaluate("""() => ({
            katex: document.querySelectorAll('.katex').length,
            mathml: document.querySelectorAll('model-response math').length,
            visual: document.querySelectorAll('.katex-html').length
        })"""))
        from PIL import Image, ImageDraw
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "arithmetic-test.png"
            bitmap = Image.new("RGB", (360, 120), "white")
            ImageDraw.Draw(bitmap).text((40, 35), "2 + 2", fill="black", font_size=40)
            bitmap.save(image)
            cases = [("What is 2+2?", None), ("Give only the molecular formula of methane.", None)]
            cases += [("Answer the task shown in the image.", image)] * 5
            cases += [("What is 2+2?", None), ("Answer the task shown in the image.", image)]
            if "--images-only" in sys.argv:
                cases = [("Answer the task shown in the image.", image)] * 2
            for index, (question, attachment) in enumerate(cases):
                timing = Timing(str(uuid.uuid4()))
                try:
                    async with asyncio.timeout(60):
                        session = await service.get_session()
                        session.stage = lambda phase: print("LIVE_STAGE", phase, flush=True)
                        answer = await session.ask(DEFAULT_PROMPT + "\n\n" + question, attachment, timing)
                    print("LIVE_RESULT", index + 1, repr(answer), timing.finish())
                except Exception as exc:
                    print("LIVE_FAILED", index + 1, type(exc).__name__, str(exc))
                    await service.diagnostic(timing.request_id)
                    print("RESPONSE_STRUCTURE:", await page.locator(
                        "model-response, message-content, .response-container, [data-message-author-role]"
                    ).evaluate_all("nodes => nodes.slice(-5).map(n => n.outerHTML.slice(0,14000))"), flush=True)
                    print("BUTTONS:", await page.get_by_role("button").evaluate_all(
                        "nodes => nodes.map(n => ({name:n.getAttribute('aria-label'), text:n.innerText, test:n.getAttribute('data-test-id')}))"))
                    print("COMPOSER_STRUCTURE:", await page.locator(INPUT).first.evaluate(
                        "n => n.closest('input-area-v2, input-area, .input-area, .text-input-field')?.outerHTML.slice(0,16000) || n.parentElement.parentElement.outerHTML.slice(0,10000)"))
                    break


if __name__ == "__main__":
    asyncio.run(main())
