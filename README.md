# Provenance Guard

Provenance Guard is a backend for a writing platform. A creator submits a piece of text, and the API checks it with Groq and a separate statistical analysis. It returns a label explaining the result. If the creator disagrees, they can submit an appeal without losing the original decision.

The main design choice is to leave room for uncertainty. Writing style alone cannot establish who wrote something, so the API needs a useful answer when the evidence is weak or the two checks disagree.

**Current testing:** all seven automated tests pass. The four sample passages have also been run using `python verify.py --live`; their reported results are included below. The rate-limit test returned ten successful responses followed by two HTTP 429 responses. Separate fixed-score tests exercise all three label categories.

## Setup (Mac/Linux)

Requires Python 3.10 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and replace `your_key_here` with your Groq key. Keep `.env` private. Then:

```bash
python app.py
```

The API runs at `http://127.0.0.1:5000`. If macOS is already using port 5000, run `flask --app 'app:create_app()' run --port 5001` and adjust the commands below.

The assignment recommends `meta-llama/llama-4-scout-17b-16e-instruct`. Groq's documentation lists it as retired July 17, 2026, so this project defaults to its documented replacement, `openai/gpt-oss-120b`. Override `GROQ_MODEL` in `.env` if needed. See [Groq deprecations](https://console.groq.com/docs/deprecations) and [supported models](https://console.groq.com/docs/models). Account access and provider limits still apply.

Without a key, the service remains runnable but explicitly marks the semantic signal unavailable and always returns uncertain. This degraded mode does not meet the two-working-signals requirement by itself.

## How a submission moves through the system

`POST /submit` accepts the text and a creator ID. The route checks the request and enforces the submission limit before doing the analysis. Groq assesses the passage as a whole, while the Python signal measures sentence length, vocabulary diversity, and punctuation.

Both scores go to `combine_signals()`. It calculates a weighted score, checks whether there is enough evidence for a decision, and picks one of three labels. The route then saves the content and audit event in the same SQLite transaction and returns the result as JSON.

An appeal follows a shorter path: find the submission, check the creator ID, save the reason, and change its status to `under_review`. The original classification stays in the log, with a separate event for the appeal. A reviewer can see both through `GET /appeals`.

The diagram is in [planning.md](planning.md#architecture).

Files:

- `app.py`: Flask endpoints, input validation, limiter, SQLite storage and appeals.
- `signals.py`: standalone Groq assessment, stylometrics, scoring and label text.
- `samples.py`: four illustrative sample texts.
- `verify.py`: reproducible integration evidence and live evaluation.
- `tests/test_project.py`: automated verification.

## Why use two signals?

| Signal | What it measures | Where it can fail |
| --- | --- | --- |
| Groq semantic assessment | Returns a 0–1 AI-evidence score and a short explanation. Uses contextual specificity, semantic coherence and generic boilerplate; captures properties beyond sentence statistics. | Can mistake polished human work for AI or miss edited AI. Prompt instructions are untrusted but prompt injection cannot be ruled out. Model self-assessment is not calibrated. |
| Python stylometrics | Sentence-length coefficient of variation (CV), type-token ratio (TTR), punctuation density. Structural regularity offers a different kind of evidence. | Poetry, technical language, text length and language differences distort measurements. Regularity is not proof of AI use. |

Exact structural formula (`clamp(x)` restricts x to [0,1]):

```text
uniformity = clamp(1 - sentence_length_cv / 0.8)
repetition = clamp((0.9 - type_token_ratio) / 0.5)
sparse_punctuation = clamp(1 - punctuation_density / 0.08)
style_score = 0.50*uniformity + 0.30*repetition + 0.20*sparse_punctuation
```

The constants make each measurement contribute on a similar scale. They were chosen for this prototype, rather than fitted to a training dataset. That matters: the structural score describes a pattern in the text, not the chance that the author used AI. Sentence splitting also has obvious limits around abbreviations, dialogue, and poetry. The project is intended for English prose.

## Confidence scoring and uncertainty

```text
p = 0.75 * llm_score + 0.25 * style_score
ai_score = p
confidence = max(p, 1 - p)
```

Groq gets more weight because it can consider context. The statistical check contributes less because regular sentence lengths or repeated words can be perfectly normal choices. `ai_score` gives direction: high values lean AI, low values lean human. `confidence` gives strength in the leading direction, even if that strength is insufficient for a categorical decision. For example, `ai_score=0.10` means confidence `0.90` in the human direction; `ai_score=0.60` means confidence `0.60` and remains uncertain. The scores have not been calibrated against a labeled dataset, so a confidence of 0.90 should not be read as “correct 90% of the time.”

| Decision | Requirements |
| --- | --- |
| `likely_ai` | p >= 0.85, both signals >= 0.70, and no guardrail |
| `likely_human` | p <= 0.20, both signals <= 0.35, and no guardrail |
| `uncertain` | All other cases |

There are also three reasons to hold back a categorical result:

- Groq is unavailable or returns an invalid response.
- The passage has fewer than 40 words or fewer than three sentences.
- The scores differ by more than 0.45.

In any of these cases, the label is uncertain and p is restricted to [0.30, 0.70]. The AI threshold is deliberately harder to reach than the human threshold. On a writing platform, falsely accusing a creator would be especially damaging.

### Checking the scores

These are the results from the included repeatable test run. The Groq column contains fixed test values; the structural score is calculated from the actual text. This checks the calculation and label selection without depending on a changing model response. Full inputs and responses are saved in [evidence/fixture_run.json](evidence/fixture_run.json).

| Input | Injected LLM score | Actual AI score | Actual confidence | Label |
| --- | ---: | ---: | ---: | --- |
| Repeated eight-sentence paragraph, beginning “The system provides consistent value for every user.” | 0.99 | 0.980680 | 0.980680 | likely_ai |
| Irregular personal narrative, beginning “Rain. I ran.” | 0.05 | 0.079089 | 0.920911 | likely_human |
| Formal monetary policy excerpt | 0.70 | 0.661779 | 0.661779 | uncertain |
| Edited remote-work paragraph | 0.60 | 0.550446 | 0.550446 | uncertain |

The repeated paragraph and the edited remote-work passage give a high-confidence and a lower-confidence example. The test also reaches all three labels. The formal passage and edited passage are useful borderline cases, since either could plausibly be written by a person or a model. These tests check behavior; they do not measure detection accuracy.

Run:

```bash
python -m unittest discover -s tests -v
python verify.py
python verify.py --live
```

The live command sends all four samples through the Groq signal and saves `evidence/live_run.json`. Check `signals.llm.available` for each submission when inspecting this file: a live-mode run can still record a provider failure as unavailable. The terminal summary alone does not show that flag.

### Live Groq evaluation

The four sample passages were submitted using the live Groq evaluation command. The reported results were:

| Sample | AI score | Confidence | Result |
| --- | ---: | ---: | --- |
| Uniform paragraph | 0.763180 | 0.763180 | uncertain |
| Casual narrative | 0.191589 | 0.808411 | likely_human |
| Formal passage | 0.361779 | 0.638221 | uncertain |
| Edited passage | 0.475446 | 0.524554 | uncertain |


The casual narrative produced a higher-confidence human result, while the edited passage stayed close to the middle of the scoring range. The uniform paragraph also remained uncertain; repetition alone did not result in a high-confidence AI label. No passage in this live run received `likely_ai`; that category is covered by the controlled integration tests.

For example, the casual narrative begins “Rain. I ran. Why did I forget my coat again?” and receives confidence 0.808411 in the human direction. The edited passage begins “I've been thinking a lot about remote work lately.” and receives confidence 0.524554, which is too weak for a categorical result. These examples show how the system communicates differing evidence strength without forcing every passage into AI or human.

Full inputs, individual signal scores, decisions, and audit events are included in [the live evaluation output](evidence/live_run.json). A result that differs from expectations is worth investigating rather than changing the threshold just to make that example pass.

For real deployment, evaluate a held-out corpus with independently known provenance, multiple genres and dialects, measure the human false-positive rate, and calibrate scores using held-out calibration data. Those steps have not been performed.

## Labels shown to readers

| Attribution | Verbatim displayed label text |
| --- | --- |
| High-confidence AI (`likely_ai`) | "Likely AI-generated: both checks found strong AI-like patterns. This is an automated estimate, not proof of authorship. The creator can appeal." |
| High-confidence human (`likely_human`) | "Likely human-written: both checks found patterns consistent with human writing. This is an automated estimate, not verified authorship." |
| Uncertain (`uncertain`) | "Authorship uncertain: the checks do not provide enough consistent evidence. This content should not be treated as confirmed AI-generated or confirmed human-written." |

The API returns this text in `label`, ready for a platform to display. The label explains what the result means without asking a reader to interpret a decimal score.

## Trying the API

| Endpoint | Purpose |
| --- | --- |
| `POST /submit` | Analyze `{text, creator_id}` |
| `GET /content/<content_id>` | Get original decision and current status |
| `POST /appeal` | Contest with `{content_id, creator_id, creator_reasoning}` |
| `GET /appeals` | Review queue, including submitted text and original decision |
| `GET /log?limit=20` | Latest audit events, newest first; limit 1–100 |
| `GET /health` | Service status and key-configuration mode |

```bash
curl -s http://127.0.0.1:5000/health | python3 -m json.tool

curl -s -X POST http://127.0.0.1:5000/submit \
  -H 'Content-Type: application/json' \
  -d '{"creator_id":"writer-1","text":"The sun dipped below the horizon. I sat on the porch with my coffee, trying to remember where I had left my keys. The neighbor waved, but I was already late again and had spilled coffee down the front of my shirt."}' \
  | python3 -m json.tool
```

Copy the returned `content_id`, then replace `PASTE-CONTENT-ID` below:

```bash
curl -s -X POST http://127.0.0.1:5000/appeal \
  -H 'Content-Type: application/json' \
  -d '{"content_id":"PASTE-CONTENT-ID","creator_id":"writer-1","creator_reasoning":"I wrote this myself and can provide earlier drafts for review."}' \
  | python3 -m json.tool

curl -s http://127.0.0.1:5000/log | python3 -m json.tool
curl -s http://127.0.0.1:5000/appeals | python3 -m json.tool
```

Input constraints: text 1–20,000 characters, creator ID 1–100, appeal reasoning 10–2,000. Requests must have a JSON object body; maximum body 64 KiB. Invalid requests return JSON errors. Missing content returns 404, mismatched creators 403, duplicate appeals 409, and excessive submissions 429.

## What happens when a creator appeals?

A creator sends the content ID, their creator ID, and a reason for contesting the result. Appeals are allowed for any label. The server checks that the submission exists and belongs to that creator ID, then saves the appeal and changes the status to `under_review`.

This happens in one database transaction. A unique constraint on the content ID prevents two appeals for the same submission. An appeal does not replace the original confidence score or rewrite the original event in the audit log.

`GET /appeals` shows the submitted text, original assessment, current status, reasoning, and timestamps. That gives a human reviewer the context needed to investigate. Review decisions and automatic reclassification are outside this project's scope.

Creator-ID comparison is a demonstration ownership check, not authentication. The server cannot establish the real identity of someone who supplies an ID.

## Rate limiting

The submission route uses Flask-Limiter with **10 requests per minute and 100 per day per remote IP**. Ten per minute permits a small burst of drafts while constraining scripts and provider spending; 100 per day accommodates a prolific writer without permitting an endless stream. IP limits can unfairly affect shared campus networks; authenticated creator quotas plus an IP abuse cap would be preferable in production.

`memory://` explicitly configures storage for a single local process. Counters reset on restart and are not shared across processes. Set `RATELIMIT_STORAGE_URI` to a shared backend with the appropriate dependencies for deployment. Client-supplied creator IDs and forwarding headers are not trusted for rate-limit keys.

Executed rate-limit output from the verification script (fresh limiter, real Flask requests, fixed semantic score for this part of the test):

```text
200 200 200 200 200 200 200 200 200 200 429 429
```

To verify manually, restart the local server for a fresh in-memory counter. This makes up to ten real Groq calls if a key is configured:

```bash
for i in $(seq 1 12); do
  curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:5000/submit \
    -H 'Content-Type: application/json' \
    -d '{"text":"This is a test submission for rate limiting.","creator_id":"limit-test"}'
done
```

## Audit log

SQLite stores separate immutable classification and appeal events. Each includes timestamp, content ID, creator ID, attribution, confidence, AI score, both signal records, guardrails, label and status. Appeal events also include appeal ID and reasoning. Content status changes without overwriting the original audit decision.

[evidence/fixture_run.json](evidence/fixture_run.json) is the committed, executed audit sample with **five entries: four classifications and one appeal**. Each carries attribution, confidence and timestamp; the appeal retains the original decision and shows `under_review`. The file's top-level mode and `TEST_FIXTURE` model explicitly disclose its test origin. The live evaluation script also writes four classification events and one appeal event under `audit_entries` in [evidence/live_run.json](evidence/live_run.json). That file provides the live-run audit sample, including timestamps and individual signals. Runtime events can be inspected through `/log`. Commit both evidence files with the repository.

## Known limitations

- Repetitive human poetry can score high on structural regularity; formal or non-native English can confuse the LLM. These are concrete false-positive risks despite conservative thresholds.
- Edited AI text can resemble human work, and mixed authorship does not fit three simple labels well.
- Short text and abbreviations make simple sentence splitting unreliable. English-oriented heuristics are not validated across languages.
- The inspection endpoints expose submitted text, decisions, and appeals. They are useful for the local demo, but would need authentication and reviewer permissions before deployment.
- Text is sent to Groq when a key is configured. Stored content remains in SQLite until the local database is deleted. No authorship verification or provenance certificate is claimed.
- Missing API credentials or provider errors leave only one working signal and force an uncertain label. The `available` flag distinguishes a successful model assessment from that fallback.

## Reflection on the plan

Defining the score before writing the routes helped avoid a confusing API. A low AI score and low confidence are different things: a score near zero leans strongly human, while a score near 0.5 is uncertain. Writing that distinction into the plan made it easier to keep the response fields, labels, and tests consistent.

One detail added during implementation was a 64 KiB cap on the entire request body. The plan described field validation, but a body cap also rejects oversized requests before their fields are processed.

The model choice changed from the assignment's suggested Scout setup because that model had been retired. The implementation uses a configurable replacement instead. The appeal endpoint also asks for a creator ID in addition to the assignment's minimum fields. This catches an accidental mismatch, although it still needs proper authentication in a real platform.

## AI assistance

ChatGPT helped with the plan, implementation, tests, and documentation. Two examples of how that assistance was used:

1. The assignment and scoring requirements were used to draft the Flask routes and signal functions. During the code review, the scoring was clarified so that AI evidence and confidence were separate fields. Model responses also received explicit validation instead of assuming every JSON response had usable values.
2. The architecture and appeals requirements were used to draft the SQLite workflow and tests. The implementation was revised to save status changes and audit events in one transaction, close database connections after use, and preserve the original classification when an appeal is filed.
3. During live testing, Groq sometimes returned its explanation under
`reason` instead of `reasoning`. The original validation rejected
that response. With AI assistance, the parser was updated to accept
either field while keeping score and explanation validation.
The submission then returned an available semantic signal, and
all seven automated tests passed again.

The automated tests use controlled model responses. The live evaluation results are documented separately above so the two types of evidence remain distinguishable.

