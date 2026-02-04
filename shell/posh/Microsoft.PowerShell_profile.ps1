# Script Execution Policy
# Set-ExecutionPolicy RemoteSigned -Scope CurrentUser

############################################################################################################################

# Initial setup
###################################################

# Disable beep
#set-service beep -startuptype disabled

# Create symlink from dotfiles to POSH
# New-Item -ItemType SymbolicLink -Path $HOME\Documents\PowerShell\Microsoft.PowerShell_profile.ps1 -Target D:\Workspace\repos\github\tools\dotfiles\shell\posh\Microsoft.PowerShell_profile.ps1

# Emacs mode
Set-PSReadLineOption -EditMode Emacs

# Move to directories
###################################################

# Move to downloads directory
function dw {
    Set-Location "$env:USERPROFILE\Downloads"
}

# Move to workspace directory
function ws {
    Set-Location "D:\Workspace\repos"
}

# Move to downloads directory
function dotfiles {
    Set-Location "D:\Workspace\repos\github\tools\dotfiles"
}

# System aliases
###################################################

# Re-open shell
function pshcfg {
  notepad $PROFILE
}

# Alias to Explorer
function open {
   param(
        [Parameter(Mandatory=$true)]
        [string]$dest
    )
    explorer $dest
}

# Alias to Explorer short version
function o {
   param(
        [Parameter(Mandatory=$true)]
        [string]$dest
    )
    explorer $dest
}

# Alias to Clear
function c {
    clear
}

# Cmd aliases
###################################################

# Download MP3
function yta-mp3 {
    param(
        [Parameter(Mandatory=$true)]
        [string]$url
    )
    yt-dlp --extract-audio --audio-format mp3 --audio-quality 0 $url
}

# Download MP4
function yt-mp4 {
    param(
        [Parameter(Mandatory=$true)]
        [string]$url
    )
    yt-dlp -f bestvideo[ext=mp4]+bestaudio[ext=m4a]/mp4 --merge-output-format mp4 $url
}

# App aliases
###################################################

function code {
    & "C:\Users\david\AppData\Local\Programs\Microsoft VS Code\bin\code.cmd" $args
}

function zed {
    & "C:\Users\david\AppData\Local\Programs\Zed\bin\Zed.exe" $args
}

Set-Alias vim "C:\Program Files\Vim\vim91\vim.exe"
Set-Alias gvim "C:\Program Files\Vim\vim91\gvim.exe"

# Modules
###################################################

# Win
Import-Module Microsoft.WinGet.CommandNotFound
Import-Module Terminal-Icons
Import-Module git-aliases -DisableNameChecking
Import-Module posh-git
Import-Module z

# POSH
& ([ScriptBlock]::Create((oh-my-posh init pwsh --config "$env:POSH_THEMES_PATH\spaceship.omp.json" --print) -join "`n"))

# Import the Chocolatey Profile that contains the necessary code to enable
# tab-completions to function for `choco`.
# Be aware that if you are missing these lines from your profile, tab completion
# for `choco` will not function.
# See https://ch0.co/tab-completion for details.

$ChocolateyProfile = "$env:ChocolateyInstall\helpers\chocolateyProfile.psm1"
if (Test-Path($ChocolateyProfile)) {
  Import-Module "$ChocolateyProfile"
}

# Custom scripts
###################################################

function Clean-PathVariable {
    <#
    .SYNOPSIS
        Removes duplicate entries and invalid paths from Windows PATH environment variable.
    
    .DESCRIPTION
        Cleans up both User and System PATH by removing:
        - Duplicate entries
        - Empty entries
        - Optionally specified patterns (like poppler)
        - Optionally non-existent paths
    
    .PARAMETER RemoveNonExistent
        Remove paths that don't exist on the filesystem
    
    .PARAMETER RemovePattern
        Remove paths matching this pattern (e.g., "*poppler*")
    
    .PARAMETER WhatIf
        Show what would be changed without actually changing it
    
    .EXAMPLE
        Clean-PathVariable -WhatIf
        Shows what would be cleaned without making changes
    
    .EXAMPLE
        Clean-PathVariable -RemovePattern "*poppler*"
        Removes duplicates and any paths containing "poppler"
    
    .EXAMPLE
        Clean-PathVariable -RemoveNonExistent
        Removes duplicates and paths that don't exist
    #>
    
    [CmdletBinding(SupportsShouldProcess)]
    param(
        [switch]$RemoveNonExistent,
        [string]$RemovePattern
    )
    
    function Clean-Path {
        param(
            [string]$PathString,
            [string]$Scope
        )
        
        Write-Host "`n=== Cleaning $Scope PATH ===" -ForegroundColor Cyan
        
        # Split and get initial count
        $paths = $PathString -split ';'
        $initialCount = $paths.Count
        $initialLength = $PathString.Length
        
        Write-Host "Initial: $initialCount entries, $initialLength characters" -ForegroundColor Yellow
        
        # Remove empty entries
        $paths = $paths | Where-Object { $_ }
        
        # Remove pattern if specified
        if ($RemovePattern) {
            $beforePatternCount = $paths.Count
            $paths = $paths | Where-Object { $_ -notlike $RemovePattern }
            $patternRemoved = $beforePatternCount - $paths.Count
            if ($patternRemoved -gt 0) {
                Write-Host "  Removed $patternRemoved entries matching '$RemovePattern'" -ForegroundColor Green
            }
        }
        
        # Remove non-existent paths if requested
        if ($RemoveNonExistent) {
            $beforeExistCount = $paths.Count
            $paths = $paths | Where-Object { Test-Path $_ }
            $nonExistentRemoved = $beforeExistCount - $paths.Count
            if ($nonExistentRemoved -gt 0) {
                Write-Host "  Removed $nonExistentRemoved non-existent paths" -ForegroundColor Green
            }
        }
        
        # Remove duplicates (case-insensitive)
        $uniquePaths = @()
        $seen = @{}
        foreach ($path in $paths) {
            $lowerPath = $path.ToLower()
            if (-not $seen.ContainsKey($lowerPath)) {
                $uniquePaths += $path
                $seen[$lowerPath] = $true
            }
        }
        
        $duplicatesRemoved = $paths.Count - $uniquePaths.Count
        if ($duplicatesRemoved -gt 0) {
            Write-Host "  Removed $duplicatesRemoved duplicate entries" -ForegroundColor Green
        }
        
        # Join back together
        $cleanedPath = $uniquePaths -join ';'
        $finalCount = $uniquePaths.Count
        $finalLength = $cleanedPath.Length
        
        Write-Host "Final: $finalCount entries, $finalLength characters" -ForegroundColor Yellow
        Write-Host "Saved: $(($initialCount - $finalCount)) entries, $(($initialLength - $finalLength)) characters" -ForegroundColor Green
        
        return @{
            Original = $PathString
            Cleaned = $cleanedPath
            Changed = $PathString -ne $cleanedPath
        }
    }
    
    # Clean User PATH
    try {
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        $userResult = Clean-Path -PathString $userPath -Scope "User"
        
        if ($userResult.Changed) {
            if ($PSCmdlet.ShouldProcess("User PATH", "Update environment variable")) {
                [Environment]::SetEnvironmentVariable('Path', $userResult.Cleaned, 'User')
                Write-Host "✓ User PATH updated successfully`n" -ForegroundColor Green
            }
        } else {
            Write-Host "✓ User PATH is already clean`n" -ForegroundColor Green
        }
    } catch {
        Write-Error "Failed to clean User PATH: $_"
    }
    
    # Clean System PATH (requires admin)
    try {
        $isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        
        if ($isAdmin) {
            $systemPath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
            $systemResult = Clean-Path -PathString $systemPath -Scope "System"
            
            if ($systemResult.Changed) {
                if ($PSCmdlet.ShouldProcess("System PATH", "Update environment variable")) {
                    [Environment]::SetEnvironmentVariable('Path', $systemResult.Cleaned, 'Machine')
                    Write-Host "✓ System PATH updated successfully`n" -ForegroundColor Green
                }
            } else {
                Write-Host "✓ System PATH is already clean`n" -ForegroundColor Green
            }
        } else {
            Write-Host "`n⚠ Skipping System PATH (requires Administrator privileges)" -ForegroundColor Yellow
            Write-Host "  Run PowerShell as Administrator to clean System PATH`n" -ForegroundColor Yellow
        }
    } catch {
        Write-Error "Failed to clean System PATH: $_"
    }
    
    Write-Host "=== Cleanup Complete ===" -ForegroundColor Cyan
    Write-Host "Note: Restart terminals/WSL for changes to take effect" -ForegroundColor Yellow
    Write-Host "      Run 'wsl --shutdown' in a new PowerShell window`n" -ForegroundColor Yellow
}

Set-Alias -Name cleanpath -Value Clean-PathVariable
