#!/usr/bin/env python3
"""
QUANT-LAB Dashboard Server v2.0
Live dashboard backend — connected to new Alpaca paper account
Author: Muhammad Yawar Hussain — Ulster University MSc 2026
"""

import json, os, re, subprocess, statistics
from datetime import datetime, timezone
from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
import requests

app = Flask(__name__, static_folder='.')
CORS(app)

# ─── CONFIG — NEW ALPACA ACCOUNT ──────────────────────────────────────────────
API_KEY    = os.getenv('ALPACA_API_KEY', '')
SECRET_KEY = os.getenv('ALPACA_SECRET_KEY', '')
BASE_URL   = 'https://paper-api.alpaca.markets/v2'
DATA_URL   = 'https://data.alpaca.markets/v1beta3'
START_CAPITAL = 100_000.0

LOG_FILE    = os.path.expanduser('~/regime_bot.log')
TRADES_FILE = os.path.expanduser('~/regime_bot_trades.json')

HEADERS = {
    'APCA-API-KEY-ID':     API_KEY,
    'APCA-API-SECRET-KEY': SECRET_KEY,
}

# ─── ALPACA HELPERS ───────────────────────────────────────────────────────────
def alpaca_get(path, params=None, base=None):
    try:
        r = requests.get((base or BASE_URL) + path,
                         headers=HEADERS, params=params, timeout=8)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f'Alpaca error {path}: {e}')
    return None

def get_account():      return alpaca_get('/account') or {}
def get_positions():    return alpaca_get('/positions') or []
def get_orders(limit=100):
    return alpaca_get('/orders', params={'status': 'closed', 'limit': limit, 'direction': 'desc'}) or []
def get_portfolio_history(period='1D', timeframe='5Min'):
    return alpaca_get('/account/portfolio/history',
                      params={'period': period, 'timeframe': timeframe,
                              'extended_hours': 'true'}) or {}

# ─── TRADE FILE ───────────────────────────────────────────────────────────────
def load_trades():
    if not os.path.exists(TRADES_FILE):
        return []
    try:
        with open(TRADES_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else data.get('trades', [])
    except:
        return []

def read_log(n=50):
    if not os.path.exists(LOG_FILE):
        return [f'⏳ Waiting for bot log at {LOG_FILE}']
    try:
        result = subprocess.run(['tail', '-n', str(n), LOG_FILE],
                                capture_output=True, text=True, timeout=5)
        lines = result.stdout.strip().split('\n') if result.stdout else []
        return [l for l in lines if l.strip()] or ['Log is empty']
    except Exception as e:
        return [f'Error reading log: {e}']

# ─── STATS CALCULATION ────────────────────────────────────────────────────────
def compute_stats(account, trades, positions):
    portfolio_value = float(account.get('portfolio_value', START_CAPITAL))
    cash            = float(account.get('cash', 0))
    equity          = float(account.get('equity', portfolio_value))
    last_equity     = float(account.get('last_equity', equity))

    # True P&L vs starting capital
    total_pnl  = portfolio_value - START_CAPITAL
    pnl_pct    = (total_pnl / START_CAPITAL) * 100
    daily_pnl  = equity - last_equity
    daily_pct  = (daily_pnl / last_equity * 100) if last_equity > 0 else 0

    # Unrealised P&L from open positions
    unrealised = sum(float(p.get('unrealized_pl', 0)) for p in positions)

    # Closed trade stats
    closed = [t for t in trades if t.get('side') == 'sell']
    pnls   = [float(t.get('pnl', 0)) for t in closed]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    n      = len(closed)
    win_rate = (len(wins) / n * 100) if n > 0 else 0

    best  = max(pnls, default=0)
    worst = min(pnls, default=0)

    # Sharpe ratio
    if len(pnls) > 5:
        mean_r = statistics.mean(pnls)
        std_r  = statistics.stdev(pnls) if len(pnls) > 1 else 1
        sharpe = round((mean_r / std_r * (252 ** 0.5)) if std_r > 0 else 0, 2)
    else:
        sharpe = 0.0

    # Max drawdown from portfolio history
    hist = get_portfolio_history('1M', '1Day')
    max_dd = 0.0
    if hist and hist.get('equity'):
        equities = hist['equity']
        peak = equities[0] if equities else START_CAPITAL
        for val in equities:
            if val and val > peak:
                peak = val
            if val and peak > 0:
                dd = (peak - val) / peak * 100
                max_dd = max(max_dd, dd)

    # Average hold time
    hold_times = [t.get('hold_mins', 0) for t in closed if t.get('hold_mins', 0) > 0]
    avg_hold = f'{int(sum(hold_times)/len(hold_times))} min' if hold_times else 'N/A'

    # Portfolio allocation
    alloc = []
    total_pos_val = sum(abs(float(p.get('market_value', 0))) for p in positions)
    for p in positions:
        mv   = abs(float(p.get('market_value', 0)))
        pct  = (mv / portfolio_value * 100) if portfolio_value > 0 else 0
        upnl = float(p.get('unrealized_pl', 0))
        alloc.append({
            'symbol':       p['symbol'],
            'pct':          round(pct, 1),
            'market_value': round(mv, 2),
            'unrealized_pl': round(upnl, 2),
            'side':         p.get('side', 'long'),
        })
    # Cash allocation
    cash_pct = (cash / portfolio_value * 100) if portfolio_value > 0 else 0
    alloc.append({'symbol': 'Cash', 'pct': round(cash_pct, 1),
                  'market_value': round(cash, 2), 'unrealized_pl': 0, 'side': 'cash'})

    return {
        'portfolio_value':  round(portfolio_value, 2),
        'start_capital':    START_CAPITAL,
        'total_pnl':        round(total_pnl, 2),
        'pnl_pct':          round(pnl_pct, 2),
        'daily_pnl':        round(daily_pnl, 2),
        'daily_pct':        round(daily_pct, 2),
        'unrealised_pnl':   round(unrealised, 2),
        'cash':             round(cash, 2),
        'win_rate':         round(win_rate, 1),
        'wins':             len(wins),
        'losses':           len(losses),
        'total_trades':     len(trades),
        'closed_trades':    n,
        'sharpe':           sharpe,
        'max_drawdown':     round(max_dd, 2),
        'best_trade':       round(best, 2),
        'worst_trade':      round(worst, 2),
        'avg_hold':         avg_hold,
        'positions':        alloc,
        'open_positions':   len(positions),
    }

# ─── STRATEGY INFERENCE ───────────────────────────────────────────────────────
def infer_strategies(log_lines, trades):
    text = '\n'.join(log_lines[-300:])
    strategies = [
        {'name': 'Trend Following',  'detail': 'EMA-50 momentum breakout detection',    'status': 'STANDBY'},
        {'name': 'Mean Reversion',   'detail': 'RSI-14 + Bollinger Band oscillation',   'status': 'STANDBY'},
        {'name': 'Volatility Guard', 'detail': 'ATR-14 VIX-based risk hedging overlay', 'status': 'STANDBY'},
        {'name': 'Bear Market Guard','detail': 'BULL / CAUTION / BEAR / CRASH regimes', 'status': 'STANDBY'},
    ]

    if re.search(r'TREND', text):           strategies[0]['status'] = 'ACTIVE'
    if re.search(r'MEAN_REV', text):        strategies[1]['status'] = 'ACTIVE'
    if re.search(r'VOL_GUARD', text):       strategies[2]['status'] = 'HEDGING'
    if re.search(r'Bear Guard', text):      strategies[3]['status'] = 'ACTIVE'

    # Regime detection
    for regime, status in [('CRASH','HEDGING'),('BEAR','HEDGING'),
                            ('CAUTION','ACTIVE'),('BULL','ACTIVE')]:
        if re.search(regime, text):
            strategies[3]['status'] = status
            strategies[3]['detail'] = f'Current regime: {regime}'
            break

    if re.search(r'STOP LOSS|stop.loss', text, re.I): strategies[2]['status'] = 'HEDGING'

    return strategies

# ─── ROUTES ───────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('.', 'showcase.html')

@app.route('/live')
def live():
    return send_from_directory('.', 'dashboard.html')

@app.route('/api/summary')
def api_summary():
    account   = get_account()
    positions = get_positions()
    trades    = load_trades()
    stats     = compute_stats(account, trades, positions)
    return jsonify(stats)

@app.route('/api/trades')
def api_trades():
    trades = load_trades()

    # Enrich with Alpaca closed orders if no file trades
    if not trades:
        orders = get_orders(50)
        trades = []
        for o in orders:
            if o.get('filled_at'):
                trades.append({
                    'symbol':   o.get('symbol', ''),
                    'side':     o.get('side', ''),
                    'qty':      float(o.get('filled_qty', 0) or 0),
                    'price':    float(o.get('filled_avg_price', 0) or 0),
                    'pnl':      0,
                    'strategy': 'Alpaca Order',
                    'time':     (o.get('filled_at') or '')[:19].replace('T', ' '),
                    'hold_mins': 0,
                })

    trades.sort(key=lambda t: t.get('time', ''), reverse=True)
    return jsonify(trades[:100])

@app.route('/api/positions')
def api_positions():
    positions = get_positions()
    result = []
    for p in positions:
        result.append({
            'symbol':           p.get('symbol'),
            'qty':              float(p.get('qty', 0)),
            'side':             p.get('side', 'long'),
            'entry_price':      float(p.get('avg_entry_price', 0)),
            'current_price':    float(p.get('current_price', 0)),
            'market_value':     float(p.get('market_value', 0)),
            'unrealized_pl':    float(p.get('unrealized_pl', 0)),
            'unrealized_plpc':  float(p.get('unrealized_plpc', 0)) * 100,
        })
    return jsonify(result)

@app.route('/api/equity')
def api_equity():
    range_param = request.args.get('range', '1D')
    period_map  = {'1D': ('1D', '5Min'), '1W': ('1W', '1Hour'), 'ALL': ('3M', '1Day')}
    period, tf  = period_map.get(range_param, ('1D', '5Min'))

    hist = get_portfolio_history(period=period, timeframe=tf)

    if hist and hist.get('equity'):
        timestamps = hist.get('timestamp', [])
        equities   = hist.get('equity', [])
        labels, values = [], []
        for ts, eq in zip(timestamps, equities):
            if eq is None:
                continue
            dt = datetime.utcfromtimestamp(ts)
            if tf == '5Min':
                labels.append(dt.strftime('%H:%M'))
            elif tf == '1Hour':
                labels.append(dt.strftime('%m/%d %H:%M'))
            else:
                labels.append(dt.strftime('%m/%d'))
            values.append(round(eq, 2))

        # Daily P&L breakdown from history
        daily_pnls = {}
        base = get_portfolio_history('1M', '1Day')
        if base and base.get('equity') and base.get('timestamp'):
            eqs = base['equity']
            tss = base['timestamp']
            for i in range(1, len(eqs)):
                if eqs[i] and eqs[i-1]:
                    date = datetime.utcfromtimestamp(tss[i]).strftime('%m/%d')
                    daily_pnls[date] = round(eqs[i] - eqs[i-1], 2)

        return jsonify({
            'labels':       labels,
            'values':       values,
            'start_capital': START_CAPITAL,
            'daily_pnls':   daily_pnls,
        })

    # Fallback flat line
    now = datetime.utcnow()
    return jsonify({
        'labels': [now.strftime('%H:%M')],
        'values': [START_CAPITAL],
        'start_capital': START_CAPITAL,
        'daily_pnls': {},
    })

@app.route('/api/log')
def api_log():
    return jsonify(read_log(50))

@app.route('/api/strategies')
def api_strategies():
    log_lines = read_log(300)
    trades    = load_trades()
    return jsonify(infer_strategies(log_lines, trades))

@app.route('/api/performance')
def api_performance():
    """Monthly performance breakdown."""
    trades = load_trades()
    monthly = {}
    for t in trades:
        if t.get('side') == 'sell' and t.get('time'):
            month = t['time'][:7]
            monthly[month] = monthly.get(month, 0) + float(t.get('pnl', 0))
    return jsonify({
        'monthly': {k: round(v, 2) for k, v in sorted(monthly.items())},
        'total_pnl': round(sum(monthly.values()), 2),
    })

@app.route('/health')
def health():
    account = get_account()
    return jsonify({
        'status':    'ok',
        'time':      datetime.now(timezone.utc).isoformat(),
        'portfolio': float(account.get('portfolio_value', 0)),
        'cash':      float(account.get('cash', 0)),
    })

# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print('=' * 60)
    print('  QUANT-LAB Dashboard Server v2.0')
    print('  http://0.0.0.0:5000')
    print(f'  API Key: {API_KEY[:8]}...')
    print(f'  Start Capital: ${START_CAPITAL:,.0f}')
    print('=' * 60)
    app.run(host='0.0.0.0', port=5000, debug=False)
