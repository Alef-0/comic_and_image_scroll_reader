#!/usr/bin/env bash

set -euo pipefail

project_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
package_name="comic-scroll-reader"
package_version="0.2"

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

# 1. Install executable
install -Dm755 "$binary" "$package_root/usr/bin/$package_name"

# 2. Install desktop application launcher
install -Dm644 "$project_directory/comic_scroll_reader/assets/comic-scroll-reader.desktop" \
    "$package_root/usr/share/applications/comic-scroll-reader.desktop"

# 3. Install icons (SVG scalable and standard 256x256 / pixmaps for maximum compatibility)
install -Dm644 "$project_directory/comic_scroll_reader/assets/csr_app_icon.svg" \
    "$package_root/usr/share/icons/hicolor/scalable/apps/comic-scroll-reader.svg"
install -Dm644 "$project_directory/comic_scroll_reader/assets/csr_app_icon.png" \
    "$package_root/usr/share/icons/hicolor/256x256/apps/comic-scroll-reader.png"
install -Dm644 "$project_directory/comic_scroll_reader/assets/csr_app_icon.png" \
    "$package_root/usr/share/pixmaps/comic-scroll-reader.png"

# 4. Install documentation and copyright
install -Dm644 "$project_directory/LICENSE" \
    "$package_root/usr/share/doc/$package_name/copyright"

# 5. Install all supported file manager integrations into the private application directory
integration_dir="$package_root/usr/share/$package_name/integrations"

# Nemo actions
install -Dm644 \
    "$project_directory/comic_scroll_reader/assets/integrations/nemo/creader_folder.nemo_action" \
    "$integration_dir/nemo/creader_folder.nemo_action"
install -Dm644 \
    "$project_directory/comic_scroll_reader/assets/integrations/nemo/creader_here.nemo_action" \
    "$integration_dir/nemo/creader_here.nemo_action"

# Dolphin service menu
install -Dm644 \
    "$project_directory/comic_scroll_reader/assets/integrations/dolphin/comic-scroll-reader.desktop" \
    "$integration_dir/dolphin/comic-scroll-reader.desktop"

# Nautilus script
install -Dm755 \
    "$project_directory/comic_scroll_reader/assets/integrations/nautilus/comic-scroll-reader" \
    "$integration_dir/nautilus/comic-scroll-reader"

# 6. Create Debconf templates, config, postinst, and postrm scripts
install -d "$package_root/DEBIAN"

cat > "$package_root/DEBIAN/templates" <<'EOF'
Template: comic-scroll-reader/file-managers
Type: multiselect
Choices: Nemo, Dolphin, Nautilus
Description: Enable file-manager integrations:
 Comic Scroll Reader can add context-menu actions to supported file managers.
 Select which integrations should be enabled.
EOF

cat > "$package_root/DEBIAN/config" <<'EOF'
#!/bin/sh
set -e

if [ -e /usr/share/debconf/confmodule ]; then
    . /usr/share/debconf/confmodule
else
    exit 0
fi

detected=""
if command -v nemo >/dev/null 2>&1 || [ -d /usr/share/nemo/actions ]; then
    detected="Nemo"
fi

if command -v dolphin >/dev/null 2>&1 || [ -d /usr/share/kio/servicemenus ]; then
    if [ -n "$detected" ]; then
        detected="$detected, Dolphin"
    else
        detected="Dolphin"
    fi
fi

if command -v nautilus >/dev/null 2>&1 || [ -d /usr/share/nautilus-scripts ]; then
    if [ -n "$detected" ]; then
        detected="$detected, Nautilus"
    else
        detected="Nautilus"
    fi
fi

# Pre-populate defaults with detected file managers if not previously configured
db_get comic-scroll-reader/file-managers || true
if [ -z "$RET" ] && [ -n "$detected" ]; then
    db_set comic-scroll-reader/file-managers "$detected"
fi

db_input high comic-scroll-reader/file-managers || true
db_go || true
EOF
chmod 755 "$package_root/DEBIAN/config"

cat > "$package_root/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e

if [ -e /usr/share/debconf/confmodule ]; then
    . /usr/share/debconf/confmodule
fi

if [ "$1" = "configure" ]; then
    integrations=""
    if command -v db_get >/dev/null 2>&1; then
        db_get comic-scroll-reader/file-managers || true
        integrations="$RET"
    fi

    # 1. Nemo integration
    case ", $integrations," in
        *", Nemo,"*)
            mkdir -p /usr/share/nemo/actions
            ln -sf /usr/share/comic-scroll-reader/integrations/nemo/creader_folder.nemo_action \
                   /usr/share/nemo/actions/creader_folder.nemo_action
            ln -sf /usr/share/comic-scroll-reader/integrations/nemo/creader_here.nemo_action \
                   /usr/share/nemo/actions/creader_here.nemo_action
            ;;
        *)
            rm -f /usr/share/nemo/actions/creader_folder.nemo_action \
                  /usr/share/nemo/actions/creader_here.nemo_action
            ;;
    esac

    # 2. Dolphin integration
    case ", $integrations," in
        *", Dolphin,"*)
            mkdir -p /usr/share/kio/servicemenus
            ln -sf /usr/share/comic-scroll-reader/integrations/dolphin/comic-scroll-reader.desktop \
                   /usr/share/kio/servicemenus/comic-scroll-reader.desktop
            ;;
        *)
            rm -f /usr/share/kio/servicemenus/comic-scroll-reader.desktop
            ;;
    esac

    # 3. Nautilus integration
    case ", $integrations," in
        *", Nautilus,"*)
            mkdir -p /usr/share/nautilus-scripts
            ln -sf /usr/share/comic-scroll-reader/integrations/nautilus/comic-scroll-reader \
                   /usr/share/nautilus-scripts/comic-scroll-reader
            ;;
        *)
            rm -f /usr/share/nautilus-scripts/comic-scroll-reader
            ;;
    esac

    # Update desktop and MIME databases
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications || true
    fi
    if command -v update-mime-database >/dev/null 2>&1; then
        update-mime-database /usr/share/mime >/dev/null 2>&1 || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor >/dev/null 2>&1 || true
    fi
fi

exit 0
EOF
chmod 755 "$package_root/DEBIAN/postinst"

cat > "$package_root/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e

if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
    # Remove active symlinks created by postinst
    rm -f /usr/share/nemo/actions/creader_folder.nemo_action \
          /usr/share/nemo/actions/creader_here.nemo_action \
          /usr/share/kio/servicemenus/comic-scroll-reader.desktop \
          /usr/share/nautilus-scripts/comic-scroll-reader

    if [ "$1" = "purge" ] && [ -e /usr/share/debconf/confmodule ]; then
        . /usr/share/debconf/confmodule
        db_purge || true
    fi

    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications || true
    fi
    if command -v update-mime-database >/dev/null 2>&1; then
        update-mime-database /usr/share/mime >/dev/null 2>&1 || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor >/dev/null 2>&1 || true
    fi
fi

exit 0
EOF
chmod 755 "$package_root/DEBIAN/postrm"

installed_size="$(du -sk "$package_root/usr" | cut -f1)"

cat > "$package_root/DEBIAN/control" <<EOF
Package: $package_name
Version: $package_version
Architecture: $architecture
Maintainer: $maintainer
Section: graphics
Priority: optional
Depends: libc6 (>= 2.38), zenity | kdialog, debconf (>= 0.5) | debconf-2.0
Suggests: nemo, dolphin, nautilus
Installed-Size: $installed_size
Description: Continuous scrolling comic and image reader
 Reads a folder of images, individual images, or PDF documents
 as a continuous vertical scrolling comic strip.
EOF

mkdir -p "$output_directory"
output_directory="$(realpath -- "$output_directory")"
dpkg-deb --root-owner-group --build "$package_root" \
    "$output_directory/$package_file"

echo "Debian package created at: $output_directory/$package_file"
echo "Install it with: sudo apt install $output_directory/$package_file"


