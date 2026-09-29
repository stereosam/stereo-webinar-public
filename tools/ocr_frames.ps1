# OCR over a folder of frames using the OCR engine built into Windows.
# Called by webinar.py scan. Writes JSON to -Out: one item per frame,
# each line of text with its bounding box in frame pixels.
#
# ASCII ONLY in this file: Windows PowerShell 5.1 reads .ps1 as ANSI, and
# Cyrillic in the source breaks the parser before it runs a single line.
# Run with Windows PowerShell 5.1 (powershell.exe): WinRT types do not load in pwsh 7.
param(
    [Parameter(Mandatory = $true)][string]$Dir,
    [Parameter(Mandatory = $true)][string]$Out,
    [string]$Lang = 'ru'
)

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]

function Await($op, $type) {
    $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $t.Wait(-1) | Out-Null
    $t.Result
}

$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Media, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage(
    (New-Object Windows.Globalization.Language $Lang))
if (-not $engine) {
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
}
if (-not $engine) { throw "no OCR engine available" }

$items = New-Object System.Collections.ArrayList
foreach ($fi in (Get-ChildItem -Path $Dir -Filter '*.png' | Sort-Object Name)) {
    try {
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($fi.FullName)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync(0)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bmp = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $res = Await ($engine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
        $stream.Dispose()
        $lines = New-Object System.Collections.ArrayList
        foreach ($ln in $res.Lines) {
            # box of a line = union of its word boxes
            $x1 = 1e9; $y1 = 1e9; $x2 = 0; $y2 = 0
            foreach ($w in $ln.Words) {
                $r = $w.BoundingRect
                if ($r.X -lt $x1) { $x1 = $r.X }
                if ($r.Y -lt $y1) { $y1 = $r.Y }
                if ($r.X + $r.Width -gt $x2) { $x2 = $r.X + $r.Width }
                if ($r.Y + $r.Height -gt $y2) { $y2 = $r.Y + $r.Height }
            }
            $null = $lines.Add([pscustomobject]@{
                text = $ln.Text
                box  = @([int]$x1, [int]$y1, [int]($x2 - $x1), [int]($y2 - $y1)) })
        }
        $null = $items.Add([pscustomobject]@{ file = $fi.Name; lines = @($lines) })
    } catch {
        $null = $items.Add([pscustomobject]@{ file = $fi.Name; lines = @(); error = $_.Exception.Message })
    }
}

$json = ConvertTo-Json -InputObject @($items) -Depth 5 -Compress
[IO.File]::WriteAllText($Out, $json, (New-Object Text.UTF8Encoding $false))
Write-Output ("ocr done: " + $items.Count)
