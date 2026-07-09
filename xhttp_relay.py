import asyncio
import secrets
import time
import logging
from datetime import datetime

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
import uvicorn

from relay_vless import parse_vless_header, tune_socket

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("xhttp-relay")

app = FastAPI(title="XHTTP Relay", docs_url=None, redoc_url=None)

PORT = int(9999)
sessions: dict = {}
SESSIONS_LOCK = asyncio.Lock()
IDLE_TIMEOUT = 30
REAPER_INTERVAL = 10
TCP_TIMEOUT = 10.0
BUF_SIZE = 512 * 1024

async def teardown(session_id: str):
    async with SESSIONS_LOCK:
        sess = sessions.pop(session_id, None)
    if not sess:
        return
    sess["closed"] = True
    for t in ("uplink_task", "downlink_task"):
        task = sess.get(t)
        if task:
            task.cancel()
            try: await task
            except: pass
    writer = sess.get("writer")
    if writer:
        try:
            writer.close()
            await writer.wait_closed()
        except: pass

async def reaper():
    while True:
        await asyncio.sleep(REAPER_INTERVAL)
        now = time.time()
        async with SESSIONS_LOCK:
            stale = [sid for sid, s in sessions.items()
                     if now - s["last_seen"] > IDLE_TIMEOUT and not s.get("tcp_open")]
        for sid in stale:
            await teardown(sid)
        async with SESSIONS_LOCK:
            stale2 = [sid for sid, s in sessions.items()
                     if now - s["last_seen"] > IDLE_TIMEOUT and s.get("tcp_open")]
        for sid in stale2:
            await teardown(sid)

@app.on_event("startup")
async def startup():
    asyncio.create_task(reaper())
    logger.info(f"XHTTP Relay started on port {PORT}")

async def get_or_create_session(uuid: str, mode: str, session_id: str, ip: str = ""):
    async with SESSIONS_LOCK:
        sess = sessions.get(session_id)
        if sess:
            sess["last_seen"] = time.time()
            return sess
        sess = {
            "uuid": uuid, "mode": mode, "writer": None,
            "downlink_task": None, "uplink_task": None,
            "down_q": asyncio.Queue(maxsize=512),
            "last_seen": time.time(),
            "tcp_open": False, "closed": False,
            "flow_hw": 2 * 1024 * 1024,
        }
        sessions[session_id] = sess
        logger.info(f"new session [{session_id[:8]}] uuid={uuid[:8]}")
        return sess

async def pump_tcp_to_queue(session_id: str, reader, down_q):
    try:
        while True:
            data = await reader.read(BUF_SIZE)
            if not data:
                break
            await down_q.put(data)
    except: pass
    finally:
        await teardown(session_id)

async def open_tcp_for_session(session_id: str, sess: dict, first_chunk: bytes):
    command, address, port, payload = await parse_vless_header(first_chunk)
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(address, port), timeout=TCP_TIMEOUT
    )
    tune_socket(writer)
    logger.info(f"connect [{session_id[:8]}] -> {address}:{port}")
    sess["writer"] = writer
    sess["tcp_open"] = True
    if payload:
        writer.write(payload)
        await writer.drain()
    sess["downlink_task"] = asyncio.create_task(
        pump_tcp_to_queue(session_id, reader, sess["down_q"])
    )

def downstream_gen(sess):
    async def gen():
        try:
            while True:
                chunk = await sess["down_q"].get()
                if chunk is None:
                    break
                sess["last_seen"] = time.time()
                yield chunk
        finally: pass
    return gen()

@app.get("/xhttp-siz10/{mode}/{uuid}/{session_id}")
async def xhttp_downlink(mode: str, uuid: str, session_id: str, request: Request):
    if mode not in ("packet-up", "stream-up"):
        raise HTTPException(status_code=404, detail="unknown mode")
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or request.client.host or ""
    sess = await get_or_create_session(uuid, mode, session_id, ip)
    if sess.get("closed"):
        raise HTTPException(status_code=404, detail="session closed")
    headers = {
        "content-type": "application/grpc",
        "cache-control": "no-cache, no-store",
        "x-accel-buffering": "no",
    }
    return StreamingResponse(downstream_gen(sess), headers=headers)

@app.post("/xhttp-siz10/stream-up/{uuid}/{session_id}")
async def stream_up_upload(uuid: str, session_id: str, request: Request):
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or request.client.host or ""
    sess = await get_or_create_session(uuid, "stream-up", session_id, ip)
    if sess.get("closed"):
        raise HTTPException(status_code=404, detail="session closed")
    writer = sess["writer"]
    try:
        async for chunk in request.stream():
            if not chunk:
                continue
            sess["last_seen"] = time.time()
            if writer is None:
                await open_tcp_for_session(session_id, sess, chunk)
                writer = sess["writer"]
                continue
            writer.write(chunk)
            buf_size = writer.transport.get_write_buffer_size()
            if buf_size > sess["flow_hw"]:
                t0 = time.monotonic()
                await writer.drain()
                elapsed_ms = (time.monotonic() - t0) * 1000
                if elapsed_ms < 2.0:
                    sess["flow_hw"] = min(16 * 1024 * 1024, sess["flow_hw"] + 65536)
                elif elapsed_ms > 25.0:
                    sess["flow_hw"] = max(256 * 1024, sess["flow_hw"] // 2)
    except Exception as exc:
        logger.error(f"stream error [{session_id[:8]}]: {exc}")
        await teardown(session_id)
        raise HTTPException(status_code=502, detail="stream error")
    return {"ok": True}

@app.post("/xhttp-siz10/packet-up/{uuid}/{session_id}/{seq}")
async def packet_up_upload(uuid: str, session_id: str, seq: int, request: Request):
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or request.client.host or ""
    sess = await get_or_create_session(uuid, "packet-up", session_id, ip)
    if sess.get("closed"):
        raise HTTPException(status_code=404, detail="session closed")
    sess["last_seen"] = time.time()
    body = await request.body()
    if not body:
        return {"ok": True}
    try:
        if sess["writer"] is None:
            if seq != 0:
                sess.setdefault("seq_buf", {})[seq] = body
                return {"ok": True, "buffered": True}
            await open_tcp_for_session(session_id, sess, body)
            nxt = 1
            while nxt in sess.get("seq_buf", {}):
                pending = sess["seq_buf"].pop(nxt)
                sess["writer"].write(pending)
                nxt += 1
            sess["next_seq"] = nxt
            return {"ok": True, "connected": True}
        sess.setdefault("seq_buf", {})
        if seq == sess.get("next_seq", 0):
            sess["writer"].write(body)
            sess["next_seq"] = seq + 1
            while sess["next_seq"] in sess["seq_buf"]:
                pending = sess["seq_buf"].pop(sess["next_seq"])
                sess["writer"].write(pending)
                sess["next_seq"] += 1
        else:
            sess["seq_buf"][seq] = body
        if sess["writer"].transport.get_write_buffer_size() > 2 * 1024 * 1024:
            await sess["writer"].drain()
    except Exception as exc:
        logger.error(f"packet write error [{session_id[:8]}]: {exc}")
        await teardown(session_id)
        raise HTTPException(status_code=502, detail="write failed")
    return {"ok": True}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
