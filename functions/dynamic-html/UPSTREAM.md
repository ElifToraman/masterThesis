# Upstream provenance

- Project: SeBS: Serverless Benchmark Suite
- Repository: https://github.com/spcl/serverless-benchmarks
- Benchmark: `benchmarks/100.webapps/110.dynamic-html`
- Inspected revision: `b37f475542436ffb6b0ad03bd5972a680ecb8186`
- License: BSD 3-Clause

The benchmark kernel and template were adapted to the repository's Knative
Python HTTP interface. Input validation, deterministic per-request randomness,
timing, and the common JSON response envelope are local modifications.
