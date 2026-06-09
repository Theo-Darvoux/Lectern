import zlib
from pathlib import Path

import pikepdf

from app.core.file_security._pdf import _pikepdf_repack_streams


def test_repack_streams_preserves_smask(tmp_path: Path):
    # 1. Create a dummy PDF with an image having an SMask
    pdf = pikepdf.new()

    # Create main image (RGB)
    img_w, img_h = 100, 100
    img_data = bytes([255, 0, 0] * (img_w * img_h))  # Red image

    # Create smask (alpha channel, grayscale)
    # Make half transparent (0), half opaque (255)
    smask_data = bytes([0] * (img_w * img_h // 2) + [255] * (img_w * img_h // 2))

    smask_stream = pdf.make_stream(zlib.compress(smask_data))
    smask_stream.Type = pikepdf.Name("/XObject")
    smask_stream.Subtype = pikepdf.Name("/Image")
    smask_stream.Width = img_w
    smask_stream.Height = img_h
    smask_stream.ColorSpace = pikepdf.Name("/DeviceGray")
    smask_stream.BitsPerComponent = 8
    smask_stream.Filter = pikepdf.Name("/FlateDecode")

    img_stream = pdf.make_stream(zlib.compress(img_data))
    img_stream.Type = pikepdf.Name("/XObject")
    img_stream.Subtype = pikepdf.Name("/Image")
    img_stream.Width = img_w
    img_stream.Height = img_h
    img_stream.ColorSpace = pikepdf.Name("/DeviceRGB")
    img_stream.BitsPerComponent = 8
    img_stream.Filter = pikepdf.Name("/FlateDecode")
    img_stream.SMask = smask_stream

    # Create page
    xobj = pikepdf.Dictionary()
    xobj[pikepdf.Name("/Im1")] = img_stream

    content = b"q 100 0 0 100 0 0 cm /Im1 Do Q"
    page_dict = pikepdf.Dictionary(
        Type=pikepdf.Name("/Page"),
        MediaBox=[0, 0, 100, 100],
        Resources=pikepdf.Dictionary(XObject=xobj),
        Contents=pdf.make_stream(content),
    )
    pdf.pages.append(pikepdf.Page(pdf.make_indirect(page_dict)))

    input_pdf_path = tmp_path / "input.pdf"
    pdf.save(input_pdf_path)
    pdf.close()

    # 2. Run _pikepdf_repack_streams
    output_pdf_path = tmp_path / "output.pdf"
    success = _pikepdf_repack_streams(input_pdf_path, str(output_pdf_path), quality=75)

    # 3. Assert output
    assert output_pdf_path.exists()

    with pikepdf.open(output_pdf_path) as out_pdf:
        out_page = out_pdf.pages[0]
        out_images = out_page.images
        assert len(out_images) == 1

        for name, raw_img in out_images.items():
            assert "/SMask" in raw_img
            # Check the smask itself
            out_smask = raw_img.SMask
            assert out_smask.Width == img_w
            assert out_smask.Height == img_h
            assert out_smask.ColorSpace == pikepdf.Name("/DeviceGray")


def test_repack_streams_preserves_chroma_mask(tmp_path: Path):
    pdf = pikepdf.new()

    img_w, img_h = 100, 100
    # Create RGB image where half is green [0, 255, 0] and half is red [255, 0, 0]
    img_data = bytes(([0, 255, 0] * (img_w * img_h // 2)) + ([255, 0, 0] * (img_w * img_h // 2)))

    img_stream = pdf.make_stream(zlib.compress(img_data))
    img_stream.Type = pikepdf.Name("/XObject")
    img_stream.Subtype = pikepdf.Name("/Image")
    img_stream.Width = img_w
    img_stream.Height = img_h
    img_stream.ColorSpace = pikepdf.Name("/DeviceRGB")
    img_stream.BitsPerComponent = 8
    img_stream.Filter = pikepdf.Name("/FlateDecode")

    # Green is masked out: [0, 0, 255, 255, 0, 0]
    img_stream.Mask = pikepdf.Array([0, 0, 255, 255, 0, 0])

    xobj = pikepdf.Dictionary()
    xobj[pikepdf.Name("/Im1")] = img_stream

    content = b"q 100 0 0 100 0 0 cm /Im1 Do Q"
    page_dict = pikepdf.Dictionary(
        Type=pikepdf.Name("/Page"),
        MediaBox=[0, 0, 100, 100],
        Resources=pikepdf.Dictionary(XObject=xobj),
        Contents=pdf.make_stream(content),
    )
    pdf.pages.append(pikepdf.Page(pdf.make_indirect(page_dict)))

    input_pdf_path = tmp_path / "input.pdf"
    pdf.save(input_pdf_path)
    pdf.close()

    output_pdf_path = tmp_path / "output.pdf"
    success = _pikepdf_repack_streams(input_pdf_path, str(output_pdf_path), quality=75)

    assert output_pdf_path.exists()

    with pikepdf.open(output_pdf_path) as out_pdf:
        out_page = out_pdf.pages[0]
        out_images = out_page.images
        assert len(out_images) == 1

        for name, raw_img in out_images.items():
            # Chroma key mask must have been converted to /SMask
            assert "/SMask" in raw_img
            assert "/Mask" not in raw_img

            smask_raw = raw_img.SMask
            smask_data = smask_raw.read_bytes()

            # The green part (first half) should be transparent (0), red part opaque (255)
            assert smask_data[0] == 0
            assert smask_data[-1] == 255


def test_repack_streams_preserves_stencil_mask(tmp_path: Path):
    pdf = pikepdf.new()

    img_w, img_h = 100, 100
    img_data = bytes([255, 0, 0] * (img_w * img_h))

    # 1-bit stencil mask: 100 width -> 13 bytes per row -> 1300 bytes total
    mask_bytes = bytes([0xAA] * 1300)
    mask_stream = pdf.make_stream(zlib.compress(mask_bytes))
    mask_stream.Type = pikepdf.Name("/XObject")
    mask_stream.Subtype = pikepdf.Name("/Image")
    mask_stream.Width = img_w
    mask_stream.Height = img_h
    mask_stream.ColorSpace = pikepdf.Name("/DeviceGray")
    mask_stream.BitsPerComponent = 1
    mask_stream.Filter = pikepdf.Name("/FlateDecode")
    mask_stream.ImageMask = True

    img_stream = pdf.make_stream(zlib.compress(img_data))
    img_stream.Type = pikepdf.Name("/XObject")
    img_stream.Subtype = pikepdf.Name("/Image")
    img_stream.Width = img_w
    img_stream.Height = img_h
    img_stream.ColorSpace = pikepdf.Name("/DeviceRGB")
    img_stream.BitsPerComponent = 8
    img_stream.Filter = pikepdf.Name("/FlateDecode")
    img_stream.Mask = mask_stream

    xobj = pikepdf.Dictionary()
    xobj[pikepdf.Name("/Im1")] = img_stream

    content = b"q 100 0 0 100 0 0 cm /Im1 Do Q"
    page_dict = pikepdf.Dictionary(
        Type=pikepdf.Name("/Page"),
        MediaBox=[0, 0, 100, 100],
        Resources=pikepdf.Dictionary(XObject=xobj),
        Contents=pdf.make_stream(content),
    )
    pdf.pages.append(pikepdf.Page(pdf.make_indirect(page_dict)))

    input_pdf_path = tmp_path / "input.pdf"
    pdf.save(input_pdf_path)
    pdf.close()

    output_pdf_path = tmp_path / "output.pdf"
    success = _pikepdf_repack_streams(input_pdf_path, str(output_pdf_path), quality=75)

    assert output_pdf_path.exists()

    with pikepdf.open(output_pdf_path) as out_pdf:
        out_page = out_pdf.pages[0]
        out_images = out_page.images
        assert len(out_images) == 1

        for name, raw_img in out_images.items():
            # Stencil mask must have been converted to /SMask
            assert "/SMask" in raw_img
            assert "/Mask" not in raw_img
