"""Nomes canônicos dos tópicos MQTT do robô.

Centralizar os tópicos aqui evita "magic strings" espalhadas pelos serviços e
divergências de digitação entre os grupos. Qualquer mudança de tópico acontece
em um único lugar. Este módulo é o reflexo, em código, do docs/contrato-mqtt.md.
"""

# Raiz de todos os tópicos do robô.
ROOT = "robo"

# --- Comandos vindos do mundo externo (app de celular via ESP32) ---
# O serial_ingestor publica AQUI tudo que chega do Bluetooth, sem interpretar.
# O orquestrador assina este tópico e decide o roteamento.
COMANDO_ENTRADA = f"{ROOT}/comando/entrada"

# --- Motores (grupo de Movimento) ---
MOTORES_COMANDO = f"{ROOT}/motores/comando"
MOTORES_STATUS = f"{ROOT}/motores/status"

# --- Voz (grupo de IA) ---
VOZ_INTENCAO = f"{ROOT}/voz/intencao"
VOZ_FALAR = f"{ROOT}/voz/falar"

# --- GPS ---
GPS_POSICAO = f"{ROOT}/gps/posicao"

# --- Rota segura (planejada no app) ---
# O app envia a rota fatiada (inicio/ponto/fim) pelo mesmo caminho dos comandos;
# o orquestrador valida cada mensagem e a republica aqui. Sem consumidor hoje: é
# o gancho para um futuro serviço de navegação que siga a rota.
ROTA_COMANDO = f"{ROOT}/rota/comando"

# --- Wi-Fi ---
# Comando de provisionamento roteado pelo orquestrador para o serviço wifi.
# Chega originalmente como {"tipo":"wifi",...} no comando vindo do app.
WIFI_COMANDO = f"{ROOT}/wifi/comando"

# --- Sistema ---
SISTEMA_BATERIA = f"{ROOT}/sistema/bateria"
SISTEMA_HEARTBEAT = f"{ROOT}/sistema/heartbeat"
SISTEMA_BRIDGE_STATUS = f"{ROOT}/sistema/bridge_status"
# Estado da conexão Wi-Fi (publicado pelo serviço wifi após provisionamento).
SISTEMA_WIFI = f"{ROOT}/sistema/wifi"

# --- Telemetria (espelhada para a nuvem pela bridge do Mosquitto) ---
# Tudo que precisa ser PERSISTIDO no banco é publicado sob este prefixo.
TELEMETRIA = f"{ROOT}/telemetria"

# Padrão que a bridge usa para espelhar tudo para a nuvem.
TELEMETRIA_WILDCARD = f"{TELEMETRIA}/#"


def telemetria(tipo: str) -> str:
    """Monta um tópico de telemetria.

    Ex.: telemetria("gps") -> "robo/telemetria/gps".
    Use para o que deve ir para o histórico/nuvem (gps, bateria, status...).
    """
    return f"{TELEMETRIA}/{tipo}"


def heartbeat(servico: str) -> str:
    """Tópico de heartbeat de um serviço.

    Ex.: heartbeat("gps") -> "robo/sistema/heartbeat/gps".
    """
    return f"{SISTEMA_HEARTBEAT}/{servico}"
