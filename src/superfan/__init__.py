import logging

# Configure root logger
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S%f'
)

# Ensure all loggers show debug messages
logging.getLogger('superfan').setLevel(logging.DEBUG)
