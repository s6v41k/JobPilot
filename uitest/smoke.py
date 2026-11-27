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

        b.js("[...document.querySelectorAll('.tabs button')][3].click()")
        b.pump(0.6)
        check(b.js("getComputedStyle(document.querySelector('.page')).display") == "none", "switching tabs hides the chat")
        b.js("[...document.querySelectorAll('.tabs button')][0].click()")
        b.js("[...document.querySelectorAll('button')].find(x => x.innerText === 'New chat').click()")
        b.pump(0.6)
        check(b.js("document.querySelectorAll('.msg').length") == 0, "New chat clears the conversation")
        check(not b.problems, f"no console errors or exceptions {b.problems or ''}")
    finally:
        b.close()


def set_value(b: Browser, selector: str, value: str, event: str = "input") -> None:
    """Change a form field the way a user would (so Vue's v-model notices)."""
    b.js(f"(() => {{ const e = document.querySelector({json.dumps(selector)}); e.value = {json.dumps(value)}; "
         f"e.dispatchEvent(new Event({json.dumps(event)}, {{bubbles: true}})); }})()")


def click(b: Browser, selector: str, text: str | None = None) -> None:
    if text is None:
        b.js(f"document.querySelector({json.dumps(selector)}).click()")
    else:
        b.js(f"[...document.querySelectorAll({json.dumps(selector)})].find(e => e.innerText.trim() === {json.dumps(text)}).click()")


def count(b: Browser, selector: str) -> int:
    return b.js(f"document.querySelectorAll({json.dumps(selector)}).length")


def tab_button(b: Browser, name: str) -> None:
    click(b, ".tabs button", name)
    b.pump(0.4)


def tabs_flow(port: int) -> None:
    """Jobs, Tracker and Profile tabs."""
    b = Browser(port=9336)
    base = f"http://127.0.0.1:{port}/"
    api = lambda path: json.load(urllib.request.urlopen(base + path))          # noqa: E731
    try:
        b.goto(base, wait=2.5)

        # ---------------------------------------------------------------- Jobs
        tab_button(b, "Jobs")
        check(b.wait_for("document.querySelectorAll('.cards .job').length > 0", timeout=30), "Jobs: opening the tab shows vacancies")
        check("vacanc" in text(b, ".resultbar") and "Le Forem" in text(b, ".resultbar"), "Jobs: result bar shows the count and the source")
        b.screenshot(SHOTS / "6_jobs.png")

        set_value(b, "select[aria-label='Type of work']", "internship", "change")
        click(b, ".filters .btn.primary")
        b.wait_for("!document.querySelector('.loading')")
        types = b.js("[...document.querySelectorAll('.cards .tag.type')].map(e => e.innerText)")
        check(types and set(types) == {"internship"}, f"Jobs: the internship filter only returns internships ({len(types)} cards)")

        set_value(b, "input[aria-label='Keywords']", "zzzzqq")
        click(b, ".filters .btn.primary")
        b.wait_for("!!document.querySelector('.nothing')", timeout=20)
        check("No vacancies found" in text(b, ".nothing"), "Jobs: no results gives a helpful message, not a blank page")
        click(b, ".filters .btn", "Reset")
        check(b.wait_for("document.querySelectorAll('.cards .job').length > 0", timeout=20), "Jobs: Reset brings the vacancies back")

        check(not b.js("document.querySelector('.filters input[type=checkbox]').disabled"), "Jobs: 'Match with my CV' is available when a profile exists")
        click(b, ".filters input[type=checkbox]")
        click(b, ".filters .btn.primary")
        check(b.wait_for("document.querySelectorAll('.cards .score').length > 0", timeout=90), "Jobs: matching adds a score to the cards")
        scores = [int(s) for s in b.js("[...document.querySelectorAll('.cards .score')].map(e => e.innerText)")]
        check(scores == sorted(scores, reverse=True), "Jobs: cards are sorted by match score")

        click(b, ".cards .job .btn", "Details")
        check(count(b, ".cards .job .desc") == 1, "Jobs: Details shows the description")
        click(b, ".cards .job .btn.primary")
        b.pump(0.8)
        check(text(b, ".cards .job .btn.primary") == "Saved", "Jobs: Save works from the Jobs tab")

        click(b, ".cards .job .btn", "Letter")
        check(count(b, ".modal") == 1, "Jobs: the Letter button opens a dialog")
        click(b, ".modal .btn.primary")
        check(b.wait_for("!!document.querySelector('.letter-text')", timeout=20), "Jobs: the letter is written and shown")
        check("Dear hiring team" in text(b, ".letter-text"), "Jobs: the letter text is displayed")
        b.screenshot(SHOTS / "7_letter_dialog.png")
        click(b, ".modal .btn", "Close")
        check(count(b, ".modal") == 0, "Jobs: the dialog closes")

        # -------------------------------------------------------------- Tracker
        tab_button(b, "Tracker")
        b.wait_for("document.querySelectorAll('.board .job').length > 0", timeout=20)
        # the chat test already saved one vacancy and the Jobs test saved another
        check(count(b, "[data-status=interested] .job") == 2, "Tracker: both saved vacancies are in the 'Interested' column")
        set_value(b, "[data-status=interested] select", "applied", "change")
        check(b.wait_for("document.querySelectorAll('[data-status=applied] .job').length === 1", timeout=20)
              and count(b, "[data-status=interested] .job") == 1, "Tracker: changing the status moves the card")
        set_value(b, "[data-status=applied] input.note", "Call on Monday", "change")
        b.pump(1.0)
        notes = [a["notes"] for a in api("api/applications")["applications"]]
        check("Call on Monday" in notes, "Tracker: notes are saved")
        b.screenshot(SHOTS / "8_tracker.png")
        click(b, "[data-status=applied] .btn", "Remove")
        check(b.wait_for("document.querySelectorAll('.board .job').length === 1", timeout=20), "Tracker: Remove deletes the card")
        click(b, ".board .btn", "Remove")
        b.wait_for("document.querySelectorAll('.board .job').length === 0", timeout=20)
        check("Nothing tracked yet" in text(b, ".hint"), "Tracker: an empty board explains what to do")

        # -------------------------------------------------------------- Profile
        tab_button(b, "Profile")
        b.wait_for("!!document.querySelector('.profile')", timeout=20)
        check("Emma" in (b.js("document.querySelector(\".profile input[maxlength='80']\").value") or ""), "Profile: the saved profile is loaded into the form")
        check("Customer service" in b.js("[...document.querySelectorAll('.profile textarea')].map(e => e.value).join('|')"), "Profile: skills are listed one per line")
        set_value(b, ".profile textarea[maxlength='400']", "Edited by the test.")
        click(b, ".panel .btn.primary", "Save profile")
        b.pump(1.0)
        check(api("api/profile")["profile"]["summary"] == "Edited by the test.", "Profile: edits are saved")

        set_value(b, "textarea[aria-label='Paste CV text']", "Lena Peeters, student of communication in Mechelen with two years of event experience.")
        click(b, ".panel .btn", "Save pasted text")
        b.pump(1.0)
        check(api("api/profile")["has_cv"] is True, "Profile: pasted CV text is stored")
        click(b, ".panel .btn.primary", "Analyze my CV")
        check(b.wait_for("document.querySelector(\".profile input[maxlength='80']\").value === 'Scripted Student'", timeout=20), "Profile: Analyze fills the form with the extracted profile")

        set_value(b, ".panel select", "llama3.2:3b", "change")
        click(b, ".panel .btn.primary", "Use this model")
        b.wait_for("document.body.innerText.includes('Active: llama3.2:3b')", timeout=20)
        check("llama3.2:3b" in text(b, ".pill"), "Profile: switching the model updates the status bar")
        click(b, ".panel .btn", "Refresh now")
        check(b.wait_for("document.body.innerText.includes('vacancies downloaded')", timeout=20), "Profile: 'Refresh now' downloads the vacancy list")
        click(b, ".panel .btn", "Quick check")
        check(b.wait_for("document.querySelectorAll('.checks li').length >= 10", timeout=60), "Profile: the system check lists its checks")
        check(any(w in text(b, ".verdict") for w in ("READY", "NOT READY")), "Profile: the system check gives a verdict")
        b.screenshot(SHOTS / "9_profile.png")

        click(b, ".panel .btn", "Delete my data...")
        check("for good" in text(b, ".panel.danger"), "Profile: deleting asks for confirmation first")
        click(b, ".panel .btn.danger")
        b.pump(1.2)
        data = api("api/profile")
        check(data["profile"] is None and data["has_cv"] is False, "Profile: 'Delete my data' removes the CV and the profile")
        check(not b.problems, f"no console errors or exceptions {b.problems or ''}")
    finally:
        b.close()


def demo_flow(port: int) -> None:
    """The Demo panel: scenarios, live run and the labelled replay of a recording."""
    b = Browser(port=9337)
    try:
        b.goto(f"http://127.0.0.1:{port}/", wait=2.5)
        click(b, ".linkbtn")
        check(count(b, ".demo-row") == 4, "Demo: the panel lists the four scripted scenarios")
        replay_enabled = b.js("[...document.querySelectorAll('.demo-row')].map(r => !r.querySelectorAll('.btn')[1].disabled)")
        check(replay_enabled == [True, False, False, False], "Demo: Replay is only enabled where a recording exists")
        b.screenshot(SHOTS / "10_demo_panel.png")
        b.js("document.querySelectorAll('.demo-row')[0].querySelectorAll('.btn')[1].click()")
        check(b.wait_for("!!document.querySelector('.replay-badge')", timeout=20), "Demo: a replay shows the REPLAY label")
        check(b.wait_for("!!document.querySelector('.send') && document.querySelectorAll('.job').length > 0", timeout=60), "Demo: the replay finishes and shows the vacancy cards")
        badge = text(b, ".replay-badge")
        check("REPLAY" in badge and "real recording" in badge and "scripted-model" in badge, f"Demo: the label names it a recording and the model ({badge[:70]})")
        scores = b.js("[...document.querySelectorAll('.score')].map(e => e.innerText)")
        check(len(scores) == 5, "Demo: scores from the recorded tool results are shown")
        click(b, ".job .btn.primary")
        b.pump(0.8)
        check(text(b, ".job .btn.primary") == "Saved", "Demo: a vacancy from a replay can be saved (it was stored for the replay)")
        b.screenshot(SHOTS / "11_replay.png")
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
        tabs_flow(8011)
        demo_flow(8011)
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
