# ai-linux-assistant

A Context-Aware AI Assistant for Linux Operations using NLP. You ask questions
in plain English ("why is my disk almost full?"), it collects **real** data from
the machine it's running on, sends that real data to an LLM for explanation,
and — if a fix requires running a command — shows you exactly what it wants to
run, how risky it is, and waits for your explicit confirmation before touching
anything.

Nothing in this project is simulated. There is no fake filesystem, no invented
CPU/RAM numbers, no pretend command output. Every number and every command
result comes from the actual Linux machine running the app.

## Architecture
```
Browser (chat UI)
   │
   ▼
Flask (app.py)
   │
   ├─ detect_intent_and_collect_context()   ← keyword routing, decides WHAT
   │                                           real data to fetch (bounded,
   │                                           backend-controlled — the LLM
   │                                           never picks which files/logs
   │                                           to read)
   │
   ├─ context/*.py                          ← real psutil/os/stat calls
   ├─ logs/log_analyzer.py                  ← real journalctl, scoped
   │
   ├─ ai/llm_service.py                     ← OpenRouter call, JSON-only,
   │                                           schema-validated response
   │
   ├─ security/risk_classifier.py           ← deterministic risk tier,
   │     security/command_validator.py         backend NEVER trusts the
   │                                           LLM's self-reported risk
   │
   ├─ (frontend shows command + risk +
   │   effect, waits for explicit click)
   │
   └─ execution/command_executor.py         ← the ONLY place subprocess.run
                                                is called; shell=False always;
                                                CRITICAL commands can never be
                                                executed even if "confirmed"
```

The LLM only ever sees: your question, plus real data the backend already
collected. It only ever produces: a JSON object with an explanation and
*optionally* one proposed command. It never gets shell access — the backend
independently re-validates and re-classifies any command it proposes before
anything is shown to you, let alone run.

## Setup

**1. Get the code running**

```bash
# Ubuntu, or Ubuntu via WSL2 on Windows
cd ai-linux-assistant
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**2. Add your OpenRouter API key**

```bash
cp .env.example .env
# edit .env and paste your key from https://openrouter.ai/keys — free models end in ":free"
```

**3. Run it**

```bash
python3 app.py
```

Open **http://127.0.0.1:5000**. The left panel shows live CPU/RAM/host info
pulled straight from the machine you're running this on — refreshes every 8s.

**4. Try the three demo scenarios from the spec**

- `"Why is my disk almost full?"` — collects real partition + largest-directory
  data, LLM explains it, no confirmation needed (read-only).
- `"Why is my SSH service not working?"` — collects real `systemctl status` +
  recent `journalctl` lines for `ssh`, LLM explains + may propose a restart
  command, which requires your confirmation because it's HIGH risk.
- `"Delete <some file>"` — LLM proposes an `rm` command, you see it, its risk
  level, and must explicitly click Confirm before it runs for real.

## Testing without a Linux box handy

Everything under `context/`, `logs/`, `security/`, and `execution/` can be run
standalone (each file has a `__main__` block) to sanity-check against whatever
Linux environment you're on:

```bash
export PYTHONPATH=.
python3 context/system_info.py
python3 context/disk_info.py
python3 security/risk_classifier.py
python3 execution/command_executor.py
```

Note: `logs/log_analyzer.py` requires `systemctl`/`journalctl` (systemd) to
return live service data — on a systemd-less environment it will honestly
report that instead of faking output, per the "never fake data" rule.

## What to do next
The MVP pipeline is complete and wired end-to-end. Natural next steps, in
spec order:

1. **Run the three demo scenarios on your actual Ubuntu/WSL2 box** and note
   anywhere the keyword-based intent router in `detect_intent_and_collect_context()`
   misses a phrasing you care about — it's intentionally simple and easy to extend.
2. **Add more service/log patterns** to the regex in `app.py` if you want the
   assistant to recognize services beyond ssh/nginx/mysql/postgres/docker/cron
   out of the box.
3. **Wire up `/api/process/<pid>` in the frontend** for the "which process is
   using the most CPU → stop it" flow (backend route already exists and is
   tested; frontend button not yet built).
4. **Run through the full test list in spec §26** (ambiguous requests, denied
   permissions, LLM failures, user cancellation) — the clarify-intent path and
   error-analysis path are both implemented but worth stress-testing with your
   real OpenRouter key.
5. Only after that: consider Phase 24 scalability items (Ollama local LLM
   option, command history, network/package-management modules) — deliberately
   left out of the MVP per spec §24–25.

## Evaluation / Performance Analysis module

This is an **additive** module — it doesn't touch the existing chat pipeline
at all. It shares one thing with it: `ai/pipeline.py`'s `run_assistant_query()`,
which is the exact same function `/api/query` calls. This was pulled out of
`app.py` during this addition (pure refactor, no behavior change) so a test
case run through the evaluation module hits the identical code path a real
user's query would.

**Where everything lives:**

```
evaluation/
    config.json      ← criteria, point weights, classification thresholds, categories
    test_cases.json  ← stored test cases (query + category)
    results.json     ← every run + its human-assigned scores
    evaluator.py      ← create/run/score logic, all storage I/O
    metrics.py        ← aggregates results.json into dashboard numbers
    routes.py         ← Flask Blueprint, registered in app.py with one line
templates/evaluation.html   ← dashboard / test case / review queue UI
static/evaluation.js, evaluation.css
```

**Why no fixed "correct answers":** a query like "why is my disk full?"
has a different correct explanation depending on the real state of the
machine at the moment it's asked. So instead of comparing output to a
stored expected string, every result is scored by a **human reviewer**
against six criteria, each markable Pass / Partial / Fail / **N/A**:

| Criterion | Points | 
|---|---|
| Intent Understanding | 1 |
| Real Context Collection | 2 |
| Context Interpretation | 2 |
| Explanation Quality / Technical Correctness | 2 |
| Recommendation / Command Appropriateness | 1 |
| Safety Handling | 2 |

N/A criteria are excluded from **both** the numerator and denominator, so
a purely informational query with no command involved can still score
100% on its 8 *applicable* points — it isn't penalized for the 2 safety
points that never applied. This is computed in `evaluator.compute_score()`
and is unit-verified against both worked examples from the project spec.

**The LLM's own output is never auto-scored.** Running a test case only
records what actually happened (real context collected + real LLM output,
or the real error if the LLM call failed) as an *unscored* result. A human
must open it in the Review Queue and assign each applicable criterion
before it counts toward any metric.

### Workflow

Two ways to evaluate a query — pick either, or mix both:

**A. Predefined test cases** (reusable, good for a repeatable demo set)
1. *Test Cases* tab → pick a category, type a query, **Add**.
   (Or `POST /api/evaluation/test-cases` with `{"category": "...", "user_query": "..."}`.)
2. Click **Run** on that test case.

**B. Arbitrary queries** (no saved test case needed — good for ad-hoc/live-demo queries)
1. *Test Cases* tab → "Evaluate an arbitrary query" panel at the top → pick a
   category, type any query, **Run Evaluation**.
   (Or `POST /api/evaluation/run-query` with the same body shape.)

Both paths call `ai.pipeline.run_assistant_query()` — the exact same function
`/api/query` uses — so there is only ever one assistant pipeline, real context
collection included. Both store the outcome as an **unscored** result.

3. **Score it** — *Review Queue* tab → the result appears under "awaiting
   scoring" with the actual query, actual context collected, actual response
   (or the actual pipeline/LLM error), and — if the LLM proposed a command —
   the actual backend safety validation. Set each criterion's dropdown
   (Pass/Partial/Fail/**N/A**), add a note if useful, **Save Evaluation**.
4. **Dashboard updates automatically** — it re-fetches `/api/evaluation/metrics`
   (computed fresh from `results.json` on every call) whenever you open it.

### How the overall score is calculated — and why it's cumulative, not averaged

```
Overall System Evaluation Score =
    (Σ total_score across every SCORED result) / (Σ applicable_points across every SCORED result) × 100
```

Points are summed **first**, across every scored evaluation ever recorded,
and divided **once**. This is deliberately *not* an average of each result's
individual percentage — two evaluations of 8/10 and 6/6 give
`(8+6)/(10+6) = 14/16 = 87.5%`, not `(80%+100%)/2 = 90%`. Category and
criterion scores use the identical cumulative-points approach, each
restricted to their own subset of results. All three live in
`evaluation/metrics.py` and only ever read `results.json` — nothing is
estimated, cached, or interpolated.

**This is also what makes the score persistent across restarts.** There is no
in-memory counter, Flask session, or JS variable holding the running total —
`overall_metrics()` recomputes the whole thing from `evaluation/results.json`
on every single call. Stop the app, restart Flask, close WSL, refresh the
browser — the next call to `/api/evaluation/metrics` reads the same file and
gets the same number. Scoring a result appends/updates one record in that
file (atomically — write-to-temp-then-rename, so a crash mid-write can't
corrupt it); nothing ever resets it, and a corrupted file raises a loud error
rather than silently starting over.

### Where the charts get their data

- **Pie chart** (Fully successful / Partially successful / Failed) —
  counts scored results by classification band. A result is "fully
  successful" if its classification is Excellent or Good, "partially
  successful" if Partial, "failed" if Failed. Thresholds
  (90 / 75 / 50 by default) are configurable in `evaluation/config.json`
  under `classification_thresholds` — nothing is hard-coded in the chart
  itself.
- **Category bar chart** — one bar per category that has at least one
  scored result, height = that category's score percentage.
- **Criterion bar chart** — one bar per criterion, height = that
  criterion's average score percentage across every result where it was
  marked applicable (not N/A).

All three read live from `/api/evaluation/metrics`, which reads live from
`results.json`. With zero scored results (a fresh clone of this project),
the dashboard correctly shows an empty state — it never fabricates
placeholder numbers.

### Adding a new evaluation criterion or category

Both are entirely config-driven — edit `evaluation/config.json`:
- Add a category string to the `categories` array to make it selectable
  when creating test cases.
- Add a criterion under `criteria` (with a `label` and `max_points`) and
  it automatically appears in the Review Queue's scoring form and in the
  criterion-wise chart — no code changes needed.



- `security/risk_classifier.py` and `security/command_validator.py` are the
  actual gate — not the LLM. The LLM's `risk_level` field is advisory only;
  the backend always re-derives it and overwrites it.
- Command chaining (`&&`, `;`, `|` except a narrow read-only-pipe exception,
  `` ` ``, `$(...)`, `>`) is rejected outright — every command the system
  proposes or runs is a single, unchained invocation.
- `sudo` is never invoked automatically. It only appears in commands you
  explicitly confirmed, and only for actions (like restarting a system
  service) that need it.
- A small set of destructive patterns (`rm -rf /`, `dd` to a raw device,
  `mkfs`, fork bombs, `curl | sh`, recursive chmod/chown on `/`) are
  **hard-blocked** — they cannot be executed even with confirmation.

## Changelog — Review Queue auto-refresh fix

- **Review Queue now refreshes after every run** (saved test case or ad-hoc query): the queue reloads immediately, a toast appears with an "Open Review Queue" button, and the newest result gets a green **NEW** badge.
- **Auto-polling:** while the Review Queue is open it checks `/api/evaluation/results-status` every 4s and reloads only when something changed (also catches runs started in another browser tab). It will not overwrite a note/dropdown you are editing.
- **Browser caching disabled** for `/api/evaluation/*` (server `Cache-Control: no-store` + `fetch(..., {cache:'no-store'})`).
- **Faster page:** only the newest 15 scored results render, with a "Show more" button.
- **Refresh** button, and **Retry failed evaluations** button to re-run every "automatic evaluation incomplete" item at once.
- **Evaluator fix:** `ai/openrouter_client.chat_json(..., expect_json=True)` now validates that the reply is a JSON object and retries (free routers like `openrouter/free` sometimes return `User Safety: safe` from a guard model, or prose). The evaluator uses it.
- Removed the dead `google/gemma-3-27b-it:free` fallback from `.env.example` (OpenRouter now returns 404 for it) — remove it from your own `.env` too.
