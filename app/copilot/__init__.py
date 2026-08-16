"""The copilot itself: how a real inbox is read, understood, and acted on.

``providers`` talk to mail systems (Gmail, Microsoft Graph, the demo mailbox),
``enrich`` infers priority/risk signals from a raw message, ``policy`` decides
what to do, and ``pipeline`` turns those decisions into approval-gated
proposals. No simulator, no gold labels, no reward.
"""
