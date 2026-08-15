import io
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from app.core.media.avatar_processor import process_avatar


def test_process_avatar_returns_webp_bytes(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (100, 100), color="blue").save(source)

    result_bytes = process_avatar(source, size=64, quality=80)
    assert isinstance(result_bytes, bytes)
    assert len(result_bytes) > 0

    # Verify output is valid 64x64 WebP
    with Image.open(io.BytesIO(result_bytes)) as out_img:
        assert out_img.format == "WEBP"
        assert out_img.size == (64, 64)


def test_process_avatar_pixel_limit(tmp_path):
    source = tmp_path / "large.png"
    Image.new("RGB", (1, 1)).save(source)

    mock_img = MagicMock()
    mock_img.width = 4000
    mock_img.height = 3000
    mock_img.__enter__.return_value = mock_img

    with patch("PIL.Image.open", return_value=mock_img):
        with pytest.raises(ValueError, match="Avatar exceeds pixel limit"):
            process_avatar(source)


def test_process_avatar_rejects_animation(tmp_path):
    source = tmp_path / "animated.gif"
    first = Image.new("RGB", (16, 16), color="red")
    second = Image.new("RGB", (16, 16), color="blue")
    first.save(source, format="GIF", save_all=True, append_images=[second])

    with pytest.raises(ValueError, match="Animated and multi-frame avatars are not supported"):
        process_avatar(source)
