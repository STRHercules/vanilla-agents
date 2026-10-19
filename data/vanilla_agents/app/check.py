"""python -m vanilla_agents.app.check : validate app/config and say what is wrong in plain words."""
import sys

from ..contracts import VanillaError
from .check_app import check_app


def main(argv=None):
    try:
        problems = check_app()
    except VanillaError as e:
        problems = [str(e)]
    if problems:
        print("The program window cannot start yet. Please fix these settings problems:")
        for p in problems:
            print(" - " + p)
        return 1
    print("check-app: all settings look good.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
