"""Allow ``python -m local_code_bench``.

The macOS app's bundled runtime launches the CLI this way: console-script
shims carry absolute build-time shebangs, but ``-m`` works from any location
the relocatable interpreter ends up in.
"""

from local_code_bench.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
