import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import uuid

from fastapi import HTTPException

from geminisecretai import bridge as service
from geminisecretai.browser import LoginRequired
from geminisecretai.requests import RequestManager


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_manager = service.manager
        service.manager = RequestManager()
        self.old_state = dict(service.state)
        self.auth = patch.object(service, "OWNER", "test-owner")
        self.auth.start()
        self.request = SimpleNamespace(headers={"x-gemini-owner": "test-owner"},
                                       is_disconnected=AsyncMock(return_value=False))

    async def asyncTearDown(self):
        await service.manager.cancel_all()
        service.manager = self.old_manager
        service.state.update(self.old_state)
        self.auth.stop()

    def body(self):
        return {"request_id": str(uuid.uuid4()), "q": "2+2", "system_prompt": "Only answer"}

    async def test_ready_requires_a_live_page_and_usable_composer(self):
        service.state.update(phase="checking_login", mode="run", page=None,
                             browser=SimpleNamespace(is_connected=lambda: True))
        with patch.object(service, "browser_alive", return_value=True):
            self.assertEqual((await service.status(self.request))["phase"], "disconnected")
            service.state["page"] = SimpleNamespace(
                is_closed=lambda: False, locator=lambda selector: object(), url="https://gemini.google.com/app")
            with patch.object(service, "first_visible", AsyncMock(return_value=None)):
                self.assertEqual((await service.status(self.request))["phase"], "checking_login")
            with patch.object(service, "first_visible", AsyncMock(return_value=object())):
                self.assertEqual((await service.status(self.request))["phase"], "ready")

    async def test_owner_required_for_mutation(self):
        self.request.headers = {}
        with self.assertRaises(HTTPException) as caught:
            await service.ask(self.body(), self.request)
        self.assertEqual(caught.exception.status_code, 403)

    async def test_request_identity_prompt_and_empty_answer(self):
        session = SimpleNamespace(ask=AsyncMock(return_value="4"))
        body = self.body()
        with patch.object(service, "get_session", AsyncMock(return_value=session)):
            result = await service.ask(body, self.request)
        self.assertEqual(result["request_id"], body["request_id"])
        self.assertEqual(result["answer"], "4")
        self.assertIn("Only answer", session.ask.call_args.args[0])
        session.ask.return_value = " "
        with patch.object(service, "get_session", AsyncMock(return_value=session)), \
             patch.object(service, "discard_request_page", AsyncMock()) as discard, \
             patch.object(service, "diagnostic", AsyncMock()):
            with self.assertRaises(HTTPException) as caught:
                await service.ask(self.body(), self.request)
            self.assertEqual(caught.exception.status_code, 500)
            discard.assert_awaited_once()

    async def test_disconnect_cancels_request_and_discards_page(self):
        started = asyncio.Event()
        async def hangs(*args):
            started.set()
            await asyncio.sleep(10)
        session = SimpleNamespace(ask=hangs)
        checks = 0
        async def disconnected():
            nonlocal checks
            checks += 1
            return started.is_set()
        self.request.is_disconnected.side_effect = disconnected
        with patch.object(service, "get_session", AsyncMock(return_value=session)), \
             patch.object(service, "discard_request_page", AsyncMock()) as discard:
            with self.assertRaises(HTTPException) as caught:
                await service.ask(self.body(), self.request)
            self.assertEqual(caught.exception.status_code, 499)
            discard.assert_awaited_once()
        self.assertFalse(service.manager.lock.locked())

    async def test_login_failure_is_explicit(self):
        with patch.object(service, "get_session", AsyncMock(side_effect=LoginRequired("Please sign in"))), \
             patch.object(service, "discard_request_page", AsyncMock()), \
             patch.object(service, "diagnostic", AsyncMock()):
            with self.assertRaises(HTTPException) as caught:
                await service.ask(self.body(), self.request)
        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(service.state["phase"], "login_required")

    async def test_disconnected_browser_recovers_before_submission(self):
        service.state.update(mode="run", browser=SimpleNamespace(is_connected=lambda: False),
                             page=SimpleNamespace(is_closed=lambda: False), session="reconnected")
        with patch.object(service, "browser_alive", return_value=True), \
             patch.object(service, "connect_browser", AsyncMock()) as reconnect:
            self.assertEqual(await service.get_session(), "reconnected")
            reconnect.assert_awaited_once()

    async def test_health_does_not_pretend_to_be_logged_in(self):
        service.state.update(mode="login", phase="login_required")
        result = await service.status(self.request)
        self.assertEqual(result["phase"], "login_required")

    async def test_overall_timeout_and_explicit_cancellation_release_page(self):
        entered = asyncio.Event()
        async def hangs(*args):
            entered.set()
            await asyncio.sleep(10)
        session = SimpleNamespace(ask=hangs)
        with patch.object(service, "get_session", AsyncMock(return_value=session)), \
             patch.object(service, "discard_request_page", AsyncMock()) as discard, \
             patch.object(service, "MAX_WAIT", .03):
            with self.assertRaises(HTTPException) as caught:
                await service.ask(self.body(), self.request)
            self.assertEqual(caught.exception.status_code, 504)
            discard.assert_awaited_once()
        entered.clear()
        body = self.body()
        with patch.object(service, "get_session", AsyncMock(return_value=session)), \
             patch.object(service, "discard_request_page", AsyncMock()) as discard:
            running = asyncio.create_task(service.ask(body, self.request))
            await entered.wait()
            await service.cancel({"request_id": body["request_id"]}, self.request)
            with self.assertRaises(HTTPException) as caught:
                await running
            self.assertEqual(caught.exception.status_code, 409)
            discard.assert_awaited_once()
        self.assertFalse(service.manager.lock.locked())


if __name__ == "__main__":
    unittest.main()
