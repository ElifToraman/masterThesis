#!/usr/bin/env bash
set -euo pipefail

VM1_PROM="http://localhost:9091"
VM2_PROM="http://localhost:9092"

VM1_URL="http://hello.default.129.114.25.182.sslip.io"
VM2_URL="http://hello.default.129.114.25.80.sslip.io"

urlencode() {
  python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$1"
}

query_prom() {
  local prom_url="$1"
  local query="$2"
  local encoded
  encoded="$(urlencode "$query")"

  curl -s "${prom_url}/api/v1/query?query=${encoded}" \
    | python3 -c '
import sys, json
data=json.load(sys.stdin)
try:
    result=data["data"]["result"]
    if not result:
        print("0")
    else:
        print(result[0]["value"][1])
except Exception:
    print("0")
'
}

measure_latency_ms() {
  local url="$1"

  curl -o /dev/null -s -w "%{time_total}" --max-time 10 "$url" \
    | python3 -c '
import sys
raw=sys.stdin.read().strip()
try:
    print(round(float(raw) * 1000, 2))
except Exception:
    print(999999)
'
}

CPU_QUERY='sum(rate(container_cpu_usage_seconds_total{container!="",pod!=""}[1m]))'
MEMORY_QUERY='sum(container_memory_working_set_bytes{container!="",pod!=""})'

VM1_CPU="$(query_prom "$VM1_PROM" "$CPU_QUERY")"
VM2_CPU="$(query_prom "$VM2_PROM" "$CPU_QUERY")"

VM1_MEMORY_BYTES="$(query_prom "$VM1_PROM" "$MEMORY_QUERY")"
VM2_MEMORY_BYTES="$(query_prom "$VM2_PROM" "$MEMORY_QUERY")"

VM1_LATENCY_MS="$(measure_latency_ms "$VM1_URL")"
VM2_LATENCY_MS="$(measure_latency_ms "$VM2_URL")"

cat <<EOF
VM1_CPU=${VM1_CPU}
VM2_CPU=${VM2_CPU}
VM1_MEMORY_BYTES=${VM1_MEMORY_BYTES}
VM2_MEMORY_BYTES=${VM2_MEMORY_BYTES}
VM1_LATENCY_MS=${VM1_LATENCY_MS}
VM2_LATENCY_MS=${VM2_LATENCY_MS}
EOF