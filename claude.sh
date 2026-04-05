#!/bin/bash

docker compose -f $(dirname $0)/docker/docker-compose.yml run -q --rm -i claude-code bash "$@"
