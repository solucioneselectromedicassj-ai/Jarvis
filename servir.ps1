# Sirve esta carpeta en http://localhost:8080 para probar la app con el micrófono (solo en esta PC).
# Uso: clic derecho > "Ejecutar con PowerShell", o:  powershell -ExecutionPolicy Bypass -File servir.ps1
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
$tipos = @{ '.html'='text/html; charset=utf-8'; '.js'='text/javascript'; '.json'='application/json'; '.png'='image/png'; '.gz'='application/gzip'; '.css'='text/css' }
$l = New-Object System.Net.HttpListener
$l.Prefixes.Add('http://localhost:8080/')
$l.Start()
Write-Host 'Casa lista en http://localhost:8080  (cerrá esta ventana para parar)'
Start-Process 'http://localhost:8080/'
while ($l.IsListening) {
  $c = $l.GetContext()
  $ruta = [System.Uri]::UnescapeDataString($c.Request.Url.AbsolutePath).TrimStart('/')
  if ($ruta -eq '') { $ruta = 'index.html' }
  $f = [System.IO.Path]::GetFullPath((Join-Path $raiz $ruta))
  if ($f.StartsWith($raiz) -and (Test-Path $f -PathType Leaf)) {
    $b = [System.IO.File]::ReadAllBytes($f)
    $ext = [System.IO.Path]::GetExtension($f).ToLower()
    $c.Response.ContentType = if ($tipos.ContainsKey($ext)) { $tipos[$ext] } else { 'application/octet-stream' }
    $c.Response.OutputStream.Write($b, 0, $b.Length)
  } else { $c.Response.StatusCode = 404 }
  $c.Response.Close()
}
