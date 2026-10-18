# 📦 LuluJar

**Universal archive extraction tool — RAR, RAR5, ZIP, Android APK, 7z and TAR — with no size limit.**
Runs as a local command line tool **and** as a browser app.

```
lulujar extract game.apk -o ~/unpacked      # local
lulujar serve                               # browser UI on http://localhost:8000
```

---

## 1. Get it

### Option A — download the release (no tools needed)

Open the [Releases page](https://github.com/rsv-op/LuluJar/releases) and download the
**Source code (tar.gz)** or **Source code (zip)** archive for `v1.0.0`
(the `Assets` section may also carry a ready-made `LuluJar-1.0.0.tar.gz`).

```bash
tar xzf LuluJar-1.0.0.tar.gz     # or unzip LuluJar-1.0.0.zip
cd LuluJar-1.0.0
```

Windows users: unzip, then double-click `run.bat` (or `run.bat serve`).

### Option B — clone the repository

```bash
git clone https://github.com/rsv-op/LuluJar.git
cd LuluJar
```

### Option C — install with pip

```bash
pip install lulujar          # then:  lulujar --help
```

### Option D — run it straight from the zip

Nothing to install, nothing to build:

```bash
unzip LuluJar-1.0.0.zip && cd LuluJar-1.0.0 && ./run.sh serve
```

> **Requirements:** Python 3.8+ (that is it for ZIP/APK/TAR).
> Node.js is optional but unlocks RAR v4/v5 out of the box — the WebAssembly
> libarchive engine is bundled, so there is nothing to install.

---

## 2. Run it

### In your browser (the fun way)

```bash
./run.sh serve            # macOS / Linux
run.bat serve             # Windows
```

Then open **<http://localhost:8000>**.

You get a drag-and-drop area, a live progress bar, a browsable output tree
with file previews and downloads, an archive content preview, and an Android
manifest inspector.

To use it from another device on the same network (phone, tablet, colleague's
laptop):

```bash
./run.sh serve --host 0.0.0.0
```

A random access token is printed and required for non-local connections.

### On the command line

```bash
./run.sh extract archive.rar -o ./output      # extract
./run.sh extract archive.rar --list            # show what is inside
./run.sh info app.apk                         # inspect an APK manifest
./run.sh browse ./output                      # print a directory tree
./run.sh doctor                               # which engines are available
```

Or, after `pip install`:

```bash
lulujar extract archive.rar -o ./output
lulujar serve --port 8080
```

### In Docker (nothing installed on the host)

```bash
docker build -t lulujar .
docker run --rm -p 8000:8000 -v "$PWD/output:/data" lulujar
```

---

## 3. What you get

| # | Requirement | How it is met |
|---|-------------|---------------|
| 1 | Extract `.rar` / `.zip` / `.apk` — **no size limit** | Uploads are streamed to disk in 8 MB chunks and never held in memory; extraction is streaming too. Verified with a **1.26 GB** archive at ~160 MB/s. |
| 2 | **Browse** the extraction end point | A full file browser over the output: folders expand, files preview inline, and every file can be downloaded. The **destination** is also chosen through a server-side file browser. |
| 3 | Run **locally or via a browser** | One command for each: `lulujar extract …` for the terminal, `lulujar serve` for the browser. The browser UI works on the same machine or on any device on the network. |

### Extras

- **APK inspector** — package name, version, SDK levels, permissions, components, ABIs and signature status, read straight from the binary `AndroidManifest.xml`.
- **Archive preview** — see the contents before extracting.
- **Security** — path traversal (`../`), absolute paths and symlink escapes are refused, and the browser can only look inside explicitly allowed roots.
- **Jobs** — background extraction with progress, cancellation and a history.
- **Zero mandatory dependencies** — the standard library does ZIP, APK and TAR.

---

## 4. Supported formats

| Format | Extension | Engine used out of the box |
|--------|-----------|----------------------------|
| ZIP | `.zip` | Python `zipfile` (standard library) |
| Android APK | `.apk` | Python `zipfile` + manifest inspector |
| RAR / RAR5 | `.rar` | bundled WebAssembly libarchive (Node) or `7z`/`unrar`/`unar` |
| 7-Zip | `.7z` | bundled WebAssembly libarchive or `7z` |
| TAR (+gz/bz2/xz) | `.tar`, `.tar.gz`… | Python `tarfile` (standard library) |
| gzip / bzip2 / xz | `.gz`, `.bz2`, `.xz` | Python standard library |

`./run.sh doctor` prints exactly which engine handles what on **your** machine.

### Want a native engine instead of the bundled WASM one?

Any of these are picked up automatically and preferred over the bundled engine:

| Tool | Where to get it |
|------|-----------------|
| **7-Zip** (`7z`, `7zz`, `7za`) | <https://www.7-zip.org> — the most capable option |
| **unrar** | bundled with WinRAR / RARLAB |
| **unar** (The Unarchiver) | `brew install unar` / `apt install unar` |
| **bsdtar** (libarchive) | `apt install libarchive-tools` / `brew install libarchive` |
| **unrar-cffi** | `pip install unrar-cffi` — in-process RAR decoding, no binary |
| **py7zr** | `pip install py7zr` — pure Python 7z reader |

`./run.sh install-tools` downloads a portable 7-Zip into `~/.lulujar/bin` for you.

---

## 5. Where things live

```
~/.lulujar/
├── uploads/      # uploaded archives (streamed to disk)
├── extracted/    # default destination for extractions
├── jobs/         # job history
└── config.json   # browse roots, upload limits
```

Useful environment variables:

| Variable | Meaning |
|----------|---------|
| `LULUJAR_HOME` | Move the whole runtime directory somewhere else |
| `LULUJAR_QUIET=1` | Silence the request log |

---

## 6. Using the browser interface

1. **Drop an archive** on the page, or click **Choose a file**, or
   **Pick from this machine** when the archive is already on the server.
2. **Choose the destination** — type a path or use **Browse…** to walk the
   machine and pick (or create) a folder.
3. Press **Extract archive**. Progress, the current file and the result are
   shown live.
4. The **Extracted output** tab shows the result: click folders to open them,
   click a file to preview it, or download it with ⬇.
5. The **APK info** tab fills in automatically for `.apk` files.

---

## 7. Command reference

```
lulujar extract ARCHIVE [-o DIR] [--list] [--apk] [--json]
lulujar info    ARCHIVE [--json]
lulujar serve   [--host H] [--port P] [--root DIR] [--token T] [--open]
lulujar browse  PATH
lulujar doctor
lulujar install-tools
lulujar version
```

---

## 8. Development

```bash
git clone https://github.com/rsv-op/LuluJar.git
cd LuluJar
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest                    # 54 tests
```

The layout:

```
lulujar/
├── formats.py     archive type detection (magic bytes + extension)
├── engine.py      the extraction back-ends and their priority order
├── fsapi.py       sandboxed filesystem browsing
├── apk.py         binary AndroidManifest.xml reader
├── jobs.py        background jobs, progress, cancellation
├── server.py      the HTTP API
├── cli.py         command line interface
├── web/           the browser interface (no build step)
└── node/          WebAssembly libarchive worker + vendored runtime
```

---

## 9. Troubleshooting

**"No extraction engine available for RAR archive"**
Node.js is missing (or the bundled runtime is not present). Either install
Node.js, or `pip install unrar-cffi`, or install 7-Zip and re-run
`./run.sh doctor`.

**"cannot bind 0.0.0.0:8000"**
The port is busy — use `--port 8080`.

**The browser cannot reach the server from another device**
Firewalls block it. Start with `--host 0.0.0.0` and allow the port; the
access token printed at startup is required for non-local connections.

**An encrypted archive**
Encrypted RAR/ZIP archives need a password. The browser UI does not prompt
for one yet — use `7z x -p<password>` or `unrar p-` directly for those.

---

## License

MIT — see [LICENSE](LICENSE).

The bundled `libarchive.js` runtime is MIT licensed (see
`lulujar/node/vendor/libarchive.js/LICENSE`).
