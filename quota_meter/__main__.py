# -*- coding: utf-8 -*-
"""python -m quota_meter  ->  opens the meter (see README)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import medidor  # noqa: E402

sys.exit(medidor.main())
