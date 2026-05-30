Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase
Add-Type -AssemblyName System.Windows.Forms

if ([Threading.Thread]::CurrentThread.ApartmentState -ne "STA") {
    $arg = '-NoProfile -ExecutionPolicy Bypass -STA -File "{0}"' -f $PSCommandPath
    Start-Process -FilePath "powershell.exe" -ArgumentList $arg -WindowStyle Normal
    exit
}

$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $PSCommandPath
$ConfigPath = Join-Path $ScriptDir "config.json"
$YtDlpPath = Join-Path $ScriptDir "yt-dlp.exe"
$FfmpegPath = Join-Path $ScriptDir "ffmpeg.exe"

$config = [PSCustomObject]@{ lastSavePath = ""; lastFormat = 0; lastProxy = "" }
if (Test-Path $ConfigPath) {
    try { $config = Get-Content $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch {}
}
if (-not $config.lastSavePath) {
    $config.lastSavePath = [Environment]::GetFolderPath('MyVideos')
    if (-not (Test-Path $config.lastSavePath)) { $config.lastSavePath = [Environment]::GetFolderPath('Desktop') }
}

function Save-Config {
    try { $config | ConvertTo-Json | Set-Content $ConfigPath -Encoding UTF8 } catch {}
}

[xml]$xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="YouTube Video Downloader"
        Height="720" Width="920"
        MinHeight="560" MinWidth="760"
        WindowStartupLocation="CenterScreen"
        Background="#F5F5F7"
        FontFamily="Segoe UI, Microsoft YaHei UI"
        FontSize="13"
        UseLayoutRounding="True"
        TextOptions.TextFormattingMode="Ideal"
        TextOptions.TextRenderingMode="Auto">
    <Window.Resources>
        <Style TargetType="TextBox">
            <Setter Property="BorderBrush" Value="#E5E5EA"/>
            <Setter Property="Background" Value="#F9F9F9"/>
            <Setter Property="BorderThickness" Value="1"/>
        </Style>
        <Style x:Key="CardBorder" TargetType="Border">
            <Setter Property="Background" Value="White"/>
            <Setter Property="CornerRadius" Value="10"/>
            <Setter Property="BorderBrush" Value="#E8E8ED"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="Padding" Value="18"/>
        </Style>
        <Style x:Key="LabelText" TargetType="TextBlock">
            <Setter Property="FontSize" Value="13"/>
            <Setter Property="FontWeight" Value="Medium"/>
            <Setter Property="Foreground" Value="#86868B"/>
            <Setter Property="VerticalAlignment" Value="Center"/>
        </Style>
        <Style x:Key="PrimaryButton" TargetType="Button">
            <Setter Property="Height" Value="38"/>
            <Setter Property="Background" Value="#007AFF"/>
            <Setter Property="Foreground" Value="White"/>
            <Setter Property="FontWeight" Value="SemiBold"/>
            <Setter Property="FontSize" Value="14"/>
            <Setter Property="BorderThickness" Value="0"/>
            <Setter Property="Cursor" Value="Hand"/>
        </Style>
        <Style x:Key="SecondaryButton" TargetType="Button">
            <Setter Property="Height" Value="38"/>
            <Setter Property="Background" Value="#F5F5F7"/>
            <Setter Property="Foreground" Value="#1D1D1F"/>
            <Setter Property="FontWeight" Value="Medium"/>
            <Setter Property="FontSize" Value="13"/>
            <Setter Property="BorderBrush" Value="#E5E5EA"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="Cursor" Value="Hand"/>
        </Style>
    </Window.Resources>
    <Grid Margin="40,36,40,40">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
        </Grid.RowDefinitions>

        <!-- Title -->
        <TextBlock Grid.Row="0" Text="YouTube Video Downloader"
                   FontSize="28" FontWeight="SemiBold"
                   Foreground="#1D1D1F"
                   HorizontalAlignment="Center"
                   Margin="0,0,0,6"/>
        <TextBlock Grid.Row="1" Text="Paste a link, pick a quality, download."
                   FontSize="14"
                   Foreground="#86868B"
                   HorizontalAlignment="Center"
                   Margin="0,0,0,30"/>

        <!-- URL -->
        <Border Grid.Row="2" Style="{StaticResource CardBorder}" Margin="0,0,0,10">
            <Grid>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="72"/>
                    <ColumnDefinition Width="*"/>
                </Grid.ColumnDefinitions>
                <TextBlock Text="URL" Style="{StaticResource LabelText}"/>
                <TextBox x:Name="UrlBox" Grid.Column="1" Margin="14,0,0,0" Height="38"
                         Padding="12,8" VerticalContentAlignment="Center"/>
            </Grid>
        </Border>

        <!-- Save To -->
        <Border Grid.Row="3" Style="{StaticResource CardBorder}" Margin="0,0,0,10">
            <Grid>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="72"/>
                    <ColumnDefinition Width="*"/>
                    <ColumnDefinition Width="Auto"/>
                </Grid.ColumnDefinitions>
                <TextBlock Text="Save to" Style="{StaticResource LabelText}"/>
                <TextBox x:Name="PathBox" Grid.Column="1" Margin="14,0,14,0" Height="38"
                         Padding="12,8" VerticalContentAlignment="Center"/>
                <Button x:Name="BrowseButton" Grid.Column="2"
                        Height="38" MinWidth="88" Padding="16,0"
                        Content="Browse..."
                        Background="#007AFF" Foreground="White"
                        FontWeight="Medium" FontSize="13"
                        BorderThickness="0" Cursor="Hand">
                    <Button.Resources>
                        <Style TargetType="Border">
                            <Setter Property="CornerRadius" Value="8"/>
                        </Style>
                    </Button.Resources>
                </Button>
            </Grid>
        </Border>

        <!-- Quality / Proxy / Status -->
        <Grid Grid.Row="4" Margin="0,0,0,10">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="1.5*"/>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="2*"/>
            </Grid.ColumnDefinitions>

            <Border Grid.Column="0" Style="{StaticResource CardBorder}" Margin="0,0,6,0">
                <Grid>
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="Auto"/>
                        <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>
                    <TextBlock Text="Quality" Style="{StaticResource LabelText}"/>
                    <ComboBox x:Name="FormatBox" Grid.Column="1" Margin="14,0,0,0" Height="38"
                              Background="#F9F9F9" BorderBrush="#E5E5EA">
                        <ComboBoxItem Content="Best (MP4)"/>
                        <ComboBoxItem Content="1080p (MP4)"/>
                        <ComboBoxItem Content="720p (MP4)"/>
                        <ComboBoxItem Content="Audio Only (MP3)"/>
                    </ComboBox>
                </Grid>
            </Border>

            <Border Grid.Column="1" Style="{StaticResource CardBorder}" Margin="6,0,6,0">
                <Grid>
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="Auto"/>
                        <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>
                    <TextBlock Text="Proxy" Style="{StaticResource LabelText}"/>
                    <TextBox x:Name="ProxyBox" Grid.Column="1" Margin="14,0,0,0" Height="38"
                             Padding="12,8" VerticalContentAlignment="Center"
                             ToolTip="e.g. http://127.0.0.1:7890 or socks5://127.0.0.1:1080"/>
                </Grid>
            </Border>

            <Border Grid.Column="2" Style="{StaticResource CardBorder}" Margin="6,0,0,0">
                <StackPanel>
                    <Grid>
                        <Grid.ColumnDefinitions>
                            <ColumnDefinition Width="*"/>
                            <ColumnDefinition Width="Auto"/>
                        </Grid.ColumnDefinitions>
                        <TextBlock x:Name="StatusText" Text="Ready"
                                   Foreground="#86868B" VerticalAlignment="Center"
                                   FontSize="13" FontWeight="Medium"/>
                        <TextBlock x:Name="SpeedText" Grid.Column="1" Text=""
                                   Foreground="#AEAEB2" FontSize="12"/>
                    </Grid>
                    <ProgressBar x:Name="ProgressBar" Margin="0,10,0,0" Height="4"
                                 Minimum="0" Maximum="100" Value="0"
                                 Foreground="#007AFF" Background="#E8E8ED"
                                 BorderThickness="0"/>
                </StackPanel>
            </Border>
        </Grid>

        <!-- Buttons -->
        <Border Grid.Row="5" Style="{StaticResource CardBorder}" Margin="0,0,0,10">
            <WrapPanel>
                <Button x:Name="StartButton" MinWidth="150"
                        Content="Start Download" Style="{StaticResource PrimaryButton}">
                    <Button.Resources>
                        <Style TargetType="Border">
                            <Setter Property="CornerRadius" Value="8"/>
                        </Style>
                    </Button.Resources>
                </Button>
                <Button x:Name="StopButton" MinWidth="100"
                        Content="Stop" IsEnabled="False"
                        Style="{StaticResource SecondaryButton}" Margin="10,0,0,0">
                    <Button.Resources>
                        <Style TargetType="Border">
                            <Setter Property="CornerRadius" Value="8"/>
                        </Style>
                    </Button.Resources>
                </Button>
                <Button x:Name="UpdateButton" MinWidth="120"
                        Content="Update yt-dlp"
                        Style="{StaticResource SecondaryButton}" Margin="10,0,0,0">
                    <Button.Resources>
                        <Style TargetType="Border">
                            <Setter Property="CornerRadius" Value="8"/>
                        </Style>
                    </Button.Resources>
                </Button>
            </WrapPanel>
        </Border>

        <!-- Log -->
        <Border Grid.Row="6" Style="{StaticResource CardBorder}" Padding="10">
            <TextBox x:Name="LogBox" VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Auto"
                     IsReadOnly="True" TextWrapping="NoWrap"
                     Background="#1C1C1E" Foreground="#F5F5F7"
                     BorderThickness="0"
                     FontFamily="SF Mono, Consolas, monospace" FontSize="12"
                     Padding="8"/>
        </Border>
    </Grid>
</Window>
"@

$reader = New-Object System.Xml.XmlNodeReader $xaml
$window = [Windows.Markup.XamlReader]::Load($reader)

$urlBox     = $window.FindName("UrlBox")
$pathBox    = $window.FindName("PathBox")
$proxyBox   = $window.FindName("ProxyBox")
$browseBtn  = $window.FindName("BrowseButton")
$formatBox  = $window.FindName("FormatBox")
$statusText = $window.FindName("StatusText")
$speedText  = $window.FindName("SpeedText")
$progressBar = $window.FindName("ProgressBar")
$startBtn   = $window.FindName("StartButton")
$stopBtn    = $window.FindName("StopButton")
$updateBtn  = $window.FindName("UpdateButton")
$logBox     = $window.FindName("LogBox")

$formatBox.SelectedIndex = $config.lastFormat
$pathBox.Text = $config.lastSavePath
$proxyBox.Text = $config.lastProxy

$script:dlProcess   = $null
$script:pollTimer   = $null
$script:outFile     = ""
$script:errFile     = ""
$script:lastOutPos  = 0
$script:lastErrPos  = 0
$script:hasFfmpeg   = Test-Path -LiteralPath $FfmpegPath

function Add-Log {
    param([string]$Message)
    $ts = (Get-Date).ToString("HH:mm:ss")
    $logBox.AppendText("[$ts] $Message`r`n")
    $logBox.ScrollToEnd()
}

function Set-UIState {
    param([bool]$Running)
    $startBtn.IsEnabled = -not $Running
    $stopBtn.IsEnabled = $Running
    $updateBtn.IsEnabled = -not $Running
    $formatBox.IsEnabled = -not $Running
}

function Build-Arguments {
    param([string]$Url, [string]$SavePath, [int]$FormatIndex, [string]$Proxy)
    $outputTemplate = Join-Path $SavePath "%(title).200B [%(id)s].%(ext)s"
    $a = @("--newline", "--no-playlist", "--output", $outputTemplate)
    if ($script:hasFfmpeg) { $a += @("--ffmpeg-location", $FfmpegPath) }
    if ($Proxy) { $a += @("--proxy", $Proxy) }
    if ($FormatIndex -eq 0) {
        if ($script:hasFfmpeg) {
            $a += @("-f", "bestvideo+bestaudio/best", "--merge-output-format", "mp4")
        } else {
            $a += @("-f", "best[ext=mp4]/best", "--merge-output-format", "mp4")
        }
    } elseif ($FormatIndex -eq 1) {
        if ($script:hasFfmpeg) {
            $a += @("-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]", "--merge-output-format", "mp4")
        } else {
            $a += @("-f", "best[height<=1080][ext=mp4]/best[height<=1080]", "--merge-output-format", "mp4")
        }
    } elseif ($FormatIndex -eq 2) {
        if ($script:hasFfmpeg) {
            $a += @("-f", "bestvideo[height<=720]+bestaudio/best[height<=720]", "--merge-output-format", "mp4")
        } else {
            $a += @("-f", "best[height<=720][ext=mp4]/best[height<=720]", "--merge-output-format", "mp4")
        }
    } else {
        if ($script:hasFfmpeg) {
            $a += @("-x", "--audio-format", "mp3", "--audio-quality", "0")
        } else {
            $a += @("-f", "bestaudio[ext=m4a]/bestaudio")
        }
    }
    $a += $Url
    return $a
}

function Build-CommandLine {
    param([string[]]$ArgList)
    ($ArgList | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join " "
}

function Read-NewLines {
    param([string]$FilePath, [ref]$LastPos, [string]$Prefix)
    if (-not $FilePath -or -not (Test-Path $FilePath)) { return }
    $fs = [System.IO.File]::Open($FilePath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try {
        if ($fs.Length -gt $LastPos.Value) {
            [void]$fs.Seek($LastPos.Value, [System.IO.SeekOrigin]::Begin)
            $reader = New-Object System.IO.StreamReader($fs, [System.Text.Encoding]::UTF8)
            while (($line = $reader.ReadLine()) -ne $null) {
                $displayLine = if ($Prefix) { "$Prefix $line" } else { $line }
                Add-Log $displayLine
                if ($line -match '\[download\]\s+(\d{1,3}(?:\.\d)?)%') {
                    $pct = [math]::Floor([double]$matches[1])
                    if ($pct -ge 0 -and $pct -le 100) {
                        $progressBar.Value = $pct
                        $statusText.Text = "Downloading... $pct%"
                    }
                }
                if ($line -match 'at\s+([\d.]+\s*\w+/s)') {
                    $speedText.Text = $matches[1]
                }
            }
            $LastPos.Value = $fs.Position
        }
    } finally {
        $fs.Close()
    }
}

function Poll-Output {
    try {
        Read-NewLines -FilePath $script:outFile -LastPos ([ref]$script:lastOutPos) -Prefix ""
        Read-NewLines -FilePath $script:errFile -LastPos ([ref]$script:lastErrPos) -Prefix "[ERR]"

        if ($script:dlProcess -and $script:dlProcess.HasExited) {
            if ($script:pollTimer) {
                $script:pollTimer.Stop()
                $script:pollTimer = $null
            }
            Start-Sleep -Milliseconds 200
            # final read
            Read-NewLines -FilePath $script:outFile -LastPos ([ref]$script:lastOutPos) -Prefix ""
            Read-NewLines -FilePath $script:errFile -LastPos ([ref]$script:lastErrPos) -Prefix "[ERR]"

            $exitCode = $script:dlProcess.ExitCode
            try { $script:dlProcess.Dispose() } catch {}
            $script:dlProcess = $null
            Set-UIState $false
            Remove-Item $script:outFile -ErrorAction SilentlyContinue; $script:outFile = ""
            Remove-Item $script:errFile -ErrorAction SilentlyContinue; $script:errFile = ""
            if ($exitCode -eq 0) {
                $progressBar.Value = 100
                $statusText.Text = "Completed"
                $speedText.Text = ""
                Add-Log "Download completed."
            } else {
                $statusText.Text = "Failed (exit: $exitCode)"
                $speedText.Text = ""
                Add-Log "Download failed, exit code: $exitCode"
            }
        }
    } catch {
        # silently ignore polling errors
    }
}

function Stop-Download {
    try {
        if ($script:pollTimer) { $script:pollTimer.Stop(); $script:pollTimer = $null }
        if ($script:dlProcess -and -not $script:dlProcess.HasExited) {
            try {
                $pidToKill = $script:dlProcess.Id
                & taskkill /T /F /PID $pidToKill 2>$null
            } catch {}
            Add-Log "Stopped by user."
        }
        if ($script:dlProcess) { try { $script:dlProcess.Dispose() } catch {}; $script:dlProcess = $null }
    } catch {}
    if ($script:outFile) { Remove-Item $script:outFile -ErrorAction SilentlyContinue; $script:outFile = "" }
    if ($script:errFile) { Remove-Item $script:errFile -ErrorAction SilentlyContinue; $script:errFile = "" }
    Set-UIState $false
    $statusText.Text = "Stopped"
    $speedText.Text = ""
}

function Update-YtDlp {
    if (-not (Test-Path -LiteralPath $YtDlpPath)) {
        Add-Log "yt-dlp.exe not found."
        return
    }
    Add-Log "Updating yt-dlp..."
    $statusText.Text = "Updating..."
    Set-UIState $true
    try {
        $uOut = Join-Path $ScriptDir ".yt-update-out.tmp"
        $uErr = Join-Path $ScriptDir ".yt-update-err.tmp"
        $proc = Start-Process -FilePath $YtDlpPath -ArgumentList "-U" -WindowStyle Hidden -PassThru -RedirectStandardOutput $uOut -RedirectStandardError $uErr
        $proc.WaitForExit()
        if (Test-Path $uOut) { Get-Content $uOut -Encoding UTF8 | ForEach-Object { Add-Log $_ }; Remove-Item $uOut -ErrorAction SilentlyContinue }
        if (Test-Path $uErr) { Remove-Item $uErr -ErrorAction SilentlyContinue }
        Add-Log "Update done."
    } catch {
        Add-Log "Update failed: $($_.Exception.Message)"
    }
    Set-UIState $false
    $statusText.Text = "Ready"
}

$browseBtn.Add_Click({
    try {
        $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
        $dialog.Description = "Select save folder"
        if (Test-Path -LiteralPath $pathBox.Text) { $dialog.SelectedPath = $pathBox.Text }
        if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
            $pathBox.Text = $dialog.SelectedPath
        }
    } catch {}
})

$startBtn.Add_Click({
    $url = $urlBox.Text.Trim()
    $savePath = $pathBox.Text.Trim()

    if ([string]::IsNullOrWhiteSpace($url)) {
        [System.Windows.MessageBox]::Show("Please enter a video URL.", "Missing URL")
        return
    }
    if (-not (Test-Path -LiteralPath $YtDlpPath)) {
        [System.Windows.MessageBox]::Show("yt-dlp.exe not found.", "Missing File")
        return
    }
    if (-not (Test-Path -LiteralPath $savePath)) {
        try { New-Item -Path $savePath -ItemType Directory -Force | Out-Null } catch {
            [System.Windows.MessageBox]::Show("Cannot create folder: $($_.Exception.Message)", "Error")
            return
        }
    }

    # kill any previous process
    if ($script:dlProcess) {
        try { if (-not $script:dlProcess.HasExited) { $script:dlProcess.Kill() } } catch {}
        try { $script:dlProcess.Dispose() } catch {}
        $script:dlProcess = $null
    }
    if ($script:pollTimer) { $script:pollTimer.Stop(); $script:pollTimer = $null }
    if ($script:outFile) { Remove-Item $script:outFile -ErrorAction SilentlyContinue; $script:outFile = "" }
    if ($script:errFile) { Remove-Item $script:errFile -ErrorAction SilentlyContinue; $script:errFile = "" }

    try {
        $config.lastSavePath = $savePath
        $config.lastFormat = $formatBox.SelectedIndex
        $config.lastProxy = $proxyBox.Text.Trim()
        Save-Config

        Set-UIState $true
        $progressBar.Value = 0
        $statusText.Text = "Resolving..."
        $speedText.Text = ""
        Add-Log "----------------------------------------"
        Add-Log "URL: $url"
        Add-Log "Save to: $savePath"
        if ($config.lastProxy) { Add-Log "Proxy: $($config.lastProxy)" }

        $argList = Build-Arguments -Url $url -SavePath $savePath -FormatIndex $formatBox.SelectedIndex -Proxy $config.lastProxy
        $cmdLine = Build-CommandLine $argList
        Add-Log "Cmd: yt-dlp.exe $cmdLine"

        # use Start-Process with separate stdout/stderr files (no event handlers needed)
        $script:outFile = Join-Path $ScriptDir ".yt-out.tmp"
        $script:errFile = Join-Path $ScriptDir ".yt-err.tmp"
        Remove-Item $script:outFile -ErrorAction SilentlyContinue
        Remove-Item $script:errFile -ErrorAction SilentlyContinue
        $script:lastOutPos = 0
        $script:lastErrPos = 0

        $script:dlProcess = Start-Process -FilePath $YtDlpPath -ArgumentList $cmdLine -WindowStyle Hidden -PassThru -RedirectStandardOutput $script:outFile -RedirectStandardError $script:errFile

        Add-Log "Started (PID: $($script:dlProcess.Id))"

        $script:pollTimer = New-Object System.Windows.Threading.DispatcherTimer
        $script:pollTimer.Interval = [TimeSpan]::FromMilliseconds(300)
        $script:pollTimer.Add_Tick({ Poll-Output })
        $script:pollTimer.Start()

        $statusText.Text = "Downloading..."
    } catch {
        Set-UIState $false
        Add-Log "ERROR: $($_.Exception.Message)"
        [System.Windows.MessageBox]::Show("Failed to start: $($_.Exception.Message)", "Error")
    }
})

$stopBtn.Add_Click({ Stop-Download })
$updateBtn.Add_Click({ Update-YtDlp })

$window.Add_Closing({
    try {
        if ($script:pollTimer) { $script:pollTimer.Stop() }
        if ($script:dlProcess -and -not $script:dlProcess.HasExited) {
            $pidToKill = $script:dlProcess.Id
            & taskkill /T /F /PID $pidToKill 2>$null
        }
        if ($script:dlProcess) { $script:dlProcess.Dispose() }
    } catch {}
    Save-Config
    if ($script:outFile) { Remove-Item $script:outFile -ErrorAction SilentlyContinue }
    if ($script:errFile) { Remove-Item $script:errFile -ErrorAction SilentlyContinue }
})

Set-UIState $false

if (-not (Test-Path -LiteralPath $YtDlpPath)) {
    Add-Log "ERROR: yt-dlp.exe not found."
    $startBtn.IsEnabled = $false
    $updateBtn.IsEnabled = $false
}

Add-Log "yt-dlp Downloader ready."
if ($script:hasFfmpeg) {
    Add-Log "ffmpeg: detected"
} else {
    Add-Log "ffmpeg: NOT found (merge/convert limited)"
}
Add-Log "Paste a URL and click Start Download."

[void]$window.ShowDialog()
