"""The LuluJar web server.

A dependency-free HTTP server (standard library only) that exposes:

* a resumable, streamed upload endpoint - nothing is held in memory, so the
  file size is limited only by the disk;
* an extraction job API with progress polling and cancellation;
* a browser for the extraction output and for the server's filesystem, used to
  pick the extraction destination;
* downloads and previews of extracted files.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__, apk, config, engine, fsapi, formats
from .jobs import JobManager

WEB_ROOT = Path(__file__).parent / "web"

UPLOAD_CHUNK = 1024 * 1024  # 1 MiB blocks read from the socket


class UploadSession:
    """A resumable upload that is streamed straight to disk."""

    def __init__(self, upload_id: str, name: str, path: Path):
        self.id = upload_id
        self.name = name
        self.path = path
        self.received = 0
        self.created = time.time()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "path": str(self.path),
            "received": self.received,
        }


class LuluJarHandler(BaseHTTPRequestHandler):
    server_version = f"LuluJar/{__version__}"
    protocol_version = "HTTP/1.1"

    # injected by LuluJarServer
    settings: config.Settings = config.Settings()
    roots: list = []
    jobs: JobManager = JobManager()
    uploads: Dict[str, UploadSession] = {}
    upload_lock = threading.Lock()
    token: str = ""
    require_token: bool = False

    # -- logging ----------------------------------------------------------
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        if os.environ.get("LULUJAR_QUIET"):
            return
        super().log_message(fmt, *args)

    # -- helpers ----------------------------------------------------------
    def _is_local(self) -> bool:
        host = self.client_address[0] if self.client_address else ""
        return host in ("127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost")

    def _authorised(self, query: Dict[str, list]) -> bool:
        if not self.require_token or self._is_local():
            return True
        if secrets.compare_digest(query.get("token", [""])[0], self.token):
            return True
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "lulujar_token" and secrets.compare_digest(value, self.token):
                return True
        header = self.headers.get("X-LuluJar-Token", "")
        return bool(header) and secrets.compare_digest(header, self.token)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, message: str, status: int = 400) -> None:
        self._send_json({"error": message}, status=status)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _read_stream_to(self, handle, limit: int = 0) -> int:
        """Copy the request body to ``handle`` in blocks (no size limit)."""
        length = int(self.headers.get("Content-Length") or 0)
        written = 0
        if length > 0:
            remaining = length
            while remaining > 0:
                block = self.rfile.read(min(UPLOAD_CHUNK, remaining))
                if not block:
                    break
                handle.write(block)
                written += len(block)
                remaining -= len(block)
        else:
            # chunked transfer encoding
            while True:
                line = self.rfile.readline(64)
                if not line:
                    break
                try:
                    size = int(line.strip().split(b";")[0], 16)
                except ValueError:
                    break
                if size == 0:
                    self.rfile.readline(8)
                    break
                remaining = size
                while remaining > 0:
                    block = self.rfile.read(min(UPLOAD_CHUNK, remaining))
                    if not block:
                        break
                    handle.write(block)
                    written += len(block)
                    remaining -= len(block)
                self.rfile.readline(8)
        if limit and written > limit:
            raise ValueError("upload exceeds the configured limit")
        return written

    # -- routing ----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)

        if not self._authorised(query):
            self._error("invalid or missing access token", status=HTTPStatus.FORBIDDEN)
            return

        try:
            if path.startswith("/api/"):
                self._api(method, path[5:], query)
            elif method == "GET":
                self._static(path)
            else:
                self._error("not found", status=HTTPStatus.NOT_FOUND)
        except fsapi.PathNotAllowed as exc:
            self._error(str(exc), status=HTTPStatus.FORBIDDEN)
        except engine.ExtractionCancelled:
            self._error("cancelled", status=HTTPStatus.OK)
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001
            self._error(f"{exc.__class__.__name__}: {exc}", status=500)

    # -- static files -----------------------------------------------------
    def _static(self, path: str) -> None:
        if path in ("/", "/index.html", "/ui"):
            target = WEB_ROOT / "index.html"
        else:
            relative = path.lstrip("/")
            target = (WEB_ROOT / relative).resolve()
            try:
                target.relative_to(WEB_ROOT.resolve())
            except ValueError:
                self._error("not found", status=HTTPStatus.NOT_FOUND)
                return
        if not target.is_file():
            self._error("not found", status=HTTPStatus.NOT_FOUND)
            return
        content_type, _ = mimetypes.guess_type(str(target))
        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # -- api --------------------------------------------------------------
    def _api(self, method: str, route: str, query: Dict[str, list]) -> None:
        parts = [p for p in route.split("/") if p]

        if not parts:
            return self._send_json({"service": "LuluJar", "version": __version__})

        head = parts[0]

        if head == "health" and method == "GET":
            return self._api_health()
        if head == "engines" and method == "GET":
            return self._send_json(
                {"engines": [e.to_dict() for e in engine.discover_engines(refresh=True)]}
            )
        if head == "upload":
            return self._api_upload(method, parts[1:], query)
        if head == "jobs":
            return self._api_jobs(method, parts[1:], query)
        if head == "tree" and method == "GET":
            return self._api_tree(query)
        if head == "entries" and method == "GET":
            return self._api_entries(query)
        if head == "fs":
            return self._api_fs(method, parts[1:], query)
        if head == "download" and method == "GET":
            return self._api_download(query)
        if head == "preview" and method == "GET":
            return self._api_preview(query)
        if head == "apk" and method == "GET":
            return self._api_apk(query)
        if head == "delete" and method == "POST":
            return self._api_delete()

        return self._error("unknown endpoint", status=HTTPStatus.NOT_FOUND)

    def _api_health(self) -> None:
        self._send_json(
            {
                "service": "LuluJar",
                "version": __version__,
                "engines": [e.to_dict() for e in engine.discover_engines(refresh=True)],
                "roots": [str(r) for r in self.roots],
                "output_dir": str(config.output_dir()),
                "uploads_dir": str(config.uploads_dir()),
                "max_upload_bytes": self.settings.max_upload_bytes,
                "chunk_size": self.settings.chunk_size,
            }
        )

    # -- uploads ----------------------------------------------------------
    def _api_upload(self, method: str, parts, query) -> None:
        action = parts[0] if parts else ""

        if action == "start" and method == "POST":
            name = self._safe_name(query.get("name", ["upload.bin"])[0])
            upload_id = uuid.uuid4().hex[:16]
            target = config.uploads_dir() / f"{upload_id}_{name}"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
            with self.upload_lock:
                self.uploads[upload_id] = UploadSession(upload_id, name, target)
            return self._send_json(
                {
                    "id": upload_id,
                    "name": name,
                    "path": str(target),
                    "received": 0,
                    "offset": 0,
                }
            )

        if action == "chunk" and method in ("POST", "PUT"):
            upload_id = query.get("id", [""])[0]
            with self.upload_lock:
                session = self.uploads.get(upload_id)
            if session is None:
                return self._error("unknown upload", status=HTTPStatus.NOT_FOUND)
            offset = int(query.get("offset", [session.received])[0] or 0)
            try:
                with open(session.path, "r+b") as handle:
                    handle.seek(offset)
                    written = self._read_stream_to(handle, self.settings.max_upload_bytes)
            except ValueError as exc:
                return self._error(str(exc), status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            session.received = offset + written
            return self._send_json(
                {"id": upload_id, "received": session.received, "written": written}
            )

        if action == "complete" and method == "POST":
            upload_id = query.get("id", [""])[0]
            with self.upload_lock:
                session = self.uploads.pop(upload_id, None)
            if session is None:
                return self._error("unknown upload", status=HTTPStatus.NOT_FOUND)
            size = session.path.stat().st_size
            kind = formats.detect(str(session.path))
            return self._send_json(
                {
                    "id": upload_id,
                    "name": session.name,
                    "path": str(session.path),
                    "size": size,
                    "kind": kind,
                    "label": formats.describe(kind),
                }
            )

        if action == "single" and method == "POST":
            name = self._safe_name(query.get("name", ["upload.bin"])[0])
            upload_id = uuid.uuid4().hex[:16]
            target = config.uploads_dir() / f"{upload_id}_{name}"
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(target, "wb") as handle:
                    written = self._read_stream_to(handle, self.settings.max_upload_bytes)
            except ValueError as exc:
                target.unlink(missing_ok=True)
                return self._error(str(exc), status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            kind = formats.detect(str(target))
            return self._send_json(
                {
                    "id": upload_id,
                    "name": name,
                    "path": str(target),
                    "size": written,
                    "kind": kind,
                    "label": formats.describe(kind),
                }
            )

        return self._error("unknown upload endpoint", status=HTTPStatus.NOT_FOUND)

    @staticmethod
    def _safe_name(name: str) -> str:
        base = os.path.basename(str(name)).strip() or "upload.bin"
        base = re.sub(r"[^A-Za-z0-9._+-]", "_", base)
        return base[:180]

    # -- jobs -------------------------------------------------------------
    def _api_jobs(self, method: str, parts, query) -> None:
        if not parts:
            if method == "GET":
                return self._send_json(
                    {"jobs": [j.to_dict() for j in self.jobs.list(limit=30)]}
                )
            if method == "POST":
                return self._create_job()
            return self._error("method not allowed", status=405)

        job_id = parts[0]

        if len(parts) == 1 and method == "GET":
            job = self.jobs.get(job_id)
            if job is None:
                return self._error("unknown job", status=HTTPStatus.NOT_FOUND)
            return self._send_json(job.to_dict())

        if len(parts) == 2 and parts[1] == "cancel" and method == "POST":
            ok = self.jobs.cancel(job_id)
            return self._send_json({"cancelled": ok})

        if len(parts) == 1 and method == "DELETE":
            removed = self.jobs.remove(job_id)
            return self._send_json({"removed": removed})

        return self._error("unknown job endpoint", status=HTTPStatus.NOT_FOUND)

    def _create_job(self) -> None:
        payload = self._read_json_body()
        source_path = str(payload.get("source_path") or "")
        destination = str(payload.get("destination") or "")
        source_name = str(payload.get("source_name") or os.path.basename(source_path))
        kind = str(payload.get("kind") or "")

        if not source_path or not os.path.isfile(source_path):
            return self._error("source file not found", status=400)
        if not destination:
            destination = str(config.output_dir() / (source_name or "extracted"))

        try:
            dest = (
                fsapi.resolve(destination, self.roots)
                if os.path.isabs(destination)
                else Path(destination).expanduser().resolve()
            )
        except fsapi.PathNotAllowed:
            dest = Path(destination).expanduser().resolve()

        # A remote client may only write inside the browsable roots.
        if not self._is_local() and not fsapi.is_within(str(dest), self.roots):
            return self._error(
                "remote clients may only extract into the allowed locations",
                status=HTTPStatus.FORBIDDEN,
            )

        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return self._error(f"cannot create destination: {exc}", status=400)

        kind = kind or formats.detect(source_path)
        job = self.jobs.create(source_name, source_path, str(dest), kind)

        def worker(job, progress, cancel) -> None:
            result = engine.extract(
                job.source_path,
                job.destination,
                kind=job.kind,
                progress=progress,
                cancel=cancel,
            )
            job.result = result.to_dict()
            job.warnings = list(result.warnings)
            job.engine = result.engine

        self.jobs.run(job, worker)
        return self._send_json(job.to_dict(), status=202)

    # -- output browsing --------------------------------------------------
    def _api_tree(self, query) -> None:
        path = query.get("path", [""])[0] or str(config.output_dir())
        resolved = fsapi.resolve(path, self.roots)
        tree = engine.build_tree(str(resolved))
        return self._send_json(
            {
                "path": str(resolved),
                "tree": tree,
                "size": fsapi.directory_size(str(resolved)),
            }
        )

    def _api_entries(self, query) -> None:
        path = query.get("path", [""])[0]
        resolved = fsapi.resolve(path, self.roots)
        kind = formats.detect(str(resolved))
        entries = engine.list_entries(str(resolved), kind)
        total = sum(e.size for e in entries)
        return self._send_json(
            {
                "path": str(resolved),
                "kind": kind,
                "label": formats.describe(kind),
                "count": len(entries),
                "total_size": total,
                "entries": [e.to_dict() for e in entries[:5000]],
            }
        )

    # -- filesystem browsing ---------------------------------------------
    def _api_fs(self, method: str, parts, query) -> None:
        action = parts[0] if parts else "list"

        if action == "roots" and method == "GET":
            return self._send_json({"roots": [str(r) for r in self.roots]})

        if action == "list" and method == "GET":
            path = query.get("path", [""])[0]
            if not path:
                return self._send_json(
                    {
                        "path": "",
                        "entries": [
                            {"name": r.name or str(r), "path": str(r), "is_dir": True, "size": 0, "mtime": 0}
                            for r in self.roots
                        ],
                        "breadcrumbs": [],
                    }
                )
            resolved = fsapi.resolve(path, self.roots)
            entries = fsapi.list_directory(str(resolved), self.roots)
            return self._send_json(
                {
                    "path": str(resolved),
                    "entries": [e.to_dict() for e in entries],
                    "breadcrumbs": fsapi.breadcrumbs(str(resolved), self.roots),
                }
            )

        if action == "mkdir" and method == "POST":
            payload = self._read_json_body()
            path = str(payload.get("path") or "")
            if not path:
                return self._error("path is required")
            resolved = fsapi.resolve(path, self.roots)
            try:
                resolved.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return self._error(f"cannot create directory: {exc}")
            return self._send_json({"path": str(resolved)})

        return self._error("unknown filesystem endpoint", status=HTTPStatus.NOT_FOUND)

    # -- file access ------------------------------------------------------
    def _api_download(self, query) -> None:
        path = query.get("path", [""])[0]
        if not path:
            return self._error("path is required")
        resolved = fsapi.resolve(path, self.roots)
        if not resolved.is_file():
            return self._error("not a file", status=404)
        self._serve_file(resolved)

    def _serve_file(self, target: Path) -> None:
        size = target.stat().st_size
        start = 0
        end = size - 1
        partial = False

        range_header = self.headers.get("Range", "")
        match = re.match(r"bytes=(\d*)-(\d*)", range_header or "")
        if match and size:
            partial = True
            if match.group(1):
                start = int(match.group(1))
                if match.group(2):
                    end = min(int(match.group(2)), size - 1)
            elif match.group(2):
                start = max(0, size - int(match.group(2)))
            if start >= size:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            end = min(end, size - 1)

        length = max(0, end - start + 1) if size else 0
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header(
            "Content-Disposition", f'attachment; filename="{target.name}"'
        )
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(target, "rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                block = handle.read(min(256 * 1024, remaining))
                if not block:
                    break
                try:
                    self.wfile.write(block)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(block)

    def _api_preview(self, query) -> None:
        path = query.get("path", [""])[0]
        if not path:
            return self._error("path is required")
        data = fsapi.read_preview(path, self.roots)
        return self._send_json(data)

    def _api_apk(self, query) -> None:
        path = query.get("path", [""])[0]
        if not path:
            return self._error("path is required")
        resolved = fsapi.resolve(path, self.roots)
        return self._send_json(apk.inspect_apk(str(resolved)))

    def _api_delete(self) -> None:
        payload = self._read_json_body()
        path = str(payload.get("path") or "")
        if not path:
            return self._error("path is required")
        try:
            freed = fsapi.remove_tree(path, self.roots)
        except FileNotFoundError:
            return self._error("not found", status=404)
        return self._send_json({"deleted": True, "freed": freed})


class LuluJarServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, settings, roots, jobs, token, require_token):
        self.settings = settings
        self.roots = roots
        self.jobs = jobs
        self.token = token
        self.require_token = require_token
        handler.settings = settings
        handler.roots = roots
        handler.jobs = jobs
        handler.token = token
        handler.require_token = require_token
        super().__init__(address, handler)

    def server_bind(self) -> None:
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        super().server_bind()
