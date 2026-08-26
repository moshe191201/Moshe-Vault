#!/usr/bin/env python3
"""Docling conversion module: docling-serve HTTP client + large-PDF splitting.

DoclingClient talks to a docling-serve-compatible API:
  POST {url}/v1/convert/file   multipart upload; sync JSON or {"task_id": ...}
  GET  {url}/v1/status/poll/{task_id}
  GET  {url}/v1/result/{task_id}

PDFs larger than `split_threshold` pages are split into `chunk_pages`-page
PDFs in a temp dir, converted one by one, and the markdown is concatenated
in order.
"""
from __future__ import annotations

import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

import pypdfium2 as pdfium
import requests


class DoclingServerDown(RuntimeError):
    def __init__(self, message: str, pending: list[Path]):
        super().__init__(message)
        self.pending: list[Path] = sorted(set(Path(p) for p in pending))


class PdfSplitCfg(TypedDict):
    split_threshold: int
    chunk_pages: int


@dataclass(frozen=True)
class _PollSettings:
    timeout: float = 300.0
    max_retries: int = 5
    retry_delay: float = 1.0
    poll_interval: float = 2.0


class DoclingHealthMonitor:
    def __init__(self, client: "DoclingClient", interval: float = 30.0):
        self.client = client
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="docling-health", daemon=True)
        self._thread.start()
        return self
    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
    def is_down(self):
        return self.client.is_down()
    def __enter__(self):
        return self.start()
    def __exit__(self, *_exc):
        self.stop()
    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                reachable = self.client.is_reachable(timeout=3.0)
            except Exception:
                reachable = False
            if not reachable:
                self.client._mark_down()
                break


class DoclingClient:
    def __init__(self, base_url: str, timeout: float = 300.0,
                 max_retries: int = 5, retry_delay: float = 1.0,
                 poll_interval: float = 2.0):
        self.base_url = base_url.rstrip("/")
        self._settings = _PollSettings(
            timeout=timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
            poll_interval=poll_interval,
        )
        self.timeout = self._settings.timeout
        self.max_retries = self._settings.max_retries
        self.retry_delay = self._settings.retry_delay
        self.poll_interval = self._settings.poll_interval
        self._pending: set[Path] = set()
        self._pending_lock = threading.Lock()
        self._down = False
        self._down_lock = threading.Lock()
        self._monitor: DoclingHealthMonitor | None = None
    def is_down(self):
        with self._down_lock:
            return self._down
    def _mark_down(self):
        with self._down_lock:
            self._down = True
    def _require_healthy(self):
        if self.is_down():
            pending = self.pending_snapshot()
            raise DoclingServerDown(f"docling server {self.base_url} is unreachable - {len(pending)} document(s) sent but not returned: " + ", ".join(p.name for p in pending[:20]) + (f" (+{len(pending) - 20} more)" if len(pending) > 20 else ""), pending)
    def _pending_add(self, path: Path):
        with self._pending_lock:
            self._pending.add(Path(path))
    def _pending_discard(self, path: Path):
        with self._pending_lock:
            self._pending.discard(Path(path))
    def pending_snapshot(self):
        with self._pending_lock:
            return sorted(self._pending)
    def start_health_monitor(self, interval: float = 30.0):
        if self._monitor is not None and self._monitor._thread is not None and self._monitor._thread.is_alive():
            return self._monitor
        self._monitor = DoclingHealthMonitor(self, interval=interval)
        self._monitor.start()
        return self._monitor
    def stop_health_monitor(self):
        if self._monitor is not None:
            self._monitor.stop()
            self._monitor = None

    def is_reachable(self, timeout: float = 1.0) -> bool:
        """Quick probe if docling-serve is reachable; no exception on failure."""
        try:
            requests.get(f"{self.base_url}/health", timeout=timeout).raise_for_status()
            return True
        except Exception:  # noqa: BLE001
            try:
                requests.get(f"{self.base_url}/v1/status/poll/test", timeout=timeout)
                return True
            except Exception:  # noqa: BLE001
                return False

    def convert_file(self, path: Path) -> str:
        self._require_healthy()
        path = Path(path)
        self._pending_add(path)
        try:
            data = self._post_with_retries(path)
            self._require_healthy()
            if "task_id" in data:
                data = self._wait_for_result(data["task_id"])
                self._require_healthy()
            try:
                return data["document"]["md_content"]
            except KeyError:
                raise RuntimeError(f"docling response missing document.md_content: {list(data.keys())}")
        finally:
            self._pending_discard(path)

    # -- internal HTTP helpers: hide url construction + timeout/raise duplication --

    def _get(self, path: str) -> dict:
        resp = requests.get(f"{self.base_url}{path}", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, files: dict) -> dict:
        resp = requests.post(f"{self.base_url}{path}", files=files,
                             timeout=self.timeout)
        if resp.status_code >= 500:
            raise RuntimeError(f"docling {resp.status_code}: {resp.text[:200]}")
        resp.raise_for_status()
        return resp.json()

    def _post_with_retries(self, path: Path) -> dict:
        last_err = None
        for attempt in range(self.max_retries):
            self._require_healthy()
            try:
                with open(path, "rb") as fh:
                    return self._post("/v1/convert/file", files={"files": (path.name, fh)})
            except Exception as e:
                last_err = e
                if isinstance(e, DoclingServerDown):
                    raise
                if attempt < self.max_retries - 1:
                    try:
                        self._require_healthy()
                    except DoclingServerDown:
                        raise
                    time.sleep(self.retry_delay * (2 ** attempt))
        raise RuntimeError(f"docling convert failed after {self.max_retries} "
                           f"attempts for {path.name}: {last_err}")

    def _wait_for_result(self, task_id: str) -> dict:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            self._require_healthy()
            status = self._get(f"/v1/status/poll/{task_id}").get("task_status")
            if status == "success":
                return self._get(f"/v1/result/{task_id}")
            if status == "failure":
                raise RuntimeError(f"docling task {task_id} failed")
            time.sleep(self.poll_interval)
        raise RuntimeError(f"docling task {task_id} timed out")


def pdf_page_count(path: Path) -> int:
    with pdfium.PdfDocument(str(path)) as pdf:
        return len(pdf)


def split_pdf(path: Path, chunk_pages: int, out_dir: Path) -> list[Path]:
    """Split path into <=chunk_pages PDFs in out_dir; return chunks in order."""
    path, out_dir = Path(path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks = []
    with pdfium.PdfDocument(str(path)) as src:
        total = len(src)
        for start in range(0, total, chunk_pages):
            end = min(start + chunk_pages, total)
            chunk = pdfium.PdfDocument.new()
            chunk.import_pages(src, list(range(start, end)))
            out = out_dir / f"{path.stem}_p{start + 1}-{end}.pdf"
            chunk.save(str(out))
            chunk.close()
            chunks.append(out)
    return chunks


def convert(path: Path, client: DoclingClient, config: PdfSplitCfg) -> str:
    client._require_healthy()
    path = Path(path)
    client._pending_add(path)
    try:
        threshold = config.get("split_threshold", 100)
        chunk_pages = config.get("chunk_pages", 50)
        if path.suffix.lower() == ".pdf" and pdf_page_count(path) > threshold:
            with tempfile.TemporaryDirectory(prefix="docling_chunks_") as td:
                chunks = split_pdf(path, chunk_pages, Path(td))
                return "\n\n".join(client.convert_file(c) for c in chunks)
        return client.convert_file(path)
    finally:
        client._pending_discard(path)
