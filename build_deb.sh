#!/usr/bin/env bash

set -euo pipefail

project_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
package_name="comic-scroll-reader"
package_version="0.1"

usage() {
    echo "Usage: $0 /path/to/artifact-folder [output-directory]"
}

if (( $# < 1 || $# > 2 )); then
    usage >&2
    exit 2
fi

artifact_directory="$(realpath -- "$1")"
binary="$artifact_directory/comic-scroll-reader"
output_directory="${2:-$project_directory/dist}"
maintainer="${DEB_MAINTAINER:-Comic Scroll Reader contributors <noreply@localhost>}"

if [[ ! -f "$binary" ]]; then
    echo "Binary not found: $binary" >&2
    exit 1
fi

if [[ ! -x "$binary" ]]; then
    echo "Binary is not executable: $binary" >&2
    exit 1
fi

if ! command -v dpkg-deb >/dev/null; then
    echo "dpkg-deb is required to build the package." >&2
    exit 1
fi

architecture="$(dpkg --print-architecture)"
temporary_directory="$(mktemp -d /tmp/comic-scroll-reader-deb.XXXXXX)"
trap 'rm -rf -- "$temporary_directory"' EXIT

package_root="$temporary_directory/package-root"
package_file="${package_name}_${package_version}_${architecture}.deb"

install -Dm755 "$binary" "$package_root/usr/bin/$package_name"
install -Dm644 "$project_directory/comic_scroll_reader/assets/comic-scroll-reader.desktop" \
    "$package_root/usr/share/applications/comic-scroll-reader.desktop"
install -Dm644 "$project_directory/comic_scroll_reader/assets/csr_app_icon.svg" \
    "$package_root/usr/share/icons/hicolor/scalable/apps/comic-scroll-reader.svg"
install -Dm644 "$project_directory/LICENSE" \
    "$package_root/usr/share/doc/$package_name/copyright"
install -d "$package_root/DEBIAN"

installed_size="$(du -sk "$package_root/usr" | cut -f1)"

cat > "$package_root/DEBIAN/control" <<EOF
Package: $package_name
Version: $package_version
Architecture: $architecture
Maintainer: $maintainer
Section: graphics
Priority: optional
Depends: libc6 (>= 2.38), zenity | kdialog
Installed-Size: $installed_size
Description: Continuous scrolling comic and image reader
 Reads a folder of images as a continuous vertical comic.
EOF

mkdir -p "$output_directory"
output_directory="$(realpath -- "$output_directory")"
dpkg-deb --root-owner-group --build "$package_root" \
    "$output_directory/$package_file"

echo "Debian package created at: $output_directory/$package_file"
echo "Install it with: sudo apt install $output_directory/$package_file"
