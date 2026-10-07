#!/bin/sh
# Pide/renueva el certificado HTTPS del servidor dentro de la VPN (Tailscale) y recarga Caddy.
# Uso: sudo ./renovar-cert-vpn.sh casa.xxxx.ts.net     (agregalo al cron: una vez por semana alcanza)
set -e
HOST="$1"; DIR="${CASA_DIR:-/etc/casa-servidor}/vpn-cert"
[ -n "$HOST" ] || { echo "Falta el nombre: casa.xxxx.ts.net (lo ves con: tailscale status)"; exit 1; }
mkdir -p "$DIR"
tailscale cert --cert-file "$DIR/casa.crt" --key-file "$DIR/casa.key" "$HOST"
chmod 600 "$DIR/casa.key"
if command -v systemctl >/dev/null 2>&1; then systemctl reload caddy || true; fi
echo "Certificado de $HOST actualizado."
