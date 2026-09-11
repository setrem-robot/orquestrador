#!/usr/bin/env bash
# Publica uma cópia preparada do site na instalação existente da Atlas.
# Uso: bash publicar-site.sh DIRETORIO_CLOUD DIRETORIO_PREPARADO
set -euo pipefail
umask 077

cloud="$(realpath "${1:?informe o diretório cloud}")"
preparado="$(realpath "${2:?informe o diretório preparado}")"
test -f "$cloud/docker-compose.yml"
test -f "$cloud/caddy/Caddyfile"
test -f "$preparado/site/index.html"
test -f "$preparado/site/painel/index.html"
test -f "$preparado/Caddyfile"

# A senha é digitada pelo operador no terminal; não entra em arquivo nem log.
sudo -v
sudo docker run --rm -v "$preparado/Caddyfile:/etc/caddy/Caddyfile:ro" \
  caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile

backup="$cloud/backups/site-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup"
cp "$cloud/docker-compose.yml" "$backup/docker-compose.yml"
cp "$cloud/caddy/Caddyfile" "$backup/Caddyfile"
if test -d "$cloud/site"; then cp -a "$cloud/site" "$backup/site"; fi

restaurar() {
  echo "Publicação incompleta; restaurando a configuração anterior. Backup: $backup" >&2
  cp "$backup/docker-compose.yml" "$cloud/docker-compose.yml"
  cp "$backup/Caddyfile" "$cloud/caddy/Caddyfile"
  if test -d "$backup/site"; then cp -a "$backup/site/." "$cloud/site/"; fi
  (cd "$cloud" && sudo docker compose up -d --no-deps --force-recreate caddy) || true
}
trap restaurar ERR

mkdir -p "$cloud/site"
cp -a "$preparado/site/." "$cloud/site/"
cp "$preparado/Caddyfile" "$cloud/caddy/Caddyfile"
python3 - "$cloud/docker-compose.yml" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
anchor = '      - ./caddy/Caddyfile:/etc/caddy/Caddyfile:ro'
mount = '      - ./site:/srv/atlas:ro'
if mount not in s:
    if s.count(anchor) != 1:
        raise SystemExit('Montagem do Caddy mudou; revisar compose antes de publicar.')
    s = s.replace(anchor, anchor + '\n' + mount)
    p.write_text(s)
PY

cd "$cloud"
sudo docker compose config --quiet
sudo docker compose up -d --no-deps --force-recreate caddy
ok=0
for tentativa in 1 2 3 4 5; do
  if sudo docker exec caddy-cloud wget -q -O /dev/null http://127.0.0.1/ && \
     sudo docker exec caddy-cloud wget -q -O /dev/null http://127.0.0.1/painel/ && \
     sudo docker exec caddy-cloud wget -q -O /dev/null http://127.0.0.1/api/saude; then
    ok=1
    break
  fi
  sleep 2
done
test "$ok" = 1
trap - ERR
echo "Site publicado. Backup anterior: $backup"
echo "Apresentação: https://atlas.kerlonr.com.br/"
echo "Painel: https://atlas.kerlonr.com.br/painel/"
