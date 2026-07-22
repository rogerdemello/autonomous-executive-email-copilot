"""Marketing surface: a self-contained landing page and pricing page.

Rendered server-side from the single :data:`licensing.PLANS` registry, so
pricing copy can never drift from what the entitlement system grants. Served
under ``/welcome`` and ``/pricing`` on the app's own domain — a public,
freely-shareable URL, no third-party host. Everything is inline (no external
assets), so it works behind the strictest CSP and in air-gapped demos.

Design: a restrained "quiet office" identity — deep ink-teal ground, warm
brass highlight, a serif display over a system sans, and a CSS-only live-triage
motif in the hero. Theme-aware (light/dark) via CSS custom properties.
"""

from __future__ import annotations

import html
import json

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from ..config import get_settings
from . import licensing

marketing_router = APIRouter(tags=["marketing"])

_FEATURE_LABELS = {
    licensing.FEATURE_APPROVALS: "Human-in-the-loop approvals",
    licensing.FEATURE_ANALYTICS: "Analytics & reporting",
    licensing.FEATURE_AUDIT_LOG: "Audit log",
    licensing.FEATURE_SSO: "SSO (SAML/OIDC)",
    licensing.FEATURE_PRIORITY_SUPPORT: "Priority support & SLA",
    licensing.FEATURE_CUSTOM_MODELS: "Bring-your-own / custom models",
}

_CSS = """
:root{
  --bg:#eef1f1;--paper:#fbfcfc;--panel:#f5f7f7;--ink:#111a1c;--muted:#55636a;
  --line:#dde3e3;--accent:#0e6e62;--brass:#b3822f;--good:#2e8f66;--warn:#b5842a;--crit:#bd5138;
  --shadow:20px 40px 80px -50px rgba(16,40,40,.5);
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,"Times New Roman",serif;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --mono:ui-monospace,"SF Mono","Cascadia Code","Roboto Mono",Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#0a1012;--paper:#101a1c;--panel:#14211f;--ink:#ecf1f0;--muted:#93a2a5;
  --line:#213230;--accent:#35b8a5;--brass:#d9a85c;--good:#4bbd88;--warn:#d3a44a;--crit:#db6f55;
  --shadow:24px 46px 90px -50px rgba(0,0,0,.75);}}
:root[data-theme="light"]{--bg:#eef1f1;--paper:#fbfcfc;--panel:#f5f7f7;--ink:#111a1c;--muted:#55636a;
  --line:#dde3e3;--accent:#0e6e62;--brass:#b3822f;--good:#2e8f66;--warn:#b5842a;--crit:#bd5138;
  --shadow:20px 40px 80px -50px rgba(16,40,40,.5);}
:root[data-theme="dark"]{--bg:#0a1012;--paper:#101a1c;--panel:#14211f;--ink:#ecf1f0;--muted:#93a2a5;
  --line:#213230;--accent:#35b8a5;--brass:#d9a85c;--good:#4bbd88;--warn:#d3a44a;--crit:#db6f55;
  --shadow:24px 46px 90px -50px rgba(0,0,0,.75);}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);line-height:1.6;-webkit-font-smoothing:antialiased}
a{color:inherit}
.wrap{width:min(1120px,92vw);margin-inline:auto}
.eyebrow{font-family:var(--mono);font-size:.72rem;letter-spacing:.22em;text-transform:uppercase;color:var(--accent);margin:0}
h1,h2,h3{font-family:var(--serif);font-weight:600;text-wrap:balance;letter-spacing:-.01em}
.top{display:flex;align-items:center;justify-content:space-between;padding:22px 0;gap:20px}
.logo{display:flex;align-items:center;gap:11px;font-family:var(--serif);font-size:1.15rem;font-weight:600;text-decoration:none}
.logo .glyph{width:34px;height:34px;border-radius:9px;display:grid;place-items:center;background:var(--accent);color:#06110f;font-family:var(--mono);font-weight:700;font-size:.9rem}
.top nav{display:flex;align-items:center;gap:26px;font-size:.92rem;color:var(--muted)}
.top nav a{text-decoration:none}
.top nav a:hover{color:var(--ink)}
.btn{display:inline-flex;align-items:center;gap:8px;justify-content:center;padding:11px 20px;border-radius:9px;font:inherit;font-size:.94rem;font-weight:600;text-decoration:none;cursor:pointer;border:1px solid var(--line);background:var(--paper);color:var(--ink);transition:transform .15s ease,box-shadow .15s ease}
.btn--primary{background:var(--accent);border-color:var(--accent);color:#06110f}
.btn:hover{transform:translateY(-1px)}
.btn--primary:hover{box-shadow:0 12px 26px -14px var(--accent)}
@media (max-width:720px){.top nav .hide{display:none}}
.hero{display:grid;grid-template-columns:1.05fr .95fr;gap:56px;align-items:center;padding:40px 0 72px}
.hero h1{font-size:clamp(2.4rem,5.4vw,4rem);line-height:1.02;margin:18px 0 20px}
.hero h1 .soft{color:var(--accent);font-style:italic}
.lede{font-size:1.16rem;color:var(--muted);max-width:34ch;margin:0 0 30px}
.cta{display:flex;gap:12px;flex-wrap:wrap;align-items:center}
.note{margin:22px 0 0;font-size:.84rem;color:var(--muted);font-family:var(--mono);letter-spacing:.02em}
.note b{color:var(--ink);font-weight:600}
.triage{background:var(--paper);border:1px solid var(--line);border-radius:18px;padding:20px;box-shadow:var(--shadow)}
.triage__bar{display:flex;align-items:center;gap:8px;padding:4px 6px 14px;border-bottom:1px solid var(--line)}
.dot{width:10px;height:10px;border-radius:50%;background:var(--line)}
.triage__title{margin-left:auto;font-family:var(--mono);font-size:.72rem;letter-spacing:.12em;color:var(--muted);text-transform:uppercase}
.mail{display:grid;grid-template-columns:1fr auto;gap:4px 12px;align-items:center;padding:13px 8px;border-bottom:1px solid var(--line);opacity:0;transform:translateY(6px);animation:rise .6s ease forwards}
.mail:last-child{border-bottom:0}
.mail:nth-child(2){animation-delay:.15s}
.mail:nth-child(3){animation-delay:.5s}
.mail:nth-child(4){animation-delay:.85s}
.mail__from{font-weight:600;font-size:.92rem}
.mail__subj{grid-column:1;font-size:.82rem;color:var(--muted)}
.chip{grid-row:1 / span 2;align-self:center;font-family:var(--mono);font-size:.68rem;letter-spacing:.08em;text-transform:uppercase;padding:5px 10px;border-radius:999px;white-space:nowrap;border:1px solid transparent}
.chip--reply{color:var(--good);background:color-mix(in srgb,var(--good) 14%,transparent);border-color:color-mix(in srgb,var(--good) 30%,transparent)}
.chip--escalate{color:var(--crit);background:color-mix(in srgb,var(--crit) 14%,transparent);border-color:color-mix(in srgb,var(--crit) 30%,transparent)}
.chip--defer{color:var(--warn);background:color-mix(in srgb,var(--warn) 14%,transparent);border-color:color-mix(in srgb,var(--warn) 30%,transparent)}
.chip--wait{color:var(--muted);background:color-mix(in srgb,var(--muted) 12%,transparent)}
.triage__foot{display:flex;align-items:center;gap:8px;padding:14px 8px 4px;font-size:.8rem;color:var(--muted)}
.pulse{width:8px;height:8px;border-radius:50%;background:var(--accent);animation:pulse 1.8s ease-in-out infinite}
@keyframes rise{to{opacity:1;transform:translateY(0)}}
@keyframes pulse{0%,100%{opacity:.35;transform:scale(.85)}50%{opacity:1;transform:scale(1)}}
@media (prefers-reduced-motion:reduce){.mail{animation:none;opacity:1;transform:none}.pulse{animation:none}}
section{padding:62px 0;border-top:1px solid var(--line)}
.sec-head{max-width:56ch;margin-bottom:38px}
.sec-head h2{font-size:clamp(1.7rem,3.4vw,2.5rem);margin:12px 0 0}
.pillars{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line);border:1px solid var(--line);border-radius:16px;overflow:hidden}
.pillar{background:var(--paper);padding:26px 24px}
.pillar h3{font-size:1.16rem;margin:14px 0 8px}
.pillar p{margin:0;color:var(--muted);font-size:.95rem}
.pillar .mark{font-family:var(--mono);font-size:.72rem;letter-spacing:.14em;color:var(--brass)}
.proof{background:var(--panel)}
.scores{display:grid;grid-template-columns:1.1fr 1.4fr;gap:40px;align-items:center}
.table-wrap{overflow-x:auto}
table.bench{width:100%;border-collapse:collapse;font-size:.92rem}
table.bench th,table.bench td{text-align:left;padding:12px 14px;border-bottom:1px solid var(--line)}
table.bench thead th{font-family:var(--mono);font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);font-weight:500}
table.bench td.num{font-family:var(--mono);font-variant-numeric:tabular-nums;text-align:right}
table.bench td.task{font-weight:600}
.lead{font-weight:700;color:var(--accent)}
.statline{display:flex;gap:34px;flex-wrap:wrap;margin-top:26px}
.stat .n{font-family:var(--serif);font-size:2.2rem;font-weight:600;line-height:1}
.stat .l{font-size:.82rem;color:var(--muted);margin-top:6px}
.plans{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}
.plan{background:var(--paper);border:1px solid var(--line);border-radius:15px;padding:24px 22px;display:flex;flex-direction:column}
.plan--feature{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent),var(--shadow);position:relative}
.plan__tag{position:absolute;top:-11px;left:22px;font-family:var(--mono);font-size:.64rem;letter-spacing:.12em;text-transform:uppercase;background:var(--accent);color:#06110f;padding:4px 10px;border-radius:6px}
.plan__name{font-family:var(--serif);font-size:1.25rem}
.plan__price{font-family:var(--mono);font-size:.9rem;color:var(--muted);margin:6px 0 4px}
.plan__seats{font-size:.82rem;color:var(--brass);font-family:var(--mono);letter-spacing:.04em}
.plan ul{list-style:none;padding:0;margin:16px 0 22px;flex:1;display:grid;gap:9px}
.plan li{font-size:.88rem;display:flex;gap:9px}
.plan li::before{content:"→";color:var(--accent)}
.close{text-align:center}
.close h2{font-size:clamp(1.9rem,4vw,2.8rem)}
.close p{color:var(--muted);max-width:46ch;margin:12px auto 28px}
footer{border-top:1px solid var(--line);padding:28px 0 44px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:12px;color:var(--muted);font-size:.85rem}
@media (max-width:900px){.hero,.scores{grid-template-columns:1fr}.pillars,.plans{grid-template-columns:1fr}}
"""


def _nav() -> str:
    return """
<header class="wrap top">
  <a class="logo" href="/welcome"><span class="glyph">EC</span> Email Copilot</a>
  <nav>
    <a class="hide" href="/welcome#how">How it works</a>
    <a class="hide" href="/welcome#proof">Proof</a>
    <a class="hide" href="/pricing">Pricing</a>
    <a class="btn" href="/dashboard/">Open app</a>
  </nav>
</header>"""


def _footer() -> str:
    email = html.escape(get_settings().sales_contact_email)
    return f"""
<footer class="wrap">
  <span>© Autonomous Executive Email Copilot — autonomous inbox management for leaders.</span>
  <span style="font-family:var(--mono);letter-spacing:.04em">Sales: <a href="mailto:{email}">{email}</a></span>
</footer>"""


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{_CSS}</style></head>
<body>{_nav()}<main>{body}</main>{_footer()}</body></html>"""


def _plan_card(plan: licensing.Plan, *, featured: bool) -> str:
    feats = "".join(f"<li>{html.escape(_FEATURE_LABELS.get(f, f))}</li>" for f in plan.features)
    tag = '<span class="plan__tag">Most chosen</span>' if featured else ""
    cta_href = "/dashboard/" if plan.key == "trial" else "/pricing#contact"
    cta_label = "Start trial" if plan.key == "trial" else "Talk to sales"
    cls = "plan plan--feature" if featured else "plan"
    btn = "btn btn--primary" if featured else "btn"
    return f"""
<div class="{cls}">{tag}
  <div class="plan__name">{html.escape(plan.name)}</div>
  <div class="plan__price">{html.escape(plan.price_display)}</div>
  <div class="plan__seats">up to {plan.seats} seats</div>
  <ul>{feats}</ul>
  <a class="{btn}" href="{cta_href}">{cta_label}</a>
</div>"""


def _pricing_grid() -> str:
    order = ["trial", "team", "business", "enterprise"]
    cards = "".join(
        _plan_card(licensing.PLANS[k], featured=(k == "business"))
        for k in order
        if k in licensing.PLANS
    )
    return f'<div class="plans">{cards}</div>'


_TRIAGE = """
<div class="triage" role="img" aria-label="A mailbox where the copilot has triaged four incoming emails: a client reply, a legal escalation, an internal deferral, and one still being decided.">
  <div class="triage__bar"><span class="dot"></span><span class="dot"></span><span class="dot"></span>
    <span class="triage__title">Live triage</span></div>
  <div class="mail"><span class="mail__from">Priya Nair · Northwind</span><span class="chip chip--reply">Reply</span>
    <span class="mail__subj">Re: renewal terms for Q3 — needs a number today</span></div>
  <div class="mail"><span class="mail__from">Legal — outside counsel</span><span class="chip chip--escalate">Escalate</span>
    <span class="mail__subj">Draft indemnification clause · flagged: legal</span></div>
  <div class="mail"><span class="mail__from">All-hands logistics</span><span class="chip chip--defer">Defer</span>
    <span class="mail__subj">Room booking for the offsite — low priority</span></div>
  <div class="mail"><span class="mail__from">CFO · budget review</span><span class="chip chip--wait">Deciding…</span>
    <span class="mail__subj">Q3 forecast — awaiting your approval to reply</span></div>
  <div class="triage__foot"><span class="pulse"></span> Copilot working the inbox · 2 actions held for approval</div>
</div>"""


@marketing_router.get("/welcome", response_class=HTMLResponse, include_in_schema=False)
def landing() -> HTMLResponse:
    body = f"""
<section class="wrap hero" style="border-top:0">
  <div>
    <p class="eyebrow">Autonomous Executive Email Copilot</p>
    <h1>The corner-office inbox that <span class="soft">runs itself.</span></h1>
    <p class="lede">An agent that triages, prioritizes, drafts, and escalates high-stakes email — with your sign-off where it matters, and a benchmark behind every decision.</p>
    <div class="cta">
      <a class="btn btn--primary" href="/dashboard/">Start free trial</a>
      <a class="btn" href="/welcome#how">See how it works</a>
    </div>
    <p class="note">No card to start · <b>14-day trial</b> · SSO &amp; audit log on Business+</p>
  </div>
  {_TRIAGE}
</section>

<section id="how" class="wrap">
  <div class="sec-head"><p class="eyebrow">What it does</p>
    <h2>Built for the people whose inbox can't wait.</h2></div>
  <div class="pillars">
    <div class="pillar"><span class="mark">01 · TRIAGE</span>
      <h3>Reads the room, not just the subject</h3>
      <p>Classifies, prioritizes, and drafts across a high-volume mailbox — weighing deadlines, business value, and risk so nothing critical slips.</p></div>
    <div class="pillar"><span class="mark">02 · CONTROL</span>
      <h3>Approvals where they matter</h3>
      <p>Replies and escalations pause for a human. Legal, finance, and security tags route to the right owner automatically.</p></div>
    <div class="pillar"><span class="mark">03 · TRUST</span>
      <h3>Tenant-isolated &amp; audited</h3>
      <p>Every organization's data stays its own. SSO, a full audit log, and encrypted mailbox tokens — designed for procurement.</p></div>
  </div>
</section>

<section id="proof" class="proof"><div class="wrap">
  <div class="sec-head"><p class="eyebrow">Measured, not guessed</p>
    <h2>Every policy is scored before it touches a real inbox.</h2></div>
  <div class="scores">
    <div class="table-wrap"><table class="bench">
      <thead><tr><th>Task</th><th style="text-align:right">Heuristic</th><th style="text-align:right">Multi-agent</th><th style="text-align:right">LLM (gpt-4o)</th></tr></thead>
      <tbody>
        <tr><td class="task">Classification</td><td class="num lead">1.00</td><td class="num">0.80</td><td class="num">0.17</td></tr>
        <tr><td class="task">Prioritization</td><td class="num lead">1.00</td><td class="num lead">1.00</td><td class="num lead">1.00</td></tr>
        <tr><td class="task">Full management</td><td class="num lead">0.67</td><td class="num">0.09</td><td class="num">0.62</td></tr>
      </tbody></table></div>
    <div>
      <p style="margin-top:0;color:var(--muted)">Scores are bounded to the open interval <span style="font-family:var(--mono)">(0,1)</span>, deterministic across seeds, and reproducible from a single command. The frontier LLM is competitive on full management and honestly weaker on narrow classification — a finding, not a slogan.</p>
      <div class="statline">
        <div class="stat"><div class="n">3×3×3</div><div class="l">tasks × personas × seeds</div></div>
        <div class="stat"><div class="n">≈$0.009</div><div class="l">per managed episode</div></div>
        <div class="stat"><div class="n">100%</div><div class="l">actions audit-logged</div></div>
      </div>
    </div>
  </div>
</div></section>

<section class="wrap close">
  <h2>Give every executive an inbox that runs itself.</h2>
  <p>Start a free trial today, or walk through a rollout across your leadership team with us.</p>
  <div class="cta" style="justify-content:center">
    <a class="btn btn--primary" href="/dashboard/">Start free trial</a>
    <a class="btn" href="/pricing">See pricing</a>
  </div>
</section>"""
    return HTMLResponse(
        _page("Executive Email Copilot — the corner-office inbox that runs itself", body)
    )


@marketing_router.get("/pricing", response_class=HTMLResponse, include_in_schema=False)
def pricing_page() -> HTMLResponse:
    email = html.escape(get_settings().sales_contact_email)
    body = f"""
<section class="wrap hero" style="border-top:0;grid-template-columns:1fr;padding-bottom:24px">
  <div>
    <p class="eyebrow">Pricing</p>
    <h1>Start free. <span class="soft">Scale on a handshake.</span></h1>
    <p class="lede" style="max-width:52ch">Start a trial with no card. When you're ready to roll out across teams, we tailor seats, security, and support — activated with a signed license key.</p>
  </div>
</section>

<section class="wrap" style="border-top:0">{_pricing_grid()}</section>

<section id="contact" class="wrap close">
  <p class="eyebrow" style="text-align:center">Talk to sales</p>
  <h2>Team, Business &amp; Enterprise are activated after a short conversation.</h2>
  <p>Tell us about your rollout and we'll issue your license key.</p>
  <div class="cta" style="justify-content:center">
    <a class="btn btn--primary" href="mailto:{email}?subject=Executive%20Email%20Copilot%20—%20Pricing">Email {email}</a>
    <a class="btn" href="/dashboard/">Start free trial</a>
  </div>
</section>"""
    return HTMLResponse(_page("Pricing — Executive Email Copilot", body))


@marketing_router.get("/api/pricing", include_in_schema=True)
def pricing_json() -> JSONResponse:
    """Machine-readable pricing, sourced from the entitlement plan registry."""
    plans = [
        {
            "key": p.key,
            "name": p.name,
            "seats": p.seats,
            "features": list(p.features),
            "price_display": p.price_display,
            "blurb": p.blurb,
        }
        for p in licensing.PLANS.values()
    ]
    return JSONResponse(json.loads(json.dumps({"plans": plans})))


@marketing_router.get(
    "/.well-known/security.txt", response_class=PlainTextResponse, include_in_schema=False
)
def security_txt() -> PlainTextResponse:
    """Serve an RFC 9116 security.txt at the well-known location."""
    settings = get_settings()
    contact = settings.sales_contact_email.replace("sales@", "security@")
    base = settings.resolved_app_public_url
    body = (
        f"Contact: mailto:{contact}\n"
        "Preferred-Languages: en\n"
        f"Policy: {base}/SECURITY.md\n"
        f"Canonical: {base}/.well-known/security.txt\n"
        "Expires: 2027-01-01T00:00:00.000Z\n"
    )
    return PlainTextResponse(body)
