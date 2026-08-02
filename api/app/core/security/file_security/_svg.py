"""SVG security: allowlist-based sanitizer.

Parses SVG as XML (via defusedxml) and walks every element/attribute looking
for dangerous active content: script elements, event handlers, javascript: URIs,
external URLs, and CSS injection vectors.
"""

import io
import logging
import re
from typing import IO, cast

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
        # SMIL animation can mutate otherwise-safe attributes such as a local
        # image href into an external URL after validation has completed.
        "animate",
        "animatemotion",
        "animatetransform",
        "discard",
        "set",
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

# CSS values are decoded before validation so escaped schemes and function names
# cannot bypass the policy. Local paint-server references remain supported.
_CSS_ESCAPE_RE = re.compile(r"\\([0-9a-fA-F]{1,6})(?:\s)?|\\(.)", re.DOTALL)
_SVG_CSS_INJECTION_RE = re.compile(r"@import|expression\s*\(", re.IGNORECASE)
_SVG_URL_VALUE_RE = re.compile(r"url\s*\((.*?)\)", re.IGNORECASE | re.DOTALL)
_SVG_URL_FUNCTION_RE = re.compile(r"url\s*\(", re.IGNORECASE)
_SVG_LOCAL_URL_RE = re.compile(r"^url\s*\(\s*#[A-Za-z_][\w:.-]*\s*\)$", re.IGNORECASE)
_SVG_LOCAL_HREF_RE = re.compile(r"^#[A-Za-z_][\w:.-]*$")


def _decode_css_escapes(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        if match.group(1):
            try:
                return chr(int(match.group(1), 16))
            except (ValueError, OverflowError):
                return ""
        return match.group(2) or ""

    return _CSS_ESCAPE_RE.sub(replace, value)


def _validate_svg_style(value: str) -> str:
    """Return normalized CSS after rejecting active or external resources."""
    without_comments = re.sub(r"/\*.*?\*/", "", value, flags=re.DOTALL)
    normalized = _decode_css_escapes(without_comments).strip()
    if _SVG_CSS_INJECTION_RE.search(normalized):
        raise SvgSecurityError("SVG files containing suspicious CSS are not allowed.")
    if _SVG_DANGEROUS_VALUE_RE.search(normalized) or _SVG_DATA_SVG_RE.search(normalized):
        raise SvgSecurityError("SVG files containing dangerous CSS URIs are not allowed.")
    for match in _SVG_URL_VALUE_RE.finditer(normalized):
        target = match.group(1).strip().strip("\"'")
        if not _SVG_LOCAL_HREF_RE.fullmatch(target):
            raise SvgSecurityError("SVG files containing external CSS resources are not allowed.")
    return normalized


class SvgSecurityError(ValueError):
    """Raised when an SVG contains potentially malicious content."""


def check_svg_safety(file_bytes: bytes, filename: str = "") -> None:
    """Validate an SVG using an allowlist approach.

    Parses the SVG as XML with defusedxml (protects against entity/bomb attacks),
    then walks every element and attribute looking for dangerous content.
    """
    check_svg_safety_stream(io.BytesIO(file_bytes), filename)


def check_svg_safety_stream(file_obj: IO[bytes], filename: str = "") -> None:
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
                    normalized_value = attr_value.strip()
                    security_value = _decode_css_escapes(normalized_value)

                    if bare_attr_lower == "base":
                        raise SvgSecurityError(
                            "SVG files containing XML base URI overrides are not allowed."
                        )

                    if bare_attr_lower.startswith("on"):
                        logger.warning("SVG event handler %s in %s", bare_attr, filename)
                        raise SvgSecurityError(
                            "SVG files containing event handler attributes are not allowed."
                        )

                    if bare_attr_lower == "style":
                        try:
                            security_value = _validate_svg_style(normalized_value)
                        except SvgSecurityError:
                            logger.warning(
                                "SVG CSS injection in style attribute of <%s> in %s",
                                local,
                                filename,
                            )
                            raise

                    if _SVG_DANGEROUS_VALUE_RE.search(security_value):
                        logger.warning("SVG dangerous URI in attr %s of %s", bare_attr, filename)
                        raise SvgSecurityError(
                            "SVG files containing active content or dangerous URI handlers are not allowed."
                        )

                    if _SVG_DATA_SVG_RE.search(security_value):
                        logger.warning(
                            "SVG data:image/svg+xml in attr %s of %s", bare_attr, filename
                        )
                        raise SvgSecurityError(
                            "SVG files referencing nested SVG content via data URIs are not allowed."
                        )

                    if (
                        bare_attr_lower != "style"
                        and _SVG_URL_FUNCTION_RE.search(security_value)
                        and not _SVG_LOCAL_URL_RE.fullmatch(security_value)
                    ):
                        logger.warning("SVG external url() in attr %s of %s", bare_attr, filename)
                        raise SvgSecurityError(
                            "SVG files containing external CSS resource URLs are not allowed."
                        )

                    if bare_attr_lower in ("href", "xlink:href", "src", "action"):
                        # References may only target an element in this document.
                        # Relative and absolute paths are external resources too;
                        # checking URL schemes alone would allow `../asset` and
                        # `/asset` to escape this policy.
                        if security_value and not _SVG_LOCAL_HREF_RE.fullmatch(security_value):
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
