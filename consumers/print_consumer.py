import asyncio
import json
from aiokafka import AIOKafkaConsumer
from config.settings import KAFKA_BOOTSTRAP_SERVERS

async def consume(topic='ticks', group_id='print-consumer'):
    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=group_id,
        auto_offset_reset='earliest',
        enable_auto_commit=True
    )
    await consumer.start()
    print(f"Connected to Kafka at {KAFKA_BOOTSTRAP_SERVERS}, listening to topic '{topic}'...")
    try:
        async for msg in consumer:
            try:
                tick = json.loads(msg.value.decode())
                print(f"Received tick: {tick}")
            except Exception as e:
                print(f"Error decoding message: {e}")
    finally:
        await consumer.stop()

if __name__ == '__main__':
    try:
        asyncio.run(consume())
    except KeyboardInterrupt:
        print('Consumer stopped by user') 