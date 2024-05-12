#!/usr/bin/python3
# -*- coding: utf-8 -*-
import re
import sys
import os
from almost_make.cli import main

pp = "."
if "PYTHONPATH" in os.environ:
    pp = os.environ["PYTHONPATH"]
script_directory = os.path.dirname(os.path.abspath(sys.argv[0]))
if script_directory not in pp:
    os.environ["PYTHONPATH"] = f"{pp}:{script_directory}"

if __name__ == '__main__':
    sys.argv[0] = re.sub(r'(-script\.pyw|\.exe)?$', '', sys.argv[0])
    sys.exit(main())
