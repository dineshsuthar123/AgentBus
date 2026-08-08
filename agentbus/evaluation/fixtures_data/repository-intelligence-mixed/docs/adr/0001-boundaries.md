# Service Boundaries

Frontend, Python, Java, and Go services remain separate components.

The Python service must not depend directly on the shared-python component. The
fixture intentionally violates this boundary so impact analysis has explicit
cross-project evidence to report.
