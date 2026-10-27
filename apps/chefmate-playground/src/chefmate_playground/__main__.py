"""Launch the authenticated app; Docker supplies its explicit bind address."""

import argparse

import uvicorn

from .app import create_app
from .config import PlaygroundConfig


def main():
    parser = argparse.ArgumentParser(description="ChefMate 本地智能体测试页面")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", default=None)
    args = parser.parse_args()
    config = PlaygroundConfig.load()
    port = args.port or config.port
    if not 1 <= port <= 65535:
        parser.error("端口必须在 1 到 65535 之间")
    uvicorn.run(create_app(config), host=args.host or config.host, port=port, log_level="info")


if __name__ == "__main__":
    main()
