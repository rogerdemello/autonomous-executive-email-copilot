"""Executive Email Copilot — the product.

Layers, from the bottom up:

- ``app.core``    configuration, persistence, domain models, security
- ``app.copilot`` reading a real mailbox, inferring signals, deciding, drafting
- ``app.llm``     optional model-backed decisioning (degrades to heuristics)
- ``app.saas``    accounts, organizations, RBAC, licensing, mailbox connections
- ``app.web``     the server-rendered UI
- ``app.main``    the ASGI application that assembles all of the above

The research benchmark this project grew out of lives in ``research/`` and
imports from here, never the other way around.
"""

__version__ = "1.0.0"

__all__ = ["__version__"]
