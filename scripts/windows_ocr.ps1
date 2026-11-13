param([Parameter(Mandatory = $true)][string]$Path)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime] | Out-Null
[Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime] | Out-Null
$methods = [System.WindowsRuntimeSystemExtensions].GetMethods()
$asTask = $methods | Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 } | Select-Object -First 1
function Await-Result($Operation, $ResultType) {
    $task = $asTask.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    $task.Wait()
    return $task.Result
}
$resolved = (Resolve-Path -LiteralPath $Path).Path
$file = Await-Result ([Windows.Storage.StorageFile]::GetFileFromPathAsync($resolved)) ([Windows.Storage.StorageFile])
$stream = Await-Result ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await-Result ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await-Result ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$language = New-Object Windows.Globalization.Language('zh-Hans-CN')
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
if ($null -eq $engine) { throw '本机没有可用的简体中文 OCR 引擎' }
$result = Await-Result ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$items = @($result.Lines | ForEach-Object {
    $words = @($_.Words)
    if ($words.Count -gt 0) {
        $first = $words[0].BoundingRect
        [PSCustomObject]@{ Text = $_.Text; X = [double]$first.X; Y = [double]$first.Y; Height = [double]$first.Height }
    }
})
$groups = @()
foreach ($item in ($items | Sort-Object Y, X)) {
    $matched = $null
    foreach ($group in $groups) {
        if ([Math]::Abs($group.Y - $item.Y) -le [Math]::Max($group.Height, $item.Height) * 0.65) {
            $matched = $group
            break
        }
    }
    if ($null -eq $matched) {
        $groups += ,@{ Y = $item.Y; Height = $item.Height; Items = @($item) }
    } else {
        $matched.Items += $item
        $matched.Height = [Math]::Max($matched.Height, $item.Height)
    }
}
$lines = @($items | ForEach-Object { $_.Text })
$rows = @($groups | Sort-Object { $_.Y } | ForEach-Object { (($_.Items | Sort-Object X | ForEach-Object { $_.Text }) -join ' ') })
$stream.Dispose()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::Out.WriteLine((@{ engine = 'Windows.Media.Ocr'; language = 'zh-Hans-CN'; lines = $lines; rows = $rows; text = ($rows -join "`n") } | ConvertTo-Json -Compress))
