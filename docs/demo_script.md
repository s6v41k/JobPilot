# Demo script (15-minute slot, about 10 minutes of live demo)

**Golden rule: the demo must not crash. Stability beats features.** Every step below has a plan B, and plan B is
always *honest* - we never pretend a recording is a live answer.

Roles: **Driver** (keyboard), **Speaker** (talks, watches the clock), **Backup** (reads the screen for trouble, runs plan B commands).

---

## 1. Before the demo

### The day before
```powershell
.venv\Scripts\python -m app.demo prepare                 # fresh vacancies + fresh real snapshot + warm-up + pre-flight
.venv\Scripts\python -m app.demo record --scenario S1    # record the real runs once, on THIS laptop (also S2, S3, S4)
.venv\Scripts\python -m app.demo list                    # every scenario should say "recorded"
.venv\Scripts\python -m app.preflight --chat             # must end with READY
.venv\Scripts\python -m pytest                           # no failures
.venv\Scripts\python -m uitest.smoke                     # all checks passed
```
Upload the (fictional or your own) CV in **Profile**, press *Analyze my CV*, correct the profile, and leave it saved.
Commit the new recordings (`data/demo_recordings/`) and the new snapshot (`data/snapshots/`) so nobody loses them.

### One hour before
- Laptop **plugged in**, power mode "best performance", sleep and notifications **off**. Close every other program (RAM is shared with the model).
- Start Ollama (tray icon), then `.venv\Scripts\python -m app.main` and open http://127.0.0.1:8000 in the browser.
- Wait until the status bar shows the model name **without** "loading...". Open **Profile -> System check -> Quick check**: it must say READY.
- Run scenario S1 once as a rehearsal (it also warms everything), then press **New chat**.
- Open a second terminal in the project folder (plan B), and keep `docs/demo_script.md` open on another screen.

### Five minutes before
`New chat`, browser zoom 110-125 % so the back row can read, close the Demo panel, go to the **Chat** tab.

---

## 2. The script

| Time | Screen | Driver does | Speaker says |
|---|---|---|---|
| 0:00 - 1:00 | slide | - | The problem: a student in Belgium searches jobs on many sites in three languages, and nobody tells them which job fits *them*. JobPilot is a local AI agent: it runs on this laptop, reads the CV, searches **real** vacancies and decides by itself which tools to use. |
| 1:00 - 1:45 | Profile | open **Profile**, scroll to the profile form, then **System check -> Quick check** | The CV never leaves the laptop. The agent works from this profile, which the student can correct. The check shows Ollama, the models and the data are ready. |
| 1:45 - 5:15 | Chat | click **Demo**, **S1 - Run live** (or type the prompt) | *"Find student jobs in Brussels and tell me which one fits my CV best."* Nobody told it to compare with the CV - watch the chips: it **searches**, then **matches** all five vacancies by itself. (While the model works: "a 7-billion-parameter model on a laptop CPU, no cloud".) |
| 5:15 - 6:15 | Chat | expand **Tool trace** | Every decision is visible: which tool, which arguments, how long. The cards and scores come from the *tool results*, not from the model's text. |
| 6:15 - 7:30 | Chat | **S2 - Run live**: *"Zoek een stage in Antwerpen"* | Answers in Dutch because I wrote in Dutch. Same agent, other language. |
| 7:30 - 9:00 | Chat | **S4 - Run live**: *"Is there a job as an astronaut in Mechelen?"* | When there is nothing, it says so. If a model ever invents a vacancy, the answer is **thrown away** before you see it (show the guard in the slide/Q&A). |
| 9:00 - 10:30 | Jobs | open **Jobs**, filter *Internship*, tick **Match with my CV**, **Search**; click **Letter** on the best card, *Write the letter* | The Jobs tab needs no model: filters on 24 000 real offers from Le Forem. With the match score each card says why. The letter is short, in the language of the ad, and uses only facts from the CV. (Start the letter, keep talking while it writes.) |
| 10:30 - 11:00 | Tracker | open **Tracker**, move a card to *Applied* | Saved vacancies become a small kanban. |
| 11:00 - 12:30 | slide | - | Model selection: we compared several models (speed, correct tools, honesty) and chose ... *(fill in with the benchmark result)*. |
| 12:30 - 15:00 | - | - | Questions. |

The live part is deliberately **under 10 minutes**; the rest is buffer for a slow model.

If the model is slower than planned, **skip S4 first, then S2** (they are the optional ones). S1 and the Jobs tab are the core.

---

## 3. When something goes wrong (plan B)

Decide in **10 seconds**. Speaker keeps talking; Backup runs the fix.

| Problem | What the audience sees | Do this |
|---|---|---|
| No first answer after **90 s** | spinner "Thinking... 95 s" | Driver clicks **Demo -> Replay** for that scenario. Speaker says it plainly: *"The laptop is slow today, this is a recording of a real run from <date>"* - the screen shows the **REPLAY** label. |
| The model gives a wrong or odd answer | text | Keep calm: *"small local model; that is why the app checks answers against the tool results."* Then **Replay** or move on. |
| Ollama stopped / "Cannot reach Ollama" | red banner (it explains itself) | Backup starts Ollama from the tray; Jobs and Tracker keep working meanwhile - switch to them. Then `python -m app.preflight`. |
| No internet / Wi-Fi gone | nothing - searches use the **local** vacancy list | Carry on. (The status bar shows how old the list is.) |
| Vacancy list empty or very old | badge says *Cached real data* / *Saved snapshot* | Fine: real data, honestly labelled. Say so. |
| The web page freezes or shows an error | browser | Reload the page (F5). Server still down? Backup: `.venv\Scripts\python -m app.main` again; wait for the model name to appear. |
| The whole web app is dead | - | Terminal plan C: `.venv\Scripts\python -m app.agent_minimal` (the 50-line agent from the course) and type the S1 question. Or `python -m app.cli`. |
| Everything is dead | - | Show the recorded screen capture / screenshots (`eval/results/ui/`) and the code, and explain the architecture from `docs/development_log.md`. |

**Never**: restart the laptop mid-demo, install anything, or edit code on stage.

---

## 4. Scenarios (the same prompts as in the app's Demo panel)

| ID | Prompt | Tools we expect | Live time |
|---|---|---|---|
| S1 | Find student jobs in Brussels and tell me which one fits my CV best | `search_jobs`, `match_cv` | ~2.5 min (measured 122 s) |
| S2 | Zoek een stage in Antwerpen | `search_jobs` | ~1 min (48 s) |
| S3 | Chercher un job étudiant à Liège | `search_jobs` | ~1 min (23 s) |
| S4 | Is there a job as an astronaut in Mechelen? | `search_jobs` | ~1 min (40 s) |

`python -m app.demo list` shows which ones have a recording. A recording is a **real** run (every token and tool result, with the original timing);
a replay shortens the waits but changes nothing else, and the UI always shows the REPLAY label with the date and the model.

---

## 5. Likely questions

- **Why run locally?** Privacy (the CV never leaves the laptop), no cost, works offline.
- **Where do the vacancies come from?** Le Forem's official open data (CC BY-SA 4.0): about 24 000 current offers, also covering Flanders and Brussels through partner agencies. Adzuna is only a last resort (25 free calls a month).
- **Why not scrape Jobat or VDAB?** They block scripts (HTTP 403); we do not circumvent that.
- **Can the AI invent vacancies?** Small models try. Every answer is checked: links, ids and scores must come from a tool result, otherwise the answer is discarded and the model must search.
- **Why is it slow?** It runs a 7B model on a laptop CPU (about 5 tokens/s). That is why we warm the model up and compared smaller ones (see the model-selection slide).
- **What is the "agent"?** An LLM in a loop: it decides which tool to call (search, match, letter, tracker), reads the result and decides the next step - at most 6 rounds.
