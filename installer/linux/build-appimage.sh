#!/usr/bin/env bash
# AppImage di Converter: un solo file eseguibile per quasi tutte le distro.
#
#   installer/linux/build-appimage.sh <cartella onedir> <versione> <cartella output> <appimagetool>
#
# La cartella onedir è quella di PyInstaller costruita con ffmpeg/ffprobe STATICI
# su PATH: finiscono in _internal/bin e l'AppImage non dipende da nulla di
# installato, a parte le librerie grafiche di base di ogni desktop (glibc ≥ quella
# della macchina di build, GL/EGL, fontconfig, xkbcommon).
set -euo pipefail

dist="$1"; version="$2"; out="$3"; tool="$4"
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
[ -x "$dist/Converter" ] || { echo "manca $dist/Converter" >&2; exit 1; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
appdir="$work/Converter.AppDir"

install -d "$appdir/usr/lib/converter" "$appdir/usr/share/applications" "$appdir/usr/share/metainfo"
cp -a "$dist/." "$appdir/usr/lib/converter/"
install -m 755 "$here/AppRun" "$appdir/AppRun"

# Nel menu di un AppImage il comando e' il launcher interno (AppRun), non il
# wrapper /usr/bin/converter che esiste solo nel pacchetto .deb: con quello la
# voce di menu non parte (il comando non e' nel PATH).
sed 's|^Exec=.*|Exec=AppRun|' "$here/converter.desktop" > "$appdir/converter.desktop"
grep -q '^Exec=AppRun$' "$appdir/converter.desktop" || {
  echo "il desktop file dell'AppImage non punta ad AppRun" >&2; exit 1; }
install -m 644 "$appdir/converter.desktop" "$appdir/usr/share/applications/converter.desktop"
if command -v desktop-file-validate >/dev/null 2>&1; then
  desktop-file-validate "$appdir/converter.desktop"
fi

# Metadati AppStream: i negozi di software leggono qui nome, descrizione,
# licenza, icona e versione dell'app.
sed -e "s/@VERSION@/$version/g" -e "s/@DATE@/$(date -u +%Y-%m-%d)/g" \
  "$here/metainfo.xml.in" > "$appdir/usr/share/metainfo/converter.metainfo.xml"

install -m 644 "$repo/converter/assets/icons/converter_256.png" "$appdir/converter.png"
for n in 16 24 32 48 64 128 256; do
  install -D -m 644 "$repo/converter/assets/icons/converter_$n.png" \
    "$appdir/usr/share/icons/hicolor/${n}x${n}/apps/converter.png"
done
ln -s converter.png "$appdir/.DirIcon"

mkdir -p "$out"
image="$out/Converter-v${version}-x86_64.AppImage"
# --appimage-extract-and-run: i runner CI non hanno FUSE per montare appimagetool.
ARCH=x86_64 "$tool" --appimage-extract-and-run --no-appstream "$appdir" "$image"
echo "$image"
