# Cover letter rubric (manual scoring)

The benchmark checks letters automatically (length, language, no placeholders, nothing invented about school or employers,
no list of technologies). It cannot judge whether a letter is *good*. Do that by hand:

1. Run `python -m eval.benchmark letters` and `python -m eval.report`.
2. Open `eval/results/letters_for_review.md`: every letter of every model, sorted by vacancy.
3. Give each letter **1-5** on the five criteria below. Score blind if you can: cover the model name first.
4. Write the numbers into the table at the bottom and add the averages to `docs/model_selection.md`.

| Criterion | 1 (poor) | 3 (acceptable) | 5 (very good) |
|---|---|---|---|
| **Motivation** | no reason for *this* job or company | a generic reason | a specific, believable reason for this job and company |
| **Personal qualities** | none, or a list of skills/technologies | some qualities, little proof | 2-3 qualities that fit the job, each tied to the CV facts |
| **Language quality** | errors that change the meaning, or the wrong language | understandable, some slips ("un brasserie") | natural and correct, right register for the tone |
| **Tone and format** | wrong greeting, rude, or exaggerated | polite but stiff | warm, sincere, correct greeting and sign-off, about 150 words |
| **Facts** | invents experience, school or numbers | small stretches | uses only what the CV says |

A letter that **invents facts scores 1 on Facts and cannot be the winner**, whatever its other scores.

## Score table

| Letter | Model | Motivation | Qualities | Language | Tone | Facts | Average |
|---|---|---|---|---|---|---|---|
| | | | | | | | |
