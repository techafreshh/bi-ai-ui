"""Fake OpenAI-compatible streaming server for contract tests.

Speaks just enough of `POST {base_url}/chat/completions` (SSE chunks + DONE)
for langchain's ChatOpenAI streaming client. The server also records each
request's Authorization header and parsed body so tests can assert the
OpenRouter-shaped request contract.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeOpenAICompatServer:
    def __init__(self, reply: str = "pong") -> None:
        self.reply = reply
        self.requests: list[dict] = []  # {"path", "authorization", "body"}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                raw_body = self.rfile.read(length) if length else b"{}"
                outer.requests.append(
                    {
                        "path": self.path,
                        "authorization": self.headers.get("authorization"),
                        "body": json.loads(raw_body.decode("utf-8")),
                    }
                )
                if not self.path.endswith("/chat/completions"):
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for token in outer.reply:
                    chunk = {
                        "id": "chatcmpl-fake",
                        "object": "chat.completion.chunk",
                        "choices": [
                            {"index": 0, "delta": {"content": token}, "finish_reason": None}
                        ],
                    }
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                final = {
                    "id": "chatcmpl-fake",
                    "object": "chat.completion.chunk",
                    "choices": [
                        {"index": 0, "delta": {}, "finish_reason": "stop"}
                    ],
                }
                self.wfile.write(f"data: {json.dumps(final)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")

            def log_message(self, *args):
                pass  # keep test output quiet

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "FakeOpenAICompatServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/v1"
