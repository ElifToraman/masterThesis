import json
import os
import socket
import time
import httpx


def new():
    return Function()


class Function:
    async def handle(self, scope, receive, send):
        start = time.time()

        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.request":
                body += message.get("body", b"")
                if not message.get("more_body", False):
                    break

        try:
            payload = json.loads(body.decode()) if body else {}
        except Exception:
            payload = {}

        work_ms = int(payload.get("work_ms", 50))
        time.sleep(work_ms / 1000)

        f3_url = os.getenv("F3_URL")
        if not f3_url:
            result = {"error": "F3_URL is not set"}
            status = 500
        else:
            next_payload = {
                "from": "f2",
                "work_ms": work_ms,
                "previous": payload,
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                f3_response = await client.post(f3_url, json=next_payload)

            duration_ms = round((time.time() - start) * 1000, 2)

            result = {
                "function": "f2",
                "message": "middle function completed",
                "hostname": socket.gethostname(),
                "vm_floating_ip": os.getenv("VM_FLOATING_IP", "unknown"),
                "work_ms": work_ms,
                "duration_ms": duration_ms,
                "f3_response": f3_response.json(),
            }
            status = 200

        response_body = json.dumps(result).encode()

        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [[b"content-type", b"application/json"]],
        })
        await send({
            "type": "http.response.body",
            "body": response_body,
        })