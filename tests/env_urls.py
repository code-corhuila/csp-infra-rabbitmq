"""Static check of the AMQP URLs of the env examples (ADR-036), run without a broker.

The services read RELAY_AMQP_URL, TICKETING_AMQP_URL and CONCESSIONS_AMQP_URL. A URL that ends in a bare "/" is an empty
vhost for the Java amqp-client and Spring AMQP, so the broker answers "vhost not found". The default vhost "/" must be
written %2F, which every client reads the same way. The script prints one line per check and a total, and exits
non-zero when a check failed or none ran.
"""
import json
import pathlib
import re
import sys
from urllib.parse import urlsplit

ROOT = pathlib.Path(__file__).resolve().parent.parent
VARIABLES = ["RELAY_AMQP_URL", "TICKETING_AMQP_URL", "CONCESSIONS_AMQP_URL"]
VHOSTS = {vhost["name"] for vhost in json.loads((ROOT / "rabbitmq" / "definitions.json.template").read_text())["vhosts"]}

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


def vhost_of(url):
    """The vhost as the URL spells it: the path without its leading slash, still percent-encoded."""
    return urlsplit(url).path[1:]


def is_valid_vhost(spelled):
    return spelled == "%2F" or spelled in VHOSTS - {"/"}


for env_file in sorted((ROOT / "env").glob("*.env.example")):
    lines = dict(re.findall(r"^([A-Z_]+)=(.*)$", env_file.read_text(), re.MULTILINE))
    for variable in VARIABLES:
        url = lines.get(variable, "")
        check(f"{env_file.name}: {variable} is an amqp:// URL", url.startswith("amqp://"))
        check(f"{env_file.name}: {variable} ends in the default vhost written %2F", is_valid_vhost(vhost_of(url)))

readme = (ROOT / "README.md").read_text()
for url in re.findall(r"amqp://\S+", readme):
    url = url.rstrip("`|.,")
    check(f"README.md: {url} ends in the default vhost written %2F", is_valid_vhost(vhost_of(url)))

check("the validator rejects a trailing slash", not is_valid_vhost(vhost_of("amqp://u:p@rabbitmq:5672/")))
check("the validator rejects a missing path", not is_valid_vhost(vhost_of("amqp://u:p@rabbitmq:5672")))
check("the vhost / exists in the definitions", "/" in VHOSTS)

print(f"url checks: {total} run, {total - failed} passed, {failed} failed")
sys.exit(1 if failed or total == 0 else 0)
