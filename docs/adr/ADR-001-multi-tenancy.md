# ADR-001: Multi-tenancy with shared schema and organization scoping

- **Status:** Accepted
- **Date:** 2026-09-23

## Context

The platform starts with one organization and must become a multi-tenant SaaS without a rewrite. Tenant data must never leak across organizations.

## Decision

Shared database, shared schema. `organization_id` on every tenant-owned table (denormalised even when derivable through a parent). Isolation is enforced server-side: `TenantScopedModel` + `for_organization()`, `OrganizationScopedMixin` (membership-checked `X-Organization-ID`, 404 for foreign tenants), `ensure_same_organization()` in services, and globally unique provider ids so webhook routing maps to exactly one tenant.

## Consequences

- (+) One schema to migrate; cheap tenants; simple cross-tenant ops tooling.
- (+) Every index leads with the tenant, so queries stay tenant-local as data grows.
- (-) Isolation depends on code discipline, mitigated by the fail-closed mixin and tests (`tests/api`). PostgreSQL row-level security is the planned second layer.
- Rejected: schema-per-tenant (migration fan-out, connection pressure at hundreds of tenants); database-per-tenant (operational cost far beyond current needs).
