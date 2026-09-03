"""
varco_core
=============
Backend-agnostic domain model and query layer.

All stable public symbols are importable directly from ``varco_core``::

    # Domain
    from varco_core import DomainModel, AuditedDomainModel, cast_raw, register
    from varco_core import TenantDomainModel, TenantAuditedDomainModel
    from varco_core import AbstractMapper, AsyncRepository, AsyncUnitOfWork
    from varco_core import DomainModelRegistry, RepositoryProvider

    # Metadata
    from varco_core.meta import (
        FieldHint, ForeignKey, PrimaryKey, PKStrategy,
        UniqueConstraint, CheckConstraint, pk_field,
    )

    # Query system
    from varco_core import QueryBuilder, QueryParams, QueryParser
    from varco_core import SortField, SortOrder, Operation

    # DTOs (API layer)
    from varco_core import CreateDTO, ReadDTO, UpdateDTO, UpdateOperation

Sub-package layout
------------------
    varco_core/
    ├── model.py          — DomainModel, cast_raw
    ├── meta.py           — FieldHint, ForeignKey, PKStrategy, constraints
    ├── mapper.py         — AbstractMapper (bidirectional ORM ↔ domain)
    ├── registry.py       — DomainModelRegistry, @register decorator
    ├── repository.py     — AsyncRepository ABC (CRUD + query)
    ├── providers.py      — RepositoryProvider ABC + autodiscover
    ├── uow.py            — AsyncUnitOfWork ABC
    ├── dto.py            — CreateDTO, ReadDTO, UpdateDTO, UpdateOperation
    └── query/
        ├── type.py       — AST node types
        ├── builder.py    — Fluent QueryBuilder
        ├── params.py     — QueryParams value object
        ├── parser.py     — QueryParser (string → AST via Lark)
        ├── transformer.py— Lark transformer
        ├── grammar.lark  — Query grammar
        ├── visitor/      — ASTVisitor, optimizer, type coercion, SA compiler
        └── applicator/   — QueryApplicator ABC + SA implementation
"""

from __future__ import annotations

# ── Auth helpers ─────────────────────────────────────────────────────────────────
from varco_core.auth.helpers import (
    GrantBasedAuthorizer,
    OwnershipAuthorizer,
    RoleBasedAuthorizer,
)

# ── Authority layer ─────────────────────────────────────────────────────────────
from varco_core.authority import (
    AuthorityError,
    AuthoritySource,
    AuthorizationConfig,
    IssuerNotFoundError,
    JwtAuthority,
    KeyLoadError,
    MultiKeyAuthority,
    TrustedIssuerEntry,
    TrustedIssuerRegistry,
    UnknownKidError,
)

# ── Cache system ────────────────────────────────────────────────────────────────
from varco_core.cache import (
    AsyncCache,
    CacheBackend,
    CachedService,
    CacheInvalidated,
    CacheInvalidationConsumer,
    CacheInvalidationEvent,
    CacheServiceMixin,
    CacheSettings,
    CompositeStrategy,
    ExplicitStrategy,
    InMemoryCache,
    InvalidationStrategy,
    LayeredCache,
    NoOpCache,
    TaggedStrategy,
    TTLStrategy,
    cached,
)

# ── Settings base ───────────────────────────────────────────────────────────────
from varco_core.config import VarcoSettings
from varco_core.connection import (
    BasicAuthConfig,
    ConnectionSettings,
    OAuth2Config,
    SaslConfig,
    SSLConfig,
)

# ── Ambient request context (Plan 011 X1) ────────────────────────────────────
from varco_core.context import (
    AmbientVar,
    NullTenantDefaults,
    RequestContext,
    Resolved,
    StaticTenantDefaults,
    TenantDefaultsProvider,
    TenantLocalizationDefaults,
    current_locale,
    current_request_context,
    current_timezone,
    request_context,
    resolve_precedence,
)

# ── Deprecation mechanism (Plan 022 / §D-DEP) ─────────────────────────────────
from varco_core.deprecation import deprecated, deprecated_alias

# ── DTO layer ──────────────────────────────────────────────────────────────────
from varco_core.dto import (
    CreateDTO,
    ReadDTO,
    TCreateDTO,
    TReadDTO,
    TUpdateDTO,
    UpdateDTO,
    UpdateOperation,
)
from varco_core.dto.factory import DTOSet, generate_dtos
from varco_core.dto.pagination import (
    PageCursor,
    PagedReadDTO,
    SortCursorField,
    paged_response,
)

# ── Event system ────────────────────────────────────────────────────────────────
from varco_core.event import (
    CHANNEL_ALL,
    CHANNEL_DEFAULT,
    AbstractDeadLetterQueue,
    AbstractEventBus,
    AbstractEventProducer,
    BusEventProducer,
    ChannelManager,
    DeadLetterEntry,
    EntityCreatedEvent,
    EntityDeletedEvent,
    EntityEvent,
    EntityUpdatedEvent,
    ErrorPolicy,
    Event,
    EventBusSettings,
    EventConsumer,
    EventMiddleware,
    InMemoryDeadLetterQueue,
    InMemoryEventBus,
    JsonEventSerializer,
    NoopEventProducer,
    Subscription,
    listen,
)

# ── Error codes and HTTP error mapping ──────────────────────────────────────────
from varco_core.exception.codes import ErrorCode, FastrestErrorCodes, VarcoErrorCodes
from varco_core.exception.http import (
    AnyErrorCode,
    ErrorMessage,
    FieldError,
    MessageResolver,
    error_code_for,
    error_message_for,
    register_error_code,
)

# ── Domain layer ───────────────────────────────────────────────────────────────
from varco_core.exception.repository import StaleEntityError
from varco_core.exception.settings import ErrorEnvelopeSettings

# ── Health probes ────────────────────────────────────────────────────────────────
from varco_core.health import (
    CompositeHealthCheck,
    HealthCheck,
    HealthResult,
    HealthStatus,
)

# ── Job layer ────────────────────────────────────────────────────────────────────
from varco_core.job import (
    AbstractJobRunner,
    AbstractJobStore,
    Job,
    JobStatus,
    auth_context_from_snapshot,
    auth_context_to_snapshot,
)

# ── JWK layer ──────────────────────────────────────────────────────────────────
from varco_core.jwk import (
    JsonWebKey,
    JsonWebKeySet,
    JwkBuilder,
)

# ── JWT layer ──────────────────────────────────────────────────────────────────
from varco_core.jwt import (
    IDENTITY,
    PROFILE_METADATA_KEY,
    SYSTEM_ISSUER,
    CanonicalClaim,
    ClaimMapping,
    ClaimPath,
    ClaimRule,
    ClaimTransformer,
    ClaimTransformError,
    IdentityClaimTransformer,
    JsonWebToken,
    JwtBuilder,
    JwtException,
    JwtParser,
    JwtUtil,
    JwtVerificationSettings,
    MappingClaimTransformer,
    TokenProfile,
    TokenProfileError,
    TokenProfileRegistry,
    ValueShape,
    configure_claim_transforms,
    configure_token_profiles,
    read_claim,
    reset_claim_transforms,
    reset_token_profiles,
    resolve_claim_transformer,
    resolve_token_profile,
)
from varco_core.mapper import AbstractMapper

# NOTE: varco_core.migrator (DomainMigrator — data/field migration for domain
# models) owns the top-level names "MigrationError" and "MigrationPlan". Until
# 3.0.0 the *schema*-migration package owned those same two names, so they were
# deliberately NOT re-exported here and had to be imported from
# varco_core.migration explicitly. Plan 022 / AB-2 renamed the newer, narrower
# schema pair to SchemaMigrationError / SchemaMigrationPlan, which closes that
# hole: both concepts are now re-exported below and an import site says which
# one it means. The old varco_core.migration names still resolve there as
# deprecated aliases until 4.0.0.
from varco_core.migration import (
    AbstractMigrator,
    InMemoryMigrator,
    IrreversibleMigrationError,
    MigrationBackendUnavailable,
    MigrationLockTimeout,
    MigrationReport,
    MigrationSettings,
    PendingMigrationsError,
    Revision,
    SchemaMigrationError,
    SchemaMigrationPlan,
)
from varco_core.migrator import (
    DomainMigrator,
    MigrationError,
    MigrationPlan,  # noqa: F401
    StepSpec,  # noqa: F401
)
from varco_core.model import (
    AuditedDomainModel,
    DomainModel,
    SoftDeleteAuditedDomainModel,
    SoftDeleteDomainModel,
    SoftDeleteMixin,
    TenantAuditedDomainModel,
    TenantDomainModel,
    TenantMixin,
    TenantVersionedDomainModel,
    VersionedDomainModel,
    cast_raw,
)

# ── OpenTelemetry observability ──────────────────────────────────────────────────
from varco_core.observability import (
    CounterConfig,
    HistogramConfig,
    Metric,
    MetricKind,
    OtelConfig,
    OtelConfiguration,
    SpanConfig,
    TracingServiceMixin,
    counter,
    create_counter,
    create_histogram,
    create_span,
    histogram,
    register_gauge,
    span,
)
from varco_core.providers import RepositoryProvider

# ── Query system ───────────────────────────────────────────────────────────────
from varco_core.query.builder import QueryBuilder
from varco_core.query.params import QueryParams
from varco_core.query.parser import QueryParser
from varco_core.query.type import Operation, SortField, SortOrder
from varco_core.registry import DomainModelRegistry, register
from varco_core.repository import AsyncRepository

# ── Resilience patterns ──────────────────────────────────────────────────────────
from varco_core.resilience import (
    CallTimeoutError,
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitOpenError,
    CircuitState,
    RetryExhaustedError,
    RetryPolicy,
    circuit_breaker,
    retry,
    timeout,
)

# ── Serialization ────────────────────────────────────────────────────────────────
from varco_core.serialization import JsonSerializer, NoOpSerializer, Serializer

# ── Soft delete ─────────────────────────────────────────────────────────────────
from varco_core.service.soft_delete import SoftDeleteService

# ── Multi-tenancy ───────────────────────────────────────────────────────────────
from varco_core.service.tenant import (
    TenantAwareService,
    TenantUoWProvider,
    current_tenant,
    tenant_context,
)

# ── Service type aliases and protocols ──────────────────────────────────────────
from varco_core.service.types import Assembler, ServiceProtocol
from varco_core.service.validation import ValidatorServiceMixin

# ── Multitenancy (Plan 007) ─────────────────────────────────────────────────
# Grep-checked for collisions against every existing top-level name before
# export (the MigrationError/MigrationPlan lesson from Plan 006) — none of
# these names were previously bound at the top level.
from varco_core.tenancy import (
    AbstractTenantCatalog,
    AbstractTenantProvisioner,
    DestructiveOperationRefused,
    DynamicTenantUoWProvider,
    ExternalTenantProvisioner,
    StaticTenantCatalog,
    TenancySettings,
    TenantDescriptor,
    TenantIsolation,
    TenantIsolationError,
    TenantNotFoundError,
    TenantResourcePool,
    TenantScope,
    TenantStatus,
)

# ── Tracing / correlation ID ────────────────────────────────────────────────────
from varco_core.tracing import (
    CorrelationIdFilter,
    correlation_context,
    current_correlation_id,
    generate_correlation_id,
)
from varco_core.uow import AsyncUnitOfWork

# ── Validation layer ─────────────────────────────────────────────────────────────
from varco_core.validation import (
    VALID,
    CompositeValidator,
    DomainModelValidator,
    ValidationError,
    ValidationResult,
    Validator,
)

# NOTE (Plan 025 / T1, T2): varco_core.watch and varco_core.reload are deliberately NOT
# re-exported here — import them explicitly (`from varco_core.watch import StatPollWatcher`,
# `from varco_core.reload import ReloadableResource`). Plan 028 / P1 is about *shrinking* this
# file's eager import graph, and varco_core.watch is meant to be usable from a sidecar or CLI
# without paying for the whole framework's import cost.

__all__ = [
    # ── Ambient request context (Plan 011 X1) ──────────────────────────────────
    "AmbientVar",
    "Resolved",
    "resolve_precedence",
    "RequestContext",
    "current_request_context",
    "current_locale",
    "current_timezone",
    "request_context",
    "TenantDefaultsProvider",
    "TenantLocalizationDefaults",
    "NullTenantDefaults",
    "StaticTenantDefaults",
    # ── Domain base ────────────────────────────────────────────────────────────
    "DomainModel",
    "AuditedDomainModel",
    "VersionedDomainModel",
    "TenantMixin",
    "TenantDomainModel",
    "TenantAuditedDomainModel",
    "TenantVersionedDomainModel",
    "cast_raw",
    # ── Soft delete domain mixins ───────────────────────────────────────────────
    "SoftDeleteMixin",
    "SoftDeleteDomainModel",
    "SoftDeleteAuditedDomainModel",
    # ── Migration ──────────────────────────────────────────────────────────────
    "DomainMigrator",
    "MigrationError",
    "StaleEntityError",
    # ── Abstraction layer ──────────────────────────────────────────────────────
    "AbstractMapper",
    "AsyncRepository",
    "AsyncUnitOfWork",
    # ── Registration ───────────────────────────────────────────────────────────
    "DomainModelRegistry",
    "register",
    # ── Provider ABC ───────────────────────────────────────────────────────────
    "RepositoryProvider",
    # ── Deprecation mechanism (Plan 022 / §D-DEP) ──────────────────────────────
    "deprecated",
    "deprecated_alias",
    # ── DTO layer ──────────────────────────────────────────────────────────────
    "CreateDTO",
    "ReadDTO",
    "UpdateDTO",
    "UpdateOperation",
    "TCreateDTO",
    "TReadDTO",
    "TUpdateDTO",
    "DTOSet",
    "generate_dtos",
    # ── Pagination ──────────────────────────────────────────────────────────────
    "SortCursorField",
    "PageCursor",
    "PagedReadDTO",
    "paged_response",
    # ── Query system ───────────────────────────────────────────────────────────
    "QueryBuilder",
    "QueryParams",
    "QueryParser",
    "SortField",
    "SortOrder",
    "Operation",
    # ── Multi-tenancy ───────────────────────────────────────────────────────────
    "TenantAwareService",
    "TenantUoWProvider",
    "current_tenant",
    "tenant_context",
    # ── Multitenancy — isolation strategies & control plane (Plan 007) ──────────
    "TenantIsolation",
    "TenantScope",
    "TenantStatus",
    "TenancySettings",
    "TenantDescriptor",
    "AbstractTenantCatalog",
    "StaticTenantCatalog",
    "TenantNotFoundError",
    "TenantIsolationError",
    "TenantResourcePool",
    "DynamicTenantUoWProvider",
    "AbstractTenantProvisioner",
    "ExternalTenantProvisioner",
    "DestructiveOperationRefused",
    # ── Soft delete service ─────────────────────────────────────────────────────
    "SoftDeleteService",
    # ── Service type aliases ────────────────────────────────────────────────────
    "Assembler",
    "ServiceProtocol",
    # ── Validation layer ─────────────────────────────────────────────────────────
    "VALID",
    "CompositeValidator",
    "DomainModelValidator",
    "ValidationError",
    "ValidationResult",
    "Validator",
    "ValidatorServiceMixin",
    # ── Settings base ─────────────────────────────────────────────────────────────
    "VarcoSettings",
    # ── Serialization ────────────────────────────────────────────────────────────
    "Serializer",
    "JsonSerializer",
    "NoOpSerializer",
    # ── Event system ─────────────────────────────────────────────────────────────
    "CHANNEL_ALL",
    "CHANNEL_DEFAULT",
    "AbstractDeadLetterQueue",
    "AbstractEventBus",
    "AbstractEventProducer",
    "BusEventProducer",
    "ChannelManager",
    "DeadLetterEntry",
    "EntityCreatedEvent",
    "EntityDeletedEvent",
    "EntityEvent",
    "EntityUpdatedEvent",
    "ErrorPolicy",
    "Event",
    "EventBusSettings",
    "EventConsumer",
    "EventMiddleware",
    "JsonEventSerializer",
    "InMemoryDeadLetterQueue",
    "InMemoryEventBus",
    "NoopEventProducer",
    "Subscription",
    "listen",
    # ── Cache system ──────────────────────────────────────────────────────────────
    "AsyncCache",
    "CacheBackend",
    "CacheSettings",
    "NoOpCache",
    "InMemoryCache",
    "LayeredCache",
    "InvalidationStrategy",
    "TTLStrategy",
    "ExplicitStrategy",
    "TaggedStrategy",
    "CompositeStrategy",
    "CacheServiceMixin",
    "CacheInvalidationConsumer",
    "cached",
    "CacheInvalidated",
    "CacheInvalidationEvent",
    "CachedService",
    # ── Tracing / correlation ID ─────────────────────────────────────────────────
    "CorrelationIdFilter",
    "correlation_context",
    "current_correlation_id",
    "generate_correlation_id",
    # ── OpenTelemetry observability ───────────────────────────────────────────────
    "OtelConfig",
    "OtelConfiguration",
    "span",
    "SpanConfig",
    "create_span",
    "counter",
    "CounterConfig",
    "histogram",
    "HistogramConfig",
    "create_counter",
    "create_histogram",
    "TracingServiceMixin",
    "Metric",
    "MetricKind",
    "register_gauge",
    # ── Health probes ─────────────────────────────────────────────────────────────
    "HealthCheck",
    "HealthResult",
    "HealthStatus",
    "CompositeHealthCheck",
    # ── Auth helpers ─────────────────────────────────────────────────────────────
    "GrantBasedAuthorizer",
    "OwnershipAuthorizer",
    "RoleBasedAuthorizer",
    # ── Error codes and HTTP error mapping ───────────────────────────────────────
    "AnyErrorCode",
    "ErrorCode",
    "FastrestErrorCodes",
    "VarcoErrorCodes",
    "ErrorMessage",
    "FieldError",
    "MessageResolver",
    "ErrorEnvelopeSettings",
    "error_code_for",
    "error_message_for",
    "register_error_code",
    # ── JWT layer ───────────────────────────────────────────────────────────────
    "SYSTEM_ISSUER",
    "JsonWebToken",
    "JwtBuilder",
    "JwtParser",
    "JwtUtil",
    # ── JWT claim transformation + token profiles (Plan 002) ────────────────────
    "JwtException",
    "ClaimTransformError",
    "TokenProfileError",
    "JwtVerificationSettings",
    "PROFILE_METADATA_KEY",
    "TokenProfile",
    "TokenProfileRegistry",
    "configure_token_profiles",
    "reset_token_profiles",
    "resolve_token_profile",
    "ClaimPath",
    "read_claim",
    "ValueShape",
    "CanonicalClaim",
    "ClaimMapping",
    "ClaimRule",
    "ClaimTransformer",
    "IdentityClaimTransformer",
    "IDENTITY",
    "MappingClaimTransformer",
    "configure_claim_transforms",
    "reset_claim_transforms",
    "resolve_claim_transformer",
    # ── JWK layer ───────────────────────────────────────────────────────────────
    "JsonWebKey",
    "JsonWebKeySet",
    "JwkBuilder",
    # ── Resilience patterns ──────────────────────────────────────────────────────
    "CallTimeoutError",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitOpenError",
    "CircuitState",
    "RetryExhaustedError",
    "RetryPolicy",
    "circuit_breaker",
    "retry",
    "timeout",
    # ── Job layer ────────────────────────────────────────────────────────────────
    "Job",
    "JobStatus",
    "AbstractJobStore",
    "AbstractJobRunner",
    "auth_context_to_snapshot",
    "auth_context_from_snapshot",
    # ── Authority layer ─────────────────────────────────────────────────────────
    "JwtAuthority",
    "MultiKeyAuthority",
    "TrustedIssuerRegistry",
    "TrustedIssuerEntry",
    "AuthoritySource",
    "AuthorizationConfig",
    "AuthorityError",
    "UnknownKidError",
    "IssuerNotFoundError",
    "KeyLoadError",
    # ── Connection abstraction layer ─────────────────────────────────────────────
    "SSLConfig",
    "BasicAuthConfig",
    "OAuth2Config",
    "SaslConfig",
    "ConnectionSettings",
    # ── Schema migrations (varco_core.migration) ────────────────────────────────
    # Renamed from MigrationError/MigrationPlan in 3.0.0 (Plan 022 / AB-2) so
    # they no longer collide with varco_core.migrator's domain pair — see the
    # NOTE above the `from varco_core.migration import (...)` block.
    "SchemaMigrationError",
    "SchemaMigrationPlan",
    "AbstractMigrator",
    "MigrationReport",
    "Revision",
    "MigrationSettings",
    "PendingMigrationsError",
    "MigrationLockTimeout",
    "IrreversibleMigrationError",
    "MigrationBackendUnavailable",
    "InMemoryMigrator",
]
