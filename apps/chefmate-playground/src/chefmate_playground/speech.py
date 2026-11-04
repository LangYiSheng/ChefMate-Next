"""Bounded, authenticated live dictation relay. Audio is never persisted."""
import asyncio
import gzip
import json
import os
import struct
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from dotenv import dotenv_values
from fastapi import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import connect

from .accounts import COOKIE_NAME
from .config import APP_ROOT

URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"


def packet(data: bytes, sequence: int, *, audio=False, final=False) -> bytes:
    payload = gzip.compress(data)
    return bytes([0x11, (0x20 if audio else 0x10) | (3 if final else 1),
                  0x01 if audio else 0x11, 0]) + struct.pack(">iI", -sequence if final else sequence, len(payload)) + payload


def response(data: bytes) -> tuple[dict, bool]:
    if len(data) < 8:
        raise ValueError("Invalid speech response")
    kind, flags = data[1] >> 4, data[1] & 15
    offset = (data[0] & 15) * 4
    if kind == 15:
        raise ValueError("Speech provider rejected request")
    if flags & 1:
        offset += 4
    if flags & 4:
        offset += 4
    size = struct.unpack_from(">I", data, offset)[0]
    body = data[offset + 4:offset + 4 + size]
    if data[2] & 15 == 1:
        body = gzip.decompress(body)
    payload = json.loads(body)
    if payload.get("code", 0) not in (0, 1000, 20000000):
        raise ValueError("Speech recognition failed")
    return payload, bool(flags & 2)


def speech_settings():
    runtime = Path(os.environ.get("CHEFMATE_RUNTIME_DIR", str(APP_ROOT / ".runtime")))
    values = {**dotenv_values(runtime / "speech.env"), **os.environ}
    return values.get("CHEFMATE_SPEECH_API_KEY", ""), values.get(
        "CHEFMATE_SPEECH_RESOURCE_ID", "volc.seedasr.sauc.duration")


def mount_speech(app, config, accounts=None):
    active = set()

    @app.websocket("/api/speech/stream")
    async def speech(ws: WebSocket):
        try:
            origin = urlsplit(ws.headers.get("origin", ""))
            host = urlsplit("http://" + ws.headers.get("host", "")).hostname
            valid = (origin.scheme in {"http", "https"} and origin.netloc == ws.headers.get("host")
                     and (host in config.allowed_hosts or "*" in config.allowed_hosts))
        except ValueError:
            valid = False
        if not valid:
            await ws.close(code=1008)
            return
        identity = "local"
        if accounts is not None:
            try:
                user = await asyncio.wait_for(asyncio.to_thread(
                    accounts.authenticate_session, ws.cookies.get(COOKIE_NAME)), 3)
            except Exception:
                user = None
            if not user or user.get("disabled"):
                await ws.close(code=1008)
                return
            identity = user["id"]
        await ws.accept()
        api_key, resource = speech_settings()
        if not api_key or identity in active or len(active) >= 16:
            await ws.send_json({"type": "error", "message": "语音未配置或已有录音正在进行，请稍后重试。"})
            await ws.close()
            return
        active.add(identity)
        try:
            async with asyncio.timeout(185):
                async with connect(URL, additional_headers={"X-Api-Key": api_key,
                    "X-Api-Resource-Id": resource, "X-Api-Request-Id": str(uuid4())},
                    open_timeout=10, close_timeout=2, max_size=1_000_000) as upstream:
                    payload = {"user": {"uid": identity}, "audio": {"format": "pcm", "codec": "raw",
                        "rate": 16000, "bits": 16, "channel": 1}, "request": {"model_name": "bigmodel",
                        "enable_itn": True, "enable_punc": True, "show_utterances": True}}
                    await upstream.send(packet(json.dumps(payload).encode(), 1))
                    response(await asyncio.wait_for(upstream.recv(), 10))
                    await ws.send_json({"type": "ready"})

                    async def upload():
                        sequence, total = 2, 0
                        while True:
                            msg = await asyncio.wait_for(ws.receive(), 15)
                            if msg["type"] == "websocket.disconnect":
                                raise WebSocketDisconnect()
                            audio = msg.get("bytes")
                            if audio is not None:
                                total += len(audio)
                                if len(audio) > 32000 or len(audio) % 2 or total > 16000 * 2 * 180:
                                    raise ValueError("Audio limit")
                                await upstream.send(packet(audio, sequence, audio=True))
                                sequence += 1
                            elif json.loads(msg.get("text", "{}")) == {"type": "stop"}:
                                await upstream.send(packet(b"", sequence, audio=True, final=True))
                                return
                            else:
                                raise ValueError("Invalid control")

                    async def download():
                        async for raw in upstream:
                            result, final = response(raw)
                            text = result.get("result", {}).get("text", "")
                            await ws.send_json({"type": "final" if final else "partial", "text": text[:8000]})
                            if final:
                                return
                        raise ValueError("No final result")

                    sender = asyncio.create_task(upload())
                    receiver = asyncio.create_task(download())
                    try:
                        done, _ = await asyncio.wait((sender, receiver), return_when=asyncio.FIRST_COMPLETED)
                        for task in done:
                            task.result()
                        if receiver not in done:
                            await asyncio.wait_for(receiver, 6)
                    finally:
                        sender.cancel()
                        receiver.cancel()
                        await asyncio.gather(sender, receiver, return_exceptions=True)
        except WebSocketDisconnect:
            pass
        except Exception:
            try:
                await ws.send_json({"type": "error", "message": "语音连接中断或转写未完成，已保留文字，请重试。"})
            except Exception:
                pass
        finally:
            active.discard(identity)
            try:
                await ws.close()
            except Exception:
                pass
