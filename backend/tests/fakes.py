"""Test doubles for the Gemini SDK."""

import asyncio
from types import SimpleNamespace


class Recorder:
    """Stands in for asyncio.sleep so tests do not wait."""

    def __init__(self):
        self.delays = []

    async def __call__(self, delay):
        self.delays.append(delay)


class FakeSdk:
    """Looks like genai.Client for the one call we use.

    Each entry of `outcomes` is consumed by one request: a string is returned as the
    response text, an exception is raised, a callable is awaited.
    """

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._generate))

    async def _generate(self, **kwargs):
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome):
            return await outcome()
        return SimpleNamespace(text=outcome)


class HandlerSdk:
    """Answers every request with `handler(contents) -> str`, and records how many
    requests were in flight at once."""

    def __init__(self, handler, delay=0.0):
        self.handler = handler
        self.delay = delay
        self.requests = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._generate))

    async def _generate(self, **kwargs):
        self.requests.append(kwargs)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            return SimpleNamespace(text=self.handler(kwargs["contents"]))
        finally:
            self.in_flight -= 1
