import json
import sys

from . import __version__, tools
from .db import connect


def response(request_id, result=None, error=None):
    payload = {"jsonrpc": "2.0", "id": request_id}
    if error:
        payload["error"] = error
    else:
        payload["result"] = result or {}
    return payload


def serve():
    conn = connect()
    try:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                if request.get("jsonrpc") != "2.0":
                    raise ValueError("需要 JSON-RPC 2.0")
                method = request.get("method")
                if method.startswith("notifications/"):
                    continue
                request_id = request.get("id")
                params = request.get("params") or {}
                if method == "initialize":
                    result = {"protocolVersion": params.get("protocolVersion", "2024-11-05"),
                              "capabilities": {"tools": {"listChanged": False}},
                              "serverInfo": {"name": "finance-audit-local", "version": __version__}}
                    message = response(request_id, result)
                elif method == "tools/list":
                    message = response(request_id, {"tools": tools.SPECS})
                elif method == "tools/call":
                    try:
                        result = tools.execute(conn, params.get("name"), params.get("arguments") or {})
                        text = json.dumps(result, ensure_ascii=False)
                        message = response(request_id, {"content": [{"type": "text", "text": text}]})
                    except Exception as exc:
                        message = response(request_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
                elif method == "ping":
                    message = response(request_id, {})
                else:
                    message = response(request_id, error={"code": -32601, "message": "Method not found"})
            except Exception as exc:
                message = response(locals().get("request", {}).get("id"), error={"code": -32602, "message": str(exc)})
            sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    finally:
        conn.close()


if __name__ == "__main__":
    serve()
