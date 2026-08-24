"""python -m rates runs the same CLI as the rates console script."""

import sys

from ._cli import main

sys.exit(main())
