import logging
import sys
import os
from logging.handlers import RotatingFileHandler

# Log formatter
# Example: 2026-05-28 16:30:00,123 - newsagg.main - INFO - Scraped 15 articles
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# Configured flag to avoid duplicate handlers if imported multiple times
_configured = False

def setup_logging(default_level=logging.DEBUG):
    global _configured
    if _configured:
        return
        
    root_logger = logging.getLogger()
    # Set to lowest level so handlers can filter individually
    root_logger.setLevel(default_level)
    
    formatter = logging.Formatter(LOG_FORMAT)
    
    # 1. Console Handler (Standard Error)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # 2. Rotating File Handler (aggregator.log)
    log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aggregator.log")
    try:
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3,
            encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except Exception as e:
        # Fallback if unable to write to the file
        print(f"Warning: Failed to set up rotating file handler for {log_file}: {e}", file=sys.stderr)
        
    _configured = True

def get_logger(name: str) -> logging.Logger:
    """Returns a logger instance with the specified name."""
    if not _configured:
        setup_logging()
    return logging.getLogger(name)
