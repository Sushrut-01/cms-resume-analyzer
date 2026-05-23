@echo off
title K Recruit — Deploy

echo.
echo  ============================================
echo   K Recruit — Rancher Desktop
echo  ============================================
echo.

:: Check if nerdctl (Rancher Desktop) or docker is available
where nerdctl >nul 2>&1
if %errorlevel% equ 0 (
    set RUNTIME=nerdctl
) else (
    where docker >nul 2>&1
    if %errorlevel% equ 0 (
        set RUNTIME=docker
    ) else (
        echo [ERROR] Neither nerdctl nor docker found.
        echo Please install Rancher Desktop from rancherdesktop.io
        pause
        exit /b 1
    )
)

echo  Using container runtime: %RUNTIME%
echo.

:: ── Option 1: Docker Compose (Simplest) ─────────────────────────────────
echo  Choose deployment method:
echo  [1] Docker Compose  (recommended — simple, one command)
echo  [2] Kubernetes      (Rancher Desktop k3s — more resilient)
echo.
set /p CHOICE="Enter 1 or 2: "

if "%CHOICE%"=="1" goto COMPOSE
if "%CHOICE%"=="2" goto KUBERNETES
echo Invalid choice.
pause
exit /b 1


:COMPOSE
echo.
echo  Starting with Docker Compose...
echo  This will start the app + PostgreSQL database.
echo.
%RUNTIME%-compose up -d --build
if %errorlevel% neq 0 (
    :: fallback to docker compose (newer syntax)
    docker compose up -d --build
)
echo.
echo  ─────────────────────────────────────────
ipconfig | findstr /i "IPv4"
echo  ─────────────────────────────────────────
echo  HR Team URL: http://YOUR-IP-ABOVE:8000
echo.
echo  Useful commands:
echo    View logs:    %RUNTIME%-compose logs -f app
echo    Stop:         %RUNTIME%-compose down
echo    Restart:      %RUNTIME%-compose restart app
goto END


:KUBERNETES
echo.
echo  Building image for Kubernetes...
%RUNTIME% build -t k-recruit:latest .
if %errorlevel% neq 0 ( echo [ERROR] Build failed. & pause & exit /b 1 )

echo  Deploying to Rancher Desktop (k3s)...
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/postgres.yaml
kubectl apply -f k8s/app.yaml

echo.
echo  Waiting for pods to start...
kubectl rollout status deployment/cms-db  -n cms --timeout=60s
kubectl rollout status deployment/cms-app -n cms --timeout=60s

echo.
echo  ─────────────────────────────────────────
ipconfig | findstr /i "IPv4"
echo  ─────────────────────────────────────────
echo  HR Team URL: http://YOUR-IP-ABOVE:30800
echo.
echo  Useful commands:
echo    View pods:    kubectl get pods -n cms
echo    View logs:    kubectl logs -n cms deployment/cms-app
echo    Stop all:     kubectl delete namespace cms
goto END


:END
echo.
pause
