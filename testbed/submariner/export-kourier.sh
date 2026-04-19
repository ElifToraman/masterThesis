#!/usr/bin/env bash
set -e

echo "Exporting Kourier internal service through Submariner..."
subctl export service kourier-internal -n kourier-system
kubectl get serviceexports -A
