"""Real headless Chromium tests against local fixtures; no Google requests."""
import asyncio
import base64
from pathlib import Path
import tempfile
import time
import unittest

from playwright.async_api import async_playwright

from geminisecretai.browser import EXTRACT, GeminiPage, URL
from geminisecretai.bridge import find_browser
from geminisecretai.requests import Timing

FIXTURE = Path(__file__).parent / "fixtures" / "gemini.html"


class BrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(executable_path=find_browser(), headless=True)
        self.context = await self.browser.new_context()
        await self.context.route("**/*", lambda route: route.fulfill(
            body=FIXTURE.read_text(encoding="utf-8"), content_type="text/html"))
        self.page = await self.context.new_page()
        await self.page.goto(URL)
        self.session = GeminiPage(self.page)
        self.folder = tempfile.TemporaryDirectory()
        self.image = Path(self.folder.name) / "capture-123.png"
        self.image.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg=="))

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()
        self.folder.cleanup()

    async def ask(self, question, image=False):
        async with asyncio.timeout(12):
            return await self.session.ask(question, self.image if image else None, Timing(question))

    async def test_text_and_identical_followup_are_distinct_requests(self):
        self.assertEqual(await self.ask("2+2"), "4")
        self.assertEqual(await self.ask("2+2 again"), "4")
        self.assertEqual(await self.page.evaluate("sends"), 2)

    async def test_math_is_extracted_once_with_no_ui_or_attachment(self):
        self.assertEqual(await self.ask("CHEMISTRY"), "CH4")
        await self.page.evaluate("copyMode='dirty'")
        self.assertEqual(await self.ask("CHEMISTRY again"), "CH4")
        self.assertIsNone(await self.page.evaluate("window.__secretaiCopy || null"))

    async def test_carets_survive_composer_submission_and_answer_extraction(self):
        question = "2^3; x^2 + y^2; SO4^2-; ^^"
        await self.page.evaluate("""() => {
            const original = submitQuestion;
            submitQuestion = () => { window.sentText = editor.innerText; original(); };
        }""")
        await self.ask(question)
        self.assertEqual(await self.page.evaluate("window.sentText"), question)
        body = self.page.locator("message-content").last
        await body.evaluate("(node, text) => node.textContent = text", question)
        self.session.copy_supported = False
        self.assertEqual(await self.session.extract(body), question)

    async def test_mathml_equation_charges_fractions_and_legitimate_repetition(self):
        await self.page.set_content("""
            <model-response><message-content>
            <p>CH4 and CH4</p>
            <math><mrow><msubsup><mi>SO</mi><mn>4</mn><mrow><mn>2</mn><mo>-</mo></mrow></msubsup></mrow></math>
            <math><mfrac><mn>1</mn><mn>2</mn></mfrac></math>
            <p>2H2 + O2 -&gt; 2H2O</p>
            <div class="attachment">capture-123.png</div><button>Copy</button>
            </message-content></model-response>""")
        result = await self.page.locator("message-content").evaluate(EXTRACT)
        self.assertIn("CH4 and CH4", result)
        self.assertIn("SO4^2-", result)
        self.assertIn("(1)/(2)", result)
        self.assertIn("2H2 + O2 -> 2H2O", result)
        self.assertNotIn("capture", result)
        self.assertNotIn("Copy", result)

    async def test_five_images_danish_menu_then_text_image_alternation(self):
        await self.page.evaluate("language='da'")
        elapsed = []
        for index in range(5):
            start = time.monotonic()
            self.assertEqual(await self.ask(f"image{index}", image=True), "4")
            elapsed.append(round(time.monotonic() - start, 3))
        self.assertEqual(await self.ask("text afterwards"), "4")
        self.assertEqual(await self.ask("CHEMISTRY", image=True), "CH4")
        self.assertEqual(await self.page.evaluate("sends"), 7)
        self.assertEqual(await self.page.evaluate("resets"), 5)
        print("fixture_image_seconds=", elapsed)

    async def test_generation_pause_longer_than_old_completion_threshold(self):
        await self.page.evaluate("generationDelay=1300")
        started = time.monotonic()
        self.assertEqual(await self.ask("CHEMISTRY"), "CH4")
        self.assertGreater(time.monotonic() - started, 1.3)
        self.assertEqual(await self.page.evaluate("sends"), 1)

    async def test_delayed_attachment_processing_is_observed(self):
        await self.page.evaluate("uploadDelay=600")
        self.assertEqual(await self.ask("image", image=True), "4")
        self.assertEqual(await self.page.evaluate("sends"), 1)

    async def test_ambiguous_submission_is_never_retried(self):
        await self.page.evaluate("submissionFails=true")
        editor = await self.session.prepare(False)
        with self.assertRaises(TimeoutError):
            async with asyncio.timeout(.8):
                await self.session.send(editor, "2+2")
        self.assertEqual(await self.page.evaluate("sends"), 1)
        self.assertTrue(self.session.dirty)

    async def test_unsupported_new_chat_action_is_not_retried_each_image(self):
        # Mirrors the live layout whose New chat action leaves the old response.
        await self.page.evaluate("resetChat = () => { window.resets++; }")
        await self.page.locator("#history").evaluate(
            "node => node.innerHTML='<model-response><message-content>old</message-content></model-response>'")
        await self.session.reset_chat()
        self.assertFalse(self.session.new_chat_supported)
        await self.session.reset_chat()
        self.assertEqual(await self.page.evaluate("resets"), 0)


if __name__ == "__main__":
    unittest.main()
