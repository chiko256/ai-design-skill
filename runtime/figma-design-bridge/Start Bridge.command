#!/bin/zsh
cd "${0:A:h}"
bridge_node="${FIGMA_BRIDGE_NODE:-node}"
if ! command -v "$bridge_node" >/dev/null 2>&1; then
  if [[ -s "$HOME/.nvm/nvm.sh" ]]; then source "$HOME/.nvm/nvm.sh"; fi
fi
if ! command -v "$bridge_node" >/dev/null 2>&1; then
  print 'Node.js 18以上が必要です。CodexにCODEX_SETUP.mdを読んで設定するよう依頼してください。'
  read
  exit 1
fi
"$bridge_node" bridge/start.mjs
bridge_exit=$?
if (( bridge_exit != 0 )); then read; fi
exit $bridge_exit
