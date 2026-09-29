"""Fetch the public PowerCenter XML corpus at pinned commits and verify file digests.

    python scripts/fetch_corpus.py                  # clone/verify into benchmarks/external/
    python scripts/fetch_corpus.py --record         # (maintainers) rewrite file digests

Files are downloaded from their upstream repositories, never redistributed by ETLIR.
Groups without a license are for local, authorized research use only; check the license
field in benchmarks/corpus.toml before reusing any file.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "benchmarks" / "corpus.toml"
DEST = REPO / "benchmarks" / "external"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_powercenter(path: Path) -> bool:
    sys.path.insert(0, str(REPO / "src"))
    from etlir.sources.powercenter.xmlio import sniff

    return sniff(path)


def git(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True)  # noqa: S603, S607


def fetch(group: dict, dest: Path) -> Path:
    target = dest / group["id"]
    if not (target / ".git").exists():
        git("clone", "--quiet", "--filter=blob:none", group["upstream"], str(target))
    git("-c", "advice.detachedHead=false", "checkout", "--quiet", group["commit"], cwd=target)
    return target


def record(manifest_text: str, digests: dict[str, list[tuple[str, str]]]) -> str:
    """Replace each group's files block with freshly computed digests."""
    out, current, skip = [], None, False
    for line in manifest_text.splitlines():
        if line.startswith("id = "):
            current = line.split('"')[1]
        if line.startswith("files = ["):
            out.append("files = [")
            for rel, digest in digests.get(current or "", []):
                out.append(f'  {{ path = "{rel}", sha256 = "{digest}" }},')
            out.append("]")
            skip = not line.rstrip().endswith("]")
            continue
        if skip:
            skip = line.strip() != "]"
            continue
        out.append(line)
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dest", type=Path, default=DEST)
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()
    manifest = tomllib.loads(MANIFEST.read_text("utf-8"))
    digests: dict[str, list[tuple[str, str]]] = {}
    failures = 0
    for group in manifest["group"]:
        if group.get("upstream", "").startswith("https://"):
            root = fetch(group, args.dest)
        else:
            continue
        found = sorted(
            p
            for p in root.rglob("*")
            if p.is_file() and ".git" not in p.parts and is_powercenter(p)
        )
        digests[group["id"]] = [(p.relative_to(root).as_posix(), sha256(p)) for p in found]
        if args.record:
            continue
        for entry in group.get("files", []):
            path = root / entry["path"]
            ok = path.exists() and sha256(path) == entry["sha256"]
            failures += not ok
            if not ok:
                print(f"MISMATCH {group['id']}/{entry['path']}", file=sys.stderr)
        print(
            f"{group['id']}: {len(group.get('files', []))} file(s) at {group['commit'][:10]}"
            f" (license: {group['license']})"
        )
    if args.record:
        MANIFEST.write_text(record(MANIFEST.read_text("utf-8"), digests), "utf-8")
        print(f"recorded digests for {sum(len(v) for v in digests.values())} files")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
