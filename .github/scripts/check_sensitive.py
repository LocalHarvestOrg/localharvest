#!/usr/bin/env python3
"""Refuse secrets and real documents before they reach this public repository.

    check_sensitive.py --staged         what is about to be committed (pre-commit hook)
    check_sensitive.py --unpushed SHA   commits not on GitHub yet (pre-push hook)
    check_sensitive.py --range A..B     every commit in a range (CI)
    check_sensitive.py --all            every tracked file

Files are read from git, never from the working tree, and are never executed, so
CI can safely inspect a pull request from a fork. Reports give the rule, file and
line but never the matched text: CI logs are public too.

A false positive goes in .github/sensitive-allowlist, which needs owner review.
"""

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import PurePosixPath
from typing import Iterable, NamedTuple

MAX_FILE_BYTES = 2_000_000
ALLOWLIST = ".github/sensitive-allowlist"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

DATA_FOLDERS = {"annotations", "model_output", "models", "pdfs"}
LOCK_FILES = {"Cargo.lock", "package-lock.json", "uv.lock"}

BLOCKED_EXTENSIONS = {
    "document": "pdf doc docx docm odt rtf ppt pptx odp pages epub",
    "spreadsheet or dataset": "xls xlsx xlsm ods numbers csv tsv parquet feather arrow jsonl ndjson",
    "email": "eml msg mbox pst",
    "database": "db sqlite sqlite3 dump bak",
    "archive": "zip 7z rar tar gz tgz bz2 xz",
    "scan or photo": "png jpg jpeg tif tiff bmp gif webp heic",
    "model weights": "gguf safetensors onnx pt pth ckpt",
    "key or certificate": "pem key p12 pfx jks keystore kdbx asc gpg ppk",
}
EXTENSION_KIND = {
    extension: kind
    for kind, extensions in BLOCKED_EXTENSIONS.items()
    for extension in extensions.split()
}

CREDENTIAL_FILES = {
    ".git-credentials", ".htpasswd", ".netrc", ".npmrc", ".pgpass", ".pypirc",
    "credentials.json", "id_dsa", "id_ecdsa", "id_ed25519", "id_rsa",
    "secrets.json", "secrets.toml", "secrets.yaml", "secrets.yml",
}
ENV_TEMPLATES = {".env.example", ".env.sample", ".env.template"}

# Real files keep their format whatever they are renamed to
MAGIC_BYTES = {
    b"%PDF-": "PDF",
    b"PK\x03\x04": "zip or Office file",
    b"SQLite format 3\x00": "SQLite database",
    b"\xd0\xcf\x11\xe0": "Office file",
    b"\x1f\x8b": "gzip archive",
    b"7z\xbc\xaf\x27\x1c": "7z archive",
    b"Rar!": "RAR archive",
    b"\xff\xd8\xff": "JPEG image",
    b"II*\x00": "TIFF scan",
    b"MM\x00*": "TIFF scan",
}

SECRET_PATTERNS = {
    "private-key": r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----",
    "aws-key": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    "github-token": r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b",
    "huggingface-token": r"\bhf_[A-Za-z0-9]{30,}\b",
    "anthropic-key": r"\bsk-ant-[A-Za-z0-9_-]{20,}",
    "openai-key": r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}",
    "google-key": r"\bAIza[0-9A-Za-z_-]{35}\b",
    "slack-token": r"\bxox[abeoprs]-[A-Za-z0-9-]{10,}",
    "stripe-key": r"\b[rs]k_live_[A-Za-z0-9]{16,}",
    "jwt": r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
}
SECRET_RULES = {rule: re.compile(pattern) for rule, pattern in SECRET_PATTERNS.items()}

URL_PASSWORD = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@'\"]+:([^\s/:@'\"]+)@[^\s/'\"]")
PLACEHOLDER_PASSWORDS = {"password", "passwd", "pass", "secret", "token", "xxx", "xxxx", "changeme"}

HOME_PATH = re.compile(r"(?:/Users/|/home/|\b[A-Za-z]:\\+Users\\+)([A-Za-z0-9._-]+)")
PLACEHOLDER_USERS = {"example", "me", "name", "runner", "shared", "ubuntu", "user", "username", "you"}

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})\b")
PUBLIC_EMAIL_DOMAINS = re.compile(
    r"(?:^|\.)(?:example\.(?:com|net|org)|[a-z0-9-]+\.example|[a-z0-9-]+\.test|"
    r"[a-z0-9-]+\.invalid|users\.noreply\.github\.com)$",
    re.IGNORECASE,
)
# name@2x.png and package@1.2.js look like addresses
NOT_EMAIL_ENDINGS = {"css", "gif", "ico", "jpg", "js", "json", "md", "png", "py", "rs", "scss", "svg", "ts", "vue", "webp"}

PRIVATE_ADDRESS = re.compile(
    r"(?<![\d.])(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}(?![\d.])"
)


class Finding(NamedTuple):
    rule: str
    path: str
    line: int
    detail: str


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def decode_path(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


def printable(path: str) -> str:
    """Escape a file name: a fork chooses it, and it is printed in CI logs."""
    return json.dumps(path)[1:-1]


def parse_raw_diff(output: bytes) -> list[tuple[str, str]]:
    """(path, blob id) pairs from `git diff-* --raw -z --no-renames`."""
    fields = output.split(b"\0")
    files = []
    for meta, path in zip(fields[0::2], fields[1::2]):
        _, new_mode, _, new_blob, _ = meta.decode().split(" ")
        if new_mode != "160000":  # a submodule has no content here
            files.append((decode_path(path), new_blob))
    return files


def staged_files() -> list[tuple[str, str]]:
    has_head = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "HEAD"], capture_output=True
    ).returncode == 0
    return parse_raw_diff(git(
        "diff-index", "--cached", "--raw", "-z", "--no-renames", "--no-abbrev",
        "--diff-filter=ACMT", "HEAD" if has_head else EMPTY_TREE,
    ))


def tracked_files() -> list[tuple[str, str]]:
    files = []
    for entry in git("ls-files", "--stage", "-z").split(b"\0"):
        if entry:
            meta, path = entry.split(b"\t", 1)
            mode, blob, _ = meta.decode().split(" ")
            if mode != "160000":
                files.append((decode_path(path), blob))
    return files


def files_in_commits(*rev_list_args: str) -> list[tuple[str, str]]:
    """Every file version the commits add, even if a later commit removes it."""
    files: dict[tuple[str, str], None] = {}
    for commit in git("rev-list", *rev_list_args).decode().split():
        changed = git(
            "diff-tree", "-r", "-m", "-z", "--root", "--raw", "--no-commit-id",
            "--no-renames", "--no-abbrev", "--diff-filter=ACMT", commit,
        )
        files.update(dict.fromkeys(parse_raw_diff(changed)))
    return list(files)


def check_path(path: str) -> Iterable[Finding]:
    pure = PurePosixPath(path)
    name = pure.name.lower()
    extension = pure.suffix.lower().lstrip(".")
    folders = DATA_FOLDERS.intersection(part.lower() for part in pure.parts[:-1])
    if folders:
        yield Finding("data-folder", path, 0, f"inside {sorted(folders)[0]}/")
    if extension in EXTENSION_KIND:
        yield Finding("file-type", path, 0, EXTENSION_KIND[extension])
    is_env_file = name == ".env" or (name.startswith(".env.") and name not in ENV_TEMPLATES)
    if is_env_file or name in CREDENTIAL_FILES or name.startswith("service-account"):
        yield Finding("credential-file", path, 0, "file that normally holds credentials")


def check_notebook(path: str, content: bytes) -> Iterable[Finding]:
    """Notebook outputs can hold extracted tables or page images."""
    try:
        cells = json.loads(content).get("cells", [])
        has_outputs = any(cell.get("outputs") for cell in cells)
    except (AttributeError, ValueError):
        yield Finding("notebook-output", path, 0, "not a readable notebook")
        return
    if has_outputs:
        yield Finding("notebook-output", path, 0, "clear the outputs before committing")


def check_line(path: str, number: int, line: str, is_lock_file: bool) -> Iterable[Finding]:
    for rule, pattern in SECRET_RULES.items():
        if pattern.search(line):
            yield Finding(rule, path, number, "looks like a live credential")
    for match in URL_PASSWORD.finditer(line):
        password = match.group(1)
        if password.lower() not in PLACEHOLDER_PASSWORDS and not set(password) & set("$<{[*%"):
            yield Finding("url-password", path, number, "password inside a URL")
    if is_lock_file:
        return
    for match in HOME_PATH.finditer(line):
        if match.group(1).lower() not in PLACEHOLDER_USERS:
            yield Finding("personal-path", path, number, "path with someone's user name")
    for match in EMAIL.finditer(line):
        domain = match.group(1)
        if (
            domain.rsplit(".", 1)[-1].lower() not in NOT_EMAIL_ENDINGS
            and not PUBLIC_EMAIL_DOMAINS.search(domain)
        ):
            yield Finding("email", path, number, "email address")
    if PRIVATE_ADDRESS.search(line):
        yield Finding("private-address", path, number, "internal network address")


def check_content(path: str, content: bytes) -> Iterable[Finding]:
    if len(content) > MAX_FILE_BYTES:
        yield Finding("large-file", path, 0, f"over {MAX_FILE_BYTES // 1_000_000} MB")
        return
    start = content[:1024].lstrip(b"\xef\xbb\xbf \t\r\n")
    for magic, kind in MAGIC_BYTES.items():
        if start.startswith(magic):
            yield Finding("file-content", path, 0, f"this is a {kind}")
            return
    if b"\0" in content[:8192]:
        return
    if path.lower().endswith(".ipynb"):
        yield from check_notebook(path, content)
    is_lock_file = PurePosixPath(path).name in LOCK_FILES
    text = content.decode("utf-8", "replace")
    for number, line in enumerate(text.splitlines(), start=1):
        yield from check_line(path, number, line, is_lock_file)


def load_allowlist(root: str) -> list[tuple[str, str]]:
    """`<rule> <path glob>` lines; `#` starts a comment."""
    entries = []
    try:
        with open(f"{root}/{ALLOWLIST}", encoding="utf-8") as allowlist:
            for line in allowlist:
                fields = line.split("#", 1)[0].split()
                if len(fields) == 2:
                    entries.append((fields[0], fields[1]))
                elif fields:
                    raise SystemExit(f"{ALLOWLIST}: expected `<rule> <path glob>`: {line.strip()}")
    except FileNotFoundError:
        pass
    return entries


def is_allowed(finding: Finding, allowlist: list[tuple[str, str]]) -> bool:
    return any(
        rule == finding.rule and fnmatch.fnmatchcase(finding.path, glob)
        for rule, glob in allowlist
    )


def scan(files: Iterable[tuple[str, str]], allowlist: list[tuple[str, str]]) -> list[Finding]:
    findings: dict[Finding, None] = {}
    for path, blob in files:
        content = git("cat-file", "blob", blob)
        for finding in (*check_path(path), *check_content(path, content)):
            if not is_allowed(finding, allowlist):
                findings[finding] = None
    return sorted(findings, key=lambda f: (f.path, f.line, f.rule))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true")
    mode.add_argument("--all", action="store_true")
    mode.add_argument("--range", metavar="A..B")
    mode.add_argument("--unpushed", metavar="SHA")
    args = parser.parse_args()

    try:
        root = git("rev-parse", "--show-toplevel").decode().strip()
        if args.staged:
            files = staged_files()
        elif args.all:
            files = tracked_files()
        elif args.range:
            files = files_in_commits(args.range)
        else:
            files = files_in_commits(args.unpushed, "--not", "--remotes")
        findings = scan(files, load_allowlist(root))
    except subprocess.CalledProcessError as error:
        print(f"Sensitive data check could not run: {error.stderr.decode(errors='replace')}", file=sys.stderr)
        return 2

    if not findings:
        print(f"Sensitive data check passed ({len(files)} files).")
        return 0
    for finding in findings:
        where = printable(finding.path) + (f":{finding.line}" if finding.line else "")
        print(f"BLOCKED  {finding.rule:<16} {where}  ({finding.detail})", file=sys.stderr)
    print(
        f"\n{len(findings)} problem(s). This repository is public: remove them from every commit.\n"
        f"If one is a false positive, propose it in {ALLOWLIST}.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
