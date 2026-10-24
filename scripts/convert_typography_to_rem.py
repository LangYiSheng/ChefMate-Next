"""Convert CSS typography from today's 16px baseline into root-relative units.

Dry-run by default. Use --write only after frontend changes have been merged.
Layout dimensions, breakpoints, icon sizes, and local preview calculations stay intact.
"""

import argparse
import re
from pathlib import Path

DECLARATION = re.compile(r"(?P<name>(?<![-\w])font(?:-size)?)\s*:(?P<value>[^;{}]+)")
PIXELS = re.compile(r"(?<![\w.-])(?P<size>\d+(?:\.\d+)?)px\b")
ROOT_BLOCK = re.compile(r":root(?:\[[^\]]*\])?\s*\{[^{}]*\}")


def convert(source: str) -> tuple[str, int]:
    count = 0
    root_ranges = [(match.start(), match.end()) for match in ROOT_BLOCK.finditer(source)]

    def declaration(match):
        nonlocal count
        if "--font-size-base" in match["value"]:
            return match[0]
        # A rem-based root size would refer back to the browser's initial font.
        # Keep the actual root preference and its px fallback independent.
        if match["name"] == "font-size" and (
            "--font-size-base" in match["value"]
            or any(start <= match.start() < end for start, end in root_ranges)
        ):
            return match[0]

        def pixels(value):
            nonlocal count
            count += 1
            return f"{float(value['size']) / 16:g}rem"

        return f"{match['name']}:{PIXELS.sub(pixels, match['value'])}"

    return DECLARATION.sub(declaration, source), count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Apply conversions in place; omit to preview counts.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1] / "apps/chefmate-playground/frontend/src")
    arguments = parser.parse_args()
    changed = 0
    for path in sorted(arguments.root.rglob("*.css")):
        source = path.read_text(encoding="utf-8")
        converted, count = convert(source)
        if not count:
            continue
        changed += count
        if arguments.write:
            path.write_text(converted, encoding="utf-8")
        print(f"{path.relative_to(arguments.root)}: {count}")
    print(f"{'Converted' if arguments.write else 'Would convert'} {changed} typography values at a 16px baseline.")


if __name__ == "__main__":
    main()
