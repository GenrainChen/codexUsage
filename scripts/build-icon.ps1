# Render the simple vector mark at Windows icon sizes, using built-in GDI+.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$assetDir = Join-Path (Split-Path -Parent $PSScriptRoot) 'assets'

function New-RoundedPath([single]$x, [single]$y, [single]$w, [single]$h, [single]$r) {
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $d = $r * 2
    $path.AddArc($x, $y, $d, $d, 180, 90)
    $path.AddArc(($x + $w - $d), $y, $d, $d, 270, 90)
    $path.AddArc(($x + $w - $d), ($y + $h - $d), $d, $d, 0, 90)
    $path.AddArc($x, ($y + $h - $d), $d, $d, 90, 90)
    $path.CloseFigure()
    return $path
}

$sizes = @(16, 20, 24, 32, 40, 48, 64, 128, 256)
$images = [Collections.Generic.List[byte[]]]::new()
foreach ($size in $sizes) {
    $large = [Drawing.Bitmap]::new(($size * 4), ($size * 4))
    $g = [Drawing.Graphics]::FromImage($large)
    $g.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.ScaleTransform(($size * 4 / 128.0), ($size * 4 / 128.0))
    $orange = [Drawing.SolidBrush]::new([Drawing.ColorTranslator]::FromHtml('#CA602E'))
    $paper = [Drawing.SolidBrush]::new([Drawing.ColorTranslator]::FromHtml('#F7F4EC'))
    $fold = [Drawing.SolidBrush]::new([Drawing.ColorTranslator]::FromHtml('#E7B695'))
    $tile = New-RoundedPath 4 4 120 120 28
    $g.FillPath($orange, $tile)
    $g.FillPolygon($paper, [Drawing.PointF[]]@(
        [Drawing.PointF]::new(32,24), [Drawing.PointF]::new(76,24),
        [Drawing.PointF]::new(100,48), [Drawing.PointF]::new(100,100),
        [Drawing.PointF]::new(32,100)))
    $g.FillPolygon($fold, [Drawing.PointF[]]@(
        [Drawing.PointF]::new(76,24), [Drawing.PointF]::new(76,48),
        [Drawing.PointF]::new(100,48)))
    foreach ($bar in @(@(43,69,10,18), @(61,53,10,34), @(79,61,10,26))) {
        $shape = New-RoundedPath $bar[0] $bar[1] $bar[2] $bar[3] 2
        $g.FillPath($orange, $shape)
        $shape.Dispose()
    }
    $small = [Drawing.Bitmap]::new($size, $size)
    $down = [Drawing.Graphics]::FromImage($small)
    $down.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $down.PixelOffsetMode = [Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $down.DrawImage($large, 0, 0, $size, $size)
    $png = [IO.MemoryStream]::new()
    $small.Save($png, [Drawing.Imaging.ImageFormat]::Png)
    $images.Add($png.ToArray())
    if ($size -eq 32 -or $size -eq 128) {
        [IO.File]::WriteAllBytes((Join-Path $assetDir "codex-usage-$size.png"), $png.ToArray())
    }
    $png.Dispose(); $down.Dispose(); $small.Dispose()
    $tile.Dispose(); $orange.Dispose(); $paper.Dispose(); $fold.Dispose()
    $g.Dispose(); $large.Dispose()
}
$ico = [IO.MemoryStream]::new()
$writer = [IO.BinaryWriter]::new($ico)
$writer.Write([uint16]0)
$writer.Write([uint16]1)
$writer.Write([uint16]$sizes.Count)
$offset = 6 + 16 * $sizes.Count
for ($i = 0; $i -lt $sizes.Count; $i++) {
    $dimension = if ($sizes[$i] -eq 256) { 0 } else { $sizes[$i] }
    $writer.Write([byte]$dimension); $writer.Write([byte]$dimension)
    $writer.Write([byte]0); $writer.Write([byte]0)
    $writer.Write([uint16]1); $writer.Write([uint16]32)
    $writer.Write([uint32]$images[$i].Length); $writer.Write([uint32]$offset)
    $offset += $images[$i].Length
}
foreach ($pngBytes in $images) { $writer.Write($pngBytes) }
[IO.File]::WriteAllBytes((Join-Path $assetDir 'codex-usage.ico'), $ico.ToArray())
$writer.Dispose(); $ico.Dispose()
Write-Output "Built icon sizes: $($sizes -join ', ')"
