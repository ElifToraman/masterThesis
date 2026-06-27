import json
import os
import socket
import time
import httpx


def new():
    return Function()


class Function:
    async def handle(self, scope, receive, send):
        chain_start = time.time()

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

        f2_url = os.getenv("F2_URL")
        if not f2_url:
            result = {"error": "F2_URL is not set"}
            status = 500
        else:
            next_payload = {
                "from": "f1",
                "work_ms": work_ms,
                "original_request": payload,
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                f2_response = await client.post(f2_url, json=next_payload)

            chain_duration_ms = round((time.time() - chain_start) * 1000, 2)

            result = {
                "function": "f1",
                "message": "function chain completed",
                "hostname": socket.gethostname(),
                "vm_floating_ip": os.getenv("VM_FLOATING_IP", "unknown"),
                "work_ms": work_ms,
                "chain_duration_ms": chain_duration_ms,
                "f2_response": f2_response.json(),
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
