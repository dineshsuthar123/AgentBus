# Business Value

## Engineering Teams

- Delegate bounded repository tasks while retaining human control over risky
  execution.
- See failures, retries, review decisions, and repository effects instead of
  trusting an opaque transcript.
- Resume interrupted work without rerunning successful terminal tasks.
- Preserve evidence for debugging, incident review, and quality governance.
- Keep local source and credentials out of a hosted agent service.

## High-Consequence Systems

Payment and financial systems turn small concurrency mistakes into duplicate
side effects, reconciliation work, and loss of trust. AgentBus does not claim to
prove a payment system correct. It demonstrates a safer engineering workflow:
an autonomous repair is scoped, its Maven execution requires a human decision,
sequential and concurrent behavior is tested, the final diff is reviewed, and
the run leaves verifiable evidence.

Incorrect automation should fail visibly rather than silently ship.
