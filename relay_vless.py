import asyncio
import socket
import time

RELAY_BUF = 256 * 1024

async def parse_vless_header(chunk: bytes):
    if len(chunk) < 24:
        raise ValueError("chunk too small")
    pos = 1
    pos += 16
    addon_len = chunk[pos]; pos += 1 + addon_len
    command = chunk[pos]; pos += 1
    port = int.from_bytes(chunk[pos:pos+2], "big"); pos += 2
    addr_type = chunk[pos]; pos += 1
    if addr_type == 1:
        address = ".".join(str(b) for b in chunk[pos:pos+4]); pos += 4
    elif addr_type == 2:
        dlen = chunk[pos]; pos += 1
        address = chunk[pos:pos+dlen].decode("utf-8", errors="ignore"); pos += dlen
    elif addr_type == 3:
        ab = chunk[pos:pos+16]; pos += 16
        address = ":".join(f"{ab[i]:02x}{ab[i+1]:02x}" for i in range(0, 16, 2))
    else:
        raise ValueError(f"unknown addr type: {addr_type}")
    return command, address, port, chunk[pos:]

def tune_socket(writer):
    sock = writer.transport.get_extra_info("socket")
    if not sock:
        return
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2 * 1024 * 1024)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2 * 1024 * 1024)
    except OSError:
        pass

async def relay_ws_to_tcp(ws, writer, conn_id):
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            data = msg.get("bytes") or (msg.get("text") or "").encode()
            if not data:
                continue
            writer.write(data)
            if writer.transport.get_write_buffer_size() > RELAY_BUF:
                await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.write_eof()
        except Exception:
            pass

async def relay_tcp_to_ws(ws, reader, conn_id):
    first = True
    try:
        while True:
            data = await reader.read(RELAY_BUF)
            if not data:
                break
            payload = (b"\x00\x00" + data) if first else data
            first = False
            await ws.send_bytes(payload)
    except Exception:
        pass
