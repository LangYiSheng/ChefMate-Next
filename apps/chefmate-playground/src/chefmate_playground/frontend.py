"""Serve the single React application and its Vite build from one origin."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles


def mount_frontend(app: FastAPI, root: Path) -> None:
    async def index():
        document = root / "index.html"
        if not document.is_file():
            return HTMLResponse(
                "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
                "<title>ChefMate · 需要构建前端</title><h1>前端尚未构建</h1>"
                "<p>请运行 apps/chefmate-playground/run.ps1，或在 frontend 目录执行 "
                "npm ci 和 npm run build 后重启服务。</p></html>",
                status_code=503,
            )
        return FileResponse(document)

    for path in ("/", "/settings", "/recipes", "/journal", "/account", "/admin"):
        app.add_api_route(path, index, methods=["GET"], include_in_schema=False)

    if root.is_dir():
        app.mount("/static", StaticFiles(directory=root), name="static")
