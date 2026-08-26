"""Tests for scripts/docling_convert.py — docling-serve client + PDF splitting.

The docling API is stubbed with a real local HTTP server (http.server) so the
client is exercised end-to-end over HTTP, not via mocks.
"""
from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from convert.docling_convert import DoclingClient, convert, pdf_page_count, split_pdf


def make_pdf(path: Path, pages: int) -> None:
    pdf = pdfium.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(width=595, height=842)
    pdf.save(str(path))
    pdf.close()


class StubDoclingHandler(BaseHTTPRequestHandler):
    """Configurable stub: sync response, async task flow, or flaky 500s."""

    mode = "sync"          # class-level so tests can flip it
    failures_left = 0
    received_files = []

    def log_message(self, *args):  # silence
        pass

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        # multipart body must carry the uploaded file name
        if b"filename=" in body:
            name = body.split(b'filename="')[1].split(b'"')[0].decode()
            StubDoclingHandler.received_files.append(name)
        if StubDoclingHandler.failures_left > 0:
            StubDoclingHandler.failures_left -= 1
            self._send_json({"error": "boom"}, code=500)
            return
        if self.path == "/v1/convert/file":
            if StubDoclingHandler.mode == "sync":
                self._send_json({"document": {"md_content": "# converted md"}})
            else:
                self._send_json({"task_id": "task-123"})
        else:
            self._send_json({"error": "not found"}, code=404)

    def do_GET(self):
        if self.path == "/v1/status/poll/task-123":
            self._send_json({"task_status": "success"})
        elif self.path == "/v1/result/task-123":
            self._send_json({"document": {"md_content": "# async md"}})
        else:
            self._send_json({"error": "not found"}, code=404)


class StubServerMixin:
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), StubDoclingHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()

    def setUp(self):
        StubDoclingHandler.mode = "sync"
        StubDoclingHandler.failures_left = 0
        StubDoclingHandler.received_files = []
        self.client = DoclingClient(f"http://127.0.0.1:{self.port}",
                                    retry_delay=0.01)


class TestClient(StubServerMixin, unittest.TestCase):
    def test_sync_convert_returns_markdown(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "doc.pdf"
            make_pdf(p, 2)
            md = self.client.convert_file(p)
        self.assertEqual(md, "# converted md")
        self.assertEqual(StubDoclingHandler.received_files, ["doc.pdf"])

    def test_async_task_flow(self):
        import tempfile
        StubDoclingHandler.mode = "async"
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "doc.pdf"
            make_pdf(p, 2)
            md = self.client.convert_file(p)
        self.assertEqual(md, "# async md")

    def test_retries_on_500(self):
        import tempfile
        StubDoclingHandler.failures_left = 2
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "doc.pdf"
            make_pdf(p, 2)
            md = self.client.convert_file(p)
        self.assertEqual(md, "# converted md")

    def test_raises_after_max_retries(self):
        import tempfile
        StubDoclingHandler.failures_left = 99
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "doc.pdf"
            make_pdf(p, 2)
            with self.assertRaises(RuntimeError):
                self.client.convert_file(p)


class TestPdfSplit(unittest.TestCase):
    def test_page_count(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "doc.pdf"
            make_pdf(p, 7)
            self.assertEqual(pdf_page_count(p), 7)

    def test_split_into_chunks(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "big.pdf"
            make_pdf(p, 120)
            chunks = split_pdf(p, chunk_pages=50, out_dir=Path(td) / "chunks")
            self.assertEqual(len(chunks), 3)
            self.assertEqual([pdf_page_count(c) for c in chunks], [50, 50, 20])

    def test_convert_small_pdf_single_call(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            server = HTTPServer(("127.0.0.1", 0), StubDoclingHandler)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                StubDoclingHandler.mode = "sync"
                StubDoclingHandler.received_files = []
                client = DoclingClient(
                    f"http://127.0.0.1:{server.server_address[1]}", retry_delay=0.01)
                p = Path(td) / "small.pdf"
                make_pdf(p, 100)
                md = convert(p, client, {"split_threshold": 100, "chunk_pages": 50})
                self.assertEqual(md, "# converted md")
                self.assertEqual(len(StubDoclingHandler.received_files), 1)
            finally:
                server.shutdown()
                t.join()

    def test_convert_large_pdf_splits_and_combines(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            server = HTTPServer(("127.0.0.1", 0), StubDoclingHandler)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                StubDoclingHandler.mode = "sync"
                StubDoclingHandler.received_files = []
                client = DoclingClient(
                    f"http://127.0.0.1:{server.server_address[1]}", retry_delay=0.01)
                p = Path(td) / "big.pdf"
                make_pdf(p, 101)
                md = convert(p, client, {"split_threshold": 100, "chunk_pages": 50})
                # 3 chunks, each converted, joined in order
                self.assertEqual(md, "# converted md\n\n# converted md\n\n# converted md")
                self.assertEqual(len(StubDoclingHandler.received_files), 3)
            finally:
                server.shutdown()
                t.join()


if __name__ == "__main__":
    unittest.main()
