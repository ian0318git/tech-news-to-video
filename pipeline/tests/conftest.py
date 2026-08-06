"""讓測試可以直接 import scripts/ 下的模組。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
