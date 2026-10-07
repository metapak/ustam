param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Target,
    [ValidateSet("astra", "sol")]
    [string]$Profile,
    [ValidateSet("balanced", "quality", "economy", "quota-saver", "custom")]
    [string]$Preset,
    [string[]]$RoleModel = @(),
    [string[]]$RoleEffort = @(),
    [ValidateSet("none", "anthropic", "deepseek")]
    [string]$ExternalProvider,
    [string]$ExternalModel,
    [ValidateSet("none", "minimal", "low", "medium", "high", "xhigh", "max")]
    [string]$ExternalEffort,
    [switch]$Interactive,
    [switch]$Force,
    [switch]$ForceConfig,
    [switch]$DryRun,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Arguments = @("$ScriptDir\install.py", $Target)
if ($Profile) { $Arguments += @("--profile", $Profile) }
if ($Preset) { $Arguments += @("--preset", $Preset) }
foreach ($Value in $RoleModel) { $Arguments += @("--role-model", $Value) }
foreach ($Value in $RoleEffort) { $Arguments += @("--role-effort", $Value) }
if ($ExternalProvider) { $Arguments += @("--external-provider", $ExternalProvider) }
if (-not [string]::IsNullOrWhiteSpace($ExternalModel)) { $Arguments += @("--external-model", $ExternalModel) }
if (-not [string]::IsNullOrWhiteSpace($ExternalEffort)) { $Arguments += @("--external-effort", $ExternalEffort) }
if ($Interactive) { $Arguments += "--interactive" }
if ($Force) { $Arguments += "--force" }
if ($ForceConfig) { $Arguments += "--force-config" }
if ($DryRun) { $Arguments += "--dry-run" }
if ($Uninstall) { $Arguments += "--uninstall" }

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 @Arguments
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python @Arguments
} elseif (Get-Command python3 -ErrorAction SilentlyContinue) {
    & python3 @Arguments
} else {
    Write-Error "Python 3.11 or newer was not found on PATH."
    exit 2
}
exit $LASTEXITCODE
