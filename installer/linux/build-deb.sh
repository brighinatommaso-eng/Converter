#!/usr/bin/env bash
# Pacchetto .deb di Converter (Ubuntu/Debian/Mint).
#
#   installer/linux/build-deb.sh <cartella onedir> <versione> <cartella output>
#
# La cartella onedir è quella di PyInstaller costruita con CONVERTER_BUNDLE_TOOLS=0:
# ffmpeg NON è dentro: il pacchetto lo dichiara come dipendenza e usa quello del
# sistema (aggiornato da apt, e il pacchetto resta piccolo).
# Installa in /opt/converter, con `converter` in /usr/bin, voce nel menu e icone.
set -euo pipefail

dist="$1"; version="$2"; out="$3"
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
[ -x "$dist/Converter" ] || { echo "manca $dist/Converter" >&2; exit 1; }

root="$(mktemp -d)"
trap 'rm -rf "$root"' EXIT

install -d "$root/DEBIAN" "$root/opt/converter" "$root/usr/bin" \
           "$root/usr/share/applications" "$root/usr/share/doc/converter"
cp -a "$dist/." "$root/opt/converter/"

# Lanciatore: uno script e non un symlink, così l'eseguibile PyInstaller trova
# sempre la sua cartella _internal accanto a sé.
cat > "$root/usr/bin/converter" <<'SH'
#!/bin/sh
exec /opt/converter/Converter "$@"
SH

install -m 644 "$here/converter.desktop" "$root/usr/share/applications/converter.desktop"
for n in 16 24 32 48 64 128 256; do
  install -D -m 644 "$repo/converter/assets/icons/converter_$n.png" \
    "$root/usr/share/icons/hicolor/${n}x${n}/apps/converter.png"
done
install -m 644 "$repo/LICENSE" "$root/usr/share/doc/converter/copyright"
install -m 644 "$repo/THIRD_PARTY_NOTICES.md" "$root/usr/share/doc/converter/THIRD_PARTY_NOTICES.md"

# Permessi standard: cartelle 755, file 644, eseguibili restano eseguibili.
chmod -R u=rwX,go=rX "$root"
chmod 755 "$root/usr/bin/converter" "$root/opt/converter/Converter"

installed_kb="$(du -sk --exclude=DEBIAN "$root" | cut -f1)"
cat > "$root/DEBIAN/control" <<CTRL
Package: converter
Version: $version
Section: utils
Priority: optional
Architecture: amd64
Maintainer: Tommaso Brighina <brighinatommaso-eng@users.noreply.github.com>
Installed-Size: $installed_kb
Depends: ffmpeg, libegl1, libgl1, libxkbcommon-x11-0, libxcb-cursor0, libfontconfig1, libdbus-1-3
Recommends: libreoffice-writer
Suggests: ocrmypdf, tesseract-ocr
Homepage: https://github.com/brighinatommaso-eng/Converter
Description: Offline file converter, honest about quality
 Converts images, audio, video, data, documents and 3D models locally,
 without uploading anything. Every conversion shows whether it is lossless
 or lossy, and why.
 .
 LibreOffice (recommended) enables the faithful Office conversions of the
 Full mode.
CTRL
# Menu e cache delle icone si aggiornano da soli: i trigger dpkg di
# desktop-file-utils e hicolor-icon-theme guardano proprio queste cartelle.

mkdir -p "$out"
deb="$out/converter_${version}_amd64.deb"
dpkg-deb --root-owner-group -Zxz --build "$root" "$deb"
echo "$deb"
