"""AI judge: asks Claude the fuzzy questions a regex can't answer.

The judge can only flag things for a human (REVIEW). It never passes or fails
an app on its own. Every flag must quote real code, and flags whose quotes
can't be found in the files are thrown away as likely hallucinations.
"""
from __future__ import annotations

import json
import os
import re

import anthropic

from rules import App, Result, is_env_file, redact

MODEL = os.environ.get("JUDGE_MODEL", "claude-opus-5")
# If the model declines, retry server-side on Anthropic's recommended fallback (newest models only).
FALLBACK = (
    {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    if MODEL.startswith(("claude-opus-5", "claude-fable-5"))
    else {}
)
MAX_PROMPT_CHARS = 400_000  # roughly 100k tokens; files beyond this are listed as not reviewed

# id -> (title shown in the report, question asked to the model)
QUESTIONS = {
    "auth-coverage": (
        "Every endpoint is behind the login",
        "Does every HTTP endpoint that does real work require authentication? A health check may be public.",
    ),
    "prompt-injection": (
        "User text can't override the AI's instructions",
        "Is untrusted user input kept apart from the model's instructions? Flag user-controlled values "
        "placed into the system prompt or instructions.",
    ),
    "output-handling": (
        "AI output is treated as untrusted",
        "Is the model's output treated as untrusted? Flag output that is executed, passed to a shell, SQL "
        "or eval, rendered as raw HTML, or used for access decisions.",
    ),
    "sensitive-data": (
        "No secrets or personal data in logs",
        "Does the app keep secrets and personal data out of logs and error responses? Flag credentials, "
        "prompts or user-submitted text being logged or echoed back in errors.",
    ),
    "failure-visibility": (
        "AI failures are caught and logged",
        "Are LLM failures (API errors, timeouts, refusals, truncated output) caught and logged, so the "
        "people running the app notice before users report it?",
    ),
}

SYSTEM_PROMPT = """You review small internal apps that call an LLM, before they ship. Answer each question you are given about the code shown.

For each question, answer "ok" or "concern".
- "concern" means you found a specific problem in the code. Back it with evidence: the file path, the line number, and the offending line copied exactly from the code (without the line-number prefix). If the problem is something missing, quote the line where it should have been, such as the unprotected route or the unguarded call.
- "ok" means you found no such problem. Don't raise concerns about code you weren't shown, and skip style or best-practice nits.

Keep each explanation to one or two plain sentences."""

SCHEMA = {
    "type": "object",
    "properties": {
        "assessments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question_id": {"type": "string", "enum": list(QUESTIONS)},
                    "verdict": {"type": "string", "enum": ["ok", "concern"]},
                    "explanation": {"type": "string"},
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "file": {"type": "string"},
                                "line": {"type": "integer"},
                                "quote": {"type": "string"},
                            },
                            "required": ["file", "line", "quote"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["question_id", "verdict", "explanation", "evidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["assessments"],
    "additionalProperties": False,
}

LINE_PREFIX = re.compile(r"^\s*\d+\s*\|\s?")


def _norm(s: str) -> str:
    return " ".join(s.split())


def verify(evidence: dict, sent: dict[str, str]) -> str | None:
    """Return 'file:line  code' if every quoted line really exists in the file, else None."""
    text = sent.get(evidence["file"])
    if text is None:
        return None
    quoted = [_norm(LINE_PREFIX.sub("", q)) for q in evidence["quote"].splitlines()]
    quoted = [q for q in quoted if len(q) >= 4]
    lines = text.splitlines()
    normed = [_norm(line) for line in lines]
    if not quoted or not all(any(q in line for line in normed) for q in quoted):
        return None
    matches = [n for n, line in enumerate(normed, 1) if quoted[0] in line]
    n = min(matches, key=lambda m: abs(m - evidence["line"]))
    return f"{evidence['file']}:{n}  {lines[n - 1].strip()[:100]}"


def _parse_json(text: str) -> dict:
    """Parse the model's JSON answer, tolerating code fences or prose around it.

    The API enforces the schema via output_config, but some API gateways drop
    that setting, so the schema is also spelled out in the prompt.
    """
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start : end + 1] if start != -1 else text)


def _to_results(answers: dict, sent: dict[str, str]) -> tuple[list[Result], int]:
    """Turn the model's answers into Results; returns (results, number of flags discarded)."""
    results, discarded = [], 0
    for qid, (title, _) in QUESTIONS.items():
        answer = answers.get(qid)
        if answer is None:
            results.append(Result(qid, title, "SKIP", "the AI judge did not answer this question"))
        elif answer["verdict"] == "ok":
            results.append(Result(qid, title, "CLEAR", answer["explanation"]))
        elif evidence := [v for v in (verify(e, sent) for e in answer["evidence"]) if v]:
            results.append(Result(qid, title, "REVIEW", answer["explanation"], evidence))
        else:
            discarded += 1
            detail = f"flag discarded, its quotes don't match the code: {answer['explanation']}"
            results.append(Result(qid, title, "CLEAR", detail))
    return results, discarded


def _skip_all(reason: str) -> tuple[list[Result], list[str]]:
    return [Result(qid, title, "SKIP", "AI judge not run") for qid, (title, _) in QUESTIONS.items()], [reason]


def run_judge(app: App) -> tuple[list[Result], list[str]]:
    """Return one Result per question, plus notes for the report."""
    # Env files are never sent, and secret values are redacted from the rest.
    sent, blocks, not_reviewed, size = {}, [], [], 0
    for path, text in app.files.items():
        if is_env_file(path):
            continue
        text = redact(text)
        numbered = "\n".join(f"{n}| {line}" for n, line in enumerate(text.splitlines(), 1))
        block = f'<file path="{path}">\n{numbered}\n</file>'
        if size + len(block) > MAX_PROMPT_CHARS:
            not_reviewed.append(path)
            continue
        sent[path] = text
        blocks.append(block)
        size += len(block)

    client = anthropic.Anthropic(timeout=300.0)
    if not (client.api_key or client.auth_token or client.credentials):
        return _skip_all("AI judge not run: no Anthropic credentials, set ANTHROPIC_API_KEY")

    questions = "\n".join(f"- {qid}: {question}" for qid, (_, question) in QUESTIONS.items())
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=f"{SYSTEM_PROMPT}\n\nReply with only a JSON object matching this JSON schema:\n{json.dumps(SCHEMA)}",
            messages=[{"role": "user", "content": f"Questions:\n{questions}\n\nCode:\n" + "\n\n".join(blocks)}],
            **FALLBACK,
        )
    except anthropic.AuthenticationError:
        return _skip_all("AI judge not run: the Anthropic API key was rejected")
    except anthropic.APIConnectionError as e:
        return _skip_all(f"AI judge not run: could not reach the Anthropic API ({type(e).__name__})")
    except anthropic.APIStatusError as e:
        return _skip_all(f"AI judge not run: Anthropic API error {e.status_code} ({type(e).__name__})")

    if response.stop_reason != "end_turn":
        return _skip_all(f"AI judge gave no usable answer (stop reason: {response.stop_reason})")
    text = "".join(b.text for b in response.content if b.type == "text")
    try:
        answers = {a["question_id"]: a for a in _parse_json(text)["assessments"]}
        results, discarded = _to_results(answers, sent)
    except (ValueError, KeyError, TypeError):
        return _skip_all(f"AI judge not run: its answer wasn't in the expected format (starts with {text[:80]!r})")

    notes = [f"model: {response.model}"]
    if discarded:
        notes.append(f"{discarded} AI flag(s) discarded because the cited code doesn't exist (likely hallucination)")
    if not_reviewed:
        notes.append(f"not reviewed by the AI judge (too large): {', '.join(not_reviewed)}")
    return results, notes
