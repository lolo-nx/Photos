#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
DEDUP_DIRNAME = "_duplicates"

SECTION_DIRS = {
    "france-alpes": "Photos/France/Alpes",
    "france-bretagne": "Photos/France/Bretagne",
    "france-prefailles": "Photos/France/Préfailles",
    "france-vosges": "Photos/France/Vosges",
    "swiss-lausanne": "Photos/Suisse/Lausanne",
    "swiss-alpes": "Photos/Suisse/Lioson Lake",
    "italy": "Photos/Italie",
    "australia-melbourne": "Photos/Australie/Melbourne-city",
    "australia-great-ocean-road": "Photos/Australie/GreatOceanRoad",
    "australia-philip-island": "Photos/Australie/Philip-island",
    "australia-animaux": "Photos/Australie/Animaux",
    "australie-Roadtrip": "Photos/Australie/Roadtrip",
}


@dataclass(frozen=True)
class RenamePlan:
    src: Path
    dest: Path
    orientation: str


@dataclass(frozen=True)
class ImageInfo:
    path: Path
    orientation: str
    exif_ts: Optional[float]
    mtime: float


def _normalize_ext(ext: str) -> str:
    ext = ext.lower()
    if ext in {".jpg", ".jpeg"}:
        return ".jpg"
    return ext


def _parse_exif_datetime(value: str) -> Optional[datetime]:
    value = value.strip()
    if value == "<nil>" or not value:
        return None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _parse_sips_output(output: str) -> Tuple[int, int, Optional[datetime]]:
    width = height = None
    exif_dt = None
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("pixelWidth:"):
            width = int(line.split(":", 1)[1].strip())
        elif line.startswith("pixelHeight:"):
            height = int(line.split(":", 1)[1].strip())
        elif line.startswith("DateTimeOriginal:"):
            value = line.split(":", 1)[1].strip()
            exif_dt = _parse_exif_datetime(value)
    if width is None or height is None:
        raise ValueError("Impossible de lire les dimensions via sips.")
    return width, height, exif_dt


def get_image_info(path: Path) -> Tuple[int, int, Optional[float]]:
    result = subprocess.run(
        [
            "sips",
            "-g",
            "pixelWidth",
            "-g",
            "pixelHeight",
            "-g",
            "DateTimeOriginal",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"sips a échoué pour {path}: {result.stderr.strip()}")
    width, height, exif_dt = _parse_sips_output(result.stdout)
    exif_ts = exif_dt.timestamp() if exif_dt else None
    return width, height, exif_ts


def iter_image_dirs(root: Path) -> Iterable[Tuple[Path, List[Path]]]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != DEDUP_DIRNAME]
        dir_path = Path(dirpath)
        images = [
            dir_path / name
            for name in filenames
            if (dir_path / name).suffix.lower() in IMAGE_EXTS
        ]
        if images:
            yield dir_path, images


def _hash_thumbnail(path: Path, tmp_dir: Path) -> str:
    tmp_name = hashlib.sha1(str(path).encode("utf-8")).hexdigest() + ".bmp"
    tmp_path = tmp_dir / tmp_name
    try:
        result = subprocess.run(
            [
                "sips",
                "-Z",
                "64",
                "-s",
                "format",
                "bmp",
                str(path),
                "--out",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"sips a échoué pour {path}: {result.stderr.strip()}"
            )
        h = hashlib.sha256()
        with tmp_path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _bmp_ahash(path: Path, tmp_dir: Path, size: int = 8) -> str:
    tmp_name = hashlib.sha1(f"{path}-{size}".encode("utf-8")).hexdigest() + ".bmp"
    tmp_path = tmp_dir / tmp_name
    try:
        result = subprocess.run(
            [
                "sips",
                "-z",
                str(size),
                str(size),
                "-s",
                "format",
                "bmp",
                str(path),
                "--out",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"sips a échoué pour {path}: {result.stderr.strip()}"
            )
        data = tmp_path.read_bytes()
        offset = int.from_bytes(data[10:14], "little")
        width = int.from_bytes(data[18:22], "little", signed=True)
        height = int.from_bytes(data[22:26], "little", signed=True)
        bpp = int.from_bytes(data[28:30], "little")
        if bpp != 24:
            raise ValueError(f"Format BMP inattendu ({bpp} bpp) pour {path}")

        width = abs(width)
        height = abs(height)
        row_size = ((bpp * width + 31) // 32) * 4
        pixels = []
        for y in range(height):
            row_start = offset + y * row_size
            for x in range(width):
                i = row_start + x * 3
                b, g, r = data[i : i + 3]
                lum = (r * 299 + g * 587 + b * 114) // 1000
                pixels.append(lum)

        avg = sum(pixels) / len(pixels)
        bits = "".join("1" if p >= avg else "0" for p in pixels)
        return hex(int(bits, 2))[2:].zfill(len(bits) // 4)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _cleanup_tmp_dir(tmp_dir: Path) -> None:
    if not tmp_dir.exists():
        return
    for file_path in tmp_dir.iterdir():
        if file_path.is_file():
            file_path.unlink()
    try:
        tmp_dir.rmdir()
    except OSError:
        pass


def dedupe_photos(root: Path, dry_run: bool = False) -> int:
    duplicates_dir = root / DEDUP_DIRNAME
    tmp_dir = root.parent / ".tmp_dedupe"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    moved = 0
    try:
        for dir_path, images in iter_image_dirs(root):
            if duplicates_dir == dir_path or duplicates_dir in dir_path.parents:
                continue
            images.sort(key=lambda p: p.name.lower())
            seen: Dict[str, Path] = {}
            for img in images:
                digest = _hash_thumbnail(img, tmp_dir)
                if digest in seen:
                    rel = img.relative_to(root)
                    dest = duplicates_dir / rel
                    if dry_run:
                        print(f"Doublon: {img} -> {dest}")
                    else:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(img), str(dest))
                    moved += 1
                else:
                    seen[digest] = img
    finally:
        _cleanup_tmp_dir(tmp_dir)
    return moved


def _sort_key(info: ImageInfo, sort_mode: str) -> Tuple:
    if sort_mode == "mtime":
        return (info.mtime, info.path.name.lower())
    if sort_mode == "date":
        sort_ts = info.exif_ts if info.exif_ts is not None else info.mtime
        return (sort_ts, info.path.name.lower())
    return (info.path.name.lower(),)


def build_plan_for_dir(
    dir_path: Path, images: List[Path], sort_mode: str
) -> List[RenamePlan]:
    infos: List[ImageInfo] = []
    for img in images:
        width, height, exif_ts = get_image_info(img)
        orientation = "p" if height > width else "l"
        infos.append(
            ImageInfo(
                path=img,
                orientation=orientation,
                exif_ts=exif_ts,
                mtime=img.stat().st_mtime,
            )
        )

    infos.sort(key=lambda info: _sort_key(info, sort_mode))
    counts = {
        "p": len([info for info in infos if info.orientation == "p"]),
        "l": len([info for info in infos if info.orientation == "l"]),
    }
    widths = {
        "p": max(3, len(str(max(1, counts["p"])))),
        "l": max(3, len(str(max(1, counts["l"])))),
    }

    counters = {"p": 0, "l": 0}
    plan: List[RenamePlan] = []
    for info in infos:
        orientation = info.orientation
        counters[orientation] += 1
        ext = _normalize_ext(info.path.suffix)
        number = f"{counters[orientation]:0{widths[orientation]}d}"
        new_name = f"{orientation}{number}{ext}"
        plan.append(
            RenamePlan(
                src=info.path, dest=dir_path / new_name, orientation=info.orientation
            )
        )
    return plan


def plan_all(root: Path, sort_mode: str) -> List[RenamePlan]:
    plan: List[RenamePlan] = []
    for dir_path, images in iter_image_dirs(root):
        plan.extend(build_plan_for_dir(dir_path, images, sort_mode))
    return plan


def validate_plan(plan: List[RenamePlan]) -> None:
    dests = [p.dest for p in plan]
    if len(dests) != len(set(dests)):
        raise ValueError("Collision détectée dans les noms de destination.")


def apply_plan(plan: List[RenamePlan]) -> Dict[Path, Path]:
    mapping: Dict[Path, Path] = {}
    temp_map: Dict[Path, Path] = {}

    for item in plan:
        mapping[item.src] = item.dest

    # Renommage en 2 passes pour éviter les collisions.
    for src, dest in mapping.items():
        if src == dest:
            continue
        tmp = src.with_name(f".__tmp__{src.name}")
        if tmp.exists():
            tmp = src.with_name(f".__tmp__{src.stem}_{os.getpid()}{src.suffix}")
        src.rename(tmp)
        temp_map[tmp] = dest

    for tmp, dest in temp_map.items():
        if dest.exists():
            raise FileExistsError(f"Le fichier existe déjà: {dest}")
        tmp.rename(dest)

    return mapping


def build_path_mapping(mapping: Dict[Path, Path], root: Path) -> Dict[str, str]:
    path_map: Dict[str, str] = {}
    for src, dest in mapping.items():
        if src == dest:
            continue
        src_rel = src.relative_to(root.parent).as_posix()
        dest_rel = dest.relative_to(root.parent).as_posix()
        for form in {"NFC", "NFD"}:
            src_key = unicodedata.normalize(form, src_rel)
            dest_val = unicodedata.normalize(form, dest_rel)
            path_map[src_key] = dest_val
    return path_map


def update_index_html(index_path: Path, path_map: Dict[str, str]) -> int:
    content = index_path.read_text(encoding="utf-8")
    changed = 0

    def replace_src(match: re.Match[str]) -> str:
        nonlocal changed
        src = match.group(1)
        if src in path_map:
            changed += 1
            return f'src="{path_map[src]}"'
        src_nfc = unicodedata.normalize("NFC", src)
        src_nfd = unicodedata.normalize("NFD", src)
        if src_nfc in path_map:
            changed += 1
            return f'src="{path_map[src_nfc]}"'
        if src_nfd in path_map:
            changed += 1
            return f'src="{path_map[src_nfd]}"'
        return match.group(0)

    new_content, _ = re.subn(r'src="([^"]+)"', replace_src, content)
    if changed:
        index_path.write_text(new_content, encoding="utf-8")
    return changed


def _extract_h2_text(section_html: str) -> str:
    match = re.search(r"<h2[^>]*>(.*?)</h2>", section_html, re.DOTALL)
    if not match:
        return ""
    text = re.sub(r"<[^>]+>", "", match.group(1))
    return " ".join(text.split())


def _section_indent(section_html: str) -> str:
    for line in section_html.splitlines():
        if "<h2" in line:
            return line.split("<h2", 1)[0]
    return "  "


def _iter_images(folder: Path) -> List[Path]:
    images = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    return sorted(images, key=lambda p: p.name.lower())


def build_gallery_html(images: List[Path], title: str, indent: str, root: Path) -> str:
    landscapes = [p for p in images if p.name.lower().startswith("l")]
    portraits = [p for p in images if p.name.lower().startswith("p")]
    others = [p for p in images if p not in landscapes and p not in portraits]
    landscapes.extend(others)

    alt_base = title or "Photo"
    lines = [f'{indent}<div class="gallery">']
    indent_item = indent + "  "
    indent_img = indent + "    "

    l_idx = p_idx = 0
    l_alt = p_alt = 1
    while l_idx < len(landscapes) or p_idx < len(portraits):
        if l_idx < len(landscapes):
            src = landscapes[l_idx].relative_to(root).as_posix()
            lines.append(f'{indent_item}<div class="landscape-container">')
            lines.append(
                f'{indent_img}<img src="{src}" alt="{alt_base} paysage {l_alt}" class="landscape">'
            )
            lines.append(f"{indent_item}</div>")
            l_idx += 1
            l_alt += 1

        if p_idx < len(portraits):
            lines.append(f'{indent_item}<div class="portrait-row">')
            for _ in range(2):
                if p_idx >= len(portraits):
                    break
                src = portraits[p_idx].relative_to(root).as_posix()
                lines.append(
                    f'{indent_img}<img src="{src}" alt="{alt_base} portrait {p_alt}" class="portrait">'
                )
                p_idx += 1
                p_alt += 1
            lines.append(f"{indent_item}</div>")

    lines.append(f"{indent}</div>")
    return "\n".join(lines)


def rebuild_galleries(index_path: Path, root: Path) -> int:
    content = index_path.read_text(encoding="utf-8")
    updated_sections = 0

    for section_id, folder_rel in SECTION_DIRS.items():
        folder = (root.parent / folder_rel).resolve()
        if not folder.exists():
            continue

        pattern = re.compile(
            rf'(<section\s+id="{re.escape(section_id)}"[^>]*>\s*<h2[^>]*>.*?</h2>)(.*?)(</section>)',
            re.DOTALL,
        )
        match = pattern.search(content)
        if not match:
            continue

        section_html = match.group(0)
        line_start = content.rfind("\n", 0, match.start()) + 1
        section_indent = re.match(r"[ \t]*", content[line_start:match.start()]).group(0)
        title = _extract_h2_text(section_html)
        indent = _section_indent(section_html)
        images = _iter_images(folder)
        gallery_html = build_gallery_html(images, title, indent, root.parent)

        replacement = f"{match.group(1)}\n{gallery_html}\n{section_indent}</section>"
        content = content[: match.start()] + replacement + content[match.end() :]
        updated_sections += 1

    if updated_sections:
        index_path.write_text(content, encoding="utf-8")
    return updated_sections


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Renomme les photos par orientation et met à jour index.html."
    )
    parser.add_argument(
        "--photos-root",
        default="Photos",
        help="Dossier racine des photos (défaut: Photos)",
    )
    parser.add_argument(
        "--index",
        default="index.html",
        help="Chemin vers index.html (défaut: index.html)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Affiche les changements sans modifier les fichiers",
    )
    parser.add_argument(
        "--rebuild-galleries",
        action="store_true",
        help="Reconstruit les galeries dans index.html selon la règle 1 paysage + 2 portraits",
    )
    parser.add_argument(
        "--dedupe",
        action="store_true",
        help="Déplace les doublons (pixels identiques) vers Photos/_duplicates",
    )
    parser.add_argument(
        "--sort",
        choices=["name", "mtime", "date"],
        default="name",
        help="Tri pour le renommage (name, mtime, date=EXIF si dispo sinon mtime)",
    )
    args = parser.parse_args()

    root = Path(args.photos_root).resolve()
    index_path = Path(args.index).resolve()

    dupes_moved = 0
    if args.dedupe:
        dupes_moved = dedupe_photos(root, dry_run=args.dry_run)

    plan = plan_all(root, args.sort)
    validate_plan(plan)

    if args.dry_run:
        for item in plan:
            if item.src != item.dest:
                print(f"{item.src} -> {item.dest} ({item.orientation})")
        return

    mapping = apply_plan(plan)
    path_map = build_path_mapping(mapping, root)
    updated = update_index_html(index_path, path_map)
    rebuilt = 0
    if args.rebuild_galleries:
        rebuilt = rebuild_galleries(index_path, root)

    if args.dedupe:
        print(f"Doublons déplacés: {dupes_moved}")
    print(f"Photos renommées: {len([m for m in mapping if mapping[m] != m])}")
    print(f"Références réellement modifiées dans index.html: {updated}")
    if args.rebuild_galleries:
        print(f"Galeries reconstruites: {rebuilt}")


if __name__ == "__main__":
    main()
