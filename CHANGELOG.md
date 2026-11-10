# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.1] - 2026-11-10

- Fix release workflows: pin astral-sh/setup-uv to v10.3.3 (v0.2.0 was tagged but never reached PyPI).

## [0.2.0] - 2026-11-10

### Added

- Initial public release: fleet-wide cron load simulation, harmonic resonance
  spectrum, and the constrained phase-shift compiler (`anneal`, `fnv-hash`,
  `prime-splay`) with unified diff output and `--max-crest-factor` CI gate.
- Parses UNIX crontab, Kubernetes CronJob YAML and GitHub Actions schedules.
