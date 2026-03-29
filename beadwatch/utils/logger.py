import logging
from logging.handlers import RotatingFileHandler
from config.settings import LOG_FILE_PATH, LOG_MAX_BYTES, LOG_BACKUP_COUNT


def setup_logger() -> logging.Logger:
    """Configure and return the application logger"""
    logger = logging.getLogger('beadwatch')
    logger.setLevel(logging.INFO)

    # Avoid duplicate handlers on reload
    if logger.handlers:
        return logger

    # File handler with rotation
    LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        LOG_FILE_PATH,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT
    )
    file_handler.setLevel(logging.INFO)

    # Console handler (warnings and above)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)

    # Formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger
