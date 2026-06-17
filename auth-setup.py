#!/usr/bin/env python3
"""
auth-setup.py — One-command auth bootstrapper for Remote-Sense.

Automates every step from AUTH.md:
  1. Ensures RS_JWT_SECRET exists in .env (generates one if missing)
  2. Mints a 30-day HS256 JWT with configurable subject & roles
  3. Writes VITE_DEV_TOKEN into frontend/.env
  4. Prints a summary with next steps

Run from the project root:
    python auth-setup.py                       # defaults: sub="you", role=admin, 30 days
    python auth-setup.py --sub alice            # custom subject
    python auth-setup.py --roles analyst        # non-admin role
    python auth-setup.py --days 7               # shorter-lived token
    python auth-setup.py --roles viewer analyst  # multiple roles
    python auth-setup.py --dry-run              # preview without writing any files
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────

VALID_ROLES = ("viewer", "analyst", "publisher", "admin")

ROLE_PERMISSIONS = {
    "viewer":    ["Read analyses, interpretations, history"],
    "analyst":   ["+ Annotate, run analysis"],
    "publisher": ["+ Push to gateway / publish interpretations"],
    "admin":     ["Everything"],
}

# ──────────────────────────────────────────────────────────────────────
# Terminal colours (graceful fallback on Windows without ANSI support)
# ──────────────────────────────────────────────────────────────────────

# Force UTF-8 output on Windows to avoid cp1252 encoding errors
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace"
        )
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="replace"
        )
    except Exception:
        pass

try:
    os.system("")  # enable ANSI on Windows 10+
    _ANSI = True
except Exception:
    _ANSI = False

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _ANSI else text

def bold(t: str)   -> str: return _c("1", t)
def green(t: str)  -> str: return _c("32", t)
def yellow(t: str) -> str: return _c("33", t)
def red(t: str)    -> str: return _c("31", t)
def cyan(t: str)   -> str: return _c("36", t)
def dim(t: str)    -> str: return _c("2", t)

# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def b64url(raw: bytes) -> str:
    """Base64url-encode without padding (JWT spec)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict (handles comments, blank lines, quoting)."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip optional surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        env[key] = value
    return env


def set_env_value(path: Path, key: str, value: str) -> bool:
    """Set a key=value in a .env file. Returns True if the file was modified."""
    if not path.exists():
        path.write_text(f"{key}={value}\n", encoding="utf-8")
        return True

    content = path.read_text(encoding="utf-8")
    # Match the key line (with or without value), preserving inline comments isn't standard
    # for .env, so we just replace the whole line.
    pattern = re.compile(rf"^({re.escape(key)}\s*=).*$", re.MULTILINE)

    if pattern.search(content):
        new_content = pattern.sub(rf"\g<1>{value}", content)
        if new_content != content:
            path.write_text(new_content, encoding="utf-8")
            return True
        return False  # already set to same value
    else:
        # Key doesn't exist — append it
        if not content.endswith("\n"):
            content += "\n"
        content += f"{key}={value}\n"
        path.write_text(content, encoding="utf-8")
        return True


def mint_jwt(secret: str, sub: str, roles: list[str], days: int) -> tuple[str, int]:
    """Mint an HS256 JWT. Returns (token_string, expiry_unix_ts)."""
    exp = int(time.time()) + days * 86400
    header = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    claims = b64url(json.dumps({"sub": sub, "roles": roles, "exp": exp}).encode())
    sig = b64url(
        hmac.new(secret.encode(), f"{header}.{claims}".encode(), hashlib.sha256).digest()
    )
    return f"{header}.{claims}.{sig}", exp


def decode_jwt_claims(token: str) -> dict | None:
    """Decode the claims from a JWT (no verification, just base64 decode)."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        # Add back padding
        claims_b64 = parts[1]
        padding = 4 - len(claims_b64) % 4
        if padding != 4:
            claims_b64 += "=" * padding
        claims_json = base64.urlsafe_b64decode(claims_b64)
        return json.loads(claims_json)
    except Exception:
        return None


def format_expiry(unix_ts: int) -> str:
    """Format a unix timestamp to a readable local-time string."""
    return time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(unix_ts))


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap auth for Remote-Sense (JWT secret + dev token).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python auth-setup.py                        # admin token, 30 days
  python auth-setup.py --sub alice --days 7   # 7-day token for 'alice'
  python auth-setup.py --roles analyst        # analyst-only token
  python auth-setup.py --verify               # check existing setup
  python auth-setup.py --dry-run              # preview without writes
        """,
    )
    parser.add_argument(
        "--sub", default="you",
        help="JWT subject claim — identifies the token holder (default: 'you')",
    )
    parser.add_argument(
        "--roles", nargs="+", default=["admin"], choices=VALID_ROLES,
        help="Roles to embed in the token (default: admin)",
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="Token validity in days (default: 30)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would happen without writing any files",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Verify the current auth setup and report status",
    )
    parser.add_argument(
        "--force-new-secret", action="store_true",
        help="Generate a new RS_JWT_SECRET even if one already exists (invalidates all existing tokens!)",
    )

    args = parser.parse_args()

    # ── Resolve paths ────────────────────────────────────────────────
    project_root = Path(__file__).resolve().parent
    root_env = project_root / ".env"
    frontend_env = project_root / "frontend" / ".env"

    print()
    print(bold("╔══════════════════════════════════════════════════════════╗"))
    print(bold("║         🔐  Remote-Sense Auth Setup                     ║"))
    print(bold("╚══════════════════════════════════════════════════════════╝"))
    print()

    # ── Verify mode ──────────────────────────────────────────────────
    if args.verify:
        return verify_setup(root_env, frontend_env)

    # ── Step 1: RS_JWT_SECRET ────────────────────────────────────────
    print(bold("  Step 1 ─ JWT Secret"))
    print(f"  {dim('File:')} {root_env}")
    print()

    root_vars = read_env_file(root_env)
    existing_secret = root_vars.get("RS_JWT_SECRET", "")

    if existing_secret and not args.force_new_secret:
        masked = existing_secret[:8] + "…" + existing_secret[-4:]
        print(f"  {green('✓')} RS_JWT_SECRET already set: {dim(masked)}")
        jwt_secret = existing_secret
    else:
        jwt_secret = secrets.token_urlsafe(36)
        if args.force_new_secret and existing_secret:
            print(f"  {yellow('⟳')} Generating NEW secret (--force-new-secret)")
            print(f"  {yellow('⚠')} All existing tokens will be invalidated!")
        else:
            print(f"  {yellow('⟳')} No RS_JWT_SECRET found — generating one")

        if args.dry_run:
            masked = jwt_secret[:8] + "…" + jwt_secret[-4:]
            print(f"  {dim('Would set:')} RS_JWT_SECRET={masked}")
        else:
            set_env_value(root_env, "RS_JWT_SECRET", jwt_secret)
            masked = jwt_secret[:8] + "…" + jwt_secret[-4:]
            print(f"  {green('✓')} Wrote RS_JWT_SECRET={masked}")

    print()

    # ── Step 2: Mint JWT ─────────────────────────────────────────────
    print(bold("  Step 2 ─ Mint JWT Token"))
    print(f"  {dim('Subject:')} {args.sub}")
    print(f"  {dim('Roles:  ')} {', '.join(args.roles)}")
    print(f"  {dim('Valid:  ')} {args.days} days")
    print()

    token, exp_ts = mint_jwt(jwt_secret, args.sub, args.roles, args.days)

    print(f"  {green('✓')} Token minted (expires {format_expiry(exp_ts)})")
    print()

    # ── Step 3: Write to frontend/.env ───────────────────────────────
    print(bold("  Step 3 ─ Frontend Dev Token"))
    print(f"  {dim('File:')} {frontend_env}")
    print()

    if not frontend_env.parent.exists():
        print(f"  {red('✗')} frontend/ directory not found!")
        print(f"    Expected at: {frontend_env.parent}")
        print()
        print(f"  {yellow('Manual step:')} Add this to your frontend .env:")
        print(f"    VITE_DEV_TOKEN={token}")
        print()
    elif args.dry_run:
        print(f"  {dim('Would set:')} VITE_DEV_TOKEN=<token>")
    else:
        changed = set_env_value(frontend_env, "VITE_DEV_TOKEN", token)
        if changed:
            print(f"  {green('✓')} VITE_DEV_TOKEN updated in frontend/.env")
        else:
            print(f"  {green('✓')} VITE_DEV_TOKEN already up to date")

    print()

    # ── Summary ──────────────────────────────────────────────────────
    print(bold("  ────────────────────────────────────────────────────────"))
    print(bold("  Summary"))
    print(bold("  ────────────────────────────────────────────────────────"))
    print()

    if args.dry_run:
        print(f"  {yellow('⚠')}  Dry run — no files were modified.")
        print(f"     Re-run without --dry-run to apply changes.")
        print()

    # Role permissions table
    print(f"  {bold('Granted permissions:')}")
    for role in args.roles:
        perms = ROLE_PERMISSIONS.get(role, ["(unknown)"])
        print(f"    {cyan(role):20s} │ {', '.join(perms)}")
    print()

    # Token details
    print(f"  {bold('Token details:')}")
    print(f"    Subject:  {args.sub}")
    print(f"    Roles:    {', '.join(args.roles)}")
    print(f"    Expires:  {format_expiry(exp_ts)}")
    print(f"    Algorithm: HS256")
    print()

    # Next steps
    print(bold("  ────────────────────────────────────────────────────────"))
    print(bold("  Next Steps"))
    print(bold("  ────────────────────────────────────────────────────────"))
    print()
    print(f"  1. {bold('Restart the frontend dev server')} so Vite picks up")
    print(f"     the new VITE_DEV_TOKEN:")
    print(f"       {dim('cd frontend && npm run dev')}")
    print()
    print(f"  2. {bold('Restart the API server')} if you changed the JWT secret:")
    print(f"       {dim('docker compose up -d api')}")
    print(f"       {dim('  — or —')}")
    print(f"       {dim('python start.py')}")
    print()
    print(f"  3. For a {bold('collaborator')}, share the same RS_JWT_SECRET")
    print(f"     and have them run:")
    print(f"       {dim('python auth-setup.py --sub their-name')}")
    print()
    print(f"  4. To {bold('verify')} the current setup at any time:")
    print(f"       {dim('python auth-setup.py --verify')}")
    print()

    return 0


def verify_setup(root_env: Path, frontend_env: Path) -> int:
    """Check existing auth configuration and report status."""
    all_ok = True

    # Check root .env
    print(bold("  Checking .env"))
    print(f"  {dim('File:')} {root_env}")
    print()

    if not root_env.exists():
        print(f"  {red('✗')} .env file not found!")
        all_ok = False
    else:
        root_vars = read_env_file(root_env)
        secret = root_vars.get("RS_JWT_SECRET", "")
        algo = root_vars.get("RS_JWT_ALGORITHM", "HS256")

        if secret:
            masked = secret[:8] + "…" + secret[-4:]
            print(f"  {green('✓')} RS_JWT_SECRET    = {dim(masked)}")
        else:
            print(f"  {red('✗')} RS_JWT_SECRET    = {red('(empty or missing)')}")
            all_ok = False

        print(f"  {green('✓')} RS_JWT_ALGORITHM = {dim(algo)}")

        audience = root_vars.get("RS_JWT_AUDIENCE", "")
        if audience:
            print(f"  {green('✓')} RS_JWT_AUDIENCE  = {dim(audience)}")
        else:
            print(f"  {dim('·')} RS_JWT_AUDIENCE  = {dim('(not set — audience check disabled)')}")

    print()

    # Check frontend .env
    print(bold("  Checking frontend/.env"))
    print(f"  {dim('File:')} {frontend_env}")
    print()

    if not frontend_env.exists():
        print(f"  {red('✗')} frontend/.env not found!")
        all_ok = False
    else:
        fe_vars = read_env_file(frontend_env)
        token = fe_vars.get("VITE_DEV_TOKEN", "")

        if not token:
            print(f"  {red('✗')} VITE_DEV_TOKEN = {red('(empty or missing)')}")
            all_ok = False
        else:
            # Try to decode and check expiry
            claims = decode_jwt_claims(token)
            if claims is None:
                print(f"  {yellow('⚠')} VITE_DEV_TOKEN = {yellow('(set but could not decode)')}")
            else:
                exp = claims.get("exp", 0)
                sub = claims.get("sub", "?")
                roles = claims.get("roles", [])
                now = int(time.time())

                token_preview = token[:30] + "…"
                print(f"  {green('✓')} VITE_DEV_TOKEN = {dim(token_preview)}")
                print(f"      Subject:  {sub}")
                print(f"      Roles:    {', '.join(roles)}")

                if exp < now:
                    elapsed = (now - exp) // 86400
                    print(f"      Expires:  {red(format_expiry(exp))} {red(f'(EXPIRED {elapsed}d ago!)')}")
                    all_ok = False
                else:
                    remaining = (exp - now) // 86400
                    print(f"      Expires:  {green(format_expiry(exp))} ({remaining}d remaining)")

                # Cross-check: can we verify the signature?
                if root_env.exists():
                    root_vars = read_env_file(root_env)
                    secret = root_vars.get("RS_JWT_SECRET", "")
                    if secret:
                        # Re-sign header.claims with the secret and compare
                        parts = token.split(".")
                        expected_sig = b64url(
                            hmac.new(
                                secret.encode(),
                                f"{parts[0]}.{parts[1]}".encode(),
                                hashlib.sha256,
                            ).digest()
                        )
                        if expected_sig == parts[2]:
                            print(f"      Signature: {green('✓ valid (matches RS_JWT_SECRET)')}")
                        else:
                            print(f"      Signature: {red('✗ MISMATCH — token was signed with a different secret!')}")
                            print(f"                 {red('The API will reject this token with 401.')}")
                            all_ok = False

    print()

    # Verdict
    print(bold("  ────────────────────────────────────────────────────────"))
    if all_ok:
        print(f"  {green('✓')} {bold('Auth setup looks good!')}")
    else:
        print(f"  {red('✗')} {bold('Issues found.')} Run {dim('python auth-setup.py')} to fix.")
    print()

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
