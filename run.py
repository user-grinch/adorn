#!/usr/bin/env python3
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from adorn.main import main

if __name__ == "__main__":
    sys.exit(main())
