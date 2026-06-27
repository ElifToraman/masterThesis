import json
import os
import socket
import time


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

        duration_ms = round((time.time() - start) * 1000, 2)

        result = {
            "function": "f3",
            "message": "final function completed",
            "hostname": socket.gethostname(),
            "vm_floating_ip": os.getenv("VM_FLOATING_IP", "unknown"),
            "work_ms": work_ms,
            "duration_ms": duration_ms,
            "input": payload,
        }

        response_body = json.dumps(result).encode()

        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [[b"content-type", b"application/json"]],
        })
        await send({
            "type": "http.response.body",
            "body": response_body,
        })