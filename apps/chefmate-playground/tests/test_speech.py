import gzip
import json
import struct
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from chefmate_playground.speech import mount_speech, packet, response


def test_last_audio_packet_preserves_tail():
    data = packet(b'\x01\x02', 7, audio=True, final=True)
    assert data[:4] == bytes([0x11, 0x23, 1, 0])
    assert struct.unpack('>i', data[4:8])[0] == -7
    assert gzip.decompress(data[12:]) == b'\x01\x02'


def test_partial_and_final_response():
    payload = gzip.compress(json.dumps({'result': {'text': '今晚吃鸡肉'}}).encode())
    for flags in (1, 3):
        data = bytes([0x11, 0x90 | flags, 0x11, 0]) + struct.pack('>iI', 1, len(payload)) + payload
        parsed, final = response(data)
        assert parsed['result']['text'] == '今晚吃鸡肉'
        assert final == (flags == 3)


@pytest.mark.parametrize('origin,logged_in', [('http://evil.example', True), ('http://testserver', False)])
def test_rejects_foreign_origin_and_missing_login(origin, logged_in):
    app = FastAPI()
    accounts = SimpleNamespace(authenticate_session=lambda cookie: {'id': 'test'} if logged_in else None)
    mount_speech(app, SimpleNamespace(allowed_hosts=('testserver',)), accounts)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/api/speech/stream', headers={'origin': origin}):
                pass
