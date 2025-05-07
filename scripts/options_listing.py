# %%
import math
import pandas as pd
from datetime import datetime, timedelta
import pytz

# %%
from ib_insync import *

util.startLoop()  # uncomment this line when in a notebook
ib = IB()
# ib.disconnect()

# %%

ib.connect('127.0.0.1', 7497, clientId=23)

# %%
# ib.disconnect()


# %%

# Specify the underlying asset
symbol = 'ES'
exchange = 'CME'
currency = 'USD'
# underlying = Future(symbol, exchange, currency)
# contract = Future('ES', '', 'CME')
contract = Future('ES', '202306', 'CME')
qualified_contracts = ib.qualifyContracts(contract)
pd.DataFrame(qualified_contracts)



# %%
contract = qualified_contracts[0]
contract

# %%
# Calculate the start date for 3 months ago
start_date = datetime.now() - timedelta(3*365/12)
end_date = datetime.now().astimezone(pytz.UTC) # .timezone('US/Eastern') 
start_date = end_date - timedelta(3*365/12)

# Initialize an empty DataFrame to store the data
df = pd.DataFrame()

# Fetch one week of data at a time
while end_date > start_date:
    bars = ib.reqHistoricalData(
        contract,
        endDateTime=end_date,
        durationStr='1 W',
        barSizeSetting='1 min',
        whatToShow='TRADES',
        useRTH=False,
        formatDate=1,
        keepUpToDate=False)
    
    # Append the data to the DataFrame
    new_data = util.df(bars)
    df = df.append(new_data)
    
    # Update the end_date to be the start of the just fetched data
    # Convert the 'date' column to datetime before getting the min
    new_data['date'] = pd.to_datetime(new_data['date'])
    end_date = new_data['date'].min() - timedelta(minutes=1)


df

# %%
# Save the data to a CSV, indexed by date (ascending)
df.to_csv('es_data.csv', index=True)

# %%
df1 = pd.read_csv('es_data.csv', index_col=0)
df1

# %%
df

# %%

# Request the security definition option parameters
opt_params = ib.reqSecDefOptParams(underlyingSymbol=contract.symbol,
                                   futFopExchange=contract.exchange,
                                   underlyingSecType=contract.secType,
                                   underlyingConId=contract.conId)



# %%

df = pd.DataFrame(opt_params).explode('expirations')
df['expiration_date'] = pd.to_datetime(df['expirations'], format='%Y%m%d')
df.sort_values('expiration_date', inplace=True)
df

# %%
# Define a function to determine if a date is the third Friday of its month
def option_type(date):
    if date.weekday() == 4 and 15 <= date.day <= 21:  # if it's the third Friday
        return 'monthly'
    elif date.weekday() == 4:  # if it's any other Friday
        return 'weekly'
    else:  # if it's any other day
        return 'daily'

# Create a new column, 'option_type', that specifies whether each option is daily, weekly or monthly
df['option_type'] = df['expiration_date'].apply(option_type)

df

# %%

# Create a list of Option objects representing the options to include in the DataFrame
options = []
for expiry in expiration_dates:
    for right in ['C', 'P']:
        for strike in opt_params[1]:
            if min_strike <= strike <= max_strike:
                option = Option(symbol, expiry, strike, right, exchange, currency)
                options.append(option)

# Retrieve the ticker data for the options
tickers = ib.reqTickers(*options)

# Convert the ticker data to a DataFrame
data = []
for ticker in tickers:
    data.append({'symbol': ticker.contract.symbol,
                 'expiry': ticker.contract.lastTradeDateOrContractMonth,
                 'strike': ticker.contract.strike,
                 'right': ticker.contract.right,
                 'bid': ticker.bid,
                 'ask': ticker.ask,
                 'last': ticker.last})
df = pd.DataFrame(data)

print(df)


