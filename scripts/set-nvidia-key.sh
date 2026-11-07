#!/usr/bin/env bash
# Prompt for the NVIDIA API key (hidden) and save it to .env. Never echoes the key.
set -euo pipefail
ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"
touch "$ENV_FILE"; chmod 600 "$ENV_FILE"

read -rsp "Paste NVIDIA API key (nvapi-..., hidden): " KEY; echo
[[ "$KEY" == nvapi-* ]] || { echo "Doesn't start with nvapi- ; not saved."; exit 1; }
read -rp "Model id [nvidia/nemotron-3.5-super-vl-120b-a12b]: " MODEL
MODEL="${MODEL:-nvidia/nemotron-3.5-super-vl-120b-a12b}"

tmp="$(mktemp)"; chmod 600 "$tmp"
grep -vE '^(NVIDIA_API_KEY|NEMOTRON_MODEL)=' "$ENV_FILE" > "$tmp" || true
printf 'NVIDIA_API_KEY=%s\nNEMOTRON_MODEL=%s\n' "$KEY" "$MODEL" >> "$tmp"
mv "$tmp" "$ENV_FILE"; chmod 600 "$ENV_FILE"
unset KEY

echo "Saved to $ENV_FILE. Testing..."
set -a; . "$ENV_FILE"; set +a
code=$(curl -s -o /dev/null -w '%{http_code}' "${NVIDIA_BASE_URL:-https://integrate.api.nvidia.com/v1}/chat/completions" \
  -H "Authorization: Bearer $NVIDIA_API_KEY" -H 'Content-Type: application/json' \
  -d "{\"model\":\"$NEMOTRON_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":5}")
echo "Nemotron test: HTTP $code (200 = working)"
