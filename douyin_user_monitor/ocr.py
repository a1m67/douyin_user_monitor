from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass(frozen=True)
class OCRResult:
    text: str
    confidence: float


class OCRBackend(Protocol):
    async def extract_text(self, image_url: str) -> OCRResult:
        ...


class HttpOCRBackend:
    def __init__(self, *, api_url: str, api_key: str = "", timeout_seconds: float = 15) -> None:
        self.api_url, self.api_key, self.timeout_seconds = api_url, api_key, timeout_seconds

    async def extract_text(self, image_url: str) -> OCRResult:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(self.api_url, json={"image_url": image_url}, headers=headers)
            response.raise_for_status()
            data = response.json()
        return OCRResult(text=str(data.get("text") or "").strip(), confidence=max(0.0, min(float(data.get("confidence") or 0), 1.0)))


class FakeOCRBackend:
    def __init__(self, result: OCRResult | None = None, *, error: Exception | None = None) -> None:
        self.result, self.error, self.calls = result or OCRResult("", 0), error, 0

    async def extract_text(self, image_url: str) -> OCRResult:
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def run_ocr(backend: OCRBackend, image_url: str, timeout_seconds: float) -> OCRResult:
    return asyncio.run(asyncio.wait_for(backend.extract_text(image_url), timeout=timeout_seconds))
