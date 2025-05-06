import asyncio
from collectors.symbol_collector import SymbolCollector
from config.settings import KAFKA_BOOTSTRAP_SERVERS, SYMBOLS_TO_COLLECT

async def main():
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

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('Collectors stopped by user') 