"""Structural accessibility, asserted across every page the product serves.

Phase 3 did this work for the inbox and approvals and stopped there, which is
the normal way an accessibility pass decays: the pages someone was looking at
get fixed, the rest drift, and nothing notices because nothing checks.

So this is a *sweep*, not a sample. It renders every public and signed-in page
and asserts the structural invariants that a screen reader, a keyboard and a
320px viewport all depend on:

- exactly one ``<h1>``, so the page announces what it is
- a ``<main>`` landmark, so "skip to content" has somewhere to go
- every form control has an accessible name — a ``<label for>``, a wrapping
  label, ``aria-label`` or ``aria-labelledby``
- no positive ``tabindex``, which overrides document order and breaks the tab
  sequence for the whole page, not just the element carrying it
- every ``<table>`` has a caption and column headers with ``scope``
- every ``<img>`` has ``alt`` (empty is fine and correct for decoration)
- buttons and links have discernible text

Parsed with the stdlib ``html.parser`` on purpose: this runs in CI on every
push, and an accessibility gate that needs a new dependency is a gate someone
eventually deletes.
"""

from __future__ import annotations

import re
import uuid
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from app.core.db import migrate_db
from app.main import app

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')

# Controls that are not user-facing: a hidden input has nothing to announce,
# and a submit button's own text is its name.
_UNLABELLED_OK = {"hidden", "submit", "button", "reset", "image"}
_VOID = {"input", "img", "br", "hr", "meta", "link", "source", "col"}


class Page(HTMLParser):
    """Just enough of a DOM to ask structural questions of a rendered page."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.h1s: list[str] = []
        self.landmarks: list[str] = []
        self.controls: list[dict] = []
        self.label_targets: set[str] = set()
        self.labels_wrapping: int = 0
        self.positive_tabindex: list[str] = []
        self.images: list[dict] = []
        self.tables: list[dict] = []
        self.links: list[dict] = []
        self.buttons: list[dict] = []
        self.lang: str | None = None
        self._stack: list[str] = []
        self._text_into: list[list[str]] = []
        self._open_label: dict | None = None
        self._table: dict | None = None
        self.feed(html)

    # -- helpers ------------------------------------------------------------
    def _collect_text(self, sink: list[str]) -> None:
        self._text_into.append(sink)

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        for sink in self._text_into:
            sink.append(text)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_starttag(self, tag, attrs):
        attr = {k: (v or "") for k, v in attrs}
        if tag not in _VOID:
            self._stack.append(tag)

        if tag == "html":
            self.lang = attr.get("lang")
        elif tag == "h1":
            sink: list[str] = []
            self.h1s.append("")
            self._collect_text(sink)
            self._h1_sink = sink
        elif tag in ("main", "nav", "header", "footer"):
            self.landmarks.append(tag)
        elif tag in ("input", "select", "textarea"):
            control = {
                "tag": tag,
                "type": attr.get("type", "text").lower(),
                "id": attr.get("id"),
                "name": attr.get("name"),
                "aria_label": attr.get("aria-label"),
                "aria_labelledby": attr.get("aria-labelledby"),
                "title": attr.get("title"),
                "inside_label": self._open_label is not None,
                # A spam honeypot is deliberately not for people. aria-hidden
                # plus tabindex=-1 is the correct way to say so, and demanding
                # a label on top of that would mean announcing a trap field to
                # exactly the users who cannot see it is a trap.
                "hidden": attr.get("aria-hidden") == "true",
            }
            self.controls.append(control)
            if self._open_label is not None:
                self._open_label["wrapped"] = True
        elif tag == "label":
            self._open_label = {"for": attr.get("for"), "wrapped": False}
            if attr.get("for"):
                self.label_targets.add(attr["for"])
        elif tag == "img":
            self.images.append({"alt": attr.get("alt"), "src": attr.get("src", "")})
        elif tag == "table":
            self._table = {"caption": False, "headers": [], "aria_label": attr.get("aria-label")}
        elif tag == "caption" and self._table is not None:
            self._table["caption"] = True
        elif tag == "th" and self._table is not None:
            self._table["headers"].append(attr.get("scope"))
        elif tag == "a":
            sink = []
            self.links.append({"href": attr.get("href", ""), "text": sink, "attrs": attr})
            self._collect_text(sink)
        elif tag == "button":
            sink = []
            self.buttons.append({"text": sink, "attrs": attr})
            self._collect_text(sink)

        raw_tabindex = attr.get("tabindex")
        if raw_tabindex and raw_tabindex.lstrip("+").isdigit() and int(raw_tabindex) > 0:
            self.positive_tabindex.append(tag)

    def handle_endtag(self, tag):
        if tag == "h1" and self.h1s:
            self.h1s[-1] = " ".join(self._h1_sink)
            self._text_into.pop()
        elif tag == "label":
            self._open_label = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None
        elif tag in ("a", "button") and self._text_into:
            self._text_into.pop()
        if tag in self._stack:
            while self._stack and self._stack.pop() != tag:
                pass

    # -- questions ----------------------------------------------------------
    def unlabelled_controls(self) -> list[dict]:
        out = []
        for control in self.controls:
            if control["type"] in _UNLABELLED_OK or control["hidden"]:
                continue
            named = (
                (control["id"] and control["id"] in self.label_targets)
                or control["aria_label"]
                or control["aria_labelledby"]
                or control["inside_label"]
            )
            if not named:
                out.append(control)
        return out

    def empty_links(self) -> list[dict]:
        return [
            link
            for link in self.links
            if not " ".join(link["text"]).strip()
            and not link["attrs"].get("aria-label")
            and not link["attrs"].get("title")
        ]

    def empty_buttons(self) -> list[dict]:
        return [
            button
            for button in self.buttons
            if not " ".join(button["text"]).strip()
            and not button["attrs"].get("aria-label")
            and not button["attrs"].get("title")
        ]


# --------------------------------------------------------------------------- #
# Fixtures: one client per surface, so every page is rendered for real
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _schema():
    migrate_db()


@pytest.fixture(scope="module")
def anon() -> TestClient:
    return TestClient(app, follow_redirects=False)


@pytest.fixture(scope="module")
def member() -> TestClient:
    """A signed-in workspace with the demo mailbox connected and synced."""
    migrate_db()
    client = TestClient(app, follow_redirects=False)
    email = f"a11y_{uuid.uuid4().hex[:10]}@northwind.example"
    page = client.get("/signup").text
    assert (
        client.post(
            "/signup",
            data={
                "csrf_token": CSRF_RE.search(page).group(1),
                "org_name": "Northwind Industries",
                "full_name": "Alex Chen",
                "email": email,
                "password": "a-strong-password",
            },
        ).status_code
        == 303
    )
    page = client.get("/app/connect").text
    assert (
        client.post(
            "/app/connect/demo", data={"csrf_token": CSRF_RE.search(page).group(1)}
        ).status_code
        == 303
    )
    return client


PUBLIC_PAGES = [
    "/",
    "/login",
    "/signup",
    "/forgot-password",
    "/privacy",
    "/terms",
    "/contact-sales",
]
APP_PAGES = [
    "/app/inbox",
    "/app/approvals",
    "/app/waiting",
    "/app/activity",
    "/app/settings",
    "/app/connect",
]


def _page(client: TestClient, path: str) -> Page:
    response = client.get(path)
    assert response.status_code == 200, f"{path} returned {response.status_code}"
    return Page(response.text)


@pytest.fixture(scope="module")
def public_pages(anon) -> dict[str, Page]:
    return {path: _page(anon, path) for path in PUBLIC_PAGES}


@pytest.fixture(scope="module")
def app_pages(member) -> dict[str, Page]:
    return {path: _page(member, path) for path in APP_PAGES}


@pytest.fixture(scope="module")
def all_pages(public_pages, app_pages) -> dict[str, Page]:
    return {**public_pages, **app_pages}


# --------------------------------------------------------------------------- #
# The invariants
# --------------------------------------------------------------------------- #
def test_every_page_declares_its_language(all_pages):
    for path, page in all_pages.items():
        assert page.lang == "en", f"{path} has no lang on <html>"


def test_every_page_has_exactly_one_h1(all_pages):
    for path, page in all_pages.items():
        assert len(page.h1s) == 1, f"{path} has {len(page.h1s)} <h1> elements: {page.h1s}"
        assert page.h1s[0].strip(), f"{path} has an empty <h1>"


def test_every_page_has_a_main_landmark(all_pages):
    """A skip link needs somewhere to skip to, and "main" is how it is found."""
    for path, page in all_pages.items():
        assert "main" in page.landmarks, f"{path} has no <main>"


def test_every_form_control_has_an_accessible_name(all_pages):
    """An unlabelled field is announced as "edit text, blank" and nothing else."""
    for path, page in all_pages.items():
        missing = page.unlabelled_controls()
        assert not missing, f"{path} has unlabelled controls: {missing}"


def test_no_page_uses_a_positive_tabindex(all_pages):
    """One positive tabindex reorders the tab sequence for the entire page."""
    for path, page in all_pages.items():
        assert not page.positive_tabindex, (
            f"{path} uses positive tabindex on {page.positive_tabindex}"
        )


def test_every_image_has_an_alt_attribute(all_pages):
    """Empty alt is correct for decoration; a *missing* one reads out the filename."""
    for path, page in all_pages.items():
        missing = [img["src"] for img in page.images if img["alt"] is None]
        assert not missing, f"{path} has images with no alt: {missing}"


def test_every_table_is_navigable(all_pages):
    """Without scope, a screen reader cannot say which column a cell is in."""
    for path, page in all_pages.items():
        for index, table in enumerate(page.tables):
            assert table["caption"] or table["aria_label"], (
                f"{path} table #{index} has neither a <caption> nor an aria-label"
            )
            assert table["headers"], f"{path} table #{index} has no <th>"
            unscoped = [h for h in table["headers"] if not h]
            assert not unscoped, f"{path} table #{index} has {len(unscoped)} <th> without scope"


def test_every_link_and_button_says_something(all_pages):
    for path, page in all_pages.items():
        assert not page.empty_links(), f"{path} has links with no text: {page.empty_links()}"
        assert not page.empty_buttons(), f"{path} has buttons with no text: {page.empty_buttons()}"


def test_the_signed_in_shell_offers_a_skip_link(member):
    """Five sidebar links before the content, on every page, is the case for it."""
    html = member.get("/app/inbox").text
    assert 'class="skip-link"' in html
    assert 'href="#content"' in html
    assert 'id="content"' in html


def test_the_public_pages_offer_a_skip_link(anon):
    """The landing page's nav is the same barrier the app's sidebar is."""
    html = anon.get("/").text
    assert 'class="skip-link"' in html


def test_the_viewport_is_not_locked(all_pages, anon):
    """Blocking zoom fails WCAG 1.4.4 outright and is a common copy-paste."""
    html = anon.get("/").text
    assert "user-scalable=no" not in html
    assert "maximum-scale" not in html


# --------------------------------------------------------------------------- #
# The operator surface holds to the same bar
# --------------------------------------------------------------------------- #
@pytest.fixture
def operator(monkeypatch) -> TestClient:
    monkeypatch.setenv("OPERATOR_TOKEN", "a11y-operator-token")
    client = TestClient(app, follow_redirects=False)
    assert (
        client.post("/operator/session", data={"operator_token": "a11y-operator-token"}).status_code
        == 303
    )
    return client


@pytest.mark.parametrize("path", ["/operator", "/operator/session"])
def test_the_operator_pages_meet_the_same_bar(operator, path):
    page = _page(operator, path)
    assert page.lang == "en"
    assert len(page.h1s) == 1
    assert "main" in page.landmarks
    assert not page.unlabelled_controls()
    assert not page.positive_tabindex
    assert not page.empty_buttons()
    for table in page.tables:
        assert table["caption"] or table["aria_label"]
        assert table["headers"] and all(table["headers"])


# --------------------------------------------------------------------------- #
# Form errors have to reach someone who cannot see them
# --------------------------------------------------------------------------- #
class TestFormErrorsAreAnnounced:
    def test_a_failed_sign_in_focuses_the_reason(self, anon):
        """A full page reload means role="alert" alone never fires: the banner
        is present at load, so there is no mutation for a live region to
        announce. It has to take focus."""
        page = anon.get("/login").text
        csrf = CSRF_RE.search(page).group(1)

        html = anon.post(
            "/login",
            data={
                "csrf_token": csrf,
                "email": "nobody@example.test",
                "password": "wrong",
                "next": "",
            },
        ).text

        assert 'id="form-error"' in html
        assert "autofocus" in html
        assert 'tabindex="-1"' in html

    def test_the_fields_point_at_the_error(self, anon):
        """So returning to a field repeats why, instead of leaving it to memory."""
        page = anon.get("/login").text
        csrf = CSRF_RE.search(page).group(1)

        html = anon.post(
            "/login",
            data={
                "csrf_token": csrf,
                "email": "nobody@example.test",
                "password": "wrong",
                "next": "",
            },
        ).text

        assert html.count('aria-describedby="form-error"') >= 2
        assert 'aria-invalid="true"' in html

    def test_a_clean_form_carries_no_error_wiring(self, anon):
        """aria-invalid on a field nobody has touched yet is a lie."""
        html = anon.get("/login").text
        assert "aria-invalid" not in html
        assert 'id="form-error"' not in html
