#!/usr/bin/env bash
#
# A telemetria está chegando ao LARCC?
#
# A bridge do Mosquitto falha em silêncio por natureza: o robô continua andando,
# os serviços continuam publicando, e só o histórico deixa de existir. Este
# script torna isso visível em cinco segundos, sem abrir o banco.
#
# Uso (no Raspberry Pi):
#   ./pi/scripts/verificar-telemetria.sh
#
# O que ele lê:
#   robo/sistema/bridge_status      1 = ligada ao LARCC, 0 = fora
#   $SYS/broker/messages/stored     o que está guardado esperando subir
#   robo/telemetria/#               o último valor de cada tipo (são retidos)
set -uo pipefail

HOST="${MQTT_HOST:-127.0.0.1}"
PORTA="${MQTT_PORT:-1883}"
ESPERA="${ESPERA:-3}"

command -v mosquitto_sub >/dev/null || {
    echo "mosquitto_sub não encontrado: sudo apt install mosquitto-clients" >&2
    exit 1
}

ler() {
    # -W encerra sozinho; sem ele o script ficaria pendurado quando o tópico
    # não existe — que é justamente um dos casos que queremos relatar.
    mosquitto_sub -h "${HOST}" -p "${PORTA}" -t "$1" -C "${2:-1}" -W "${ESPERA}" 2>/dev/null
}

echo "=== Telemetria do robô -> LARCC ==="
echo

ESTADO="$(ler 'robo/sistema/bridge_status')"
case "${ESTADO}" in
    1) echo "  bridge:    LIGADA ao LARCC" ;;
    0) echo "  bridge:    FORA — o que for publicado agora fica na fila local" ;;
    "") echo "  bridge:    sem notícia. A bridge está configurada?"
        echo "             (pi/mosquitto/config/conf.d/bridge.conf — copie do .example)" ;;
    *) echo "  bridge:    resposta inesperada: ${ESTADO}" ;;
esac

GUARDADAS="$(ler '$SYS/broker/messages/stored')"
if [[ -n "${GUARDADAS}" ]]; then
    echo "  na fila:   ${GUARDADAS} mensagens guardadas no broker local"
    # O teto está em mosquitto.conf. Passar dele não dá erro: o Mosquitto
    # descarta as mais antigas em silêncio, e o buraco só aparece no banco.
    if (( ${GUARDADAS%%.*} > 90000 )); then
        echo "             ^ perto do teto de 100000: aumente max_queued_messages"
        echo "               ou verifique por que a bridge não sobe há tanto tempo"
    fi
fi

echo
echo "  Último valor de cada tipo (tópicos retidos):"
ULTIMOS="$(mosquitto_sub -h "${HOST}" -p "${PORTA}" -t 'robo/telemetria/#' \
    -v -W "${ESPERA}" 2>/dev/null)"
if [[ -z "${ULTIMOS}" ]]; then
    echo "    (nada) — nenhum serviço publicou telemetria ainda."
    echo "    O que espelha para robo/telemetria/* é o orquestrador:"
    echo "      systemctl status robo-orquestrador"
else
    echo "${ULTIMOS}" | sed 's/^/    /'
fi

echo
echo "Para conferir do outro lado, na VM do LARCC:"
echo "  docker exec -it timescaledb psql -U robo -d robo \\"
echo "    -c \"select tipo, count(*), max(ts) from telemetria group by tipo;\""
