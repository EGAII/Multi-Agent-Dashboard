# Long-running helper: reads one JSON request per line on stdin, answers one JSON line on stdout.
#   {"op":"list"}                                   -> all Windows Terminal tabs
#   {"op":"select","titles":["..."],"nth":0}        -> select the first tab whose name matches a title
# Uses UI Automation, which ships with Windows PowerShell 5.1.
$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$AE = [System.Windows.Automation.AutomationElement]
$scopeChildren = [System.Windows.Automation.TreeScope]::Children
$scopeDesc = [System.Windows.Automation.TreeScope]::Descendants
$winCond = New-Object System.Windows.Automation.PropertyCondition($AE::ClassNameProperty, "CASCADIA_HOSTING_WINDOW_CLASS")
$tabCond = New-Object System.Windows.Automation.PropertyCondition($AE::ControlTypeProperty, [System.Windows.Automation.ControlType]::TabItem)

function Get-Tabs {
    $rows = @()
    foreach ($w in $AE::RootElement.FindAll($scopeChildren, $winCond)) {
        $i = 0
        foreach ($t in $w.FindAll($scopeDesc, $tabCond)) {
            $sel = $false
            try { $sel = $t.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Current.IsSelected } catch {}
            $rows += [pscustomobject]@{ hwnd = [int64]$w.Current.NativeWindowHandle; pid = $w.Current.ProcessId; index = $i; name = $t.Current.Name; selected = $sel; el = $t }
            $i++
        }
    }
    return $rows
}

function Norm([string]$s) { return ($s -replace '[^\p{L}\p{N}]', '').ToLowerInvariant() }

while ($null -ne ($line = [Console]::In.ReadLine())) {
    try {
        $req = $line | ConvertFrom-Json
        $tabs = Get-Tabs
        if ($req.op -eq "list") {
            $out = @{ ok = $true; tabs = @($tabs | ForEach-Object { @{ hwnd = $_.hwnd; pid = $_.pid; index = $_.index; name = $_.name; selected = $_.selected } }) }
        } elseif ($req.op -eq "select") {
            $hit = @()
            foreach ($title in $req.titles) {
                if ([string]::IsNullOrWhiteSpace($title)) { continue }
                $n = Norm $title
                $hit = @($tabs | Where-Object { (Norm $_.name) -eq $n })
                if ($hit.Count -eq 0 -and -not $req.exact) { $hit = @($tabs | Where-Object { $nn = Norm $_.name; $nn.Length -gt 2 -and ($nn.Contains($n) -or $n.Contains($nn)) }) }
                if ($hit.Count -gt 0) { break }
            }
            if ($hit.Count -gt 0) {
                $t = $hit[[int]$req.nth % $hit.Count]
                $t.el.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select()
                $out = @{ ok = $true; hwnd = $t.hwnd; name = $t.name; matches = $hit.Count }
            } else {
                $out = @{ ok = $false; error = "no tab matched"; tabs = @($tabs | ForEach-Object { $_.name }) }
            }
        } else {
            $out = @{ ok = $false; error = "unknown op" }
        }
    } catch {
        $out = @{ ok = $false; error = $_.Exception.Message }
    }
    [Console]::Out.WriteLine(($out | ConvertTo-Json -Compress -Depth 4))
    [Console]::Out.Flush()
}
