# The Problem

AI coding agents can already generate patches. The harder engineering problem
is allowing them to act on a real repository without turning model output into
authorization.

An autonomous run can misunderstand repository scope, execute an unsafe tool,
lose state after interruption, retry without useful failure context, or produce
a plausible change that tests and review should reject. If those failures are
hidden inside a terminal transcript, a human cannot make a confident decision.

This matters most in high-consequence software. A duplicate payment event, for
example, can confirm the same payment more than once unless the implementation
is atomic under concurrent retries. An agent attempting that repair needs
bounded repository access, human control over execution, mechanical proof, and
an inspectable record of what actually happened.

The product question is therefore not only "Can a model write the fix?" It is:

> Can an engineering team safely control, verify, recover, and audit an
> autonomous repository change?
