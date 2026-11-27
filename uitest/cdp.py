"""A tiny browser driver: Microsoft Edge (headless) controlled through the Chrome DevTools Protocol.

It uses only the `websockets` package that already comes with uvicorn, so the UI can be checked
without installing Selenium or Playwright. Edge is pre-installed on Windows.
"""
from __future__ import annotations

import base64
import json
import subprocess
import tempfile
import time
import urllib.request

from websockets.sync.client import connect

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


class Browser:
    def __init__(self, port: int = 9333, width: int = 1280, height: int = 900):
        self.profile = tempfile.mkdtemp()
        self.proc = subprocess.Popen(
            [EDGE, "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={self.profile}",
             f"--window-size={width},{height}", "--no-first-run", "--disable-gpu", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        page = None
        for _ in range(60):
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=1))
                page = next(t for t in targets if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.5)
        if page is None:
            raise RuntimeError("Edge did not start (is it installed at the usual place?)")
        self.ws = connect(page["webSocketDebuggerUrl"], max_size=50_000_000)
        self.n = 0
        self.problems: list[tuple[str, str]] = []        # console errors and exceptions seen by the page
        for domain in ("Page", "Runtime", "Log"):
            self.call(f"{domain}.enable")
        self.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)

    # ---- protocol plumbing
    def call(self, method: str, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv(timeout=60))
            self._note(msg)
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    def _note(self, msg: dict) -> None:
        kind, p = msg.get("method"), msg.get("params", {})
        if kind == "Runtime.consoleAPICalled" and p["type"] in ("error", "warning"):
            text = " ".join(str(a.get("value", a.get("description", ""))) for a in p["args"])
            self.problems.append(("console." + p["type"], text))
        elif kind == "Runtime.exceptionThrown":
            d = p["exceptionDetails"]
            self.problems.append(("exception", d.get("exception", {}).get("description", d.get("text", ""))))
        elif kind == "Log.entryAdded" and p["entry"]["level"] == "error":
            self.problems.append(("log.error", p["entry"]["text"] + " " + p["entry"].get("url", "")))

    def pump(self, seconds: float) -> None:
        """Let the page run for a while (and collect its events)."""
        end = time.time() + seconds
        while time.time() < end:
            try:
                self._note(json.loads(self.ws.recv(timeout=0.3)))
            except TimeoutError:
                pass

    # ---- things a test wants to do
    def goto(self, url: str, wait: float = 1.5) -> None:
        self.call("Page.navigate", url=url)
        self.pump(wait)

    def js(self, expression: str):
        r = self.call("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description", "javascript error"))
        return r["result"].get("value")

    def wait_for(self, expression: str, timeout: float = 60, step: float = 0.4) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self.js(expression):
                return True
            self.pump(step)
        return False

    def type(self, text: str) -> None:
        self.call("Input.insertText", text=text)

    def enter(self) -> None:
        for kind in ("keyDown", "keyUp"):
            self.call("Input.dispatchKeyEvent", type=kind, key="Enter", code="Enter", windowsVirtualKeyCode=13,
                      text="\r" if kind == "keyDown" else "")

    def screenshot(self, path) -> None:
        open(path, "wb").write(base64.b64decode(self.call("Page.captureScreenshot", format="png")["data"]))

    def close(self) -> None:
        try:
            self.call("Browser.close")
        except Exception:
            pass
        self.proc.terminate()
