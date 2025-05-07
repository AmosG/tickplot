import asyncio
import sys
import argparse
from collectors.symbol_collector import SymbolCollector
from config.settings import KAFKA_BOOTSTRAP_SERVERS, SYMBOLS_TO_COLLECT

async def run_collectors():
    collectors = []
    
    for config in SYMBOLS_TO_COLLECT:
        if len(config) == 2:
            # Standard format: (symbol, instrument_type)
            symbol, inst_type = config
            collectors.append(SymbolCollector(
                symbol, 
                inst_type, 
                kafka_bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS
            ))
        elif len(config) == 4:
            # Extended format for options: (symbol, instrument_type, num_strikes, expiration_days)
            symbol, inst_type, num_strikes, expiration_days = config
            collectors.append(SymbolCollector(
                symbol, 
                inst_type, 
                kafka_bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                num_strikes=num_strikes,
                expiration_days=expiration_days
            ))
        else:
            print(f"Invalid configuration format: {config}")
    
    print(f"Starting {len(collectors)} collectors...")
    tasks = []
    
    # Start collectors with a small delay between each to avoid connection contention
    for i, collector in enumerate(collectors):
        task = asyncio.create_task(collector.run())
        tasks.append(task)
        print(f"Started collector {i+1}/{len(collectors)}: {collector.symbol} ({collector.instrument_type.name})")
        
        # Add a small delay between starting collectors to avoid connection race conditions
        if i < len(collectors) - 1:
            await asyncio.sleep(0.4)
    
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        print("Collectors cancelled. Stopping...")
        for collector in collectors:
            collector.stop()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        print("All collectors stopped.")

async def run_consumer(consumer_type='print', topic='ticks', group_id=None):
    if consumer_type == 'print':
        from consumers.print_consumer import consume
        await consume(topic=topic, group_id=group_id)
    else:
        print(f"Unknown consumer type: {consumer_type}")
        return

def parse_args():
    parser = argparse.ArgumentParser(description='Tickplot data collector and consumer')
    
    subparsers = parser.add_subparsers(dest='mode', help='Mode to run in')
    
    # Collector mode
    collector_parser = subparsers.add_parser('collector', help='Run in collector mode')
    
    # Consumer mode
    consumer_parser = subparsers.add_parser('consumer', help='Run in consumer mode')
    consumer_parser.add_argument('--type', default='print', choices=['print'], 
                                help='Type of consumer to run')
    consumer_parser.add_argument('--topic', default='ticks', 
                                help='Kafka topic to consume from')
    consumer_parser.add_argument('--group-id', 
                                help='Consumer group ID (defaults to <type>-consumer if not specified)')
    
    return parser.parse_args()

async def main():
    args = parse_args()
    
    if args.mode == 'collector':
        await run_collectors()
    elif args.mode == 'consumer':
        group_id = args.group_id or f"{args.type}-consumer"
        await run_consumer(consumer_type=args.type, topic=args.topic, group_id=group_id)
    else:
        print("Please specify a mode: collector or consumer")
        print("Example: python main.py collector")
        print("Example: python main.py consumer --type print --topic ticks")
        print("Example: python main.py consumer --type print --topic option_ticks")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('Process stopped by user') 