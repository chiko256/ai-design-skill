#!/bin/zsh

set -eu

if [[ "$(/usr/bin/uname -s)" != "Darwin" ]]; then
  echo "ai-design-auth:error:macos-required" >&2
  exit 1
fi

readonly script_dir="${0:A:h}"
readonly loader_path="$script_dir/load-macos-auth.sh"
readonly keychain_service="ai-design-skill.web-design-pro"
readonly account_name="$(/usr/bin/id -un)"
readonly agent_label="com.ai-design-skill.web-design-pro-auth"
readonly agents_dir="$HOME/Library/LaunchAgents"
readonly agent_path="$agents_dir/$agent_label.plist"
readonly user_domain="gui/$(/usr/bin/id -u)"
readonly setup_mode="${1:-}"

if [[ $# -gt 1 || ( -n "$setup_mode" && "$setup_mode" != "--use-existing-token" ) ]]; then
  echo "ai-design-auth:error:unsupported-option" >&2
  exit 2
fi

if [[ ! -x "$loader_path" ]]; then
  echo "ai-design-auth:error:loader-missing" >&2
  exit 2
fi

if [[ "$loader_path" == *[\&\<\>]* ]]; then
  echo "ai-design-auth:error:unsupported-install-path" >&2
  exit 3
fi

if [[ "$setup_mode" != "--use-existing-token" ]]; then
  echo "契約者専用トークンを入力してEnterを押してください。入力内容は表示されません。"
  /usr/bin/security add-generic-password \
    -U \
    -a "$account_name" \
    -s "$keychain_service" \
    -l "AIデザインSkill web-design-pro" \
    -w
fi

if ! "$loader_path"; then
  echo "ai-design-auth:error:keychain-read-or-token-format" >&2
  exit 4
fi

/bin/mkdir -p "$agents_dir"
/bin/chmod 700 "$agents_dir"

/bin/cat > "$agent_path" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$agent_label</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>$loader_path</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>LimitLoadToSessionType</key>
  <string>Aqua</string>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>ThrottleInterval</key>
  <integer>30</integer>
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>/dev/null</string>
  <key>StandardErrorPath</key>
  <string>/dev/null</string>
</dict>
</plist>
PLIST

/bin/chmod 600 "$agent_path"
/usr/bin/plutil -lint "$agent_path" >/dev/null

/bin/launchctl bootout "$user_domain" "$agent_path" >/dev/null 2>&1 || true
/bin/launchctl bootstrap "$user_domain" "$agent_path"

echo "ai-design-auth:configured"
