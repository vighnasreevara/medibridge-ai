# Render the synthetic intake scripts (tests/fixtures/intake_scripts.json) to 16 kHz mono WAV files with the
# offline Windows speech synthesizer. Clinician turns use one voice, patient turns another.
# Output: var/fixtures/audio/<key>.wav  (generated, not committed)
# Note: synthesized speech is cleaner than real speech, so ASR accuracy measured on it is optimistic.
[CmdletBinding()]
param([string]$OutDir)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
if (-not $OutDir) { $OutDir = Join-Path $Root 'var\fixtures\audio' }
New-Item -ItemType Directory -Force $OutDir | Out-Null

Add-Type -AssemblyName System.Speech
$voices = @{ clinician = 'Microsoft Zira Desktop'; patient = 'Microsoft David Desktop' }
$fixtures = Get-Content (Join-Path $Root 'tests\fixtures\intake_scripts.json') -Raw | ConvertFrom-Json
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)

foreach ($script in $fixtures.scripts) {
    $path = Join-Path $OutDir "$($script.key).wav"
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $synth.Rate = -1
    $synth.SetOutputToWaveFile($path, $format)
    $prompt = New-Object System.Speech.Synthesis.PromptBuilder
    foreach ($turn in $script.turns) {
        $prompt.StartVoice($voices[$turn[0]])
        $prompt.AppendText($turn[1])
        $prompt.EndVoice()
        $prompt.AppendBreak([TimeSpan]::FromMilliseconds(600))
    }
    $synth.Speak($prompt)
    $synth.Dispose()
    Write-Host ("{0,-24} {1,8:N0} bytes" -f $script.key, (Get-Item $path).Length)
}
