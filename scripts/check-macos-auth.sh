#!/bin/zsh

set -u

if [[ "$(/usr/bin/uname -s)" != "Darwin" ]]; then
  echo "ai-design-auth:check:macos-required"
  exit 1
fi

readonly keychain_service="ai-design-skill.web-design-pro"
readonly account_name="$(/usr/bin/id -un)"
readonly agent_label="com.ai-design-skill.web-design-pro-auth"
readonly agent_path="$HOME/Library/LaunchAgents/$agent_label.plist"
readonly user_domain="gui/$(/usr/bin/id -u)"

if /usr/bin/security find-generic-password -a "$account_name" -s "$keychain_service" >/dev/null 2>&1; then
  echo "ai-design-auth:check:keychain-item=present"
else
  echo "ai-design-auth:check:keychain-item=missing"
fi

if auth_value="$(/usr/bin/security find-generic-password -a "$account_name" -s "$keychain_service" -w 2>/dev/null)"; then
  if [[ "$auth_value" == wdp_* && ${#auth_value} -eq 47 && "$auth_value" != *[^A-Za-z0-9_-]* ]]; then
    echo "ai-design-auth:check:keychain-token=readable"
  else
    echo "ai-design-auth:check:keychain-token=invalid-format"
  fi
else
  echo "ai-design-auth:check:keychain-token=unreadable"
fi
unset auth_value

if [[ -f "$agent_path" ]] && /usr/bin/plutil -lint "$agent_path" >/dev/null 2>&1; then
  echo "ai-design-auth:check:launchagent-file=valid"
else
  echo "ai-design-auth:check:launchagent-file=missing-or-invalid"
fi

if /bin/launchctl print "$user_domain/$agent_label" >/dev/null 2>&1; then
  echo "ai-design-auth:check:launchagent=loaded"
else
  echo "ai-design-auth:check:launchagent=not-loaded"
fi

auth_value="$(/bin/launchctl getenv WEB_DESIGN_PRO_TOKEN 2>/dev/null)"
if [[ "$auth_value" == wdp_* && ${#auth_value} -eq 47 && "$auth_value" != *[^A-Za-z0-9_-]* ]]; then
  echo "ai-design-auth:check:gui-token=present"
else
  echo "ai-design-auth:check:gui-token=missing-or-invalid"
fi
unset auth_value
