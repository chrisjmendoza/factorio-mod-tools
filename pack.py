"""Packages every mod in this workspace into a Factorio-ready zip.

Zips built on Windows break on macOS and Linux in two ways that are invisible
until someone else installs them, so both are checked here rather than found
in the wild:

  * Entry names must use forward slashes. Windows tooling happily writes
    backslashes, which Factorio then reads as part of the filename.
  * Paths inside the archive must match the case that the Lua refers to. NTFS
    does not care about case, so a `__Mod__/Graphics/bird.png` reference
    against a `graphics/` folder loads fine on Windows and fails everywhere
    else.

Usage:
    python tools/pack.py            # build every mod into dist/
    python tools/pack.py --deploy   # ...and copy the zips into the mods folder
    python tools/pack.py AmbientLife
"""

import argparse
import json
import os
import re
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
MODS_DIR = os.path.join(os.environ.get("APPDATA", ""), "Factorio", "mods")

# Build artefacts, editor leavings and our own tooling stay out of the archive.
EXCLUDE_DIRS = {".git", ".vscode", "__pycache__", "tools", "dist", ".idea"}
EXCLUDE_FILES = {".DS_Store", "Thumbs.db", "desktop.ini", ".gitignore",
                 "thumbnail_720.png"}
EXCLUDE_EXTS = {".zip", ".pyc", ".bak", ".orig"}

# Matches a Factorio resource path such as "__AmbientLife__/graphics/bird.png".
RESOURCE = re.compile(r"__([A-Za-z0-9_\-]+)__/([^\"']+)")

# Matches a `defines.events.on_something` reference in Lua.
EVENT = re.compile(r"defines\.events\.([a-z_]+)")

# The API docs shipped with the game are version-exact, unlike the web copy.
DOC_HTML = os.path.join("C:\\", "Games", "Steam", "steamapps", "common",
                        "Factorio", "doc-html", "defines.html")


def known_events():
    """Every defines.events name the installed Factorio actually defines.

    An invented event name is not a load error in Lua — defines.events.foo is
    simply nil, and script.on_event fails at runtime with a traceback that
    only names a line number. Cheaper to catch it here.
    """
    path = os.environ.get("FACTORIO_DEFINES_HTML", DOC_HTML)
    if not os.path.isfile(path):
        return None  # no local docs to check against; skip rather than guess
    with open(path, encoding="utf-8", errors="ignore") as handle:
        text = re.sub(r"<[^>]+>", " ", handle.read())
    return set(re.findall(r"\b(on_[a-z_]+|script_raised_[a-z_]+)\b", text))


def find_mods(root):
    """A mod is any directory holding an info.json, searched two deep."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        if os.path.relpath(dirpath, root).count(os.sep) > 2:
            dirnames[:] = []
            continue
        if "info.json" in filenames:
            found.append(dirpath)
            dirnames[:] = []  # a mod never contains another mod
    return sorted(found)


def collect_files(mod_dir):
    """Every shipped file, as (absolute path, forward-slash relative path)."""
    out = []
    for dirpath, dirnames, filenames in os.walk(mod_dir):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for name in sorted(filenames):
            if name in EXCLUDE_FILES or os.path.splitext(name)[1].lower() in EXCLUDE_EXTS:
                continue
            if name.startswith("_preview"):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, mod_dir).replace(os.sep, "/")
            out.append((full, rel))
    return out


def check_resource_paths(mod_dir, mod_name, files):
    """Confirms every __Mod__/path reference resolves with exact case."""
    problems = []
    real = {rel for _, rel in files}

    for full, rel in files:
        if not rel.endswith(".lua"):
            continue
        try:
            text = open(full, encoding="utf-8").read()
        except UnicodeDecodeError:
            continue
        for ref_mod, ref_path in RESOURCE.findall(text):
            if ref_mod != mod_name:
                continue  # a reference into base, core or another mod
            if ref_path in real:
                continue
            lowered = {r.lower(): r for r in real}
            actual = lowered.get(ref_path.lower())
            if actual:
                problems.append(
                    f"{rel}: case mismatch, Lua asks for '{ref_path}' "
                    f"but the file on disk is '{actual}' (breaks on macOS/Linux)"
                )
            else:
                problems.append(f"{rel}: references missing file '{ref_path}'")

    events = known_events()
    if events:
        for full, rel in files:
            if not rel.endswith(".lua"):
                continue
            try:
                text = open(full, encoding="utf-8").read()
            except UnicodeDecodeError:
                continue
            for name in sorted(set(EVENT.findall(text))):
                if name not in events:
                    problems.append(
                        f"{rel}: defines.events.{name} does not exist in this "
                        f"Factorio version (it will be nil at runtime)"
                    )
    return problems


def build(mod_dir, deploy=False):
    with open(os.path.join(mod_dir, "info.json"), encoding="utf-8") as handle:
        info = json.load(handle)

    name, version = info["name"], info["version"]
    folder = f"{name}_{version}"
    files = collect_files(mod_dir)

    problems = check_resource_paths(mod_dir, name, files)
    if problems:
        print(f"  {folder}: FAILED")
        for problem in problems:
            print(f"    - {problem}")
        return None

    os.makedirs(DIST, exist_ok=True)
    out_path = os.path.join(DIST, folder + ".zip")

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for full, rel in files:
            # Built by hand rather than via write()'s arcname so the separator
            # and the permission bits are ours on every platform.
            entry = zipfile.ZipInfo(f"{folder}/{rel}")
            entry.date_time = (2024, 1, 1, 0, 0, 0)  # reproducible archives
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            with open(full, "rb") as handle:
                zf.writestr(entry, handle.read())

    size = os.path.getsize(out_path) / 1024
    print(f"  {folder}.zip  ({len(files)} files, {size:.0f} KB)")

    if deploy:
        if not os.path.isdir(MODS_DIR):
            print(f"    ! mods folder not found: {MODS_DIR}")
        else:
            try:
                # Clear older builds of the same mod so Factorio cannot load two.
                for existing in os.listdir(MODS_DIR):
                    if re.fullmatch(re.escape(name) + r"_\d+\.\d+\.\d+\.zip", existing):
                        os.remove(os.path.join(MODS_DIR, existing))
                shutil.copy2(out_path, os.path.join(MODS_DIR, folder + ".zip"))
                print(f"    -> deployed to {MODS_DIR}")
            except PermissionError:
                # Factorio keeps every mod zip open for as long as it runs.
                print(f"    ! could not deploy: Factorio is running and holding "
                      f"{name}'s zip open. Close Factorio and re-run.")
                print(f"      The built zip is still available at {out_path}")

    return out_path


def verify(path):
    """Re-opens a finished zip and asserts the cross-platform invariants."""
    with zipfile.ZipFile(path) as zf:
        bad = zf.testzip()
        if bad:
            return [f"corrupt entry: {bad}"]
        names = zf.namelist()
        issues = []
        if any("\\" in n for n in names):
            issues.append("archive contains backslash separators")
        roots = {n.split("/")[0] for n in names}
        if len(roots) != 1:
            issues.append(f"expected exactly one top-level folder, got {sorted(roots)}")
        if not any(n.endswith("/info.json") for n in names):
            issues.append("no info.json inside the top-level folder")
        lowered = [n.lower() for n in names]
        if len(set(lowered)) != len(lowered):
            issues.append("entries collide when compared case-insensitively")
        return issues


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mods", nargs="*", help="mod names to build (default: all)")
    parser.add_argument("--deploy", action="store_true",
                        help="copy the finished zips into the Factorio mods folder")
    args = parser.parse_args()

    mod_dirs = find_mods(ROOT)
    if args.mods:
        wanted = {m.lower() for m in args.mods}
        mod_dirs = [d for d in mod_dirs
                    if json.load(open(os.path.join(d, "info.json"), encoding="utf-8")
                                 )["name"].lower() in wanted]

    if not mod_dirs:
        print("No mods found.")
        return 1

    print(f"Packing {len(mod_dirs)} mod(s) into dist/:")
    built, failed = [], 0
    for mod_dir in mod_dirs:
        path = build(mod_dir, deploy=args.deploy)
        if path:
            built.append(path)
        else:
            failed += 1

    print("\nVerifying archives:")
    for path in built:
        issues = verify(path)
        label = os.path.basename(path)
        if issues:
            failed += 1
            print(f"  {label}: FAILED")
            for issue in issues:
                print(f"    - {issue}")
        else:
            print(f"  {label}: ok (forward slashes, single root, case-unique)")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
