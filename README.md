# `cronphase` — Fleet-Wide Cron Thundering-Herd Oscilloscope & Phase-Shift Compiler

> **Why does your database CPU spike to 100% at `00:00`, `:00`, and `:15` every day?**
> Because humans write `0 * * * *`, `*/5 * * * *`, `*/15 * * * *`, and `0 0 * * *`. Across 20+ microservices, Kubernetes `CronJob` manifests, and GitHub Actions workflows, those schedules constructively interfere like acoustic waves—creating **The Midnight Stampede**.

Existing tools (like `crontab.guru`) only translate a **single** cron line into English.
**`cronphase`** analyzes your **entire fleet** as a 1,440-minute diurnal signal, pinpoints multi-job resource collisions (accounting for multi-minute execution durations and midnight wrap-around), and runs a **constrained minimax phase-shift solver** that rewrites cron expressions with deterministic offsets (`*/15 * * * *` → `7-59/15 * * * *`, `0 * * * *` → `23 * * * *`, `0 */4 * * *` → `19 1-23/4 * * *`) while strictly preserving every job's exact frequency, SLA window bounds, and pinned locks.

## Key Capabilities

1. **Multi-Format Fleet Ingestion**: Parses standard UNIX `/etc/crontab`, Kubernetes `batch/v1 CronJob` YAML manifests, and GitHub Actions `.github/workflows/*.yml` schedule blocks.
2. **1,440-Minute Load Envelope & Crest Factor Simulator**: Models job execution duration (`@duration=8m`), resource weight (`@weight=7.5`), and target subsystem contention (`@resource=db-primary`), including jobs that start at `23:55` and spill across midnight into `00:05`.
3. **Harmonic Resonance Spectrum**: Quantifies `:00`, `:15`, `:30`, `:45`, and `5m-grid` harmonic clustering across your fleet.
4. **Constrained Minimax Phase-Shift Compiler**:
   - **Coordinate Descent / Minimax (`--strategy anneal`)**: Minimizes $L_\infty$ peak resource load and $L_2$ load variance across the 1,440-minute ring.
   - **Deterministic FNV-1a Hash Jitter (`--strategy fnv-hash`)**: Zero-coordination stateless phase assignment (compiles Jenkins `H/15 * * * *` semantics to standard POSIX/Vixie cron syntax).
   - **Prime-Modulo Splay (`--strategy prime-splay`)**: Coprime stride distribution across periodic lattices.
5. **Ready-to-Apply Unified Git Diff & CI Gate**: Generates a `git apply`-ready patch and supports `--max-crest-factor` for CI/CD pull request checks.

## Quick Start (CLI)

```bash
# 1. Analyze a production crontab and print the 24h waveform + collision spikes
python3 -m cronphase examples/prod.crontab

# 2. Run the constrained phase-shift compiler and emit a unified git diff patch
python3 -m cronphase examples/prod.crontab --optimize --diff

# 3. Gate CI pipelines if peak-to-mean crest factor exceeds 2.5x
python3 -m cronphase examples/k8s-cronjobs.yaml --optimize --max-crest-factor 2.5
```
