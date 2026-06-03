"""SVG security: allowlist-based sanitizer.

Parses SVG as XML (via defusedxml) and walks every element/attribute looking
for dangerous active content: script elements, event handlers, javascript: URIs,
external URLs, and CSS injection vectors.
"""

import io
import logging
import re
from typing import cast

import scour.scour as scour
from defusedxml import ElementTree

logger = logging.getLogger(__name__)

# Elements whose presence in an SVG is unconditionally rejected.
_SVG_BLOCKED_ELEMENTS = frozenset(
    {
        "script",
        "foreignobject",  # can embed arbitrary HTML
        "iframe",
        "embed",
        "object",
        "handler",  # SVG 1.2 Tiny event handler element
        "style",  # CSS injection vector (url(), @import for tracking/SSRF)
    }
)

# Attribute value patterns that indicate active/dangerous content.
_SVG_DANGEROUS_VALUE_RE = re.compile(
    r"(javascript:|vbscript:|data:text/html|data:application/)",
    re.IGNORECASE,
)

# href / xlink:href values that resolve to data URIs containing SVG/XML
# (can embed scripts inside nested SVGs loaded via <use>).
_SVG_DATA_SVG_RE = re.compile(r"data:\s*image/svg\+xml", re.IGNORECASE)

# External URL schemes and protocol-relative URLs in href/src/action attributes
# trigger outbound fetches (SSRF, tracking pixels, resource loading).
_SVG_EXTERNAL_URL_RE = re.compile(
    r"^[a-zA-Z][a-zA-Z0-9+\-.]*:|^//",
    re.IGNORECASE,
)

# CSS injection vectors in <style> elements or style attributes.
# Blocks url(), @import, and legacy IE expression().
# Includes check for CSS comments (/*...*/) inside url() to prevent evasion.
_SVG_CSS_INJECTION_RE = re.compile(
    r"url\s*\(|@import|expression\s*\(|\\|/\*.*\*/",
    re.IGNORECASE,
)


class SvgSecurityError(ValueError):
    """Raised when an SVG contains potentially malicious content."""


def check_svg_safety(file_bytes: bytes, filename: str = "") -> None:
    """Validate an SVG using an allowlist approach.

    Parses the SVG as XML with defusedxml (protects against entity/bomb attacks),
    then walks every element and attribute looking for dangerous content.
    """
    check_svg_safety_stream(io.BytesIO(file_bytes), filename)


def check_svg_safety_stream(file_obj: io.IOBase, filename: str = "") -> None:
    try:
        context = ElementTree.iterparse(file_obj, events=("start", "end"))

        for event, element in context:
            if event == "start":
                local = element.tag
                if "{" in local:
                    local = local.split("}", 1)[1]
                local_lower = local.lower()

                if local_lower in _SVG_BLOCKED_ELEMENTS:
                    logger.warning("SVG blocked element <%s> in %s", local, filename)
                    raise SvgSecurityError(
                        f"SVG files containing <{local}> elements are not allowed."
                    )

                for attr_name, attr_value in element.attrib.items():
                    bare_attr = attr_name
                    if "{" in bare_attr:
                        bare_attr = bare_attr.split("}", 1)[1]
                    bare_attr_lower = bare_attr.lower()

                    if bare_attr_lower.startswith("on"):
                        logger.warning("SVG event handler %s in %s", bare_attr, filename)
                        raise SvgSecurityError(
                            "SVG files containing event handler attributes are not allowed."
                        )

                    if bare_attr_lower == "style" and _SVG_CSS_INJECTION_RE.search(attr_value):
                        logger.warning(
                            "SVG CSS injection in style attribute of <%s> in %s", local, filename
                        )
                        raise SvgSecurityError(
                            "SVG files containing suspicious CSS injection are not allowed."
                        )

                    if _SVG_DANGEROUS_VALUE_RE.search(attr_value):
                        logger.warning("SVG dangerous URI in attr %s of %s", bare_attr, filename)
                        raise SvgSecurityError(
                            "SVG files containing active content or dangerous URI handlers are not allowed."
                        )

                    if _SVG_DATA_SVG_RE.search(attr_value):
                        logger.warning(
                            "SVG data:image/svg+xml in attr %s of %s", bare_attr, filename
                        )
                        raise SvgSecurityError(
                            "SVG files referencing nested SVG content via data URIs are not allowed."
                        )

                    if bare_attr_lower in ("href", "xlink:href", "src", "action"):
                        if _SVG_EXTERNAL_URL_RE.search(attr_value):
                            logger.warning("SVG external URL in attr %s of %s", bare_attr, filename)
                            raise SvgSecurityError(
                                "SVG files containing external URLs are not allowed."
                            )
            elif event == "end":
                if element.text and "<script" in element.text.lower():
                    raise SvgSecurityError(
                        "SVG files containing encoded <script> tags are not allowed."
                    )
                if element.tail and "<script" in element.tail.lower():
                    raise SvgSecurityError(
                        "SVG files containing encoded <script> tags are not allowed."
                    )

                element.clear()

    except ElementTree.ParseError as exc:
        raise SvgSecurityError(f"SVG failed XML parsing: {exc}") from exc
    except SvgSecurityError:
        raise
    except Exception as exc:
        raise SvgSecurityError(f"SVG security validation failed: {exc}") from exc


def _optimize_svg(file_bytes: bytes) -> bytes:
    """Optimise SVG markup with scour (remove redundant attributes, whitespace, etc.)."""
    options = scour.generateDefaultOptions()
    options.remove_descriptive_elements = True
    options.enable_viewboxing = True
    options.indent_type = "none"
    options.newlines = False
    options.strip_xml_prolog = False  # keep declaration for XML parsers
    options.strip_comments = True
    optimised = cast(str, scour.scourString(file_bytes.decode("utf-8", errors="replace"), options))
    return optimised.encode("utf-8")
