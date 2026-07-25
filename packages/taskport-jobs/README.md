# taskport-jobs

Portable execution of **finite workloads** — ETL, GIS, raster/tiles, ML training,
large imports, CPU/RAM-intensive batch — across local, GCP, AWS, Azure and
Kubernetes. Part of the [Taskport](https://taskport.dev) family.

Plain Python. **Does not require Django.**

```bash
pip install taskport-jobs                 # local runner only
pip install taskport-jobs[gcp]            # + Google Cloud Run Jobs
pip install taskport-jobs[aws]            # + AWS Batch
pip install taskport-jobs[azure]          # + Azure Container Apps Jobs
pip install taskport-jobs[kubernetes]     # + Kubernetes Jobs
```

## Usage

```python
from taskport.jobs import JobSpec, runners

handle = runners["default"].run(
    JobSpec(name="nightly-etl", command=["python", "-m", "etl", "--date", "2026-07-24"])
)
result = runners["default"].get(handle)
print(result.status)  # running / succeeded / failed / cancelled
```

Configure named runners (lazy — no SDK imported until first use):

```python
from taskport.jobs import runners

runners.configure(
    {
        "heavy": {
            "factory": "taskport.jobs.runners.gcp:make_cloud_run_jobs_runner",
            "project": "my-proj",
            "location": "us-central1",
        },
        "batch": {
            "factory": "taskport.jobs.runners.aws:make_batch_runner",
            "region": "us-east-1",
            "job_queue": "q",
            "job_definition": "d",
        },
    }
)
runners["heavy"].run(JobSpec(name="raster", parallelism=8, timeout=3600))
```

## Honest capabilities

Every runner advertises what it supports. A spec requesting something the runner
can't do is **rejected**, never silently ignored:

```python
from taskport.jobs import JobSpec, JobResources, runners

# LocalJobRunner has no GPU → this raises UnsupportedCapabilityError:
runners["default"].run(JobSpec(name="x", command=["true"], resources=JobResources(gpu=1)))
```

## Contract tests

`taskport.jobs.contract.JobRunnerContract` is a reusable pytest base every
runner (including your own) must pass. Which assertions are mandatory is driven
by the runner's capabilities.

## Delivery semantics

A job runs **once** per submission. Assume the surrounding infrastructure can
retry on failure; write idempotent workloads. See the family docs on delivery
semantics.

## License

Apache-2.0.
