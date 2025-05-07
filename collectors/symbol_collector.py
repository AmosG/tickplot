import asyncio
import json
from aiokafka import AIOKafkaProducer
from collectors.base import InstrumentType, CollectorStatus, CollectorProtocol
from ib_insync import IB, Stock, Option, Future
from datetime import datetime, timedelta
from utils.logger import get_logger

logger = get_logger(__name__)

# Counter for generating unique client IDs
_next_client_id = 0

def get_next_client_id():
    global _next_client_id
    client_id = _next_client_id
    _next_client_id += 1
    return client_id

class SymbolCollector:
    def __init__(self, symbol: str, instrument_type: InstrumentType, kafka_bootstrap_servers='localhost:9092', 
                 num_strikes=5, expiration_days=30):
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
        self.num_strikes = num_strikes  # Number of strikes above and below current price
        self.expiration_days = expiration_days  # Days to expiration for options
        self.option_contracts = []
        self.last_option_volumes = {}
        self.client_id = get_next_client_id()

    async def start_producer(self):
        logger.info(f"[{self.symbol}] Connecting to Kafka broker at {self.kafka_bootstrap_servers}...")
        self.producer = AIOKafkaProducer(bootstrap_servers=self.kafka_bootstrap_servers)
        await self.producer.start()
        logger.info(f"[{self.symbol}] Connected to Kafka broker.")

    async def stop_producer(self):
        if self.producer:
            await self.producer.stop()
            logger.info(f"[{self.symbol}] Kafka producer stopped.")

    async def connect_to_interactive(self):
        logger.info(f"[{self.symbol}] Connecting to Interactive Brokers with client ID {self.client_id}...")
        self.ib = IB()
        try:
            await self.ib.connectAsync('127.0.0.1', 4001, clientId=self.client_id)
            self.is_connected = True
            logger.info(f"[{self.symbol}] Connected to Interactive Brokers.")
            
            if self.instrument_type == InstrumentType.STOCK:
                self.contract = Stock(self.symbol, 'SMART', 'USD')
                self.ib.reqMktData(self.contract, '', False, False)
            elif self.instrument_type == InstrumentType.OPTION:
                try:
                    # First get the underlying stock to determine current price
                    underlying = Stock(self.symbol, 'SMART', 'USD')
                    logger.info(f"[{self.symbol}] Qualifying underlying stock contract...")
                    await self.ib.qualifyContractsAsync(underlying)
                    logger.info(f"[{self.symbol}] Requesting market data for underlying...")
                    self.ib.reqMktData(underlying, '', False, False)
                    # Wait for ticker data to arrive
                    for _ in range(20):  # Wait up to 2 seconds
                        await asyncio.sleep(0.1)
                        ticker = self.ib.ticker(underlying)
                        if ticker and (getattr(ticker, 'last', None) or (getattr(ticker, 'bid', None) and getattr(ticker, 'ask', None))):
                            break
                    else:
                        ticker = self.ib.ticker(underlying)
                        logger.warning(f"[{self.symbol}] Underlying ticker data not fully available after wait.")
                    
                    if not hasattr(ticker, 'last') or not ticker.last:
                        # Fall back to midpoint if last price not available
                        mid_price = (ticker.bid + ticker.ask) / 2 if hasattr(ticker, 'bid') and hasattr(ticker, 'ask') else 100.0
                        current_price = mid_price
                        logger.info(f"[{self.symbol}] Using midpoint price for underlying: {current_price}")
                    else:
                        current_price = ticker.last
                        logger.info(f"[{self.symbol}] Using last price for underlying: {current_price}")

                    # Dynamically fetch valid expirations and strikes
                    logger.info(f"[{self.symbol}] Fetching option security definition parameters...")
                    params = await self.ib.reqSecDefOptParamsAsync(self.symbol, '', 'STK', underlying.conId)
                    expirations = sorted(params[0].expirations)
                    strikes = sorted(params[0].strikes)
                    logger.info(f"[{self.symbol}] Got {len(expirations)} expirations and {len(strikes)} strikes from IB.")

                    # Pick the nearest expiration (or use config logic)
                    if self.expiration_days == 0:
                        # Use the soonest available expiration
                        target_expiry = expirations[0]
                    else:
                        # Find the expiration closest to now + expiration_days
                        target_date = (datetime.now() + timedelta(days=self.expiration_days)).strftime('%Y%m%d')
                        # Find the expiration closest to target_date
                        target_expiry = min(expirations, key=lambda x: abs(int(x) - int(target_date)))
                    logger.info(f"[{self.symbol}] Using target expiry: {target_expiry}")

                    # Pick N strikes closest to underlying price
                    closest_strikes = sorted(strikes, key=lambda x: abs(x - current_price))[:self.num_strikes*2+1]
                    logger.info(f"[{self.symbol}] Using closest strikes: {closest_strikes}")

                    # Create option contracts for calls and puts
                    self.option_contracts = []
                    for strike in closest_strikes:
                        for right in ['C', 'P']:
                            option = Option(self.symbol, target_expiry, strike, right, 'SMART', '100', 'USD')
                            self.option_contracts.append(option)

                    logger.info(f"[{self.symbol}] Created {len(self.option_contracts)} option contracts, qualifying now...")
                    qualified_contracts = await self.ib.qualifyContractsAsync(*self.option_contracts)
                    valid_contracts = [c for c in qualified_contracts if c.conId]
                    logger.info(f"[{self.symbol}] Successfully qualified {len(valid_contracts)} of {len(self.option_contracts)} option contracts")

                    # Only keep valid contracts
                    self.option_contracts = valid_contracts

                    # Request market data for all options
                    for contract in self.option_contracts:
                        logger.info(f"[{self.symbol}] Requesting market data for {contract.right} {contract.strike} {contract.lastTradeDateOrContractMonth}")
                        self.ib.reqMktData(contract, '', False, False)
                        self.last_option_volumes[contract.conId] = 0

                    logger.info(f"[{self.symbol}] Subscribed to {len(self.option_contracts)} option contracts")
                except Exception as e:
                    logger.error(f"[{self.symbol}] Failed to initialize option contracts: {str(e)}")
                    self.status = CollectorStatus.ERROR
                    raise
            else:
                raise ValueError(f"Instrument type {self.instrument_type} not supported yet")
        except Exception as e:
            logger.error(f"[{self.symbol}] Failed to connect to Interactive Brokers: {str(e)}")
            self.status = CollectorStatus.ERROR
            raise

    def on_tick(self, tickers):
        loop = asyncio.get_event_loop()
        for ticker in tickers:
            current_time = datetime.utcnow()
            
            # For stock tickers
            if hasattr(ticker, 'contract') and hasattr(ticker.contract, 'secType'):
                if ticker.contract.secType == 'STK' and hasattr(ticker, 'last') and ticker.last:
                    if self.instrument_type == InstrumentType.STOCK:
                        current_volume = getattr(ticker, 'volume', 0) or 0
                        if not self.initialized:
                            self.last_volume = current_volume
                            self.initialized = True
                            return
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
                            logger.info(f"[{self.symbol}] Sent tick: {tick}")
                        self.last_volume = current_volume
                
                # For option tickers
                elif ticker.contract.secType == 'OPT' and ticker.contract.conId in self.last_option_volumes:
                    # Log option ticker info even without volume
                    log_msg = f"Option ticker received: {ticker.contract.right} {ticker.contract.strike}"
                    if hasattr(ticker, 'last') and ticker.last:
                        log_msg += f", last={ticker.last}"
                    if hasattr(ticker, 'bid') and ticker.bid:
                        log_msg += f", bid={ticker.bid}"
                    if hasattr(ticker, 'ask') and ticker.ask:
                        log_msg += f", ask={ticker.ask}"
                    if hasattr(ticker, 'bidSize') and ticker.bidSize:
                        log_msg += f", bidSize={ticker.bidSize}"
                    if hasattr(ticker, 'askSize') and ticker.askSize:
                        log_msg += f", askSize={ticker.askSize}"
                    logger.debug(f"[{self.symbol}] {log_msg}")
                    
                    current_volume = getattr(ticker, 'volume', 0) or 0
                    last_volume = self.last_option_volumes.get(ticker.contract.conId, 0)
                    tick_volume = max(0, current_volume - last_volume)
                    
                    # Send option data regardless of volume change to make sure we're getting data
                    option_tick = {
                        'symbol': self.symbol,
                        'instrument_type': 'OPTION',
                        'option_type': ticker.contract.right,  # 'C' for call, 'P' for put
                        'strike': ticker.contract.strike,
                        'expiry': ticker.contract.lastTradeDateOrContractMonth,
                        'timestamp': current_time.isoformat(),
                        'price': ticker.last if hasattr(ticker, 'last') else None,
                        'volume': tick_volume,
                        'bid': ticker.bid if hasattr(ticker, 'bid') else None,
                        'ask': ticker.ask if hasattr(ticker, 'ask') else None,
                        'bid_size': ticker.bidSize if hasattr(ticker, 'bidSize') else None,
                        'ask_size': ticker.askSize if hasattr(ticker, 'askSize') else None
                    }
                    
                    # Schedule Kafka send in the event loop
                    asyncio.run_coroutine_threadsafe(
                        self.producer.send_and_wait('option_ticks', json.dumps(option_tick).encode()), loop
                    )
                    logger.debug(f"[{self.symbol}] Sent option tick: {ticker.contract.strike} {ticker.contract.right}")
                    
                    self.last_option_volumes[ticker.contract.conId] = current_volume

    async def collect_ticks(self):
        try:
            await self.connect_to_interactive()
            logger.info(f"[{self.symbol}] Starting tick collection...")
            self.running = True
            loop = asyncio.get_event_loop()
            self.last_volume = None
            self.initialized = False

            # Create a bound method for use with the event handler
            self.ib.pendingTickersEvent += self.on_tick
            
            try:
                while self.running:
                    await asyncio.sleep(1)
            finally:
                self.ib.disconnect()
                logger.info(f"[{self.symbol}] Stopped tick collection.")
        except Exception as e:
            logger.error(f"[{self.symbol}] Error in tick collection: {str(e)}")
            self.status = CollectorStatus.ERROR
            raise

    async def run(self):
        self.running = True
        self.status = CollectorStatus.RUNNING
        try:
            await self.start_producer()
            try:
                await self.collect_ticks()
            except Exception as e:
                logger.error(f"[{self.symbol}] Collector error: {str(e)}")
                self.status = CollectorStatus.ERROR
            finally:
                await self.stop_producer()
        except Exception as e:
            logger.error(f"[{self.symbol}] Failed to start producer: {str(e)}")
            self.status = CollectorStatus.ERROR
        finally:
            self.status = CollectorStatus.STOPPED
            logger.info(f"[{self.symbol}] Collector stopped.")

    def stop(self):
        self.running = False
        self.status = CollectorStatus.STOPPED 