# Independent serverless benchmark functions

These are three standalone Knative functions used by the intent controller.
They are never invoked as a function chain.

| Function | Upstream suite | Fixed controller workload |
|---|---|---|
| `dynamic-html` | SeBS `110.dynamic-html` | Render 1,000 seeded random values |
| `graph-pagerank` | SeBS `501.graph-pagerank` | PageRank on a seeded 10,000-vertex graph |
| `gzip-compression` | FunctionBench gzip | Write and compress a unique 1 MiB temporary file |

Each project contains its upstream revision and license metadata. The HTTP
inputs are defined centrally in `controller/config/function-profiles.yaml`.

With both registry tunnels running:

```bash
make -C functions check
make -C functions build-push-all
make -C functions verify-images
```

The controller expects the identical `v1` image in both edge registries before
starting a two-cluster orchestration.
