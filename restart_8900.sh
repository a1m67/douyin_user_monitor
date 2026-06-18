#!/usr/bin/env bash
set -euo pipefail

systemctl restart douyin-api-8899.service
systemctl restart douyin-monitor-8900.service

sleep 1
ss -lntp | rg ':(8899|8900)'
