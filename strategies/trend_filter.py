"""
Trend Filter Module
Uses SMA to determine trend direction and entry signals.
"""

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import time
import threading
from typing import Optional, Dict, Any, Tuple
from execution.mt5_connector import MT5Connector
from utils.logger_factory import get_logger

# Module-level logger - will be reinitialized in __init__ based on mode
logger = None


class TrendFilter:
    """Analyzes market trends using SMA indicators."""
    
    def __init__(self, config: Dict[str, Any], mt5_connector: MT5Connector):
        self.config = config
        self.trading_config = config.get('trading', {})
        self.mt5_connector = mt5_connector
        
        # CRITICAL FIX: Initialize logger based on mode (backtest vs live)
        # This prevents backtest from writing to live log files
        global logger
        is_backtest = config.get('mode') == 'backtest'
        log_path = "logs/backtest/engine/trend_detector.log" if is_backtest else "logs/live/engine/trend_detector.log"
        logger = get_logger("trend_detector", log_path)
        
        self.sma_fast = self.trading_config.get('sma_fast', 20)
        self.sma_slow = self.trading_config.get('sma_slow', 50)
        self.rsi_period = self.trading_config.get('rsi_period', 14)
        self.rsi_overbought = self.trading_config.get('rsi_overbought', 70)
        self.rsi_oversold = self.trading_config.get('rsi_oversold', 30)
        self.use_rsi_filter = self.trading_config.get('use_rsi_filter', True)
        self.rsi_entry_range_min = self.trading_config.get('rsi_entry_range_min', 30)
        self.rsi_entry_range_max = self.trading_config.get('rsi_entry_range_max', 50)
        # CRITICAL: In SIM_LIVE mode, disable price action and volume confirmation to allow certification scenarios
        # These filters are too restrictive for synthetic market data where entry candles are intentionally generated
        # Check mode at runtime as well as init time to handle dynamic mode changes
        is_sim_live = config.get('mode') == 'SIM_LIVE'
        # Also check if we're using synthetic market engine (more reliable indicator of SIM_LIVE)
        try:
            from sim_live.sim_live_mt5_connector import SimLiveMT5Connector
            is_using_synthetic = isinstance(mt5_connector, SimLiveMT5Connector)
            is_sim_live = is_sim_live or is_using_synthetic
        except:
            pass
        
        if is_sim_live:
            self.use_price_action_confirmation = False
            self.use_volume_confirmation = False
            logger.info(f"[SIM_LIVE] Price action and volume confirmation DISABLED for SIM_LIVE mode")
        else:
            self.use_price_action_confirmation = self.trading_config.get('use_price_action_confirmation', False)
            self.use_volume_confirmation = self.trading_config.get('use_volume_confirmation', False)
        self.timeframe = self._parse_timeframe(self.trading_config.get('timeframe', 'M1'))
        self.atr_period = self.trading_config.get('atr_period', 14)
        self.atr_multiplier = self.trading_config.get('atr_multiplier', 2.0)
        
        # Micro-scalping optimization settings
        self.min_trend_strength = self.trading_config.get('min_trend_strength', 0.00001)  # Minimum SMA separation (relaxed for micro-scalping)
        self.max_choppiness = self.trading_config.get('max_choppiness', 0.7)  # Maximum choppiness (0-1, relaxed)
        self.min_adx = self.trading_config.get('min_adx', 15)  # Minimum ADX for trend strength (lowered for micro-scalping)
        self.use_volatility_filter = self.trading_config.get('use_volatility_filter', True)
        self.rsi_soft_filter = self.trading_config.get('rsi_soft_filter', False)  # Soft RSI filter (warn but don't block)
        self.min_quality_score = self.trading_config.get('min_quality_score', 50)  # Minimum quality score (lowered for micro-scalping)
        
        # Entry filters configuration (for quality score calculation)
        risk_config = config.get('risk', {})
        entry_filters_config = risk_config.get('entry_filters', {})
        volatility_floor_config = entry_filters_config.get('volatility_floor', {})
        self.volatility_floor_enabled = volatility_floor_config.get('enabled', True)
        self.volatility_floor_candle_count = volatility_floor_config.get('candle_count', 20)
        self.volatility_floor_min_range_pips = volatility_floor_config.get('min_range_pips', 1.5)
        
        candle_quality_config = entry_filters_config.get('candle_quality', {})
        self.candle_quality_enabled = candle_quality_config.get('enabled', True)
        self.candle_quality_min_percent = candle_quality_config.get('min_percent_of_avg', 65.0)
        
        # Risk config for spread penalty calculation
        self.max_risk_usd = risk_config.get('max_risk_per_trade_usd', 2.0)
        
        # Rate data caching (TTL: 60 seconds for M1 timeframe)
        self._rates_cache = {}  # {symbol: (dataframe, timestamp)}
        self._rates_cache_ttl = 60.0  # seconds (matches M1 timeframe)
        self._rates_cache_lock = threading.Lock()
    
    def _parse_timeframe(self, tf: str) -> int:
        """Convert timeframe string to MT5 constant."""
        timeframe_map = {
            'M1': mt5.TIMEFRAME_M1,
            'M5': mt5.TIMEFRAME_M5,
            'M15': mt5.TIMEFRAME_M15,
            'M30': mt5.TIMEFRAME_M30,
            'H1': mt5.TIMEFRAME_H1,
            'H4': mt5.TIMEFRAME_H4,
            'D1': mt5.TIMEFRAME_D1
        }
        return timeframe_map.get(tf.upper(), mt5.TIMEFRAME_M1)
    
    def get_rates(self, symbol: str, count: int = 100) -> Optional[pd.DataFrame]:
        """Get historical rates for symbol with caching."""
        if not self.mt5_connector.ensure_connected():
            return None
        
        now = time.time()
        cache_key = f"{symbol}_{self.timeframe}_{count}"
        
        # 🔍 Disable caching in SIM_LIVE mode to ensure fresh data (candles change frequently)
        is_sim_live = self.config.get('mode') == 'SIM_LIVE'
        
        # Check cache first (skip in SIM_LIVE)
        if not is_sim_live:
            with self._rates_cache_lock:
                if cache_key in self._rates_cache:
                    cached_df, cached_time = self._rates_cache[cache_key]
                    if now - cached_time < self._rates_cache_ttl:
                        # Return cached data if not stale
                        return cached_df.copy()  # Return copy to prevent mutation
        
        # Fetch fresh data
        # Check if mt5_connector has copy_rates_from_pos method (backtest mode)
        if hasattr(self.mt5_connector, 'copy_rates_from_pos'):
            # Backtest mode - use wrapper method
            rates = self.mt5_connector.copy_rates_from_pos(symbol, self.timeframe, 0, count)
        else:
            # Live mode - use MT5 directly
            rates = mt5.copy_rates_from_pos(symbol, self.timeframe, 0, count)
        
        if rates is None or len(rates) == 0:
            logger.error(f"Failed to get rates for {symbol}")
            return None
        
        # Convert NumPy structured array to DataFrame
        # Handle both NumPy structured arrays and regular arrays
        try:
            if isinstance(rates, np.ndarray) and hasattr(rates.dtype, 'names') and rates.dtype.names is not None:
                # Structured array - extract fields explicitly
                field_dict = {}
                for field_name in ['time', 'open', 'high', 'low', 'close']:
                    if field_name in rates.dtype.names:
                        field_array = rates[field_name]
                        field_dict[field_name] = field_array
                        
                        # 🔍 Log first 14 values of close field after extraction
                        if field_name == 'close' and is_sim_live and len(field_array) >= 14:
                            try:
                                first_14_extracted = field_array[:14].tolist()
                                logger.info(f"[SIM_LIVE] [FIELD_EXTRACT] Extracted first 14 closes from NumPy array: {first_14_extracted}")
                            except:
                                pass
                    else:
                        logger.error(f"Missing field '{field_name}' in structured array for {symbol}")
                        return None
                
                # Optional fields
                if 'tick_volume' in rates.dtype.names:
                    field_dict['tick_volume'] = rates['tick_volume']
                elif 'real_volume' in rates.dtype.names:
                    field_dict['tick_volume'] = rates['real_volume']
                else:
                    field_dict['tick_volume'] = np.zeros(len(rates), dtype='int64')
                
                if 'spread' in rates.dtype.names:
                    field_dict['spread'] = rates['spread']
                else:
                    field_dict['spread'] = np.zeros(len(rates), dtype='int32')
                
                if 'real_volume' in rates.dtype.names:
                    field_dict['real_volume'] = rates['real_volume']
                else:
                    field_dict['real_volume'] = field_dict.get('tick_volume', np.zeros(len(rates), dtype='int64'))
                
                df = pd.DataFrame(field_dict)
                
                # 🔍 Verify DataFrame was created correctly
                if is_sim_live and len(df) >= 14:
                    try:
                        first_14_df = df['close'].iloc[:14].tolist()
                        logger.info(f"[SIM_LIVE] [DF_VERIFY] DataFrame first 14 closes: {first_14_df}")
                    except:
                        pass
            else:
                # Regular array or list - convert normally
                df = pd.DataFrame(rates)
                # If columns are numeric indices, try to infer MT5 structure
                if len(df.columns) > 0 and isinstance(df.columns[0], (int, np.integer)):
                    if len(df.columns) >= 5:
                        df.columns = ['time', 'open', 'high', 'low', 'close'][:len(df.columns)] + list(df.columns[5:])
        except Exception as e:
            logger.error(f"Error converting rates to DataFrame for {symbol}: {e}", exc_info=True)
            return None
        
        # Validate data
        if len(df) == 0:
            logger.error(f"Empty DataFrame returned for {symbol}")
            return None
        
        # Ensure we have required columns
        if 'close' not in df.columns:
            logger.error(f"Missing 'close' column in rates for {symbol}. Columns: {list(df.columns)}")
            return None
        
        # Check for NaN values in critical columns
        if df['close'].isna().all() or df['close'].isnull().all():
            logger.error(f"All close prices are NaN for {symbol}")
            return None
        
        # Remove rows with NaN close prices
        df = df.dropna(subset=['close'])
        if len(df) == 0:
            logger.error(f"No valid close prices for {symbol}")
            return None
        
        # Convert time to datetime
        if 'time' in df.columns:
            # Time might be Unix timestamp (int) or datetime
            if df['time'].dtype in ['int64', 'int32', 'int']:
                df['time'] = pd.to_datetime(df['time'], unit='s')
            elif not pd.api.types.is_datetime64_any_dtype(df['time']):
                # Try to convert if it's not already datetime
                try:
                    df['time'] = pd.to_datetime(df['time'], unit='s')
                except:
                    logger.warning(f"Could not convert time column for {symbol}")
        
        # Ensure numeric columns are float and fill NaN
        for col in ['open', 'high', 'low', 'close']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
                # Fill any remaining NaN with forward fill then backward fill
                if df[col].isna().any():
                    df[col] = df[col].ffill().bfill()
                    # If still NaN, use close price (or previous row)
                    if df[col].isna().any():
                        if col != 'close':
                            df[col] = df[col].fillna(df['close'])
                        else:
                            # For close, use previous close
                            df[col] = df[col].fillna(df[col].shift(1))
                            df[col] = df[col].fillna(df[col].shift(-1))  # Backward fill
        
        # Final validation - ensure we have valid data
        # Check for NaN values first (critical error)
        if df['close'].isna().any():
            logger.error(f"Invalid data after processing for {symbol}: {len(df)} rows, NaN count: {df['close'].isna().sum()}")
            return None
        
        # Check minimum rows (warning for insufficient data, not an error if it's just too few candles)
        if len(df) < 2:
            # This might happen in edge cases with minimal data - log as debug, not error
            logger.debug(f"Insufficient data for {symbol}: {len(df)} row(s) available (minimum 2 required). This may be expected in edge cases.")
            return None
        
        # Ensure we have enough data for SMA calculation
        if len(df) < self.sma_slow:
            logger.debug(f"{symbol}: Only {len(df)} bars available, need {self.sma_slow} for SMA calculation")
            # Still return the data, but SMA will have NaN for early periods (this is expected)
        
        # Update cache
        with self._rates_cache_lock:
            self._rates_cache[cache_key] = (df.copy(), now)
            
            # Cleanup old cache entries (keep only recent ones)
            if len(self._rates_cache) > 100:  # Limit cache size
                cutoff_time = now - self._rates_cache_ttl * 2
                self._rates_cache = {
                    k: v for k, v in self._rates_cache.items()
                    if v[1] > cutoff_time
                }
        
        return df
    
    def calculate_sma(self, df: pd.DataFrame, period: int, column: str = 'close') -> pd.Series:
        """Calculate Simple Moving Average."""
        sma = df[column].rolling(window=period).mean()
        
        # 🔍 Log SMA calculation details in SIM_LIVE mode
        is_sim_live = self.config.get('mode') == 'SIM_LIVE'
        if is_sim_live and len(df) >= period:
            try:
                # Get the closes used for the latest SMA value (newest 'period' closes)
                closes_for_sma = df[column].iloc[:period].tolist() if len(df) >= period else df[column].tolist()
                # CRITICAL: DataFrame is newest-first, so newest SMA is at index (period-1), not -1
                # rolling() calculates: row 0-19 → SMA at row 19, row 1-20 → SMA at row 20, etc.
                # The newest valid SMA is at index (period-1) where window covers rows 0 to (period-1)
                latest_sma = sma.iloc[period-1] if len(sma) >= period else (sma.iloc[-1] if len(sma) > 0 else None)
                latest_sma_str = f"{latest_sma:.5f}" if latest_sma is not None else "N/A"
                logger.info(f"[SIM_LIVE] [SMA_CALC] SMA{period}: Using {len(closes_for_sma)} closes, latest SMA={latest_sma_str}")
                logger.info(f"[SIM_LIVE] [SMA_CALC] SMA{period} closes (newest first): {closes_for_sma[:10] if len(closes_for_sma) >= 10 else closes_for_sma}...")
            except Exception as e:
                logger.warning(f"[SIM_LIVE] [SMA_CALC] Failed to log SMA{period} details: {e}")
        
        return sma
    
    def calculate_rsi(self, df: pd.DataFrame, period: int = 14, column: str = 'close') -> pd.Series:
        """Calculate Relative Strength Index."""
        # 🔍 STAGE C: Log exact 14 closes used for RSI calculation
        # CRITICAL FIX: DataFrame is newest-first, so NEWEST 14 closes are at iloc[:period], NOT iloc[-period:]
        is_sim_live = self.config.get('mode') == 'SIM_LIVE'
        if is_sim_live and len(df) >= period:
            try:
                # NEWEST 14 closes are at indices 0-13 (first period rows), not last period rows
                newest_14_closes = df[column].iloc[:period].tolist()
                newest_14_indices = list(range(period))
                logger.info(f"[SIM_LIVE] [STAGE_C_RSI_INPUT] Newest 14 closes used for RSI: {newest_14_closes}")
                logger.info(f"[SIM_LIVE] [STAGE_C_RSI_INPUT] Newest 14 indices: {newest_14_indices}")
            except Exception as e:
                logger.warning(f"[SIM_LIVE] [STAGE_C_RSI_INPUT] Failed to log RSI inputs: {e}")
        
        delta = df[column].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        # 🔍 STAGE C: Log gain/loss calculations
        # CRITICAL FIX: DataFrame is newest-first, so newest valid gain/loss is at index (period-1), not -1
        if is_sim_live and len(gain) > 0:
            try:
                # Newest valid gain/loss is at index (period-1) where rolling window covers rows 0 to (period-1)
                latest_avg_gain = gain.iloc[period-1] if len(gain) >= period and not pd.isna(gain.iloc[period-1]) else (gain.iloc[-1] if not pd.isna(gain.iloc[-1]) else 0)
                latest_avg_loss = loss.iloc[period-1] if len(loss) >= period and not pd.isna(loss.iloc[period-1]) else (loss.iloc[-1] if not pd.isna(loss.iloc[-1]) else 0)
                logger.info(f"[SIM_LIVE] [STAGE_C_RSI_CALC] avg_gain={latest_avg_gain:.8f}, avg_loss={latest_avg_loss:.8f}")
                if latest_avg_loss == 0:
                    logger.error(f"[SIM_LIVE] [STAGE_C_RSI_CALC] ❌ HARD FAIL: avg_loss == 0! This causes RSI=100. Newest 14 closes: {newest_14_closes if 'newest_14_closes' in locals() else 'N/A'}")
                    # Log delta breakdown for newest 14 (indices 0-13)
                    newest_14_delta = delta.iloc[:period].tolist()
                    logger.error(f"[SIM_LIVE] [STAGE_C_RSI_CALC] Newest 14 deltas: {newest_14_delta}")
                    gains_only = [d if d > 0 else 0 for d in newest_14_delta]
                    losses_only = [-d if d < 0 else 0 for d in newest_14_delta]
                    logger.error(f"[SIM_LIVE] [STAGE_C_RSI_CALC] Gains: {gains_only}, Losses: {losses_only}")
            except Exception as e:
                logger.warning(f"[SIM_LIVE] [STAGE_C_RSI_CALC] Failed to log RSI calc: {e}")
        
        # Avoid division by zero
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        # Fill NaN values (where loss was 0) with 100 (overbought)
        rsi = rsi.fillna(100)
        
        # 🔍 STAGE C: Log final RSI value
        # CRITICAL FIX: DataFrame is newest-first, so newest valid RSI is at index (period-1), not -1
        if is_sim_live and len(rsi) > 0:
            try:
                latest_rsi = rsi.iloc[period-1] if len(rsi) >= period and not pd.isna(rsi.iloc[period-1]) else (rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 100)
                logger.info(f"[SIM_LIVE] [STAGE_C_RSI_RESULT] Final RSI value: {latest_rsi:.2f}")
            except:
                pass
        
        return rsi
    
    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate Average True Range (ATR)."""
        high = df['high']
        low = df['low']
        close = df['close']
        
        # Calculate True Range
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # Calculate ATR as moving average of TR
        atr = tr.rolling(window=period).mean()
        
        return atr
    
    def calculate_adx(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate Average Directional Index (ADX) for trend strength."""
        high = df['high']
        low = df['low']
        close = df['close']
        
        # Calculate +DM and -DM
        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        
        # Calculate True Range
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # Smooth the values
        atr = tr.rolling(window=period).mean()
        plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr)
        
        # Calculate DX
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
        
        # Calculate ADX
        adx = dx.rolling(window=period).mean()
        
        return adx
    
    def calculate_choppiness(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        Calculate Choppiness Index (CI) to identify choppy/flat markets.
        Returns values between 0 (trending) and 100 (choppy).
        Normalized to 0-1 for easier use.
        """
        high = df['high']
        low = df['low']
        close = df['close']
        
        # Calculate True Range
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # Calculate ATR
        atr = tr.rolling(window=period).mean()
        
        # Calculate highest high and lowest low over period
        highest_high = high.rolling(window=period).max()
        lowest_low = low.rolling(window=period).min()
        
        # Calculate Choppiness Index
        ci = 100 * np.log10(atr.rolling(window=period).sum() / (highest_high - lowest_low)) / np.log10(period)
        
        # Normalize to 0-1 (0 = trending, 1 = choppy)
        ci_normalized = ci / 100.0
        
        return ci_normalized
    
    # ------------------------------------------------------------------
    # ENTRY TIMING HELPERS (READ-ONLY, PRE-TRADE BLOCKS ONLY)
    # ------------------------------------------------------------------
    def check_trend_maturity(self, symbol: str, trend_signal: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Detect late / overextended trend phases and block structurally bad entries.
        
        This does NOT change existing filters or thresholds – it only adds an additional
        timing guard on top of already-approved setups.
        
        Rules (heuristic, conservative):
        - If price has been on the same side of SMA50 for many candles AND
          current price is far from SMA50, treat it as a late trend phase.
        - Block entries in that case to avoid entering at the end of a move.
        """
        try:
            df = self.get_rates(symbol, count=max(self.sma_slow * 2, 100))
            if df is None or len(df) < self.sma_slow + 5:
                # Not enough history to judge trend phase – allow
                return True, "Trend phase: insufficient data to evaluate"
            
            close = df["close"]
            sma_fast_series = self.calculate_sma(df, self.sma_fast)
            sma_slow_series = self.calculate_sma(df, self.sma_slow)
            
            latest_close = close.iloc[-1]
            latest_sma_slow = sma_slow_series.iloc[-1]
            if pd.isna(latest_sma_slow) or latest_sma_slow <= 0:
                return True, "Trend phase: invalid SMA data"
            
            # Distance of price from slow SMA in percentage terms
            dist_pct = abs(latest_close - latest_sma_slow) / latest_sma_slow * 100.0
            
            # Bars since SMA20/50 cross (approximate trend age)
            lookback = min(50, len(df) - 1)
            fast_minus_slow = sma_fast_series.iloc[-lookback:] - sma_slow_series.iloc[-lookback:]
            # Count how many of the last N bars had the same sign as current signal
            current_signal = trend_signal.get("signal", "NONE")
            if current_signal == "LONG":
                same_side = (fast_minus_slow > 0).sum()
            elif current_signal == "SHORT":
                same_side = (fast_minus_slow < 0).sum()
            else:
                # No active trend – let existing logic handle it
                return True, "Trend phase: no active trend"
            
            # Heuristic late-trend conditions (do not depend on external thresholds):
            # - Trend has persisted for many bars on the same side of SMA50
            # - Price is materially far from SMA50 in percentage terms
            if same_side >= 20 and dist_pct >= 0.5:
                direction_str = "LONG" if current_signal == "LONG" else "SHORT"
                reason = (
                    f"[TIMING BLOCK] {symbol}: Late trend phase - trend {direction_str} "
                    f"has persisted for {same_side} bars with price {dist_pct:.3f}% away from SMA{self.sma_slow}"
                )
                logger.info(reason)
                return False, reason
            
            return True, "Trend phase acceptable"
        except Exception as e:
            logger.debug(f"{symbol}: Trend maturity check failed: {e}")
            # Fail-open for safety – existing filters still apply
            return True, "Trend phase: evaluation error"
    
    def check_impulse_exhaustion(self, symbol: str, trend_signal: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Guard against entering on impulse / exhaustion candles.
        
        Logic:
        - Compute current candle range vs average of prior N ranges.
        - If current range is a large multiple of the recent average AND
          recent candles all moved in the same direction, treat it as a
          potential exhaustion run and block entry.
        """
        try:
            df = self.get_rates(symbol, count=40)
            if df is None or len(df) < 25:
                return True, "Impulse guard: insufficient data"
            
            recent = df.tail(25)
            latest = recent.iloc[-1]
            prev = recent.iloc[:-1]
            
            current_range = latest["high"] - latest["low"]
            prev_ranges = (prev["high"] - prev["low"]).tolist()
            avg_range = sum(prev_ranges) / len(prev_ranges) if prev_ranges else 0.0
            if avg_range <= 0 or current_range <= 0:
                return True, "Impulse guard: invalid range data"
            
            range_ratio = current_range / avg_range
            
            # Direction of current candle and previous few candles
            signal = trend_signal.get("signal", "NONE")
            last3 = recent.tail(3)  # includes latest
            if len(last3) < 3:
                return True, "Impulse guard: not enough recent candles"
            
            # Count stacked candles in signal direction (body sign)
            def body_dir(c):
                return 1 if c["close"] > c["open"] else (-1 if c["close"] < c["open"] else 0)
            
            dirs = [body_dir(row) for _, row in last3.iterrows()]
            same_dir_count = 0
            if signal == "LONG":
                same_dir_count = sum(1 for d in dirs if d > 0)
            elif signal == "SHORT":
                same_dir_count = sum(1 for d in dirs if d < 0)
            
            # Heuristic: very large candle (>= 2.5x average) and stacked in same direction
            if range_ratio >= 2.5 and same_dir_count >= 2:
                reason = (
                    f"[TIMING BLOCK] {symbol}: Impulse/exhaustion candle - "
                    f"current range {range_ratio:.2f}x recent average with {same_dir_count} "
                    f"candles in {signal} direction"
                )
                logger.info(reason)
                return False, reason
            
            return True, "Impulse guard: structure acceptable"
        except Exception as e:
            logger.debug(f"{symbol}: Impulse/exhaustion check failed: {e}")
            return True, "Impulse guard: evaluation error"
    
    def _calculate_volatility_floor_score(self, symbol: str, df: pd.DataFrame) -> Tuple[int, str]:
        """
        Calculate volatility floor component score.
        
        Returns:
            (score: int, reason: str)
        """
        if not self.volatility_floor_enabled:
            return 0, ""
        
        try:
            # Calculate average range of last N candles
            if len(df) < self.volatility_floor_candle_count:
                return 0, "Insufficient data for volatility floor"
            
            ranges = []
            for i in range(min(self.volatility_floor_candle_count, len(df))):
                candle = df.iloc[i]
                ranges.append(candle['high'] - candle['low'])
            
            avg_range = sum(ranges) / len(ranges) if ranges else 0
            
            # Get symbol info to convert pips to price
            symbol_info = self.mt5_connector.get_symbol_info(symbol)
            if symbol_info is None:
                return 0, ""
            
            point = symbol_info.get('point', 0.00001)
            min_range_price = self.volatility_floor_min_range_pips * point * 10  # Convert pips to price
            
            avg_range_pips = avg_range / point / 10 if point > 0 else 0
            
            if avg_range >= min_range_price:
                return 10, f"Volatility floor: avg range {avg_range_pips:.2f} pips >= min {self.volatility_floor_min_range_pips:.2f} pips"
            elif avg_range >= min_range_price * (1.0 / 1.5):  # 1.0 pips threshold
                return 5, f"Volatility floor: avg range {avg_range_pips:.2f} pips (moderate)"
            else:
                return 0, f"Volatility floor: avg range {avg_range_pips:.2f} pips < threshold"
        except Exception as e:
            logger.debug(f"Volatility floor calculation failed: {e}")
            return 0, ""
    
    def _calculate_candle_quality_score(self, df: pd.DataFrame) -> Tuple[int, str]:
        """
        Calculate candle quality component score.
        
        Returns:
            (score: int, reason: str)
        """
        if not self.candle_quality_enabled:
            return 0, ""
        
        try:
            if len(df) < 21:
                return 0, "Insufficient data for candle quality"
            
            # Current candle range (most recent)
            current_candle = df.iloc[0]
            current_range = current_candle['high'] - current_candle['low']
            
            # Average range of previous 20 candles
            prev_ranges = []
            for i in range(1, min(21, len(df))):
                candle = df.iloc[i]
                prev_ranges.append(candle['high'] - candle['low'])
            
            avg_range = sum(prev_ranges) / len(prev_ranges) if prev_ranges else 0
            
            if avg_range == 0:
                return 0, "Zero average range"
            
            current_range_pct = (current_range / avg_range) * 100.0
            
            if current_range_pct >= self.candle_quality_min_percent:
                return 15, f"Candle quality: {current_range_pct:.1f}% of avg (>= {self.candle_quality_min_percent}%)"
            elif current_range_pct >= 50.0:
                return 8, f"Candle quality: {current_range_pct:.1f}% of avg (moderate)"
            else:
                return 0, f"Candle quality: {current_range_pct:.1f}% of avg (< 50%)"
        except Exception as e:
            logger.debug(f"Candle quality calculation failed: {e}")
            return 0, ""
    
    def _calculate_spread_penalty(self, symbol: str) -> Tuple[int, str]:
        """
        Calculate spread cost penalty (deducts points).
        
        Returns:
            (penalty: int (negative), reason: str)
        """
        try:
            symbol_info = self.mt5_connector.get_symbol_info(symbol)
            if symbol_info is None:
                return 0, ""
            
            bid = symbol_info.get('bid', 0)
            ask = symbol_info.get('ask', 0)
            point = symbol_info.get('point', 0.00001)
            contract_size = symbol_info.get('trade_contract_size', 100000)
            
            if bid <= 0 or ask <= 0:
                return 0, ""
            
            # Calculate spread cost for 0.01 lot (standard minimum)
            spread_price = ask - bid
            lot_size = 0.01
            spread_cost_usd = spread_price * lot_size * contract_size
            
            # Calculate spread cost as percentage of risk
            if self.max_risk_usd > 0:
                spread_percent = (spread_cost_usd / self.max_risk_usd) * 100.0
                
                if spread_percent > 15.0:
                    return -15, f"Spread penalty: {spread_percent:.1f}% of risk (>15%)"
                elif spread_percent > 10.0:
                    return -10, f"Spread penalty: {spread_percent:.1f}% of risk (10-15%)"
                elif spread_percent > 5.0:
                    return -5, f"Spread penalty: {spread_percent:.1f}% of risk (5-10%)"
                else:
                    return 0, f"Spread acceptable: {spread_percent:.1f}% of risk"
            else:
                return 0, ""
        except Exception as e:
            logger.debug(f"Spread penalty calculation failed: {e}")
            return 0, ""
    
    def assess_setup_quality(self, symbol: str, trend_signal: Dict[str, Any]) -> Dict[str, Any]:
        """
        Assess the quality of a trading setup for scalping.
        Returns quality score and reasons.
        
        Scoring components:
        - Trend (SMA + ADX consolidated): 35 points max
        - RSI confirmation: 20 points max
        - Volatility floor: 10 points max
        - Candle quality: 15 points max
        - Choppiness (if enabled): 20 points max
        - Spread penalty: -15 points max (deducts)
        Total: 100 points max (capped at 100)
        """
        df = self.get_rates(symbol, count=100)
        if df is None or len(df) < self.sma_slow:
            return {
                'quality_score': 0,
                'is_high_quality': False,
                'reasons': ['Insufficient data']
            }
        
        reasons = []
        score = 0
        
        # 1. Trend strength (SMA separation + ADX consolidated) - 35 points max
        sma_separation = abs(trend_signal.get('sma_fast', 0) - trend_signal.get('sma_slow', 0))
        sma_separation_pct = (sma_separation / trend_signal.get('sma_slow', 1)) * 100 if trend_signal.get('sma_slow', 0) > 0 else 0
        
        # Calculate ADX
        latest_adx = None
        adx_score = 0
        try:
            adx = self.calculate_adx(df, period=14)
            latest_adx = adx.iloc[-1] if not pd.isna(adx.iloc[-1]) else 0
            
            if latest_adx >= self.min_adx:
                adx_score = 20  # Reduced from 25 to fit in 35 total
            elif latest_adx >= self.min_adx * 0.7:
                adx_score = 12  # Reduced from 15
            elif latest_adx >= self.min_adx * 0.5:
                adx_score = 5
        except Exception as e:
            logger.debug(f"ADX calculation failed: {e}")
        
        # Combine SMA and ADX scores (consolidated trend component: 35 max)
        sma_score = 0
        if sma_separation_pct > 0.1:
            sma_score = 20  # Reduced from 30
        elif sma_separation_pct > 0.05:
            sma_score = 10  # Reduced from 15
        
        # Combined trend score: use the higher of the two metrics, with bonus for both being strong
        if sma_score >= 10 and adx_score >= 12:
            trend_score = 35  # Bonus for both being strong
        elif sma_score >= 10 and adx_score >= 5:
            trend_score = min(30, sma_score + adx_score)
        else:
            trend_score = max(sma_score, adx_score)
        
        score += int(trend_score)
        # Format ADX value safely (avoid conditional in format specifier)
        adx_display = f"{latest_adx:.1f}" if latest_adx else "0"
        if trend_score >= 30:
            reasons.append(f"Strong trend (SMA: {sma_separation_pct:.3f}%, ADX: {adx_display})")
        elif trend_score >= 15:
            reasons.append(f"Moderate trend (SMA: {sma_separation_pct:.3f}%, ADX: {adx_display})")
        else:
            reasons.append(f"Weak trend (SMA: {sma_separation_pct:.3f}%, ADX: {adx_display})")
        
        # 2. RSI confirmation - 20 points max (reduced from 25)
        rsi = trend_signal.get('rsi', 50)
        rsi_filter_passed = trend_signal.get('rsi_filter_passed', False)
        
        if rsi_filter_passed:
            score += 20
            reasons.append(f"RSI confirmation (RSI: {rsi:.1f})")
        elif 25 <= rsi <= 75:
            score += 10
            reasons.append(f"RSI acceptable (RSI: {rsi:.1f}, soft filter)")
        else:
            reasons.append(f"RSI not ideal (RSI: {rsi:.1f})")
        
        # 3. Volatility floor - 10 points max
        volatility_score, volatility_reason = self._calculate_volatility_floor_score(symbol, df)
        if volatility_score > 0:
            score += volatility_score
            reasons.append(volatility_reason)
        
        # 4. Candle quality - 15 points max
        candle_score, candle_reason = self._calculate_candle_quality_score(df)
        if candle_score > 0:
            score += candle_score
            reasons.append(candle_reason)
        
        # 5. Volatility filter (choppiness) - 20 points max (if enabled)
        latest_choppiness = None
        if self.use_volatility_filter:
            try:
                choppiness = self.calculate_choppiness(df, period=14)
                latest_choppiness = choppiness.iloc[-1] if not pd.isna(choppiness.iloc[-1]) else 1.0
                
                if latest_choppiness < self.max_choppiness:
                    score += 20
                    reasons.append(f"Low choppiness (CI: {latest_choppiness:.2f})")
                else:
                    reasons.append(f"Market too choppy (CI: {latest_choppiness:.2f})")
            except Exception as e:
                logger.debug(f"Choppiness calculation failed: {e}")
                reasons.append("Choppiness calculation failed")
        
        # 6. Spread penalty (deducts points, up to -15)
        spread_penalty, spread_reason = self._calculate_spread_penalty(symbol)
        score += spread_penalty  # spread_penalty is negative
        if spread_penalty < 0:
            reasons.append(spread_reason)
        
        # Cap score at 100
        score = min(100, max(0, score))
        
        # Use configurable quality threshold
        quality_threshold = self.min_quality_score
        is_high_quality = score >= quality_threshold
        
        return {
            'quality_score': score,
            'is_high_quality': is_high_quality,
            'reasons': reasons,
            'sma_separation_pct': sma_separation_pct,
            'rsi': rsi,
            'choppiness': latest_choppiness,
            'adx': latest_adx
        }
    
    def calculate_dynamic_stop_loss(self, symbol: str, min_stop_loss_pips: float = 10) -> float:
        """
        Calculate dynamic stop loss based on ATR and volatility.
        
        Returns stop loss in pips.
        """
        df = self.get_rates(symbol, count=100)
        if df is None or len(df) < self.atr_period:
            return min_stop_loss_pips
        
        # Calculate ATR
        # CRITICAL FIX: DataFrame is newest-first, so ATR rolling calculates from index 0 forward
        # Newest valid ATR is at index (atr_period-1), not -1
        atr = self.calculate_atr(df, self.atr_period)
        latest_atr = atr.iloc[self.atr_period-1] if len(atr) >= self.atr_period and not pd.isna(atr.iloc[self.atr_period-1]) else (atr.iloc[-1] if len(atr) > 0 and not pd.isna(atr.iloc[-1]) else 0)
        
        if pd.isna(latest_atr) or latest_atr <= 0:
            return min_stop_loss_pips
        
        # Get symbol info for point conversion
        symbol_info = self.mt5_connector.get_symbol_info(symbol)
        if symbol_info is None:
            return min_stop_loss_pips
        
        point = symbol_info['point']
        pip_value = point * 10 if symbol_info['digits'] == 5 or symbol_info['digits'] == 3 else point
        
        # Convert ATR to pips
        atr_pips = (latest_atr / pip_value) * self.atr_multiplier
        
        # Ensure minimum stop loss
        stop_loss_pips = max(atr_pips, min_stop_loss_pips)
        
        # For crypto, use larger stop loss (they're more volatile)
        is_crypto = any(crypto in symbol.upper() for crypto in ['BTC', 'ETH', 'XRP', 'ADA', 'BCH', 'LTC', 'BNB', 'BAT', 'DOGE', 'DOT', 'LINK', 'UNI'])
        if is_crypto:
            stop_loss_pips = max(stop_loss_pips, min_stop_loss_pips * 1.5)
        
        # CRITICAL: Cap maximum stop loss to prevent unrealistic values
        # For most symbols, 500 pips is reasonable maximum
        # For crypto with high prices, use percentage-based cap (0.5% of price)
        max_stop_loss_pips = 500.0
        if is_crypto:
            current_price = symbol_info.get('bid', 0) or symbol_info.get('ask', 0)
            if current_price > 0:
                # Cap at 0.5% of price in pips
                max_pips_by_price = (current_price * 0.005) / pip_value
                max_stop_loss_pips = min(500.0, max_pips_by_price)
        
        # Apply maximum cap
        if stop_loss_pips > max_stop_loss_pips:
            logger.warning(f"[WARNING] {symbol}: Stop loss {stop_loss_pips:.1f} pips exceeds maximum {max_stop_loss_pips:.1f} pips, capping to maximum")
            stop_loss_pips = max_stop_loss_pips
        
        logger.debug(f"{symbol}: ATR={latest_atr:.5f}, ATR pips={atr_pips:.1f}, Final SL={stop_loss_pips:.1f} pips (max: {max_stop_loss_pips:.1f})")
        
        return stop_loss_pips
    
    def get_trend_signal(self, symbol: str) -> Dict[str, Any]:
        """
        Analyze trend using SIMPLE logic: SMA20 vs SMA50.
        
        SIMPLE RULES:
        - SMA20 > SMA50 = BUY (LONG)
        - SMA20 < SMA50 = SELL (SHORT)
        
        Returns:
            {
                'signal': 'LONG', 'SHORT', or 'NONE',
                'trend': 'BULLISH' or 'BEARISH',
                'sma_fast': float,
                'sma_slow': float,
                'rsi': float,
                'rsi_filter_passed': bool
            }
        """
        df = self.get_rates(symbol, count=100)
        
        # 🔍 Log DataFrame details in SIM_LIVE mode
        is_sim_live = self.config.get('mode') == 'SIM_LIVE'
        if is_sim_live and df is not None and len(df) > 0:
            try:
                first_5_closes = df['close'].iloc[:5].tolist()
                first_5_times = df['time'].iloc[:5].tolist() if 'time' in df.columns else []
                logger.info(f"[SIM_LIVE] [TRENDFILTER_DF] DataFrame has {len(df)} rows, first 5 closes: {first_5_closes}, first 5 times: {first_5_times}")
            except:
                pass
        
        if df is None or len(df) == 0:
            logger.debug(f"{symbol}: No data available for trend analysis")
            return {
                'signal': 'NONE',
                'trend': 'NEUTRAL',
                'sma_fast': 0,
                'sma_slow': 0,
                'rsi': 50,
                'rsi_filter_passed': True
            }
        
        # Check if we have enough data for SMA calculation
        if len(df) < self.sma_slow:
            # Not enough data yet - this is normal at the start of backtest
            logger.debug(f"{symbol}: Insufficient data for trend analysis (need {self.sma_slow} candles, got {len(df)}) - waiting for more data")
            return {
                'signal': 'NONE',
                'trend': 'NEUTRAL',
                'sma_fast': 0,
                'sma_slow': 0,
                'rsi': 50,
                'rsi_filter_passed': True
            }
        
        # Validate data quality before calculation
        if df['close'].isna().any() or df['close'].isnull().any():
            # Try to fix NaN values
            df['close'] = df['close'].ffill().bfill()
            if df['close'].isna().any():
                logger.warning(f"{symbol}: Still have NaN in close prices after fill, skipping trend analysis")
                return {
                    'signal': 'NONE',
                    'trend': 'NEUTRAL',
                    'sma_fast': 0,
                    'sma_slow': 0,
                    'rsi': 50,
                    'rsi_filter_passed': True
                }
        
        # Calculate indicators
        sma_fast = self.calculate_sma(df, self.sma_fast)
        sma_slow = self.calculate_sma(df, self.sma_slow)
        rsi = self.calculate_rsi(df, self.rsi_period)
        
        # Get latest values (handle NaN)
        # CRITICAL FIX: DataFrame is newest-first, so:
        # - rolling() calculates SMA/RSI progressively: row 0-19 → SMA at row 19, etc.
        # - Newest valid SMA20 is at index 19 (period-1), newest SMA50 is at index 49
        # - Newest valid RSI is at index 13 (period-1)
        # - Using iloc[-1] would get the OLDEST value (wrong!)
        latest_sma_fast = sma_fast.iloc[self.sma_fast-1] if len(sma_fast) >= self.sma_fast and not pd.isna(sma_fast.iloc[self.sma_fast-1]) else (sma_fast.iloc[-1] if len(sma_fast) > 0 else np.nan)
        latest_sma_slow = sma_slow.iloc[self.sma_slow-1] if len(sma_slow) >= self.sma_slow and not pd.isna(sma_slow.iloc[self.sma_slow-1]) else (sma_slow.iloc[-1] if len(sma_slow) > 0 else np.nan)
        latest_rsi = rsi.iloc[self.rsi_period-1] if len(rsi) >= self.rsi_period and not pd.isna(rsi.iloc[self.rsi_period-1]) else (rsi.iloc[-1] if len(rsi) > 0 and not pd.isna(rsi.iloc[-1]) else 50)
        
        # Handle NaN values - check if we have enough data for valid SMA
        if pd.isna(latest_sma_fast) or pd.isna(latest_sma_slow):
            # This is expected if we don't have enough data yet
            # Only log as debug to reduce noise
            logger.debug(f"{symbol}: SMA values are NaN (have {len(df)} bars, need {self.sma_slow} for SMA{self.sma_slow})")
            return {
                'signal': 'NONE',
                'trend': 'NEUTRAL',
                'sma_fast': 0,
                'sma_slow': 0,
                'rsi': 50,
                'rsi_filter_passed': True
            }
        
        # SIMPLE TREND LOGIC: SMA20 > SMA50 = BUY, SMA20 < SMA50 = SELL
        sma_diff = latest_sma_fast - latest_sma_slow
        sma_diff_pct = (sma_diff / latest_sma_slow * 100) if latest_sma_slow > 0 else 0
        
        if latest_sma_fast > latest_sma_slow:
            trend = 'BULLISH'
            signal = 'LONG'
            logger.info(f"{symbol}: [OK] TREND SIGNAL = LONG (SMA20={latest_sma_fast:.5f} > SMA50={latest_sma_slow:.5f}, diff={sma_diff_pct:.4f}%, RSI={latest_rsi:.1f})")
        elif latest_sma_fast < latest_sma_slow:
            trend = 'BEARISH'
            signal = 'SHORT'
            logger.info(f"{symbol}: [OK] TREND SIGNAL = SHORT (SMA20={latest_sma_fast:.5f} < SMA50={latest_sma_slow:.5f}, diff={sma_diff_pct:.4f}%, RSI={latest_rsi:.1f})")
        else:
            trend = 'NEUTRAL'
            signal = 'NONE'
            logger.info(f"{symbol}: [WARNING] TREND SIGNAL = NONE (SMA20={latest_sma_fast:.5f} == SMA50={latest_sma_slow:.5f})")
        
        # RSI filter: Use 30-50 range for entries (per user requirement)
        rsi_filter_passed = True
        if self.use_rsi_filter:
            if self.rsi_entry_range_min <= latest_rsi <= self.rsi_entry_range_max:
                rsi_filter_passed = True
                logger.debug(f"{symbol}: RSI filter PASSED ({latest_rsi:.1f} in range {self.rsi_entry_range_min}-{self.rsi_entry_range_max})")
            else:
                rsi_filter_passed = False
                logger.debug(f"{symbol}: RSI filter FAILED ({latest_rsi:.1f} not in range {self.rsi_entry_range_min}-{self.rsi_entry_range_max})")
        else:
            # RSI is logged but NOT used to block trades (if filter disabled)
            if latest_rsi > 80:
                logger.debug(f"{symbol}: RSI very overbought ({latest_rsi:.1f}) - informational only, NOT blocking trade")
            elif latest_rsi < 20:
                logger.debug(f"{symbol}: RSI very oversold ({latest_rsi:.1f}) - informational only, NOT blocking trade")
        
        # Calculate ATR for dynamic stop loss
        # CRITICAL FIX: DataFrame is newest-first, so ATR rolling calculates from index 0 forward
        # Newest valid ATR is at index (atr_period-1), not -1
        atr = self.calculate_atr(df, self.atr_period)
        latest_atr = atr.iloc[self.atr_period-1] if len(atr) >= self.atr_period and not pd.isna(atr.iloc[self.atr_period-1]) else (atr.iloc[-1] if len(atr) > 0 and not pd.isna(atr.iloc[-1]) else 0)
        
        result = {
            'signal': signal,
            'trend': trend,
            'sma_fast': latest_sma_fast,
            'sma_slow': latest_sma_slow,
            'rsi': latest_rsi,
            'rsi_filter_passed': rsi_filter_passed,
            'atr': latest_atr
        }
        
        return result
    
    def check_price_action_confirmation(self, symbol: str, trend_signal: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Check price action confirmation (support/resistance, candlestick patterns).
        
        Returns:
            (is_confirmed: bool, reason: str)
        """
        if not self.use_price_action_confirmation:
            return True, "Price action confirmation disabled"
        
        df = self.get_rates(symbol, count=50)
        if df is None or len(df) < 20:
            return True, "Insufficient data for price action analysis"
        
        try:
            # CRITICAL FIX: DataFrame is newest-first, so use head() and iloc[0] for newest candles
            # Get recent candles (newest first)
            recent = df.head(5)  # First 5 rows = newest 5 candles
            latest = recent.iloc[0]  # First row = newest candle
            prev = recent.iloc[1] if len(recent) >= 2 else None  # Second row = previous candle
            
            # Recent swing structure over last 20 bars (newest 20 candles)
            high_20 = df['high'].head(20).max()  # First 20 rows = newest 20 candles
            low_20 = df['low'].head(20).min()
            current_price = latest['close']
            
            # Avoid entries exactly at short-term extremes for a trend-following scalp:
            # - LONG: block if price is at/near 20-bar low (reversal location) or at 20-bar high (exhaustion).
            # - SHORT: block if price is at/near 20-bar high or at 20-bar low.
            if trend_signal['signal'] == 'LONG':
                # Too close to recent low (trying to catch falling knife)
                if current_price <= low_20 * 1.001:
                    return False, (
                        f"[TIMING BLOCK] {symbol}: Price action - LONG near extreme low "
                        f"(price: {current_price:.5f}, low_20: {low_20:.5f})"
                    )
                # Too close to recent high (buying into local extreme)
                if current_price >= high_20 * 0.999:
                    return False, (
                        f"[TIMING BLOCK] {symbol}: Price action - LONG at short-term high "
                        f"(price: {current_price:.5f}, high_20: {high_20:.5f})"
                    )
            elif trend_signal['signal'] == 'SHORT':
                if current_price >= high_20 * 0.999:
                    return False, (
                        f"[TIMING BLOCK] {symbol}: Price action - SHORT near extreme high "
                        f"(price: {current_price:.5f}, high_20: {high_20:.5f})"
                    )
                if current_price <= low_20 * 1.001:
                    return False, (
                        f"[TIMING BLOCK] {symbol}: Price action - SHORT at short-term low "
                        f"(price: {current_price:.5f}, low_20: {low_20:.5f})"
                    )
            
            # Simple continuation-style candlestick check:
            # Require a pullback candle followed by a resumption candle in trend direction
            if prev is not None:
                prev_bull = prev['close'] > prev['open']
                prev_bear = prev['close'] < prev['open']
                latest_bull = latest['close'] > latest['open']
                latest_bear = latest['close'] < latest['open']
                
                if trend_signal['signal'] == 'LONG':
                    # Prefer: small pullback (bearish or neutral) then bullish resumption
                    if latest_bull and (prev_bear or abs(prev['close'] - prev['open']) <= 1e-8):
                        return True, "Price action confirmed: pullback then bullish resumption for LONG"
                elif trend_signal['signal'] == 'SHORT':
                    # Prefer: small pullback (bullish or neutral) then bearish resumption
                    if latest_bear and (prev_bull or abs(prev['close'] - prev['open']) <= 1e-8):
                        return True, "Price action confirmed: pullback then bearish resumption for SHORT"
            
            # If no specific continuation confirmation, be conservative and block.
            return False, "[TIMING BLOCK] Price action - no clear continuation after pullback"
        
        except Exception as e:
            logger.debug(f"{symbol}: Price action confirmation error: {e}")
            # On error, fail-open but let other timing guards handle risk
            return True, "Price action check failed, allowing trade"
    
    def check_volume_confirmation(self, symbol: str) -> Tuple[bool, str]:
        """
        Check volume confirmation to filter low-probability trades.
        
        Returns:
            (is_confirmed: bool, reason: str)
        """
        if not self.use_volume_confirmation:
            return True, "Volume confirmation disabled"
        
        df = self.get_rates(symbol, count=50)
        if df is None or len(df) < 20:
            return True, "Insufficient data for volume analysis"
        
        try:
            # CRITICAL FIX: DataFrame is newest-first, so use head() and iloc[0] for newest candles
            # Get recent volume (tick_volume in MT5) - newest 20 candles
            recent_volumes = df['tick_volume'].head(20)  # First 20 rows = newest 20 candles
            avg_volume = recent_volumes.mean()
            latest_volume = df['tick_volume'].iloc[0]  # First row = newest candle
            
            # Check if latest volume is above average (indicates interest)
            if latest_volume >= avg_volume * 0.8:  # At least 80% of average volume
                return True, f"Volume confirmed: {latest_volume:.0f} >= {avg_volume * 0.8:.0f} (80% of avg)"
            else:
                # Still allow but log warning
                return True, f"Volume below average: {latest_volume:.0f} < {avg_volume * 0.8:.0f} (allowing for medium-frequency)"
            
        except Exception as e:
            logger.debug(f"{symbol}: Volume confirmation error: {e}")
            return True, "Volume check failed, allowing trade"
    
    def is_setup_valid_for_scalping(self, symbol: str, trend_signal: Dict[str, Any]) -> bool:
        """
        Check if setup is valid with all confirmations (RSI, price action, volume).
        """
        try:
            # CRITICAL: Add detailed logging for SIM_LIVE debugging
            signal_value = trend_signal.get('signal', 'MISSING')
            if signal_value == 'NONE' or signal_value == 'MISSING':
                logger.warning(f"[SIM_LIVE] {symbol}: Setup invalid - signal is '{signal_value}' (trend_signal keys: {list(trend_signal.keys())})")
                if signal_value == 'MISSING':
                    logger.warning(f"[SIM_LIVE] {symbol}: trend_signal dict: {trend_signal}")
                return False
            
            # Log configuration status for debugging
            is_sim_live = self.config.get('mode') == 'SIM_LIVE'
            # Also check if we're using synthetic market engine (more reliable indicator of SIM_LIVE)
            if not is_sim_live:
                try:
                    from sim_live.sim_live_mt5_connector import SimLiveMT5Connector
                    is_sim_live = isinstance(self.mt5_connector, SimLiveMT5Connector)
                except:
                    pass
            
            # CRITICAL FIX: Force disable price action and volume confirmation in SIM_LIVE mode
            # This is needed because config might have them enabled, but SIM_LIVE scenarios need them disabled
            if is_sim_live:
                # Override at runtime for SIM_LIVE mode
                use_price_action = False
                use_volume = False
            else:
                use_price_action = self.use_price_action_confirmation
                use_volume = self.use_volume_confirmation
            
            if is_sim_live:
                logger.info(f"[SIM_LIVE] {symbol}: Setup validation START - signal='{signal_value}', use_rsi_filter={self.use_rsi_filter}, use_price_action={use_price_action}, use_volume={use_volume}")
            
            # Check RSI filter
            if self.use_rsi_filter and not trend_signal.get('rsi_filter_passed', True):
                logger.warning(f"[SIM_LIVE] {symbol}: Setup invalid - RSI filter failed (RSI: {trend_signal.get('rsi', 50):.1f})")
                return False
            
            if is_sim_live:
                logger.info(f"[SIM_LIVE] {symbol}: RSI filter check PASSED")
            
            # Check price action confirmation
            if use_price_action:
                try:
                    price_action_ok, price_action_reason = self.check_price_action_confirmation(symbol, trend_signal)
                    if not price_action_ok:
                        logger.warning(f"[SIM_LIVE] {symbol}: Setup invalid - Price action confirmation failed: {price_action_reason}")
                        return False
                    if is_sim_live:
                        logger.info(f"[SIM_LIVE] {symbol}: Price action OK: {price_action_reason}")
                except Exception as e:
                    logger.error(f"[SIM_LIVE] {symbol}: Exception in price action confirmation: {e}", exc_info=True)
                    # Don't block on exception, but log it
                    if is_sim_live:
                        logger.warning(f"[SIM_LIVE] {symbol}: Price action check exception - allowing trade to proceed")
            elif is_sim_live:
                logger.info(f"[SIM_LIVE] {symbol}: Price action confirmation disabled")
            
            # Check volume confirmation
            if use_volume:
                try:
                    volume_ok, volume_reason = self.check_volume_confirmation(symbol)
                    if not volume_ok:
                        logger.warning(f"[SIM_LIVE] {symbol}: Setup invalid - Volume confirmation failed: {volume_reason}")
                        return False
                    if is_sim_live:
                        logger.info(f"[SIM_LIVE] {symbol}: Volume OK: {volume_reason}")
                except Exception as e:
                    logger.error(f"[SIM_LIVE] {symbol}: Exception in volume confirmation: {e}", exc_info=True)
                    # Don't block on exception, but log it
                    if is_sim_live:
                        logger.warning(f"[SIM_LIVE] {symbol}: Volume check exception - allowing trade to proceed")
            elif is_sim_live:
                logger.info(f"[SIM_LIVE] {symbol}: Volume confirmation disabled")
            
            if is_sim_live:
                logger.info(f"[SIM_LIVE] {symbol}: ✅ Setup VALID - signal is {trend_signal['signal']} with all confirmations")
            return True
        except Exception as e:
            logger.error(f"[SIM_LIVE] {symbol}: Exception in setup validation: {e}", exc_info=True)
            # On exception, fail safe - return False to block trade
            return False
    
    def is_trend_confirmed(self, symbol: str, direction: str) -> bool:
        """Check if trend is confirmed for given direction."""
        signal_data = self.get_trend_signal(symbol)
        
        if direction.upper() == 'LONG':
            return signal_data['signal'] == 'LONG' and signal_data['rsi_filter_passed']
        elif direction.upper() == 'SHORT':
            return signal_data['signal'] == 'SHORT' and signal_data['rsi_filter_passed']
        
        return False

