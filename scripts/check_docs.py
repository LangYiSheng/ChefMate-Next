"""Check repository Markdown file links and local Markdown heading anchors."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"!?\[[^\]\n]*\]\((<[^>]+>|[^\s)]+)(?:\s+['\"][^\n]*?['\"])?\)")


def markdown_text(path: Path) -> str:
    """Exclude fenced examples so their placeholder links are not treated as docs."""
    lines = []
    fence = None
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            lines.append("")
        else:
            lines.append(line if fence is None else "")
    return "\n".join(lines)


def anchors(path: Path) -> set[str]:
    counts: dict[str, int] = {}
    result = set()
    content = markdown_text(path)
    for heading in re.findall(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", content, re.MULTILINE):
        plain = re.sub(r"!?\[([^\]]+)\]\([^)]*\)", r"\1", heading)
        plain = re.sub(r"<[^>]*>", "", plain).lower()
        slug = re.sub(r"[^\w\- ]", "", plain).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        result.add(f"{slug}-{count}" if count else slug)
    result.update(re.findall(r"\b(?:id|name)=[\"']([^\"']+)[\"']", content))
    return result


def main() -> int:
    output = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.md"], cwd=ROOT
    )
    # test/ is a separate, user-owned manual sandbox; fixtures are source data.
    files = sorted({ROOT / name.decode("utf-8") for name in output.split(b"\0") if name})
    files = [
        path for path in files
        if path.is_file() and path.relative_to(ROOT).parts[0] != "test" and "fixtures" not in path.parts
    ]
    errors = []
    checked = 0
    cache: dict[Path, set[str]] = {}
    for path in files:
        content = markdown_text(path)
        for match in LINK.finditer(content):
            target = match[1].strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc:
                continue
            checked += 1
            if not parsed.path:
                resolved = path
            elif parsed.path.startswith("/"):
                resolved = (ROOT / unquote(parsed.path).lstrip("/")).resolve()
            else:
                resolved = (path.parent / unquote(parsed.path)).resolve()
            line = content.count("\n", 0, match.start()) + 1
            label = f"{path.relative_to(ROOT).as_posix()}:{line}: {target}"
            if not resolved.exists():
                errors.append(f"Missing target: {label}")
            elif parsed.fragment and resolved.suffix.lower() == ".md":
                if resolved not in cache:
                    cache[resolved] = anchors(resolved)
                if unquote(parsed.fragment) not in cache[resolved]:
                    errors.append(f"Missing heading: {label}")
    for error in errors:
        print(error)
    print(f"Checked {len(files)} Markdown files and {checked} local links; {len(errors)} errors.")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
