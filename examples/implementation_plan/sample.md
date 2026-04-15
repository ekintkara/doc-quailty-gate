# Implementation Plan: User Authentication Refactor

## Objective

Refactor the existing monolithic authentication module into a modular, service-oriented architecture supporting OAuth 2.0, SAML, and password-based authentication.

## Background

The current auth module handles all authentication in a single 2000-line file. It couples session management, token generation, and user lookup. This makes testing difficult and prevents independent scaling.

## Architecture

### Current State
```
[Client] -> [Auth Controller] -> [Auth Service (monolith)] -> [Database]
```

### Target State
```
[Client] -> [Auth Gateway] -> [Session Service]
                            -> [Token Service]
                            -> [Identity Provider Router] -> [Password Provider]
                                                            -> [OAuth Provider]
                                                            -> [SAML Provider]
                            -> [User Service]
```

## Implementation Phases

### Phase 1: Extract Token Service (Week 1-2)
1. Create new `TokenService` class in `services/auth/token.py`
2. Extract JWT generation logic from `auth/service.py`
3. Extract JWT validation logic
4. Extract refresh token management
5. Add unit tests for TokenService (target: 95% coverage)
6. Update Auth Controller to use TokenService

**Dependencies:** None
**Risk:** Low - extract only, no behavioral change

### Phase 2: Extract Session Service (Week 2-3)
1. Create `SessionService` in `services/auth/session.py`
2. Move session creation and validation logic
3. Implement session storage adapter pattern
4. Support Redis and in-memory session stores
5. Add session TTL management
6. Write integration tests

**Dependencies:** Phase 1
**Risk:** Medium - session storage migration

### Phase 3: Implement Identity Provider Pattern (Week 3-5)
1. Define `IdentityProvider` abstract base class
2. Implement `PasswordProvider` wrapping existing password auth
3. Implement `OAuthProvider` for Google, GitHub
4. Implement `SAMLProvider` for enterprise SSO
5. Build `ProviderRouter` to select provider based on request
6. Add provider-specific configuration

**Dependencies:** Phase 2
**Risk:** High - new external integrations

### Phase 4: Build Auth Gateway (Week 5-6)
1. Create `AuthGateway` facade in `services/auth/gateway.py`
2. Wire together SessionService, TokenService, ProviderRouter
3. Implement request routing based on auth type
4. Add circuit breaker for external providers
5. Add logging and metrics

**Dependencies:** Phase 3
**Risk:** Medium - integration complexity

### Phase 5: Migration and Cutover (Week 6-7)
1. Create database migration for new session schema
2. Implement dual-write to old and new auth during migration
3. Deploy new auth gateway behind feature flag
4. Gradually route traffic to new gateway (10% -> 50% -> 100%)
5. Remove old auth module after full cutover

**Dependencies:** Phase 4
**Risk:** High - production migration

## Testing Strategy

- Unit tests for each service (target: 95% coverage)
- Integration tests for provider interactions
- E2E tests for full auth flows
- Load test: simulate 10,000 concurrent auth requests
- Security test: OWASP authentication checklist

## Rollback Plan

Each phase is behind a feature flag. If issues arise:
1. Disable the feature flag to revert to old code path
2. Phase 5 maintains dual-write, so old auth remains functional
3. Database changes are additive only (no destructive migrations)

## Monitoring

- Auth success/failure rates per provider
- Token generation latency (p50, p95, p99)
- Session creation rate
- External provider response times
- Error rates by provider and auth type

## Team

- Lead: TBD
- Backend Engineers: 2
- QA: 1
- DevOps: 0.5
