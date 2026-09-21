#!/usr/bin/env bash
#
# The bridge counts as running only while it is up and not explicitly unhealthy.
#
# "starting" counts as running. The offline scan legitimately takes minutes, and
# treating that as down would have the lease timer recreate the container
# underneath itself on every tick.
#
# restart: "no" stays in compose deliberately, because the lease decides which
# host runs this singleton and a self-restarting container would outvote it.
# Recovery therefore has to arrive through the lease timer, which is why a
# wedged container has to read as not running here.
set -uo pipefail

name=livesync-bridge

docker ps --filter "name=^${name}$" --format '{{.Names}}' | grep -qx "$name" || exit 1
docker ps --filter "name=^${name}$" --filter health=unhealthy --format '{{.Names}}' | grep -qx "$name" && exit 1
exit 0
