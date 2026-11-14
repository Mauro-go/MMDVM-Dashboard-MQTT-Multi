#!/usr/bin/env bash
set -Eeuo pipefail

APP="${1:?Uso: update-dmr-ids.sh DIRECTORY_APP}"
DB_DIR="$APP/data/databases"
DB_FILE="$DB_DIR/DMRIds.dat"
URL="https://raw.githubusercontent.com/DMR-Database/database-beta/master/DMRIds.dat"

[[ -d "$DB_DIR" ]] || {
    echo "ERRORE: directory database inesistente: $DB_DIR" >&2
    exit 1
}

TMP=$(mktemp "$DB_DIR/.dmrids.XXXXXXXX")
trap 'rm -f -- "$TMP"' EXIT

echo "Aggiornamento database DMR: $DB_FILE"

curl --fail --location --silent --show-error \
    --retry 2 --connect-timeout 15 --max-time 180 \
    --output "$TMP" "$URL"

# Controllo formato e numero minimo di record.
awk '
    BEGIN { valid=0; invalid=0 }
    NF == 0 { next }
    NF == 1 && $1 ~ /^[0-9]+$/ { next }
    $1 ~ /^[0-9]+$/ && NF >= 2 { valid++; next }
    { invalid++ }
    END {
        if (valid < 100 || invalid > 0) exit 1
    }
' "$TMP" || {
    echo "ERRORE: database scaricato non valido." >&2
    exit 1
}

# Mantiene proprietario e gruppo della directory database.
OWNER=$(stat -c '%u:%g' "$DB_DIR")
chown "$OWNER" "$TMP"
chmod 0640 "$TMP"

mv -f -- "$TMP" "$DB_FILE"

echo "Database DMR aggiornato correttamente."
wc -l "$DB_FILE"
