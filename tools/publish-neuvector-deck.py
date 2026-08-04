#!/usr/bin/env python3
"""Publish the NeuVector GTM deck from its authoring folder into site/.

The authored deck and the published deck are deliberately NOT identical.

The authored copy uses relative asset paths so it opens from disk with a
double-click and presents with no network at all -- that is the copy Rick runs
live, and it must stay that way.

The published copy needs absolute paths. It is served at two URLs:

    /neuvector-gtm/index.html
    /for/<token>                     <- per-recipient tracking path

Relative paths resolve against the *directory* of the current URL. At the token
URL, which has no trailing slash, that directory is /for/ -- so `vendor/impress.js`
resolves to /for/vendor/impress.js and 404s. Every dependency did, and the deck
rendered as an uninitialised page for anyone handed that link. Absolute paths do
not depend on the base URL, so both URLs work.

Copying the file across by hand reintroduces the bug silently. Use this instead.

    python3 tools/publish-neuvector-deck.py [--check]

--check verifies the published copy is current without writing anything.
"""

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
SRC = pathlib.Path(
    "/Users/rashford/Library/CloudStorage/Dropbox/Claude Cowork/Job Search/"
    "FOSSA/Sales Engineer/demo-deck/Neuvector_GTM_Pitch_Deck.html"
)
DEST = REPO / "site" / "neuvector-gtm" / "index.html"
PREFIX = "/neuvector-gtm/"
DIRS = ("assets", "fonts", "vendor")

# src="assets/x.svg"  href="fonts/y.woff2"  url("fonts/z.woff2")  url(vendor/a.js)
PATTERN = re.compile(
    r'((?:src|href)=["\']|url\(["\']?)(' + "|".join(DIRS) + r')/'
)


def rewrite(html):
    """assets/x -> /neuvector-gtm/assets/x, preserving the matched prefix
    (src=", href=', url(", url( ...) exactly as it appeared."""
    n_before = len(PATTERN.findall(html))
    out = PATTERN.sub(lambda m: m.group(1) + PREFIX + m.group(2) + "/", html)
    n_after = len(PATTERN.findall(out))
    return out, n_before, n_after


def referenced(html):
    return sorted(set(re.findall(r'(?:' + "|".join(DIRS) + r')/[A-Za-z0-9._-]+', html)))


def main():
    check = "--check" in sys.argv

    if not SRC.exists():
        sys.exit("source deck not found: %s" % SRC)
    html = SRC.read_text()

    rel_before = len(PATTERN.findall(html))
    if rel_before == 0:
        sys.exit("source has no relative refs -- has it already been rewritten?")

    out, n_before, n_after = rewrite(html)
    if n_after != 0:
        sys.exit("rewrite incomplete: %d relative refs remain" % n_after)

    # every referenced file must already exist in the published tree
    missing = [p for p in referenced(html) if not (DEST.parent / p).exists()]
    if missing:
        sys.exit("missing from site/neuvector-gtm/: " + ", ".join(missing))

    # and the absolute form must be the only form present
    stray = re.findall(r'(?:src|href)="(?!/|https?:|data:|#)[^"]+"', out)
    stray = [s for s in stray if any(d + "/" in s for d in DIRS)]
    if stray:
        sys.exit("stray relative refs after rewrite: %s" % stray[:5])

    if check:
        current = DEST.read_text() if DEST.exists() else ""
        if current == out:
            print("up to date (%d refs absolute, %d assets present)"
                  % (n_before, len(referenced(html))))
            return 0
        print("STALE -- published copy differs from a fresh publish of the source")
        return 1

    DEST.write_text(out)
    print("published %s -> %s" % (SRC.name, DEST.relative_to(REPO)))
    print("  %d refs rewritten to %s" % (n_before, PREFIX))
    print("  %d assets checked, all present" % len(referenced(html)))
    print("  %d bytes" % len(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
