"""Fail a release when the tag, package version, and Info.plist disagree.

The disk-image job reads ``galley.__version__`` and ``CFBundleShortVersionString``
and passes them here. On a ``refs/tags/v*`` ref it also passes the tag with the
leading ``v`` removed. Other refs omit the tag, and the tag is not compared.
"""

import argparse

CHECK = "Check release version"
FIX = "Bump galley.__version__ or re-tag."


def failure_message(package: str, plist: str, tag: str | None) -> str | None:
    """Return the failure text, or None when the versions agree.

    ``tag`` is None when this run is not a ``v*`` tag, so the tag is skipped.
    """
    bundle_differs = package != plist
    tag_differs = tag is not None and (tag != package or tag != plist)
    if not bundle_differs and not tag_differs:
        return None
    lines = [
        f"{CHECK} failed.",
        f"galley.__version__ is {package}",
        f"CFBundleShortVersionString is {plist}",
    ]
    if tag is not None:
        lines.append(f"tag is {tag}")
    lines.append(FIX)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Compare versions from the command line. Return 0 when they agree."""
    parser = argparse.ArgumentParser(description=CHECK)
    parser.add_argument("--package", required=True)
    parser.add_argument("--plist", required=True)
    parser.add_argument("--tag")
    args = parser.parse_args(argv)
    failure = failure_message(args.package, args.plist, args.tag)
    if failure is None:
        print(f"{CHECK}: versions agree ({args.package}).")
        return 0
    print(failure)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
