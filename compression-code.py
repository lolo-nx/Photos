import os
import cv2
from pathlib import Path

# Extensions supportées
EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}

def compress_image(
    in_path: str,
    out_path: str,
    max_dim: int = 1920,      # comme beaucoup de compresseurs en ligne
    quality: int = 82,        # bon compromis "iLoveIMG-like"
    out_format: str = "jpg"   # "jpg" ou "webp"
):
    in_path = Path(in_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    img = cv2.imread(str(in_path), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Impossible de lire: {in_path}")

    h, w = img.shape[:2]
    m = max(h, w)

    # 1) Redimensionner si nécessaire
    if m > max_dim:
        scale = max_dim / float(m)
        new_w = int(round(w * scale))
        new_h = int(round(h * scale))
        img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)

    # 2) Encoder avec une qualité fixe
    out_format = out_format.lower().strip(".")
    if out_format in ("jpg", "jpeg"):
        params = [cv2.IMWRITE_JPEG_QUALITY, int(quality)]
        out_file = out_path.with_suffix(".jpg")
    elif out_format == "webp":
        params = [cv2.IMWRITE_WEBP_QUALITY, int(quality)]
        out_file = out_path.with_suffix(".webp")
    else:
        raise ValueError("out_format doit être 'jpg' ou 'webp'")

    ok = cv2.imwrite(str(out_file), img, params)
    if not ok:
        raise RuntimeError("Échec d'écriture du fichier")

    return str(out_file)

def compress_folder(in_dir: str, out_dir: str, max_dim=1920, quality=82, out_format="jpg"):
    in_dir = Path(in_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for p in in_dir.iterdir():
        if p.is_file() and p.suffix.lower() in EXTS:
            out_path = out_dir / p.stem
            out_file = compress_image(
                in_path=str(p),
                out_path=str(out_path),
                max_dim=max_dim,
                quality=quality,
                out_format=out_format
            )
            print(f"{p.name} -> {Path(out_file).name}")

if __name__ == "__main__":
    # Exemple d’usage:
    # compress_image("photo.png", "out/photo", max_dim=1920, quality=82, out_format="webp")
    compress_folder("images", "compressed", max_dim=1920, quality=82, out_format="webp")
