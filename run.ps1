param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Url,
    [Parameter(Mandatory = $true, Position = 1)][string]$Start,
    [Parameter(Mandatory = $true, Position = 2)][string]$End,
    [string]$Model = "large-v3",
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest
)

$script = Join-Path $PSScriptRoot "workflow\bili_transcript.py"
$argsList = @($script, $Url, $Start, $End, "--model", $Model) + $Rest
& python @argsList
exit $LASTEXITCODE
