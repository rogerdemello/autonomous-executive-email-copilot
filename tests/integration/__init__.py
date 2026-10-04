"""Integration tests: the product driven through the real provider classes.

Every test in this package runs the shipping code path — the OAuth callback,
:func:`app.saas.provider_factory.build_provider`, the real ``GmailProvider`` and
``MicrosoftGraphProvider``, ``InboxSyncService``, the signed-in HTML app — against
a stateful fake of each provider's HTTP API (:mod:`tests.integration.wire`).
The only patch in the product's path is ``httpx`` itself, at the very bottom.
(Two things outside it are also patched, and neither is under test: the SMTP
sender, and the background worker's deliberately cross-tenant work list — see
``pin_sweep_to_workspace``.)

The reason this package exists is written on every commit between v1.1.0 and now:
HTML bodies stored as markup, replies that did not thread for the recipient,
Outlook categories deleted by triage, escalations addressed to the customer,
Graph returning the oldest mail in the mailbox. Seven bugs, all of them in
production-only code, and the suite was green on every one — because the tests
either exercised a provider on its own with canned per-call responses, or
exercised the product against ``DemoProvider``, which is a dict in memory whose
bodies are plain text and whose mailbox is never written to.

So the fake here is not a stub. It is a mailbox: writes change what later reads
return, a label has to exist before it can be applied, a reply only threads for
the recipient if the headers say so, an expired access token is a 401 until the
refresh token is spent, and a URL nobody taught it about is a test failure rather
than an empty success.
"""
