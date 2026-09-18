# taskferry-cloudrun

Run [Taskferry](https://github.com/taskferry/taskferry) jobs on **Google Cloud Run
Jobs** — serverless, run-to-completion container workloads.

```mermaid
flowchart LR
    APP["Application"]
    TP["Taskferry"]
    AD["taskferry-cloudrun"]
    CR["Cloud Run Jobs"]
    C["container"]

    APP --> TP --> AD --> CR --> C
```

This is the adapter that shows why Task and Job are separate primitives. A Cloud
Run Job has its own image, its own resource envelope, its own timeout and scales
to zero — none of which is "a task that takes a while".

## Install

```bash
pip install 'taskferry-cloudrun[gcp]'
```

## Use

```python
from taskferry import Taskferry, Resources

runtime = Taskferry.from_mapping(
    {
        "backends": {
            "heavy": {
                "factory": "cloudrun",
                "project": "my-project",
                "location": "europe-west1",
            }
        },
        "defaults": {"job": "heavy"},
    }
)

handle = runtime.jobs.submit(
    "build-cog",
    args=["--input", "gs://bucket/scene.tif"],
    env={"GDAL_CACHEMAX": "512"},
    timeout=3600,
)
print(handle.status())
```

## Capabilities

| Capability | Supported | Why |
| ---------- | :-------: | --- |
| `SUBMIT` · `STATE` · `CANCEL` · `LOGS` | yes | the Executions API |
| `ENVIRONMENT` · `PARALLELISM` · `TIMEOUT` · `RETRY` | yes | per-execution overrides |
| `CPU` · `MEMORY` · `GPU` | **no** | they live on the Job resource, not the execution |

A spec asking for `Resources(gpu=1)` is rejected at submit time rather than run
on a CPU. Route GPU work at a backend that really allocates GPUs
(`taskferry-jobs[kubernetes]`, `taskferry-jobs[aws]`).

Deploy the Job resource itself with Terraform or `gcloud`; Taskferry starts
executions of it.

## Testing

Both clients are injectable, so the whole suite runs with no GCP account:

```python
CloudRunJobBackend(project="p", location="eu", jobs_client=FakeJobs())
```

## License

Apache-2.0.
