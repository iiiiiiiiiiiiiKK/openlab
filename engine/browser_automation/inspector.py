"""Page inspection: discover navigation, interactive elements, forms and links.

Everything is read from the live DOM of the running project; nothing is invented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DISCOVERY_JS = """
() => {
  const visible = (el) => {
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 2 && rect.height > 2;
  };
  const label = (el) => {
    return (el.innerText || el.getAttribute('aria-label') || el.getAttribute('placeholder')
            || el.getAttribute('title') || el.getAttribute('name') || el.value || '').trim().slice(0, 120);
  };
  const selectorFor = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    const testId = el.getAttribute('data-testid');
    if (testId) return `[data-testid="${testId}"]`;
    const name = el.getAttribute('name');
    if (name) return `${el.tagName.toLowerCase()}[name="${name}"]`;
    let path = [];
    let node = el;
    let depth = 0;
    while (node && node.nodeType === 1 && depth < 4) {
      let part = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter((c) => c.tagName === node.tagName);
        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      }
      path.unshift(part);
      node = node.parentElement;
      depth += 1;
    }
    return path.join(' > ');
  };
  const nav = Array.from(document.querySelectorAll('a[href], nav a, [role="navigation"] a'))
    .filter(visible)
    .map((a) => ({ text: label(a), href: a.href, selector: selectorFor(a) }))
    .filter((n) => n.text && n.href)
    .slice(0, 40);
  const buttons = Array.from(document.querySelectorAll('button, [role="button"], input[type="submit"], input[type="button"]'))
    .filter(visible)
    .map((b) => ({ text: label(b), selector: selectorFor(b), type: b.getAttribute('type') || 'button' }))
    .filter((b) => b.text)
    .slice(0, 40);
  const inputs = Array.from(document.querySelectorAll('input, textarea, select'))
    .filter(visible)
    .map((i) => ({
      tag: i.tagName.toLowerCase(),
      type: i.getAttribute('type') || (i.tagName.toLowerCase() === 'textarea' ? 'textarea' : 'text'),
      name: i.getAttribute('name'),
      placeholder: i.getAttribute('placeholder'),
      selector: selectorFor(i),
      label: label(i),
    }))
    .slice(0, 40);
  const headings = Array.from(document.querySelectorAll('h1, h2, h3'))
    .filter(visible)
    .map((h) => ({ level: h.tagName.toLowerCase(), text: (h.innerText || '').trim().slice(0, 160) }))
    .filter((h) => h.text)
    .slice(0, 30);
  const forms = Array.from(document.querySelectorAll('form')).filter(visible).map((f) => ({
    action: f.getAttribute('action') || '',
    method: (f.getAttribute('method') || 'get').toLowerCase(),
    fields: Array.from(f.querySelectorAll('input, textarea, select')).length,
    selector: selectorFor(f),
  })).slice(0, 10);
  return {
    url: location.href,
    title: document.title,
    lang: document.documentElement.lang || null,
    textLength: (document.body ? document.body.innerText.length : 0),
    nav,
    buttons,
    inputs,
    headings,
    forms,
    images: Array.from(document.images).filter(visible).length,
    canvas: document.querySelectorAll('canvas').length,
    svg: document.querySelectorAll('svg').length,
    scripts: document.querySelectorAll('script[src]').length,
    metaDescription: (document.querySelector('meta[name="description"]') || {}).content || null,
    bodyExcerpt: (document.body ? document.body.innerText.slice(0, 1200) : ''),
  };
}
"""


@dataclass
class PageSnapshot:
    url: str
    title: str
    headings: list[dict] = field(default_factory=list)
    nav: list[dict] = field(default_factory=list)
    buttons: list[dict] = field(default_factory=list)
    inputs: list[dict] = field(default_factory=list)
    forms: list[dict] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    text_length: int = 0
    body_excerpt: str = ""

    def as_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "headings": self.headings,
            "nav": self.nav,
            "buttons": self.buttons,
            "inputs": self.inputs,
            "forms": self.forms,
            "meta": self.meta,
            "text_length": self.text_length,
            "body_excerpt": self.body_excerpt[:800],
        }

    def feature_hints(self) -> list[str]:
        """Human-readable feature list derived only from observed elements."""
        hints: list[str] = []
        for heading in self.headings[:6]:
            if heading.get("text"):
                hints.append(f"{heading['level'].upper()}: {heading['text']}")
        for nav in self.nav[:8]:
            hints.append(f"nav → {nav.get('text')}")
        for button in self.buttons[:8]:
            hints.append(f"button → {button.get('text')}")
        for form in self.forms[:4]:
            hints.append(f"form ({form.get('fields')} fields, {form.get('method')})")
        return hints[:24]


async def inspect_page(page) -> PageSnapshot:
    data = await page.evaluate(DISCOVERY_JS)
    return PageSnapshot(
        url=data.get("url") or page.url,
        title=data.get("title") or "",
        headings=data.get("headings") or [],
        nav=data.get("nav") or [],
        buttons=data.get("buttons") or [],
        inputs=data.get("inputs") or [],
        forms=data.get("forms") or [],
        meta={
            "lang": data.get("lang"),
            "description": data.get("metaDescription"),
            "images": data.get("images"),
            "canvas": data.get("canvas"),
            "svg": data.get("svg"),
            "scripts": data.get("scripts"),
        },
        text_length=int(data.get("textLength") or 0),
        body_excerpt=data.get("bodyExcerpt") or "",
    )


class ConsoleCollector:
    """Collect console messages and failed network requests for a page."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.network_errors: list[dict] = []
        self.responses: list[dict] = []
        self._attached = False

    def attach(self, page) -> None:
        if self._attached:
            return
        self._attached = True

        def on_console(message) -> None:
            try:
                location = message.location or {}
            except Exception:  # noqa: BLE001
                location = {}
            self.messages.append(
                {
                    "type": message.type,
                    "text": (message.text or "")[:2000],
                    "url": location.get("url"),
                    "line": location.get("lineNumber"),
                }
            )
            if len(self.messages) > 500:
                del self.messages[:100]

        def on_page_error(error) -> None:
            self.messages.append({"type": "pageerror", "text": str(error)[:2000], "url": None})

        def on_response(response) -> None:
            try:
                status = response.status
            except Exception:  # noqa: BLE001
                return
            if status >= 400:
                self.network_errors.append(
                    {"url": response.url[:600], "status": status, "method": response.request.method}
                )
                if len(self.network_errors) > 200:
                    del self.network_errors[:50]

        def on_request_failed(request) -> None:
            failure = request.failure
            self.network_errors.append(
                {
                    "url": request.url[:600],
                    "status": None,
                    "method": request.method,
                    "error": (failure if isinstance(failure, str) else str(failure))[:300],
                }
            )

        page.on("console", on_console)
        page.on("pageerror", on_page_error)
        page.on("response", on_response)
        page.on("requestfailed", on_request_failed)

    @property
    def console_errors(self) -> list[dict]:
        return [m for m in self.messages if m.get("type") in ("error", "pageerror")]

    def console_errors_since(self, message_index: int) -> list[dict]:
        """Console errors recorded after ``message_index`` (per-step attribution).

        ``ConsoleCollector`` is attached once per browser session, so without this the
        errors of every earlier step leaked into later steps and made verification
        results wrong in both directions.
        """
        start = max(0, int(message_index or 0))
        return [
            m for m in self.messages[start:] if m.get("type") in ("error", "pageerror")
        ]

    def summary(self) -> dict:
        return {
            "console_total": len(self.messages),
            "console_errors": len(self.console_errors),
            "network_errors": len(self.network_errors),
            "sample_console_errors": self.console_errors[:10],
            "sample_network_errors": self.network_errors[:10],
        }

    def reset(self) -> None:
        self.messages.clear()
        self.network_errors.clear()
