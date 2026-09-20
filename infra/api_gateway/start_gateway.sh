#!/bin/sh
set -eu
EXPECTED_GATEWAY_TOKEN=$(cat /run/complect-secrets/gateway_token)
export EXPECTED_GATEWAY_TOKEN
exec /docker-entrypoint.sh kong docker-start
