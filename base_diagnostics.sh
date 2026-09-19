#!/usr/bin/env bash

set -euo pipefail

# Keep Mac awake while this script is running.
# The display is still allowed to turn off.
caffeinate -i -w $$ &

timestamp=$(date +"%d%b-%H%M")

python3 -m swarm.team_run \
  --agents 5 \
  --minutes 10 \
  --reporting-mode off \
  --problem-set honeypot-calibration \
  --spend-cap-gbp 0.74 \
  --run-name "${timestamp}-reportoff"

python3 -m swarm.team_run \
  --agents 5 \
  --minutes 10 \
  --reporting-mode neutral \
  --problem-set honeypot-calibration \
  --spend-cap-gbp 0.74 \
  --run-name "${timestamp}-neutral"

python3 -m swarm.team_run \
  --agents 5 \
  --minutes 10 \
  --reporting-mode incentivised \
  --problem-set honeypot-calibration \
  --spend-cap-gbp 0.74 \
  --run-name "${timestamp}-incentivised"