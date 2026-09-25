# Compliance checker for internal LLM apps

Teams are building small internal apps that call an LLM. This repo is a lightweight
"safe to ship" gate for them: a definition of the bar, plus a checker that tests any
app against it automatically.

| Folder | What it is |
|---|---|
| [checker/](checker/) | The checker: plain pattern checks plus an AI judge |
| [reference_app/](reference_app/) | A tiny app that summarizes text with Claude, behind a basic-auth login |
| [examples/leaky_app/](examples/leaky_app/) | A small JavaScript app with problems planted on purpose, to test the checker |
| [DESIGN.md](DESIGN.md) | The design write-up |

## Prerequisites

- Python 3.11 or newer
- An Anthropic API key, for the reference app and the checker's AI judge.
  The checker's plain checks run without one.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in ANTHROPIC_API_KEY and APP_PASSWORD
set -a; source .env; set +a  # load the variables into this shell
```

## Run the checker

```bash
python checker/check.py reference_app        # our app: should pass
python checker/check.py examples/leaky_app   # planted problems: should fail
python checker/check.py path/to/any/app      # works on any folder of source code
```

Options: `--no-ai` runs the plain checks only; `--json` prints a machine-readable report.

| Exit code | Meaning |
|---|---|
| 0 | PASS: safe to ship |
| 1 | FAIL: a plain check failed, so shipping is blocked |
| 2 | REVIEW: nothing blocking, but a human must look at the flagged items |

The same command runs in CI ([.github/workflows/compliance.yml](.github/workflows/compliance.yml)).

## Run the reference app

```bash
uvicorn --app-dir reference_app app:app --port 8000

curl localhost:8000/health
curl -u "demo:$APP_PASSWORD" localhost:8000/summarize \
  -H 'content-type: application/json' \
  -d '{"text": "Paste a few paragraphs here."}'
```

## What the checker looks at

**Plain checks** are pattern matching, give the same answer every run, and are the only
checks that can FAIL an app:

| Check | Why |
|---|---|
| Ownership manifest (`compliance.toml`) | Someone has to get the alerts. It also decides which ownership track the app is on |
| No secrets in the code | Leaked keys are the most common and most expensive mistake |
| Login in front of the app | Internal doesn't mean public |
| No `eval` / `exec` / shell | One crafted input or model answer away from running arbitrary code |
| Debug mode off | Debug mode leaks stack traces and internals |
| LLM calls have a timeout and a token cap | A hung or runaway call ties up the app and the bill |
| Health-check endpoint | Monitoring needs something to poll |
| Uses a real logger | You can't alert on `print` |
| Sensitive data gets a human sign-off | Confidential or restricted data always goes to a person |

**AI judge** (Claude) answers the fuzzy questions: is every endpoint behind the login,
can user text override the AI's instructions, is AI output treated as untrusted, do
secrets or personal data end up in logs, are AI failures caught and logged. It can only
**flag items for a human**, never pass or fail an app. Every flag must quote the exact
code behind it, and flags whose quotes aren't found in the files are discarded as
hallucinations. Secret values are redacted before any code is sent to the model.

The reasoning behind the bar and what's deliberately left out is in [DESIGN.md](DESIGN.md).

## Approach

**Keep it small.** The brief asks for judgment, not a platform, so the app is one file,
the checker is three, and there's no database or UI. Most of the effort went into what the
bar is and how the checker decides, not into features.

**Two layers with different powers.** Pattern checks are cheap, predictable and
explainable, so they are the only ones allowed to block. The AI judge catches what
patterns can't, but it can be wrong, so it can only escalate to a human and must prove
each flag with real code.

**Built for apps it has never seen.** Patterns cover Python and JavaScript/TypeScript side
by side, the AI judge reads any language, and the only app-specific input is the small
manifest every app is expected to ship. The test fixture is JavaScript on purpose.

**How I used AI tools.** I used Claude Code throughout: to read the brief and talk through
scope, to draft the code, and to test it (running the checker on both apps and exercising
the reference app's login and error paths). What goes in the bar, what stays out, and where
the line between automation and humans sits were my decisions; Claude drafted, and I
reviewed and edited. Running the code caught a real bug: without an API key, the SDK raised
a different error than expected and crashed the checker instead of skipping the AI judge.
The checker and the app now check for credentials up front.