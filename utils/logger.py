import logging

LOG_FORMAT = '[%(asctime)s] %(levelname)s %(filename)s:%(lineno)d - %(message)s'


def get_logger(name=None):
    logger = logging.getLogger(name)
    if not logger.hasHandlers():
        handler = logging.StreamHandler()
        formatter = logging.Formatter(LOG_FORMAT)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger 