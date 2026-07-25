# ADR-0006: Configuration flows in; adapters don't read the environment

- Status: Accepted
- Date: 2026-07-24

## Context

12-factor apps configure from the environment, but a library that scatters
`os.environ["..."]` reads through its adapters is untestable, surprising, and
impossible to configure from Django settings, Vault, or a secret manager.

## Decision

```text
Environment / secrets / platform config → application settings → Taskport
```

- Taskport is configured by the **application**, which may source config from
  Django settings, plain Python, a dict, `.env`, Vault, Secret Manager, AWS
  Secrets Manager, Azure Key Vault, Kubernetes/Docker secrets, etc.
- Adapters receive configuration as constructor arguments. They do **not** reach
  into `os.environ` themselves.
- `taskport-core` provides **opt-in, injectable** env helpers (`env_str`,
  `env_int`, `env_bool`) that take the environment mapping as an argument. These
  are for the application's convenience, not a hidden dependency.
- Taskport reads **standard** variable names the ecosystem already defines
  (`REDIS_URL`, `DATABASE_URL`, `AWS_REGION`, `GOOGLE_CLOUD_PROJECT`,
  `AZURE_TENANT_ID`) and does **not** force `TASKPORT_`-prefixed duplicates.
- `taskport-django` uses the official `TASKS` setting; it does not invent
  `TASKPORT_TASKS` (section 36).
- Provider-specific options use the `ProviderOptions` escape hatch (ADR: section
  17), namespaced per provider, keeping common models clean.

## Consequences

- Everything is configurable and testable without touching global state.
- One configuration object can be built once and passed anywhere.
- Env vars are supported but never the *only* mechanism.
