"""Static content pages: legal, policy, help, and API documentation.

Each page is data — a title, a summary, and a list of sections — so the same
template renders all of them with a consistent table of contents, and the copy
lives somewhere a non-programmer can edit without touching routing code.

A note for whoever runs this instance: the policy text below is written to be
accurate about *what the software actually does*, which is the hard part and
the part a generic template always gets wrong. It is not legal advice. Before
going live you must fill in every ``OPERATOR_*`` value in the block below, and
you should have a lawyer review the result against the jurisdictions you
operate in — data-protection law in particular varies enormously.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["PAGES", "page_by_slug", "LEGAL_NAV", "OPERATOR"]


# --------------------------------------------------------------------------- #
# Fill these in before deploying. They are interpolated into the pages below.
# --------------------------------------------------------------------------- #

OPERATOR = {
    "name": "the SerikaSearch operator",
    "site": "SerikaSearch",
    "contact_email": "support@serika.dev",
    "privacy_email": "privacy@serika.dev",
    "legal_email": "legal@serika.dev",
    "security_email": "security@serika.dev",
    "crawler_agent": "serikacrawler",
    "jurisdiction": "your jurisdiction",
    "updated": "15 August 2026",
}


@dataclass
class Section:
    id: str
    heading: str
    body: str          # trusted HTML, authored here — never user input


@dataclass
class Page:
    slug: str
    title: str
    kicker: str
    summary: str
    sections: list[Section] = field(default_factory=list)
    updated: str = ""
    nav_label: str = ""

    @property
    def label(self) -> str:
        return self.nav_label or self.title


def _p(*paragraphs: str) -> str:
    return "".join(f"<p>{text}</p>" for text in paragraphs)


def _ul(*items: str) -> str:
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"


def _ol(*items: str) -> str:
    return "<ol>" + "".join(f"<li>{item}</li>" for item in items) + "</ol>"


def _table(headers: tuple[str, ...], rows: tuple[tuple[str, ...], ...]) -> str:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return (f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


_AGENT = OPERATOR["crawler_agent"]
_UPDATED = OPERATOR["updated"]


# --------------------------------------------------------------------------- #
# Privacy
# --------------------------------------------------------------------------- #

PRIVACY = Page(
    slug="privacy",
    title="Privacy policy",
    nav_label="Privacy",
    kicker="Policy",
    updated=_UPDATED,
    summary=("SerikaSearch has no user accounts, no advertising, and no "
             "tracking cookies. It does not build a profile of you and it "
             "does not keep a log of what you search for. This page explains "
             "exactly what does happen, including the parts that are less "
             "flattering."),
    sections=[
        Section("no-profile", "What we do not do", _p(
            "There are no accounts, so there is nothing to sign in to and "
            "nothing to link searches together with. There is no advertising "
            "network, no analytics script, no tracking pixel, no fingerprinting, "
            "and no third-party JavaScript of any kind — the pages you are "
            "reading load one stylesheet bundle and one script, both served "
            "from this domain.",
            "Search queries are not written to a persistent query log, are not "
            "sold, and are not shared with anyone. Autocomplete suggestions are "
            "built from the titles of pages in the crawl index, not from what "
            "other people have typed, precisely so that no query log has to "
            "exist for the feature to work.",
        )),
        Section("what-we-process", "What is processed when you search", _p(
            "Handling a search request necessarily involves some data. Here is "
            "all of it:"
        ) + _table(
            ("Data", "Why", "How long it is kept"),
            (
                ("Your query", "To run the search and render results",
                 "In memory for the duration of the request. Query text is "
                 "cached in Redis against a hash for up to 5 minutes so a "
                 "popular search doesn't re-run — the cache holds results, "
                 "not who asked."),
                ("IP address", "TCP has to send the response somewhere",
                 "Not written to an application log. Your hosting provider or "
                 "reverse proxy may keep its own access logs — see below."),
                ("User agent", "Only to answer the “my ip” instant answer, "
                 "which echoes it back to you",
                 "Not stored."),
                ("Settings you choose", "Results per page, safe search, "
                 "open-links-in-new-tab",
                 "Stored in your own browser's localStorage. Never sent to the "
                 "server, never readable by us."),
            ),
        )),
        Section("server-logs", "Server and infrastructure logs", _p(
            "The SerikaSearch application does not write an access log. That is "
            "not the whole story, and pretending otherwise would be dishonest: "
            "whatever runs in front of it — a reverse proxy, a CDN, a hosting "
            "provider's load balancer — may keep its own logs containing IP "
            "addresses, timestamps and requested paths. Requested paths include "
            "the query string, which means they can include your search terms.",
            f"If you are running this instance yourself, configuring those "
            f"upstream logs (or turning them off) is on you, and you should "
            f"describe what you chose here. If you are a visitor and this "
            f"matters to you, ask {OPERATOR['privacy_email']} what the "
            f"infrastructure in front of this instance retains.",
        )),
        Section("third-parties", "Requests that leave this site", _p(
            "This is the part most search engines gloss over. Some features "
            "cause your browser, or this server, to contact somewhere else."
        ) + _table(
            ("Feature", "Who is contacted", "By whom"),
            (
                ("Image results and knowledge-panel photos",
                 "The website that hosts the image",
                 "<strong>Your browser.</strong> Thumbnails are hot-linked from "
                 "the origin site, so that site can see your IP address and "
                 "that you loaded the image. We send "
                 "<code>referrerpolicy=\"no-referrer\"</code> so it does not "
                 "learn your search terms."),
                ("Video results",
                 "YouTube (via youtube-nocookie.com), Vimeo",
                 "<strong>Your browser</strong>, and only after you click play. "
                 "Thumbnails and the player are loaded from those services and "
                 "are subject to their privacy policies."),
                ("Favicons next to results", "Nobody",
                 "The crawler fetched and cached these ahead of time; they are "
                 "served from this domain."),
                ("Knowledge panel text",
                 "Wikipedia, Simple Wikipedia, Wiktionary, Grokipedia",
                 "<strong>This server</strong>, not your browser. The article "
                 "title is sent; your IP is not."),
                ("Weather answers", "Open-Meteo",
                 "<strong>This server.</strong> The place name you asked about "
                 "is sent."),
                ("Currency answers", "Frankfurter / European Central Bank",
                 "<strong>This server.</strong> Only the currency pair is sent."),
                ("Dictionary answers", "dictionaryapi.dev",
                 "<strong>This server.</strong> Only the word is sent."),
                ("!bang shortcuts", "The site you jumped to",
                 "<strong>Your browser</strong>, after a redirect you asked "
                 "for. The destination sees a normal visit from you."),
            ),
        ) + _p(
            "Every server-side lookup above is cached, so a popular query "
            "reaches the upstream service once rather than once per visitor. "
            "None of them receive your IP address, and none of them are told "
            "who asked."
        )),
        Section("cookies", "Cookies", _p(
            "SerikaSearch sets no tracking cookies and no advertising cookies. "
            "Preferences are kept in localStorage in your browser instead of a "
            "cookie, which means they never travel to the server at all. "
            "Clearing site data in your browser resets them. See the "
            "<a href=\"/cookies\">cookie policy</a> for the full detail."
        )),
        Section("crawled-data", "Data about websites", _p(
            f"The crawler, <code>{_AGENT}</code>, indexes publicly reachable web "
            f"pages: their text, titles, descriptions, images, and the "
            f"structured metadata pages publish about themselves. If a page you "
            f"published contains personal information, that information can end "
            f"up in the index because it was public on the open web.",
            "You can have it removed. See "
            "<a href=\"/how-to-opt-out\">how to opt out</a> for the robots.txt "
            "directives the crawler obeys and a form for requesting removal of "
            "material that is already indexed.",
        )),
        Section("your-rights", "Your rights", _p(
            "Because no personal data about searchers is stored, there is "
            "generally nothing to export or erase in response to a data-subject "
            "request about your searching — there is no record of it to begin "
            "with.",
            f"For personal data that appears in <em>indexed pages</em>, write to "
            f"{OPERATOR['privacy_email']} with the URLs concerned. Depending on "
            f"where you live you may have rights of access, rectification, "
            f"erasure, restriction, objection and portability under laws such "
            f"as the GDPR or the CCPA; requests are answered within one month.",
        )),
        Section("children", "Children", _p(
            "SerikaSearch is a general-audience search tool and is not directed "
            "at children. It knowingly collects no personal information from "
            "anyone, including children."
        )),
        Section("changes", "Changes to this policy", _p(
            f"Material changes will be reflected in the “last updated” date at "
            f"the top of this page. Because there are no accounts, there is no "
            f"mailing list to notify — if this matters to you, check back "
            f"occasionally. Questions go to {OPERATOR['privacy_email']}."
        )),
    ],
)


# --------------------------------------------------------------------------- #
# Terms
# --------------------------------------------------------------------------- #

TERMS = Page(
    slug="terms",
    title="Terms of use",
    nav_label="Terms",
    kicker="Legal",
    updated=_UPDATED,
    summary=("Plain terms for a free, no-account search engine: use it "
             "reasonably, don't overload it, and understand that results are "
             "other people's content served as found."),
    sections=[
        Section("acceptance", "Using this service", _p(
            f"By using {OPERATOR['site']} you accept these terms. If you do not "
            f"accept them, don't use it. The service is provided free of charge "
            f"and without any account, which also means without any promise of "
            f"availability."
        )),
        Section("acceptable-use", "Acceptable use", _p(
            "You may search, read results, use the tools, and query the public "
            "JSON API. You may not:"
        ) + _ul(
            "send automated traffic at a volume that degrades the service for "
            "others, or attempt to scrape the entire index",
            "attempt to gain unauthorised access to the service, its "
            "infrastructure, or any data in it",
            "use the service to break the law, to harass anyone, or to "
            "distribute malware",
            "misrepresent the service as your own, or imply endorsement that "
            "has not been given",
        ) + _p(
            "The API is rate-limited. Please cache what you fetch and identify "
            "your client with a descriptive User-Agent so problems can be "
            "traced to a person rather than to an IP address."
        )),
        Section("results", "About the results", _p(
            "Search results are automatically generated references to third-"
            "party web pages. Their content belongs to whoever published it, "
            "is not reviewed or endorsed here, and may be inaccurate, "
            "offensive, out of date, or unlawful in your country. Snippets and "
            "thumbnails are shown to help you decide whether to visit a page — "
            "the ordinary purpose of a search index.",
            "Instant answers, knowledge panels and dictionary entries are drawn "
            "from third-party sources that are named on every card. They are "
            "provided for convenience and can be wrong. Do not rely on them for "
            "medical, legal, financial or safety-critical decisions."
        )),
        Section("intellectual-property", "Intellectual property", _p(
            "The SerikaSearch software and its interface are the operator's. "
            "Indexed content belongs to its respective owners. If you believe "
            "material in the index infringes your copyright, see the "
            "<a href=\"/dmca\">copyright policy</a>."
        )),
        Section("warranty", "No warranty", _p(
            "The service is provided “as is” and “as available”, without "
            "warranties of any kind, express or implied, including "
            "merchantability, fitness for a particular purpose, accuracy, and "
            "non-infringement. Search coverage is partial by nature: an index "
            "is a snapshot of a crawl, not a map of the web."
        )),
        Section("liability", "Limitation of liability", _p(
            "To the fullest extent the law allows, the operator is not liable "
            "for indirect, incidental, special, consequential or exemplary "
            "damages, or for any loss of data, profits or goodwill, arising "
            "from your use of the service or from any third-party content "
            "reached through it. Nothing here excludes liability that cannot "
            "lawfully be excluded."
        )),
        Section("termination", "Availability and changes", _p(
            "The service may be changed, suspended or discontinued at any time "
            "without notice. Access may be blocked where use breaches these "
            "terms."
        )),
        Section("law", "Governing law", _p(
            f"These terms are governed by the laws of {OPERATOR['jurisdiction']}, "
            f"without regard to conflict-of-law rules. Questions go to "
            f"{OPERATOR['legal_email']}."
        )),
    ],
)


# --------------------------------------------------------------------------- #
# Opt out
# --------------------------------------------------------------------------- #

OPT_OUT = Page(
    slug="how-to-opt-out",
    title="How to opt out",
    nav_label="Opt out",
    kicker="For site owners",
    updated=_UPDATED,
    summary=(f"Keeping your site out of SerikaSearch takes one line in "
             f"robots.txt, and the crawler obeys it on its next visit. To have "
             f"pages that are already indexed removed now, use the form at the "
             f"bottom of this page."),
    sections=[
        Section("identify", "How to recognise the crawler", _p(
            f"The crawler identifies itself honestly and never disguises itself "
            f"as a browser. Its robots.txt token is <code>{_AGENT}</code> and "
            f"its full User-Agent looks like this:"
        ) + f"<pre><code>{_AGENT}/1.1 (+https://github.com/serikasearch/"
            f"serikacrawler; respects robots.txt)</code></pre>" + _p(
            "It requests robots.txt before anything else on a host, caches the "
            "result, honours <code>Crawl-delay</code>, keeps one request in "
            "flight per host at a time, and caps how many pages it takes from "
            "any single site."
        )),
        Section("robots", "Block it with robots.txt", _p(
            "Add this to the robots.txt file at the root of your domain to "
            "block SerikaSearch specifically while leaving other crawlers "
            "alone:"
        ) + f"<pre><code>User-agent: {_AGENT}\nDisallow: /</code></pre>" + _p(
            "To block every crawler that respects the standard:"
        ) + "<pre><code>User-agent: *\nDisallow: /</code></pre>" + _p(
            "To allow the site but exclude one area:"
        ) + f"<pre><code>User-agent: {_AGENT}\n"
            f"Disallow: /private/\nDisallow: /drafts/</code></pre>" + _p(
            "Changes take effect the next time the crawler reads your "
            "robots.txt, which is at most 24 hours after you publish them. "
            "Blocking future crawling does not remove pages already in the "
            "index — use the removal form below for that, or the meta tags in "
            "the next section, which do cause removal on the next visit."
        )),
        Section("meta", "Block or remove individual pages", _p(
            "A robots meta tag in a page's <code>&lt;head&gt;</code> keeps that "
            "page out of the index, and causes it to be dropped from the index "
            "the next time it is crawled:"
        ) + "<pre><code>&lt;meta name=\"robots\" content=\"noindex\"&gt;</code></pre>"
          + _p("The same instruction as an HTTP response header, which works "
               "for non-HTML files too:")
          + "<pre><code>X-Robots-Tag: noindex</code></pre>"
          + _p("Use <code>nofollow</code> to stop links on a page from being "
               "followed, and <code>noarchive</code> to prevent snippets:")
          + "<pre><code>&lt;meta name=\"robots\" "
            "content=\"noindex, nofollow, noarchive\"&gt;</code></pre>"),
        Section("images", "Keeping images out", _p(
            "Images are indexed from the pages that display them. Blocking a "
            "page with robots.txt or <code>noindex</code> also removes its "
            "images. To keep a specific image directory out while leaving the "
            "pages indexed:"
        ) + f"<pre><code>User-agent: {_AGENT}\n"
            f"Disallow: /images/private/</code></pre>" + _p(
            "Note that SerikaSearch does not copy or re-host your images: "
            "thumbnails in image search are loaded from your server by the "
            "visitor's browser. If you would rather they were not, the "
            "directives above stop them appearing at all."
        )),
        Section("removal", "Request removal of indexed content", _p(
            "Use this form to ask for material that is already indexed to be "
            "taken out. Requests are reviewed by a person before anything is "
            "deleted, and site ownership is verified first — otherwise anyone "
            "could remove anyone's site.",
            "The fastest route to verification is to add the robots.txt "
            "directive above before submitting: it proves control of the "
            "domain and blocks re-crawling at the same time. Removal of the "
            "existing index entries normally happens within a few days.",
        )),
        Section("other", "Other kinds of request", _p(
            "Copyright complaints are handled separately — see the "
            "<a href=\"/dmca\">copyright policy</a>. Requests about personal "
            "information appearing in someone else's pages are covered by the "
            "<a href=\"/privacy#your-rights\">privacy policy</a>, and should go "
            f"to {OPERATOR['privacy_email']}."
        )),
    ],
)


# --------------------------------------------------------------------------- #
# Copyright
# --------------------------------------------------------------------------- #

DMCA = Page(
    slug="dmca",
    title="Copyright and takedowns",
    nav_label="Copyright",
    kicker="Legal",
    updated=_UPDATED,
    summary=("How to report material in the index that infringes your "
             "copyright, and what happens next."),
    sections=[
        Section("what-we-store", "What is actually stored here", _p(
            "SerikaSearch is an index, not a host. For each page it stores the "
            "URL, the title, a description, extracted text used to match "
            "searches, and the metadata the page publishes about itself. "
            "Images and videos are referenced by URL and loaded from their "
            "original servers; they are not copied here. The one exception is "
            "site favicons, which are cached locally so result pages don't make "
            "requests to third parties for icons.",
            "Removing something from this index does not remove it from the "
            "web. If the material is hosted somewhere else, the host is who you "
            "need."
        )),
        Section("notice", "Sending a notice", _p(
            "Send a written notice to "
            f"{OPERATOR['legal_email']} including all of the following. A "
            "notice missing any of it cannot be acted on."
        ) + _ol(
            "Your physical or electronic signature.",
            "Identification of the copyrighted work you claim has been "
            "infringed.",
            "The exact URLs on this site (search result URLs) where the "
            "material appears, specific enough that it can be located.",
            "Your name, address, telephone number and email address.",
            "A statement that you have a good-faith belief that the use is not "
            "authorised by the copyright owner, its agent, or the law.",
            "A statement, under penalty of perjury, that the information in "
            "the notice is accurate and that you are the copyright owner or "
            "authorised to act on their behalf.",
        )),
        Section("process", "What happens next", _p(
            "Valid notices are actioned promptly: the URLs are removed from the "
            "index and, where appropriate, the host is blocked from future "
            "crawling. Notices are reviewed by a person, not automatically.",
            "Deliberately materially misrepresenting that material is "
            "infringing can make you liable for damages, including costs and "
            "legal fees. Please be sure before you send one."
        )),
        Section("counter", "Counter-notice", _p(
            "If your material was removed and you believe that was a mistake or "
            "a misidentification, send a counter-notice to "
            f"{OPERATOR['legal_email']} with your signature, identification of "
            "the removed material and where it appeared, a statement under "
            "penalty of perjury that you have a good-faith belief it was "
            "removed in error, and your name, address and telephone number."
        )),
    ],
)


# --------------------------------------------------------------------------- #
# Cookies
# --------------------------------------------------------------------------- #

COOKIES = Page(
    slug="cookies",
    title="Cookie policy",
    nav_label="Cookies",
    kicker="Policy",
    updated=_UPDATED,
    summary=("SerikaSearch sets no cookies at all. There is no consent banner "
             "because there is nothing to consent to."),
    sections=[
        Section("none", "No cookies are set", _p(
            "This site does not set cookies — not for analytics, not for "
            "advertising, not for sessions. There are no accounts, so there is "
            "no session to keep. This is why you have not been shown a cookie "
            "banner: under laws such as the EU's ePrivacy Directive, consent is "
            "required for storing or accessing information on your device "
            "beyond what is strictly necessary, and nothing here does that "
            "without your action."
        )),
        Section("localstorage", "What is stored in your browser", _p(
            "If you change a setting on the <a href=\"/settings\">settings "
            "page</a>, that choice is saved in your browser's localStorage. "
            "localStorage is not a cookie: it is never attached to requests and "
            "the server cannot read it. The keys used are:"
        ) + _table(
            ("Key", "Purpose"),
            (
                ("<code>serika:settings</code>",
                 "Results per page, safe search, whether links open in a new "
                 "tab, image grid density."),
                ("<code>serika:recent</code>",
                 "Your own recent searches, shown under the search box on the "
                 "home page. Off by default; cleared with one click."),
            ),
        ) + _p(
            "Clearing site data for this domain in your browser removes both, "
            "and the site keeps working exactly as before with default "
            "settings."
        )),
        Section("third-party", "Third-party storage", _p(
            "Embedded video players are the one place third-party storage can "
            "appear, and only after you press play. YouTube embeds use "
            "<code>youtube-nocookie.com</code>, which does not set persistent "
            "cookies until playback starts; Vimeo embeds are loaded with "
            "<code>dnt=1</code>. Once you start a video, that provider's own "
            "policies apply. See the <a href=\"/privacy#third-parties\">privacy "
            "policy</a> for the full list of requests that leave this site."
        )),
    ],
)


# --------------------------------------------------------------------------- #
# Accessibility, security, about, help, API
# --------------------------------------------------------------------------- #

ACCESSIBILITY = Page(
    slug="accessibility",
    title="Accessibility",
    nav_label="Accessibility",
    kicker="Statement",
    updated=_UPDATED,
    summary=("SerikaSearch aims to meet WCAG 2.2 level AA. Here is what has "
             "been done, and what is known to fall short."),
    sections=[
        Section("commitment", "What has been done", _ul(
            "Every interactive control is reachable and operable by keyboard, "
            "with a visible focus ring that is never removed.",
            "A skip link jumps past the header straight to the results.",
            "Text meets or exceeds 4.5:1 contrast against the background; "
            "large text and UI borders meet 3:1.",
            "The interface reflows to a single column at 320 CSS pixels without "
            "horizontal scrolling, and supports 200% zoom.",
            "Result lists, tab bars and dialogs use native semantics or correct "
            "ARIA roles, and dialogs trap focus and restore it on close.",
            "All animation is suppressed when your system asks for reduced "
            "motion.",
            "Images that carry meaning have text alternatives; decorative "
            "images are hidden from assistive technology.",
            "Touch targets are at least 44×44 CSS pixels on small screens.",
        )),
        Section("limitations", "Known limitations", _p(
            "Two honest caveats. First, the alt text shown with image results "
            "is the alt text the original page provided — where a site wrote a "
            "poor description, or none, that is what appears. Second, embedded "
            "video players are third-party components whose accessibility is "
            "not under this site's control."
        )),
        Section("feedback", "Feedback", _p(
            f"If something here is unusable with your assistive technology, "
            f"that is a bug and worth reporting. Write to "
            f"{OPERATOR['contact_email']} describing what you were doing, what "
            f"you expected, and what your setup is."
        )),
    ],
)

SECURITY = Page(
    slug="security",
    title="Security",
    nav_label="Security",
    kicker="Policy",
    updated=_UPDATED,
    summary="How to report a vulnerability, and what this service does to "
            "protect itself.",
    sections=[
        Section("reporting", "Reporting a vulnerability", _p(
            f"Send details to {OPERATOR['security_email']}. Please include "
            f"steps to reproduce, the impact you believe it has, and any proof "
            f"of concept. You will get an acknowledgement, and you are welcome "
            f"to be credited if a fix ships.",
            "Please do not run automated scanners against the live service, "
            "access or modify data that is not yours, or degrade the service "
            "for other people while testing. Reports made in good faith under "
            "those conditions will not be pursued."
        )),
        Section("measures", "What the service does", _ul(
            "All rendered output is HTML-escaped by default; the template "
            "engine requires an explicit marker to emit raw HTML, and that "
            "marker is only ever used with markup generated here.",
            "Every database query is parameterised — no user input is ever "
            "concatenated into SQL.",
            "The link-preview endpoint refuses non-public addresses, URLs with "
            "credentials, non-HTTP schemes and unusual ports, so it cannot be "
            "used to probe internal networks.",
            "Static file and template paths are confined to their directories, "
            "so a crafted path cannot escape them.",
            "The calculator parses expressions into a syntax tree and evaluates "
            "only a whitelist of nodes — user input is never passed to eval.",
            "Responses carry a strict Content-Security-Policy, "
            "<code>nosniff</code>, frame-ancestors protection, and a referrer "
            "policy that keeps query strings from leaking to other sites.",
            "Generated passwords use the operating system's cryptographic "
            "random source, and are never stored or logged.",
        )),
    ],
)

ABOUT = Page(
    slug="about",
    title="About SerikaSearch",
    nav_label="About",
    kicker="Colophon",
    updated=_UPDATED,
    summary=("An independent search engine with its own crawler and its own "
             "index — no results borrowed from anyone else's API."),
    sections=[
        Section("what", "What this is", _p(
            "SerikaSearch runs its own crawler, builds its own index, and ranks "
            "its own results. It is not a skin over another search engine's "
            "API. That means the coverage is smaller than the giants' — "
            "considerably smaller — and it also means nothing here is shaped by "
            "an advertising auction.",
            "There are no ads, no accounts, no tracking, and no AI-generated "
            "answers. Instant answers are computed or fetched from named "
            "sources, and every one of them tells you where it came from."
        )),
        Section("how", "How it works", _p(
            "The crawler fetches pages politely — robots.txt first, one request "
            "per host at a time, per-host caps — and extracts text, links, "
            "images, video embeds, favicons, and the structured metadata pages "
            "publish about themselves. Pages go into PostgreSQL with weighted "
            "full-text vectors: titles count for more than body text, which "
            "counts for more than URLs. Redis holds the crawl frontier and "
            "caches hot queries.",
            "Ranking combines text relevance with signals for title matches, "
            "URL matches, content depth and freshness. There is no personalised "
            "ranking, because there is no profile to personalise against — two "
            "people searching the same words get the same results."
        )),
        Section("instant", "Instant answers", _p(
            "Roughly twenty tools answer directly in the results: a calculator, "
            "unit and currency conversion, a world clock, date arithmetic, "
            "colour conversion, hashes and encodings, a QR generator, "
            "dictionary lookups and more. Most run locally with no network "
            "call. Browse them all on the <a href=\"/tools\">tools page</a>."
        )),
        Section("open", "Open by default", _p(
            "There is a public JSON <a href=\"/api-docs\">API</a>, an "
            "<a href=\"/opensearch.xml\">OpenSearch descriptor</a> so you can "
            "add SerikaSearch to your browser's address bar, and "
            "<a href=\"/llms.txt\">llms.txt</a> for machine consumers. Site "
            "owners can opt out at any time — see "
            "<a href=\"/how-to-opt-out\">how to opt out</a>."
        )),
    ],
)

HELP = Page(
    slug="help",
    title="Search help",
    nav_label="Help",
    kicker="Guide",
    updated=_UPDATED,
    summary="Operators, shortcuts and keyboard controls.",
    sections=[
        Section("operators", "Search operators", _table(
            ("Operator", "What it does", "Example"),
            (
                ("<code>\"…\"</code>", "Match an exact phrase",
                 "<code>\"time complexity\"</code>"),
                ("<code>site:</code>", "Restrict to one site",
                 "<code>rust site:github.com</code>"),
                ("<code>-word</code>", "Exclude pages containing a word",
                 "<code>python -snake</code>"),
                ("<code>intitle:</code>", "The title must contain the word",
                 "<code>intitle:tutorial svg</code>"),
                ("<code>inurl:</code>", "The URL must contain the word",
                 "<code>inurl:docs asyncio</code>"),
            ),
        ) + _p("Operators can be combined freely: "
               "<code>intitle:guide site:python.org -legacy</code>.")),
        Section("instant", "Things you can just type", _table(
            ("Type this", "Get"),
            (
                ("<code>1+1</code> · <code>sqrt(144)</code> · "
                 "<code>20% of 80</code>", "Calculator"),
                ("<code>5 km to miles</code> · <code>180 f in c</code>",
                 "Unit conversion"),
                ("<code>100 usd to eur</code>", "Live currency rates"),
                ("<code>weather in tokyo</code>", "Conditions and forecast"),
                ("<code>time in tokyo</code>", "World clock"),
                ("<code>days until christmas</code>", "Date arithmetic"),
                ("<code>define serendipity</code>", "Dictionary entry"),
                ("<code>#a274ff</code>", "Colour conversion"),
                ("<code>sha256 hello</code> · <code>base64 encode hi</code>",
                 "Hashes and encodings"),
                ("<code>qr code for …</code>", "QR code"),
                ("<code>generate password</code> · <code>uuid</code>",
                 "Generators"),
                ("<code>roll 2d6</code> · <code>flip a coin</code>", "Random"),
            ),
        )),
        Section("bangs", "!bang shortcuts", _p(
            "Start (or end) a search with a bang to jump straight to another "
            "site's results: <code>!w kyoto</code> goes to Wikipedia, "
            "<code>!gh serika</code> to GitHub, <code>!yt lofi</code> to "
            "YouTube. The full list is on the <a href=\"/bangs\">bangs "
            "page</a>."
        )),
        Section("keyboard", "Keyboard shortcuts", _table(
            ("Key", "Action"),
            (
                ("<kbd>/</kbd>", "Focus the search box"),
                ("<kbd>j</kbd> / <kbd>k</kbd>", "Next / previous result"),
                ("<kbd>Enter</kbd>", "Open the highlighted result"),
                ("<kbd>1</kbd>–<kbd>4</kbd>", "Switch tab (Web, Images, "
                                               "Videos, News)"),
                ("<kbd>←</kbd> / <kbd>→</kbd>", "Previous / next image in the "
                                                "lightbox, or page of results"),
                ("<kbd>Esc</kbd>", "Close the lightbox, player or suggestions"),
                ("<kbd>?</kbd>", "Show the shortcut list"),
            ),
        )),
    ],
)

API_DOCS = Page(
    slug="api-docs",
    title="API",
    nav_label="API",
    kicker="Developers",
    updated=_UPDATED,
    summary=("A public, keyless JSON API over the same index the website "
             "uses."),
    sections=[
        Section("endpoints", "Endpoints", _table(
            ("Endpoint", "Parameters", "Returns"),
            (
                ("<code>GET /api/search</code>",
                 "<code>q</code>, <code>limit</code> (1–50), <code>page</code>, "
                 "<code>when</code>",
                 "Web results with title, URL, host, description, snippet, "
                 "score and rich metadata"),
                ("<code>GET /api/images</code>",
                 "<code>q</code>, <code>limit</code> (1–100), <code>page</code>",
                 "Image results with source page, dimensions and alt text"),
                ("<code>GET /api/videos</code>",
                 "<code>q</code>, <code>limit</code> (1–50), <code>page</code>",
                 "Video embeds with platform, id and thumbnail"),
                ("<code>GET /api/suggest</code>", "<code>q</code>",
                 "Autocomplete completions drawn from indexed titles"),
                ("<code>GET /api/answer</code>", "<code>q</code>",
                 "The instant answer for a query, if there is one"),
                ("<code>GET /api/define</code>", "<code>w</code>",
                 "A dictionary entry"),
                ("<code>GET /api/unfurl</code>", "<code>url</code>",
                 "Open Graph, Twitter card, JSON-LD and oEmbed metadata for one "
                 "URL"),
                ("<code>GET /api/similar</code>",
                 "<code>src</code>, <code>page</code>, <code>host</code>",
                 "Images related to a given image"),
                ("<code>GET /api/stats</code>", "—",
                 "Index size, host count, frontier depth, categories"),
            ),
        )),
        Section("example", "Example", _p("A request:")
                + "<pre><code>curl 'https://example.com/api/search?q=rust+ownership&amp;limit=3'"
                  "</code></pre>"
                + _p("And the shape that comes back:")
                + """<pre><code>{
  "query": "rust ownership",
  "total": 42,
  "page": 1,
  "limit": 3,
  "answer": null,
  "results": [
    {
      "title": "Understanding Ownership",
      "url": "https://doc.rust-lang.org/book/ch04-00-understanding-ownership.html",
      "host": "doc.rust-lang.org",
      "description": "Ownership is Rust's most unique feature…",
      "snippet": "Ownership is a set of rules that govern…",
      "score": 3.4127,
      "meta": {
        "site_name": "The Rust Programming Language",
        "image": "https://doc.rust-lang.org/…/og.png",
        "published": "2024-02-08"
      }
    }
  ]
}</code></pre>"""),
        Section("terms", "Using it well", _ul(
            "No key is needed and no registration exists.",
            "Requests are rate-limited per IP address. Cache what you fetch.",
            "Send a descriptive <code>User-Agent</code> so problems can be "
            "traced to a project rather than to an address.",
            "Responses are CORS-enabled for GET, so browser clients work "
            "without a proxy.",
            "The index is a snapshot of a crawl. Coverage is partial and "
            "results change as the crawler runs.",
        )),
    ],
)


PAGES: tuple[Page, ...] = (
    ABOUT, HELP, API_DOCS, PRIVACY, TERMS, COOKIES, OPT_OUT, DMCA,
    ACCESSIBILITY, SECURITY,
)

_BY_SLUG = {page.slug: page for page in PAGES}

# The order these appear in the footer.
LEGAL_NAV = ("about", "help", "api-docs", "privacy", "terms", "cookies",
             "how-to-opt-out", "dmca", "accessibility", "security")


def page_by_slug(slug: str) -> Page | None:
    return _BY_SLUG.get(slug)
