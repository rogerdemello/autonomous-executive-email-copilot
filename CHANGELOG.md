# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Phase A (evaluation correctness & reproducibility) is in progress. Highlights so far:

### Added

- Results harness for running and aggregating benchmark scores across
  `(task, seed, persona)` combinations into reproducible result sets.
- Scenario schema that formalizes task scenario definitions, enabling
  validation of fixtures and stable, documented inputs.
- Methodology document describing how scores are computed and compared, so
  reported numbers are traceable to a procedure.

### Changed

- Improved test isolation: tests no longer leak state (config, env vars,
  database artifacts) between cases, making the suite deterministic regardless
  of run order.

### Fixed

- Azure OpenAI authentication: corrected credential/base-URL handling so that
  Azure-hosted deployments authenticate correctly (previously misconfigured).

[Unreleased]: https://github.com/OWNER/REPO/compare/HEAD
