"""The server-rendered product UI: landing, auth, mailbox connect, inbox.

Plain Jinja templates and one stylesheet — no build step, no bundler, no npm.
The pages call the same services the JSON API does (``app.saas``,
``app.copilot``), so there is exactly one implementation of every behaviour.
"""
