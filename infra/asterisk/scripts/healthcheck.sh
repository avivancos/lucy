#!/bin/sh
set -eu

/usr/sbin/asterisk -rx "core show uptime" >/dev/null
/usr/sbin/asterisk -rx "module show like chan_pjsip.so" | grep -q "chan_pjsip.so"
/usr/sbin/asterisk -rx "pjsip show endpoint lucy-lab" \
  | grep -Eq "Endpoint:[[:space:]]+lucy-lab"
/usr/bin/curl --fail --silent --show-error \
  --user "${LUCY_TELEPHONY_ARI_USER}:${LUCY_TELEPHONY_ARI_PASSWORD}" \
  "http://127.0.0.1:${LUCY_TELEPHONY_ARI_PORT}/ari/asterisk/info" >/dev/null
