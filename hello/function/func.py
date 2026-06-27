# Function
import json
import logging
import os
import socket
import time

from flask import Flask, request
from asgiref.wsgi import WsgiToAsgi

flask_app = Flask(__name__)


@flask_app.route('/', methods=['GET', 'POST'])
def hello():
    name = request.args.get('name', 'world')
    work = int(request.args.get('work', '0'))

    # Simulate CPU work if requested
    work_start = time.time()
    if work > 0:
        result = 0
        for i in range(work * 1_000_000):
            result += i
    work_duration = time.time() - work_start

    response = {
        "message": f"Hello, {name}",
        "pod": os.environ.get('HOSTNAME', 'unknown'),
        "hostname": socket.gethostname(),
        "vm_floating_ip": os.environ.get('VM_FLOATING_IP', 'not-set'),
        "work_requested": work,
        "work_duration_seconds": round(work_duration, 3),
    }

    return json.dumps(response, indent=2) + "\n", 200, {"Content-Type": "application/json"}


asgi_app = WsgiToAsgi(flask_app)


def new():
    return Function()


class Function:
    def __init__(self):
        pass

    async def handle(self, scope, receive, send):
        await asgi_app(scope, receive, send)

    def start(self, cfg):
        logging.info("Function starting")

    def stop(self):
        logging.info("Function stopping")

    def alive(self):
        return True, "Alive"

    def ready(self):
        return True, "Ready"
