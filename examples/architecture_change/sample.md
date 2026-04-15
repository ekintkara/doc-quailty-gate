# Architecture Change: Event-Driven Order Processing

## Current Architecture

Orders are processed synchronously through a monolithic Order Service. When a customer places an order:

1. Order Service validates inventory by calling Inventory Service
2. Order Service processes payment via Payment Service
3. Order Service creates shipment via Shipping Service
4. Order Service sends confirmation via Notification Service

All calls are synchronous REST APIs. If any downstream service is slow or unavailable, the entire order flow is blocked.

**Problems:**
- Single point of failure: if Payment Service is down, no orders can be placed
- Poor latency: total response time = sum of all downstream latencies
- Tight coupling: changes to any downstream service require Order Service updates
- No retry resilience: transient failures cause order failures

## Proposed Architecture

Move to an event-driven architecture using a message broker:

1. Order Service publishes `OrderCreated` event
2. Downstream services subscribe and process independently
3. Order Service tracks order state via a saga pattern
4. Dead letter queue handles failures

### Component Diagram

```
[Order Service] --> [Message Broker] --> [Inventory Worker]
                  |                  --> [Payment Worker]
                  |                  --> [Shipping Worker]
                  |                  --> [Notification Worker]
                  |
                  <-- [Saga Orchestrator] <-- [Completion Events]
```

### State Machine

```
CREATED -> INVENTORY_RESERVED -> PAYMENT_PROCESSED -> SHIPMENT_SCHEDULED -> COMPLETED
   |            |                     |
   v            v                     v
 CANCELLED   CANCELLED           PAYMENT_FAILED -> RETRY/REFUND
```

## Message Broker Selection

**Selected:** Apache Kafka

**Rationale:**
- High throughput (100K+ events/sec)
- Durable message storage with replay
- Strong ordering guarantees per partition
- Mature ecosystem and tooling
- Team has existing Kafka experience

**Alternatives Considered:**
- RabbitMQ: simpler but lower throughput
- AWS SQS/SNS: cloud lock-in concern
- Redis Streams: less mature for this use case

## Data Model Changes

### New: Order Events Table
```sql
CREATE TABLE order_events (
    event_id UUID PRIMARY KEY,
    order_id UUID NOT NULL REFERENCES orders(id),
    event_type VARCHAR(50) NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    processed_at TIMESTAMP,
    INDEX idx_order_id (order_id),
    INDEX idx_event_type (event_type)
);
```

### New: Order Saga State Table
```sql
CREATE TABLE order_saga_state (
    order_id UUID PRIMARY KEY REFERENCES orders(id),
    current_state VARCHAR(50) NOT NULL,
    inventory_status VARCHAR(20),
    payment_status VARCHAR(20),
    shipping_status VARCHAR(20),
    retry_count INT DEFAULT 0,
    last_error TEXT,
    updated_at TIMESTAMP DEFAULT NOW()
);
```

## Migration Strategy

### Phase 1: Deploy Kafka Infrastructure
- Provision Kafka cluster
- Set up monitoring and alerting
- Create topics: `order-events`, `order-saga-commands`, `order-dead-letter`

### Phase 2: Implement Saga Orchestrator
- Build saga state machine
- Implement compensating transactions for rollback
- Add saga timeout handling

### Phase 3: Dual-Write Period
- Order Service publishes events AND makes synchronous calls
- Compare results for consistency
- Verify event delivery and processing

### Phase 4: Cutover
- Switch workers to consume from Kafka only
- Remove synchronous downstream calls
- Monitor for issues

### Phase 5: Cleanup
- Remove old synchronous code paths
- Archive legacy tables
- Update documentation

## Rollback Plan

- Phase 3 maintains both paths, so reverting is straightforward
- Feature flags control Kafka publishing and consumption
- Saga state table allows resuming from any point

## Impact Assessment

### Services Changed
- Order Service: major refactor
- Inventory Service: add event consumer
- Payment Service: add event consumer
- Shipping Service: add event consumer
- Notification Service: add event consumer

### Infrastructure Added
- Kafka cluster (3 brokers)
- Schema Registry
- Kafka Connect for monitoring

### Performance Targets
- Order creation latency: < 200ms (down from 2-5s)
- Throughput: 10,000 orders/minute
- Event processing latency: < 1 second per step

## Monitoring

- Kafka consumer lag per topic
- Saga completion rate and time
- Dead letter queue depth
- Order state distribution
- Per-step processing latency

## Timeline

- Phase 1: Week 1-2
- Phase 2: Week 3-5
- Phase 3: Week 6-7
- Phase 4: Week 8
- Phase 5: Week 9-10

Total: 10 weeks
