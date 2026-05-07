import shutil
from pathlib import Path

from app.core.file_security._pdf import _strip_pdf_from_path, check_pdf_safety

src = Path("/home/psders/Downloads/formabash.pdf")
tmp = Path("test_formabash.pdf")
shutil.copyfile(src, tmp)

print("Before stripping:")
try:
    check_pdf_safety(tmp)
    print("Safe!")
except ValueError as e:
    print(f"Error: {e}")

stripped = _strip_pdf_from_path(tmp)

print("\nAfter stripping:")
try:
    check_pdf_safety(stripped)
    print("Safe!")
except ValueError as e:
    print(f"Error: {e}")

stripped.unlink()
tmp.unlink()
