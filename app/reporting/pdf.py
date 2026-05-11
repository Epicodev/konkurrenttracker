"""WeasyPrint-wrapper. HTML in, PDF ud.

Importeres lazily fordi WeasyPrint kræver cairo/pango via Nix/apt - kan fejle lokalt
på Mac uden brew. På Railway (Linux) virker det med nixpacks.toml's nixPkgs.
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path(__file__).parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
)


def render_html(template: str, payload: dict) -> str:
    return _jinja_env.get_template(template).render(**payload)


def render_pdf(template: str, payload: dict) -> bytes:
    """Render HTML-template til PDF-bytes. Kræver WeasyPrint installeret."""
    from weasyprint import HTML  # lazy import - kan fejle uden system-deps

    html = render_html(template, payload)
    return HTML(string=html).write_pdf()
