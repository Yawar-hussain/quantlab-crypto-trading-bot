#!/usr/bin/env python3
"""
QUANT-LAB Trading Bot v2.0
Adaptive multi-strategy crypto trading system
Author: Muhammad Yawar Hussain — Ulster University MSc 2026
"""

import os, json, time, logging, math
from datetime import datetime, timezone
import requests

# ─── CONFIG ───────────────────────────────────────────────────────────────────
API_KEY    = os.getenv('ALPACA_API_KEY', '')
SECRET_KEY = os.getenv('ALPACA_SECRET_KEY', '')
BASE_URL   = 'https://paper-api.alpaca.markets/v2'
DATA_URL   = 'https://data.alpaca.markets/v1beta3'

HEADERS = {
    'APCA-API-KEY-ID':     API_KEY,
    'APCA-API-SECRET-KEY': SECRET_KEY,
}

START_CAPITAL   = 100_000.0
RISK_PER_TRADE  = 0.01        # 1% risk per trade
ATR_MULTIPLIER  = 1.5         # stop loss = 1.5x ATR
RR_RATIO        = 1.5         # reward:risk ratio
RSI_BUY         = 35          # RSI oversold threshold
RSI_SELL        = 65          # RSI overbought threshold
EMA_PERIOD      = 50
RSI_PERIOD      = 14
ATR_PERIOD      = 14
SCAN_INTERVAL   = 600         # scan every 10 minutes
MIN_CASH_PCT    = 0.05        # keep at least 5% cash

COINS = [
    'BTC/USD', 'ETH/USD', 'SOL/USD', 'DOT/USD',
    'LINK/USD', 'UNI/USD', 'LTC/USD', 'DOGE/USD',
    'ADA/USD', 'BCH/USD'
]

TRADES_FILE = os.path.expanduser('~/regime_bot_trades.json')
LOG_FILE    = os.path.expanduser('~/regime_bot.log')

# ─── LOGGING ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('quantlab')

# ─── ALPACA HELPERS ───────────────────────────────────────────────────────────
def api_get(path, params=None, base=None):
    url = (base or BASE_URL) + path
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if r.status_code == 200:
            return r.json()
        log.warning(f'API {path} → {r.status_code}: {r.text[:120]}')
    except Exception as e:
        log.error(f'API error {path}: {e}')
    return None

def api_post(path, data):
    try:
        r = requests.post(BASE_URL + path, headers=HEADERS, json=data, timeout=10)
        if r.status_code in (200, 201):
            return r.json()
        log.warning(f'POST {path} → {r.status_code}: {r.text[:200]}')
    except Exception as e:
        log.error(f'POST error {path}: {e}')
    return None

def api_delete(path):
    try:
        r = requests.delete(BASE_URL + path, headers=HEADERS, timeout=10)
        return r.status_code in (200, 204)
    except Exception as e:
        log.error(f'DELETE error {path}: {e}')
    return False

def get_account():
    return api_get('/account') or {}

def get_positions():
    return api_get('/positions') or []

def get_position(symbol):
    sym = symbol.replace('/', '')
    return api_get(f'/positions/{sym}')

def close_position(symbol):
    sym = symbol.replace('/', '')
    return api_delete(f'/positions/{sym}')

def place_order(symbol, qty, side, order_type='market',
                stop_loss=None, take_profit=None):
    sym = symbol.replace('/', '')
    data = {
        'symbol':        sym,
        'qty':           str(round(qty, 8)),
        'side':          side,
        'type':          order_type,
        'time_in_force': 'gtc',
    }
    if stop_loss and take_profit:
        data['order_class'] = 'bracket'
        data['stop_loss']   = {'stop_price': str(round(stop_loss, 6))}
        data['take_profit'] = {'limit_price': str(round(take_profit, 6))}
    result = api_post('/orders', data)
    if result:
        log.info(f'✅ ORDER {side.upper()} {sym} qty={qty:.6f} @ market')
    return result

# ─── MARKET DATA ──────────────────────────────────────────────────────────────
def get_bars(symbol, limit=100):
    """Get hourly OHLCV bars for a symbol."""
    sym = symbol.replace('/', '%2F')
    data = api_get(
        f'/crypto/us/bars',
        params={'symbols': symbol, 'timeframe': '1Hour', 'limit': limit},
        base=DATA_URL
    )
    if data and 'bars' in data:
        bars = data['bars'].get(symbol, [])
        return bars
    return []

def get_latest_price(symbol):
    """Get latest trade price."""
    data = api_get(
        '/crypto/us/latest/trades',
        params={'symbols': symbol},
        base=DATA_URL
    )
    if data and 'trades' in data:
        trade = data['trades'].get(symbol, {})
        return float(trade.get('p', 0))
    return 0.0

# ─── INDICATORS ───────────────────────────────────────────────────────────────
def ema(prices, period):
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    val = sum(prices[:period]) / period
    for p in prices[period:]:
        val = p * k + val * (1 - k)
    return val

def rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i+1] - prices[i] for i in range(len(prices)-1)]
    gains  = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]
    avg_gain = sum(gains[-period:]) / period if gains else 0.0001
    avg_loss = sum(losses[-period:]) / period if losses else 0.0001
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def atr(bars, period=14):
    if len(bars) < period + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        h = float(bars[i]['h'])
        l = float(bars[i]['l'])
        pc = float(bars[i-1]['c'])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period

def bollinger(prices, period=20, std_dev=2):
    if len(prices) < period:
        return None, None, None
    window = prices[-period:]
    mid = sum(window) / period
    variance = sum((p - mid)**2 for p in window) / period
    std = math.sqrt(variance)
    return mid - std_dev * std, mid, mid + std_dev * std

# ─── TRADE LOG ────────────────────────────────────────────────────────────────
def load_trades():
    if not os.path.exists(TRADES_FILE):
        return []
    try:
        with open(TRADES_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else data.get('trades', [])
    except:
        return []

def save_trade(symbol, side, qty, price, pnl, strategy, entry_time=None):
    trades = load_trades()
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    trade = {
        'symbol':     symbol,
        'side':       side,
        'qty':        round(float(qty), 8),
        'price':      round(float(price), 6),
        'pnl':        round(float(pnl), 2),
        'strategy':   strategy,
        'time':       now,
        'entry_time': entry_time or now,
        'hold_mins':  0,
    }
    if entry_time:
        try:
            fmt = '%Y-%m-%d %H:%M:%S'
            entry_dt = datetime.strptime(entry_time, fmt)
            exit_dt  = datetime.strptime(now, fmt)
            trade['hold_mins'] = int((exit_dt - entry_dt).total_seconds() / 60)
        except:
            pass
    trades.insert(0, trade)
    with open(TRADES_FILE, 'w') as f:
        json.dump(trades[:500], f, indent=2)
    log.info(f'📝 TRADE SAVED: {symbol} {side} P&L=${pnl:.2f} Strategy={strategy}')

# ─── BEAR MARKET GUARD ────────────────────────────────────────────────────────
class BearMarketGuard:
    def __init__(self):
        self.peak = START_CAPITAL
        self.regime = 'BULL'

    def update(self, portfolio_value):
        if portfolio_value > self.peak:
            self.peak = portfolio_value
        drawdown = (self.peak - portfolio_value) / self.peak * 100 if self.peak > 0 else 0

        if drawdown >= 10:
            self.regime = 'CRASH'
        elif drawdown >= 5:
            self.regime = 'BEAR'
        elif drawdown >= 2:
            self.regime = 'CAUTION'
        else:
            self.regime = 'BULL'

        log.info(f'🛡️  Bear Guard: {self.regime} | Drawdown: {drawdown:.2f}% | Peak: ${self.peak:,.2f}')
        return self.regime

    def size_multiplier(self):
        return {'BULL': 1.0, 'CAUTION': 0.5, 'BEAR': 0.0, 'CRASH': 0.0}[self.regime]

    def should_trade(self):
        return self.regime in ('BULL', 'CAUTION')

# ─── STRATEGIES ───────────────────────────────────────────────────────────────
def signal_mean_reversion(bars):
    """RSI + Bollinger Band mean reversion strategy."""
    if len(bars) < 30:
        return None
    closes = [float(b['c']) for b in bars]
    current_rsi = rsi(closes, RSI_PERIOD)
    bb_low, bb_mid, bb_high = bollinger(closes)
    price = closes[-1]

    if current_rsi < RSI_BUY and bb_low and price < bb_low:
        return {'signal': 'BUY', 'strategy': 'MEAN_REV',
                'reason': f'RSI={current_rsi:.1f} oversold + below BB lower'}
    if current_rsi > RSI_SELL and bb_high and price > bb_high:
        return {'signal': 'SELL', 'strategy': 'MEAN_REV',
                'reason': f'RSI={current_rsi:.1f} overbought + above BB upper'}
    return None

def signal_trend_following(bars):
    """EMA-50 trend following strategy."""
    if len(bars) < EMA_PERIOD + 5:
        return None
    closes = [float(b['c']) for b in bars]
    ema50 = ema(closes, EMA_PERIOD)
    price = closes[-1]
    prev  = closes[-2]

    if ema50 and price > ema50 and prev < ema50:
        return {'signal': 'BUY', 'strategy': 'TREND',
                'reason': f'Price crossed above EMA50 (${ema50:.4f})'}
    if ema50 and price < ema50 and prev > ema50:
        return {'signal': 'SELL', 'strategy': 'TREND',
                'reason': f'Price crossed below EMA50 (${ema50:.4f})'}
    return None

def signal_volatility_guard(bars):
    """ATR volatility guard — skip entries during high volatility spikes."""
    if len(bars) < ATR_PERIOD + 5:
        return None
    current_atr = atr(bars, ATR_PERIOD)
    prev_atr    = atr(bars[:-5], ATR_PERIOD)
    if current_atr and prev_atr and current_atr > prev_atr * 2:
        return {'signal': 'GUARD', 'strategy': 'VOL_GUARD',
                'reason': f'ATR spike detected: {current_atr:.4f} vs {prev_atr:.4f}'}
    return None

# ─── POSITION SIZING ──────────────────────────────────────────────────────────
def calc_position_size(price, atr_val, cash, multiplier=1.0):
    """Risk 1% of portfolio per trade, sized by ATR."""
    if not atr_val or price <= 0:
        return 0
    risk_amount  = START_CAPITAL * RISK_PER_TRADE * multiplier
    stop_dist    = atr_val * ATR_MULTIPLIER
    raw_qty      = risk_amount / stop_dist
    max_qty      = (cash * 0.20) / price   # max 20% of cash per trade
    qty          = min(raw_qty, max_qty)
    return max(qty, 0)

# ─── MAIN BOT ─────────────────────────────────────────────────────────────────
class QuantLabBot:
    def __init__(self):
        self.bear_guard  = BearMarketGuard()
        self.open_trades = {}   # symbol → entry info
        log.info('=' * 60)
        log.info('  QUANT-LAB Trading Bot v2.0 — Starting')
        log.info(f'  Coins: {len(COINS)} | Risk/trade: {RISK_PER_TRADE*100}%')
        log.info('=' * 60)

    def check_open_positions(self, positions, bars_map):
        """Check existing positions for take-profit / stop-loss."""
        for pos in positions:
            symbol   = pos.get('symbol', '')
            qty      = float(pos.get('qty', 0))
            entry_px = float(pos.get('avg_entry_price', 0))
            curr_px  = float(pos.get('current_price', 0))
            unreal   = float(pos.get('unrealized_pl', 0))
            side     = pos.get('side', 'long')

            # Find matching coin
            coin = None
            for c in COINS:
                if c.replace('/', '') == symbol:
                    coin = c
                    break
            if not coin:
                continue

            bars = bars_map.get(coin, [])
            if not bars:
                continue

            atr_val  = atr(bars, ATR_PERIOD) or (entry_px * 0.02)
            stop_px  = entry_px - atr_val * ATR_MULTIPLIER
            tp_px    = entry_px + atr_val * ATR_MULTIPLIER * RR_RATIO

            pnl_pct = ((curr_px - entry_px) / entry_px * 100) if entry_px > 0 else 0

            log.info(f'📊 {symbol} [{side}] Entry:${entry_px:.4f} Now:${curr_px:.4f} '
                     f'({pnl_pct:+.1f}%) SL:${stop_px:.4f} TP:${tp_px:.4f}')

            should_close = False
            close_reason = ''

            if side == 'long':
                if curr_px <= stop_px:
                    should_close = True
                    close_reason = f'STOP LOSS hit @ ${curr_px:.4f}'
                elif curr_px >= tp_px:
                    should_close = True
                    close_reason = f'TAKE PROFIT hit @ ${curr_px:.4f}'

            # Bear guard override
            if not self.bear_guard.should_trade() and self.bear_guard.regime == 'BEAR':
                should_close = True
                close_reason = f'BEAR MARKET regime — emergency exit'

            if should_close:
                log.info(f'🔴 CLOSING {symbol}: {close_reason}')
                result = close_position(coin)
                if result is not False:
                    entry_time = self.open_trades.get(symbol, {}).get('time')
                    save_trade(symbol, 'sell', qty, curr_px, unreal,
                               self.open_trades.get(symbol, {}).get('strategy', 'MEAN_REV'),
                               entry_time)
                    self.open_trades.pop(symbol, None)
                else:
                    log.warning(f'⚠️  Could not close {symbol}')

    def scan_entries(self, bars_map, cash, portfolio_value):
        """Scan all coins for entry signals."""
        if not self.bear_guard.should_trade():
            log.info(f'⛔ Bear Guard [{self.bear_guard.regime}] — skipping all entries')
            return

        min_cash = portfolio_value * MIN_CASH_PCT
        if cash < min_cash:
            log.info(f'💰 Low cash (${cash:.2f}) — skipping entries')
            return

        positions = get_positions()
        held = {p['symbol'] for p in positions}
        size_mult = self.bear_guard.size_multiplier()

        entries = 0
        for coin in COINS:
            sym = coin.replace('/', '')
            if sym in held:
                continue   # already holding

            bars = bars_map.get(coin, [])
            if len(bars) < 30:
                continue

            # Check volatility guard first
            vol = signal_volatility_guard(bars)
            if vol and vol['signal'] == 'GUARD':
                log.info(f'🛡️  {coin} — {vol["reason"]} — skipping')
                continue

            # Get signals
            mr_sig   = signal_mean_reversion(bars)
            tr_sig   = signal_trend_following(bars)
            signal   = mr_sig or tr_sig

            if not signal or signal['signal'] != 'BUY':
                continue

            price   = float(bars[-1]['c'])
            atr_val = atr(bars, ATR_PERIOD)
            if not atr_val or price <= 0:
                continue

            qty = calc_position_size(price, atr_val, cash, size_mult)
            if qty <= 0 or qty * price < 1.0:
                continue

            stop_px = price - atr_val * ATR_MULTIPLIER
            tp_px   = price + atr_val * ATR_MULTIPLIER * RR_RATIO

            log.info(f'🟢 {signal["strategy"]} ENTRY {coin}: {signal["reason"]}')
            log.info(f'   Price=${price:.4f} Qty={qty:.6f} SL=${stop_px:.4f} TP=${tp_px:.4f}')

            result = place_order(coin, qty, 'buy', stop_loss=stop_px, take_profit=tp_px)
            if result:
                now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                self.open_trades[sym] = {
                    'strategy': signal['strategy'],
                    'time': now,
                    'entry_price': price,
                }
                save_trade(sym, 'buy', qty, price, 0, signal['strategy'])
                cash -= qty * price
                entries += 1
                if entries >= 3:   # max 3 new entries per scan
                    break

        log.info(f'🔍 Scan complete — {entries} new entries')

    def run(self):
        log.info('🚀 Bot running — scanning every 10 minutes')
        while True:
            try:
                log.info('-' * 50)
                log.info(f'⏰ Scan at {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")} UTC')

                # Account state
                account  = get_account()
                port_val = float(account.get('portfolio_value', START_CAPITAL))
                cash     = float(account.get('cash', 0))

                log.info(f'💼 Portfolio: ${port_val:,.2f} | Cash: ${cash:,.2f}')

                # Bear guard update
                regime = self.bear_guard.update(port_val)

                # Fetch bars for all coins
                log.info(f'📡 Fetching data for {len(COINS)} coins...')
                bars_map = {}
                for coin in COINS:
                    bars = get_bars(coin, limit=100)
                    if bars:
                        bars_map[coin] = bars
                    time.sleep(0.3)   # rate limit

                log.info(f'📊 Got data for {len(bars_map)}/{len(COINS)} coins')

                # Check open positions
                positions = get_positions()
                if positions:
                    log.info(f'📈 Checking {len(positions)} open positions...')
                    self.check_open_positions(positions, bars_map)

                # Scan for entries
                log.info(f'🔍 Scanning {len(COINS)} symbols...')
                self.scan_entries(bars_map, cash, port_val)

                log.info(f'⏳ Next scan in {SCAN_INTERVAL//60} min...')
                time.sleep(SCAN_INTERVAL)

            except KeyboardInterrupt:
                log.info('🛑 Bot stopped by user')
                break
            except Exception as e:
                log.error(f'❌ Bot error: {e}', exc_info=True)
                time.sleep(60)

if __name__ == '__main__':
    QuantLabBot().run()
