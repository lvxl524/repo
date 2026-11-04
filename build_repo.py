#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Auto-build a Cydia/Sileo repo's Packages / Packages.gz / Release.

Source of truth = each .deb's DEBIAN control file.
Optional overrides.json maps Package-id -> {field: value} applied on top
(e.g. {"com.sini.tinge": {"section": "系统增强"}}).

Run:  python3 build_repo.py
Works on Windows and Linux (pure stdlib, no dpkg needed).
"""
import os, io, sys, json, gzip, lzma, hashlib, tarfile, struct

ROOT = os.path.dirname(os.path.abspath(__file__))
DEBS_DIR = os.path.join(ROOT, "debs")
PKG = os.path.join(ROOT, "Packages")
PKG_GZ = os.path.join(ROOT, "Packages.gz")
REL = os.path.join(ROOT, "Release")
OVERRIDES = os.path.join(ROOT, "overrides.json")

DEFAULT_RELEASE_HEADER = (
    "Origin: 溯元阁\n"
    "Label: 溯元阁\n"
    "Suite: stable\n"
    "Version: 1.0\n"
    "Codename: ios\n"
    "Architectures: iphoneos-arm64 iphoneos-arm64e\n"
    "Components: main\n"
    "Description: 溯元阁\n"
    "Support: https://lvxl524.github.io/repo/\n"
    "Icon: https://lvxl524.github.io/repo/CydiaIcon.png\n"
)


# ---------- .deb (ar) parsing ----------
def parse_ar(data: bytes):
    """Return list of (name, bytes) for ar archive members."""
    if data[:8] != b"!<arch>\n":
        raise ValueError("not an ar archive")
    members = []
    off = 8
    while off < len(data):
        if data[off:off + 8] == b"":
            break
        header = data[off:off + 60]
        if len(header) < 60:
            break
        name = header[0:16].decode("utf-8", "replace").strip().rstrip("/")
        size = int(header[48:58].decode("ascii").strip() or "0")
        off += 60
        content = data[off:off + size]
        members.append((name, content))
        off += size
        if size % 2 == 1:  # ar pads to even boundary
            off += 1
    return members


def extract_control_fields(deb_path: str):
    with open(deb_path, "rb") as f:
        data = f.read()
    members = parse_ar(data)
    ctrl = None
    for name, content in members:
        if name.startswith("control.tar"):
            ctrl = content
            break
    if ctrl is None:
        raise ValueError(f"{deb_path}: no control.tar found")

    if name.endswith(".zst"):
        try:
            import zstandard
            dctx = zstandard.ZstdDecompressor()
            ctrl = dctx.decompress(ctrl)
        except ImportError:
            raise RuntimeError("zstd control.tar needs 'zstandard' module")
    tar = tarfile.open(fileobj=io.BytesIO(ctrl))
    control_bytes = None
    for m in tar.getmembers():
        if m.name in ("./control", "control", "./control/"):
            control_bytes = tar.extractfile(m).read()
            break
    if control_bytes is None:
        raise ValueError(f"{deb_path}: no ./control in control.tar")
    return parse_control(control_bytes.decode("utf-8"))


def parse_control(text: str):
    """Parse a Debian control file into an ordered list of (field, value)."""
    fields = []
    cur_field = None
    cur_val = []
    for line in text.splitlines():
        if not line:
            break  # control files end at first blank line
        if line[0] in (" ", "\t"):
            if cur_field is not None:
                cur_val.append(line.strip())
            continue
        if ":" in line:
            if cur_field is not None:
                fields.append((cur_field, " ".join(cur_val)))
            cur_field, val = line.split(":", 1)
            cur_field = cur_field.strip()
            cur_val = [val.strip()]
    if cur_field is not None:
        fields.append((cur_field, " ".join(cur_val)))
    return fields


# ---------- build ----------
def compute_hashes(path: str):
    h_md5, h_sha1, h_sha256, h_sha512 = (hashlib.md5(), hashlib.sha1(),
                                         hashlib.sha256(), hashlib.sha512())
    size = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            size += len(chunk)
            for h in (h_md5, h_sha1, h_sha256, h_sha512):
                h.update(chunk)
    return size, h_md5.hexdigest(), h_sha1.hexdigest(), h_sha256.hexdigest(), h_sha512.hexdigest()


def main():
    if not os.path.isdir(DEBS_DIR):
        print("No debs/ directory found; nothing to build.", file=sys.stderr)
        sys.exit(1)

    overrides = {}
    if os.path.exists(OVERRIDES):
        with open(OVERRIDES, "r", encoding="utf-8") as f:
            overrides = json.load(f)

    stanzas = []
    for fn in sorted(os.listdir(DEBS_DIR)):
        if not fn.endswith(".deb"):
            continue
        deb_path = os.path.join(DEBS_DIR, fn)
        fields = extract_control_fields(deb_path)
        fmap = {k: v for k, v in fields}
        pkg_id = fmap.get("Package", fn)
        ov = overrides.get(pkg_id, {})
        if "section" in ov:
            # replace or insert Section
            new_fields = [(k, v) for k, v in fields if k.lower() != "section"]
            new_fields.append(("Section", ov["section"]))
            fields = new_fields

        size, md5s, sha1s, sha256s, sha512s = compute_hashes(deb_path)
        computed = [
            ("Filename", f"debs/{fn}"),
            ("Size", str(size)),
            ("MD5sum", md5s),
            ("SHA1", sha1s),
            ("SHA256", sha256s),
            ("SHA512", sha512s),
        ]
        # apply other overrides
        for k, v in ov.items():
            if k == "section":
                continue
            fields = [(fk, fv) for fk, fv in fields if fk.lower() != k.lower()]
            fields.append((k, v))

        stanza = "\n".join(f"{k}: {v}" for k, v in fields + computed)
        stanzas.append((pkg_id, stanza))

    stanzas.sort(key=lambda x: x[0])
    packages_text = "\n\n".join(s for _, s in stanzas) + "\n"
    pkg_bytes = packages_text.encode("utf-8")

    with open(PKG, "wb") as f:
        f.write(pkg_bytes)
    with gzip.GzipFile(PKG_GZ, "wb", mtime=0) as f:
        f.write(pkg_bytes)

    # Release header: reuse existing header if present, else default
    header = DEFAULT_RELEASE_HEADER
    if os.path.exists(REL):
        with open(REL, "r", encoding="utf-8") as f:
            rel_text = f.read()
        if "MD5Sum:" in rel_text:
            header = rel_text.split("MD5Sum:")[0].rstrip("\n") + "\n"

    def sums(b):
        return (hashlib.md5(b).hexdigest(), hashlib.sha256(b).hexdigest(), len(b))

    m_p, s_p, sz_p = sums(pkg_bytes)
    with open(PKG_GZ, "rb") as f:
        gz_bytes = f.read()
    m_g, s_g, sz_g = sums(gz_bytes)

    release_text = (
        header
        + "MD5Sum:\n"
        + f" {m_p}  {sz_p} Packages\n"
        + f" {m_g}  {sz_g} Packages.gz\n"
        + "SHA256:\n"
        + f" {s_p}  {sz_p} Packages\n"
        + f" {s_g}  {sz_g} Packages.gz\n"
    )
    with open(REL, "wb") as f:
        f.write(release_text.encode("utf-8"))

    print(f"Built {len(stanzas)} package(s):")
    for pid, _ in stanzas:
        print("  -", pid)
    print(f"Packages: {sz_p} bytes | Packages.gz: {sz_g} bytes")


if __name__ == "__main__":
    main()
