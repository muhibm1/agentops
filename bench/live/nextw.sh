#!/usr/bin/env bash
# live/nextw.sh WORK ID: drive incident ID in WORK and print the outcome, pending key and schema
cd /home/user/agentops/bench
timeout 900 live/drive.sh "$1" "$2" | grep -E "ENDED|NEEDS|FAILED|awaiting" | cut -c1-260
tail -1 "$1/$2/events.jsonl" | python3 -c "
import json,sys;d=json.load(sys.stdin);k=d.get('key')
print(d['step'],k, json.load(open('$1/model/requests/%s.json'%k))['schema_name'] if k else '')"
