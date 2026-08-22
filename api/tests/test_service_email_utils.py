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
    assert _parse_name_segments('{"text": "Sample"}') is None


def test_parse_name_segments_valid_list() -> None:
    raw = '[{"text": "Sample", "font": "Inter", "bold": true}]'
    result = _parse_name_segments(raw)
    assert result is not None
    assert len(result) == 1
    assert result[0]["text"] == "Sample"
    assert result[0]["bold"] is True


def test_render_site_name_html_no_style() -> None:
    name_html, fonts_url = _render_site_name_html("Sample", None)
    assert name_html == "Sample"
    assert fonts_url == ""


def test_render_site_name_html_invalid_style_falls_back() -> None:
    name_html, fonts_url = _render_site_name_html("Sample", "not-json")
    assert name_html == "Sample"
    assert fonts_url == ""


def test_render_site_name_html_plain_text_segment_no_span() -> None:
    style = '[{"text": "Hello"}]'
    name_html, fonts_url = _render_site_name_html("Hello", style)
    assert name_html == "Hello"
    assert fonts_url == ""


def test_render_site_name_html_bold_segment() -> None:
    style = '[{"text": "Sam", "bold": true}, {"text": "ple"}]'
    name_html, _ = _render_site_name_html("Sample", style)
    assert "font-weight: 700" in name_html
    assert "Sam" in name_html
    assert "ple" in name_html


def test_render_site_name_html_color_segment() -> None:
    style = '[{"text": "Sample", "color": "#ff0000"}]'
    name_html, _ = _render_site_name_html("Sample", style)
    assert "color: #ff0000" in name_html


def test_render_site_name_html_italic_segment() -> None:
    style = '[{"text": "Sample", "italic": true}]'
    name_html, _ = _render_site_name_html("Sample", style)
    assert "font-style: italic" in name_html


def test_render_site_name_html_font_generates_google_url() -> None:
    style = '[{"text": "Wik", "font": "Poppins"}, {"text": "INT", "font": "Inter"}]'
    name_html, fonts_url = _render_site_name_html("Sample", style)
    assert "fonts.googleapis.com" in fonts_url
    assert "Poppins" in fonts_url
    assert "Inter" in fonts_url


def test_render_site_name_html_deduplicates_fonts() -> None:
    style = '[{"text": "A", "font": "Inter"}, {"text": "B", "font": "Inter"}]'
    _, fonts_url = _render_site_name_html("AB", style)
    assert fonts_url.count("Inter") == 1


def test_render_site_name_html_font_with_spaces_encodes_plus() -> None:
    style = '[{"text": "Sample", "font": "Open Sans"}]'
    _, fonts_url = _render_site_name_html("Sample", style)
    assert "Open+Sans" in fonts_url


def test_render_code_box_html_contains_code_and_bgcolor() -> None:
    from app.services.email import _render_code_box_html

    html = _render_code_box_html("ABCD1234")
    assert "ABCD1234" in html
    assert 'bgcolor="#161826"' in html
    assert "#ffffff" in html


def test_html_to_plain_preserves_links() -> None:
    from app.core.events.email import _html_to_plain

    html = '<p>Click <a href="https://intellect.clubcode.fr/verify">here</a> to sign in.</p>'
    plain = _html_to_plain(html)
    assert "https://intellect.clubcode.fr/verify" in plain
    assert "Click here" in plain
