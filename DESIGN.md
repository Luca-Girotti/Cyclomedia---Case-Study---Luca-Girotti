# Design write-up

**Built:** the bar, the checker (plain checks + AI judge), the `compliance.toml` manifest, a CI step.
**Proposed:** the rest below (shared deploy pipeline, alert routing, review queue).

## 1. The bar

An app is safe to ship when it passes these. They cover what the manual reviewer checks today
(auth, secrets, LLM safety, observability), plus ownership.

| Area | Plain check (can block) | AI judge question (can only flag) |
|---|---|---|
| Ownership | `compliance.toml` names owner, track, contact, data class | |
| Auth | Login code exists | Every endpoint that does work is behind it |
| Secrets | No keys or passwords in code, no `.env` shipped | No secrets or user text in logs or errors |
| LLM safety | No eval/exec/shell; timeout and token cap on LLM calls; debug off | User input kept out of the instructions; output treated as untrusted |
| Observability | `/health` endpoint; a real logger | LLM failures caught and logged |
| Data | Confidential or restricted data goes to a human | |

**Why these:** each is a common, costly mistake in small LLM apps, and each can be checked from
source code in seconds, in any language. Plain checks are allowed to block because they're
predictable and explainable. Fuzzy questions go to the AI because patterns can't read intent.

**Left out on purpose:**
- **Dependency vulnerability scanning.** Org-wide tools (Dependabot, pip-audit) already do it better.
- **Quality of the app's own answers.** App-specific and needs per-app test sets. It's the owner's
  job, supported by failure logging and feedback routing, not a ship gate.
- **Pen-testing a running app.** Needs a sandbox; it's the two-week item.
- **Load, cost and performance.** These are small internal tools; platform quotas cover it.
- **"Prompt-injection proof".** Impossible to guarantee. We check the basics: instructions kept
  apart from user text, and output never executed.

## 2. Two tracks, one set of tooling

Every app ships a `compliance.toml`. Its `track` field (`team` or `central`) is the only difference
between the tracks. Everything else is shared: the same checker in CI, the same deploy pipeline,
the same logging and alerting. Handing an app off is a pull request that changes `track` and
`contact`. There's no migration, because the app was built the central way from day one.

| | Team track | Central track |
|---|---|---|
| Monitoring alerts | Team's on-call channel | Central on-call |
| User feedback | Team's channel | Central queue; builders consulted on feature requests |
| Deploys | Team merges, shared pipeline deploys | Central approves and deploys; builders can still open PRs |
| Rollbacks | Team, one click in the shared pipeline | Central, same button |
| Compliance check | Every PR | Every PR, and a pass is required for the hand-off |

**What keeps it from getting chaotic:**
- **One paved road.** Every app deploys the same way (container, `/health`, standard logs). That's
  why health, logging and the manifest are in the bar: they make an app hand-off-able.
- **The manifest is the single source of truth.** Alert routing and deploy approvals read it, so
  who owns what can't drift from reality.
- **The hand-off is a reviewed PR.** The central team approves it and can refuse an app that
  isn't on the paved road.

## 3. At 50 teams

Running 50 checks at once isn't the problem: each check runs in the team's own CI job, and the
plain checks take seconds. What bends is what all the teams share:

| What bends | Why | What we'd change |
|---|---|---|
| **1. Reviewers** (first) | Every AI flag needs a person, so we're back to one overloaded reviewer | Drop AI questions that often raise false alarms; rotate reviewers; let the app's own team approve small flags themselves, with a written reason and an expiry date |
| **2. The AI budget** | All teams share one API bill and one rate limit | Skip the AI when a plain check already failed; only re-check changed files; use a cheaper model for routine runs |
| **3. Rule changes** | A new rule fails all 50 apps on the same morning | New rules only warn for two weeks before they block |

## 4. Automated vs. always a human

- **Fully automated (blocks):** secrets, missing manifest, eval/exec, debug on, no timeout or token
  cap, no health check, no logger. These are predictable and cheap to check, and the fix is
  obvious. A wrong block costs a developer a minute.
- **Always a human:** every AI flag, any app with confidential or restricted data, any waiver, and
  the hand-off to central.

**Why the line is there:** automate where being wrong is cheap and the answer doesn't depend on
context. A human decides where it depends on intent (should this endpoint be public? is this data
OK to send to an LLM?) or where a wrong "yes" could leak data.

**When the AI judge hallucinates:** it can escalate but never approve or block. Every flag must
quote the code behind it, and flags quoting code that doesn't exist are thrown away automatically.
The risk that remains is a miss (the AI says CLEAR when it shouldn't). The plain checks catch the
most severe issues regardless, and reviewers should spot-check a sample of CLEAR results.

## 5. With two more weeks

1. **An eval set for the checker (first, about 3 days).** 15 to 20 small apps with known planted
   issues, run on every prompt or model change, reporting catches and false alarms per rule.
   Without it, we can't tell whether a change helps.
2. **An agentic runtime check (about a week).** An agent starts the app in a sandbox and tries to
   break it: it calls endpoints without logging in, sends prompt-injection text and sends
   oversized input. It proves problems instead of guessing, which means fewer false alarms and
   fewer human reviews.
3. **Waivers and a review queue (the rest).** Waivers in the manifest, and a simple page listing
   the items waiting for review, who owns each, and how long they've waited.
