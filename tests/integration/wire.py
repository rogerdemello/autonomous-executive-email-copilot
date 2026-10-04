"""A stateful fake of the Gmail API, Microsoft Graph, and both token endpoints.

Installed one level below the provider classes, by patching ``httpx.request`` and
``httpx.post`` — so the code under test includes ``_httpx_transport`` itself, the
``Authorization`` header it builds, and its handling of a response with no JSON
body. The providers' own ``transport=`` seam is deliberately *not* used here;
``tests/test_gmail_provider.py`` covers that, and going through httpx is what
makes this an integration test rather than a second unit test.

(``TestClient`` is an ``httpx.Client`` subclass and calls its own bound methods,
so patching the module-level functions never touches the app's own requests.)

What makes it a mailbox rather than a stub, and why each property is here:

* **Writes change what later reads return.** A label applied in one sync is
  visible in the next; an archived message stops being fetched. Canned
  per-call responses cannot express "this sync must not re-propose what the
  last one already did".
* **Ordering is the fake's choice, not the insertion order.** Graph does not
  document a default sort for ``/messages``, so with no ``$orderby`` this
  returns the *oldest* mail first. A provider that forgets to ask for newest
  first therefore syncs a mailbox's history instead of its morning — which is
  exactly what Graph did until ``$orderby`` was made explicit.
* **Gmail threads only when the headers say so.** ``threadId`` is a Gmail-side
  concept; the recipient's client threads on ``In-Reply-To``/``References``. A
  send that carries a ``threadId`` and no ``In-Reply-To`` is accepted and
  recorded as ``threaded=False``, which is precisely the bug that shipped.
* **Graph's ``categories`` PATCH replaces the collection.** It is destructive
  here because it is destructive there. That is the only way a test can prove
  triage keeps the categories a person filed a message under.
* **A stale access token is a 401 until the refresh token is spent.** Google
  keeps its refresh token; Microsoft rotates it, and the rotated one is the
  only one that works next time — so the token the product persisted has to be
  the right one or the second sync fails.
* **An unrouted URL is a failure, not an empty 200.** ``install`` returns the
  list of requests no wire claimed, and the fixtures assert it is empty. A
  fake that answers ``{}`` to anything turns a wrong URL into a green test.
"""

from __future__ import annotations

import base64
import copy
from dataclasses import dataclass, field
from datetime import datetime
from email import message_from_bytes
from email.header import decode_header, make_header
from urllib.parse import parse_qs, unquote

GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GRAPH_BASE = "https://graph.microsoft.com/v1.0/me"
MICROSOFT_TOKEN_HOST = "https://login.microsoftonline.com"

# What Gmail's list endpoint returns when maxResults is not given, and what
# Graph returns when $top is not given. Both matter: a provider that omits the
# cap silently inherits these, and they are not the same number.
GMAIL_DEFAULT_MAX_RESULTS = 100
GRAPH_DEFAULT_TOP = 10


# --------------------------------------------------------------------------- #
# Mailbox content
# --------------------------------------------------------------------------- #
@dataclass
class WireMessage:
    """One message, provider-neutral, as the *mailbox* holds it.

    The same list of these is handed to both wires, which is what lets a test
    assert that Gmail and Graph turn identical mail into identical rows. Each
    wire renders it in its own dialect and keeps its own mutable copy.
    """

    id: str
    sender: str
    subject: str
    received_at: datetime
    sender_name: str = ""
    text: str = ""
    html: str = ""
    reply_to: str = ""
    thread_id: str = ""
    references: str = ""
    # Gmail label ids on the message. "INBOX" is a system id and a real one.
    labels: list[str] = field(default_factory=lambda: ["INBOX", "UNREAD"])
    # Graph categories. Seeded non-empty on purpose in at least one message:
    # the person filed it themselves, and triage must not wipe that.
    categories: list[str] = field(default_factory=list)
    # Overrides Graph's declared body.contentType. Real senders compose HTML
    # into a message Graph then labels "text", and the provider sniffs for it.
    graph_declares: str | None = None
    snippet: str = ""

    @property
    def message_id_header(self) -> str:
        return f"<{self.id}@mail.example>"

    @property
    def readable(self) -> str:
        """What a person would have read — the text if there is any, else the HTML."""
        return self.text or self.html

    def graph_body(self) -> tuple[str, str]:
        """``(contentType, content)`` exactly as Graph would report it."""
        content = self.html or self.text
        declared = self.graph_declares or ("html" if self.html else "text")
        return declared, content


def message(
    *,
    id: str,
    sender: str,
    subject: str,
    received_at: str,
    sender_name: str = "",
    text: str = "",
    html: str = "",
    reply_to: str = "",
    thread_id: str = "",
    references: str = "",
    categories: tuple[str, ...] = (),
    graph_declares: str | None = None,
    snippet: str = "",
) -> WireMessage:
    """Build a :class:`WireMessage`; ``received_at`` is an ISO-8601 UTC string."""
    return WireMessage(
        id=id,
        sender=sender,
        sender_name=sender_name,
        subject=subject,
        received_at=datetime.fromisoformat(received_at),
        text=text,
        html=html,
        reply_to=reply_to,
        thread_id=thread_id or f"thread-{id}",
        references=references,
        categories=list(categories),
        graph_declares=graph_declares,
        snippet=snippet or (text or subject)[:120],
    )


@dataclass
class SentMail:
    """A message that actually left the mailbox."""

    to: str
    subject: str
    body: str
    thread_id: str | None = None
    in_reply_to: str = ""
    references: str = ""

    @property
    def threaded(self) -> bool:
        """Whether the *recipient's* client will file this into the conversation.

        ``threadId`` groups it on the sender's side only. Gmail documents
        ``In-Reply-To``/``References`` as what makes threading apply, and it is
        the only thing any other mail client has to go on.
        """
        return bool(self.in_reply_to)


@dataclass
class DraftMail:
    """A draft sitting in the mailbox, waiting for a human to press Send."""

    to: str
    subject: str
    body: str
    thread_id: str | None = None


# --------------------------------------------------------------------------- #
# The token endpoint
# --------------------------------------------------------------------------- #
def _b64url(raw: bytes) -> str:
    """Base64url with the padding stripped, which is what both providers send."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64url(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class _AuthServer:
    """The provider's OAuth token endpoint, with real token state.

    Access tokens are minted, and only the most recent one is accepted: that is
    what turns "the product persisted the refreshed token" from an assertion
    about a database row into an assertion about whether the next sync works.
    """

    def __init__(self, *, account_email: str, rotates_refresh_token: bool) -> None:
        self.account_email = account_email
        self.rotates_refresh_token = rotates_refresh_token
        self._codes: dict[str, str] = {}
        self.access_tokens: set[str] = set()
        self.refresh_token = ""
        self.minted_access_tokens: list[str] = []
        self.minted_refresh_tokens: list[str] = []
        self.refresh_calls = 0
        self.refresh_revoked = False
        self.refresh_unavailable = False

    # -- test-facing controls ------------------------------------------------
    def authorize(self) -> str:
        """Grant consent the way a browser would, and return the ``code``."""
        code = f"auth-code-{len(self._codes) + 1}"
        self._codes[code] = self.account_email
        return code

    def expire_access_tokens(self) -> None:
        """Every issued access token stops working — the ordinary hourly event."""
        self.access_tokens.clear()

    def revoke_refresh_token(self) -> None:
        """The customer removed the app, or Google's seven-day test clock fired."""
        self.refresh_revoked = True
        self.access_tokens.clear()

    def break_the_token_endpoint(self) -> None:
        """The token endpoint is having a bad minute — a 5xx, not a refusal.

        Nothing is wrong with the mailbox, which is the whole point: this is the
        case that must *not* flag it or tell the customer to reconnect.
        """
        self.refresh_unavailable = True
        self.access_tokens.clear()

    # -- the endpoint --------------------------------------------------------
    def token(self, form: dict) -> tuple[int, dict | None]:
        if not form.get("client_id") or not form.get("client_secret"):
            return 401, {"error": "invalid_client", "error_description": "no credentials"}
        grant = form.get("grant_type")
        if grant == "authorization_code":
            return self._authorization_code(form)
        if grant == "refresh_token":
            return self._refresh(form)
        return 400, {"error": "unsupported_grant_type", "error_description": str(grant)}

    def _authorization_code(self, form: dict) -> tuple[int, dict | None]:
        account = self._codes.pop(str(form.get("code")), None)
        if account is None:
            return 400, {"error": "invalid_grant", "error_description": "code already redeemed"}
        self.refresh_token = self._mint_refresh()
        return 200, {
            "access_token": self._mint_access(),
            "refresh_token": self.refresh_token,
            "expires_in": 3599,
            "token_type": "Bearer",
            "id_token": self._id_token(account),
        }

    def _refresh(self, form: dict) -> tuple[int, dict | None]:
        self.refresh_calls += 1
        if self.refresh_unavailable:
            return 503, {"error": "temporarily_unavailable"}
        if self.refresh_revoked:
            return 400, {
                "error": "invalid_grant",
                "error_description": "Token has been expired or revoked.",
            }
        if form.get("refresh_token") != self.refresh_token:
            # Reached when the product kept a rotated-away refresh token.
            return 400, {
                "error": "invalid_grant",
                "error_description": "This refresh token has been superseded.",
            }
        self.access_tokens.clear()
        body = {
            "access_token": self._mint_access(),
            "expires_in": 3599,
            "token_type": "Bearer",
        }
        if self.rotates_refresh_token:
            self.refresh_token = self._mint_refresh()
            body["refresh_token"] = self.refresh_token
        return 200, body

    def _mint_access(self) -> str:
        token = f"access-{len(self.minted_access_tokens) + 1}"
        self.minted_access_tokens.append(token)
        self.access_tokens.add(token)
        return token

    def _mint_refresh(self) -> str:
        token = f"refresh-{len(self.minted_refresh_tokens) + 1}"
        self.minted_refresh_tokens.append(token)
        return token

    def _id_token(self, account: str) -> str:
        """An unsigned id_token carrying the ``email`` claim.

        The product reads this claim without verifying the signature (it came
        from the token endpoint over TLS) purely to label the connection, and
        the label matters: ``account_email`` is what decides whether a sender
        is internal or external, which changes how the mail is routed.
        """
        header = _b64url(b'{"alg":"none","typ":"JWT"}')
        payload = _b64url(f'{{"email":"{account}","email_verified":true}}'.encode())
        return f"{header}.{payload}.signature"


# --------------------------------------------------------------------------- #
# Shared plumbing
# --------------------------------------------------------------------------- #
def _bearer(headers: dict) -> str:
    value = str(headers.get("Authorization") or headers.get("authorization") or "")
    return value[len("Bearer ") :] if value.startswith("Bearer ") else ""


def _split_path(url: str, base: str) -> tuple[list[str], dict[str, list[str]]]:
    """``(path segments, query params)`` for a URL under ``base``.

    Segments stay percent-encoded until they are unquoted individually, so an
    id containing ``/`` cannot invent a route.
    """
    remainder = url[len(base) :] if url.startswith(base) else url
    path, _, query = remainder.partition("?")
    return [seg for seg in path.split("/") if seg], parse_qs(query)


def _mime_text(parsed) -> str:
    if parsed.is_multipart():
        for part in parsed.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", "replace")
        return ""
    payload = parsed.get_payload(decode=True) or b""
    return payload.decode(parsed.get_content_charset() or "utf-8", "replace")


def _header(parsed, name: str) -> str:
    """A header, RFC 2047-decoded.

    ``Subject: Re: =?utf-8?q?Pr=C3=A9sentation?=`` is what a correctly built
    non-ASCII subject looks like on the wire; a test should be able to assert
    on the words the recipient sees.
    """
    raw = parsed.get(name)
    return str(make_header(decode_header(raw))) if raw else ""


class _Wire:
    """Common request bookkeeping for both provider APIs."""

    api_base = ""
    token_url = ""
    rotates_refresh_token = False

    def __init__(self, messages: list[WireMessage], *, account_email: str) -> None:
        # Deep-copied so two wires can be handed the same content and then
        # diverge as each is written to.
        self.messages: dict[str, WireMessage] = {m.id: copy.deepcopy(m) for m in messages}
        self.sent: list[SentMail] = []
        self.drafts: list[DraftMail] = []
        self.calls: list[tuple[str, str]] = []
        self.auth = _AuthServer(
            account_email=account_email,
            rotates_refresh_token=self.rotates_refresh_token,
        )

    # -- test-facing helpers -------------------------------------------------
    def deliver(self, msg: WireMessage) -> None:
        """New mail arrives. The next fetch is the only way to learn about it."""
        self.messages[msg.id] = copy.deepcopy(msg)

    def authorize(self) -> str:
        return self.auth.authorize()

    def expire_access_tokens(self) -> None:
        self.auth.expire_access_tokens()

    def revoke_refresh_token(self) -> None:
        self.auth.revoke_refresh_token()

    def break_the_token_endpoint(self) -> None:
        self.auth.break_the_token_endpoint()

    @property
    def refresh_calls(self) -> int:
        return self.auth.refresh_calls

    def call_count(self, method: str, needle: str) -> int:
        return sum(1 for m, url in self.calls if m == method and needle in url)

    def in_inbox(self) -> list[str]:
        raise NotImplementedError

    # -- routing -------------------------------------------------------------
    def handles(self, url: str) -> bool:
        return url.startswith(self.api_base)

    def handles_token(self, url: str) -> bool:
        return url == self.token_url

    def token(self, form: dict) -> tuple[int, dict | None]:
        return self.auth.token(form)

    def api(self, method: str, url: str, headers: dict, body: dict | None):
        self.calls.append((method, url))
        if _bearer(headers) not in self.auth.access_tokens:
            return self.unauthenticated()
        segments, params = _split_path(url, self.api_base)
        return self.route(method, segments, params, body or {})

    def unauthenticated(self):
        raise NotImplementedError

    def route(self, method: str, segments: list[str], params: dict, body: dict):
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Gmail
# --------------------------------------------------------------------------- #
class GoogleWire(_Wire):
    """The Gmail API, statefully.

    Gmail does not rotate refresh tokens, so the product keeps the one it has —
    the opposite of Microsoft, and both branches of the refresher are real.
    """

    api_base = GMAIL_BASE
    token_url = GOOGLE_TOKEN_URL
    rotates_refresh_token = False

    _SYSTEM_LABELS = ("INBOX", "SENT", "DRAFT", "UNREAD", "STARRED")

    def __init__(self, messages: list[WireMessage], *, account_email: str) -> None:
        super().__init__(messages, account_email=account_email)
        self.labels: dict[str, str] = {name: name for name in self._SYSTEM_LABELS}
        self.created_labels: list[str] = []
        self._sent_count = 0
        self._draft_count = 0

    # -- state a test reads --------------------------------------------------
    def label_names(self, message_id: str) -> list[str]:
        """The label *names* on a message, resolved from the ids it carries."""
        by_id = {lid: name for name, lid in self.labels.items()}
        return [by_id.get(lid, lid) for lid in self.messages[message_id].labels]

    def in_inbox(self) -> list[str]:
        return [m.id for m in self.messages.values() if "INBOX" in m.labels]

    def unauthenticated(self):
        return 401, {
            "error": {
                "code": 401,
                "message": "Invalid Credentials",
                "status": "UNAUTHENTICATED",
            }
        }

    # -- routing -------------------------------------------------------------
    def route(self, method: str, segments: list[str], params: dict, body: dict):
        if segments[:1] == ["messages"]:
            rest = segments[1:]
            if method == "GET" and not rest:
                return self._list(params)
            if method == "POST" and rest == ["send"]:
                return self._send(body)
            if method == "POST" and rest == ["batchModify"]:
                return self._batch_modify(body)
            if method == "GET" and len(rest) == 1:
                return self._get(unquote(rest[0]), params)
            if method == "POST" and len(rest) == 2 and rest[1] == "modify":
                return self._modify(unquote(rest[0]), body)
        if segments == ["labels"]:
            if method == "GET":
                return self._list_labels()
            if method == "POST":
                return self._create_label(body)
        if segments == ["drafts"] and method == "POST":
            return self._create_draft(body)
        return self._error(404, "Requested entity was not found.", "NOT_FOUND")

    @staticmethod
    def _error(status: int, message: str, code: str):
        return status, {"error": {"code": status, "message": message, "status": code}}

    # -- read ----------------------------------------------------------------
    def _list(self, params: dict):
        """``users.messages.list`` — ids only, newest first.

        Returning nothing but ids and thread ids is the whole reason a Gmail
        sync is N+1 calls, and it is why the ``format=full`` fetch below is on
        the critical path of every first sync.
        """
        label = unquote((params.get("labelIds") or ["INBOX"])[0])
        limit = int((params.get("maxResults") or [GMAIL_DEFAULT_MAX_RESULTS])[0])
        matching = [m for m in self.messages.values() if label in m.labels]
        matching.sort(key=lambda m: m.received_at, reverse=True)
        page = matching[:limit]
        return 200, {
            "messages": [{"id": m.id, "threadId": m.thread_id} for m in page],
            "resultSizeEstimate": len(matching),
        }

    def _get(self, message_id: str, params: dict):
        msg = self.messages.get(message_id)
        if msg is None:
            return self._error(404, "Requested entity was not found.", "NOT_FOUND")
        fmt = (params.get("format") or ["full"])[0]
        if fmt not in ("full", "metadata", "minimal", "raw"):
            return self._error(400, f"Invalid format: {fmt}", "INVALID_ARGUMENT")
        return 200, {
            "id": msg.id,
            "threadId": msg.thread_id,
            "labelIds": list(msg.labels),
            # Epoch milliseconds, as a string. Stored raw it renders as
            # '1758787200000' where the arrival time should be.
            "internalDate": str(int(msg.received_at.timestamp() * 1000)),
            "snippet": msg.snippet,
            "payload": self._payload(msg, with_body=fmt == "full"),
        }

    def _payload(self, msg: WireMessage, *, with_body: bool) -> dict:
        """The MIME tree, in Gmail's shape.

        ``format=metadata`` carries headers and no body data at all — which is
        the correct, cheap read for a reply's addressing, and useless for
        anything that needs the text.
        """
        headers = [
            {"name": "Date", "value": msg.received_at.strftime("%a, %d %b %Y %H:%M:%S %z")},
            {"name": "From", "value": self._from_header(msg)},
            {"name": "To", "value": self.auth.account_email},
            {"name": "Subject", "value": msg.subject},
            {"name": "Message-ID", "value": msg.message_id_header},
            {"name": "References", "value": msg.references},
        ]
        if msg.reply_to:
            headers.append({"name": "Reply-To", "value": msg.reply_to})
        if not with_body:
            return {"mimeType": "multipart/alternative", "headers": headers}
        if msg.text and msg.html:
            return {
                "mimeType": "multipart/alternative",
                "headers": headers,
                "body": {"size": 0},
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": _b64url(msg.text.encode())}},
                    {"mimeType": "text/html", "body": {"data": _b64url(msg.html.encode())}},
                ],
            }
        if msg.html:
            # HTML with no plain-text alternative, and no part structure at all:
            # a single top-level text/html body. A large share of real mail.
            return {
                "mimeType": "text/html",
                "headers": headers,
                "body": {"data": _b64url(msg.html.encode())},
            }
        return {
            "mimeType": "text/plain",
            "headers": headers,
            "body": {"data": _b64url(msg.text.encode())},
        }

    @staticmethod
    def _from_header(msg: WireMessage) -> str:
        return f"{msg.sender_name} <{msg.sender}>" if msg.sender_name else msg.sender

    # -- write ---------------------------------------------------------------
    def _send(self, body: dict):
        raw = body.get("raw")
        if not raw:
            return self._error(400, "Missing 'raw' in the message resource.", "INVALID_ARGUMENT")
        try:
            parsed = message_from_bytes(_unb64url(str(raw)))
        except Exception:  # noqa: BLE001 - a bad body is a 400, not a crash
            return self._error(400, "'raw' is not base64url MIME.", "INVALID_ARGUMENT")
        recipient = _header(parsed, "To")
        if not recipient.strip():
            return self._error(400, "Recipient address required", "INVALID_ARGUMENT")
        thread_id = body.get("threadId")
        if thread_id and not any(m.thread_id == thread_id for m in self.messages.values()):
            return self._error(404, "Requested entity was not found.", "NOT_FOUND")
        self._sent_count += 1
        self.sent.append(
            SentMail(
                to=recipient,
                subject=_header(parsed, "Subject"),
                body=_mime_text(parsed),
                thread_id=thread_id,
                in_reply_to=_header(parsed, "In-Reply-To"),
                references=_header(parsed, "References"),
            )
        )
        return 200, {
            "id": f"sent-{self._sent_count}",
            "threadId": thread_id or f"thread-sent-{self._sent_count}",
            "labelIds": ["SENT"],
        }

    def _create_draft(self, body: dict):
        raw = ((body.get("message") or {}).get("raw")) or ""
        try:
            parsed = message_from_bytes(_unb64url(str(raw)))
        except Exception:  # noqa: BLE001
            return self._error(400, "'raw' is not base64url MIME.", "INVALID_ARGUMENT")
        recipient = _header(parsed, "To")
        if not recipient.strip():
            return self._error(400, "Recipient address required", "INVALID_ARGUMENT")
        self._draft_count += 1
        self.drafts.append(
            DraftMail(
                to=recipient,
                subject=_header(parsed, "Subject"),
                body=_mime_text(parsed),
                thread_id=(body.get("message") or {}).get("threadId"),
            )
        )
        return 200, {
            "id": f"draft-{self._draft_count}",
            "message": {"id": f"draft-message-{self._draft_count}", "labelIds": ["DRAFT"]},
        }

    def _modify(self, message_id: str, body: dict):
        msg = self.messages.get(message_id)
        if msg is None:
            return self._error(404, "Requested entity was not found.", "NOT_FOUND")
        known = set(self.labels.values())
        for label_id in body.get("addLabelIds") or []:
            if label_id not in known:
                # Gmail will not invent a label for you. The provider has to
                # create it first, which is what _ensure_label is for.
                return self._error(400, f"Invalid label: {label_id}", "INVALID_ARGUMENT")
            if label_id not in msg.labels:
                msg.labels.append(label_id)
        for label_id in body.get("removeLabelIds") or []:
            if label_id in msg.labels:
                msg.labels.remove(label_id)
        return 200, {"id": msg.id, "threadId": msg.thread_id, "labelIds": list(msg.labels)}

    def _batch_modify(self, body: dict):
        ids = body.get("ids") or []
        if len(ids) > 1000:
            return self._error(400, "Too many message ids (max 1000).", "INVALID_ARGUMENT")
        known = set(self.labels.values())
        for label_id in body.get("addLabelIds") or []:
            if label_id not in known:
                return self._error(400, f"Invalid label: {label_id}", "INVALID_ARGUMENT")
        for message_id in ids:
            msg = self.messages.get(str(message_id))
            if msg is None:
                return self._error(404, "Requested entity was not found.", "NOT_FOUND")
            for label_id in body.get("addLabelIds") or []:
                if label_id not in msg.labels:
                    msg.labels.append(label_id)
            for label_id in body.get("removeLabelIds") or []:
                if label_id in msg.labels:
                    msg.labels.remove(label_id)
        # batchModify answers 204 with no body at all, so the transport's
        # "response that isn't JSON" path is on the happy route here.
        return 204, None

    def _list_labels(self):
        return 200, {
            "labels": [
                {
                    "id": lid,
                    "name": name,
                    "type": "system" if name in self._SYSTEM_LABELS else "user",
                }
                for name, lid in self.labels.items()
            ]
        }

    def _create_label(self, body: dict):
        name = str(body.get("name") or "").strip()
        if not name:
            return self._error(400, "Label name is required.", "INVALID_ARGUMENT")
        if name in self.labels:
            # Gmail refuses a duplicate rather than returning the existing one.
            return self._error(409, "Label name exists or conflicts", "ALREADY_EXISTS")
        label_id = f"Label_{len(self.created_labels) + 1}"
        self.labels[name] = label_id
        self.created_labels.append(name)
        return 200, {"id": label_id, "name": name, "type": "user"}


# --------------------------------------------------------------------------- #
# Microsoft Graph
# --------------------------------------------------------------------------- #
class MicrosoftWire(_Wire):
    """Microsoft Graph, statefully.

    Entra rotates the refresh token on every use, so the product must persist
    the new one: the second sync is the assertion.
    """

    api_base = GRAPH_BASE
    rotates_refresh_token = True

    def __init__(
        self, messages: list[WireMessage], *, account_email: str, tenant: str = "common"
    ) -> None:
        super().__init__(messages, account_email=account_email)
        self.token_url = f"{MICROSOFT_TOKEN_HOST}/{tenant}/oauth2/v2.0/token"
        # Every PATCH of the categories collection, in order, so a test can
        # prove a no-op filing sent no write at all.
        self.category_patches: list[tuple[str, list[str]]] = []
        self.archived: list[str] = []
        self._forward_count = 0

    def in_inbox(self) -> list[str]:
        return [m.id for m in self.messages.values() if m.id not in self.archived]

    def unauthenticated(self):
        return 401, {
            "error": {
                "code": "InvalidAuthenticationToken",
                "message": "Access token has expired or is not yet valid.",
            }
        }

    @staticmethod
    def _error(status: int, code: str, message: str):
        return status, {"error": {"code": code, "message": message}}

    # -- routing -------------------------------------------------------------
    def route(self, method: str, segments: list[str], params: dict, body: dict):
        if method == "GET" and segments[:1] == ["mailFolders"] and segments[2:] == ["messages"]:
            return self._list(unquote(segments[1]), params)
        if segments[:1] == ["messages"] and len(segments) >= 2:
            message_id = unquote(segments[1])
            tail = segments[2:]
            if method == "GET" and not tail:
                return self._get(message_id, params)
            if method == "PATCH" and not tail:
                return self._patch(message_id, body)
            if method == "POST" and tail == ["reply"]:
                return self._reply(message_id, body)
            if method == "POST" and tail == ["createForward"]:
                return self._create_forward(message_id, body)
            if method == "POST" and tail == ["move"]:
                return self._move(message_id, body)
        return self._error(404, "ResourceNotFound", "Resource could not be discovered.")

    # -- read ----------------------------------------------------------------
    def _list(self, folder: str, params: dict):
        """``mailFolders/{id}/messages`` — full resources, in the asked-for order.

        Graph publishes no default sort here, so this deliberately answers an
        ``$orderby``-less request with the *oldest* mail first. A mailbox is
        capped per sweep, so a provider that does not ask for newest-first
        syncs someone's 2019 and never reaches the message they are waiting on.
        """
        if folder.lower() not in ("inbox", "archive"):
            return self._error(404, "ErrorItemNotFound", f"Unknown folder: {folder}")
        live = [m for m in self.messages.values() if m.id not in self.archived]
        order = (params.get("$orderby") or [""])[0].lower()
        newest_first = "receiveddatetime desc" in order
        live.sort(key=lambda m: m.received_at, reverse=newest_first)
        top = int((params.get("$top") or [GRAPH_DEFAULT_TOP])[0])
        return 200, {
            "@odata.context": f"{GRAPH_BASE}/messages",
            "value": [self._resource(m) for m in live[:top]],
        }

    def _get(self, message_id: str, params: dict):
        msg = self.messages.get(message_id)
        if msg is None or msg.id in self.archived:
            return self._error(404, "ErrorItemNotFound", "The specified object was not found.")
        select = [f for f in (params.get("$select") or [""])[0].split(",") if f]
        resource = self._resource(msg)
        # $select really does narrow the response: a provider that asks for two
        # fields and then reads a third gets None, not the value.
        return 200, {k: v for k, v in resource.items() if not select or k in select}

    def _resource(self, msg: WireMessage) -> dict:
        content_type, content = msg.graph_body()
        return {
            "id": msg.id,
            "conversationId": msg.thread_id,
            "subject": msg.subject,
            "receivedDateTime": msg.received_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "bodyPreview": msg.snippet,
            "body": {"contentType": content_type, "content": content},
            "from": {"emailAddress": {"name": msg.sender_name, "address": msg.sender}},
            "replyTo": (
                [{"emailAddress": {"name": "", "address": msg.reply_to}}] if msg.reply_to else []
            ),
            "categories": list(msg.categories),
            "isRead": False,
        }

    # -- write ---------------------------------------------------------------
    def _patch(self, message_id: str, body: dict):
        msg = self.messages.get(message_id)
        if msg is None:
            return self._error(404, "ErrorItemNotFound", "The specified object was not found.")
        if "categories" in body:
            # Destructive, exactly as Graph is: a collection PATCH replaces the
            # collection. Sending only our own category deleted every category
            # the person had filed the message under.
            msg.categories = [str(c) for c in (body.get("categories") or [])]
            self.category_patches.append((message_id, list(msg.categories)))
        return 200, self._resource(msg)

    def _reply(self, message_id: str, body: dict):
        msg = self.messages.get(message_id)
        if msg is None:
            return self._error(404, "ErrorItemNotFound", "The specified object was not found.")
        # Graph addresses and threads the reply itself, from the original —
        # including Reply-To, which is why this provider never has to read it.
        subject = msg.subject if msg.subject.lower().startswith("re:") else f"Re: {msg.subject}"
        self.sent.append(
            SentMail(
                to=msg.reply_to or msg.sender,
                subject=subject,
                body=str(body.get("comment") or ""),
                thread_id=msg.thread_id,
                in_reply_to=msg.message_id_header,
                references=msg.message_id_header,
            )
        )
        # 202 Accepted, with no body — the transport gets a response it cannot
        # parse as JSON and must treat as success.
        return 202, None

    def _create_forward(self, message_id: str, body: dict):
        msg = self.messages.get(message_id)
        if msg is None:
            return self._error(404, "ErrorItemNotFound", "The specified object was not found.")
        recipients = [
            str(((r or {}).get("emailAddress") or {}).get("address") or "")
            for r in (body.get("toRecipients") or [])
        ]
        recipients = [r for r in recipients if r.strip()]
        if not recipients:
            return self._error(400, "ErrorInvalidRecipients", "toRecipients is required.")
        self._forward_count += 1
        self.drafts.append(
            DraftMail(
                to=", ".join(recipients),
                subject=f"FW: {msg.subject}",
                # Graph attaches the original itself; the comment is the note.
                body=f"{body.get('comment') or ''}\n\n{msg.readable}",
                # A forward starts its own conversation, so the hand-off is not
                # sitting in the customer's thread one stray Send from going out.
                thread_id=None,
            )
        )
        return 201, {
            "id": f"AAMkforward-{self._forward_count}",
            "subject": f"FW: {msg.subject}",
            "isDraft": True,
            "toRecipients": body.get("toRecipients"),
        }

    def _move(self, message_id: str, body: dict):
        msg = self.messages.get(message_id)
        if msg is None:
            return self._error(404, "ErrorItemNotFound", "The specified object was not found.")
        destination = str(body.get("destinationId") or "")
        if destination not in ("archive", "inbox", "deleteditems", "junkemail"):
            return self._error(400, "ErrorInvalidIdMalformed", f"Bad folder: {destination}")
        if destination != "inbox":
            self.archived.append(msg.id)
        # A moved message gets a NEW id in Graph. Anything holding the old one
        # is holding a 404.
        moved_id = f"{msg.id}-moved"
        return 201, {"id": moved_id, "parentFolderId": destination, "isDraft": False}


# --------------------------------------------------------------------------- #
# Installation
# --------------------------------------------------------------------------- #
def install(monkeypatch, *wires: _Wire) -> list[tuple[str, str]]:
    """Point every provider HTTP call at ``wires``. Returns the unrouted log.

    The list is the safety net: a request no wire claimed is answered with a
    404 *and* recorded, and the fixtures assert it stayed empty. Without that,
    a provider calling a URL the fake does not know about would read as a
    mailbox that is simply empty — green, and wrong.
    """
    import httpx

    unrouted: list[tuple[str, str]] = []

    def _response(status: int, body: dict | None) -> httpx.Response:
        # No body means no body: the provider's own transport has to survive a
        # 202/204 whose payload cannot be parsed as JSON, and it does.
        return httpx.Response(status) if body is None else httpx.Response(status, json=body)

    def _request(method: str, url: str, *, headers=None, json=None, timeout=None, **_kwargs):
        for wire in wires:
            if wire.handles(url):
                return _response(*wire.api(method, url, dict(headers or {}), json))
        unrouted.append((method, url))
        return _response(404, {"error": {"message": "no fake wire serves this URL"}})

    def _post(url: str, *, data=None, timeout=None, **_kwargs):
        for wire in wires:
            if wire.handles_token(url):
                return _response(*wire.token(dict(data or {})))
        unrouted.append(("POST", url))
        return _response(404, {"error": "no fake token endpoint serves this URL"})

    monkeypatch.setattr(httpx, "request", _request)
    monkeypatch.setattr(httpx, "post", _post)
    return unrouted
