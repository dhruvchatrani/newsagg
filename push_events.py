"""Backward-compatible entrypoint.

This repo historically had multiple push scripts. The canonical implementation
is now in push_events_api_alloc.py.
"""

from push_events_api_alloc import main


if __name__ == "__main__":
    main()
