# Leaky app (test fixture)

A small Express app that translates meeting notes with Claude. It has problems
planted on purpose, so we can see what the checker catches. It is never run.
It's written in JavaScript to show the checker isn't tied to our Python app.
The list below lives here, not in the code, so the AI judge gets no hints.

| Planted problem | Caught by |
|---|---|
| Anthropic API key hardcoded in `server.js` | plain check `secrets` |
| No `compliance.toml` (no owner) | plain check `manifest` |
| No timeout on the LLM client | plain check `llm-limits` |
| No `/health` endpoint | plain check `health` |
| Only `console.log`, no real logger | plain check `logging` |
| `/translate` has no login (only `/usage` does) | AI judge `auth-coverage` |
| `language` from the request goes into the system prompt | AI judge `prompt-injection` |
| Model output sent back as raw HTML | AI judge `output-handling` |
| Full request body (user text) logged | AI judge `sensitive-data` |
| No error handling around the LLM call | AI judge `failure-visibility` |
