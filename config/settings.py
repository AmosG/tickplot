from collectors.base import InstrumentType

KAFKA_BOOTSTRAP_SERVERS = 'localhost:9092'

# Symbol configuration format:
# For stocks: (symbol, InstrumentType.STOCK)
# For options: (symbol, InstrumentType.OPTION, num_strikes, expiration_days)
# num_strikes: Number of strikes above/below current price (default: 5)
# expiration_days: Days to expiration for options (default: 30)

SYMBOLS_TO_COLLECT = [
    # ('QQQ', InstrumentType.STOCK),  # Commenting out stock collector to focus on options
    ('QQQ', InstrumentType.OPTION, 3, 0),  # QQQ options: 3 strikes up/down, 0 days expiration
    # ('TQQQ', InstrumentType.STOCK),
    # ('SPY', InstrumentType.STOCK),
    # ('SPY', InstrumentType.OPTION, 5, 45),  # SPY options: 5 strikes up/down, 45 days expiration
    # ('UPRO', InstrumentType.STOCK),
    # ('ES', InstrumentType.FUTURE),
    # ('AAPL', InstrumentType.STOCK),
    # ('AAPL', InstrumentType.OPTION, 4, 14),  # AAPL options: 4 strikes up/down, 14 days expiration
] 
