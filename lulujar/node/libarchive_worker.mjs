/**
 * LuluJar - libarchive (WASM) worker.
 *
 * Runs the pure-WebAssembly build of libarchive under Node so that RAR v4 and
 * v5, 7-Zip, ZIP and TAR can be handled without any native binary.
 *
 *   node libarchive_worker.mjs list    <archive>
 *   node libarchive_worker.mjs extract <archive> <destination>
 *
 * The final JSON result is written to stdout, progress information to stderr.
 */

import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

/* ------------------------------------------------------------------ */
/* locate the libarchive.js build                                      */
/* ------------------------------------------------------------------ */

async function loadLibarchive() {
  const candidates = [];
  const here = path.dirname(new URL(import.meta.url).pathname);

  if (process.env.LULUJAR_LIBARCHIVE) {
    candidates.push(path.join(process.env.LULUJAR_LIBARCHIVE, "dist", "libarchive-node.mjs"));
    candidates.push(process.env.LULUJAR_LIBARCHIVE);
  }

  // Bundled with LuluJar - works with no installation at all.
  candidates.push(path.join(here, "vendor", "libarchive.js", "dist", "libarchive-node.mjs"));

  // Installed next to this file (project local node_modules).
  candidates.push(
    path.join(here, "node_modules", "libarchive.js", "dist", "libarchive-node.mjs")
  );

  // Installed in the project root (npm install libarchive.js).
  candidates.push(
    path.join(here, "..", "..", "node_modules", "libarchive.js", "dist", "libarchive-node.mjs")
  );

  // Global npm install.
  try {
    const { execFileSync } = await import("node:child_process");
    const root = execFileSync("npm", ["root", "-g"], { encoding: "utf-8", timeout: 15000 }).trim();
    if (root) {
      candidates.push(path.join(root, "libarchive.js", "dist", "libarchive-node.mjs"));
    }
  } catch (_) {
    /* npm not available - fine */
  }

  let lastError = null;
  for (const candidate of candidates) {
    try {
      const module = await import(pathToFileURL(candidate).href);
      if (module.Archive) return module;
    } catch (error) {
      lastError = error;
    }
  }
  throw new Error(
    `libarchive.js not found. Install it with: npm install libarchive.js  (${lastError?.message ?? ""})`
  );
}

/* ------------------------------------------------------------------ */
/* helpers                                                             */
/* ------------------------------------------------------------------ */

const FileCtor = globalThis.File ?? (await import("node:buffer")).File;

/**
 * Strip traversal and absolute path components from an archive member.
 * Returns null when the member tries to escape the destination.
 */
function safeRelative(name) {
  const parts = String(name).split(/[\\/]+/);
  const clean = [];
  for (const part of parts) {
    if (part === "" || part === ".") continue;
    if (part === "..") return null;
    clean.push(part);
  }
  return clean.join("/");
}

function report(payload) {
  process.stderr.write(`${JSON.stringify(payload)}\n`);
}

/* ------------------------------------------------------------------ */
/* commands                                                            */
/* ------------------------------------------------------------------ */

async function openArchive(Archive, archivePath) {
  const buffer = fs.readFileSync(archivePath);
  const file = new FileCtor([buffer], path.basename(archivePath));
  return Archive.open(file);
}

async function commandList(Archive, archivePath) {
  const archive = await openArchive(Archive, archivePath);
  const files = await archive.getFilesArray();

  const entries = [];
  const seenDirs = new Set();
  for (const item of files) {
    const name = item.file?.name ?? "";
    const dir = item.path ?? "";
    const full = safeRelative(`${dir}${name}`);
    if (!full) continue;

    // Register the parent directories so empty folders show up as well.
    const segments = full.split("/");
    for (let i = 1; i < segments.length; i++) {
      const dirPath = segments.slice(0, i).join("/");
      if (dirPath && !seenDirs.has(dirPath)) {
        seenDirs.add(dirPath);
        entries.push({ path: dirPath, size: 0, is_dir: true });
      }
    }

    entries.push({
      path: full,
      size: item.file?.size ?? 0,
      is_dir: false,
    });
  }

  return { entries };
}

async function commandExtract(Archive, archivePath, destination) {
  const archive = await openArchive(Archive, archivePath);
  const tree = await archive.extractFiles();

  let files = 0;
  let bytes = 0;

  const write = async (node, prefix) => {
    for (const key of Object.keys(node)) {
      const value = node[key];
      if (value instanceof FileCtor) {
        const relative = safeRelative(prefix ? `${prefix}/${key}` : key);
        if (!relative) {
          report({ warning: `skipped unsafe path: ${prefix ? `${prefix}/` : ""}${key}` });
          continue;
        }
        const target = path.join(destination, relative);
        const parent = path.dirname(target);
        fs.mkdirSync(parent, { recursive: true });
        // Never write through a symlink that points outside the destination.
        if (!isInside(destination, parent)) {
          report({ warning: `skipped ${relative}: path escapes the destination` });
          continue;
        }
        const buffer = Buffer.from(await value.arrayBuffer());
        fs.writeFileSync(target, buffer);
        files += 1;
        bytes += buffer.length;
        report({ progress: { current: relative, bytes_done: bytes, files_done: files } });
      } else if (value && typeof value === "object") {
        const relative = safeRelative(prefix ? `${prefix}/${key}` : key);
        if (relative) {
          const dir = path.join(destination, relative);
          fs.mkdirSync(dir, { recursive: true });
          if (!isInside(destination, dir)) {
            report({ warning: `skipped ${relative}: path escapes the destination` });
            continue;
          }
        }
        await write(value, relative);
      }
    }
  };

  /** True when ``child`` really lives inside ``base`` (symlinks resolved). */
  function isInside(base, child) {
    try {
      const realBase = fs.realpathSync(base);
      const realChild = fs.realpathSync(child);
      return realChild === realBase || realChild.startsWith(realBase + path.sep);
    } catch (_) {
      return false;
    }
  }

  fs.mkdirSync(destination, { recursive: true });
  await write(tree, "");

  return { files, bytes };
}

/* ------------------------------------------------------------------ */

async function main() {
  const [command, archivePath, destination] = process.argv.slice(2);
  if (!command || !archivePath) {
    process.stderr.write("usage: libarchive_worker.mjs <list|extract> <archive> [destination]\n");
    process.exit(2);
  }

  const { Archive } = await loadLibarchive();

  if (command === "list") {
    const result = await commandList(Archive, archivePath);
    process.stdout.write(`${JSON.stringify(result)}\n`);
    return;
  }

  if (command === "extract") {
    if (!destination) {
      process.stderr.write("destination is required\n");
      process.exit(2);
    }
    const result = await commandExtract(Archive, archivePath, destination);
    process.stdout.write(`${JSON.stringify(result)}\n`);
    return;
  }

  process.stderr.write(`unknown command: ${command}\n`);
  process.exit(2);
}

main().catch((error) => {
  process.stderr.write(`${error?.message ?? error}\n`);
  process.exit(1);
});
