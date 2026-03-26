# Hyperbot - Setup & Operation Manual

## Prerequisites
- Python 3.10 or higher
- A Hyperliquid account with API key (wallet private key)
- Windows, macOS, or Linux

---

## Step 1: Install Python Dependencies

Open a terminal/command prompt in the Hyperbot folder and run:

```bash
pip install -r requirements.txt
```

If you get errors with `hyperliquid-python-sdk`, you can skip it - the bot uses direct API calls:

```bash
pip install pandas numpy scipy pyyaml python-dotenv aiohttp websockets flask requests matplotlib pyarrow eth-account
```

---

## Step 2: Create the .env File

The `.env` file is NOT included in the download for security reasons. You must create it yourself.

**Create a file named `.env`** (exactly this name, with the dot) in the root Hyperbot folder (same folder as `main.py`).

### On Windows:
1. Open Notepad
2. Paste the content below
3. File → Save As → set "Save as type" to "All Files" → name it `.env` → save in the Hyperbot folder

### On macOS/Linux:
```bash
nano .env
```

### .env content:

```
HYPERLIQUID_API_KEY=your_wallet_private_key_here
HYPERLIQUID_API_SECRET=
HYPERLIQUID_WALLET_ADDRESS=your_wallet_address_here
HYPERLIQUID_MAINNET=true
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
DISCORD_WEBHOOK_URL=
```

Replace:
- `your_wallet_private_key_here` → your Hyperliquid API/wallet private key (starts with 0x...)
- `your_wallet_address_here` → your wallet public address (starts with 0x...)

Leave `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and `DISCORD_WEBHOOK_URL` blank unless you want notifications (see Step 6).

---

## Step 3: Configure the Bot

Edit `config.yaml` to customize settings. The defaults are safe to start with.

### Key settings:

```yaml
mode: "paper"          # "paper" = simulated, "live" = real money, "backtest" = test on history

assets:                # Which coins to trade
  - "BTC"
  - "ETH"
  - "SOL"
  - "ARB"
  - "DOGE"

risk:
  risk_per_trade: 0.015      # 1.5% of equity risked per trade
  max_leverage: 5.0           # Maximum 5x leverage
  max_concurrent_positions: 3 # Max 3 open positions at once

dashboard:
  port: 8080                  # Dashboard URL: http://localhost:8080
```

**Do NOT change `mode` to `"live"` until you have paper traded for at least 48 hours.**

---

## Step 4: Run the Bot

### Paper Trading (recommended first):
```bash
python main.py
```
This runs in simulated mode. No real money is used. It connects to Hyperliquid for real market data but only simulates trades.

### Backtest (test strategy on historical data):
```bash
python main.py --mode backtest
```
This downloads 90+ days of historical data and replays the strategy. Outputs performance metrics and equity curve charts to `backtest_results/`.

### Live Trading (real money):
```bash
python main.py --mode live
```
You will be asked to type `CONFIRM LIVE` before it starts. This uses real money on your Hyperliquid account.

**Recommended live progression:**
1. First 48+ hours: paper mode
2. Week 1-2: live at 25% size → set `risk_per_trade: 0.004` in config.yaml
3. Week 3-4: increase to 50% → set `risk_per_trade: 0.008`
4. After 1 month: full size → set `risk_per_trade: 0.015`

### Use a custom config file:
```bash
python main.py --config my_config.yaml
```

---

## Step 5: Monitor the Bot

### Dashboard
Once running, open your browser to:
```
http://localhost:8080
```

The dashboard shows:
- Account equity
- Open positions with PnL
- Recent trades
- Daily PnL chart (30 days)
- Drawdown gauge
- System status (WebSocket connection, mode)

The dashboard auto-refreshes every 10 seconds.

### Logs
Logs are in the `logs/` folder:
- `system.log` - General bot activity
- `trades.log` - All trade entries and exits
- `signals.log` - Signal scores for each asset
- `errors.log` - Errors only

### Database
All trades, signals, and equity snapshots are stored in `hyperbot.db` (SQLite). You can open it with any SQLite browser (e.g., DB Browser for SQLite).

---

## Step 6: Stop the Bot

### Normal shutdown:
Press `Ctrl+C` in the terminal. The bot will:
1. Cancel all open orders
2. Save data cache
3. Log the shutdown
4. Exit cleanly

**Positions are kept open** on shutdown (stop losses remain on the exchange).

### Emergency Kill Switch:
If you need to close ALL positions immediately:

**Option A - Dashboard:**
Click the red **KILL SWITCH** button at the top right of the dashboard.

**Option B - Browser:**
```
POST http://localhost:8080/kill
```

This will:
1. Cancel all open orders
2. Close all positions at market price
3. Stop the bot

---

## Step 7: Set Up Notifications (Optional)

### Telegram:
1. Message @BotFather on Telegram → `/newbot` → follow prompts → copy the token
2. Message @userinfobot to get your chat ID
3. Add to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=your_bot_token
   TELEGRAM_CHAT_ID=your_chat_id
   ```
4. In `config.yaml`, set:
   ```yaml
   notifications:
     enabled: true
     channels:
       telegram: true
   ```

### Discord:
1. In your Discord server → Server Settings → Integrations → Webhooks → New Webhook
2. Copy the webhook URL
3. Add to `.env`:
   ```
   DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
   ```
4. In `config.yaml`, set:
   ```yaml
   notifications:
     enabled: true
     channels:
       discord: true
   ```

### What gets notified:
- Every trade entry and exit (with PnL)
- Daily summary at 00:00 UTC
- Risk alerts (approaching limits, circuit breakers)
- System start/stop
- Disconnection warnings

---

## Step 8: Run 24/7 (Optional)

To keep the bot running permanently on a server:

### Linux (using screen):
```bash
screen -S hyperbot
python main.py
# Press Ctrl+A then D to detach
# To reattach: screen -r hyperbot
```

### Linux (using systemd):
Create `/etc/systemd/system/hyperbot.service`:
```ini
[Unit]
Description=Hyperbot Trading Bot
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/Hyperbot
ExecStart=/usr/bin/python3 main.py
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl enable hyperbot
sudo systemctl start hyperbot
sudo systemctl status hyperbot    # Check status
sudo journalctl -u hyperbot -f    # View logs
```

### Windows (using Task Scheduler):
1. Open Task Scheduler → Create Basic Task
2. Trigger: "When the computer starts"
3. Action: Start a Program
4. Program: `python` (or full path to python.exe)
5. Arguments: `main.py`
6. Start in: `C:\path\to\Hyperbot`

---

## Safety Features (Always Active)

| Protection | Trigger | Action |
|---|---|---|
| Max trade risk | > 2% equity | Trade blocked |
| Leverage cap | > 5x | Capped at 5x |
| Daily loss limit | > 3% loss in a day | Halt trading until next day |
| Weekly loss limit | > 5% loss in a week | Halt 24 hours |
| Max drawdown | > 10% from peak | Close ALL positions, halt 24h |
| Consecutive losses | 5 in a row | Pause 4 hours, 50% size for 3 trades |
| Low win rate | < 30% over 20 trades | Reduce to 50% size |
| Stale orders | Open > 2 minutes | Auto-cancelled |
| Time exit | 40+ candles, < 1 ATR profit | Close at market |
| Heartbeat | No activity 60 seconds | Alert sent |

---

## Troubleshooting

### "Cannot connect to Hyperliquid API"
- Check your internet connection
- Verify `HYPERLIQUID_MAINNET=true` in `.env`
- Make sure your API key is correct

### "Zero equity detected"
- Your wallet may not have funds deposited on Hyperliquid
- Deposit USDC to your Hyperliquid account first

### Bot crashes / restarts
- Check `logs/errors.log` for details
- The bot catches most errors and continues running
- If using systemd, it auto-restarts after 30 seconds

### No trades happening
- Check `logs/signals.log` - signals may not be strong enough
- Lower `signal_thresholds.normal_entry` in config.yaml (e.g., 0.4)
- Make sure market is active (low volume = fewer signals)

### Dashboard not loading
- Check the port isn't in use: change `dashboard.port` in config.yaml
- Make sure the bot is actually running
