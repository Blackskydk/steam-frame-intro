"""Use Steam's existing local CEF debugger for normal customization calls."""
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import urllib.parse
import urllib.request


def evaluate(target, expression):
    url = urllib.parse.urlsplit(target["webSocketDebuggerUrl"])
    port = url.port or 8080
    if port != 8080:
        raise ValueError('Steam debugger must use its existing local port 8080')
    with socket.create_connection(("127.0.0.1", port), timeout=8) as sock:
        sock.settimeout(5)

        def read(n):
            result = b""
            while len(result) < n:
                part = sock.recv(n - len(result))
                if not part:
                    raise EOFError("Debug connection closed")
                result += part
            return result

        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {url.path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(request.encode())
        header = b""
        while not header.endswith(b"\r\n\r\n"):
            header += read(1)
            if len(header) > 65536:
                raise RuntimeError("Oversized WebSocket header")
        if b" 101 " not in header.split(b"\r\n")[0]:
            raise RuntimeError(header.decode(errors="replace"))
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        )
        if expected not in header:
            raise RuntimeError("Invalid WebSocket handshake")

        def send(opcode, data):
            length = len(data)
            if length < 126:
                prefix = bytes([0x80 | opcode, 0x80 | length])
            elif length <= 65535:
                prefix = bytes([0x80 | opcode, 0xFE]) + struct.pack("!H", length)
            else:
                prefix = bytes([0x80 | opcode, 0xFF]) + struct.pack("!Q", length)
            mask = os.urandom(4)
            masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            sock.sendall(prefix + mask + masked)

        send(1, json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True, "awaitPromise": True},
        }).encode())
        chunks = b""
        while True:
            a, b = read(2)
            opcode = a & 15
            length = b & 127
            if length == 126:
                length = struct.unpack("!H", read(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", read(8))[0]
            if length > 16 * 1024 * 1024:
                raise RuntimeError("Oversized debug response")
            mask = read(4) if b & 128 else None
            data = read(length)
            if mask:
                data = bytes(v ^ mask[i % 4] for i, v in enumerate(data))
            if opcode == 8:
                raise RuntimeError("WebSocket closed")
            if opcode == 9:
                send(10, data)
                continue
            if opcode not in (0, 1):
                continue
            chunks += data
            if not a & 128:
                continue
            result = json.loads(chunks)
            chunks = b""
            if result.get("id") == 1:
                return result


def request(expression):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8080/json/list', timeout=3) as response:
            targets = json.load(response)
        target = next(t for t in targets if t.get('title') == 'SharedJSContext')
        result = evaluate(target, expression)
        if 'error' in result or result.get('result', {}).get('exceptionDetails'):
            raise RuntimeError(json.dumps(result))
        return result['result']['result'].get('value')
    except (OSError, StopIteration, KeyError) as error:
        raise RuntimeError('Steam shared UI debugger is unavailable at localhost:8080. '
                           'Keep Steam running, or install with --keep-dashboard-intro.') from error


def snapshot():
    value = request("Object.fromEntries(['startup_movie_id','startup_movie_local_path',"
                    "'startup_movie_shuffle'].map(k=>[k,window.settingsStore.clientSettings[k]]))")
    if (not isinstance(value, dict) or not isinstance(value.get('startup_movie_local_path'), str)
            or not isinstance(value.get('startup_movie_id'), str)
            or type(value.get('startup_movie_shuffle')) is not bool):
        raise RuntimeError('Steam returned an unexpected startup-movie configuration')
    return value


def select(url):
    encoded = json.dumps(url)
    return request("(async()=>{const url=" + encoded + ";"
                   "const movies=await SteamClient.Customization.GetLocalStartupMovies();"
                   "if(!movies.some(m=>m.strMovieURL===url))throw Error('Local skip movie not discovered');"
                   "await window.userProfileStore.SetStartupMovie({communityitemid:'0',movie_webm_local_path:url});"
                   "const [,setShuffle]=window.settingsStore.GetClientSetting('startup_movie_shuffle');"
                   "await setShuffle(false);return window.settingsStore.clientSettings.startup_movie_local_path;})()")


def restore(before, owned_url):
    return request("(async()=>{const before=" + json.dumps(before) + ";const owned=" + json.dumps(owned_url) + ";"
                   "if(window.settingsStore.clientSettings.startup_movie_local_path!==owned)return false;"
                   "if(!before.startup_movie_local_path&&before.startup_movie_id==='0')"
                   "await window.userProfileStore.SetStartupMovie(null);else "
                   "await window.userProfileStore.SetStartupMovie({communityitemid:before.startup_movie_id,"
                   "movie_webm_local_path:before.startup_movie_local_path});"
                   "const [,setShuffle]=window.settingsStore.GetClientSetting('startup_movie_shuffle');"
                   "await setShuffle(Boolean(before.startup_movie_shuffle));return true;})()")
