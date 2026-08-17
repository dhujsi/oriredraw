$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$previewRoot = Join-Path $repoRoot ".pages-preview"
$pythonRoot = Join-Path $previewRoot "python"

New-Item -ItemType Directory -Force -Path $previewRoot, $pythonRoot | Out-Null
Copy-Item -Path (Join-Path $repoRoot "web\*") -Destination $previewRoot -Recurse -Force
Copy-Item -Path @(
    (Join-Path $repoRoot "foldability.py"),
    (Join-Path $repoRoot "reconstructor.py"),
    (Join-Path $repoRoot "web_bridge.py")
) -Destination $pythonRoot -Force

Write-Host "Static preview assembled at $previewRoot"
Write-Host "Serve it with: python -m http.server 4173 --directory `"$previewRoot`""
