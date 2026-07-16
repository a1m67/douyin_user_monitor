#!/usr/bin/env bash
set -euo pipefail

systemctl restart douyin-monitor-8900.service

sleep 1
ss -lntp | rg ':8900' || true
