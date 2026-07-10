#!/bin/bash
# setup-env-crypt.sh - Encrypt/decrypt .env with a password (OpenSSL AES-256-CBC)
#
# Usage:
#   bash/setup-env-crypt.sh --encrypt <password> [--hint <text>]
#   bash/setup-env-crypt.sh --decrypt <password>
#
# Options:
#   --encrypt <password>   Encrypt .env (only when plaintext)
#   --decrypt <password>   Decrypt .env (only when ciphertext)
#   --hint <text>          Store a password hint shown when decryption fails
#
# The encrypted .env is committed to git so secrets stay safe in the repo.
# Decrypt locally before running the application, re-encrypt before committing.
#
# Detection logic:
#   - .env plaintext: first line starts with "KEY=VALUE" or "# comment"  → --encrypt only
#   - .env ciphertext: first line is the magic header                     → --decrypt only

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
MAGIC_HEADER='##### ENCRYPTED with setup-env-crypt.sh #####'
HINT=""

# ── helpers ──────────────────────────────────────────────────────────

usage() {
    echo "Usage:"
    echo "  $(basename "$0") --encrypt <password> [--hint <text>]"
    echo "  $(basename "$0") --decrypt <password>"
    echo ""
    echo "Options:"
    echo "  --encrypt <password>   Encrypt .env (only when plaintext)"
    echo "  --decrypt <password>   Decrypt .env (only when ciphertext)"
    echo "  --hint <text>          Store password hint (use with --encrypt)"
    echo ""
    echo "Operations are idempotent: plaintext → encrypted (--encrypt), ciphertext → plaintext (--decrypt)."
    echo "Running --encrypt on an already-encrypted file (or --decrypt on plaintext) is an error."
    exit 1
}

die() {
    echo -e "Error: $*" >&2
    exit 1
}

is_encrypted() {
    [[ -f "$ENV_FILE" && -s "$ENV_FILE" ]] && head -1 "$ENV_FILE" | grep -qF "$MAGIC_HEADER"
}

# Extract password hint from the header of an encrypted .env (if present)
extract_hint() {
    head -1 "$ENV_FILE" | grep -o 'hint=[^ ]*' 2>/dev/null | cut -d= -f2 || true
}

TMP_FILE=""  # scoped for trap cleanup
cleanup() {
    [[ -n "$TMP_FILE" ]] && rm -f "$TMP_FILE"
}
trap cleanup EXIT

# ── argument parsing ────────────────────────────────────────────────────

MODE=""
PASSWORD=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --encrypt|--decrypt)
            MODE="$1"
            PASSWORD="$2"
            shift 2
            ;;
        --hint)
            HINT="$2"
            shift 2
            ;;
        *)
            usage
            ;;
    esac
done

[[ -n "$MODE" ]] || usage
[[ -n "$PASSWORD" ]] || die "password cannot be empty"

# ── main ────────────────────────────────────────────────────────────────

cd "$PROJECT_DIR"

case "$MODE" in
    --encrypt)
        if is_encrypted; then
            die ".env is already encrypted. Use --decrypt first."
        fi
        [[ -f "$ENV_FILE" ]] || die ".env not found at $ENV_FILE"
        [[ -s "$ENV_FILE" ]] || die ".env is empty — nothing to encrypt"

        TMP_FILE=$(mktemp)
        # Encrypt → base64, then prepend header (with optional hint)
        openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -salt \
            -pass "pass:$PASSWORD" -in "$ENV_FILE" | base64 > "$TMP_FILE"
        if [[ -n "$HINT" ]]; then
            { echo "$MAGIC_HEADER hint=$HINT"; cat "$TMP_FILE"; } > "$ENV_FILE"
        else
            { echo "$MAGIC_HEADER"; cat "$TMP_FILE"; } > "$ENV_FILE"
        fi
        echo "✓ .env encrypted — commit the encrypted file as-is to git."
        ;;

    --decrypt)
        if ! is_encrypted; then
            die ".env is not encrypted or does not exist. Use --encrypt first."
        fi

        TMP_FILE=$(mktemp)
        # Strip header, base64 decode, then decrypt
        tail -n +2 "$ENV_FILE" | base64 -d 2>/dev/null \
            | openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -d \
                -pass "pass:$PASSWORD" -out "$TMP_FILE" 2>/dev/null \
            || {
                _hint="$(extract_hint)"
                if [[ -n "$_hint" ]]; then
                    die "Decryption failed (wrong password or corrupted file).\nPassword hint: $_hint"
                else
                    die "Decryption failed (wrong password or corrupted file)."
                fi
            }

        mv "$TMP_FILE" "$ENV_FILE"
        echo "✓ .env decrypted — you can now edit API keys or run the application."
        ;;

    *)
        usage
        ;;
esac