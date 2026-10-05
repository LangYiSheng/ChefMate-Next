"""Run with chefmate-usda or python -m chefmate_core.cli."""

import argparse
import json
import sqlite3
import zipfile

from .datasets import download, import_datasets
from .fooddata import LocalFoodData


def main(argv=None):
    parser = argparse.ArgumentParser(description="USDA 本地数据维护，不访问营养查询 API")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("download", "import", "install", "status", "search"):
        command = commands.add_parser(name)
        if name in ("download", "import", "install"):
            command.add_argument("--directory", required=True, help="官方 ZIP 保存目录")
        if name != "download":
            command.add_argument("--database", required=True, help="独立 USDA SQLite 文件")
        if name == "search":
            command.add_argument("query")
    args = parser.parse_args(argv)
    try:
        if args.command in ("download", "install"):
            paths = download(args.directory)
            result = {"files": [str(p) for p in paths]}
        if args.command in ("import", "install"):
            result = import_datasets(args.directory, args.database)
        if args.command == "status":
            result = LocalFoodData(args.database).status()
        if args.command == "search":
            result = LocalFoodData(args.database).search(args.query)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if isinstance(result, dict) and result.get("ready") is False else 0
    except (ValueError, OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
