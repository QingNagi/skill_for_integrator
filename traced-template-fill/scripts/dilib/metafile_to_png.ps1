Add-Type -AssemblyName System.Drawing
$jobs = Get-Content -LiteralPath $args[0] -Encoding UTF8
$results = @()
foreach ($line in $jobs) {
  if (-not $line -or -not $line.Trim()) { continue }
  $p = $line.Split("`t"); [string]$src = $p[0]; [string]$dst = $p[1]; [double]$scale = [double]$p[2]
  try {
    $img = [System.Drawing.Image]::FromFile($src)
    $w = [int]([Math]::Max(1, $img.Width * $scale)); $h = [int]([Math]::Max(1, $img.Height * $scale))
    if ($w -gt 3000) { $h = [int]($h * 3000 / $w); $w = 3000 }
    $bmp = New-Object System.Drawing.Bitmap $w, $h
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.Clear([System.Drawing.Color]::White)
    $g.SmoothingMode = 'HighQuality'
    $g.DrawImage($img, 0, 0, $w, $h)
    $bmp.Save($dst, [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $bmp.Dispose(); $img.Dispose()
    $results += "OK`t$dst"
  } catch { $results += "ERR`t$dst`t" + $_.Exception.Message }
}
[IO.File]::WriteAllLines($args[1], [string[]]$results, (New-Object Text.UTF8Encoding($true)))
