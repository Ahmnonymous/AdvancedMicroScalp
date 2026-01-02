"""
Regression Guard - Change Budget Monitoring
Monitors metrics against phase-specific change budgets and triggers automatic rollback.
"""

import json
import os
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
from utils.logger_factory import get_logger

logger = get_logger("regression_guard", "logs/live/system/regression_guard.log")


class RegressionGuard:
    """Monitors metrics against change budget and triggers rollback."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.baseline_metrics = self._load_baseline()
        self.phase_budgets = self._load_phase_budgets()
        self.current_phase = config.get('deployment', {}).get('current_phase', 1)
        self.config_path = config.get('config_path', 'config.json')
        
    def _load_baseline(self) -> Dict[str, float]:
        """Load baseline metrics from file or config."""
        baseline_file = 'logs/live/system/baseline_metrics.json'
        if os.path.exists(baseline_file):
            try:
                with open(baseline_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load baseline metrics: {e}")
        
        # Default baseline from audit
        return {
            'win_rate': 0.829,  # 82.9%
            'expectancy_per_trade': -0.84,  # -$0.84
            'p95_latency_ms': 310.0,
            'sl_worker_health_events_per_day': 4466.0
        }
    
    def _load_phase_budgets(self) -> Dict[int, Dict[str, Dict[str, Any]]]:
        """Load phase-specific change budgets."""
        return {
            1: {
                'win_rate': {
                    'baseline': 0.829,
                    'rollback_trigger': 0.80,
                    'type': 'lower_bound'
                },
                'expectancy_per_trade': {
                    'baseline': -0.84,
                    'rollback_trigger': -1.00,
                    'type': 'lower_bound'
                },
                'p95_latency_ms': {
                    'baseline': 310.0,
                    'rollback_trigger': 500.0,
                    'type': 'upper_bound'
                },
                'sl_worker_health_events_per_hour': {
                    'baseline': 186.0,  # 4466/day / 24
                    'rollback_trigger': 50.0,
                    'type': 'upper_bound'
                }
            },
            2: {
                'win_rate': {
                    'baseline': None,  # Will use Phase 1 result
                    'rollback_trigger': None,  # Will calculate from Phase 1
                    'type': 'lower_bound'
                },
                'expectancy_per_trade': {
                    'baseline': None,
                    'rollback_trigger': None,
                    'type': 'lower_bound'
                },
                'p95_latency_ms': {
                    'baseline': None,
                    'rollback_trigger': 500.0,
                    'type': 'upper_bound'
                },
                'cache_hit_rate': {
                    'baseline': 0.0,
                    'rollback_trigger': 0.70,
                    'type': 'lower_bound'
                }
            },
            3: {
                'win_rate': {
                    'baseline': None,
                    'rollback_trigger': None,
                    'type': 'lower_bound'
                },
                'log_overhead_ms': {
                    'baseline': 0.0,
                    'rollback_trigger': 10.0,
                    'type': 'upper_bound'
                }
            },
            4: {
                'win_rate': {
                    'baseline': None,
                    'rollback_trigger': None,
                    'type': 'lower_bound'
                },
                'expectancy_per_trade': {
                    'baseline': None,
                    'rollback_trigger': None,
                    'type': 'lower_bound'
                },
                'rejection_rate': {
                    'baseline': 0.9999,
                    'rollback_trigger': 0.98,
                    'type': 'upper_bound'
                }
            },
            5: {
                'win_rate': {
                    'baseline': None,
                    'rollback_trigger': None,
                    'type': 'lower_bound'
                },
                'expectancy_per_trade': {
                    'baseline': None,
                    'rollback_trigger': None,
                    'type': 'lower_bound'
                },
                'risk_reward_ratio': {
                    'baseline': 0.19,
                    'rollback_trigger': 0.15,
                    'type': 'lower_bound'
                },
                'avg_loss': {
                    'baseline': -1.04,
                    'rollback_trigger': -1.20,
                    'type': 'lower_bound'
                }
            }
        }
    
    def check_metrics(self, metrics: Dict[str, float], min_sample_size: int = 10) -> Tuple[bool, Optional[str]]:
        """
        Check if metrics breach change budget. Returns (is_safe, rollback_reason).
        
        CRITICAL FIX: Only check metrics if we have sufficient sample size.
        Don't trigger kill switch on 0.0 win_rate when there are no trades yet.
        
        Args:
            metrics: Dictionary of metric_name -> value
            min_sample_size: Minimum number of trades required before checking metrics (default: 10)
            
        Returns:
            Tuple of (is_safe: bool, rollback_reason: Optional[str])
        """
        # Check if we have sufficient sample size
        total_trades = metrics.get('total_trades', 0)
        if total_trades < min_sample_size:
            logger.debug(f"[REGRESSION_GUARD] Insufficient sample size ({total_trades} trades < {min_sample_size}) - skipping check")
            return True, None  # Safe - not enough data to make decision
        
        # CRITICAL FIX: Additional safety check - if total_trades is 0, definitely skip
        # This prevents false triggers when metrics are collected but no trades exist
        if total_trades == 0:
            logger.debug(f"[REGRESSION_GUARD] No trades yet (total_trades=0) - skipping check to prevent false triggers")
            return True, None  # Safe - no trades means no metrics to evaluate
        
        budget = self.phase_budgets.get(self.current_phase, {})
        
        for metric_name, value in metrics.items():
            if metric_name not in budget:
                continue
            
            # Skip checking metrics that require trades if we don't have enough trades
            if metric_name in ['win_rate', 'expectancy_per_trade'] and total_trades < min_sample_size:
                logger.debug(f"[REGRESSION_GUARD] Skipping {metric_name} check - insufficient sample size ({total_trades} trades)")
                continue
            
            budget_config = budget[metric_name]
            threshold = budget_config.get('rollback_trigger')
            
            if threshold is None:
                # Try to calculate from previous phase
                threshold = self._calculate_threshold_from_previous_phase(metric_name)
                if threshold is None:
                    continue
            
            if self._breaches_threshold(metric_name, value, threshold, budget_config):
                reason = f"{metric_name} breached: {value} vs threshold {threshold}"
                logger.critical(f"[REGRESSION_GUARD] {reason}")
                return False, reason
        
        return True, None
    
    def _calculate_threshold_from_previous_phase(self, metric_name: str) -> Optional[float]:
        """Calculate threshold from previous phase baseline if available."""
        if self.current_phase <= 1:
            return None
        
        # Try to get from previous phase result
        phase_result_file = f'logs/live/system/phase_{self.current_phase - 1}_result.json'
        if os.path.exists(phase_result_file):
            try:
                with open(phase_result_file, 'r') as f:
                    phase_result = json.load(f)
                    baseline_value = phase_result.get(metric_name)
                    if baseline_value is not None:
                        # Calculate threshold based on phase budget rules
                        budget = self.phase_budgets.get(self.current_phase, {}).get(metric_name, {})
                        max_degradation = budget.get('max_degradation')
                        if max_degradation:
                            if budget.get('type') == 'lower_bound':
                                return baseline_value - max_degradation
                            else:
                                return baseline_value + max_degradation
            except Exception as e:
                logger.warning(f"Failed to load previous phase result: {e}")
        
        return None
    
    def _breaches_threshold(self, metric_name: str, value: float, threshold: float, budget: Dict) -> bool:
        """Check if value breaches threshold based on metric type."""
        metric_type = budget.get('type', 'lower_bound')  # lower_bound or upper_bound
        
        if metric_type == 'lower_bound':
            return value < threshold
        else:  # upper_bound
            return value > threshold
    
    def trigger_master_kill_switch(self, reason: str):
        """Trigger master kill switch via config update."""
        try:
            # Load current config
            with open(self.config_path, 'r') as f:
                config = json.load(f)
            
            # Initialize governance section if not exists
            if 'governance' not in config:
                config['governance'] = {}
            
            # Set master kill switch
            config['governance']['master_kill_switch'] = {
                'enabled': True,
                'reason': reason,
                'revert_to_phase': 3,
                'disable_features': [
                    'breakeven_logic',
                    'partial_profit_taking',
                    'runner_positions',
                    'improved_sl_placement',
                    'shadow_mode_filters'
                ],
                'log_level': 'CRITICAL',
                'activated_at': datetime.now().isoformat()
            }
            
            # Save config
            with open(self.config_path, 'w') as f:
                json.dump(config, f, indent=2)
            
            logger.critical(f"[AUTO_KILL_SWITCH] Activated: {reason}")
            logger.critical(f"[AUTO_KILL_SWITCH] Config updated: {self.config_path}")
            
        except Exception as e:
            logger.error(f"Failed to trigger master kill switch: {e}", exc_info=True)
    
    def clear_master_kill_switch(self, reason: str = "Auto-cleared due to insufficient data"):
        """Clear master kill switch via config update."""
        try:
            # Load current config
            with open(self.config_path, 'r') as f:
                config = json.load(f)
            
            # Initialize governance section if not exists
            if 'governance' not in config:
                config['governance'] = {}
            
            # Clear master kill switch
            config['governance']['master_kill_switch'] = {
                'enabled': False,
                'reason': reason,
                'revert_to_phase': 3,
                'disable_features': [],
                'log_level': 'INFO',
                'cleared_at': datetime.now().isoformat()
            }
            
            # Save config
            with open(self.config_path, 'w') as f:
                json.dump(config, f, indent=2)
            
            logger.info(f"[AUTO_KILL_SWITCH] Cleared: {reason}")
            logger.info(f"[AUTO_KILL_SWITCH] Config updated: {self.config_path}")
            
        except Exception as e:
            logger.error(f"Failed to clear master kill switch: {e}", exc_info=True)
    
    def save_phase_result(self, phase: int, metrics: Dict[str, float]):
        """Save phase result for use in next phase baseline."""
        phase_result_file = f'logs/live/system/phase_{phase}_result.json'
        os.makedirs(os.path.dirname(phase_result_file), exist_ok=True)
        
        try:
            with open(phase_result_file, 'w') as f:
                json.dump({
                    'phase': phase,
                    'timestamp': datetime.now().isoformat(),
                    **metrics
                }, f, indent=2)
            
            logger.info(f"[REGRESSION_GUARD] Saved Phase {phase} results: {metrics}")
        except Exception as e:
            logger.error(f"Failed to save phase result: {e}", exc_info=True)

