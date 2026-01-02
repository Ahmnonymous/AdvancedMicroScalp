"""
Enhanced Config Validation Utility
Provides JSON validation, checksum calculation, and corruption detection.
"""

import json
import hashlib
from typing import Dict, Any, Tuple, Optional
from utils.logger_factory import get_logger

logger = get_logger("config_validator", "logs/live/system/config_validator.log")


def calculate_config_checksum(config: Dict[str, Any]) -> str:
    """
    Calculate checksum for configuration dictionary.
    
    Args:
        config: Configuration dictionary
        
    Returns:
        MD5 checksum as hex string
    """
    # Convert config to JSON string (sorted keys for consistency)
    config_str = json.dumps(config, sort_keys=True, indent=2)
    # Calculate MD5 hash
    checksum = hashlib.md5(config_str.encode('utf-8')).hexdigest()
    return checksum


def validate_config_json(config_path: str) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    """
    Validate and load configuration JSON file.
    
    Args:
        config_path: Path to config.json file
        
    Returns:
        Tuple of (is_valid, config_dict, error_message)
        - is_valid: True if config is valid JSON
        - config_dict: Parsed config dict if valid, None otherwise
        - error_message: Error message if invalid, None otherwise
    """
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # Basic structure validation
        if not isinstance(config, dict):
            return False, None, "Config root must be a dictionary"
        
        # Validate required sections exist
        required_sections = ['mode', 'mt5', 'risk', 'trading']
        missing_sections = [s for s in required_sections if s not in config]
        if missing_sections:
            return False, None, f"Missing required sections: {', '.join(missing_sections)}"
        
        return True, config, None
        
    except json.JSONDecodeError as e:
        return False, None, f"Invalid JSON: {e}"
    except FileNotFoundError:
        return False, None, f"Config file not found: {config_path}"
    except Exception as e:
        return False, None, f"Error loading config: {e}"


def validate_master_kill_switch(config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Validate master kill switch configuration.
    
    Args:
        config: Configuration dictionary
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    governance = config.get('governance', {})
    master_kill_switch = governance.get('master_kill_switch', {})
    
    if not isinstance(master_kill_switch, dict):
        return False, "master_kill_switch must be a dictionary"
    
    # Validate enabled field
    if 'enabled' in master_kill_switch:
        if not isinstance(master_kill_switch['enabled'], bool):
            return False, "master_kill_switch.enabled must be a boolean"
    
    # Validate revert_to_phase if present
    if 'revert_to_phase' in master_kill_switch:
        if not isinstance(master_kill_switch['revert_to_phase'], int):
            return False, "master_kill_switch.revert_to_phase must be an integer"
    
    # Validate disable_features if present
    if 'disable_features' in master_kill_switch:
        if not isinstance(master_kill_switch['disable_features'], list):
            return False, "master_kill_switch.disable_features must be a list"
    
    return True, None

