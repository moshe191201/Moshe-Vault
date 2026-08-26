#!/usr/bin/env python3
"""VSDX -> Markdown (headless, text-only).

Converts .vsdx (Visio) to LLM-readable markdown without rendering images.
.vsdx is a ZIP of XML - we parse shapes, text, and connectors via the
``vsdx`` Python library (https://github.com/dave-howard/vsdx) and emit:

  - per-page shape inventory (ID, text, master/type)
  - connector edge list (resolved to shape text)
  - Mermaid flowchart (``flowchart TD``) for topology

Requires:  pip install vsdx
Only .vsdx is supported; legacy binary .vsd is intentionally out of scope
and will be reported as unsupported (per spec).

This is a text-only conversion: an LLM without vision capabilities can
understand the diagram from the markdown alone - no image export or
human clicking needed.
"""
from __future__ import annotations

from pathlib import Path

import vsdx  # required: pip install vsdx - headless text-only VSDX parser


def _collect_shapes(page) -> list:
    """Flatten top-level + grouped shapes (recursive)."""
    out: list = []
    stack = list(getattr(page, "child_shapes", []) or [])
    while stack:
        shape = stack.pop(0)
        out.append(shape)
        try:
            children = getattr(shape, "child_shapes", None)
            if children:
                # prepend children so they appear near parent
                stack = list(children) + stack
        except Exception:
            pass
    return out


def _shape_id(shape) -> str:
    try:
        return str(shape.ID)
    except Exception:
        try:
            return str(shape.xml.attrib.get("ID", "?"))
        except Exception:
            return "?"


def _shape_text(shape) -> str:
    try:
        t = shape.text
        if t is None:
            return ""
        return str(t).strip()
    except Exception:
        return ""


def _master_name(shape) -> str:
    try:
        mp = getattr(shape, "master_page", None)
        if mp is not None and getattr(mp, "name", None):
            return str(mp.name)
    except Exception:
        pass
    try:
        ms = getattr(shape, "master_shape", None)
        if ms is not None and getattr(ms, "text", None):
            # fallback: master shape name
            return str(getattr(ms, "text", "") or "")
    except Exception:
        pass
    return ""


def _sanitize_mermaid(text: str, max_len: int = 60) -> str:
    t = text.replace('"', "'").replace("\n", " ").replace("\r", " ").strip()
    if len(t) > max_len:
        t = t[: max_len - 1] + "…"
    # Mermaid breaks on []{}| etc inside label - escape brackets
    t = t.replace("[", "(").replace("]", ")").replace("{", "(").replace("}", ")")
    t = t.replace("|", "/")
    return t


def convert_vsdx(path: Path) -> str:
    """Convert one .vsdx file to markdown text.

    Raises if the file cannot be parsed. Caller handles reporting
    (no cross-library retry).
    """
    from vsdx import VisioFile

    path = Path(path)
    if path.suffix.lower() != ".vsdx":
        raise ValueError(f"vsdx converter only handles .vsdx, got {path.suffix}")

    vis = VisioFile(str(path))
    try:
        lines: list[str] = []
        if not vis.pages:
            return f"# {path.name}\n\nNo pages found in diagram.\n"

        for page in vis.pages:
            lines.append(f"## Page: {page.name}\n")
            shapes = _collect_shapes(page)

            # Only shapes with user-visible text or a meaningful master are interesting
            visible = [s for s in shapes if _shape_text(s) or _master_name(s)]
            if not visible:
                lines.append("No labeled shapes on this page.\n")
                # Still emit empty mermaid block for consistency?
                continue

            # Map ID -> text for edge resolution
            id_to_text: dict[str, str] = {}
            id_to_shape: dict[str, object] = {}
            for s in visible:
                sid = _shape_id(s)
                id_to_text[sid] = _shape_text(s) or _master_name(s) or f"Shape {sid}"
                id_to_shape[sid] = s

            # Shape inventory
            lines.append("| Shape ID | Text | Type |")
            lines.append("|---|---|---|")
            for s in visible:
                sid = _shape_id(s)
                txt = _shape_text(s).replace("|", "/").replace("\n", " ")[:120]
                master = _master_name(s).replace("|", "/")[:40]
                lines.append(f"| {sid} | {txt} | {master} |")
            lines.append("")

            # Connectors -> edges (group by connector shape)
            try:
                connects = page.get_connects()
            except Exception:
                connects = []

            # Group connects by connector id (FromSheet)
            by_connector: dict[str, list] = {}
            for c in connects:
                key = getattr(c, "from_id", None) or getattr(c, "connector_shape_id", None) or "?"
                by_connector.setdefault(str(key), []).append(c)

            edges: list[tuple[str, str, str]] = []  # (from_text, to_text, label)
            # Also track direct shape-to-shape via paired connects on same connector
            for conn_id, clist in by_connector.items():
                # Each connector should have 2 endpoints (BeginX, EndX)
                targets = [str(getattr(c, "to_id", "")) for c in clist if getattr(c, "to_id", None)]
                if len(targets) >= 2:
                    # Resolve connector label if any
                    conn_shape = id_to_shape.get(conn_id)
                    label = _shape_text(conn_shape) if conn_shape else ""
                    # Create edge between the two targets via connector
                    edges.append((targets[0], targets[1], label))
                elif len(targets) == 1 and conn_id in id_to_text:
                    # Fallback: connector itself is the edge source
                    pass

            if edges:
                lines.append("### Connections")
                for a, b, label in edges:
                    at = id_to_text.get(a, f"Shape {a}")
                    bt = id_to_text.get(b, f"Shape {b}")
                    # truncate for table
                    at_s = _sanitize_mermaid(at, 40)
                    bt_s = _sanitize_mermaid(bt, 40)
                    if label:
                        lab = _sanitize_mermaid(label, 30)
                        lines.append(f"- `{at_s}` --[{lab}]--> `{bt_s}`")
                    else:
                        lines.append(f"- `{at_s}` --> `{bt_s}`")
                lines.append("")

            # Mermaid
            lines.append("### Diagram (Mermaid)")
            lines.append("```mermaid")
            lines.append("flowchart TD")
            for s in visible:
                sid = _shape_id(s)
                txt = _shape_text(s) or _master_name(s)
                if txt:
                    safe = _sanitize_mermaid(txt)
                    lines.append(f'  n{sid}["{safe}"]')
            for a, b, label in edges:
                if label:
                    lab = _sanitize_mermaid(label, 30)
                    lines.append(f'  n{a} -- "{lab}" --> n{b}')
                else:
                    lines.append(f'  n{a} --> n{b}')
            if not edges:
                # No edges: still emit nodes so diagram is not empty
                pass
            lines.append("```\n")

        return "\n".join(lines).strip() + "\n"
    finally:
        try:
            vis.close_vsdx()
        except Exception:
            pass
