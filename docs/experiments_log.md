# Experiments log

Living notes: what we tried, what we measured, what we learned. New entries go at the bottom of
their section. Final conclusions are collected later in `model_selection.md`.

**Honesty rules.** Every number below says how it was measured. Most are *single runs* on one
laptop: they show where the problems are, not final rankings. The real benchmark (phase P4) will
repeat each case several times.

## Setup

| | |
|---|---|
| Machine | team laptop, 16 GB RAM, AMD Radeon integrated graphics, **no discrete GPU** |
| Ollama | 0.40.18 (`ollama` Python library 0.6.3) |
| Models installed | `qwen2.5:7b`, `llama3.2:3b`, `qwen3:4b` (thinking-only), `qwen3:4b-instruct`, `bge-m3` (embeddings) |
| Data | real vacancies: Le Forem open data (live), Adzuna (budget 25 calls/month) |
| Test CV | fictional: `data/sample_cvs/emma_verbeke.txt` |
| Reproduce | `python -m eval.run_scenario --model <name>` (see `testing.md`) |

---

## 1. Data sources (2026-11-27)

| Source | Result | Decision |
|---|---|---|
| Adzuna API (`be`) | 1 real call: 2070 hits for "student", we parsed 50. Descriptions **cut to 500 characters**. 10 of 50 adverts had **no location** (only "België"). | last resort, 25 calls/month budget counted in SQLite |
| **Le Forem open data** (ODWB dataset `offres-d-emploi-forem`) | official, free, licence CC BY-SA 4.0, ~25k offers. Contains **expired offers** (must filter on end date) and **no free-text description**. Also carries Flemish (3 575) and Brussels (1 725) offers relayed by partner agencies; Wallonia 19 522 (counts of non-expired offers on that day). | main source; attribution shown in the app |
| Jobat | HTTP 403 for scripts (bot protection) | not used, protection is not circumvented |
| VDAB | site API answers 403 without a key header that is embedded in their own web app; their official vacancy API is for employers only | not used |
| Actiris | no public API found | not used |
| StudentJob.be | list is loaded by JavaScript | maybe later, only if a clean route exists |

**Lesson:** the best source was an official open-data portal, found by searching for it, not by scraping.

## 2. Speed on this laptop (CPU only)

- `ollama ps` shows `qwen2.5:7b` with **0 MB in VRAM**: the AMD graphics are not used.
- Generation about **6 tokens/s** (`qwen2.5:7b`), 10-13 tokens/s (`llama3.2:3b`).
- A normal two-step answer (tool call, then written answer) was **~50-60 s** for `qwen2.5:7b`:
  about half generation, half reading the prompt.
- A repeated prompt prefix is cached by Ollama (800-token system prompt + tools read in 0.2-0.5 s
  when warm), so keeping the system prompt *identical* between turns matters.
- Shorter answers are faster: asking for one line per vacancy and no links saved ~60 s per answer.

**To try:** Ollama's experimental Vulkan backend for the AMD iGPU, smaller `num_ctx`, fewer tool results in the prompt.

## 3. Hallucinations seen in real runs

| # | Model | What happened | Fix (commit) |
|---|---|---|---|
| H1 | `qwen2.5:7b` | 2nd question of a chat: **no tool call**, but listed a vacancy with an invented link. Cause: the chat history kept only text, so the model copied the habit of answering without tools. | history now keeps compact tool calls; answers are checked: links/ids must appear in a tool result, otherwise the answer is discarded and the model must search (`d2be62b`) |
| H2 | `llama3.2:3b` | search failed (bad arguments), then it **listed 5 invented vacancies** with fake ids like "(123)" | guard extended to bracketed ids in lists (`1875e28`) |
| H3 | `llama3.2:3b` | search worked, then it wrote "according to match_cv, 85/100" **without ever calling match_cv** | guard checks every "N/100" against real `match_cv` results (`1875e28`) |

Typical argument mistakes of small models (all now tolerated or described in the tool schema):
`radius_km=0`, `limit="5"` (string), `language="en"` (the user's language instead of the ad's language),
filling every optional field.

## 4. CV profile extraction

- `qwen2.5:7b`, structured output with a JSON schema: **37-45 s** per CV.
- First version returned **empty `skills`, `languages` and `summary`** although the CV had them.
  Fix: stricter prompt plus a rule-based fallback that reads the "Skills / Languages / Profile"
  sections (`27a3a30`). After the fix both fictional CVs were extracted completely.

## 5. Match score calibration (`match_cv`)

Measured on the 108 real vacancies in `data/snapshots/forem_2026-11-27.json`, `bge-m3` embeddings:

| CV | cosine min | median | max |
|---|---|---|---|
| Emma Verbeke (business student) | 0.47 | 0.57 | 0.69 |
| Lucas Moreau (computer science) | 0.42 | 0.50 | 0.63 |

- Ranking made sense (Emma: waiter / student jobs on top, electricians at the bottom).
- First scale (0.30-0.70 -> 0-100) was too generous (80-96 for mediocre fits), so it is now 0.40-0.70,
  and a score based on a single signal is capped at 65.
- Embedding the 108 vacancies took ~55 s the first time on CPU; afterwards they come from the cache.
- Lucas' best matches are cashier jobs: the snapshot simply has no IT vacancies (data limit, not a bug).

## 6. Same task, different models

Task: *"Find student jobs in Brussels and tell me which one fits my CV best"* (CV: Emma Verbeke).
Needed behaviour: `search_jobs`, then `match_cv` with **all** ids, then an honest answer.

| Model | Wall time | tokens/s | Tool calls | Verdict |
|---|---|---|---|---|
| `qwen2.5:7b` | 204 s | 5.4 | search_jobs, match_cv (**3 of 5** ids) | correct and grounded, but compared only 3 vacancies |
| `qwen2.5:7b` (newer prompt, saved: `eval/results/scenario_qwen2.5-7b_20261127_121143.json`) | 189 s | 5.4 | search_jobs, match_cv (**4 of 5** ids) | grounded, best pick "Serveur" 60/100 is a real `match_cv` score; but it explained the score wrongly ("mentions skills your CV doesn't show") |
| `llama3.2:3b` (run 1) | 56 s | 13.0 | search_jobs failed, match_cv failed | **invented 5 vacancies** (H2) |
| `llama3.2:3b` (run 2) | 85 s | 9.8 | search_jobs | real vacancies, but **invented a match score** (H3) |
| `qwen3:4b` (thinking) | 715 s | 5.9 | search_jobs, match_cv | right answer, but long reasoning leaked into the answer |
| `qwen3:4b-instruct` | 222 s | 6.9 | search_jobs, **extract_profile (not needed, 35 s)**, match_cv (all 5 ids) | best behaviour; honest "partial fit 38/100" |

Caveats: single runs; the prompt changed between rows (rules 3 and 8 were added after the first rows),
so rows are *not* strictly comparable. `qwen3:4b-instruct` ran with the newer prompt.

**Reading so far:** speed and honesty pull in opposite directions. The 3B model is 2x faster but not
trustworthy; the 4B-instruct and 7B models behave correctly but take 3-4 minutes on this CPU.
The demo needs a faster setup (iGPU test, shorter prompts) or a pre-warmed model and rehearsed prompts.

## 7. The `think` option (Qwen3)

Tested directly on `qwen3:4b` with the question "What is 17*3?":

| Request | Result |
|---|---|
| `think=False` | still reasons (309 tokens) and puts the reasoning **into the visible answer** |
| `think=True` | reasoning in the separate `thinking` field, answer empty within 400 tokens |
| `think` omitted | reasoning separated, answer "51" after 257 tokens |
| `/no_think` in system or user message | no effect |

Explanation: in this Ollama build the tag `qwen3:4b` is the **thinking-only 2507 variant**.
The non-thinking one is `qwen3:4b-instruct`. For the benchmark we compare think on/off only on models
that really support both modes (e.g. `qwen3:8b`), and use `-instruct` variants otherwise.

---

## Open questions
1. Can the AMD iGPU speed things up (Ollama Vulkan)? Expected gain 2-3x, to be measured.
2. Which smaller model keeps the guard from firing (honest) *and* is fast enough?
3. Can the first tool-call round be made shorter (system prompt + tool schemas are ~800 tokens)?
4. Does a smaller `num_ctx` (4096) change speed or quality?
