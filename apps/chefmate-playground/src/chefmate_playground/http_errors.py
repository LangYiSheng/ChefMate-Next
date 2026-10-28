"""Storage outages have a stable, secret-free HTTP boundary."""

import sqlite3

import psycopg
from fastapi.responses import JSONResponse


STORAGE_ERRORS = (OSError, sqlite3.Error, psycopg.Error)


def install_storage_handlers(app):
    async def unavailable(request, error):
        return JSONResponse({"detail": "应用数据服务暂时不可用，请稍后重试。"}, status_code=503)
    for error_type in STORAGE_ERRORS:
        app.add_exception_handler(error_type, unavailable)
