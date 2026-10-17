"""Command line interface.

    lulujar extract archive.rar -o ~/out     # extract locally
    lulujar serve                            # start the browser UI
    lulujar info app.apk                     # inspect an archive
    lulujar doctor                           # show available engines
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

from . import __version__, apk, config, engine, fsapi, formats
from .jobs import JobManager


def _human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def cmd_extract(args: argparse.Namespace) -> int:
    archive = Path(args.archive).expanduser()
    if not archive.is_file():
        print(f"error: no such file: {archive}", file=sys.stderr)
        return 2

    kind = formats.detect(str(archive))
    if not formats.is_archive(kind):
        print(f"error: {archive.name} does not look like an archive", file=sys.stderr)
        return 2

    destination = (
        Path(args.output).expanduser()
        if args.output
        else config.output_dir() / archive.stem
    )
    destination.mkdir(parents=True, exist_ok=True)

    if args.list:
        return _print_entries(archive, kind, as_json=args.json)

    started = time.time()
    last_line = ""

    def progress(payload: dict) -> None:
        nonlocal last_line
        if not sys.stdout.isatty():
            return
        percent = payload.get("percent")
        current = payload.get("current")
        if percent is not None:
            line = f"  {percent:3d}%  {current or ''}"
        else:
            done = payload.get("bytes_done", 0)
            total = payload.get("bytes_total", 0)
            line = f"  {done} / {total} bytes  {current or ''}".replace("  ", " ")
        if line != last_line:
            print(f"\r{line[:120]}", end="", flush=True)
            last_line = line

    try:
        result = engine.extract(
            str(archive), str(destination), kind=kind, progress=progress
        )
    except engine.ExtractionError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130

    if sys.stdout.isatty():
        print("\r" + " " * 80 + "\r", end="")

    elapsed = time.time() - started
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"Extracted {archive.name} -> {result.destination}")
        print(
            f"  engine   : {result.engine}\n"
            f"  files    : {result.files}\n"
            f"  folders  : {result.dirs}\n"
            f"  size     : {_human(result.bytes_written)}\n"
            f"  time     : {elapsed:.1f}s"
        )
        for warning in result.warnings:
            print(f"  warning  : {warning}")

    if args.apk and kind == formats.APK:
        info = apk.inspect_apk(str(archive))
        print(json.dumps(info, indent=2, default=str))
    return 0


def _print_entries(archive: Path, kind: str, as_json: bool = False) -> int:
    try:
        entries = engine.list_entries(str(archive), kind)
    except engine.ExtractionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps([e.to_dict() for e in entries], indent=2))
        return 0
    total = sum(e.size for e in entries)
    print(f"{archive.name}: {len(entries)} entries, {_human(total)} uncompressed")
    for entry in entries[:200]:
        marker = "/" if entry.is_dir else " "
        print(f" {marker} {_human(entry.size):>10}  {entry.path}")
    if len(entries) > 200:
        print(f" ... and {len(entries) - 200} more")
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    archive = Path(args.archive).expanduser()
    if not archive.is_file():
        print(f"error: no such file: {archive}", file=sys.stderr)
        return 2
    kind = formats.detect(str(archive))
    if kind == formats.APK:
        print(json.dumps(apk.inspect_apk(str(archive)), indent=2, default=str))
        return 0
    return _print_entries(archive, kind, as_json=args.json)


def cmd_doctor(args: argparse.Namespace) -> int:
    config.ensure_dirs()
    print(f"LuluJar {__version__}")
    print(f"  home       : {config.home_dir()}")
    print(f"  output dir : {config.output_dir()}")
    print(f"  uploads    : {config.uploads_dir()}")
    print("")

    engines = engine.discover_engines(refresh=True)
    if not engines:
        print("no engines found")
    for item in engines:
        version = f" {item.version}" if item.version else ""
        print(f"  [ok] {item.name:<12}{version:<10} {item.location}")
        print(f"        handles: {', '.join(item.kinds)}")

    for kind in (formats.ZIP, formats.APK, formats.RAR, formats.SEVENZ, formats.TAR):
        chosen = engine.best_engine(kind)
        label = formats.describe(kind)
        if chosen:
            print(f"  {label:<16} -> {chosen.name}")
        else:
            print(f"  {label:<16} -> NONE (install 7-Zip, unrar, unar or pip install unrar-cffi)")

    missing = [k for k in (formats.RAR, formats.SEVENZ) if not engine.best_engine(k)]
    if missing:
        print("")
        print("Some formats need a helper. Easiest options:")
        print("  pip install unrar-cffi      # in-process RAR decoder, no binary")
        print("  pip install py7zr            # pure Python 7z reader")
        print("  lulujar install-tools        # downloads a portable 7-Zip")
    return 0


def cmd_browse(args: argparse.Namespace) -> int:
    target = Path(args.path).expanduser()
    if not target.exists():
        print(f"error: no such path: {target}", file=sys.stderr)
        return 2

    def walk(directory: Path, prefix: str = "") -> None:
        try:
            children = sorted(
                directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
            )
        except OSError:
            return
        for child in children:
            if child.name.startswith("."):
                continue
            if child.is_dir():
                print(f"{prefix}{child.name}/")
                walk(child, prefix + "  ")
            else:
                print(f"{prefix}{child.name}  ({_human(child.stat().st_size)})")

    print(str(target))
    walk(target)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    config.ensure_dirs()
    settings = config.load_settings()
    if args.root:
        settings.browse_roots = list(args.root)
    config.save_settings(settings)

    roots = fsapi.default_roots(settings.browse_roots)
    jobs = JobManager()
    jobs.load_previous()

    host = args.host
    require_token = host not in ("127.0.0.1", "localhost", "::1")
    token = args.token or (os.urandom(8).hex() if require_token else "")
    if args.no_auth:
        require_token = False
        token = ""

    from .server import LuluJarHandler, LuluJarServer

    try:
        httpd = LuluJarServer(
            (host, args.port), LuluJarHandler, settings, roots, jobs, token, require_token
        )
    except OSError as exc:
        print(f"error: cannot bind {host}:{args.port} - {exc}", file=sys.stderr)
        return 1

    actual_port = httpd.server_address[1]
    shown_host = "localhost" if host in ("0.0.0.0", "127.0.0.1", "::1") else host
    url = f"http://{shown_host}:{actual_port}/"
    if token:
        url += f"?token={token}"

    print("")
    print(f"  LuluJar {__version__} is running")
    print(f"  ------------------------------------------------")
    print(f"  Open in your browser : {url}")
    if require_token:
        print(f"  Access token         : {token}")
        print(f"  (required for connections from other machines)")
    elif host not in ("127.0.0.1", "localhost", "::1"):
        print(f"  WARNING: --no-auth is on, anyone who can reach this port")
        print(f"           can browse and extract files on this machine.")
    print(f"  Listening on         : {host}:{actual_port}")
    print(f"  Output directory     : {config.output_dir()}")
    print(f"  Engines              : {', '.join(e.name for e in engine.discover_engines())}")
    print("")
    print("  Press Ctrl+C to stop.")
    print("")

    if args.open:
        import webbrowser

        threading_open(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        httpd.server_close()
    return 0


def threading_open(url: str) -> None:
    import threading
    import webbrowser

    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()


def cmd_install_tools(args: argparse.Namespace) -> int:
    """Fetch a portable 7-Zip so RAR/7z extraction works out of the box."""
    import urllib.error
    import urllib.request

    target_dir = config.bin_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    system = os.uname().sysname if hasattr(os, "uname") else ""
    machine = os.uname().machine if hasattr(os, "uname") else ""
    if system == "Linux" and machine in ("x86_64", "amd64"):
        asset, member = "7z<ver>-linux-x64.tar.xz", "7zz"
    elif system == "Linux" and machine in ("aarch64", "arm64"):
        asset, member = "7z<ver>-linux-arm64.tar.xz", "7zz"
    elif system == "Darwin":
        asset, member = "7z<ver>-mac.tar.xz", "7zz"
    elif os.name == "nt":
        asset, member = "7z<ver>-x64.exe", "7zz.exe"
    else:
        print("unsupported platform - install 7-Zip manually", file=sys.stderr)
        return 1

    try:
        with urllib.request.urlopen(
            "https://api.github.com/repos/ip7z/7zip/releases/latest", timeout=30
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        tag = payload["tag_name"]
        version = tag.replace(".", "")
        url = ""
        for item in payload.get("assets", []):
            if item["name"] == asset.replace("<ver>", version):
                url = item["browser_download_url"]
                break
        if not url:
            print(f"no matching download for this platform in {tag}", file=sys.stderr)
            return 1
        print(f"downloading {url}")
        archive = target_dir / "7z.tar.xz" if not url.endswith(".exe") else target_dir / "7z.exe"
        urllib.request.urlretrieve(url, archive)
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        print(f"download failed: {exc}", file=sys.stderr)
        print("", file=sys.stderr)
        print("No problem - LuluJar already ships a WebAssembly RAR engine.", file=sys.stderr)
        print("It only needs Node.js on the PATH. As an alternative you can", file=sys.stderr)
        print("install a native decoder with:", file=sys.stderr)
        print("", file=sys.stderr)
        print("    python3 -m venv .venv && .venv/bin/pip install unrar-cffi", file=sys.stderr)
        return 0

    if archive.suffix == ".xz":
        try:
            subprocess.run(
                ["tar", "xf", str(archive), "-C", str(target_dir)], check=True, timeout=300
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            print(f"could not unpack the download: {exc}", file=sys.stderr)
            return 1
    else:
        shutil.copy(archive, target_dir / "7zz.exe")

    binary = target_dir / member
    if binary.exists():
        binary.chmod(0o755)
        print(f"installed: {binary}")
        print("add it to your PATH or run with PATH=$PATH:" + str(target_dir))
    return 0


def _pip_install(packages: List[str]) -> int:
    """Install optional Python packages, tolerating an externally managed env."""
    command = [sys.executable, "-m", "pip", "install", "--user", *packages]
    try:
        code = subprocess.call(command)
    except OSError as exc:
        print(f"pip failed: {exc}", file=sys.stderr)
        return 1
    if code != 0:
        print("", file=sys.stderr)
        print("pip refused the install (externally managed environment).", file=sys.stderr)
        print("Use a virtual environment instead:", file=sys.stderr)
        print("    python3 -m venv .venv && .venv/bin/pip install " + " ".join(packages))
    return code


def cmd_version(args: argparse.Namespace) -> int:
    print(__version__)
    return 0


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lulujar",
        description="Universal archive extraction tool for RAR, RAR5, ZIP, APK, 7z and TAR.",
    )
    parser.add_argument("--version", action="version", version=f"LuluJar {__version__}")
    sub = parser.add_subparsers(dest="command")

    extract = sub.add_parser("extract", help="extract an archive")
    extract.add_argument("archive")
    extract.add_argument("-o", "--output", help="destination directory")
    extract.add_argument("--list", action="store_true", help="list entries only")
    extract.add_argument("--apk", action="store_true", help="also print APK details")
    extract.add_argument("--json", action="store_true", help="machine readable output")
    extract.set_defaults(func=cmd_extract)

    info = sub.add_parser("info", help="inspect an archive")
    info.add_argument("archive")
    info.add_argument("--json", action="store_true")
    info.set_defaults(func=cmd_info)

    serve = sub.add_parser("serve", help="run the browser interface")
    serve.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8000, help="port (default 8000)")
    serve.add_argument("--root", action="append", help="extra browsable directory")
    serve.add_argument("--token", help="access token for remote use")
    serve.add_argument(
        "--no-auth",
        action="store_true",
        help="do not require a token for remote connections (trusted networks only)",
    )
    serve.add_argument("--open", action="store_true", help="open a browser window")
    serve.set_defaults(func=cmd_serve)

    browse = sub.add_parser("browse", help="print a directory tree")
    browse.add_argument("path")
    browse.set_defaults(func=cmd_browse)

    doctor = sub.add_parser("doctor", help="show the available extraction engines")
    doctor.set_defaults(func=cmd_doctor)

    install = sub.add_parser("install-tools", help="download a portable 7-Zip")
    install.set_defaults(func=cmd_install_tools)

    version = sub.add_parser("version", help="print the version")
    version.set_defaults(func=cmd_version)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)
