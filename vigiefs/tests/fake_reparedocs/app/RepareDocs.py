r"""Lanceur du faux moteur, calqué sur app\RepareDocs.py de RepareDocs V2.5 (mode --web uniquement)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    args = sys.argv[1:]
    if args[:1] == ["--web"]:
        from repare_docs.pilotage import main as web_cmd
        return web_cmd(args[1:])
    print("Faux moteur : seul le mode --web est disponible")
    return 2


if __name__ == "__main__":
    sys.exit(main() or 0)
