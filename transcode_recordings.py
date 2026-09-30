#!/usr/bin/env python3
"""Safely convert existing MP4 recordings to browser-compatible H.264."""
from __future__ import annotations

import argparse
import os
import shutil
import stat
from pathlib import Path

from surveillance_media import probe_video_codec, transcode_mp4_to_h264


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("captures"),
                        help="Dossier des enregistrements (défaut : captures).")
    parser.add_argument("--apply", action="store_true",
                        help="Effectuer les conversions; sans cette option, affiche seulement le bilan.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limiter le nombre de conversions (0 = toutes).")
    parser.add_argument("--reserve-mib", type=int, default=512,
                        help="Espace libre minimal à préserver (défaut : 512 Mio).")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit doit être positif ou nul.")
    if args.reserve_mib < 0:
        parser.error("--reserve-mib doit être positif ou nul.")
    directory = args.directory.expanduser().resolve()
    if not directory.is_dir():
        parser.error(f"Dossier introuvable : {directory}")

    candidates: list[Path] = []
    already_compatible = 0
    invalid = 0
    for path in sorted(directory.glob("*.mp4")):
        if path.is_symlink() or not path.is_file():
            continue
        codec, pixel_format = probe_video_codec(path)
        if codec == "h264" and pixel_format == "yuv420p":
            already_compatible += 1
        elif codec:
            candidates.append(path)
        else:
            invalid += 1
    if args.limit:
        candidates = candidates[:args.limit]

    mode = "Conversion" if args.apply else "Simulation"
    print(f"{mode} — fichiers H.264 déjà compatibles : {already_compatible}; "
          f"à convertir : {len(candidates)}; invalides/illisibles ignorés : {invalid}.")
    if not args.apply:
        print("Relancez avec --apply pour réencoder chaque fichier de façon atomique.")
        return 0

    converted = 0
    failed = 0
    stopped_for_space = False
    originals = directory / ".originals"
    for index, source in enumerate(candidates, start=1):
        required_free = args.reserve_mib * 1024 * 1024 + source.stat().st_size * 2
        if shutil.disk_usage(directory).free < required_free:
            print(f"Arrêt avant {index}/{len(candidates)} : espace libre insuffisant; source suivante conservée.")
            stopped_for_space = True
            break
        staging = source.with_name(f".{source.stem}.h264-conversion.mp4")
        try:
            source_stat = source.stat()
            if not transcode_mp4_to_h264(source, staging):
                failed += 1
                staging.unlink(missing_ok=True)
                print(f"Échec {index}/{len(candidates)} : conversion impossible; source conservée.")
                continue
            os.chmod(staging, stat.S_IMODE(source_stat.st_mode))
            os.utime(staging, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
            originals.mkdir(mode=0o700, exist_ok=True)
            backup = originals / source.name
            if not backup.exists():
                os.link(source, backup)
            os.replace(staging, source)
            converted += 1
            print(f"Converti {index}/{len(candidates)}.")
        except OSError:
            failed += 1
            staging.unlink(missing_ok=True)
            print(f"Échec {index}/{len(candidates)} : source conservée.")
    pending = len(candidates) - converted - failed
    print(f"Terminé — convertis : {converted}; échecs : {failed}; invalides ignorés : {invalid}; "
          f"restants : {pending}.")
    return 1 if failed or stopped_for_space else 0


if __name__ == "__main__":
    raise SystemExit(main())
