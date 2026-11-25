"""Bounded, cancellable request execution without automatic resubmission."""
import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
import logging
import time

log = logging.getLogger("gemini_bridge")


class Timing:
    def __init__(self, request_id):
        self.request_id = request_id
        self.started = time.monotonic()
        self.values = {}

    @asynccontextmanager
    async def stage(self, name):
        started = time.monotonic()
        try:
            yield
        finally:
            self.values[name] = round(time.monotonic() - started, 3)

    def finish(self):
        self.values["total"] = round(time.monotonic() - self.started, 3)
        log.info("request=%s timings_seconds=%s", self.request_id, self.values)
        return dict(self.values)


class RequestManager:
    def __init__(self, queue_timeout=5):
        self.lock = asyncio.Lock()
        self.queue_timeout = queue_timeout
        self.active = {}
        self.finished = OrderedDict()

    async def execute(self, request_id, operation, timeout):
        if request_id in self.finished or request_id in self.active:
            raise ValueError("This request ID has already been used; it will not be sent again.")

        async def run():
            acquired = False
            try:
                async with asyncio.timeout(timeout):
                    await asyncio.wait_for(self.lock.acquire(), self.queue_timeout)
                    acquired = True
                    return await operation()
            finally:
                if acquired:
                    self.lock.release()
                self.finished[request_id] = True
                while len(self.finished) > 128:
                    self.finished.popitem(last=False)

        task = asyncio.create_task(run(), name=request_id)
        self.active[request_id] = task
        try:
            return await task
        finally:
            self.active.pop(request_id, None)

    async def cancel(self, request_id):
        task = self.active.get(request_id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        elif request_id:
            # Cancellation can arrive just before the HTTP request is registered.
            self.finished[request_id] = True
            while len(self.finished) > 128:
                self.finished.popitem(last=False)
        return bool(task)

    async def cancel_all(self):
        tasks = list(self.active.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
