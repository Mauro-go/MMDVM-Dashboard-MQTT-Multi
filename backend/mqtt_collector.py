#!/usr/bin/env python3
"""Dashboard-only MQTT collector; reconnects when private settings change.

SQLite events are retained for 48 hours. Cleanup runs at startup and once
per local calendar day after midnight, within the existing collector process.
"""
import json
import logging
import os
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from mqtt_store import connect, save, init_gateway_links, save_gateway_link, save_gateway_connection, save_dstar_link_history
from mqtt_settings import SETTINGS, load
import remote_agent
from multi_mqtt import accept_event

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("mqtt-collector")

RETENTION_HOURS = 48
CLEANUP_CHECK_INTERVAL = 60  # seconds; no additional service or timer


def cleanup_old_events(db):
    """Remove expired events without deleting the database or WAL files.

    SQLite datetime() interprets ISO-8601 timestamps with UTC offsets and
    fractional seconds, and compares them to the current UTC time.
    """
    before = db.total_changes
    db.execute(
        "DELETE FROM events WHERE datetime(timestamp) < datetime('now', ?)",
        (f"-{RETENTION_HOURS} hours",),
    )
    db.commit()
    removed = db.total_changes - before
    # PASSIVE does not wait for dashboard readers and is safe in WAL mode.
    db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    logger.info("SQLite cleanup completed: removed %d events older than %d hours", removed, RETENTION_HOURS)


def on_message(client, userdata, msg):
    try:
        gateway_topics = userdata.get("gateway_topics", {})
        gateway = gateway_topics.get(msg.topic)
        if gateway:
            event = json.loads(msg.payload.decode("utf-8"))
            if isinstance(event, dict):
                save_gateway_link(userdata["db"], gateway, event)
                save_gateway_connection(userdata["db"], gateway, event)
                if gateway == "dstargateway":
                    save_dstar_link_history(userdata["db"], gateway, event)
            return

        if remote_agent.ingest(msg.topic, msg.payload):
            return
        event = json.loads(msg.payload.decode("utf-8"))
        if not isinstance(event, dict) or not accept_event(msg.topic, event):
            return
        for mode, payload in event.items():
            if not isinstance(payload, dict):
                continue
            timestamp = payload.get("timestamp") or datetime.now(timezone.utc).isoformat()
            action = payload.get("action") or payload.get("state") or ""
            save(userdata["db"], str(timestamp), str(mode), str(action), payload)
            logger.info("%s %s %s", mode, action, timestamp)
    except (ValueError, UnicodeDecodeError, TypeError) as exc:
        logger.warning("Invalid MQTT JSON: %s", exc)
    except Exception:
        logger.exception("Failed to store MQTT event")


def main():
    db = connect()
    init_gateway_links(db)
    client = None
    current = None
    retry_at = 0
    last_cleanup_date = None
    next_cleanup_check = 0.0
    try:
        while True:
            now = time.monotonic()
            if now >= next_cleanup_check:
                next_cleanup_check = now + CLEANUP_CHECK_INTERVAL
                today = datetime.now().date()  # local date, midnight rollover
                if today != last_cleanup_date:
                    try:
                        cleanup_old_events(db)
                        last_cleanup_date = today
                    except Exception:
                        logger.exception("SQLite cleanup failed; will retry")
                        try:
                            db.rollback()
                        except Exception:
                            pass
            try:
                settings = load()
                if not SETTINGS.exists():
                    settings.update({
                        "host": os.environ.get("MMDVM_MQTT_HOST", "127.0.0.1"),
                        "port": int(os.environ.get("MMDVM_MQTT_PORT", "1883")),
                        "topic": os.environ.get("MMDVM_MQTT_TOPIC", "mmdvm/+/json")
                    })
                # COLLECTOR_DEPLOYMENT_RELOAD_V1
                import config as dashboard_config
                dashboard_config.reload_config()

                agent_enabled = remote_agent.enabled()
                agent_prefix = remote_agent.prefix() if agent_enabled else ""

                if agent_enabled and not agent_prefix:
                    raise ValueError("Remote Agent topic prefix is empty")

                signature = json.dumps({
                    "mqtt": settings,
                    "agent_enabled": agent_enabled,
                    "agent_prefix": agent_prefix,
                    "agent_node_id": remote_agent.node_id() if agent_enabled else ""
                }, sort_keys=True)
            except Exception:
                logger.exception("Invalid MQTT configuration; keeping current connection")
                time.sleep(3)
                continue
            if signature != current and time.monotonic() >= retry_at:
                if client is not None:
                    try:
                        client.disconnect()
                    except Exception:
                        pass
                    client = None
                try:
                    # GATEWAY_TOPICS_V1
                    topic = settings["topic"]
                    gateway_topics = {}
                    if topic.endswith("/json") and "+" not in topic and "#" not in topic:
                        base = topic[:-5]
                        gateway_topics = {
                            f"{base}/{name}/json": name
                            for name in (
                                "dmrgateway",
                                "ysfgateway",
                                "dstargateway",
                                "aprsgateway",
                            )
                        }

                    new_client = mqtt.Client(userdata={
                        "db": db,
                        "gateway_topics": gateway_topics,
                    })
                    new_client.on_message = on_message
                    new_client.on_subscribe = lambda *args: logger.info("MQTT subscription active (pid=%s)", os.getpid())
                    # COLLECTOR_CALLBACK_SNAPSHOT_V1
                    subscribed_topic = settings["topic"]
                    subscribed_gateways = tuple(gateway_topics)
                    subscribed_agent = agent_enabled
                    subscribed_prefix = agent_prefix

                    def connected(
                        c, userdata, flags, rc,
                        subscribed_topic=subscribed_topic,
                        subscribed_gateways=subscribed_gateways,
                        subscribed_agent=subscribed_agent,
                        subscribed_prefix=subscribed_prefix
                    ):
                        if rc == 0:
                            c.subscribe(subscribed_topic, qos=0)
                            for gateway_topic in subscribed_gateways:
                                c.subscribe(gateway_topic, qos=0)
                                logger.info("Subscribed gateway: %s", gateway_topic)
                            if subscribed_agent:
                                c.subscribe(subscribed_prefix + "/#", qos=1)
                                logger.info("Subscribed Agent: %s/#", subscribed_prefix)
                            logger.info("Subscribed: %s", subscribed_topic)
                        else:
                            logger.error("MQTT connect error: %s", rc)
                    new_client.on_connect = connected
                    if settings.get("username"):
                        new_client.username_pw_set(settings["username"], settings.get("password") or None)
                    if settings.get("tls"):
                        new_client.tls_set()
                    new_client.connect(settings["host"], int(settings["port"]), keepalive=60)
                    client = new_client
                    current = signature
                    logger.info("MQTT connection configured")
                except Exception as exc:
                    logger.error("MQTT connection failed: %s", exc)
                    retry_at = time.monotonic() + 5
            if client is not None:
                try:
                    rc = client.loop(timeout=1.0)
                    if rc != mqtt.MQTT_ERR_SUCCESS:
                        client.reconnect()
                except Exception as exc:
                    logger.warning("MQTT reconnect pending: %s", exc)
                    time.sleep(3)
            else:
                time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopped")
    finally:
        if client is not None:
            client.disconnect()
        db.close()


if __name__ == "__main__":
    main()
