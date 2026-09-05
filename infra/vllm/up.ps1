#Requires -Version 5.1
<#
.SYNOPSIS
    Print the vLLM bring-up commands for a rented GPU host (ADR-0006).

.DESCRIPTION
    The MX330 cannot run vLLM. This script does not start containers on the
    local machine; it emits the exact remote + tunnel commands used for the
    serving benchmark session documented in evals/results/serving/.
#>
[CmdletBinding()]
param(
    [string]$GpuHost = "<gpu-host>",
    [string]$Model = "meta-llama/Meta-Llama-3.1-8B-Instruct",
    [int]$MaxNumSeqs = 32
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

Write-Host @"
# 1. On the rented GPU host (A10G / L4), from a checkout of this repo:
export VLLM_MODEL=$Model
export VLLM_MAX_NUM_SEQS=$MaxNumSeqs
cd $repo/infra/vllm
docker compose up -d
docker compose logs -f vllm   # wait until /health is ready

# 2. From the Windows dev machine:
ssh -L 8000:localhost:8000 $GpuHost
`$env:VYOMEL_VLLM_BASE_URL = 'http://localhost:8000/v1'
`$env:VYOMEL_PLANNER_BACKEND = 'vllm'

# 3. Benchmark (live):
.\.venv\Scripts\python.exe evals\suites\serving\run.py --backend live --base-url http://localhost:8000/v1 --concurrencies 1,4,8,16,32

# 4. Fixture harness (no GPU; CI):
.\.venv\Scripts\python.exe evals\suites\serving\run.py --backend fixture
"@ -ForegroundColor Cyan
