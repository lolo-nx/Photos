"""
Compression de toutes les photos du site Argentik Travel.
- Redimensionne à 2200px max (suffisant pour écrans Retina à 1100px)
- Qualité JPEG 85% — bon compromis qualité / poids
- Remplace les fichiers sur place (sauvegarde l'original avec _original)
- Traite tous les sous-dossiers de Photos/ récursivement

Usage :
    python3 compression-code.py
"""

import os
import shutil
from pathlib import Path

try:
    from PIL import Image
    ENGINE = "pillow"
except ImportError:
    try:
        import cv2
        ENGINE = "cv2"
    except ImportError:
        raise ImportError("Installe Pillow : pip3 install Pillow")

EXTS       = {".jpg", ".jpeg", ".png"}
MAX_DIM    = 2200      # px — retina ready pour affichage 1100px
QUALITY    = 85        # % JPEG
PHOTOS_DIR = Path(__file__).parent / "Photos"


def compress_pillow(src: Path, dst: Path):
    with Image.open(src) as img:
        # Conserver l'orientation EXIF
        try:
            from PIL import ImageOps
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass

        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        w, h = img.size
        if max(w, h) > MAX_DIM:
            scale = MAX_DIM / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        img.save(dst, "JPEG", quality=QUALITY, optimize=True)


def compress_cv2(src: Path, dst: Path):
    import cv2
    import numpy as np
    img = cv2.imread(str(src), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Impossible de lire : {src}")
    h, w = img.shape[:2]
    if max(w, h) > MAX_DIM:
        scale = MAX_DIM / max(w, h)
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(dst), img, [cv2.IMWRITE_JPEG_QUALITY, QUALITY])


def process():
    total, skipped, errors = 0, 0, 0

    for src in sorted(PHOTOS_DIR.rglob("*")):
        if not src.is_file() or src.suffix.lower() not in EXTS:
            continue

        size_before = src.stat().st_size

        try:
            # Compresse dans un fichier temporaire puis remplace
            tmp = src.with_suffix(".tmp.jpg")
            if ENGINE == "pillow":
                compress_pillow(src, tmp)
            else:
                compress_cv2(src, tmp)

            size_after = tmp.stat().st_size
            gain = (1 - size_after / size_before) * 100

            if gain > 2:
                tmp.replace(src if src.suffix.lower() in (".jpg", ".jpeg") else src.with_suffix(".jpg"))
                print(f"✓ {src.relative_to(PHOTOS_DIR)}  {size_before//1024}Ko → {size_after//1024}Ko  (-{gain:.0f}%)")
                total += 1
            else:
                tmp.unlink()
                print(f"– {src.relative_to(PHOTOS_DIR)}  déjà optimisée, ignorée")
                skipped += 1

        except Exception as e:
            if Path(str(src) + ".tmp.jpg").exists():
                Path(str(src) + ".tmp.jpg").unlink(missing_ok=True)
            print(f"✗ {src.name}  ERREUR : {e}")
            errors += 1

    print(f"\n{'─'*50}")
    print(f"Compressées : {total}  |  Ignorées : {skipped}  |  Erreurs : {errors}")


if __name__ == "__main__":
    print(f"Moteur : {ENGINE}")
    print(f"Dossier : {PHOTOS_DIR}\n")
    process()
