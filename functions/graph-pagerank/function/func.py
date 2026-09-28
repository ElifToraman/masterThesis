from __future__ import annotations

import logging

from asgiref.wsgi import WsgiToAsgi
from flask import Flask, jsonify, request

from .workload import run_graph_pagerank


flask_app = Flask(__name__)


@flask_app.route("/", methods=["GET", "POST"])
def invoke():
    payload = request.get_json(silent=True) or {}

    if request.method == "GET":
        payload = {
            "size": request.args.get("size", 10_000, type=int),
            "seed": request.args.get("seed", 42, type=int),
        }

    try:
        result = run_graph_pagerank(
            size=payload.get("size", 10_000),
            seed=payload.get("seed", 42),
        )
    except ValueError as error:
        return jsonify({"success": False, "error": str(error)}), 400

    return jsonify(result), 200


asgi_app = WsgiToAsgi(flask_app)


def new():
    return Function()


class Function:
    async def handle(self, scope, receive, send):
        await asgi_app(scope, receive, send)

    def start(self, cfg):
        logging.info("graph-pagerank function starting")

    def stop(self):
        logging.info("graph-pagerank function stopping")

    def alive(self):
        return True, "Alive"

    def ready(self):
        return True, "Ready"
