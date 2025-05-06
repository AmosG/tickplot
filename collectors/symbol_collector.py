import asyncio
import json
from aiokafka import AIOKafkaProducer
from collectors.base import InstrumentType, CollectorStatus, CollectorProtocol
from ib_insync import IB, Stock
from datetime import datetime

class SymbolCollector:
    def __init__(self, symbol: str, instrument_type: InstrumentType, kafka_bootstrap_servers='localhost:9092'):
        self.symbol = symbol
        self.instrument_type = instrument_type
        self.kafka_bootstrap_servers = kafka_bootstrap_servers
        self.producer = None
        self.status = CollectorStatus.STOPPED
        self.running = False
        self.ib = None
        self.contract = None
        self.is_connected = False
        self.last_volume = None
        self.initialized = False

    async def start_producer(self):
        print(f"[{self.symbol}] Connecting to Kafka broker at {self.kafka_bootstrap_servers}...")
        self.producer = AIOKafkaProducer(bootstrap_servers=self.kafka_bootstrap_servers)
        await self.producer.start()
        print(f"[{self.symbol}] Connected to Kafka broker.")

    async def stop_producer(self):
        if self.producer:
            await self.producer.stop()
            print(f"[{self.symbol}] Kafka producer stopped.")

    async def connect_to_interactive(self):
        print(f"[{self.symbol}] Connecting to Interactive Brokers...")
        self.ib = IB()
        await self.ib.connectAsync('127.0.0.1', 4001, clientId=1)
        self.is_connected = True
        print(f"[{self.symbol}] Connected to Interactive Brokers.")
        # Only handle stocks for now
        self.contract = Stock(self.symbol, 'SMART', 'USD')
        self.ib.reqMktData(self.contract, '', False, False)

    async def collect_ticks(self):
        await self.connect_to_interactive()
        print(f"[{self.symbol}] Starting tick collection...")
        self.running = True
        loop = asyncio.get_event_loop()
        self.last_volume = None
        self.initialized = False

        def on_tick(tickers):
            for ticker in tickers:
                if hasattr(ticker, 'last') and ticker.last:
                    current_time = datetime.utcnow()
                    current_volume = getattr(ticker, 'volume', 0) or 0
                    if not self.initialized:
                        self.last_volume = current_volume
                        self.initialized = True
                        continue
                    tick_volume = max(0, current_volume - self.last_volume)
                    if tick_volume > 0:
                        tick = {
                            'symbol': self.symbol,
                            'instrument_type': self.instrument_type.name,
                            'timestamp': current_time.isoformat(),
                            'price': ticker.last,
                            'volume': tick_volume
                        }
                        # Schedule Kafka send in the event loop
                        asyncio.run_coroutine_threadsafe(
                            self.producer.send_and_wait('ticks', json.dumps(tick).encode()), loop
                        )
                    self.last_volume = current_volume

        self.ib.pendingTickersEvent += on_tick
        try:
            while self.running:
                await asyncio.sleep(1)
        finally:
            self.ib.disconnect()
            print(f"[{self.symbol}] Stopped tick collection.")

    async def run(self):
        self.running = True
        self.status = CollectorStatus.RUNNING
        await self.start_producer()
        try:
            await self.collect_ticks()
        finally:
            await self.stop_producer()
            self.status = CollectorStatus.STOPPED
            print(f"[{self.symbol}] Collector stopped.")

    def stop(self):
        self.running = False
        self.status = CollectorStatus.STOPPED 