import asyncio
import websockets


async def main():
    async with websockets.connect("ws://127.0.0.1:8000/ws/metrics") as ws:
        msg = await asyncio.wait_for(ws.recv(), timeout=6)
        print(msg)


asyncio.run(main())