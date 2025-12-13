"""
News Filter Module
Checks for high-impact news events and blocks trading during news windows.
"""

import requests
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import time
from bs4 import BeautifulSoup
import MetaTrader5 as mt5
from utils.logger_factory import get_logger

logger = get_logger("news_filter", "logs/live/engine/news_filter.log")


class NewsFilter:
    """Filters trading based on news events."""
    
    def __init__(self, config: Dict[str, Any], mt5_connector=None):
        self.config = config
        self.news_config = config.get('news', {})
        self.mt5_connector = mt5_connector
        self.enabled = self.news_config.get('enabled', True)
        self.api_provider = self.news_config.get('api_provider', 'financialmodelingprep')
        self.api_key = self.news_config.get('api_key', '')
        # STEP 2a: News avoidance ±10 minutes around news events (per requirements)
        self.block_window_minutes = self.news_config.get('block_window_minutes', 10)
        self.high_impact_only = self.news_config.get('high_impact_only', True)
        self.check_interval = self.news_config.get('check_interval_seconds', 60)
        self.fallback_to_mt5 = self.news_config.get('fallback_to_mt5', True)
        
        self.cached_events = []
        self.last_check_time = None
        self.last_error = None
        self.fallback_active = False
    
    def get_currency_from_symbol(self, symbol: str) -> List[str]:
        """Extract currency pairs from symbol."""
        # Common currency codes
        currencies = ['USD', 'EUR', 'GBP', 'JPY', 'CHF', 'AUD', 'CAD', 'NZD', 'NZD', 'SEK', 'NOK', 'DKK']
        
        result = []
        for currency in currencies:
            if currency in symbol:
                result.append(currency)
        
        # For crypto, return empty (news filter mainly for forex)
        if any(crypto in symbol.upper() for crypto in ['BTC', 'ETH', 'XRP', 'ADA', 'BCH', 'LTC', 'BNB', 'BAT', 'DOGE', 'DOT', 'LINK', 'UNI']):
            return []  # Crypto doesn't have traditional news events
        
        return result if len(result) >= 1 else []
    
    def check_forexfactory_news(self, symbol: str) -> List[Dict[str, Any]]:
        """
        Check Forex Factory calendar (web scraping approach).
        Note: Forex Factory may block automated requests. Consider using an API service.
        """
        if not self.enabled:
            return []
        
        try:
            currencies = self.get_currency_from_symbol(symbol)
            if len(currencies) < 2:
                return []
            
            # Forex Factory calendar URL
            url = "https://www.forexfactory.com/calendar"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                events = []
                
                # Forex Factory calendar structure (simplified parsing)
                # Note: Actual parsing depends on current HTML structure
                # This is a basic implementation - may need adjustment
                calendar_rows = soup.find_all('tr', class_='calendar_row')
                
                for row in calendar_rows:
                    try:
                        # Extract event data (structure may vary)
                        impact_elem = row.find('td', class_='impact')
                        if impact_elem:
                            impact_class = impact_elem.get('class', [])
                            impact = 'LOW'
                            if 'high' in str(impact_class).lower():
                                impact = 'HIGH'
                            elif 'medium' in str(impact_class).lower():
                                impact = 'MEDIUM'
                            
                            if self.high_impact_only and impact != 'HIGH':
                                continue
                            
                        # Extract currency and time
                        currency_elem = row.find('td', class_='currency')
                        time_elem = row.find('td', class_='time')
                        event_elem = row.find('td', class_='event')
                        
                        if currency_elem and time_elem:
                            currency = currency_elem.get_text(strip=True)
                            time_str = time_elem.get_text(strip=True)
                            event_name = event_elem.get_text(strip=True) if event_elem else 'Unknown'
                            
                            # Check if event affects our currency pair
                            if any(curr in currency for curr in currencies):
                                # Parse time (simplified - may need timezone handling)
                                try:
                                    # Forex Factory times are usually in EST/EDT
                                    # This is a simplified parser - adjust as needed
                                    event_time = datetime.now()  # Placeholder
                                    # In production, properly parse Forex Factory time format
                                    
                                    events.append({
                                        'time': event_time,
                                        'currency': currency,
                                        'event': event_name,
                                        'impact': impact
                                    })
                                except:
                                    pass
                    except Exception as e:
                        logger.debug(f"Error parsing Forex Factory row: {e}")
                        continue
                
                logger.debug(f"Found {len(events)} Forex Factory events for {symbol}")
                return events
            else:
                logger.warning(f"Forex Factory returned status {response.status_code}")
                return []
            
        except requests.RequestException as e:
            logger.warning(f"Error fetching Forex Factory news: {e}")
            return []
        except Exception as e:
            logger.error(f"Error parsing Forex Factory news: {e}")
            return []
    
    def check_investing_com_news(self, symbol: str) -> List[Dict[str, Any]]:
        """
        Check Investing.com economic calendar.
        Note: Investing.com requires API access or proper web scraping setup.
        This is a basic implementation.
        """
        if not self.enabled:
            return []
        
        try:
            currencies = self.get_currency_from_symbol(symbol)
            if len(currencies) < 2:
                return []
            
            # Investing.com economic calendar URL
            url = "https://www.investing.com/economic-calendar/"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                # Investing.com uses JavaScript to load calendar data
                # For a full implementation, you would need to:
                # 1. Use Selenium/Playwright to render JavaScript
                # 2. Or use Investing.com API if available
                # 3. Or parse the JSON data from their API endpoints
                
                logger.debug(f"Investing.com calendar accessed (parsing not fully implemented)")
                # Return empty for now - implement full parsing as needed
                return []
            else:
                logger.warning(f"Investing.com returned status {response.status_code}")
                return []
                
        except requests.RequestException as e:
            logger.warning(f"Error fetching Investing.com news: {e}")
            return []
        except Exception as e:
            logger.error(f"Error checking Investing.com news: {e}")
            return []
    
    def check_financial_modeling_prep(self, symbol: str) -> List[Dict[str, Any]]:
        """Check Financial Modeling Prep API."""
        if not self.enabled:
            return []
        
        # If no API key, skip this provider
        if not self.api_key:
            logger.debug("Financial Modeling Prep API key not provided, skipping")
            return []
        
        try:
            currencies = self.get_currency_from_symbol(symbol)
            if len(currencies) < 1:
                return []
            
            # FMP economic calendar endpoint
            url = f"https://financialmodelingprep.com/api/v3/economic_calendar"
            params = {
                'apikey': self.api_key,
                'from': datetime.now().strftime('%Y-%m-%d'),
                'to': (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
            }
            
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                events = response.json()
                
                # Filter for relevant currencies and high impact
                relevant_events = []
                for event in events:
                    event_currency = event.get('country', '')
                    impact = event.get('impact', '').upper()
                    
                    # Check if event affects our currency pair
                    if any(curr in event_currency for curr in currencies):
                        if not self.high_impact_only or impact == 'HIGH':
                            event_time_str = event.get('date', '')
                            try:
                                event_time = datetime.fromisoformat(event_time_str.replace('Z', '+00:00'))
                                relevant_events.append({
                                    'time': event_time,
                                    'currency': event_currency,
                                    'event': event.get('event', ''),
                                    'impact': impact
                                })
                            except:
                                pass
                
                self.last_error = None
                self.fallback_active = False
                return relevant_events
            else:
                logger.warning(f"Financial Modeling Prep returned status {response.status_code}")
                self.last_error = f"HTTP {response.status_code}"
                return []
            
        except requests.RequestException as e:
            logger.warning(f"Error fetching Financial Modeling Prep news: {e}")
            self.last_error = str(e)
            return []
        except Exception as e:
            logger.error(f"Error checking Financial Modeling Prep: {e}")
            self.last_error = str(e)
            return []
    
    def check_mt5_calendar(self, symbol: str) -> List[Dict[str, Any]]:
        """Check MT5 economic calendar if available."""
        if not self.enabled or not self.fallback_to_mt5:
            return []
        
        if not self.mt5_connector or not self.mt5_connector.ensure_connected():
            return []
        
        try:
            # MT5 calendar API
            from_date = datetime.now()
            to_date = datetime.now() + timedelta(days=1)
            
            # Get calendar events from MT5
            calendar = mt5.calendar(from_date, to_date)
            
            if calendar is None or len(calendar) == 0:
                return []
            
            currencies = self.get_currency_from_symbol(symbol)
            if len(currencies) < 1:
                return []
            
            events = []
            for event in calendar:
                # Check if event affects our currency
                country = event.country if hasattr(event, 'country') else ''
                importance = event.importance if hasattr(event, 'importance') else 0
                
                # MT5 importance: 0=low, 1=medium, 2=high
                if self.high_impact_only and importance < 2:
                    continue
                
                # Check if currency matches
                if any(curr in country for curr in currencies):
                    event_time = datetime.fromtimestamp(event.time) if hasattr(event, 'time') else datetime.now()
                    events.append({
                        'time': event_time,
                        'currency': country,
                        'event': event.event if hasattr(event, 'event') else 'Unknown',
                        'impact': 'HIGH' if importance >= 2 else 'MEDIUM' if importance >= 1 else 'LOW'
                    })
            
            if events:
                logger.info(f"MT5 calendar found {len(events)} events for {symbol}")
                self.fallback_active = True
            
            return events
            
        except Exception as e:
            logger.debug(f"MT5 calendar check failed: {e}")
            return []
    
    def get_upcoming_news(self, symbol: str) -> List[Dict[str, Any]]:
        """Get upcoming news events for symbol with fallback mechanism."""
        if not self.enabled:
            return []
        
        events = []
        
        # Try primary news source
        if self.api_provider == 'forexfactory':
            events = self.check_forexfactory_news(symbol)
        elif self.api_provider == 'investing':
            events = self.check_investing_com_news(symbol)
        elif self.api_provider == 'financialmodelingprep':
            events = self.check_financial_modeling_prep(symbol)
        else:
            # Default to financialmodelingprep
            events = self.check_financial_modeling_prep(symbol)
        
        # If primary source fails and fallback enabled, try MT5 calendar
        if len(events) == 0 and self.fallback_to_mt5 and self.last_error:
            logger.info(f"Primary news source failed ({self.last_error}), trying MT5 calendar fallback")
            events = self.check_mt5_calendar(symbol)
        
        # If still no events and we have an error, log it
        if len(events) == 0 and self.last_error and not self.fallback_active:
            logger.debug(f"No news events found for {symbol} (last error: {self.last_error})")
        
        return events
    
    def is_news_blocking(self, symbol: str) -> bool:
        """
        Check if trading should be blocked due to HIGH-IMPACT news.
        
        Rules:
        - Block 10 minutes BEFORE high-impact news
        - Block 10 minutes AFTER high-impact news
        - Medium/low news is allowed (not blocked)
        - If news API fails, allow trading (don't block)
        
        Returns True if high-impact news event is within blocking window.
        """
        if not self.enabled:
            logger.debug(f"{symbol}: News filter disabled - allowing trade")
            return False
        
        # For crypto symbols, news filter doesn't apply
        if any(crypto in symbol.upper() for crypto in ['BTC', 'ETH', 'XRP', 'ADA', 'BCH', 'LTC', 'BNB', 'BAT', 'DOGE', 'DOT', 'LINK', 'UNI', 'XAU']):
            logger.debug(f"{symbol}: Crypto/Gold - news filter doesn't apply")
            return False  # Gold and crypto don't have traditional forex news
        
        now = datetime.now()
        
        # Check cached events first
        if self.last_check_time and (now - self.last_check_time).total_seconds() < self.check_interval:
            events = self.cached_events
        else:
            try:
                events = self.get_upcoming_news(symbol)
                self.cached_events = events
                self.last_check_time = now
            except Exception as e:
                # If news API fails, allow trading (don't block)
                logger.warning(f"{symbol}: News API check failed ({e}), allowing trade (fail-safe)")
                return False
        
        # STEP 2a: Block window: ±10 minutes around news events (per requirements)
        block_window_minutes = self.block_window_minutes
        
        # Check if any HIGH-IMPACT event is within blocking window
        for event in events:
            # Only block HIGH-IMPACT news
            impact = event.get('impact', '').upper()
            if impact != 'HIGH':
                continue  # Medium/low news is allowed
            
            event_time = event.get('time', now)
            if isinstance(event_time, str):
                try:
                    event_time = datetime.fromisoformat(event_time.replace('Z', '+00:00'))
                except:
                    continue
            
            time_diff = (event_time - now).total_seconds() / 60  # minutes
            
            # Block 10 minutes before and 10 minutes after high-impact news
            if -block_window_minutes <= time_diff <= block_window_minutes:
                logger.warning(f"[SKIP] NEWS BLOCKING trade for {symbol}: {event.get('event', 'Unknown')} "
                             f"at {event_time.strftime('%Y-%m-%d %H:%M')} (HIGH IMPACT, "
                             f"time diff: {time_diff:.1f} min, block window: ±{block_window_minutes} min)")
                return True
        
        logger.debug(f"{symbol}: No blocking news found - allowing trade")
        return False
    
    def get_next_news_time(self, symbol: str) -> Optional[datetime]:
        """Get the time of the next news event for the symbol."""
        events = self.get_upcoming_news(symbol)
        if not events:
            return None
        
        now = datetime.now()
        future_events = [e for e in events if e.get('time', now) > now]
        
        if not future_events:
            return None
        
        # Return earliest future event
        return min([e.get('time', now) for e in future_events])

