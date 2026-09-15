"""Turn an HTML email body into the text a person would have read.

Both providers hand us HTML. Gmail sends ``text/html`` with no plain-text
alternative for a large share of real mail — notifications, newsletters, and
anything composed in a modern client. Microsoft Graph is worse: ``body.content``
is HTML *by default*, so without this every Outlook message arrives as markup.

Until this existed, that markup was what got stored, shown in the reader, fed to
the drafter as "the message", and checked by the verifier as "the source". The
demo mailbox is plain text end to end, which is why nothing caught it.

Stdlib only, on purpose. This runs inside a mailbox sync on a small instance,
and a dependency that parses untrusted HTML from strangers is a dependency whose
CVEs become this product's problem. ``html.parser`` is not a browser and does not
try to be: no CSS, no layout, no scripts. It recovers the words and the line
breaks, which is all any consumer downstream actually wants.
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser

# Content that is markup, not message. Anything inside these is dropped whole —
# a <style> block otherwise contributes its CSS to the "body" of the email.
_DROP_CONTENT = {"script", "style", "head", "title", "meta", "link", "noscript"}

# Elements that end a line. Email is mostly tables and divs, so the table row
# and cell tags matter as much as <p>.
_BLOCK = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}

# Elements after which a *blank* line reads correctly — a paragraph break rather
# than a line break.
_PARAGRAPH = {"p", "div", "blockquote", "table", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}

# NBSP and friends read as spaces but are not matched by `[ \t]`. HTML email is
# full of them — a `<div>&nbsp;</div>` spacer is how a table layout fakes
# vertical rhythm — and left alone they survive every whitespace rule below,
# leaving lines that look blank but are not.
_UNICODE_SPACES = str.maketrans(
    {
        " ": " ",  # no-break space (&nbsp;)
        " ": " ",  # en space
        " ": " ",  # em space
        " ": " ",  # figure space
        " ": " ",  # thin space
        " ": " ",  # hair space
        " ": " ",  # narrow no-break space
        "​": "",  # zero-width space
        "﻿": "",  # zero-width no-break space / BOM
    }
)

_WS_RUN = re.compile(r"[ \t\r\f\v]+")
_BLANK_RUN = re.compile(r"\n{3,}")
_TRAILING_WS = re.compile(r"[ \t]+\n")
_LEADING_WS = re.compile(r"\n[ \t]+")
_BLANK_ONLY_LINE = re.compile(r"\n[ \t]+\n")


class _Extractor(HTMLParser):
    """Collect the readable text of an HTML document, with its line structure."""

    def __init__(self) -> None:
        # convert_charrefs=True lets the parser decode entities for us, so
        # "&nbsp;" and "&#8217;" arrive as characters rather than as literal
        # text in the middle of a sentence.
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._suppress = 0
        self._pre = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _DROP_CONTENT:
            self._suppress += 1
            return
        if self._suppress:
            return
        if tag == "pre":
            self._pre += 1
            self._newline(blank=True)
        elif tag == "li":
            self._newline()
            self._parts.append("- ")
        elif tag == "br":
            self._newline()
        elif tag in _BLOCK:
            self._newline(blank=tag in _PARAGRAPH)

    def handle_startendtag(self, tag: str, attrs) -> None:
        # <br/> and <hr/> are self-closing; without this they are a start tag
        # whose matching end tag never arrives, and _suppress would never
        # unwind for a self-closed <meta/> or <link/>.
        if tag in _DROP_CONTENT:
            return
        if tag in ("br", "hr"):
            self._newline()

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_CONTENT:
            self._suppress = max(0, self._suppress - 1)
            return
        if self._suppress:
            return
        if tag == "pre":
            self._pre = max(0, self._pre - 1)
            self._newline(blank=True)
            return
        if tag in _BLOCK:
            self._newline(blank=tag in _PARAGRAPH)

    def handle_data(self, data: str) -> None:
        if self._suppress or not data:
            return
        if self._pre:
            # Inside <pre> the author's line breaks are content — a pasted log
            # or a stack trace means its own rows. Leading indentation does not
            # survive the tidy-up at the end of html_to_text; keeping it would
            # mean keeping every other stray indent in the document too.
            self._parts.append(data)
            return
        if not data.strip():
            # Whitespace between tags is source formatting, not message text.
            # Emitting the author's newline here is what put a blank line
            # between every list item: `</li>\n<li>` produced one break for the
            # closing tag and a second for the newline after it. A single space
            # keeps `<b>bold</b> <i>italic</i>` from running together.
            self._parts.append(" ")
            return
        self._parts.append(_WS_RUN.sub(" ", data))

    def _newline(self, blank: bool = False) -> None:
        """Append a break without stacking one on top of another.

        Markup nests: `</li></ul></div>` is three closing block tags in a row,
        and appending blindly turns one intended break into three newlines. The
        blank-run collapse at the end caps the damage at a blank line — still
        wrong between consecutive list items, which should sit on adjacent rows.
        """
        want = 2 if blank else 1
        have = 0
        for part in reversed(self._parts):
            if part and part.strip("\n") == "":
                have += part.count("\n")
                if have >= want:
                    return
                continue
            if part.strip() == "":
                continue  # whitespace-only text between tags
            break
        if have < want:
            self._parts.append("\n" * (want - have))

    def text(self) -> str:
        return "".join(self._parts)


def html_to_text(html: str) -> str:
    """Render ``html`` as readable plain text.

    Returns an empty string for empty input. Never raises: a malformed body is
    a normal thing to receive from a stranger, and the caller is a mailbox sync
    that must not fail because someone sent unbalanced tags.
    """
    if not html or not html.strip():
        return ""
    html = html.translate(_UNICODE_SPACES)
    parser = _Extractor()
    try:
        parser.feed(html)
        parser.close()
        text = parser.text()
    except Exception:  # noqa: BLE001 - see docstring; degrade, never raise
        # Last resort: strip tags with a regex and decode entities. Cruder, but
        # it still beats handing raw markup to a reader and to the model.
        text = unescape(re.sub(r"<[^>]+>", " ", html))

    text = text.translate(_UNICODE_SPACES)
    # A line of nothing but spaces is a spacer, not content. Collapse it before
    # the blank-run rule, or it reads as two paragraph breaks around nothing.
    while _BLANK_ONLY_LINE.search(text):
        text = _BLANK_ONLY_LINE.sub("\n\n", text)
    text = _TRAILING_WS.sub("\n", text)
    # Source indentation between tags becomes a space that lands *after* a
    # break, so every line arrives indented by however the HTML was formatted.
    # (This normalises indentation inside <pre> too — its line breaks survive,
    # its leading whitespace does not. An acceptable trade in a mail reader.)
    text = _LEADING_WS.sub("\n", text)
    text = _BLANK_RUN.sub("\n\n", text)
    return text.strip()


def looks_like_html(value: str) -> bool:
    """Whether ``value`` is HTML rather than text a person typed.

    Deliberately narrow. A plain-text email may well contain "<" (a quoted
    address, an arrow, a snippet of code), and running the tag stripper over
    genuine prose would silently eat it. Requires an actual element — a tag
    name in angle brackets — before treating the body as markup.
    """
    if not value:
        return False
    return (
        re.search(r"<(?:/\s*)?(?:html|body|div|p|br|table|span|a|img|h[1-6])\b[^>]*>", value, re.I)
        is not None
    )
