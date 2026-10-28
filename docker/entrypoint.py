"""Construct default container database URLs without exposing raw passwords.

Explicit connection URLs are preserved. Compose's private service credentials
use separate fields so URI-reserved characters remain valid passwords.
"""

from __future__ import annotations

import os
import sys
from urllib.parse import quote


def configure_database(environment, prefix, target):
    if environment.get(target) or not environment.get(prefix + "_HOST"):
        return
    password = environment.get(prefix + "_PASSWORD")
    port = environment.get(prefix + "_PORT", "5432")
    if not password or not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("数据库密码或端口未正确配置。")
    host = environment[prefix + "_HOST"]
    if any(character in host for character in "/?#@") or not host.strip() or host != host.strip():
        raise ValueError("数据库主机配置无效。")
    if ":" in host and not host.startswith("["):
        host = "[" + host + "]"
    username = quote(environment.get(prefix + "_USER", "chefmate"), safe="")
    name = quote(environment.get(prefix + "_NAME", "chefmate"), safe="")
    environment[target] = f"postgresql://{username}:{quote(password, safe='')}@{host}:{port}/{name}"


def main():
    try:
        configure_database(os.environ, "CHEFMATE_APP_DB", "CHEFMATE_APP_DATABASE_URL")
        configure_database(os.environ, "CHEFMATE_CATALOG_DB", "CHEFMATE_DATABASE_URL")
    except ValueError:
        raise SystemExit("数据库容器配置无效，请核对主机、端口与密码；连接详情不写入日志。") from None
    arguments = sys.argv[1:] or ["chefmate-playground"]
    os.execvp(arguments[0], arguments)


if __name__ == "__main__":
    main()
