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

############################################################################################################################
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

