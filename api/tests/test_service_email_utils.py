"""Pure-function unit tests for app.services.email utility helpers."""

from app.services.email import _parse_name_segments, _render_site_name_html


def test_parse_name_segments_none_input() -> None:
    assert _parse_name_segments(None) is None


def test_parse_name_segments_empty_string() -> None:
    assert _parse_name_segments("") is None


def test_parse_name_segments_invalid_json() -> None:
    assert _parse_name_segments("not-json") is None


def test_parse_name_segments_empty_array() -> None:
    assert _parse_name_segments("[]") is None


def test_parse_name_segments_non_list_json() -> None:
    assert _parse_name_segments('{"text": "WikINT"}') is None


def test_parse_name_segments_valid_list() -> None:
    raw = '[{"text": "WikINT", "font": "Inter", "bold": true}]'
    result = _parse_name_segments(raw)
    assert result is not None
    assert len(result) == 1
    assert result[0]["text"] == "WikINT"
    assert result[0]["bold"] is True


def test_render_site_name_html_no_style() -> None:
    name_html, fonts_url = _render_site_name_html("WikINT", None)
    assert name_html == "WikINT"
    assert fonts_url == ""


def test_render_site_name_html_invalid_style_falls_back() -> None:
    name_html, fonts_url = _render_site_name_html("WikINT", "not-json")
    assert name_html == "WikINT"
    assert fonts_url == ""


def test_render_site_name_html_plain_text_segment_no_span() -> None:
    style = '[{"text": "Hello"}]'
    name_html, fonts_url = _render_site_name_html("Hello", style)
    assert name_html == "Hello"
    assert fonts_url == ""


def test_render_site_name_html_bold_segment() -> None:
    style = '[{"text": "Wik", "bold": true}, {"text": "INT"}]'
    name_html, _ = _render_site_name_html("WikINT", style)
    assert "font-weight: 700" in name_html
    assert "Wik" in name_html
    assert "INT" in name_html


def test_render_site_name_html_color_segment() -> None:
    style = '[{"text": "WikINT", "color": "#ff0000"}]'
    name_html, _ = _render_site_name_html("WikINT", style)
    assert "color: #ff0000" in name_html


def test_render_site_name_html_italic_segment() -> None:
    style = '[{"text": "WikINT", "italic": true}]'
    name_html, _ = _render_site_name_html("WikINT", style)
    assert "font-style: italic" in name_html


def test_render_site_name_html_font_generates_google_url() -> None:
    style = '[{"text": "Wik", "font": "Poppins"}, {"text": "INT", "font": "Inter"}]'
    name_html, fonts_url = _render_site_name_html("WikINT", style)
    assert "fonts.googleapis.com" in fonts_url
    assert "Poppins" in fonts_url
    assert "Inter" in fonts_url


def test_render_site_name_html_deduplicates_fonts() -> None:
    style = '[{"text": "A", "font": "Inter"}, {"text": "B", "font": "Inter"}]'
    _, fonts_url = _render_site_name_html("AB", style)
    assert fonts_url.count("Inter") == 1


def test_render_site_name_html_font_with_spaces_encodes_plus() -> None:
    style = '[{"text": "WikINT", "font": "Open Sans"}]'
    _, fonts_url = _render_site_name_html("WikINT", style)
    assert "Open+Sans" in fonts_url
