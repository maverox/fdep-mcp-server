"""
Configuration management for FDEP MCP Server
"""

import os
import sys
import logging
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class Config:
    """Configuration class for FDEP MCP Server"""
    
    def __init__(self):
        self.load_config()
    
    def load_config(self):
        """Load configuration from environment variables"""
        # Database configuration
        self.db_user = os.getenv("DB_USER", "postgres")
        self.db_password = os.getenv("DB_PASSWORD", "postgres")
        self.db_host = os.getenv("DB_HOST", "localhost")
        self.db_port = int(os.getenv("DB_PORT", "5432"))
        self.db_name = os.getenv("DB_NAME", "code_as_data")
        self.db_pool_size = int(os.getenv("DB_POOL_SIZE", "10"))
        self.db_max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "20"))
        self.db_pool_timeout = int(os.getenv("DB_POOL_TIMEOUT", "30"))
        self.db_pool_recycle = int(os.getenv("DB_POOL_RECYCLE", "1800"))
        
        # SSL configuration
        self.db_ssl_mode = os.getenv("DB_SSL_MODE", "prefer")
        self.db_ssl_cert = os.getenv("DB_SSL_CERT")
        self.db_ssl_key = os.getenv("DB_SSL_KEY")
        self.db_ssl_rootcert = os.getenv("DB_SSL_ROOTCERT")
        
        # FDEP data path
        self.fdep_path = os.getenv("FDEP_PATH")
        
        # Server configuration
        self.log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        self.log_file = os.getenv("LOG_FILE")
        self.dev_mode = os.getenv("DEV_MODE", "false").lower() == "true"
    
    @property
    def database_url(self) -> str:
        """Get PostgreSQL database URL"""
        return f"postgresql://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"
    
    def validate_config(self) -> tuple[bool, list[str]]:
        """Validate configuration and return (is_valid, errors)"""
        errors = []
        
        # Check FDEP path if provided
        if self.fdep_path:
            fdep_path = Path(self.fdep_path)
            if not fdep_path.exists():
                errors.append(f"FDEP_PATH does not exist: {self.fdep_path}")
            elif not fdep_path.is_dir():
                errors.append(f"FDEP_PATH is not a directory: {self.fdep_path}")
        else:
            # FDEP path is optional for basic server operation
            pass
        
        # Validate log level
        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if self.log_level not in valid_log_levels:
            errors.append(f"Invalid LOG_LEVEL: {self.log_level}. Must be one of {valid_log_levels}")
        
        # Validate database port
        if not (1 <= self.db_port <= 65535):
            errors.append(f"Invalid DB_PORT: {self.db_port}. Must be between 1 and 65535")
        
        return len(errors) == 0, errors
    
    def setup_logging(self):
        """Setup logging based on configuration"""
        # Configure log level
        log_level = getattr(logging, self.log_level, logging.INFO)
        
        # Configure logging format
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
        # Configure handler - ALWAYS use stderr for MCP protocol compliance
        if self.log_file:
            handler = logging.FileHandler(self.log_file)
        else:
            handler = logging.StreamHandler(sys.stderr)
        
        handler.setFormatter(formatter)
        
        # Configure root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)
        
        # Clear existing handlers
        for existing_handler in root_logger.handlers[:]:
            root_logger.removeHandler(existing_handler)
        
        root_logger.addHandler(handler)
    
    def get_database_config(self) -> dict:
        """Get database configuration dictionary"""
        config = {
            "url": self.database_url,
            "pool_size": self.db_pool_size,
            "max_overflow": self.db_max_overflow,
            "pool_timeout": self.db_pool_timeout,
            "pool_recycle": self.db_pool_recycle,
        }
        
        # Add SSL configuration if provided
        if self.db_ssl_cert:
            config["connect_args"] = {
                "sslmode": self.db_ssl_mode,
                "sslcert": self.db_ssl_cert,
                "sslkey": self.db_ssl_key,
                "sslrootcert": self.db_ssl_rootcert,
            }
        
        return config
    
    def __repr__(self) -> str:
        """String representation of config (hiding sensitive data)"""
        return f"Config(db_host={self.db_host}, db_port={self.db_port}, db_name={self.db_name}, fdep_path={self.fdep_path})"

# Global config instance
config = Config()