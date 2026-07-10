@echo off
setlocal

echo [Mira] Native Windows backend startup is not supported for Docker sandbox runtime.
echo [Mira] Start Mira from WSL2 instead:
echo [Mira]   wsl
echo [Mira]   cd /path/to/mira
echo [Mira]   sh start.sh
echo [Mira]
echo [Mira] Enable Docker Desktop WSL integration before starting.
exit /b 1
