"""Plain checks: pattern matching over the app's files, no AI involved.

These are the only checks allowed to FAIL an app, because they give the same
answer on every run. They are deliberately simple and language-agnostic
(Python and JavaScript/TypeScript patterns side by side) so they work on apps
we have never seen, at the cost of some false positives and misses. The AI
judge (judge.py) covers the questions that need real understanding.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", ".next"}
CODE_EXTS = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".java", ".rb", ".php", ".cs"}
CONFIG_EXTS = {".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".conf"}
CONFIG_NAMES = {"Dockerfile", "Procfile"}
LOCKFILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock", "Pipfile.lock"}
MAX_FILE_BYTES = 200_000

MANIFEST = "compliance.toml"
REQUIRED_FIELDS = ("name", "owner_team", "track", "contact", "data_classification")
TRACKS = {"team", "central"}
DATA_CLASSES = {"public", "internal", "confidential", "restricted"}


@dataclass
class Hit:
    file: str
    line: int
    text: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}  {self.text[:100]}"


@dataclass
class Result:
    id: str
    title: str
    status: str  # PASS | FAIL | REVIEW | SKIP, plus CLEAR for "AI judge raised no flag"
    detail: str
    evidence: list[str] = field(default_factory=list)


@dataclass
class App:
    root: Path
    files: dict[str, str]  # relative path -> content

    @property
    def code_files(self) -> dict[str, str]:
        return {p: t for p, t in self.files.items() if Path(p).suffix in CODE_EXTS}

    def grep(self, pattern: str, *, code_only: bool = True) -> list[Hit]:
        rx = re.compile(pattern)
        files = self.code_files if code_only else self.files
        return [
            Hit(path, n, line.strip())
            for path, text in files.items()
            for n, line in enumerate(text.splitlines(), 1)
            if rx.search(line) and not COMMENT.match(line)
        ]


COMMENT = re.compile(r"^\s*(#|//|/?\*)")


def load_app(root: Path) -> App:
    files = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts) or not path.is_file():
            continue
        wanted = (
            path.suffix in CODE_EXTS
            or path.suffix in CONFIG_EXTS
            or path.name in CONFIG_NAMES
            or path.name.startswith(".env")
        )
        if wanted and path.name not in LOCKFILES and path.stat().st_size <= MAX_FILE_BYTES:
            files[rel.as_posix()] = path.read_text(errors="replace")
    return App(root, files)


def read_manifest(app: App) -> tuple[dict | None, list[str]]:
    """Return (manifest, problems). The manifest is None if it is missing or unreadable."""
    path = app.root / MANIFEST
    if not path.is_file():
        return None, ["missing"]
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        return None, [f"not valid TOML ({e})"]
    problems = [f"'{key}' is missing" for key in REQUIRED_FIELDS if not data.get(key)]
    if data.get("track") and data["track"] not in TRACKS:
        problems.append(f"track must be one of {sorted(TRACKS)}")
    if data.get("data_classification") and data["data_classification"] not in DATA_CLASSES:
        problems.append(f"data_classification must be one of {sorted(DATA_CLASSES)}")
    return data, problems


# --- Secrets -----------------------------------------------------------------

SECRET_PATTERNS = {
    "Anthropic API key": r"sk-ant-[A-Za-z0-9_-]{16,}",
    "OpenAI API key": r"sk-(?!ant-)(?:proj-)?[A-Za-z0-9_-]{32,}",
    "AWS access key": r"AKIA[0-9A-Z]{16}",
    "GitHub token": r"gh[pousr]_[A-Za-z0-9]{30,}",
    "Slack token": r"xox[abprs]-[A-Za-z0-9-]{10,}",
    "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
}
# `password = "..."`, `apiKey: '...'`, `"secret_token": "..."` and friends.
HARDCODED = re.compile(
    r"""(?i)(?<![a-z])(api[_-]?key|secret|token|passw(?:or)?d)[\w-]*["']?\s*[:=]\s*["']([^"'\s]{8,})["']"""
)
PLACEHOLDER = re.compile(r"(?i)your|example|placeholder|change.?me|xxxx|dummy|<|\$\{|\.\.\.")


def find_secrets(line: str) -> list[tuple[str, str]]:
    """Return (label, value) for every secret-looking value on one line."""
    found = [(label, m.group()) for label, p in SECRET_PATTERNS.items() for m in re.finditer(p, line)]
    if not found and (m := HARDCODED.search(line)) and not PLACEHOLDER.search(m.group(2)):
        found.append((f"hardcoded {m.group(1).lower()}", m.group(2)))
    return found


def redact(text: str) -> str:
    """Replace secret values so they are never sent to the AI judge."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        for _, value in find_secrets(line):
            lines[i] = lines[i].replace(value, "[REDACTED]")
    return "\n".join(lines)


def is_env_file(path: str) -> bool:
    name = Path(path).name
    return name.startswith(".env") and not name.endswith((".example", ".sample", ".template"))


# --- Rules -------------------------------------------------------------------

ROUTES = (
    r"@\w+\.(get|post|put|patch|delete|route|api_route)\("
    r"|\b(app|router|server)\.(get|post|put|patch|delete|all)\("
    r"|@(Get|Post|Put|Delete|Request)Mapping|HandleFunc\("
)
AUTH = (
    r"(?i)HTTPBasic|HTTPBearer|OAuth2|APIKeyHeader|login_required|HTTPTokenAuth|flask_login"
    r"|passport|basicAuth|basic-auth|express-jwt|jsonwebtoken|jwt\.(decode|verify)"
    r"|getServerSession|next-auth|WWW-Authenticate|requireAuth|@PreAuthorize"
)
DYNAMIC_CODE = (
    r"(?<![\w.])(eval|exec)\(|os\.system\(|shell\s*=\s*True|child_process"
    r"|new Function\(|pickle\.loads?\(|vm\.runIn"
)
DEBUG_ON = r"""(?i)(?<![a-z_])debug["']?\s*[:=]\s*["']?(true|1)\b|FLASK_DEBUG\s*=\s*1"""
LLM_CALL = (
    r"messages\.(create|stream|parse)\(|chat\.completions\.create\(|responses\.create\("
    r"|generate_?[cC]ontent\(|litellm\.a?completion\(|ollama\.(chat|generate)\("
)
MAX_TOKENS = r"(?i)max_?(output_?|completion_?|new_?)?tokens|num_predict"
TIMEOUT = r"(?i)timeout"
HEALTH = r"""["'](/health|/healthz|/healthcheck|/ready|/readyz|/livez|/ping)["']"""
LOGGER = (
    r"import logging|from logging|getLogger|structlog|loguru"
    r"|pino|winston|bunyan|log4j|slf4j|log/slog|logrus"
)


def check_manifest(app: App) -> Result:
    title = "Ownership manifest (who owns it, where alerts go)"
    manifest, problems = read_manifest(app)
    if problems:
        detail = f"{MANIFEST}: {'; '.join(problems)}. Without it, nobody knows who gets alerts."
        return Result("manifest", title, "FAIL", detail)
    return Result("manifest", title, "PASS", f"owner {manifest['owner_team']}, track '{manifest['track']}'")


def check_secrets(app: App) -> Result:
    title = "No secrets in the code"
    evidence = []
    for path, text in app.files.items():
        if is_env_file(path):
            evidence.append(f"{path}  env file shipped with the app")
            continue
        for n, line in enumerate(text.splitlines(), 1):
            evidence += [f"{path}:{n}  {label}: {value[:8]}****" for label, value in find_secrets(line)]
    if evidence:
        return Result("secrets", title, "FAIL", "secrets must come from the environment or a vault", evidence)
    return Result("secrets", title, "PASS", f"no secrets found in {len(app.files)} files")


def check_auth(app: App) -> Result:
    title = "Login in front of the app"
    if not app.grep(ROUTES):
        return Result("auth", title, "SKIP", "no HTTP endpoints found")
    hits = app.grep(AUTH)
    if not hits:
        return Result("auth", title, "FAIL", "the app has HTTP endpoints but no authentication code")
    detail = "authentication found (whether it covers every endpoint is an AI judge question)"
    return Result("auth", title, "PASS", detail, [str(hits[0])])


def check_dynamic_code(app: App) -> Result:
    title = "No running of dynamic code (eval, exec, shell)"
    hits = app.grep(DYNAMIC_CODE)
    if hits:
        detail = "one crafted input or model answer away from running arbitrary code"
        return Result("no-dynamic-code", title, "FAIL", detail, [str(h) for h in hits])
    return Result("no-dynamic-code", title, "PASS", "none found")


def check_debug_off(app: App) -> Result:
    title = "Debug mode off"
    hits = app.grep(DEBUG_ON, code_only=False)
    if hits:
        detail = "debug mode leaks stack traces and internals to users"
        return Result("debug-off", title, "FAIL", detail, [str(h) for h in hits])
    return Result("debug-off", title, "PASS", "no debug flags found")


def check_llm_limits(app: App) -> Result:
    title = "AI calls have a timeout and a token cap"
    calls = app.grep(LLM_CALL)
    if not calls:
        return Result("llm-limits", title, "SKIP", "no LLM calls found")
    problems = []
    if any(not re.search(MAX_TOKENS, app.files[c.file]) for c in calls):
        problems.append("no max-tokens cap next to an LLM call")
    if not app.grep(TIMEOUT):
        problems.append("no timeout set anywhere")
    if problems:
        detail = f"{'; '.join(problems)}. A hung or runaway call ties up the app and the bill."
        return Result("llm-limits", title, "FAIL", detail, [str(c) for c in calls])
    return Result("llm-limits", title, "PASS", f"{len(calls)} LLM call(s), capped and with a timeout")


def check_health(app: App) -> Result:
    title = "Health-check endpoint"
    if not app.grep(ROUTES):
        return Result("health", title, "SKIP", "no HTTP endpoints found")
    if not app.grep(HEALTH):
        return Result("health", title, "FAIL", "no /health endpoint, so monitoring can't tell if the app is up")
    return Result("health", title, "PASS", "health endpoint found")


def check_logging(app: App) -> Result:
    title = "Uses a real logger"
    if not app.grep(LOGGER):
        detail = "no logging library found; print/console.log output has no levels to alert on"
        return Result("logging", title, "FAIL", detail)
    return Result("logging", title, "PASS", "logger found")


def check_data_sensitivity(app: App) -> Result:
    title = "Sensitive data gets a human sign-off"
    manifest, _ = read_manifest(app)
    level = (manifest or {}).get("data_classification")
    if level not in DATA_CLASSES:
        return Result("data-sensitivity", title, "SKIP", "no valid data_classification in the manifest")
    if level in {"confidential", "restricted"}:
        detail = f"handles {level} data: a human decides what may be sent to the LLM"
        return Result("data-sensitivity", title, "REVIEW", detail)
    return Result("data-sensitivity", title, "PASS", f"data classification: {level}")


RULES = [
    check_manifest,
    check_secrets,
    check_auth,
    check_dynamic_code,
    check_debug_off,
    check_llm_limits,
    check_health,
    check_logging,
    check_data_sensitivity,
]
