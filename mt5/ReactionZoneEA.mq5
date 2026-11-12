//+------------------------------------------------------------------+
//| ReactionZoneEA.mq5                                                |
//| Port of strategies/reaction_zone.py (the saved candidate,         |
//| exec_mode m5, sweep+fvg zones, no stop loss) for forward testing  |
//| on an MT5 DEMO account. Same rules as the backtest:               |
//|  - 1H SuperTrend(10,3) gives direction                            |
//|  - 15m candle sweeps the 20-candle swing (or trades into an FVG)  |
//|    and closes back with a wick >= 30% of its range                |
//|  - next 5m MACD/SuperTrend confirmation within 60 min, entry at   |
//|    market if the 5m close is back beyond the level                |
//|  - entries only 07:00-10:00 and 12:30-16:00 UTC                   |
//|  - take profit only; the "daily account" ($20) is the stop:       |
//|    everything is closed when the day's loss reaches it            |
//|  - max 2 orders a day, stop after a win, one position at a time   |
//+------------------------------------------------------------------+
#property copyright   "lunch money research"
#property version     "1.10"
#property description "Reaction-zone strategy, port of the Python backtest. Demo accounts only by default."

#include <Trade/Trade.mqh>

#define DAY_MS  86400000
#define MIN_MS  60000

enum ENUM_SERVER_CLOCK
  {
   CLOCK_AUTO = 0,   // Auto: measure it live (tester: Exness = UTC, others = New York + 7)
   CLOCK_UTC  = 1,   // UTC (Exness)
   CLOCK_NY7  = 2    // New York + 7 = Athens time (MetaQuotes-Demo, most brokers)
  };

input group "Version"
input double InpTakeProfitUSD   = 10.0;   // Take profit, USD per oz (0.01 lot: $1 per $1 move)
input int    InpFlatHourUTC     = 20;     // Close everything at this UTC hour (24 = hold until TP or stop-out)
input group "Money"
input double InpLots            = 0.01;   // Lots
input double InpDailyAccount    = 20.0;   // Daily account: close all when the loss reaches this many USD
input int    InpMaxTrades       = 2;      // Max orders per UTC day
input bool   InpStopAfterWin    = true;   // Stop for the day after a winning order
input group "Entry"
input double InpMinWick         = 0.30;   // Min rejection wick, share of the 15m candle range
input int    InpSwingLookback   = 20;     // 15m candles for the swing high / low
input int    InpSetupValidMin   = 60;     // Minutes a 15m rejection stays valid
input int    InpMaxFvgAge       = 48;     // Max fair value gap age, candles
input double InpRsiOverbought   = 0.0;    // 15m RSI must be >= this to sell (0 = off)
input double InpRsiOversold     = 100.0;  // 15m RSI must be <= this to buy (100 = off)
input string InpSessions        = "07:00-10:00,12:30-16:00"; // Entry windows, UTC
input double InpMaxSpread       = 0.60;   // Skip entries when the spread is wider, USD
input group "Technical"
input ENUM_SERVER_CLOCK InpServerClock = CLOCK_AUTO; // Broker server clock (entry windows are UTC)
input long   InpMagic           = 26111101;
input int    InpWarmupBars      = 600;    // Closed bars per timeframe used to warm up indicators
input bool   InpWriteLog        = true;   // Log signals and trades to MQL5/Files/ReactionZoneEA_<login>.csv
input bool   InpAllowReal       = false;  // Allow running on a real account (keep false for testing)

//+------------------------------------------------------------------+
//| Indicators: same formulas as xautest/indicators.py                |
//+------------------------------------------------------------------+
class CEma
  {
public:
   int               period;
   double            k, value;
   int               count;
   void              Init(int p) { period = p; k = 2.0 / (p + 1); value = 0; count = 0; }
   double            Update(double x) { if(count == 0) value = x; else value += k * (x - value); count++; return value; }
   bool              Ready() { return count >= period; }
  };

class CMacd
  {
public:
   CEma              fast, slow, sig;
   double            hist, prev_hist;
   void              Init() { fast.Init(12); slow.Init(26); sig.Init(9); hist = 0; prev_hist = 0; }
   double            Update(double x)
     {
      double m = fast.Update(x) - slow.Update(x);
      double s = sig.Update(m);
      prev_hist = hist;
      hist = m - s;
      return hist;
     }
   bool              Ready() { return slow.count >= slow.period + sig.period - 1; }
  };

class CAtr
  {
public:
   int               period, count;
   double            value, prev_close;
   void              Init(int p) { period = p; count = 0; value = 0; prev_close = 0; }
   double            Update(double h, double l, double c)
     {
      double tr = (count == 0) ? h - l : MathMax(h - l, MathMax(MathAbs(h - prev_close), MathAbs(l - prev_close)));
      prev_close = c;
      if(count == 0) value = tr; else value += (tr - value) / period;
      count++;
      return value;
     }
   bool              Ready() { return count >= period; }
  };

class CSuperTrend
  {
public:
   CAtr              atr;
   double            mult, upper, lower, prev_close;
   int               direction;
   bool              started;
   void              Init(int p, double m) { atr.Init(p); mult = m; upper = 0; lower = 0; prev_close = 0; direction = 1; started = false; }
   void              Update(double h, double l, double c)
     {
      double a = atr.Update(h, l, c);
      double hl2 = (h + l) / 2.0, bu = hl2 + mult * a, bl = hl2 - mult * a;
      if(!started) { upper = bu; lower = bl; started = true; }
      else
        {
         if(bu < upper || prev_close > upper) upper = bu;
         if(bl > lower || prev_close < lower) lower = bl;
        }
      if(direction == 1 && c < lower) direction = -1;
      else if(direction == -1 && c > upper) direction = 1;
      prev_close = c;
     }
   bool              Ready() { return atr.Ready(); }
  };

class CRsi
  {
public:
   int               period, count;
   double            prev, avg_gain, avg_loss, value;
   bool              has_prev, has_value;
   void              Init(int p) { period = p; count = 0; prev = 0; avg_gain = 0; avg_loss = 0; value = 50; has_prev = false; has_value = false; }
   void              Update(double x)
     {
      if(!has_prev) { prev = x; has_prev = true; return; }
      double ch = x - prev;
      prev = x;
      double g = MathMax(ch, 0.0), lo = MathMax(-ch, 0.0);
      count++;
      int n = period;
      if(count <= n)
        {
         avg_gain += g / n;
         avg_loss += lo / n;
         if(count < n) return;
        }
      else
        {
         avg_gain = (avg_gain * (n - 1) + g) / n;
         avg_loss = (avg_loss * (n - 1) + lo) / n;
        }
      if(avg_loss == 0) value = (avg_gain > 0) ? 100.0 : 50.0;
      else value = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss);
      has_value = true;
     }
  };

struct Zone { int side; double lo; double hi; int age; };

class CFvg
  {
public:
   Zone              zones[];
   double            ph[2], pl[2];
   int               nprev, max_age;
   void              Init(int a) { ArrayResize(zones, 0); nprev = 0; max_age = a; }
   void              Add(int s, double lo, double hi)
     {
      int n = ArraySize(zones);
      ArrayResize(zones, n + 1);
      zones[n].side = s; zones[n].lo = lo; zones[n].hi = hi; zones[n].age = 0;
     }
   void              Update(double h, double l)
     {
      int k = 0, n = ArraySize(zones);
      for(int i = 0; i < n; i++)
        {
         zones[i].age++;
         bool filled = (zones[i].side == 1) ? (l <= zones[i].lo) : (h >= zones[i].hi);
         if(!filled && zones[i].age <= max_age) { zones[k] = zones[i]; k++; }
        }
      ArrayResize(zones, k);
      if(nprev == 2)
        {
         if(ph[0] < l) Add(1, ph[0], l);           // bullish gap: candle 1 high < candle 3 low
         else if(pl[0] > h) Add(-1, h, pl[0]);     // bearish gap: candle 1 low > candle 3 high
        }
      if(nprev < 2) { ph[nprev] = h; pl[nprev] = l; nprev++; }
      else { ph[0] = ph[1]; pl[0] = pl[1]; ph[1] = h; pl[1] = l; }
     }
  };

//+------------------------------------------------------------------+
//| State                                                            |
//+------------------------------------------------------------------+
CTrade       trade;
CSuperTrend  h1St, m5St;
CMacd        m5Macd;
CFvg         fvgH1, fvg15;
CRsi         rsi15;
double       histH[], histL[];
int          histN = 0;
datetime     lastH1 = 0, lastM15 = 0, lastM5 = 0;
bool         warmed = false;
long         gLastClockMin = -1;
long         sessA[], sessB[];
string       logName;

struct Setup
  {
   bool   active;
   int    side;          // +1 buy, -1 sell
   double level, wick;
   long   ready_ms, expires_ms;
   bool   confirmed;
   string how;
  };
Setup setup;

// daily account (the stop): like SimBroker daily_account
long   acctDay = -1;
bool   acctDead = false;
double eaClosed = 0.0, floorLevel = 0.0;
// daily rules: like DailyRiskRules (fixed lots)
long   ruleDay = -1, posDay = -1;
int    dayTrades = 0, winStreak = 0;
double dayPnl = 0.0;
bool   dayStopped = false;
string stopReason = "";
ulong  posId = 0;
// Execution quality (slippage and delay), written to the log as "fill" rows for experiments/demo_slippage.py
int    fillSide = 0;                      // side of the EA's open position
double wantEntry = 0, wantExit = 0, wantTp = 0, entrySpread = 0;
ulong  entryT0 = 0, exitT0 = 0;           // microsecond clock when the order / close was sent
double entryMs = 0;                       // time OrderSend took to return

//+------------------------------------------------------------------+
long gOffsetMs = 0;          // server time minus UTC
string gClockNote = "";

int NyPlus7Hours(datetime server_time)
  {
   // US daylight time: second Sunday of March 02:00 New York to first Sunday of November 02:00 New York
   MqlDateTime d;
   TimeToStruct(server_time, d);
   MqlDateTime m;
   TimeToStruct(StringToTime(StringFormat("%d.03.01", d.year)), m);
   int mar = 1 + (7 - m.day_of_week) % 7 + 7;
   TimeToStruct(StringToTime(StringFormat("%d.11.01", d.year)), m);
   int nov = 1 + (7 - m.day_of_week) % 7;
   datetime start = StringToTime(StringFormat("%d.03.%02d 09:00", d.year, mar));   // 02:00 NY in NY+7 clock
   datetime end = StringToTime(StringFormat("%d.11.%02d 09:00", d.year, nov));
   return (server_time >= start && server_time < end) ? 3 : 2;
  }

void UpdateClock()
  {
   string note;
   long off;
   bool tester = (bool)MQLInfoInteger(MQL_TESTER);
   if(InpServerClock == CLOCK_UTC) { off = 0; note = "UTC"; }
   else if(InpServerClock == CLOCK_NY7) { off = NyPlus7Hours(TimeCurrent()) * 3600; note = "New York + 7"; }
   else if(tester)
     {
      bool exness = StringFind(AccountInfoString(ACCOUNT_SERVER), "Exness") >= 0;
      off = exness ? 0 : NyPlus7Hours(TimeCurrent()) * 3600;
      note = exness ? "UTC (tester, Exness)" : "New York + 7 (tester)";
     }
   else
     {
      long raw = (long)(TimeTradeServer() - TimeGMT());
      off = (long)MathRound(raw / 1800.0) * 1800;
      note = "measured";
     }
   if(off * 1000 != gOffsetMs || gClockNote == "")
     {
      gOffsetMs = off * 1000;
      gClockNote = note;
      Log("server clock", StringFormat("%s: UTC%+.1f", note, off / 3600.0));
     }
  }

long ToUtcMs(datetime server_time) { return (long)server_time * 1000 - gOffsetMs; }

void Log(string ev, string detail = "")
  {
   PrintFormat("%s %s", ev, detail);
   if(!InpWriteLog) return;
   int h = FileOpen(logName, FILE_READ | FILE_WRITE | FILE_CSV | FILE_ANSI | FILE_SHARE_READ, ',');
   if(h == INVALID_HANDLE) return;
   if(FileSize(h) == 0) FileWrite(h, "server_time", "event", "detail");
   FileSeek(h, 0, SEEK_END);
   MqlTick t;
   SymbolInfoTick(_Symbol, t);
   FileWrite(h, TimeToString(t.time, TIME_DATE | TIME_SECONDS), ev, detail);
   FileClose(h);
  }

bool ParseSessions()
  {
   string parts[];
   int n = StringSplit(InpSessions, ',', parts);
   ArrayResize(sessA, 0); ArrayResize(sessB, 0);
   for(int i = 0; i < n; i++)
     {
      string p = parts[i];
      StringTrimLeft(p); StringTrimRight(p);
      if(p == "") continue;
      string ab[], a[], b[];
      if(StringSplit(p, '-', ab) != 2 || StringSplit(ab[0], ':', a) != 2 || StringSplit(ab[1], ':', b) != 2) return false;
      int k = ArraySize(sessA);
      ArrayResize(sessA, k + 1); ArrayResize(sessB, k + 1);
      sessA[k] = ((long)StringToInteger(a[0]) * 60 + StringToInteger(a[1])) * MIN_MS;
      sessB[k] = ((long)StringToInteger(b[0]) * 60 + StringToInteger(b[1])) * MIN_MS;
     }
   return ArraySize(sessA) > 0;
  }

bool InSession(long ts)
  {
   long tod = ts % DAY_MS;
   for(int i = 0; i < ArraySize(sessA); i++) if(tod >= sessA[i] && tod < sessB[i]) return true;
   return false;
  }

int CountPositions()
  {
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == InpMagic) n++;
     }
   return n;
  }

double Floating()
  {
   double f = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == InpMagic)
         f += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
     }
   return f;
  }

void CloseAll(string why)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == InpMagic)
        {
         MqlTick q;
         SymbolInfoTick(_Symbol, q);
         wantExit = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? q.bid : q.ask;
         exitT0 = GetMicrosecondCount();
         if(trade.PositionClose(tk)) Log("close request", why);
         else Log("close failed", StringFormat("%s retcode %d", why, trade.ResultRetcode()));
        }
     }
  }

void RulesFor(long day)
  {
   if(day == ruleDay) return;
   ruleDay = day; dayTrades = 0; winStreak = 0; dayPnl = 0; dayStopped = false; stopReason = "";
  }

void PushHist(double h, double l)
  {
   if(histN < InpSwingLookback) { histH[histN] = h; histL[histN] = l; histN++; return; }
   for(int i = 1; i < histN; i++) { histH[i - 1] = histH[i]; histL[i - 1] = histL[i]; }
   histH[histN - 1] = h; histL[histN - 1] = l;
  }

int Bias() { return h1St.Ready() ? h1St.direction : 0; }

bool ClosedBar(ENUM_TIMEFRAMES tf, datetime &last, MqlRates &out)
  {
   datetime cur = iTime(_Symbol, tf, 0);
   if(cur == 0 || cur == last) return false;
   last = cur;
   MqlRates rr[];
   if(CopyRates(_Symbol, tf, 1, 1, rr) != 1) return false;
   out = rr[0];
   return true;
  }

//+------------------------------------------------------------------+
//| Warm-up from history (indicators only, no signals)                |
//+------------------------------------------------------------------+
bool Warmup()
  {
   MqlRates h1[], m15[], m5[];
   int a = CopyRates(_Symbol, PERIOD_H1, 1, InpWarmupBars, h1);
   int b = CopyRates(_Symbol, PERIOD_M15, 1, InpWarmupBars, m15);
   int c = CopyRates(_Symbol, PERIOD_M5, 1, InpWarmupBars, m5);
   if(a < 100 || b < 100 || c < 100) return false;
   h1St.Init(10, 3.0); m5St.Init(10, 3.0); m5Macd.Init(); rsi15.Init(14);
   fvgH1.Init(InpMaxFvgAge); fvg15.Init(InpMaxFvgAge);
   histN = 0;
   for(int i = 0; i < a; i++) { h1St.Update(h1[i].high, h1[i].low, h1[i].close); fvgH1.Update(h1[i].high, h1[i].low); }
   for(int i = 0; i < b; i++) { rsi15.Update(m15[i].close); PushHist(m15[i].high, m15[i].low); fvg15.Update(m15[i].high, m15[i].low); }
   for(int i = 0; i < c; i++) { m5St.Update(m5[i].high, m5[i].low, m5[i].close); m5Macd.Update(m5[i].close); }
   lastH1 = iTime(_Symbol, PERIOD_H1, 0);
   lastM15 = iTime(_Symbol, PERIOD_M15, 0);
   lastM5 = iTime(_Symbol, PERIOD_M5, 0);
   Log("warmed up", StringFormat("H1 %d, M15 %d, M5 %d bars; 1H trend %d; FVGs H1 %d / 15m %d",
       a, b, c, Bias(), ArraySize(fvgH1.zones), ArraySize(fvg15.zones)));
   return true;
  }

//+------------------------------------------------------------------+
//| 15m: rejection at a reaction zone                                |
//+------------------------------------------------------------------+
bool FindZone(CFvg &f, int side, const MqlRates &r, double &level)
  {
   for(int i = 0; i < ArraySize(f.zones); i++)
     {
      if(side == -1 && f.zones[i].side == -1 && r.high >= f.zones[i].lo && r.close < f.zones[i].lo) { level = f.zones[i].lo; return true; }
      if(side == 1 && f.zones[i].side == 1 && r.low <= f.zones[i].hi && r.close > f.zones[i].hi) { level = f.zones[i].hi; return true; }
     }
   return false;
  }

void OnM15(const MqlRates &r)
  {
   rsi15.Update(r.close);
   if(rsi15.has_value && histN == InpSwingLookback && CountPositions() == 0)
     {
      int b = Bias();
      double rng = r.high - r.low;
      if(b != 0 && rng > 0)
        {
         double level = 0;
         string how = "";
         if(b == -1)
           {
            double swing = histH[0];
            for(int i = 1; i < histN; i++) swing = MathMax(swing, histH[i]);
            bool wick_ok = (r.high - MathMax(r.open, r.close)) / rng >= InpMinWick;
            if(r.high > swing && r.close < swing) { level = swing; how = "sweep"; }
            else if(FindZone(fvgH1, -1, r, level) || FindZone(fvg15, -1, r, level)) how = "fvg";
            if(how != "" && wick_ok && rsi15.value >= InpRsiOverbought) NewSetup(-1, level, r.high, r.time, how);
           }
         else
           {
            double swing = histL[0];
            for(int i = 1; i < histN; i++) swing = MathMin(swing, histL[i]);
            bool wick_ok = (MathMin(r.open, r.close) - r.low) / rng >= InpMinWick;
            if(r.low < swing && r.close > swing) { level = swing; how = "sweep"; }
            else if(FindZone(fvgH1, 1, r, level) || FindZone(fvg15, 1, r, level)) how = "fvg";
            if(how != "" && wick_ok && rsi15.value <= InpRsiOversold) NewSetup(1, level, r.low, r.time, how);
           }
        }
     }
   PushHist(r.high, r.low);
   fvg15.Update(r.high, r.low);
  }

void NewSetup(int side, double level, double wick, datetime bar_time, string how)
  {
   setup.active = true; setup.side = side; setup.level = level; setup.wick = wick; setup.how = how;
   setup.ready_ms = ToUtcMs(bar_time) + 15 * MIN_MS;
   setup.expires_ms = setup.ready_ms + (long)InpSetupValidMin * MIN_MS;
   setup.confirmed = false;
   Log("rejection", StringFormat("%s via %s, level %.2f, wick %.2f", side > 0 ? "BUY" : "SELL", how, level, wick));
  }

//+------------------------------------------------------------------+
//| 5m: momentum confirmation, then entry                            |
//+------------------------------------------------------------------+
void OnM5(const MqlRates &r, const MqlTick &t, long ts)
  {
   m5St.Update(r.high, r.low, r.close);
   double h = m5Macd.Update(r.close);
   if(!setup.active || setup.confirmed || ToUtcMs(r.time) < setup.ready_ms) return;
   if(!(m5St.Ready() && m5Macd.Ready())) return;
   double prev = m5Macd.prev_hist;
   bool ok = (setup.side < 0) ? (h < 0 && (prev >= 0 || h < prev) && m5St.direction == -1)
             : (h > 0 && (prev <= 0 || h > prev) && m5St.direction == 1);
   if(!ok) return;
   setup.confirmed = true;
   bool inside = (setup.side < 0) ? r.close < setup.level : r.close > setup.level;
   Log("confirmed", StringFormat("5m close %.2f, back %s level", r.close, inside ? "beyond" : "NOT beyond"));
   if(inside) Execute(t, ts);
  }

void Execute(const MqlTick &t, long ts)
  {
   if(CountPositions() > 0) return;
   if(!InSession(ts)) { Log("skipped", "outside entry windows"); return; }
   double spread = t.ask - t.bid;
   if(spread > InpMaxSpread) { Log("skipped", StringFormat("spread %.2f", spread)); return; }
   long day = ts / DAY_MS;
   RulesFor(day);
   if(dayStopped || acctDead) { Log("skipped", "day finished: " + (acctDead ? "account stopped out" : stopReason)); return; }
   dayTrades++;
   int side = setup.side;
   double tp = NormalizeDouble(side < 0 ? t.bid - InpTakeProfitUSD : t.ask + InpTakeProfitUSD, _Digits);
   fillSide = side; wantEntry = side < 0 ? t.bid : t.ask; wantTp = tp; wantExit = 0; entrySpread = spread;
   entryT0 = GetMicrosecondCount();
   bool sent = (side < 0) ? trade.Sell(InpLots, _Symbol, 0, 0, tp, "reaction zone")
               : trade.Buy(InpLots, _Symbol, 0, 0, tp, "reaction zone");
   entryMs = (GetMicrosecondCount() - entryT0) / 1000.0;
   setup.active = false;
   uint rc = trade.ResultRetcode();
   if(!sent || (rc != TRADE_RETCODE_DONE && rc != TRADE_RETCODE_PLACED))
     {
      dayTrades--;
      Log("order failed", StringFormat("retcode %d %s", rc, trade.ResultRetcodeDescription()));
      return;
     }
   posDay = day;
   Log("entry", StringFormat("%s %.2f lots at %.2f, TP %.2f, spread %.2f", side > 0 ? "BUY" : "SELL", InpLots,
       trade.ResultPrice(), tp, spread));
  }

//+------------------------------------------------------------------+
//| Closed trades: book to the entry day, like DailyRiskRules.record  |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &tr, const MqlTradeRequest &req, const MqlTradeResult &res)
  {
   if(tr.type != TRADE_TRANSACTION_DEAL_ADD || !HistoryDealSelect(tr.deal)) return;
   if(HistoryDealGetString(tr.deal, DEAL_SYMBOL) != _Symbol) return;
   long magic = HistoryDealGetInteger(tr.deal, DEAL_MAGIC);
   ulong pid = (ulong)HistoryDealGetInteger(tr.deal, DEAL_POSITION_ID);
   long entry = HistoryDealGetInteger(tr.deal, DEAL_ENTRY);
   if(entry == DEAL_ENTRY_IN && magic == InpMagic)
     {
      posId = pid;
      double got = HistoryDealGetDouble(tr.deal, DEAL_PRICE);
      // slip > 0 = filled worse than the price on screen when the order was sent
      Log("fill", StringFormat("entry %s wanted %.3f got %.3f slip %.3f send_ms %.0f spread %.3f",
          fillSide > 0 ? "BUY" : "SELL", wantEntry, got, fillSide * (got - wantEntry), entryMs, entrySpread));
      return;
     }
   if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_OUT_BY) return;
   if(magic != InpMagic && !(posId != 0 && pid == posId)) return;
   double net = HistoryDealGetDouble(tr.deal, DEAL_PROFIT) + HistoryDealGetDouble(tr.deal, DEAL_SWAP)
                + HistoryDealGetDouble(tr.deal, DEAL_COMMISSION) + HistoryDealGetDouble(tr.deal, DEAL_FEE);
   eaClosed += net;
   long reason = HistoryDealGetInteger(tr.deal, DEAL_REASON);
   string why = reason == DEAL_REASON_TP ? "take profit" : reason == DEAL_REASON_SL ? "stop loss"
                : reason == DEAL_REASON_SO ? "broker stop-out" : "closed by EA";
   Log("exit", StringFormat("%s at %.2f, net %.2f", why, HistoryDealGetDouble(tr.deal, DEAL_PRICE), net));
   if(fillSide != 0)
     {
      double got = HistoryDealGetDouble(tr.deal, DEAL_PRICE);
      double want = reason == DEAL_REASON_TP ? wantTp : wantExit;
      string kind = reason == DEAL_REASON_TP ? "tp" : reason == DEAL_REASON_SO ? "broker_stopout" : "close";
      if(want > 0)
         Log("fill", StringFormat("exit %s wanted %.3f got %.3f slip %.3f send_ms %.0f spread %.3f", kind, want, got,
             fillSide * (want - got), reason == DEAL_REASON_TP ? 0.0 : (GetMicrosecondCount() - exitT0) / 1000.0,
             SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID)));
     }
   if(posDay >= ruleDay && posDay >= 0)
     {
      RulesFor(posDay);
      dayPnl += net;
      winStreak = (net > 0) ? winStreak + 1 : 0;
      if(!dayStopped)
        {
         if(InpStopAfterWin && winStreak >= 1) { dayStopped = true; stopReason = "win"; }
         else if(dayPnl <= -InpDailyAccount + 1e-9) { dayStopped = true; stopReason = "loss limit"; }
         else if(InpMaxTrades > 0 && dayTrades >= InpMaxTrades) { dayStopped = true; stopReason = "max trades"; }
         if(dayStopped) Log("day finished", stopReason);
        }
     }
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   if(!InpAllowReal && AccountInfoInteger(ACCOUNT_TRADE_MODE) != ACCOUNT_TRADE_MODE_DEMO)
     {
      Alert("ReactionZoneEA: this is not a demo account. Set InpAllowReal=true only if you really mean it.");
      return INIT_FAILED;
     }
   if(!ParseSessions()) { Alert("ReactionZoneEA: cannot read InpSessions"); return INIT_PARAMETERS_INCORRECT; }
   double cs = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
   if(MathAbs(cs - 100.0) > 1e-6)
      Alert(StringFormat("ReactionZoneEA: %s contract size is %.0f, not 100 oz. Dollar amounts will not match the backtest "
                         "(cent account?).", _Symbol, cs));
   ArrayResize(histH, InpSwingLookback); ArrayResize(histL, InpSwingLookback);
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(100);
   trade.SetTypeFillingBySymbol(_Symbol);
   logName = "ReactionZoneEA_" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) + ".csv";
   setup.active = false;
   Log("start", StringFormat("%s on %s: TP %.2f, flat hour %d UTC, lots %.2f, daily account %.2f, sessions %s UTC",
       _Symbol, AccountInfoString(ACCOUNT_SERVER), InpTakeProfitUSD, InpFlatHourUTC, InpLots, InpDailyAccount, InpSessions));
   UpdateClock();
   if(CountPositions() > 0) Log("warning", "an open position with this magic number already exists; it is managed as today's");
   warmed = Warmup();
   EventSetTimer(1);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason) { EventKillTimer(); Comment(""); }

void OnTimer() { if(!warmed) warmed = Warmup(); }

void OnTick()
  {
   if(!warmed) { warmed = Warmup(); if(!warmed) return; }
   MqlTick t;
   if(!SymbolInfoTick(_Symbol, t)) return;
   if(t.time_msc / 60000 != gLastClockMin) { gLastClockMin = t.time_msc / 60000; UpdateClock(); }
   long ts = t.time_msc - gOffsetMs;
   long day = ts / DAY_MS;
   int open = CountPositions();

   // daily account: refill only when flat; close everything when it is used up
   if(day != acctDay && open == 0) { acctDay = day; acctDead = false; floorLevel = eaClosed - InpDailyAccount; }
   if(open > 0 && eaClosed + Floating() <= floorLevel)
     {
      Log("account stop-out", StringFormat("loss reached %.2f", InpDailyAccount));
      CloseAll("account stop-out");
      acctDead = true;
     }

   MqlRates r;
   if(ClosedBar(PERIOD_H1, lastH1, r)) { h1St.Update(r.high, r.low, r.close); fvgH1.Update(r.high, r.low); }
   if(ClosedBar(PERIOD_M15, lastM15, r)) OnM15(r);
   if(ClosedBar(PERIOD_M5, lastM5, r)) OnM5(r, t, ts);

   if(setup.active)
     {
      if(ts >= setup.expires_ms) { setup.active = false; Log("setup expired"); }
      else if((setup.side < 0 && t.bid > setup.wick) || (setup.side > 0 && t.bid < setup.wick))
        { setup.active = false; Log("setup invalidated", "price went past the wick"); }
     }

   if(CountPositions() > 0 && (ts % DAY_MS) >= (long)InpFlatHourUTC * 3600000) CloseAll("end of day close");

   RulesFor(day);
   Comment(StringFormat("ReactionZoneEA  TP $%.2f  %s  server UTC%+.1f\n1H trend: %s\nToday: %d orders, P&L %.2f%s\nDaily account left: %.2f%s\nSetup: %s",
           InpTakeProfitUSD, InpFlatHourUTC >= 24 ? "held" : StringFormat("closed %02d:00 UTC", InpFlatHourUTC), gOffsetMs / 3600000.0,
           Bias() > 0 ? "up" : Bias() < 0 ? "down" : "none", dayTrades, dayPnl,
           dayStopped ? " (done: " + stopReason + ")" : "",
           eaClosed + Floating() - floorLevel, acctDead ? " (stopped out)" : "",
           !setup.active ? "none" : StringFormat("%s at %.2f%s", setup.side > 0 ? "BUY" : "SELL", setup.level,
                                                 setup.confirmed ? ", confirmed" : ", waiting for 5m")));
  }
//+------------------------------------------------------------------+
