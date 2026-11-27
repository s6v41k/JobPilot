"""Click through the web UI in a real (headless) browser and check what a student would see.

    python -m uitest.smoke

Starts the scripted server twice (normal / "Ollama down"), drives Edge, prints PASS/FAIL per check,
saves screenshots to eval/results/ui/ and exits with a non-zero code if anything failed.
Run it before every demo rehearsal. Needs Microsoft Edge (pre-installed on Windows).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request

from app.config import ROOT_DIR
from uitest.cdp import Browser

SHOTS = ROOT_DIR / "eval" / "results" / "ui"
results: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> None:
    results.append((bool(ok), what))
    print(("PASS  " if ok else "FAIL  ") + what)


def start_server(port: int, mode: str = "normal") -> subprocess.Popen:
    proc = subprocess.Popen([sys.executable, "-m", "uitest.scripted_server", str(port), mode], cwd=ROOT_DIR,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
            return proc
        except Exception:
            time.sleep(0.5)
    proc.terminate()
    raise RuntimeError(f"test server on port {port} did not start")


def text(browser: Browser, selector: str) -> str:
    return browser.js(f"(document.querySelector('{selector}') || {{innerText: ''}}).innerText") or ""


def chat_flow(port: int) -> None:
    b = Browser(port=9333)
    base = f"http://127.0.0.1:{port}/"
    try:
        b.goto(base, wait=2.5)
        check(b.js("document.title") == "JobPilot", "page loads and shows the JobPilot title")
        check(b.js("document.querySelectorAll('.suggestions button').length") == 4, "empty chat offers 4 suggestions")
        check("qwen2.5:7b" in text(b, ".pill"), "status bar shows the active model")
        b.screenshot(SHOTS / "1_empty.png")

        b.js("document.querySelector('textarea').focus()")
        b.type("Find student jobs in Brussels and tell me which fits my CV")
        b.enter()
        b.pump(1.0)
        check(b.js("!!document.querySelector('.thinking')"), "shows a 'thinking' indicator while the model works")
        check(b.js("!!document.querySelector('.stop')"), "shows a Stop button while busy")
        check(b.wait_for("!!document.querySelector('.send') && document.querySelectorAll('.job').length > 0"),
              "conversation finishes and vacancy cards appear")
        check(b.js("document.querySelector('textarea').value") == "", "the input is cleared after sending")
        chips = b.js("[...document.querySelectorAll('.chip b')].map(e => e.innerText)")
        check(chips == ["search_jobs", "match_cv"], f"tool chips show the agent's decisions (got {chips})")
        scores = [int(s) for s in b.js("[...document.querySelectorAll('.score')].map(e => e.innerText)")]
        check(len(scores) == 5 and scores == sorted(scores, reverse=True), f"cards carry match scores, best first {scores}")
        check("Le Forem" in text(b, ".foot"), "the data source / attribution is shown")
        check("Live data" in text(b, ".badge"), "the data badge says 'Live data'")

        b.js("document.querySelector('details.trace').open = true")
        trace = text(b, "details.trace")
        check("search_jobs" in trace and "match_cv" in trace and "job_ids" in trace, "tool trace lists calls with arguments")
        b.screenshot(SHOTS / "2_chat.png")

        b.js("document.querySelector('.job .btn.primary').click()")
        b.pump(1.0)
        saved = json.load(urllib.request.urlopen(base + "api/applications"))["count"]
        check(saved == 1 and text(b, ".job .btn.primary") == "Saved", "Save puts the vacancy into the tracker")

        b.js("document.querySelector('textarea').focus()")
        b.type("Please write a cover letter for the waiter job")
        b.enter()
        check(b.wait_for("!!document.querySelector('.letter')", timeout=30), "a cover letter card appears")
        check("Dear hiring team" in text(b, ".letter"), "the letter text is shown")
        b.js("document.querySelector('.messages').scrollTop = document.querySelector('.messages').scrollHeight")
        b.pump(1.0)
        b.screenshot(SHOTS / "3_letter.png")

        b.js("[...document.querySelectorAll('.tabs button')][1].click()")
        b.pump(0.3)
        check(text(b, ".soon").startswith("Jobs") or text(b, ".soon").startswith("Jobs".strip()), "other tabs open without a blank page")
        b.js("[...document.querySelectorAll('.tabs button')][0].click()")
        b.js("[...document.querySelectorAll('button')].find(x => x.innerText === 'New chat').click()")
        b.pump(0.6)
        check(b.js("document.querySelectorAll('.msg').length") == 0, "New chat clears the conversation")
        check(not b.problems, f"no console errors or exceptions {b.problems or ''}")
    finally:
        b.close()


def phone_flow(port: int) -> None:
    b = Browser(port=9334, width=420, height=820)
    try:
        b.goto(f"http://127.0.0.1:{port}/", wait=2.5)
        b.js("document.querySelector('.suggestions button').click()")
        b.wait_for("!!document.querySelector('.send') && document.querySelectorAll('.job').length > 0")
        b.pump(1.5)
        check(not b.js("document.documentElement.scrollWidth > document.documentElement.clientWidth"),
              "no horizontal scrolling on a phone-sized screen")
        b.screenshot(SHOTS / "4_phone.png")
    finally:
        b.close()


def ollama_down_flow(port: int) -> None:
    b = Browser(port=9335, width=1100, height=700)
    try:
        b.goto(f"http://127.0.0.1:{port}/", wait=2.5)
        check("Ollama is not running" in text(b, ".banner"), "banner explains that Ollama is not running")
        check("Ollama offline" in text(b, ".pill"), "status bar says 'Ollama offline'")
        b.js("document.querySelector('textarea').focus()")
        b.type("hello")
        b.enter()
        check(b.wait_for("!!document.querySelector('.msg-error')", timeout=30), "asking while Ollama is down shows a friendly error")
        check("Cannot reach Ollama" in text(b, ".msg-error"), "the error message says what to do")
        check(b.js("!!document.querySelector('.send')"), "the chat is usable again afterwards")
        b.screenshot(SHOTS / "5_ollama_down.png")
        check(not b.problems, f"no console errors or exceptions {b.problems or ''}")
    finally:
        b.close()


def main() -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)
    servers = [start_server(8011), start_server(8012, "down")]
    try:
        chat_flow(8011)
        phone_flow(8011)
        ollama_down_flow(8012)
    finally:
        for server in servers:
            server.terminate()
    failed = [what for ok, what in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed. Screenshots: {SHOTS}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
