$ErrorActionPreference = 'Stop'
$jobs = Get-Content -LiteralPath $args[0] -Encoding UTF8
$resultFile = $args[1]; $progid = $args[2]; $kind = $args[3]
$results = @()
$app = $null
try {
  $app = New-Object -ComObject $progid
  try { $app.Visible = $false } catch {}
  try { $app.DisplayAlerts = 0 } catch {}
  $previousSecurity = $app.AutomationSecurity
  if ($null -eq $previousSecurity) { throw 'AutomationSecurity unavailable' }
  $app.AutomationSecurity = 3
  if ($app.AutomationSecurity -ne 3) { throw 'AutomationSecurity could not be enforced' }
  foreach ($line in $jobs) {
    if (-not $line -or -not $line.Trim()) { continue }
    $p = $line.Split("`t"); [string]$src = $p[0]; [string]$dst = $p[1]; [int]$fmt = [int]$p[2]
    $doc = $null; $wb = $null
    try {
      if ($kind -eq 'word') {
        if ($progid -eq 'Word.Application') {
          $doc = $app.Documents.OpenNoRepairDialog($src, $false, $true, $false)
        } else { $doc = $app.Documents.Open($src, $false, $true, $false) }
        $done = $false
        if ($fmt -eq 17) { try { $doc.ExportAsFixedFormat($dst, 17); $done = $true } catch {} }
        if (-not $done) { try { $doc.SaveAs([ref]$dst, [ref]$fmt); $done = $true } catch {} }
        if (-not $done) { try { $doc.SaveAs($dst, $fmt); $done = $true } catch {} }
        if (-not $done) { $doc.SaveAs2($dst, $fmt) }
      } else {
        $wb = $app.Workbooks.Open($src, 0, $true)
        if ($fmt -eq 57) { $wb.ExportAsFixedFormat(0, $dst) } else { $wb.SaveAs($dst, $fmt) }
      }
      $results += "OK`t$dst"
    } catch { $results += "ERR`t$dst`t" + $_.Exception.Message }
    finally {
      if ($doc) { try { $doc.Close([ref]0) } catch {} }
      if ($wb) { try { $wb.Close($false) } catch {} }
    }
  }
} catch { $results += "FATAL`t`t" + $_.Exception.Message }
finally {
  if ($app -and $null -ne $previousSecurity) { try { $app.AutomationSecurity = $previousSecurity } catch {} }
  if ($app) { try { $app.Quit() } catch {}; try { [void][Runtime.Interopservices.Marshal]::ReleaseComObject($app) } catch {} }
}
[IO.File]::WriteAllLines($resultFile, [string[]]$results, (New-Object Text.UTF8Encoding($true)))
