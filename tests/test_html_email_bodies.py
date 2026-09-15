"""Real mail is HTML, and it has to arrive as something a person can read.

Every fixture in the demo mailbox is plain text, which is why this went
unnoticed until a real account was about to be connected: both providers stored
the raw markup as the message body. That body is not a display detail — it is
what the reader shows, what the drafter is given as "the message", and what the
verifier checks a draft's claims against. A wall of `<td style="...">` poisons
all three.

The bar here is not fidelity to a browser. It is: the words survive, the line
breaks are where a reader expects them, and nothing in the pipeline ever sees a
tag.
"""

from __future__ import annotations

import base64

from app.copilot.providers.gmail import GmailProvider, _extract_body
from app.copilot.providers.graph import MicrosoftGraphProvider
from app.copilot.providers.html_text import html_to_text, looks_like_html


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


# A body shaped like real mail: table layout, inline CSS, a <style> block,
# entities, a tracking pixel, and a script.
REAL_WORLD_HTML = """<html><head>
<style type="text/css">.wrap{font-family:Arial} a:hover{color:#06c}</style>
</head><body style="margin:0">
<table width="100%" cellpadding="0"><tr><td>
<div style="font-size:14px">Hi Alex,</div>
<div>&nbsp;</div>
<p>The utility revised its estimate from <b>four hours</b> to <i>seven</i>,
which puts restore at roughly 14:40.</p>
<p>Actions:</p>
<ul><li>Fuel ordered &mdash; tanker confirmed for 11:00</li>
<li>Failover staged &amp; tested</li></ul>
<div>Thanks,<br>Marcus</div>
</td></tr></table>
<img src="https://track.example/p.gif" width="1" height="1">
<script>fetch('https://track.example/o')</script>
</body></html>"""


class TestTheConverter:
    def test_the_words_survive(self):
        text = html_to_text(REAL_WORLD_HTML)
        for phrase in ["Hi Alex,", "four hours", "seven", "14:40", "Thanks,", "Marcus"]:
            assert phrase in text

    def test_no_tag_reaches_the_output(self):
        text = html_to_text(REAL_WORLD_HTML)
        assert "<" not in text
        assert ">" not in text
        assert "style" not in text.lower()

    def test_script_and_style_content_is_dropped_whole(self):
        """A <style> block otherwise contributes its CSS to the 'body' text."""
        text = html_to_text(REAL_WORLD_HTML)
        assert "font-family" not in text
        assert "fetch(" not in text
        assert "track.example" not in text

    def test_entities_are_decoded(self):
        text = html_to_text(REAL_WORLD_HTML)
        assert "&mdash;" not in text and "&amp;" not in text and "&nbsp;" not in text
        assert "—" in text
        assert "staged & tested" in text

    def test_nbsp_spacers_do_not_leave_lines_that_look_blank(self):
        """`<div>&nbsp;</div>` is how a table layout fakes vertical rhythm.

        U+00A0 is not matched by `[ \\t]`, so it survives naive whitespace
        cleanup and litters the body with lines that render as empty but are not.
        """
        text = html_to_text(REAL_WORLD_HTML)
        assert " " not in text
        assert not [line for line in text.split("\n") if line and not line.strip()]

    def test_list_items_sit_on_adjacent_lines(self):
        """`</li></ul></div>` is three closing tags; a blind newline per tag
        puts a blank line between every bullet."""
        text = html_to_text(REAL_WORLD_HTML)
        assert "- Fuel ordered — tanker confirmed for 11:00\n- Failover staged & tested" in text

    def test_paragraphs_are_separated(self):
        text = html_to_text("<p>First para.</p><p>Second para.</p>")
        assert text == "First para.\n\nSecond para."

    def test_br_is_a_line_break_not_a_paragraph(self):
        assert html_to_text("<div>Line one<br>Line two</div>") == "Line one\nLine two"

    def test_self_closing_tags_do_not_swallow_the_body(self):
        """A self-closed <meta/> must not leave the drop-content counter stuck."""
        text = html_to_text('<meta charset="utf-8"/><div>Still here.</div><br/>')
        assert "Still here." in text

    def test_malformed_html_degrades_instead_of_raising(self):
        """Unbalanced tags are a normal thing to receive from a stranger."""
        assert html_to_text("<div><p>unbalanced <b>bold") == "unbalanced bold"

    def test_empty_and_whitespace_input(self):
        assert html_to_text("") == ""
        assert html_to_text("   \n  ") == ""
        assert html_to_text("<style>.a{color:red}</style>") == ""


class TestHtmlDetection:
    def test_markup_is_detected(self):
        assert looks_like_html("<div>hello</div>")
        assert looks_like_html("<P STYLE='x'>hello</P>")
        assert looks_like_html("text before <br/> and after")

    def test_plain_prose_containing_angle_brackets_is_not_markup(self):
        """Running the tag stripper over real prose would silently eat it."""
        assert not looks_like_html("5 < 6 and 7 > 2")
        assert not looks_like_html("Reply to Alex <alex@acme.example> directly")
        assert not looks_like_html("")


class TestGmailBodies:
    def test_plain_text_part_wins_when_present(self):
        """It is what the sender actually wrote, wrapped the way they wrote it."""
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("The plain version.")}},
                {"mimeType": "text/html", "body": {"data": _b64("<p>The HTML version.</p>")}},
            ],
        }
        assert _extract_body(payload) == "The plain version."

    def test_html_only_mail_is_rendered_to_text(self):
        """A large share of real mail has no plain-text alternative at all."""
        payload = {
            "mimeType": "text/html",
            "body": {"data": _b64("<div>Hi Alex,</div><p>Invoice <b>4821</b> is overdue.</p>")},
        }
        body = _extract_body(payload)
        assert "Invoice 4821 is overdue." in body
        assert "<" not in body

    def test_html_nested_in_multipart_related_is_found(self):
        """Inline images wrap the HTML part one level deeper."""
        payload = {
            "mimeType": "multipart/related",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/html", "body": {"data": _b64("<p>Nested body.</p>")}}
                    ],
                },
                {"mimeType": "image/png", "body": {"attachmentId": "abc"}},
            ],
        }
        assert _extract_body(payload) == "Nested body."

    def test_an_undeclared_html_body_is_still_rendered(self):
        """Some senders put HTML in a part with no useful mimeType."""
        payload = {"mimeType": "", "body": {"data": _b64("<div>Undeclared markup.</div>")}}
        assert _extract_body(payload) == "Undeclared markup."

    def test_an_undeclared_plain_body_is_left_alone(self):
        payload = {"mimeType": "", "body": {"data": _b64("Just text, 5 < 6.")}}
        assert _extract_body(payload) == "Just text, 5 < 6."

    def test_a_fetched_html_message_carries_readable_text(self):
        """End to end through the provider, not just the helper."""
        message = {
            "id": "m-1",
            "threadId": "t-1",
            "internalDate": "1723800000000",
            "snippet": "fallback snippet",
            "payload": {
                "headers": [
                    {"name": "From", "value": "Priya Nair <priya@northwind.example>"},
                    {"name": "Subject", "value": "Outage update"},
                ],
                "mimeType": "text/html",
                "body": {"data": _b64("<p>Restore at <b>14:40</b>.</p>")},
            },
        }

        def transport(method, url, token, json_body):
            if "messages/m-1" in url:
                return 200, message
            return 200, {"messages": [{"id": "m-1"}]}

        fetched = GmailProvider("tok", transport=transport).fetch_messages()

        assert len(fetched) == 1
        assert fetched[0].body == "Restore at 14:40."
        assert fetched[0].sender == "priya@northwind.example"


class TestGraphBodies:
    def _fetch_one(self, body: dict, preview: str = "") -> str:
        message = {
            "id": "m-1",
            "conversationId": "c-1",
            "from": {"emailAddress": {"address": "dana@calloway.example", "name": "Dana"}},
            "subject": "Service credits",
            "body": body,
            "bodyPreview": preview,
            "receivedDateTime": "2026-09-15T09:15:00Z",
        }

        def transport(method, url, token, json_body):
            return 200, {"value": [message]}

        return MicrosoftGraphProvider("tok", transport=transport).fetch_messages()[0].body

    def test_html_is_the_graph_default_and_is_rendered(self):
        """`body.contentType` is html unless asked otherwise, so this is the
        normal case for every Outlook message rather than an edge case."""
        body = self._fetch_one(
            {"contentType": "html", "content": "<div>Credits still unpaid.</div>"}
        )
        assert body == "Credits still unpaid."

    def test_declared_text_is_left_alone(self):
        body = self._fetch_one({"contentType": "text", "content": "Plain note, 5 < 6."})
        assert body == "Plain note, 5 < 6."

    def test_markup_mislabelled_as_text_is_still_rendered(self):
        """The declared type is trusted, but sniffed: the cost of being wrong
        in that direction is a wall of tags in the reader."""
        body = self._fetch_one({"contentType": "text", "content": "<p>Actually HTML.</p>"})
        assert body == "Actually HTML."

    def test_an_empty_body_falls_back_to_the_preview(self):
        body = self._fetch_one({"contentType": "html", "content": "  "}, preview="Preview text")
        assert body == "Preview text"

    def test_a_body_that_renders_to_nothing_falls_back_to_the_preview(self):
        """A style-only body is markup with no message in it."""
        body = self._fetch_one(
            {"contentType": "html", "content": "<style>.a{color:red}</style>"},
            preview="Preview text",
        )
        assert body == "Preview text"
