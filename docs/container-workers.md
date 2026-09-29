# Container workers

`lean4-docker` grades frozen modules in Linux containers. Each compilation,
dependency listing and `leanchecker --fresh` replay starts a new container.
Use `evaluate_benchmark` for final benchmark acceptance; `verify_attempt` is
the lower-level API and is not supported by this module-only backend.

The trusted organizer image must contain a fully built Lake project at `/project`,
with `lean-toolchain`, `lake-manifest.json`, and `lake`, `lean`, `leanchecker`
on PATH. Build dependencies ahead of time: evaluation containers have no network.
`docker/Dockerfile` builds a minimal core-Lean image from an explicitly selected
base digest, Lean release and official release SHA256. Mathlib benchmarks need
an organizer image with their exact dependency revisions built into it.

Supply an immutable image ID (`sha256:<64 hex>`) or repository digest
(`registry/repository@sha256:<64 hex>`), rather than an image tag:

```bash
ftp-eval grade --problems benchmarks/my-bench/problems \
  --submissions submissions --backend lean4-docker \
  --option image=sha256:IMAGE_DIGEST
```

Consult `ftp-eval grade --help` for option spellings. The benchmark manifest must
pin `toolchain.lean`, `toolchain.lake_manifest_sha256`, `toolchain.image_digest`,
and a full `toolchain.mathlib_rev` if Mathlib is present. The preflight verifies
these against the actual image and tests cross-module imports before grading.

Runtime policy uses a read-only root and input mounts, user 65532, no network,
no capabilities, no new privileges, bounded memory/CPU/PIDs/logs and a bounded
temporary filesystem. No writable host paths, cloud credentials or Docker socket
are passed to submissions. The executor copies back only the requested regular
file; links and extra archive members are refused. Workers are removed after
success, failure, timeout or interruption. A hard host termination can still leave
a worker and requires operator cleanup.

Replay uses Lean's built-in kernel checker in a fresh container. It is not an
independently implemented checker and does not establish immunity to every
compiler or kernel vulnerability. Axiom policy remains a separate final step.

`ContainerExecutor.run(ContainerJob)` is the cloud boundary. An AWS executor can
stage only that job's inputs, run the pinned organizer image with the same
isolation and resource policy, return bounded validated files, and remove the
worker on timeout/interruption. No AWS service is selected or provisioned here.
The supplied `DockerExecutor` is a local Linux implementation; use a trusted
Linux supervisor or run this package inside the organizer's orchestration image.

Validate an organizer image on that supervisor with
`PYTHONPATH=src python scripts/smoke_container.py IMAGE_DIGEST`. It exercises real
Lean compilation/replay, good and broken answers, gold macro isolation, universal
health probes, read-only execution, bounded output transfer and timeout removal.
