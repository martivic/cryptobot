import websockets
import asyncio
import json
import websockets
import base58
import base64
import struct
import sys
import os

from datetime import datetime
from collections import defaultdict

from solana.rpc.types import MemcmpOpts
from solana.rpc.commitment import Confirmed
from solana.rpc.async_api import AsyncClient  # Ensure AsyncClient is here
from solana.rpc.api import Client
from solders.pubkey import Pubkey  # Ensure Pubkey is imported
#from solana.rpc.core import LAMPORTS_PER_SOL   
from config import *

# PumpPortal WebSocket URL
WS_URL = "wss://pumpportal.fun/api/data"

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import WSS_ENDPOINT, PUMP_PROGRAM

# Load the IDL JSON file
with open('./idl/pump_fun_idl.json', 'r') as f:
    idl = json.load(f)

# Extract the "create" instruction definition
create_instruction = next(instr for instr in idl['instructions'] if instr['name'] == 'create')

# Dictionary to track creators and their token counts
creator_tracker = defaultdict(int)

def format_sol(value):
    return f"{value:.6f} SOL"

def format_timestamp(timestamp):
    return datetime.fromtimestamp(timestamp / 1000).strftime('%Y-%m-%d %H:%M:%S')


def parse_create_instruction(data):
    if len(data) < 8:
        return None
    offset = 8
    parsed_data = {}

    # Parse fields based on CreateEvent structure
    fields = [
        ('name', 'string'),
        ('symbol', 'string'),
        ('uri', 'string'),
        ('mint', 'publicKey'),
        ('bondingCurve', 'publicKey'),
        ('user', 'publicKey'),
    ]

    try:
        for field_name, field_type in fields:
            if field_type == 'string':
                length = struct.unpack('<I', data[offset:offset+4])[0]
                offset += 4
                value = data[offset:offset+length].decode('utf-8')
                offset += length
            elif field_type == 'publicKey':
                value = base58.b58encode(data[offset:offset+32]).decode('utf-8')
                offset += 32

            parsed_data[field_name] = value

        return parsed_data
    except:
        return None

def print_transaction_details(log_data):
    print(f"Signature: {log_data.get('signature')}")
    
    for log in log_data.get('logs', []):
        if log.startswith("Program data:"):
            try:
                data = base58.b58decode(log.split(": ")[1]).decode('utf-8')
                print(f"Data: {data}")
            except:
                pass

def analyze_token(token_info):
    """ Dynamically analyze and rank token viability for short-term trades """
    name = token_info.get('name')
    initial_buy = token_info.get('initialBuy', 0)  # Amount of SOL first put into it
    market_cap = token_info.get('marketCapSol', 0)  # Total market cap in SOL
    virtual_sol = token_info.get('vSolInBondingCurve', 0)  # SOL in bonding curve
    virtual_tokens = token_info.get('vTokensInBondingCurve', 0)  # Total token supply

    creator_holdings = token_info.get('creatorHoldings', 0)
    total_supply = token_info.get('vTokensInBondingCurve', 1)  # Avoid division by zero
    #creator_ownership_ratio = creator_holdings / virtual_tokens
    
    creator_ownership_ratio = creator_holdings / total_supply if total_supply > 0 else 0
    print(f"🔍 Debug Info for {name}:")
    print(f"   🏦 Creator Holdings: {creator_holdings}")
    print(f"   📈 Total Supply: {total_supply}")
    print(f"   📊 Ownership Ratio: {creator_ownership_ratio:.2%}")
    print(f"  - Initial Buy (SOL): {initial_buy}")
    print(f"  - Market Cap (SOL): {market_cap}")
    print(f"  - Virtual SOL in Bonding Curve: {virtual_sol}")

    #print(f"ouput for 3 {creator_holdings} and {total_supply} and {creator_ownership_ratio}")
    #if creator_holdings / total_supply > 0.90:
     #   print(f"🚨 RUG PULL ALERT: {token_info['name']} - Creator holds more than 90% of supply!")
      #  return True
    #return False
    # **New Weighted Scoring System**
    score = (
        (100 / (market_cap + 1)) * 1.5  # Weight market cap higher
        + (virtual_sol * 5)  # SOL in bonding curve (influences price rise)
        - (initial_buy / 100000)  # Lower score if huge initial buy (reduces rug risk)
    )

    print(f"📊 Analysis Score for {name}: {score:.2f}")
    if score >= 100:
        print(f"🚀 Auto-buy triggered for {name}!")

        mint_address = token_info.get('mint')
        bonding_curve = token_info.get('bondingCurveKey')
        associated_bonding_curve = token_info.get('bondingCurveKey')  # Adjust if needed
        name = token_info.get('name')
        # Debugging prints to verify all values
        print(f"Debug Info - Mint: {mint_address}, Bonding Curve: {bonding_curve}, Associated: {associated_bonding_curve}")
        print(f"for {name} is name.")
        # Check if any values are None before running subprocess
        if not mint_address or not bonding_curve or not associated_bonding_curve:
            print("⚠️ Missing required parameters, skipping auto-buy.")
            return score

# Dictionary to track creators and their previous tokens
creator_token_history = defaultdict(list)

async def track_creator_history(creator, token_name):
    """ Tracks how many tokens a creator has launched """
    creator_token_history[creator].append(token_name)
    print(f"👨‍💻 Creator {creator} has launched {len(creator_token_history[creator])} tokens.")
    print(f"📝 Previous tokens: {', '.join(creator_token_history[creator][-3:])}")  # Show last 3 tokens


# ✅ Token Distribution Check
async def get_token_distribution(conn: AsyncClient, mint: Pubkey):
    """Fetch token holder distribution and return the top holder's percentage"""
    try:
        response = await conn.get_token_largest_accounts(mint)
    
        #if not response.value or len(response.value) == 0:
        if not hasattr(response, 'value') or response.value is None or isinstance(response, InvalidParamsMessage):
            print("⚠️ RPC error: No token holders found! Skipping this token.")
            return 100  # None

        largest_accounts = response.value
        if not largest_accounts:
            print("⚠️ No largest accounts found! Assuming 100% risk.")
            return 100

        total_supply = sum(int(account.amount.amount) for account in largest_accounts if hasattr(account.amount, "amount"))
    
        if total_supply == 0:
            print("⚠️ Total supply is zero! Skipping.")
            return 100 #None

    # Get top holder's percentage
        top_holder_amount = int(largest_accounts[0].amount.amount) if hasattr(largest_accounts[0].amount, "amount") else 0
        top_holder_percentage = (top_holder_amount / total_supply) * 100

        print(f"👤 Top holder owns: {top_holder_percentage:.2f}% of the supply.")

        return top_holder_percentage
    except Exception as e:
        print(f"⚠️ Exception occurred while fetching token distribution: {str(e)}. Assuming 100% ownership risk.")
        return 100  # Assume the worst-case scenario for safety

async def get_creator_sol_balance(conn: AsyncClient, creator_pubkey: str):
    """Fetches the SOL balance of the token creator."""
    try:
        response = await conn.get_balance(Pubkey.from_string(creator_pubkey))
        if not hasattr(response, 'value') or response.value is None:
            print(f"⚠️ RPC Error: Unable to fetch SOL balance for {creator_pubkey}. Assuming 0 SOL.")
            return 0  # Assume the creator has no SOL (high risk)

        sol_balance = response.value / LAMPORTS_PER_SOL
        print(f"👤 Creator SOL Balance: {sol_balance:.2f} SOL")
        return sol_balance

    except Exception as e:
        print(f"⚠️ Exception occurred while fetching SOL balance: {str(e)}. Assuming 0 SOL.")
        return 0

async def is_rug_pull(client: AsyncClient, mint: Pubkey, creator: str):
    """Runs rug pull checks before buying."""
    print("🔎 Checking for potential rug pull...")

    # ✅ 1️⃣ Get Creator SOL Balance
    creator_sol = await get_creator_sol_balance(client, creator)
    if creator_sol < 1:  # 🚨 Less than 1 SOL = Likely a scam
        print("❌ WARNING: Creator has low SOL balance (<1 SOL). Possible rug!")
        return True

    # ✅ 2️⃣ Check Token Distribution (Top holder %)
    top_holder_percentage = await get_token_distribution(client, mint)
    #if top_holder_percentage is None or top_holder_percentage >= 90:
    if top_holder_percentage >= 90:
        print("❌ WARNING: Creator owns 90%+ of the supply. Skipping!")
        return True

    print("✅ Token passed rug pull checks.")
    return False  # 🚀 Safe to trade



async def listen_for_new_tokens():
    async with websockets.connect(WS_URL) as websocket:
       
        # Subscribe to new token events
        await websocket.send(json.dumps({
            "method": "subscribeNewToken",
            "params": []
        }))

        print("Listening for new token creations...")

        async with websockets.connect(WSS_ENDPOINT) as websocket_sol:
            await websocket_sol.send(json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "logsSubscribe",
                "params": [
                    {"mentions": [str(PUMP_PROGRAM)]},
                    {"commitment": "processed"}
                ]
            }))
            print(f"Listening for new token creations from program: {PUMP_PROGRAM}")

            while True:
                try:
                    message = await websocket.recv()
                    data = json.loads(message)
                    
                    if 'method' in data and data['method'] == 'newToken':
                        token_info = data.get('params', [{}])[0]
                    elif 'signature' in data and 'mint' in data:
                        token_info = data
                    else:
                        continue
                    
                    creator = token_info.get('traderPublicKey')
                    token_name = token_info.get('name')
                    token_symbol = token_info.get('symbol')

                    token_mint = token_info.get('mint')
                    bonding_curve = token_info.get('bonding_curve')
                    associated_bonding_curve = token_info.get('associated_bonding_curve')
                    token_user = token_info.get('user')

                    print("Token Data:")
                    print("Name:               ", token_name)
                    print("Symbol:             ", token_symbol)
                    print("Mint:               ", token_mint)
                    print("Bonding Curve:      ", bonding_curve)
                    print("Associated Curve:   ", associated_bonding_curve)
                    print("Creator:            ", creator)
                    print("user                ", token_user)

                    creator_tracker[creator] += 1
                    print("\n" + "=" * 50)
                    print(f"New token created: {token_name} ({token_symbol})")
                    print("=" * 50)
                    print(f"Address:        {token_info.get('mint')}")
                    print(f"Creator:        {creator}")
                    print(f"Initial Buy:    {format_sol(token_info.get('initialBuy', 0))}")
                    print(f"Market Cap:     {format_sol(token_info.get('marketCapSol', 0))}")
                    print(f"Bonding Curve:  {token_info.get('bondingCurveKey')}")
                    print(f"Virtual SOL:    {format_sol(token_info.get('vSolInBondingCurve', 0))}")
                    print(f"Virtual Tokens: {token_info.get('vTokensInBondingCurve', 0):,.0f}")
                    print(f"Metadata URI:   {token_info.get('uri')}")
                    print(f"Signature:      {token_info.get('signature')}")
                    print(f"creator holdings {token_info.get('creatorHoldings')}")
                    
                    
                    print("=" * 50)

                    try:
                        async with AsyncClient(RPC_ENDPOINT) as client:
                            if await is_rug_pull(client, Pubkey.from_string(token_info['mint']), creator):
                                print(f"🚨 Skipping {token_name} due to rug pull risk!")
                                continue  # Skip this token
                    except Exception as e:
                        print(f"⚠️ An unexpected error occurred during rug pull checks: {str(e)}. Skipping this token.")
                        continue
                    score = analyze_token(token_info)
                    if score > 50:
                        print(f"🔥 High-potential token detected: {token_name} ({token_symbol})")


                    if creator_tracker[creator] > 1:
                        print(f"⚠️ ALERT: Creator {creator} has launched {creator_tracker[creator]} tokens!")
                     
                except websockets.exceptions.ConnectionClosed:
                    print("\nWebSocket connection closed. Reconnecting...")
                    break
                except json.JSONDecodeError:
                    print(f"\nReceived non-JSON message: {message}")
                except Exception as e:
                    print(f"\nAn error occurred: {e}")
            

if __name__ == "__main__":
    asyncio.run(listen_for_new_tokens())