import aiohttp
import asyncio

# Fallback rate if API fails
GLOBAL_USDT_RATE = 93.0

async def update_usdt_rate_task():
    """Background task to update the USDT rate every 5 minutes."""
    global GLOBAL_USDT_RATE
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=inr") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if "tether" in data and "inr" in data["tether"]:
                            GLOBAL_USDT_RATE = float(data["tether"]["inr"])
        except Exception as e:
            print(f"CoinGecko API Error: {e}")
        
        await asyncio.sleep(300) # Wait 5 minutes before checking again

def fmt_curr(inr_amt):
    """Converts INR to the ₹inr/$usdt format."""
    try:
        inr_amt = float(inr_amt)
        usdt_amt = inr_amt / GLOBAL_USDT_RATE
        return f"₹{inr_amt:.2f}/${usdt_amt:.2f}"
    except:
        return f"₹0.00/$0.00"
