# Upstream provenance

- Project: FunctionBench
- Repository: https://github.com/ddps-lab/serverless-faas-workbench
- Benchmark: `aws/disk/gzip_compression`
- Inspected revision: `bebda5130aaf8d9daf526186c274ed6cbf8f95af`
- License: Apache License 2.0

The benchmark kernel was adapted to the repository's Knative Python HTTP
interface. Bounded input validation, unique temporary files, cleanup, timing,
output hashing, and the common JSON response envelope are local modifications.
