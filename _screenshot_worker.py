"""Dedicated screenshot worker that waits for a stable page before capture."""

import base64
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

READY_TIMEOUT_SECONDS = 20
STABLE_POLLS_REQUIRED = 3
MAX_CAPTURE_HEIGHT = 30000


def find_free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def cdp_request(ws_url, method, params=None, msg_id=1):
    import websocket

    ws = websocket.create_connection(ws_url, timeout=30)
    ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") != msg_id:
            continue
        ws.close()
        if "error" in msg:
            raise RuntimeError(msg["error"].get("message", f"CDP error: {method}"))
        return msg.get("result", {})


def get_ws_url(host, port, retries=10):
    for _ in range(retries):
        try:
            conn = http.client.HTTPConnection(f"{host}:{port}", timeout=5)
            conn.request("GET", "/json/list")
            tabs = json.loads(conn.getresponse().read())
            if tabs:
                return tabs[0]["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("Chrome CDP not ready")


def eval_json(ws_url, expression, *, msg_id, await_promise=False):
    result = cdp_request(
        ws_url,
        "Runtime.evaluate",
        {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": await_promise,
        },
        msg_id=msg_id,
    )
    value = result.get("result", {}).get("value")
    if isinstance(value, str):
        return json.loads(value)
    return value


def wait_for_ready_state(ws_url):
    deadline = time.time() + READY_TIMEOUT_SECONDS
    while time.time() < deadline:
        state = eval_json(
            ws_url,
            "JSON.stringify({state: document.readyState})",
            msg_id=100,
        ).get("state")
        if state == "complete":
            return
        time.sleep(0.2)
    raise RuntimeError("Timed out waiting for document.readyState=complete")


def wait_for_fonts(ws_url):
    try:
        cdp_request(
            ws_url,
            "Runtime.evaluate",
            {
                "expression": """
                    (() => {
                        if (!document.fonts || document.fonts.status === 'loaded') {
                            return Promise.resolve(true);
                        }
                        return document.fonts.ready.then(() => true);
                    })()
                """,
                "returnByValue": True,
                "awaitPromise": True,
            },
            msg_id=101,
        )
    except Exception:
        pass


def wait_for_layout_stable(ws_url):
    deadline = time.time() + READY_TIMEOUT_SECONDS
    last_height = None
    stable_polls = 0
    last_metrics = {"width": 0, "height": 0}
    while time.time() < deadline:
        metrics = eval_json(
            ws_url,
            """
            JSON.stringify((() => {
                const de = document.documentElement;
                const body = document.body;
                const widths = [
                    de ? de.scrollWidth : 0,
                    de ? de.offsetWidth : 0,
                    de ? de.clientWidth : 0,
                    body ? body.scrollWidth : 0,
                    body ? body.offsetWidth : 0,
                    body ? body.clientWidth : 0,
                    Math.ceil(de && de.getBoundingClientRect ? de.getBoundingClientRect().width : 0),
                    Math.ceil(body && body.getBoundingClientRect ? body.getBoundingClientRect().width : 0),
                    window.innerWidth || 0,
                ].filter(n => Number.isFinite(n));
                const heights = [
                    de ? de.scrollHeight : 0,
                    de ? de.offsetHeight : 0,
                    de ? de.clientHeight : 0,
                    body ? body.scrollHeight : 0,
                    body ? body.offsetHeight : 0,
                    body ? body.clientHeight : 0,
                    Math.ceil(de && de.getBoundingClientRect ? de.getBoundingClientRect().height : 0),
                    Math.ceil(body && body.getBoundingClientRect ? body.getBoundingClientRect().height : 0),
                    window.innerHeight || 0,
                ].filter(n => Number.isFinite(n));
                return {
                    width: Math.max(...widths, 0),
                    height: Math.max(...heights, 0),
                    readyState: document.readyState,
                    chartsReady: window._chartsReady !== false,
                };
            })())
            """,
            msg_id=102,
        )
        last_metrics = metrics
        current_height = int(metrics.get("height") or 0)
        if current_height > 0 and metrics.get("readyState") == "complete" and metrics.get("chartsReady", True):
            if current_height == last_height:
                stable_polls += 1
            else:
                last_height = current_height
                stable_polls = 0
            if stable_polls >= STABLE_POLLS_REQUIRED:
                return metrics
        time.sleep(0.25)
    return last_metrics


def get_content_metrics(ws_url):
    result = cdp_request(ws_url, "Page.getLayoutMetrics", msg_id=103)
    content = result.get("contentSize") or {}
    return int(content.get("width") or 0), int(content.get("height") or 0)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(json.dumps({"status": "error", "msg": "Usage: worker.py <file_url> <out_path> [width] [chrome_path]"}))
        sys.exit(1)

    file_url = sys.argv[1]
    out_path = sys.argv[2]
    width = int(sys.argv[3]) if len(sys.argv) > 3 else 1440
    chrome_path = sys.argv[4] if len(sys.argv) > 4 else None

    chrome_candidates = [
        chrome_path,
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    chrome = next((path for path in chrome_candidates if path and os.path.exists(path)), None)
    if not chrome:
        print(json.dumps({"status": "error", "msg": "Chrome not found"}))
        sys.exit(1)

    port = find_free_port()
    tmp_dir = tempfile.mkdtemp(prefix="chrome_")
    proc = subprocess.Popen(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--hide-scrollbars",
            "--disable-web-security",
            "--allow-file-access-from-files",
            "--run-all-compositor-stages-before-draw",
            "--disable-extensions",
            "--no-first-run",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={tmp_dir}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        ws_url = get_ws_url("127.0.0.1", port)
        cdp_request(ws_url, "Page.enable", msg_id=1)
        cdp_request(ws_url, "Runtime.enable", msg_id=2)

        cdp_request(
            ws_url,
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": 900, "deviceScaleFactor": 1, "mobile": False},
            msg_id=3,
        )
        cdp_request(ws_url, "Page.navigate", {"url": file_url}, msg_id=4)

        wait_for_ready_state(ws_url)
        wait_for_fonts(ws_url)
        metrics = wait_for_layout_stable(ws_url)
        layout_width, layout_height = get_content_metrics(ws_url)

        page_width = max(width, int(metrics.get("width") or 0), layout_width)
        page_height = max(900, int(metrics.get("height") or 0), layout_height)
        page_height = min(page_height, MAX_CAPTURE_HEIGHT)

        cdp_request(
            ws_url,
            "Emulation.setDeviceMetricsOverride",
            {
                "width": page_width,
                "height": page_height,
                "deviceScaleFactor": 1,
                "mobile": False,
            },
            msg_id=5,
        )
        time.sleep(0.3)

        shot = cdp_request(
            ws_url,
            "Page.captureScreenshot",
            {
                "format": "png",
                "captureBeyondViewport": True,
                "fromSurface": True,
                "clip": {
                    "x": 0,
                    "y": 0,
                    "width": page_width,
                    "height": page_height,
                    "scale": 1,
                },
            },
            msg_id=6,
        )
        img_data = shot.get("data", "")
        if not img_data:
            raise RuntimeError("Empty screenshot data")

        with open(out_path, "wb") as f:
            f.write(base64.b64decode(img_data))

        print(json.dumps({"status": "ok", "path": out_path}))
    except Exception as exc:
        print(json.dumps({"status": "error", "msg": str(exc)}))
        sys.exit(1)
    finally:
        proc.terminate()
        shutil.rmtree(tmp_dir, ignore_errors=True)
