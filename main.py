import asyncio
import sys
import argparse
from collectors.symbol_collector import SymbolCollector
from config.settings import KAFKA_BOOTSTRAP_SERVERS, SYMBOLS_TO_COLLECT

async def run_collectors():
    collectors = [
        SymbolCollector(symbol, inst_type, kafka_bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS)
        for symbol, inst_type in SYMBOLS_TO_COLLECT
    ]
    tasks = [asyncio.create_task(collector.run()) for collector in collectors]
    print(f"Started {len(collectors)} collectors.")
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

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('Process stopped by user') 