import sys

from . import main

if __name__ == "__main__":
    # Sürükle-bırakta dosya yolu argüman olarak gelir.
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else None))
