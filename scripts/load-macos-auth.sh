#!/bin/zsh

set -u

readonly keychain_service="ai-design-skill.web-design-pro"
readonly account_name="$(/usr/bin/id -un)"

auth_value="$(/usr/bin/security find-generic-password \
  -a "$account_name" \
  -s "$keychain_service" \
  -w 2>/dev/null)" || {
  /bin/launchctl unsetenv WEB_DESIGN_PRO_TOKEN 2>/dev/null || true
  exit 1
}

if [[ "$auth_value" != wdp_* || ${#auth_value} -ne 47 || "$auth_value" == *[^A-Za-z0-9_-]* ]]; then
  unset auth_value
  /bin/launchctl unsetenv WEB_DESIGN_PRO_TOKEN 2>/dev/null || true
  exit 2
fi

/bin/launchctl setenv WEB_DESIGN_PRO_TOKEN "$auth_value"
unset auth_value
