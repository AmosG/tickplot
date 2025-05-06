from collectors.base import InstrumentType

KAFKA_BOOTSTRAP_SERVERS = 'localhost:9092'

SYMBOLS_TO_COLLECT = [
    ('QQQ', InstrumentType.STOCK),
    # ('TQQQ', InstrumentType.STOCK),
    # ('SPY', InstrumentType.STOCK),
    # ('UPRO', InstrumentType.STOCK),
    # ('ES', InstrumentType.FUTURE),
    # ('AAPL', InstrumentType.STOCK),
] 
