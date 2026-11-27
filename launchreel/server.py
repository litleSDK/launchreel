"""LaunchReel Web Studio & Agen-MCP-Ready Render API Server.

Zero external web framework dependencies (uses Python stdlib ThreadingHTTPServer).
Supports HTTP Byte-Range streaming for smooth HTML5 <video> playback & seeking.

Run:
    .\.venv\Scripts\python.exe -m launchreel.server
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .chain_data import FEATURED_TOKENS, _ensure_launchreel_brand_icon, build_default_script, fetch_token_telemetry
from .video_engine import RENDERS_DIR, THEMES, VOICE_PRESETS, render_launch_reel

WEB_DIR = Path(__file__).resolve().parent / "web"

_JOBS_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}


def _list_gallery() -> list[dict[str, Any]]:
    RENDERS_DIR.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    files = sorted(
        RENDERS_DIR.glob("*.mp4"),
        key=lambda x: (1 if x.name.startswith("REEL_") else 0, x.stat().st_mtime),
        reverse=True,
    )
    for p in files[:16]:
        contact = p.with_name(f"{p.stem}_contact.jpg")
        items.append(
            {
                "name": p.name,
                "url": f"/renders/{p.name}",
                "contact_url": f"/renders/{contact.name}" if contact.exists() else None,
                "size_mb": round(p.stat().st_size / (1024 * 1024), 2),
                "mtime": int(p.stat().st_mtime),
            }
        )
    return items


def _run_render_job(job_id: str, payload: dict[str, Any]) -> None:
    def _update(pct: int, msg: str, status: str = "running", **extra: Any) -> None:
        with _JOBS_LOCK:
            _JOBS[job_id].update({"pct": pct, "message": msg, "status": status, "updated_at": time.time(), **extra})

    try:
        address = str(payload.get("address") or "").strip()
        _update(5, f"Fetching live on-chain telemetry for {address[:14]}...")
        telemetry = fetch_token_telemetry(address)
        result = render_launch_reel(
            telemetry,
            kicker=payload.get("kicker"),
            hook_headline=payload.get("hook_headline"),
            cta_text=payload.get("cta_text"),
            beats=payload.get("beats"),
            theme_name=str(payload.get("theme") or "robinhood"),
            voice_preset=str(payload.get("voice") or "beat_dark"),
            aspect=str(payload.get("aspect") or "9:16"),
            out_dir=RENDERS_DIR,
            progress_cb=lambda pct, msg: _update(pct, msg),
        )
        _update(
            100,
            f"Rendered {result['mp4_name']} in {result['elapsed_s']}s",
            status="done",
            result={
                **result,
                "video_url": f"/renders/{result['mp4_name']}",
                "contact_url": f"/renders/{result['contact_sheet_name']}",
                "telemetry": telemetry.to_dict(),
            },
        )
    except Exception as exc:
        _update(0, f"Render failed: {exc}", status="error", error=str(exc))


class LaunchReelHandler(BaseHTTPRequestHandler):
    server_version = "LaunchReel/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Concise request logging
        print(f"[LaunchReel] {self.address_string()} - {fmt % args}", flush=True)

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        ctype, _ = mimetypes.guess_type(str(path))
        ctype = ctype or "application/octet-stream"
        file_size = path.stat().st_size
        range_hdr = self.headers.get("Range")

        if range_hdr and range_hdr.startswith("bytes="):
            m = re.match(r"bytes=(\d*)-(\d*)", range_hdr)
            if m:
                start = int(m.group(1)) if m.group(1) else 0
                end = int(m.group(2)) if m.group(2) else file_size - 1
                end = min(end, file_size - 1)
                length = max(0, end - start + 1)
                self.send_response(HTTPStatus.PARTIAL_CONTENT)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Length", str(length))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                with open(path, "rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(file_size))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self._send_file(WEB_DIR / "index.html")
            return

        if path == "/api/health":
            self._send_json({"ok": True, "service": "LaunchReel", "version": "0.1.0"})
            return

        if path == "/api/brand-icon":
            self._send_file(Path(_ensure_launchreel_brand_icon()))
            return

        if path == "/api/featured":
            self._send_json(
                {
                    "featured": FEATURED_TOKENS,
                    "themes": list(THEMES.keys()),
                    "voices": VOICE_PRESETS,
                    "gallery": _list_gallery(),
                }
            )
            return

        if path == "/api/token":
            qs = parse_qs(parsed.query)
            addr = (qs.get("address") or [""])[0].strip()
            if not addr:
                self._send_json({"error": "Missing address query param"}, status=400)
                return
            try:
                tel = fetch_token_telemetry(addr)
                script = build_default_script(tel)
                self._send_json({"telemetry": tel.to_dict(), "script": script})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=404)
            return

        if path.startswith("/api/jobs/"):
            job_id = path.split("/api/jobs/", 1)[1].strip("/")
            with _JOBS_LOCK:
                job = _JOBS.get(job_id)
            if not job:
                self._send_json({"error": "Job not found"}, status=404)
                return
            self._send_json(job)
            return

        if path.startswith("/renders/"):
            fname = Path(path.split("/renders/", 1)[1]).name
            self._send_file(RENDERS_DIR / fname)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path not in ("/api/render", "/api/v1/generate"):
            self._send_json({"error": "Endpoint not found"}, status=404)
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b"{}"
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            self._send_json({"error": f"Invalid JSON: {exc}"}, status=400)
            return

        if not str(payload.get("address") or "").strip():
            self._send_json({"error": "Field 'address' is required"}, status=400)
            return

        job_id = uuid.uuid4().hex[:12]
        with _JOBS_LOCK:
            _JOBS[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "pct": 2,
                "message": "Queued for rendering...",
                "created_at": time.time(),
            }

        th = threading.Thread(target=_run_render_job, args=(job_id, payload), daemon=True)
        th.start()
        self._send_json({"job_id": job_id, "status": "queued", "poll_url": f"/api/jobs/{job_id}"}, status=202)


def run_server(host: str = "127.0.0.1", port: int = 8420) -> None:
    RENDERS_DIR.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer((host, port), LaunchReelHandler)
    print(f"\n  LaunchReel Studio running at http://{host}:{port}\n", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down LaunchReel Studio...", flush=True)
        srv.shutdown()


if __name__ == "__main__":
    p = int(os.environ.get("PORT", "8420"))
    run_server(port=p)
