@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if defined FIGMA_BRIDGE_NODE (
  "%FIGMA_BRIDGE_NODE%" bridge\start.mjs
) else (
  where node >nul 2>nul
  if errorlevel 1 (
    echo Node.js 18以上が必要です。CodexにCODEX_SETUP.mdを読んで設定するよう依頼してください。
    pause
    exit /b 1
  )
  node bridge\start.mjs
)
if errorlevel 1 (
  pause
  exit /b 1
)
pause
