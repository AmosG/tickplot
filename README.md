# tickplot

A real-time market data collection system for stocks and options using Interactive Brokers and Kafka.

## Features

- Collects real-time tick data for stocks from Interactive Brokers
- Collects options data including price, volume, bid/ask, and bid_size/ask_size
- Supports configurable number of strikes around the current price
- Streams data to Kafka for real-time processing

## Configuration

Configure symbols and options collection in `config/settings.py`:

```python
SYMBOLS_TO_COLLECT = [
    # Format for stocks: (symbol, InstrumentType.STOCK)
    ('QQQ', InstrumentType.STOCK),
    
    # Format for options: (symbol, InstrumentType.OPTION, num_strikes, expiration_days)
    # This collects 3 strikes above and below current QQQ price with 30 days to expiration
    ('QQQ', InstrumentType.OPTION, 3, 30),
    
    # Use expiration_days=0 to collect current expiration options
    ('SPY', InstrumentType.OPTION, 5, 0),
]
```

### Options Configuration Parameters

- `num_strikes`: Number of strikes above and below the current price to collect data for
- `expiration_days`: Days to expiration for the options
  - Use `0` to collect current expiration options (expiring today or soonest available)
  - Use `30` for approximately one month out
  - Use `45` for approximately one and a half months out

## Running the Collector

To run the data collector:

```bash
python main.py collector
```

The system will automatically assign unique client IDs to each collector to avoid connection conflicts with Interactive Brokers.

## Consuming Data

To view collected stock tick data:

```bash
python main.py consumer --topic ticks
```

To view collected option tick data:

```bash
python main.py consumer --topic option_ticks
```

## Kafka Topics

- `ticks`: Contains stock tick data
- `option_ticks`: Contains option tick data

## Option Data Format

Option tick data includes:

```json
{
  "symbol": "QQQ",
  "instrument_type": "OPTION",
  "option_type": "C",  // "C" for call, "P" for put
  "strike": 350.0,
  "expiry": "20230630",
  "timestamp": "2023-06-01T12:34:56.789012",
  "price": 5.25,
  "volume": 10,
  "bid": 5.20,
  "ask": 5.30,
  "bid_size": 5,
  "ask_size": 8
}
```

## Troubleshooting

### Connection Issues

If you see connection errors:

1. Make sure Interactive Brokers TWS or IB Gateway is running
2. Verify the port number (default is 4001 for IB Gateway and 7496 for TWS)
3. Check that you have enabled API connections in TWS/Gateway settings

The system now includes:
- Unique client IDs for each collector to prevent conflicts
- Staggered collector startup to reduce connection contention
- Improved error handling for connection failures
