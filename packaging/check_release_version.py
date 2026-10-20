"""Fail a release when the tag, package version, and Info.plist disagree.

The disk-image job reads ``galley.__version__`` and, after the app is built,
``CFBundleShortVersionString``. On a ``refs/tags/v*`` ref it also passes the tag
with the leading ``v`` removed. Omit ``--plist`` to compare the tag before the
build. Omit ``--tag`` on any other ref, and the tag is not compared.
"""

import argparse

CHECK = "Check release version"
FIX = "Bump galley.__version__ or re-tag."


def failure_message(
    package: str,
    plist: str | None = None,
    tag: str | None = None,
) -> str | None:
    """Return the failure text, or None when the versions agree.

    ``plist`` is None when Info.plist is not part of this check.
    ``tag`` is None when this run is not a ``v*`` tag, so the tag is skipped.
    """
    plist_differs = plist is not None and package != plist
    if tag is None:
        tag_differs = False
    elif plist is None:
        tag_differs = tag != package
    else:
        tag_differs = tag != package or tag != plist
    if not plist_differs and not tag_differs:
        return None
    lines = [
        f"{CHECK} failed.",
        f"galley.__version__ is {package}",
    ]
    if plist is not None:
        lines.append(f"CFBundleShortVersionString is {plist}")
    if tag is not None:
        lines.append(f"tag is {tag}")
    lines.append(FIX)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Compare versions from the command line. Return 0 when they agree."""
    parser = argparse.ArgumentParser(description=CHECK)
    parser.add_argument("--package", required=True)
    parser.add_argument("--plist")
    parser.add_argument("--tag")
    args = parser.parse_args(argv)
    if args.plist is None and args.tag is None:
        parser.error("pass --plist, --tag, or both")
    failure = failure_message(args.package, args.plist, args.tag)
    if failure is None:
        print(f"{CHECK}: versions agree ({args.package}).")
        return 0
    print(failure)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
