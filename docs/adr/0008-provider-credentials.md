# ADR-0008: Use provider-native credential chains, invent nothing

- Status: Accepted
- Date: 2026-07-24

## Context

Credential handling is security-critical and already solved well by each cloud
provider. A bespoke "Taskport credentials" system would be redundant and a
liability.

## Decision

Taskport defines **no** credential system. Adapters use each provider's standard
credential resolution and default to the most secure, least-privilege option:

- **GCP:** Application Default Credentials → Workload Identity → attached service
  account. Adapters construct clients with no explicit keys by default.
- **AWS:** the default boto3 credential chain → IAM roles → task roles →
  instance profiles.
- **Azure:** `DefaultAzureCredential` → Managed Identity.
- **Kubernetes:** in-cluster `ServiceAccount` config, falling back to kubeconfig
  for local use.

Static long-lived secrets are **not** documented as the primary strategy. If an
application must inject explicit credentials, it does so through the provider
SDK's own mechanism and passes the constructed client to the adapter — Taskport
does not store or manage secrets.

## Consequences

- No secret storage or rotation logic in Taskport.
- Adapters accept an optional pre-constructed client (dependency injection),
  which is also how they are tested with fakes (section 38).
- Security posture inherits the provider's best practices (least privilege, IAM,
  OIDC, managed identity).
