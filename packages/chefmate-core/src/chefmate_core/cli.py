"""Compatibility forwarding; use the dedicated ChefMate package."""
from chefmate_food_data.cli import *  # noqa: F403

if __name__ == "__main__":
    raise SystemExit(main())  # noqa: F405
