param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Url,
    [string]$Start,
    [string]$End,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest
)

$script = Join-Path $PSScriptRoot "workflow\bili_to_doc.py"
$argsList = @($script, $Url)
if ($Start) { $argsList += @("--start", $Start) }
if ($End) { $argsList += @("--end", $End) }
$argsList += $Rest

& python @argsList
exit $LASTEXITCODE
