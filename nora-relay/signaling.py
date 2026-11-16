"""
signaling.py - Tiny WebRTC signaling relay. Runs on your VPS (162.35.37.67).

One host + one viewer per session. Relays offer/answer (non-trickle: ICE
candidates are bundled inside the SDP, so there are no separate candidate
messages to lose). Does NOT carry media - that's peer-to-peer / via TURN.

    pip install websockets
    python signaling.py            # ws://0.0.0.0:5001

Open port 5001/tcp on the VPS firewall.
"""
import asyncio
import json
import websockets

ROOMS = {}   # session -> {"host":ws, "viewer":ws, "pw":str}


async def handler(ws, path=None):
    session = None
    role = None
    try:
        async for raw in ws:
            try:
                m = json.loads(raw)
            except ValueError:
                continue
            t = m.get("type")

            if t == "join":
                session = m.get("session")
                role = m.get("role")
                pw = m.get("password", "")
                room = ROOMS.setdefault(session, {"host": None, "viewer": None, "pw": pw})
                if role == "host":
                    room["host"] = ws
                    room["pw"] = pw
                    print(f"[sig] host joined {session}")
                else:
                    if pw != room.get("pw"):
                        await ws.send(json.dumps({"type": "error", "msg": "bad password"}))
                        continue
                    room["viewer"] = ws
                    print(f"[sig] viewer joined {session}")
                    if room["host"]:
                        await room["host"].send(json.dumps({"type": "viewer-join"}))

            elif t == "offer":
                room = ROOMS.get(session)
                if room and room["viewer"]:
                    await room["viewer"].send(raw)

            elif t == "answer":
                room = ROOMS.get(session)
                if room and room["host"]:
                    await room["host"].send(raw)
    except websockets.ConnectionClosed:
        pass
    finally:
        if session and session in ROOMS:
            room = ROOMS[session]
            if room.get("host") is ws:
                room["host"] = None
            if room.get("viewer") is ws:
                room["viewer"] = None
            if not room["host"] and not room["viewer"]:
                ROOMS.pop(session, None)


async def main():
    async with websockets.serve(handler, "0.0.0.0", 5001, max_size=4 * 1024 * 1024):
        print("[sig] signaling on ws://0.0.0.0:5001")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
