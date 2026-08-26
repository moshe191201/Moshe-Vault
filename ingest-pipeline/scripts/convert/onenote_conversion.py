#!/usr/bin/env python3
"""
Offline OneNote -> Markdown via OfficeIMO.OneNote (headless, no OneNote/Word/COM).

Handles local files only:
  .one      -> single section (OneNoteSectionReader)
  .onetoc2  -> notebook hierarchy (OneNoteNotebookReader)
  .onepkg   -> Cabinet archive (OneNotePackageReader)
  directory containing .onetoc2 -> notebook

Uses OfficeIMO.OneNote + OfficeIMO.OneNote.Markdown (pure managed parser,
bounded, no cloud). The .NET helper `scripts/OneNoteOffline` extracts each
page to Markdown + raw binary payloads (images, embedded files, media).
This Python wrapper then:

  - calls the helper (dotnet) to get page Markdown + asset files
  - routes every extracted asset through the vault's normal converters:
      .pdf/.docx/.pptx/.xlsx/.xls -> docling
      .html/.htm/.mht            -> pandoc
      .txt/.csv                   -> markitdown
      images                     -> kept as-is (optional OCR via docling)
    Attachments are converted to Markdown sidecars and linked from the parent page.

Requires:
  .NET SDK 8.0+ (build once; published output is self-contained)
  OfficeIMO.OneNote 3.2.2 (restored via `dotnet build`)
  pandoc (for html), docling-serve (for pdf/docx/pptx/xlsx) - same as convert_to_md

Usage (standalone):
  python scripts/onenote_conversion.py <input .one|.onepkg|.onetoc2|dir> <out_dir>

Usage (via convert_to_md):
  convert_to_md dispatches .one/.onepkg/.onetoc2 here automatically.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .convert_to_md import VaultCfg
else:
    VaultCfg = dict  # type: ignore

ONENOTE_EXTS = {".one", ".onepkg", ".onetoc2"}
# Also treat a directory that contains .onetoc2 as a OneNote notebook
DOTNET_PROJECT = Path(__file__).parent / "OneNoteOffline" / "OneNoteOffline.csproj"
DOTNET_DLL = Path(__file__).parent / "OneNoteOffline" / "bin" / "Release" / "net8.0" / "OneNoteOffline.dll"

# Routing for extracted OneNote attachments (intentionally broader than raw vault ROUTING:
# raw vault per spec skips xlsx/csv; attachments from OneNote may be xlsx/xls/mhtml and should still be converted)
ATTACHMENT_ROUTING: dict[str, str] = {
    ".pdf": "docling",
    ".docx": "docling",
    ".doc": "docling",
    ".pptx": "docling",
    ".ppt": "docling",
    ".xlsx": "docling",
    ".xls": "docling",
    ".html": "pandoc",
    ".htm": "pandoc",
    ".mht": "pandoc",
    ".mhtml": "pandoc",
    ".txt": "markitdown",
    ".csv": "markitdown",
}


def _ensure_built() -> Path:
    """Build OneNoteOffline helper if needed; return dll path."""
    if DOTNET_DLL.exists():
        return DOTNET_DLL
    if not DOTNET_PROJECT.exists():
        raise RuntimeError(f"OneNoteOffline project not found: {DOTNET_PROJECT}")
    if shutil.which("dotnet") is None:
        raise RuntimeError(
            ".NET SDK 8.0+ required for OneNote conversion. "
            "Install via: winget install Microsoft.DotNet.SDK.8  "
            "or https://aka.ms/dotnet-download"
        )
    result = subprocess.run(
        ["dotnet", "build", "-c", "Release", str(DOTNET_PROJECT)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"dotnet build failed:\n{result.stdout}\n{result.stderr}")
    if not DOTNET_DLL.exists():
        raise RuntimeError(f"Build succeeded but dll not found: {DOTNET_DLL}")
    return DOTNET_DLL


def is_onenote_path(path: Path) -> bool:
    p = Path(path)
    if p.is_dir():
        # Directory is a OneNote notebook if it contains .onetoc2
        return any(p.glob("*.onetoc2"))
    return p.suffix.lower() in ONENOTE_EXTS


def run_offline_extractor(input_path: Path, out_dir: Path) -> list[dict]:
    """Call OneNoteOffline CLI; return parsed manifest.json entries."""
    dll = _ensure_built()
    out_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["dotnet", str(dll), str(input_path), str(out_dir)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"OneNoteOffline failed for {input_path.name}: {result.stderr or result.stdout}")
    manifest = out_dir / "manifest.json"
    if not manifest.exists():
        raise RuntimeError(f"OneNoteOffline produced no manifest for {input_path.name}")
    return json.loads(manifest.read_text(encoding="utf-8"))


def convert_onenote_file(
    src: Path,
    out_root: Path,
    raw_root: Path,
    client=None,
    cfg: VaultCfg | None = None,
) -> list[Path]:
    """
    Convert one OneNote artifact (file or notebook dir) to Markdown.

    Writes:
      out_root / <rel parent> / <section>/<page>.md  (one per page)
      out_root / <rel parent> / assets/<attachment>   (raw assets)
      out_root / <rel parent> / assets/<attachment>.md (converted sidecars, when convertible)

    Returns list of written .md paths (pages + converted attachments).
    """
    src = Path(src)
    cfg = cfg or {}
    # Dispatch helper for attachment conversion - lazy import to avoid cycle
    try:
        from . import convert_to_md as _cvt  # type: ignore
        _dispatch = _cvt.dispatch_convert
        _has_dispatch = True
    except ImportError:
        _has_dispatch = False
        _dispatch = None  # type: ignore

    with tempfile.TemporaryDirectory(prefix="onenote_offline_") as tmp:
        tmp_out = Path(tmp) / "extracted"
        entries = run_offline_extractor(src, tmp_out)

        written: list[Path] = []
        # Preserve relative parent for vault layout: raw/<rel>/file.onepkg -> raw_md/<rel>/...
        try:
            rel_parent = src.relative_to(raw_root).parent
        except ValueError:
            rel_parent = Path(".")

        for entry in entries:
            page_rel = Path(entry["file"])  # e.g. "Section/Page.md"
            dst = out_root / rel_parent / page_rel
            dst.parent.mkdir(parents=True, exist_ok=True)

            # Frontmatter - reuse convert_to_md helpers if available
            try:
                from . import convert_to_md as cvt
                from datetime import datetime, timezone

                created = None
                if entry.get("created"):
                    try:
                        created = datetime.fromisoformat(entry["created"].replace("Z", "+00:00"))
                    except ValueError:
                        pass
                fm = cvt.build_frontmatter(
                    entry.get("title") or page_rel.stem,
                    created,
                    entry.get("title") or src.name,
                    src.suffix.lower(),
                    False,
                )
                md_text = fm + "\n" + entry.get("markdown", "")
            except ImportError:
                md_text = entry.get("markdown", "")

            # Rewrite asset links from "assets/..." (tmp) to relative vault paths
            # Assets live at tmp_out/assets/ -> out_root/rel_parent/assets/
            assets_src_dir = tmp_out / "assets"
            assets_dst_dir = out_root / rel_parent / "assets"
            assets_dst_dir.mkdir(parents=True, exist_ok=True)

            # Copy raw assets and optionally convert them
            attachment_links: list[str] = []
            for asset in entry.get("assets", []) or []:
                asset_name = asset.get("fileName") or asset.get("payload", "").split("/")[-1]
                src_asset = assets_src_dir / asset_name
                if not src_asset.exists():
                    continue
                dst_asset = assets_dst_dir / asset_name
                # Deduplicate by copy if already exists with same content
                if not dst_asset.exists():
                    shutil.copy2(src_asset, dst_asset)

                ext = Path(asset_name).suffix.lower()
                kind = ATTACHMENT_ROUTING.get(ext)
                if kind and _has_dispatch and client is not None:
                    # Use DoclingClient.is_reachable() (Feature Envy fix) - quick probe, no 300s hang
                    try:
                        if not client.is_reachable(timeout=1.0):
                            raise RuntimeError("docling not reachable - keep raw")
                        sidecar_md = assets_dst_dir / f"{Path(asset_name).stem}.md"
                        if not sidecar_md.exists():
                            md_att, _, _, _ = _dispatch(src_asset, client, cfg, routing_ext=ext)
                            # Wrap attachment sidecar with frontmatter including attachment_of link to parent page
                            try:
                                from . import convert_to_md as _cvt2
                                # Try to apply hebrew fix if dictionary available via convert_to_md context
                                # but skip if not - keep converted text as-is with frontmatter
                                rel_page = dst.relative_to(out_root).as_posix() if dst else ""
                                fm_att = _cvt2.build_frontmatter(
                                    Path(asset_name).stem or "attachment",
                                    None,
                                    asset_name,
                                    ext,
                                    False,
                                    attachment_of=rel_page,
                                )
                                sidecar_md.write_text(fm_att + "\n" + md_att, encoding="utf-8")
                            except Exception:  # noqa: BLE001
                                sidecar_md.write_text(md_att, encoding="utf-8")
                        rel_sidecar = sidecar_md.relative_to(out_root).as_posix()
                        attachment_links.append(f"- [{asset.get('originalName') or asset_name}]({rel_sidecar}) (converted)")
                    except Exception as e:  # noqa: BLE001
                        rel_asset = dst_asset.relative_to(out_root).as_posix()
                        attachment_links.append(f"- [{asset.get('originalName') or asset_name}]({rel_asset}) (raw, conversion failed: {e})")
                else:
                    rel_asset = dst_asset.relative_to(out_root).as_posix()
                    if kind is None and ext not in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".svg", ".emf"}:
                        attachment_links.append(f"- [{asset.get('originalName') or asset_name}]({rel_asset})")
                    # Images are already linked inline via Markdown projection

            if attachment_links:
                md_text += "\n\n## Attachments\n" + "\n".join(attachment_links) + "\n"

            dst.write_text(md_text, encoding="utf-8")
            written.append(dst)

        # Also copy any remaining assets not referenced per-page (e.g. shared)
        assets_src_dir = tmp_out / "assets"
        if assets_src_dir.exists():
            for f in assets_src_dir.iterdir():
                if f.is_file():
                    dst = out_root / rel_parent / "assets" / f.name
                    if not dst.exists():
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(f, dst)

        return written


def main(argv: list[str] | None = None) -> None:
    ap = __import__("argparse").ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", type=Path, help=".one / .onetoc2 / .onepkg file or notebook directory")
    ap.add_argument("out_dir", type=Path, help="output directory")
    args = ap.parse_args(argv)
    written = convert_onenote_file(args.input, args.out_dir, args.input.parent)
    print(f"Wrote {len(written)} markdown files to {args.out_dir}")
    for p in written:
        print(f"  {p}")


if __name__ == "__main__":
    main()
