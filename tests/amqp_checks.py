"""AMQP checks of the access rules and limits of ADR-036, run against a real broker.

The shell test (tests/instance.sh) starts it in a throwaway container on the platform network. It reads the broker
host and the passwords from the environment, prints one line per check and a total at the end, and exits non-zero when
any check failed.
"""
import os
import sys
import time

import pika
from pika.exceptions import AMQPError, ChannelClosedByBroker, NackError, UnroutableError

HOST = os.environ["BROKER_HOST"]
PASSWORDS = {
    "rabbit_admin": os.environ["RABBIT_ADMIN_PASSWORD"],
    "worker_relay": os.environ["WORKER_RELAY_PASSWORD"],
    "ticketing_fulfillment": os.environ["TICKETING_FULFILLMENT_PASSWORD"],
    "concessions": os.environ["CONCESSIONS_PASSWORD"],
}
CONSUMERS = ["ticketing_fulfillment", "concessions"]
EVENTS = "cine.events"
DLX = "cine.dlx"
ROUTING_KEY = "cine.auth.user-registered.v1"
MAX_MESSAGE_SIZE = 131072
MAX_LENGTH = 10000

total = 0
failed = 0


def check(description, condition):
    global total, failed
    total += 1
    if condition:
        print(f"ok   {description}")
    else:
        failed += 1
        print(f"FAIL {description}")


def connect(user, password=None):
    credentials = pika.PlainCredentials(user, PASSWORDS.get(user, "") if password is None else password)
    return pika.BlockingConnection(pika.ConnectionParameters(HOST, credentials=credentials, heartbeat=0))


def refused(user, action):
    """True when the broker answers 403 ACCESS_REFUSED to the action (a fresh channel per attempt)."""
    connection = connect(user)
    try:
        action(connection.channel())
    except ChannelClosedByBroker as error:
        return error.reply_code == 403
    except AMQPError:
        return False
    finally:
        if connection.is_open:
            connection.close()
    return False


def publish(channel, exchange, routing_key, body):
    """Publishes with confirms, so that a refusal of the broker surfaces here and not later on the connection."""
    channel.confirm_delivery()
    channel.basic_publish(exchange, routing_key, body)


def get_eventually(channel, queue, seconds=5):
    """basic_get with a short wait: dead-lettering is asynchronous."""
    deadline = time.time() + seconds
    while True:
        method, properties, body = channel.basic_get(queue)
        if method is not None or time.time() > deadline:
            return method, properties, body
        time.sleep(0.2)


def login_refused(user, password):
    try:
        connect(user, password).close()
    except AMQPError:
        return True
    return False


def queue_names(service, event):
    queue = f"cine.{service}.{event}"
    return queue, f"{queue}.dlq"


def declare_consumer_topology(channel, service, event, routing_key=ROUTING_KEY):
    """What each consumer does at start-up (ADR-036): queue + DLQ, both bound, dead-lettering to cine.dlx."""
    queue, dlq = queue_names(service, event)
    channel.queue_declare(dlq, durable=True)
    channel.queue_bind(dlq, DLX, queue)
    channel.queue_declare(queue, durable=True, arguments={"x-dead-letter-exchange": DLX, "x-dead-letter-routing-key": queue})
    channel.queue_bind(queue, EVENTS, routing_key)
    return queue, dlq


def delete_queues(service, event):
    connection = connect(service)
    channel = connection.channel()
    for name in queue_names(service, event):
        channel.queue_delete(name)
    connection.close()


def check_logins():
    check("guest is refused", login_refused("guest", "guest"))
    for user in PASSWORDS:
        check(f"{user} is refused with a wrong password", login_refused(user, "wrong"))
        try:
            connect(user).close()
            check(f"{user} connects with its password", True)
        except AMQPError:
            check(f"{user} connects with its password", False)


def check_relay():
    connection = connect("worker_relay")
    channel = connection.channel()
    channel.confirm_delivery()
    try:
        channel.basic_publish(EVENTS, ROUTING_KEY, b"{}", mandatory=True)
        check("worker_relay publishes to cine.events (no queue bound: unroutable is reported)", False)
    except UnroutableError:
        check("worker_relay publishes to cine.events (no queue bound: unroutable is reported)", True)
    connection.close()
    check("worker_relay cannot publish to cine.dlx", refused("worker_relay", lambda ch: publish(ch, DLX, "x", b"{}")))
    check("worker_relay cannot publish to the default exchange", refused("worker_relay", lambda ch: publish(ch, "", "cine.concessions.x", b"{}")))
    check("worker_relay cannot declare a queue", refused("worker_relay", lambda ch: ch.queue_declare("cine.worker_relay.x")))
    check("worker_relay cannot declare a queue of a consumer", refused("worker_relay", lambda ch: ch.queue_declare("cine.concessions.x")))
    check("worker_relay cannot declare an exchange", refused("worker_relay", lambda ch: ch.exchange_declare("cine.relay", "topic")))
    check("worker_relay cannot read a queue", refused("worker_relay", lambda ch: ch.basic_get("cine.concessions.x")))
    check("worker_relay cannot bind", refused("worker_relay", lambda ch: ch.queue_bind("cine.concessions.x", EVENTS, "k")))


def check_consumer_flow(service):
    event = "user-registered"
    connection = connect(service)
    channel = connection.channel()
    queue, dlq = declare_consumer_topology(channel, service, event)
    check(f"{service} declares its queue and its DLQ and binds them", True)

    relay = connect("worker_relay")
    relay_channel = relay.channel()
    relay_channel.confirm_delivery()
    relay_channel.basic_publish(EVENTS, ROUTING_KEY, b'{"n":1}', mandatory=True)
    relay_channel.basic_publish(EVENTS, ROUTING_KEY, b'{"n":2}', mandatory=True)
    relay.close()

    method, _, body = channel.basic_get(queue)
    check(f"{service} consumes what the relay published through cine.events", method is not None and body == b'{"n":1}')
    channel.basic_ack(method.delivery_tag)

    method, _, body = channel.basic_get(queue)
    channel.basic_nack(method.delivery_tag, requeue=False)
    method, _, body = get_eventually(channel, dlq)
    check(f"{service}: a nack without requeue reaches its DLQ through cine.dlx", method is not None and body == b'{"n":2}')
    channel.basic_ack(method.delivery_tag)
    connection.close()

    check(f"{service} cannot publish to cine.events", refused(service, lambda ch: publish(ch, EVENTS, ROUTING_KEY, b"{}")))
    check(f"{service} publishes to cine.dlx", not refused(service, lambda ch: publish(ch, DLX, queue, b"{}")))
    other = "concessions" if service == "ticketing_fulfillment" else "ticketing_fulfillment"
    check(f"{service} cannot declare a queue of {other}", refused(service, lambda ch: ch.queue_declare(f"cine.{other}.user-registered")))
    check(f"{service} cannot declare a queue outside cine.{service}.", refused(service, lambda ch: ch.queue_declare("cine.other.user-registered")))
    check(f"{service} cannot declare a queue named as its prefix without a dot", refused(service, lambda ch: ch.queue_declare(f"cine.{service}x.a")))
    check(f"{service} cannot declare an exchange named cine.events", refused(service, lambda ch: ch.exchange_declare(EVENTS, "topic", passive=False, durable=False)))
    check(f"{service} cannot dead-letter into a foreign exchange", refused(service, lambda ch: ch.queue_declare(f"cine.{service}.bad-dlx", arguments={"x-dead-letter-exchange": "amq.direct"})))
    check(f"{service} cannot delete the exchange cine.events", refused(service, lambda ch: ch.exchange_delete(EVENTS)))
    check(f"{service} cannot bind a foreign queue", refused(service, lambda ch: ch.queue_bind(f"cine.{other}.user-registered", EVENTS, "k")))
    check(f"{service} cannot read a foreign queue", refused(service, lambda ch: ch.basic_get(f"cine.{other}.user-registered")))
    check(f"{service} cannot publish to a foreign queue by the default exchange", refused(service, lambda ch: publish(ch, "", f"cine.{other}.user-registered", b"{}")))
    delete_queues(service, event)


def check_foreign_queues_untouchable():
    ticketing = connect("ticketing_fulfillment")
    queue, dlq = declare_consumer_topology(ticketing.channel(), "ticketing_fulfillment", "isolation")
    ticketing.close()
    check("concessions cannot read the queue of ticketing_fulfillment", refused("concessions", lambda ch: ch.basic_get(queue)))
    check("concessions cannot purge the queue of ticketing_fulfillment", refused("concessions", lambda ch: ch.queue_purge(queue)))
    check("concessions cannot delete the queue of ticketing_fulfillment", refused("concessions", lambda ch: ch.queue_delete(queue)))
    check("concessions cannot read the DLQ of ticketing_fulfillment", refused("concessions", lambda ch: ch.basic_get(dlq)))
    check("concessions cannot publish to the DLQ of ticketing_fulfillment by the default exchange", refused("concessions", lambda ch: publish(ch, "", dlq, b"{}")))
    delete_queues("ticketing_fulfillment", "isolation")


def check_limits():
    service = "concessions"
    connection = connect(service)
    channel = connection.channel()
    queue, _ = declare_consumer_topology(channel, service, "limits", "cine.auth.limits.v1")
    relay = connect("worker_relay")
    relay_channel = relay.channel()
    relay_channel.confirm_delivery()
    relay_channel.basic_publish(EVENTS, "cine.auth.limits.v1", b"x" * 100_000, mandatory=True)
    check("a message of 100 KB is accepted", True)
    try:
        oversized = connect("worker_relay")
        oversized_channel = oversized.channel()
        oversized_channel.confirm_delivery()
        oversized_channel.basic_publish(EVENTS, "cine.auth.limits.v1", b"x" * (MAX_MESSAGE_SIZE + 1), mandatory=True)
        check("a message above max_message_size is rejected", False)
    except AMQPError:
        check("a message above max_message_size is rejected", True)
    channel.queue_purge(queue)

    accepted = 0
    rejected = False
    try:
        for _ in range(MAX_LENGTH + 1):
            relay_channel.basic_publish(EVENTS, "cine.auth.limits.v1", b"{}", mandatory=True)
            accepted += 1
    except NackError:
        rejected = True
    check(f"a queue over its max-length rejects the publish (overflow reject-publish) after {MAX_LENGTH} messages", rejected and accepted == MAX_LENGTH)
    relay.close()
    arguments = channel.queue_declare(queue, passive=True)
    check("the queue keeps exactly max-length messages", arguments.method.message_count == MAX_LENGTH)
    connection.close()
    delete_queues(service, "limits")


def main():
    check_logins()
    check_relay()
    for consumer in CONSUMERS:
        check_consumer_flow(consumer)
    check_foreign_queues_untouchable()
    check_limits()
    print(f"amqp checks: {total} run, {total - failed} passed, {failed} failed")
    return 1 if failed or total == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
