"""Tests for scripts/convert_to_md.py — the main orchestrator.

End-to-end tests run against a stub docling-serve HTTP server and a
synthetic vault (raw/ tree) built in a temp dir.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import convert.convert_to_md as convert_to_md
from convert.convert_to_md import build_frontmatter, load_config, run, should_skip


class StubDoclingHandler(BaseHTTPRequestHandler):
    received_files = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        if b"filename=" in body:
            name = body.split(b'filename="')[1].split(b'"')[0].decode()
            StubDoclingHandler.received_files.append(name)
        out = json.dumps({"document": {"md_content": "# from docling"}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def make_eml(path: Path, subject="שלום", date="Mon, 6 Jan 2020 10:00:00 +0200",
             body="גוף ההודעה"):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = "a@example.com"
    msg["To"] = "b@example.com"
    msg["Date"] = date
    msg.set_content(body)
    path.write_bytes(bytes(msg))


class VaultCase(unittest.TestCase):
    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), StubDoclingHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        StubDoclingHandler.received_files = []
        self.td = tempfile.TemporaryDirectory()
        self.vault = Path(self.td.name)
        (self.vault / "raw").mkdir()
        config = {
            "docling": {"url": f"http://127.0.0.1:{self.server.server_address[1]}",
                        "workers": 1, "timeout": 30, "retry_delay": 0.01},
            "pdf": {"split_threshold": 100, "chunk_pages": 50},
            "hebrew": {"dict_path": "data/hebrew_dict.json",
                       "ambiguity_margin": 2.0},
        }
        (self.vault / "convert_config.json").write_text(
            json.dumps(config), encoding="utf-8")

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.td.cleanup()

    def read_md(self, rel):
        return (self.vault / "raw_md" / rel).read_text(encoding="utf-8")


class TestEndToEnd(VaultCase):
    def test_txt_converted_mirrored_frontmatter_hebrew_fixed(self):
        (self.vault / "raw" / "sub").mkdir()
        (self.vault / "raw" / "sub" / "note.txt").write_text(
            "abc םולש", encoding="utf-8")
        report = run(self.vault)
        md = self.read_md("sub/note.md")
        self.assertIn("original_file: note.txt", md)
        self.assertIn("original_ext: .txt", md)
        self.assertIn("hebrew_fixed: true", md)
        self.assertIn("abc שלום", md)
        # file is brand new -> created omitted (<24h rule)
        self.assertNotIn("created:", md)
        entry = report["files"]["sub/note.txt"]
        self.assertEqual(entry["status"], "converted")
        self.assertEqual(entry["converter"], "markitdown")
        # report + dictionary persisted
        self.assertTrue((self.vault / "raw_md" / "conversion_report.json").exists())
        self.assertTrue((self.vault / "data" / "hebrew_dict.json").exists())

    def test_eml_header_block_and_date_from_header(self):
        make_eml(self.vault / "raw" / "mail.eml")
        report = run(self.vault)
        md = self.read_md("mail.md")
        self.assertIn("Subject: שלום", md)
        self.assertIn("From: a@example.com", md)
        self.assertIn("created: 2020-01-06", md)  # from Date header, not mtime
        self.assertIn("title: שלום", md)
        self.assertEqual(report["files"]["mail.eml"]["converter"], "email")

    def test_docx_routed_to_docling_title_from_metadata(self):
        import docx
        d = docx.Document()
        d.core_properties.title = "My Docx Title"
        d.add_paragraph("body")
        d.save(self.vault / "raw" / "doc.docx")
        report = run(self.vault)
        md = self.read_md("doc.md")
        self.assertIn("# from docling", md)
        self.assertIn("title: My Docx Title", md)
        self.assertEqual(report["files"]["doc.docx"]["converter"], "docling")
        self.assertEqual(StubDoclingHandler.received_files, ["doc.docx"])

    def test_unsupported_extensions_skipped(self):
        (self.vault / "raw" / "data.vsd").write_bytes(b"fake vsd")
        report = run(self.vault)
        self.assertEqual(report["files"]["data.vsd"]["status"], "skipped")
        self.assertFalse((self.vault / "raw_md" / "data.md").exists())

    def test_rerun_skips_up_to_date_outputs(self):
        (self.vault / "raw" / "note.txt").write_text("hello", encoding="utf-8")
        run(self.vault)
        report2 = run(self.vault)
        self.assertEqual(report2["files"]["note.txt"]["status"], "skipped")

    def test_force_reconverts(self):
        (self.vault / "raw" / "note.txt").write_text("hello", encoding="utf-8")
        run(self.vault)
        report2 = run(self.vault, force=True)
        self.assertEqual(report2["files"]["note.txt"]["status"], "converted")

    def test_docling_failure_recorded_not_retried_elsewhere(self):
        self.server.shutdown()
        self.server.server_close()  # close the socket: connections refuse fast
        self.thread.join()
        (self.vault / "raw" / "doc.docx").write_bytes(b"fake")
        report = run(self.vault)
        entry = report["files"]["doc.docx"]
        self.assertEqual(entry["status"], "failed")
        self.assertIn("error", entry)


class TestDedup(VaultCase):
    """Spec §13 + §15: dataset-wide content-hash dedup (3 recommended cases)."""

    def test_identical_content_different_names(self):
        (self.vault / "raw" / "a.txt").write_text("same content", encoding="utf-8")
        (self.vault / "raw" / "b.txt").write_text("same content", encoding="utf-8")
        report = run(self.vault)
        # One converted, one duplicate (no raw_md output for duplicate)
        statuses = {k: v["status"] for k, v in report["files"].items() if k in ("a.txt", "b.txt")}
        self.assertEqual(sorted(statuses.values()), ["converted", "duplicate"])
        dup = [k for k, v in statuses.items() if v == "duplicate"][0]
        canon = [k for k, v in statuses.items() if v == "converted"][0]
        self.assertEqual(report["files"][dup]["duplicate_of"], canon)
        self.assertIn("hash", report["files"][dup])
        self.assertFalse((self.vault / "raw_md" / Path(dup).with_suffix(".md")).exists())
        self.assertTrue((self.vault / "raw_md" / Path(canon).with_suffix(".md")).exists())

    def test_sorted_winner_deterministic(self):
        # Lexicographically first wins even if created second
        (self.vault / "raw" / "z.txt").write_text("dup", encoding="utf-8")
        (self.vault / "raw" / "a.txt").write_text("dup", encoding="utf-8")
        report = run(self.vault)
        self.assertEqual(report["files"]["z.txt"]["status"], "duplicate")
        self.assertEqual(report["files"]["z.txt"]["duplicate_of"], "a.txt")
        self.assertEqual(report["files"]["a.txt"]["status"], "converted")

    def test_force_does_not_override_dedup(self):
        (self.vault / "raw" / "a.txt").write_text("again same", encoding="utf-8")
        (self.vault / "raw" / "b.txt").write_text("again same", encoding="utf-8")
        run(self.vault)
        report2 = run(self.vault, force=True)
        # Duplicate stays suppressed even with --force (spec §13)
        self.assertEqual(report2["files"]["b.txt"]["status"], "duplicate")
        self.assertEqual(report2["files"]["b.txt"]["duplicate_of"], "a.txt")
        self.assertFalse((self.vault / "raw_md" / "b.md").exists())


class TestUnits(unittest.TestCase):
    def test_load_config_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = load_config(Path(td))
        self.assertEqual(cfg["pdf"]["chunk_pages"], 50)
        self.assertEqual(cfg["pdf"]["split_threshold"], 100)
        self.assertEqual(cfg["docling"]["workers"], 1)

    def test_load_config_merges_vault_file(self):
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            (vault / "convert_config.json").write_text(
                json.dumps({"docling": {"url": "http://10.0.0.5:9999"}}),
                encoding="utf-8")
            cfg = load_config(vault)
        self.assertEqual(cfg["docling"]["url"], "http://10.0.0.5:9999")
        self.assertEqual(cfg["pdf"]["chunk_pages"], 50)  # default preserved

    def test_should_skip(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "a.txt"
            dst = Path(td) / "a.md"
            src.write_text("x")
            self.assertFalse(should_skip(src, dst, force=False))  # dst missing
            dst.write_text("y")
            self.assertTrue(should_skip(src, dst, force=False))
            self.assertFalse(should_skip(src, dst, force=True))

    def test_frontmatter_24h_rule(self):
        recent = datetime.now(timezone.utc) - timedelta(hours=1)
        old = datetime.now(timezone.utc) - timedelta(days=2)
        fm_recent = build_frontmatter("t", recent, "f.txt", ".txt", False)
        fm_old = build_frontmatter("t", old, "f.txt", ".txt", False)
        self.assertNotIn("created:", fm_recent)
        self.assertIn("created:", fm_old)

    def test_frontmatter_hebrew_fixed_flag(self):
        fm = build_frontmatter("t", None, "f.txt", ".txt", True)
        self.assertIn("hebrew_fixed: true", fm)
        self.assertIn("original_file: f.txt", fm)


if __name__ == "__main__":
    unittest.main()
