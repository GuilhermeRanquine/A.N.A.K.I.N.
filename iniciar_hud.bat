@echo off
title A.N.A.K.I.N - Tactical CyberHUD
set PATH=%LOCALAPPDATA%\Microsoft\dotnet;%PATH%
echo [A.N.A.K.I.N] Inicializando Cockpit Tatico CyberHUD (.NET 9)...
dotnet run --project src\Anakin.UI.CyberHUD\Anakin.UI.CyberHUD.csproj
