#!/usr/bin/env python3
"""
MCP Server for Haskell Code Analysis Tool

This server provides MCP tools for querying Haskell code structure data
stored in PostgreSQL via the code analysis tool.
"""

import asyncio
import logging
import os
import sys
import warnings
from typing import Any, Dict, List

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from dotenv import load_dotenv

from .config import config

# Suppress warnings that might contaminate stdout (MCP protocol requirement)
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*declarative_base.*")

# Import required code analysis library components
from code_as_data.db.connection import SessionLocal
from code_as_data.services.query_service import QueryService
from code_as_data.db.models import Function, Module

# Load environment variables
load_dotenv()

# Setup logging from config
config.setup_logging()
logger = logging.getLogger(__name__)

# Initialize MCP server
mcp_server = Server("fdep-mcp-server")

class CodeAnalysisService:
    """Service for managing code analysis operations"""
    
    def __init__(self):
        self.db_session = None
        self.query_service = None
        self.dump_service = None
        self.initialized = False
        
    def initialize(self) -> bool:
        """Initialize database connection and services"""
        logger.debug("Starting code analysis service initialization...")
        try:
            logger.debug("Creating database session...")
            self.db_session = SessionLocal()
            logger.debug("Database session created successfully")
            
            logger.debug("Creating QueryService...")
            self.query_service = QueryService(self.db_session)
            logger.debug("QueryService created successfully")
            
            # Don't initialize DumpService here as it requires paths
            self.dump_service = None
            self.initialized = True
            logger.info("Code analysis service initialized successfully")
            logger.debug("Service initialization complete")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize code analysis service: {e}")
            logger.debug(f"Initialization failed, cleaning up resources...")
            self.cleanup()
            return False
    
    def cleanup(self):
        """Clean up database connections and resources"""
        try:
            if self.db_session:
                self.db_session.close()
                logger.debug("Database session closed")
        except Exception as e:
            logger.warning(f"Error closing database session: {e}")
        finally:
            self.db_session = None
            self.query_service = None
            self.dump_service = None
            self.initialized = False
    
    def recover_session(self) -> bool:
        """Recover from a corrupted database session"""
        logger.debug("Attempting to recover database session...")
        try:
            # Close existing session if it exists
            if self.db_session:
                try:
                    self.db_session.rollback()
                    self.db_session.close()
                    logger.debug("Closed existing session")
                except Exception as e:
                    logger.debug(f"Error closing existing session: {e}")
            
            # Create new session
            self.db_session = SessionLocal()
            self.query_service = QueryService(self.db_session)
            logger.debug("Database session recovered successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to recover database session: {e}")
            self.cleanup()
            return False
    
    
    def __enter__(self):
        """Context manager entry"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with cleanup"""
        self.cleanup()

# Global service instance
code_service = CodeAnalysisService()

# Validation helper functions

# Search pattern normalization helper
def normalize_search_pattern(pattern: str) -> str:
    """
    Normalize search patterns by converting LLM-style wildcards (*) to SQL wildcards (%)
    
    Examples:
    - '*card*' -> '%card%'
    - 'card*' -> 'card%'  
    - '*card' -> '%card'
    - 'card' -> 'card' (will get %card% added by existing logic)
    """
    if not pattern:
        return pattern
    
    # Convert * wildcards to % wildcards for SQL LIKE
    normalized = pattern.replace('*', '%')
    
    return normalized

def build_like_pattern(pattern: str) -> str:
    """
    Build a SQL LIKE pattern from user input, handling both wildcard and non-wildcard cases
    
    If pattern already contains wildcards (%), use as-is
    Otherwise, wrap with % for contains matching
    """
    normalized = normalize_search_pattern(pattern)
    
    # If the normalized pattern already has wildcards, use it as-is
    if '%' in normalized:
        return normalized
    
    # Otherwise, wrap with % for contains matching
    return f"%{normalized}%"

@mcp_server.list_tools()
async def list_tools() -> List[types.Tool]:
    """List available MCP tools"""
    return [
        types.Tool(
            name="list_modules",
            description="Get list of all modules in the database",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of modules to return",
                        "default": 100
                    }
                },
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="get_function_details",
            description="Get detailed information about a specific function",
            inputSchema={
                "type": "object",
                "properties": {
                    "function_name": {
                        "type": "string",
                        "description": "Name of the function"
                    },
                    "module_name": {
                        "type": "string",
                        "description": "Module containing the function (optional)"
                    }
                },
                "required": ["function_name"],
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="search_functions",
            description="Search for functions by name pattern",
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Search pattern (supports wildcards)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results",
                        "default": 50
                    }
                },
                "required": ["pattern"],
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="get_most_called_functions",
            description="Get the most frequently called functions",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of functions to return",
                        "default": 20
                    }
                },
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="execute_query",
            description="Execute a basic SQL query on the code database",
            inputSchema={
                "type": "object",
                "properties": {
                    "query_type": {
                        "type": "string",
                        "enum": ["modules", "functions", "types", "imports"],
                        "description": "Type of query to execute"
                    },
                    "filters": {
                        "type": "object",
                        "description": "Filters to apply to the query",
                        "properties": {
                            "name_pattern": {"type": "string"},
                            "module_id": {"type": "integer"},
                            "limit": {"type": "integer", "default": 100}
                        }
                    }
                },
                "required": ["query_type"],
                "additionalProperties": False
            }
        ),
        # Phase 1: Module Enhancement Tools
        types.Tool(
            name="get_module_details",
            description="Get detailed information about a specific module including function counts and statistics",
            inputSchema={
                "type": "object",
                "properties": {
                    "module_name": {
                        "type": "string",
                        "description": "Name of the module"
                    }
                },
                "required": ["module_name"],
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="get_functions_by_module",
            description="Get all functions defined in a specific module",
            inputSchema={
                "type": "object",
                "properties": {
                    "module_name": {
                        "type": "string",
                        "description": "Name of the module"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of functions to return",
                        "default": 100
                    },
                    "include_signatures": {
                        "type": "boolean",
                        "description": "Include function signatures in output",
                        "default": False
                    }
                },
                "required": ["module_name"],
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="search_modules",
            description="Search for modules by name pattern",
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Search pattern (supports wildcards)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results",
                        "default": 50
                    }
                },
                "required": ["pattern"],
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="get_module_dependencies",
            description="Analyze module dependencies and imports",
            inputSchema={
                "type": "object",
                "properties": {
                    "module_name": {
                        "type": "string",
                        "description": "Name of the module"
                    },
                    "include_imports": {
                        "type": "boolean",
                        "description": "Include detailed import information",
                        "default": True
                    },
                    "include_dependents": {
                        "type": "boolean",
                        "description": "Include modules that depend on this module",
                        "default": False
                    }
                },
                "required": ["module_name"],
                "additionalProperties": False
            }
        ),
        # Phase 1: Function Analysis Enhancement Tools  
        types.Tool(
            name="get_function_call_graph",
            description="Get function call hierarchy showing what functions this function calls and what calls it",
            inputSchema={
                "type": "object",
                "properties": {
                    "function_name": {
                        "type": "string",
                        "description": "Name of the function"
                    },
                    "module_name": {
                        "type": "string", 
                        "description": "Module containing the function (optional but recommended)"
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Maximum depth to traverse (default: 2)",
                        "default": 2
                    },
                    "include_callers": {
                        "type": "boolean",
                        "description": "Include functions that call this function",
                        "default": True
                    },
                    "include_callees": {
                        "type": "boolean", 
                        "description": "Include functions called by this function",
                        "default": True
                    }
                },
                "required": ["function_name"],
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="get_function_callers",
            description="Get all functions that call a specific function",
            inputSchema={
                "type": "object",
                "properties": {
                    "function_name": {
                        "type": "string",
                        "description": "Name of the function"
                    },
                    "module_name": {
                        "type": "string",
                        "description": "Module containing the function (optional)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of callers to return",
                        "default": 50
                    }
                },
                "required": ["function_name"],
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="get_function_callees",
            description="Get all functions called by a specific function",
            inputSchema={
                "type": "object",
                "properties": {
                    "function_name": {
                        "type": "string",
                        "description": "Name of the function"
                    },
                    "module_name": {
                        "type": "string",
                        "description": "Module containing the function (optional)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of callees to return",
                        "default": 50
                    }
                },
                "required": ["function_name"],
                "additionalProperties": False
            }
        ),
        # Phase 1: Advanced Query Capabilities Tools
        types.Tool(
            name="execute_advanced_query",
            description="Execute complex JSON-based queries with joins and advanced conditions",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "object",
                        "description": "JSON query with type, conditions, and optional joins",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["function", "module", "type", "class", "import", "instance"],
                                "description": "Entity type to query"
                            },
                            "conditions": {
                                "type": "array",
                                "description": "Array of condition objects",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "field": {"type": "string"},
                                        "operator": {"type": "string", "enum": ["eq", "ne", "gt", "lt", "ge", "le", "like", "ilike", "contains", "startswith", "endswith", "in", "not_in", "between", "is_null"]},
                                        "value": {"type": ["string", "number", "boolean", "null"]}
                                    }
                                }
                            },
                            "limit": {"type": "integer", "default": 100}
                        },
                        "required": ["type"]
                    }
                },
                "required": ["query"],
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="find_cross_module_calls",
            description="Find function calls that cross module boundaries",
            inputSchema={
                "type": "object",
                "properties": {
                    "source_module": {
                        "type": "string",
                        "description": "Source module pattern (optional)"
                    },
                    "target_module": {
                        "type": "string", 
                        "description": "Target module pattern (optional)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results",
                        "default": 100
                    }
                },
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="analyze_function_complexity",
            description="Analyze function complexity metrics including call count and signature complexity",
            inputSchema={
                "type": "object",
                "properties": {
                    "module_name": {
                        "type": "string",
                        "description": "Module to analyze (optional, analyzes all if not specified)"
                    },
                    "min_complexity": {
                        "type": "integer",
                        "description": "Minimum complexity threshold",
                        "default": 5
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results",
                        "default": 50
                    }
                },
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="get_code_statistics", 
            description="Get comprehensive statistics about the codebase",
            inputSchema={
                "type": "object",
                "properties": {
                    "include_details": {
                        "type": "boolean",
                        "description": "Include detailed breakdowns",
                        "default": False
                    }
                },
                "additionalProperties": False
            }
        ),
        # Phase 2: Type System Analysis Tools
        types.Tool(
            name="list_types",
            description="Get types by module or pattern with support for different type categories",
            inputSchema={
                "type": "object",
                "properties": {
                    "module_name": {
                        "type": "string",
                        "description": "Module to search in (optional)"
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Type name pattern to match (optional)"
                    },
                    "type_category": {
                        "type": "string",
                        "enum": ["DATA", "SUMTYPE", "TYPE", "NEWTYPE", "CLASS", "INSTANCE"],
                        "description": "Filter by type category (optional)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results",
                        "default": 100
                    }
                },
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="get_type_details",
            description="Get detailed information about a specific type including constructors and fields",
            inputSchema={
                "type": "object",
                "properties": {
                    "type_name": {
                        "type": "string",
                        "description": "Name of the type"
                    },
                    "module_name": {
                        "type": "string",
                        "description": "Module containing the type (optional)"
                    },
                    "include_constructors": {
                        "type": "boolean",
                        "description": "Include constructor details",
                        "default": True
                    },
                    "include_fields": {
                        "type": "boolean",
                        "description": "Include field details for constructors",
                        "default": True
                    }
                },
                "required": ["type_name"],
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="search_types",
            description="Search for types by name pattern with advanced filtering",
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Search pattern for type names"
                    },
                    "module_pattern": {
                        "type": "string",
                        "description": "Module name pattern to filter by (optional)"
                    },
                    "type_category": {
                        "type": "string",
                        "enum": ["DATA", "SUMTYPE", "TYPE", "NEWTYPE", "CLASS", "INSTANCE"],
                        "description": "Filter by type category (optional)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results",
                        "default": 50
                    }
                },
                "required": ["pattern"],
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="get_type_dependencies",
            description="Analyze type dependencies and relationships",
            inputSchema={
                "type": "object",
                "properties": {
                    "type_name": {
                        "type": "string",
                        "description": "Name of the type"
                    },
                    "module_name": {
                        "type": "string",
                        "description": "Module containing the type (optional)"
                    },
                    "include_dependents": {
                        "type": "boolean",
                        "description": "Include types that depend on this type",
                        "default": False
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Maximum dependency depth to traverse",
                        "default": 2
                    }
                },
                "required": ["type_name"],
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="analyze_type_usage",
            description="Analyze how types are used throughout the codebase",
            inputSchema={
                "type": "object",
                "properties": {
                    "type_name": {
                        "type": "string",
                        "description": "Name of the type to analyze (optional)"
                    },
                    "module_name": {
                        "type": "string",
                        "description": "Module to analyze (optional, analyzes all if not specified)"
                    },
                    "usage_threshold": {
                        "type": "integer",
                        "description": "Minimum usage count to include in results",
                        "default": 1
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results",
                        "default": 50
                    }
                },
                "additionalProperties": False
            }
        ),
        # Phase 2: Class Analysis Tools
        types.Tool(
            name="list_classes",
            description="Get class definitions with filtering by module or pattern",
            inputSchema={
                "type": "object",
                "properties": {
                    "module_name": {
                        "type": "string",
                        "description": "Module to search in (optional)"
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Class name pattern to match (optional)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results",
                        "default": 100
                    }
                },
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="get_class_details",
            description="Get detailed information about a specific class including methods and instances",
            inputSchema={
                "type": "object",
                "properties": {
                    "class_name": {
                        "type": "string",
                        "description": "Name of the class"
                    },
                    "module_name": {
                        "type": "string",
                        "description": "Module containing the class (optional)"
                    },
                    "include_instances": {
                        "type": "boolean",
                        "description": "Include class instances",
                        "default": True
                    }
                },
                "required": ["class_name"],
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="search_classes",
            description="Search for classes by name pattern with module filtering",
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Search pattern for class names"
                    },
                    "module_pattern": {
                        "type": "string",
                        "description": "Module name pattern to filter by (optional)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results",
                        "default": 50
                    }
                },
                "required": ["pattern"],
                "additionalProperties": False
            }
        ),
        # Phase 2: Import Analysis Tools
        types.Tool(
            name="analyze_imports",
            description="Analyze import patterns and dependencies for modules",
            inputSchema={
                "type": "object",
                "properties": {
                    "module_name": {
                        "type": "string",
                        "description": "Module to analyze imports for (optional)"
                    },
                    "import_pattern": {
                        "type": "string",
                        "description": "Pattern to match imported modules (optional)"
                    },
                    "include_qualified": {
                        "type": "boolean",
                        "description": "Include qualified imports information",
                        "default": True
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results",
                        "default": 100
                    }
                },
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="get_import_graph",
            description="Generate module import relationship graph",
            inputSchema={
                "type": "object",
                "properties": {
                    "root_module": {
                        "type": "string",
                        "description": "Root module to start graph from (optional)"
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Maximum depth to traverse",
                        "default": 3
                    },
                    "include_external": {
                        "type": "boolean",
                        "description": "Include external package imports",
                        "default": False
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of nodes in graph",
                        "default": 50
                    }
                },
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="find_unused_imports",
            description="Find potentially unused imports in modules",
            inputSchema={
                "type": "object",
                "properties": {
                    "module_name": {
                        "type": "string",
                        "description": "Module to analyze (optional, analyzes all if not specified)"
                    },
                    "package_pattern": {
                        "type": "string",
                        "description": "Package pattern to focus on (optional)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results",
                        "default": 100
                    }
                },
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="get_import_details",
            description="Get detailed information about imports in a module",
            inputSchema={
                "type": "object",
                "properties": {
                    "module_name": {
                        "type": "string",
                        "description": "Module to get import details for"
                    },
                    "include_source_info": {
                        "type": "boolean",
                        "description": "Include source location and other metadata",
                        "default": True
                    }
                },
                "required": ["module_name"],
                "additionalProperties": False
            }
        ),
        # Phase 1: Advanced Pattern Analysis Tools
        types.Tool(
            name="find_similar_functions",
            description="Find functions similar to a given function based on signature and code",
            inputSchema={
                "type": "object",
                "properties": {
                    "function_name": {
                        "type": "string",
                        "description": "Name of the reference function"
                    },
                    "module_name": {
                        "type": "string",
                        "description": "Module containing the reference function (optional)"
                    },
                    "similarity_threshold": {
                        "type": "number",
                        "description": "Similarity threshold (0.0 to 1.0)",
                        "default": 0.7,
                        "minimum": 0.0,
                        "maximum": 1.0
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of similar functions to return",
                        "default": 10
                    }
                },
                "required": ["function_name"],
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="find_code_patterns",
            description="Find recurring code patterns across functions",
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern_code": {
                        "type": "string",
                        "description": "Code snippet pattern to search for"
                    },
                    "min_matches": {
                        "type": "integer",
                        "description": "Minimum number of lines that must match",
                        "default": 3
                    },
                    "module_pattern": {
                        "type": "string",
                        "description": "Module name pattern to filter search (optional)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of functions to return",
                        "default": 20
                    }
                },
                "required": ["pattern_code"],
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="group_similar_functions",
            description="Group functions by similarity to identify common patterns",
            inputSchema={
                "type": "object",
                "properties": {
                    "similarity_threshold": {
                        "type": "number",
                        "description": "Minimum similarity score to group functions",
                        "default": 0.7,
                        "minimum": 0.0,
                        "maximum": 1.0
                    },
                    "module_pattern": {
                        "type": "string",
                        "description": "Module name pattern to filter analysis (optional)"
                    },
                    "min_group_size": {
                        "type": "integer",
                        "description": "Minimum number of functions in a group",
                        "default": 2
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of groups to return",
                        "default": 10
                    }
                },
                "additionalProperties": False
            }
        ),
        # Phase 1: Advanced Type Analysis Tools
        types.Tool(
            name="build_type_dependency_graph",
            description="Build a comprehensive type dependency graph showing relationships between types",
            inputSchema={
                "type": "object",
                "properties": {
                    "root_type": {
                        "type": "string",
                        "description": "Root type to start the graph from (optional)"
                    },
                    "module_pattern": {
                        "type": "string",
                        "description": "Module pattern to filter types (optional)"
                    },
                    "include_external": {
                        "type": "boolean",
                        "description": "Include external type dependencies",
                        "default": False
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Maximum depth to traverse",
                        "default": 3
                    }
                },
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="get_nested_types",
            description="Get all nested type definitions for specified types",
            inputSchema={
                "type": "object",
                "properties": {
                    "type_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of root type names to analyze"
                    },
                    "gateway_name": {
                        "type": "string",
                        "description": "Gateway name to filter by"
                    },
                    "exclude_pattern": {
                        "type": "string",
                        "description": "Pattern to exclude from results (optional)"
                    },
                    "include_raw_definitions": {
                        "type": "boolean",
                        "description": "Include raw type definitions",
                        "default": True
                    }
                },
                "required": ["type_names", "gateway_name"],
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="analyze_type_relationships",
            description="Analyze deep type relationships and dependencies",
            inputSchema={
                "type": "object",
                "properties": {
                    "type_name": {
                        "type": "string",
                        "description": "Type name to analyze"
                    },
                    "source_module": {
                        "type": "string",
                        "description": "Source module containing the type"
                    },
                    "analysis_depth": {
                        "type": "integer",
                        "description": "Depth of relationship analysis",
                        "default": 2
                    },
                    "include_dependents": {
                        "type": "boolean",
                        "description": "Include types that depend on this type",
                        "default": True
                    },
                    "module_filter": {
                        "type": "string",
                        "description": "Module pattern to filter results (optional)"
                    }
                },
                "required": ["type_name", "source_module"],
                "additionalProperties": False
            }
        ),
        # Phase 1: Source Location Tools
        types.Tool(
            name="find_element_by_location",
            description="Find code elements (functions, types, classes, imports) by source location",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the source file"
                    },
                    "line_number": {
                        "type": "integer",
                        "description": "Line number in the file"
                    },
                    "base_directory": {
                        "type": "string",
                        "description": "Base directory path (optional)"
                    },
                    "element_types": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["function", "type", "class", "import", "all"]
                        },
                        "description": "Types of elements to search for",
                        "default": ["all"]
                    }
                },
                "required": ["file_path", "line_number"],
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="get_location_context",
            description="Get comprehensive context around a source location",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the source file"
                    },
                    "line_number": {
                        "type": "integer",
                        "description": "Line number in the file"
                    },
                    "context_radius": {
                        "type": "integer",
                        "description": "Number of lines around the location to include",
                        "default": 5
                    },
                    "include_dependencies": {
                        "type": "boolean",
                        "description": "Include function/type dependencies",
                        "default": True
                    }
                },
                "required": ["file_path", "line_number"],
                "additionalProperties": False
            }
        ),
        # Phase 1: Function Context Tools
        types.Tool(
            name="get_function_context",
            description="Get complete context for a function including all used types and functions",
            inputSchema={
                "type": "object",
                "properties": {
                    "function_name": {
                        "type": "string",
                        "description": "Name of the function"
                    },
                    "module_name": {
                        "type": "string",
                        "description": "Module containing the function (optional)"
                    },
                    "include_prompts": {
                        "type": "boolean",
                        "description": "Include formatted prompts for types and functions",
                        "default": True
                    },
                    "include_local_definitions": {
                        "type": "boolean",
                        "description": "Include local type and function definitions",
                        "default": True
                    },
                    "include_external_references": {
                        "type": "boolean",
                        "description": "Include external type and function references",
                        "default": True
                    }
                },
                "required": ["function_name"],
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="generate_function_imports",
            description="Generate all necessary import statements for a function or code element",
            inputSchema={
                "type": "object",
                "properties": {
                    "element_name": {
                        "type": "string",
                        "description": "Name of the element to generate imports for"
                    },
                    "source_module": {
                        "type": "string",
                        "description": "Module where the element is used"
                    },
                    "element_type": {
                        "type": "string",
                        "enum": ["function", "type", "class", "any"],
                        "description": "Type of the element",
                        "default": "any"
                    },
                    "import_style": {
                        "type": "string",
                        "enum": ["haskell", "qualified", "explicit"],
                        "description": "Style of import statements to generate",
                        "default": "haskell"
                    }
                },
                "required": ["element_name", "source_module"],
                "additionalProperties": False
            }
        ),
        # Phase 2: Enhanced Query Capabilities
        types.Tool(
            name="execute_custom_query",
            description="Execute custom SQL queries on the code database with parameters",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "SQL query to execute (use ? for parameters)"
                    },
                    "parameters": {
                        "type": "object",
                        "description": "Parameters for the query (optional)",
                        "additionalProperties": True
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results to return",
                        "default": 100
                    }
                },
                "required": ["query"],
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="pattern_match_code",
            description="Advanced pattern matching to find code structures",
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern_type": {
                        "type": "string",
                        "enum": ["function_call", "type_usage", "code_structure"],
                        "description": "Type of pattern to match"
                    },
                    "pattern_config": {
                        "type": "object",
                        "description": "Pattern configuration based on pattern_type",
                        "properties": {
                            "caller": {
                                "type": "string",
                                "description": "Caller function name pattern (for function_call)"
                            },
                            "callee": {
                                "type": "string",
                                "description": "Callee function name pattern (for function_call)"
                            },
                            "mode": {
                                "type": "string",
                                "enum": ["calls", "called_by"],
                                "description": "Direction of function calls (for function_call)"
                            },
                            "type_name": {
                                "type": "string",
                                "description": "Type name to search for (for type_usage)"
                            },
                            "usage_in": {
                                "type": "string",
                                "enum": ["function", "type", "class"],
                                "description": "Where to look for type usage (for type_usage)"
                            },
                            "structure_type": {
                                "type": "string",
                                "enum": ["nested_function", "higher_order", "pattern_match"],
                                "description": "Type of code structure (for code_structure)"
                            }
                        }
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results",
                        "default": 50
                    }
                },
                "required": ["pattern_type", "pattern_config"],
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="analyze_cross_module_dependencies",
            description="Comprehensive analysis of cross-module dependencies and coupling",
            inputSchema={
                "type": "object",
                "properties": {
                    "analysis_type": {
                        "type": "string",
                        "enum": ["dependencies", "coupling", "complexity"],
                        "description": "Type of analysis to perform",
                        "default": "dependencies"
                    },
                    "module_pattern": {
                        "type": "string",
                        "description": "Module name pattern to filter analysis (optional)"
                    },
                    "include_metrics": {
                        "type": "boolean",
                        "description": "Include detailed coupling metrics",
                        "default": True
                    },
                    "threshold": {
                        "type": "integer",
                        "description": "Minimum dependency count to include",
                        "default": 1
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results",
                        "default": 50
                    }
                },
                "additionalProperties": False
            }
        ),
        types.Tool(
            name="enhanced_function_call_graph",
            description="Generate enhanced function call graphs with advanced options",
            inputSchema={
                "type": "object",
                "properties": {
                    "function_name": {
                        "type": "string",
                        "description": "Name of the function"
                    },
                    "module_name": {
                        "type": "string",
                        "description": "Module containing the function (optional)"
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Maximum depth to traverse",
                        "default": 3
                    },
                    "graph_format": {
                        "type": "string",
                        "enum": ["tree", "graph", "flat"],
                        "description": "Format of the output graph",
                        "default": "tree"
                    },
                    "include_signatures": {
                        "type": "boolean",
                        "description": "Include function signatures in output",
                        "default": False
                    },
                    "filter_modules": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Module patterns to include (optional)"
                    }
                },
                "required": ["function_name"],
                "additionalProperties": False
            }
        )
    ]

@mcp_server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle tool calls"""
    logger.debug(f"Tool call received: {name}")
    logger.debug(f"Tool arguments: {arguments}")
    
    if name == "list_modules":
        return await handle_list_modules(arguments)
    elif name == "get_function_details":
        return await handle_get_function_details(arguments)
    elif name == "search_functions":
        return await handle_search_functions(arguments)
    elif name == "get_most_called_functions":
        return await handle_get_most_called_functions(arguments)
    elif name == "execute_query":
        return await handle_execute_query(arguments)
    # Phase 1: Module Enhancement Tools
    elif name == "get_module_details":
        return await handle_get_module_details(arguments)
    elif name == "get_functions_by_module":
        return await handle_get_functions_by_module(arguments)
    elif name == "search_modules":
        return await handle_search_modules(arguments)
    elif name == "get_module_dependencies":
        return await handle_get_module_dependencies(arguments)
    # Phase 1: Function Analysis Enhancement Tools
    elif name == "get_function_call_graph":
        return await handle_get_function_call_graph(arguments)
    elif name == "get_function_callers":
        return await handle_get_function_callers(arguments)
    elif name == "get_function_callees":
        return await handle_get_function_callees(arguments)
    # Phase 1: Advanced Query Capabilities Tools
    elif name == "execute_advanced_query":
        return await handle_execute_advanced_query(arguments)
    elif name == "find_cross_module_calls":
        return await handle_find_cross_module_calls(arguments)
    elif name == "analyze_function_complexity":
        return await handle_analyze_function_complexity(arguments)
    elif name == "get_code_statistics":
        return await handle_get_code_statistics(arguments)
    # Phase 2: Type System Analysis Tools
    elif name == "list_types":
        return await handle_list_types(arguments)
    elif name == "get_type_details":
        return await handle_get_type_details(arguments)
    elif name == "search_types":
        return await handle_search_types(arguments)
    elif name == "get_type_dependencies":
        return await handle_get_type_dependencies(arguments)
    elif name == "analyze_type_usage":
        return await handle_analyze_type_usage(arguments)
    # Phase 2: Class Analysis Tools
    elif name == "list_classes":
        return await handle_list_classes(arguments)
    elif name == "get_class_details":
        return await handle_get_class_details(arguments)
    elif name == "search_classes":
        return await handle_search_classes(arguments)
    # Phase 2: Import Analysis Tools
    elif name == "analyze_imports":
        return await handle_analyze_imports(arguments)
    elif name == "get_import_graph":
        return await handle_get_import_graph(arguments)
    elif name == "find_unused_imports":
        return await handle_find_unused_imports(arguments)
    elif name == "get_import_details":
        return await handle_get_import_details(arguments)
    # Phase 1: Advanced Pattern Analysis Tools
    elif name == "find_similar_functions":
        return await handle_find_similar_functions(arguments)
    elif name == "find_code_patterns":
        return await handle_find_code_patterns(arguments)
    elif name == "group_similar_functions":
        return await handle_group_similar_functions(arguments)
    # Phase 1: Advanced Type Analysis Tools
    elif name == "build_type_dependency_graph":
        return await handle_build_type_dependency_graph(arguments)
    elif name == "get_nested_types":
        return await handle_get_nested_types(arguments)
    elif name == "analyze_type_relationships":
        return await handle_analyze_type_relationships(arguments)
    # Phase 1: Source Location Tools
    elif name == "find_element_by_location":
        return await handle_find_element_by_location(arguments)
    elif name == "get_location_context":
        return await handle_get_location_context(arguments)
    # Phase 1: Function Context Tools
    elif name == "get_function_context":
        return await handle_get_function_context(arguments)
    elif name == "generate_function_imports":
        return await handle_generate_function_imports(arguments)
    # Phase 2: Enhanced Query Capabilities Tools
    elif name == "execute_custom_query":
        return await handle_execute_custom_query(arguments)
    elif name == "pattern_match_code":
        return await handle_pattern_match_code(arguments)
    elif name == "analyze_cross_module_dependencies":
        return await handle_analyze_cross_module_dependencies(arguments)
    elif name == "enhanced_function_call_graph":
        return await handle_enhanced_function_call_graph(arguments)
    else:
        raise ValueError(f"Unknown tool: {name}")


async def handle_list_modules(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """List all modules"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    limit = arguments.get("limit", 100)
    
    try:
        modules = code_service.query_service.get_all_modules()
        modules = modules[:limit]  # Apply limit
        
        result = f"Found {len(modules)} modules:\n\n"
        for module in modules:
            result += f"- {module.name}\n"
        
        return [types.TextContent(type="text", text=result)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error listing modules: {e}"
        )]

async def handle_get_function_details(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Get function details"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    function_name = arguments["function_name"]
    module_name = arguments.get("module_name")
    
    try:
        # Get module if specified
        module_id = None
        if module_name:
            module = code_service.query_service.get_module_by_name(module_name)
            if not module:
                return [types.TextContent(
                    type="text",
                    text=f"Module not found: {module_name}"
                )]
            module_id = module.id
        
        # Get functions
        functions = code_service.query_service.get_function_by_name(function_name, module_id)
        
        if not functions:
            return [types.TextContent(
                type="text",
                text=f"Function not found: {function_name}"
            )]
        
        result = f"Found {len(functions)} function(s) named '{function_name}':\n\n"
        
        for func in functions:
            result += f"Function: {func.name}\n"
            result += f"Module: {func.module.name if func.module else 'Unknown'}\n"
            result += f"Signature: {func.function_signature or 'No signature'}\n"
            result += f"Source Location: {func.src_loc or 'Unknown'}\n"
            result += f"Type: {func.type_enum or 'Unknown'}\n"
            result += "---\n"
        
        return [types.TextContent(type="text", text=result)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error getting function details: {e}"
        )]

async def handle_search_functions(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Search functions by pattern"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    pattern = arguments["pattern"]
    limit = arguments.get("limit", 50)
    
    try:
        # Use efficient database query instead of N+1 pattern
        if not Function:
            return [types.TextContent(
                type="text",
                text="Function model not available - code_as_data library not loaded"
            )]
        
        like_pattern = build_like_pattern(pattern)
        results = (code_service.db_session.query(Function)
                  .filter(Function.name.like(like_pattern))
                  .limit(limit)
                  .all())
        
        if not results:
            return [types.TextContent(
                type="text",
                text=f"No functions found matching pattern: {pattern}"
            )]
        
        result_text = f"Found {len(results)} functions matching '{pattern}':\n\n"
        
        for func in results:
            result_text += f"- {func.name}"
            if hasattr(func, 'module') and func.module:
                result_text += f" (in {func.module.name})"
            result_text += "\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error searching functions: {e}"
        )]

async def handle_get_most_called_functions(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Get most called functions"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    limit = arguments.get("limit", 20)
    
    try:
        functions = code_service.query_service.get_most_called_functions(limit=limit)
        
        if not functions:
            return [types.TextContent(
                type="text",
                text="No function call data found"
            )]
        
        result = f"Top {len(functions)} most called functions:\n\n"
        
        # Handle dictionary return format
        for i, func_data in enumerate(functions, 1):
            if isinstance(func_data, dict):
                name = func_data.get('name', 'Unknown')
                module = func_data.get('module', 'Unknown')
                calls = func_data.get('calls', 0)
                result += f"{i}. {name} - {calls} calls (in {module})\n"
            else:
                result += f"{i}. {func_data}\n"
        
        return [types.TextContent(type="text", text=result)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error getting most called functions: {e}"
        )]

async def handle_execute_query(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Execute basic queries"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    query_type = arguments["query_type"]
    filters = arguments.get("filters", {})
    
    try:
        if query_type == "modules":
            modules = code_service.query_service.get_all_modules()
            result = f"Found {len(modules)} modules"
            
        elif query_type == "functions":
            if not Function:
                return [types.TextContent(
                    type="text",
                    text="Function model not available - code_as_data library not loaded"
                )]
            
            query = code_service.db_session.query(Function)
            
            if filters.get("name_pattern"):
                name_pattern = build_like_pattern(filters['name_pattern'])
                query = query.filter(Function.name.like(name_pattern))
            if filters.get("module_id"):
                query = query.filter(Function.module_id == filters["module_id"])
            
            functions = query.limit(filters.get("limit", 100)).all()
            result = f"Found {len(functions)} functions"
            
        else:
            result = f"Query type '{query_type}' not yet implemented"
        
        return [types.TextContent(type="text", text=result)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error executing query: {e}"
        )]

# Phase 1: Module Enhancement Tool Handlers

async def handle_get_module_details(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Get detailed information about a specific module"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    module_name = arguments["module_name"]
    
    try:
        # Get module
        module = code_service.query_service.get_module_by_name(module_name)
        if not module:
            return [types.TextContent(
                type="text",
                text=f"Module not found: {module_name}"
            )]
        
        # Get functions in module
        functions = code_service.query_service.get_functions_by_module(module.id)
        
        # Get other statistics
        function_count = len(functions) if functions else 0
        
        # Try to get imports and other data if available
        import_count = 0
        type_count = 0
        class_count = 0
        
        try:
            # Use QueryService methods for module statistics (if available)
            # For now, we'll focus on functions which we have QueryService support for
            # Additional statistics can be added when more QueryService methods are available
            pass
        except:
            pass  # If models not available, skip counts
        
        result = f"Module Details: {module.name}\n\n"
        result += f"Path: {module.path or 'Unknown'}\n"
        result += f"Functions: {function_count}\n"
        if import_count > 0:
            result += f"Imports: {import_count}\n"
        if type_count > 0:
            result += f"Types: {type_count}\n"
        if class_count > 0:
            result += f"Classes: {class_count}\n"
        
        if functions and len(functions) <= 10:
            result += f"\nSample Functions:\n"
            for func in functions[:10]:
                result += f"- {func.name}\n"
        elif functions:
            result += f"\nFirst 10 Functions:\n"
            for func in functions[:10]:
                result += f"- {func.name}\n"
            result += f"... and {len(functions) - 10} more\n"
        
        return [types.TextContent(type="text", text=result)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error getting module details: {e}"
        )]

async def handle_get_functions_by_module(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Get all functions defined in a specific module"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    module_name = arguments["module_name"]
    limit = arguments.get("limit", 100)
    include_signatures = arguments.get("include_signatures", False)
    
    try:
        # Get module
        module = code_service.query_service.get_module_by_name(module_name)
        if not module:
            return [types.TextContent(
                type="text",
                text=f"Module not found: {module_name}"
            )]
        
        # Get functions
        functions = code_service.query_service.get_functions_by_module(module.id)
        
        if not functions:
            return [types.TextContent(
                type="text",
                text=f"No functions found in module: {module_name}"
            )]
        
        # Apply limit
        functions = functions[:limit]
        
        result = f"Functions in module '{module_name}' ({len(functions)} shown):\n\n"
        
        for func in functions:
            result += f"- {func.name}"
            if include_signatures and func.function_signature:
                result += f" :: {func.function_signature}"
            if func.src_loc:
                result += f" (at {func.src_loc})"
            result += "\n"
        
        return [types.TextContent(type="text", text=result)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error getting functions by module: {e}"
        )]

async def handle_search_modules(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Search for modules by name pattern"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    pattern = arguments["pattern"]
    limit = arguments.get("limit", 50)
    
    try:
        # Use efficient database query instead of loading all modules
        if not Module:
            return [types.TextContent(
                type="text",
                text="Module model not available - code_as_data library not loaded"
            )]
        
        like_pattern = build_like_pattern(pattern)
        results = (code_service.db_session.query(Module)
                  .filter(Module.name.like(like_pattern))
                  .limit(limit)
                  .all())
        
        if not results:
            return [types.TextContent(
                type="text",
                text=f"No modules found matching pattern: {pattern}"
            )]
        
        result_text = f"Found {len(results)} modules matching '{pattern}':\n\n"
        
        for module in results:
            result_text += f"- {module.name}"
            if module.path:
                result_text += f" (path: {module.path})"
            result_text += "\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error searching modules: {e}"
        )]

async def handle_get_module_dependencies(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Analyze module dependencies and imports"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    module_name = arguments["module_name"]
    include_imports = arguments.get("include_imports", True)
    include_dependents = arguments.get("include_dependents", False)
    
    try:
        # Get module
        module = code_service.query_service.get_module_by_name(module_name)
        if not module:
            return [types.TextContent(
                type="text",
                text=f"Module not found: {module_name}"
            )]
        
        result = f"Module Dependencies: {module_name}\n\n"
        
        if include_imports:
            try:
                from code_as_data.db.models import Import
                if Import:
                    imports = code_service.db_session.query(Import).filter(Import.module_id == module.id).all()
                    
                    if imports:
                        result += f"Imports ({len(imports)}):\n"
                        for imp in imports:
                            result += f"- {imp.module_name}"
                            if imp.package_name:
                                result += f" (from {imp.package_name})"
                            if imp.qualified_style:
                                result += f" (qualified)"
                            if imp.as_module_name:
                                result += f" as {imp.as_module_name}"
                            result += "\n"
                        result += "\n"
                    else:
                        result += "No imports found.\n\n"
                else:
                    result += "Import data not available.\n\n"
            except Exception as e:
                result += f"Error getting imports: {e}\n\n"
        
        if include_dependents:
            try:
                from code_as_data.db.models import Import
                if Import:
                    # Find modules that import this module
                    dependents = (code_service.db_session.query(Import)
                                .filter(Import.module_name == module_name)
                                .join(Module, Import.module_id == Module.id)
                                .all())
                    
                    if dependents:
                        result += f"Dependent Modules ({len(dependents)}):\n"
                        for dep in dependents:
                            result += f"- {dep.module.name}\n"
                        result += "\n"
                    else:
                        result += "No dependent modules found.\n\n"
                else:
                    result += "Dependency data not available.\n\n"
            except Exception as e:
                result += f"Error getting dependents: {e}\n\n"
        
        return [types.TextContent(type="text", text=result)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error analyzing module dependencies: {e}"
        )]

# Phase 1: Function Analysis Enhancement Tool Handlers

async def handle_get_function_call_graph(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Get function call hierarchy"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    function_name = arguments["function_name"]
    module_name = arguments.get("module_name")
    depth = arguments.get("depth", 2)
    include_callers = arguments.get("include_callers", True)
    include_callees = arguments.get("include_callees", True)
    
    try:
        # Get the target function
        module_id = None
        if module_name:
            module = code_service.query_service.get_module_by_name(module_name)
            if not module:
                return [types.TextContent(
                    type="text",
                    text=f"Module not found: {module_name}"
                )]
            module_id = module.id
        
        functions = code_service.query_service.get_function_by_name(function_name, module_id)
        if not functions:
            return [types.TextContent(
                type="text",
                text=f"Function not found: {function_name}"
            )]
        
        # Use the first matching function
        target_function = functions[0]
        
        result = f"Call Graph for '{target_function.name}'"
        if target_function.module:
            result += f" (in {target_function.module.name})"
        result += "\n\n"
        
        if include_callers:
            # Get functions that call this function
            try:
                from code_as_data.db.models import FunctionCalled
                if FunctionCalled:
                    callers = (code_service.db_session.query(FunctionCalled)
                             .filter(FunctionCalled.name == target_function.name)
                             .join(Function, FunctionCalled.function_id == Function.id)
                             .limit(20)
                             .all())
                    
                    if callers:
                        result += f"Called by ({len(callers)} functions):\n"
                        for caller in callers:
                            if hasattr(caller, 'function') and caller.function:
                                result += f"  ← {caller.function.name}"
                                if caller.function.module:
                                    result += f" (in {caller.function.module.name})"
                                result += "\n"
                        result += "\n"
                    else:
                        result += "No callers found.\n\n"
                else:
                    result += "Caller data not available.\n\n"
            except Exception as e:
                result += f"Error getting callers: {e}\n\n"
        
        if include_callees:
            # Get functions called by this function
            try:
                from code_as_data.db.models import FunctionCalled
                if FunctionCalled:
                    callees = (code_service.db_session.query(FunctionCalled)
                             .filter(FunctionCalled.function_id == target_function.id)
                             .limit(20)
                             .all())
                    
                    if callees:
                        result += f"Calls ({len(callees)} functions):\n"
                        for callee in callees:
                            result += f"  → {callee.name}"
                            if callee.module_name:
                                result += f" (in {callee.module_name})"
                            result += "\n"
                        result += "\n"
                    else:
                        result += "No function calls found.\n\n"
                else:
                    result += "Callee data not available.\n\n"
            except Exception as e:
                result += f"Error getting callees: {e}\n\n"
        
        return [types.TextContent(type="text", text=result)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error getting function call graph: {e}"
        )]

async def handle_get_function_callers(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Get all functions that call a specific function"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    function_name = arguments["function_name"]
    module_name = arguments.get("module_name")
    limit = arguments.get("limit", 50)
    
    try:
        # Get the target function
        module_id = None
        if module_name:
            module = code_service.query_service.get_module_by_name(module_name)
            if not module:
                return [types.TextContent(
                    type="text",
                    text=f"Module not found: {module_name}"
                )]
            module_id = module.id
        
        functions = code_service.query_service.get_function_by_name(function_name, module_id)
        if not functions:
            return [types.TextContent(
                type="text",
                text=f"Function not found: {function_name}"
            )]
        
        target_function = functions[0]
        
        # Get callers
        try:
            from code_as_data.db.models import FunctionCalled
            if FunctionCalled:
                callers = (code_service.db_session.query(FunctionCalled)
                         .filter(FunctionCalled.name == target_function.name)
                         .join(Function, FunctionCalled.function_id == Function.id)
                         .limit(limit)
                         .all())
                
                if not callers:
                    return [types.TextContent(
                        type="text",
                        text=f"No functions found that call '{function_name}'"
                    )]
                
                result = f"Functions that call '{target_function.name}' ({len(callers)} found):\n\n"
                
                for caller in callers:
                    if hasattr(caller, 'function') and caller.function:
                        result += f"- {caller.function.name}"
                        if caller.function.module:
                            result += f" (in {caller.function.module.name})"
                        if caller.src_loc:
                            result += f" at {caller.src_loc}"
                        result += "\n"
                
                return [types.TextContent(type="text", text=result)]
            else:
                return [types.TextContent(
                    type="text",
                    text="Function call data not available - FunctionCalled model not loaded"
                )]
        except Exception as e:
            return [types.TextContent(
                type="text",
                text=f"Error querying callers: {e}"
            )]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error getting function callers: {e}"
        )]

async def handle_get_function_callees(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Get all functions called by a specific function"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    function_name = arguments["function_name"]
    module_name = arguments.get("module_name")
    limit = arguments.get("limit", 50)
    
    try:
        # Get the target function
        module_id = None
        if module_name:
            module = code_service.query_service.get_module_by_name(module_name)
            if not module:
                return [types.TextContent(
                    type="text",
                    text=f"Module not found: {module_name}"
                )]
            module_id = module.id
        
        functions = code_service.query_service.get_function_by_name(function_name, module_id)
        if not functions:
            return [types.TextContent(
                type="text",
                text=f"Function not found: {function_name}"
            )]
        
        target_function = functions[0]
        
        # Get callees
        try:
            from code_as_data.db.models import FunctionCalled
            if FunctionCalled:
                callees = (code_service.db_session.query(FunctionCalled)
                         .filter(FunctionCalled.function_id == target_function.id)
                         .limit(limit)
                         .all())
                
                if not callees:
                    return [types.TextContent(
                        type="text",
                        text=f"No functions found called by '{function_name}'"
                    )]
                
                result = f"Functions called by '{target_function.name}' ({len(callees)} found):\n\n"
                
                for callee in callees:
                    result += f"- {callee.name}"
                    if callee.module_name:
                        result += f" (in {callee.module_name})"
                    if callee.src_loc:
                        result += f" at {callee.src_loc}"
                    result += "\n"
                
                return [types.TextContent(type="text", text=result)]
            else:
                return [types.TextContent(
                    type="text",
                    text="Function call data not available - FunctionCalled model not loaded"
                )]
        except Exception as e:
            return [types.TextContent(
                type="text",
                text=f"Error querying callees: {e}"
            )]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error getting function callees: {e}"
        )]

# Phase 1: Advanced Query Capabilities Tool Handlers

async def handle_execute_advanced_query(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Execute complex JSON-based queries"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    query = arguments["query"]
    query_type = query["type"]
    conditions = query.get("conditions", [])
    limit = query.get("limit", 100)
    
    try:
        # Map query types to models
        model_map = {
            "function": Function,
            "module": Module,
        }
        
        # Try to import other models if available
        try:
            from code_as_data.db.models import Type, Class, Import, Instance
            model_map.update({
                "type": Type,
                "class": Class, 
                "import": Import,
                "instance": Instance
            })
        except ImportError:
            pass
        
        if query_type not in model_map:
            return [types.TextContent(
                type="text",
                text=f"Query type '{query_type}' not available or not supported"
            )]
        
        model = model_map[query_type]
        if not model:
            return [types.TextContent(
                type="text",
                text=f"Model for '{query_type}' not available - code_as_data library not fully loaded"
            )]
        
        # Build query
        db_query = code_service.db_session.query(model)
        
        # Apply conditions
        for condition in conditions:
            field = condition.get("field")
            operator = condition.get("operator")
            value = condition.get("value")
            
            if not field or not operator:
                continue
                
            # Get the field from the model
            if hasattr(model, field):
                model_field = getattr(model, field)
                
                # Apply operator
                if operator == "eq":
                    db_query = db_query.filter(model_field == value)
                elif operator == "ne":
                    db_query = db_query.filter(model_field != value)
                elif operator == "like":
                    like_pattern = build_like_pattern(value)
                    db_query = db_query.filter(model_field.like(like_pattern))
                elif operator == "ilike":
                    like_pattern = build_like_pattern(value)
                    db_query = db_query.filter(model_field.ilike(like_pattern))
                elif operator == "startswith":
                    normalized_value = normalize_search_pattern(value)
                    db_query = db_query.filter(model_field.like(f"{normalized_value}%"))
                elif operator == "endswith":
                    normalized_value = normalize_search_pattern(value)
                    db_query = db_query.filter(model_field.like(f"%{normalized_value}"))
                elif operator == "gt":
                    db_query = db_query.filter(model_field > value)
                elif operator == "lt":
                    db_query = db_query.filter(model_field < value)
                elif operator == "ge":
                    db_query = db_query.filter(model_field >= value)
                elif operator == "le":
                    db_query = db_query.filter(model_field <= value)
                elif operator == "is_null":
                    db_query = db_query.filter(model_field.is_(None))
        
        # Execute query with limit
        results = db_query.limit(limit).all()
        
        if not results:
            return [types.TextContent(
                type="text",
                text=f"No {query_type}s found matching the specified conditions"
            )]
        
        result_text = f"Advanced Query Results ({len(results)} {query_type}s found):\n\n"
        
        for item in results:
            if hasattr(item, 'name'):
                result_text += f"- {item.name}"
                if hasattr(item, 'module') and item.module:
                    result_text += f" (in {item.module.name})"
                elif hasattr(item, 'module_name') and item.module_name:
                    result_text += f" (in {item.module_name})"
                result_text += "\n"
            else:
                result_text += f"- {str(item)}\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error executing advanced query: {e}"
        )]

async def handle_find_cross_module_calls(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Find function calls that cross module boundaries"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    source_module = arguments.get("source_module")
    target_module = arguments.get("target_module")
    limit = arguments.get("limit", 100)
    
    try:
        from code_as_data.db.models import FunctionCalled
        if not FunctionCalled:
            return [types.TextContent(
                type="text",
                text="FunctionCalled model not available - cross-module call analysis not supported"
            )]
        
        # Build query for cross-module calls
        query = (code_service.db_session.query(FunctionCalled)
                .join(Function, FunctionCalled.function_id == Function.id)
                .join(Module, Function.module_id == Module.id))
        
        # Filter by source module if specified
        if source_module:
            source_pattern = build_like_pattern(source_module)
            query = query.filter(Module.name.like(source_pattern))
        
        # Filter by target module if specified  
        if target_module:
            target_pattern = build_like_pattern(target_module)
            query = query.filter(FunctionCalled.module_name.like(target_pattern))
        
        # Only get calls where source and target modules are different
        query = query.filter(Module.name != FunctionCalled.module_name)
        
        results = query.limit(limit).all()
        
        if not results:
            return [types.TextContent(
                type="text",
                text="No cross-module function calls found matching the specified criteria"
            )]
        
        result_text = f"Cross-Module Function Calls ({len(results)} found):\n\n"
        
        for call in results:
            if hasattr(call, 'function') and call.function and call.function.module:
                result_text += f"- {call.function.module.name}.{call.function.name} → {call.module_name}.{call.name}\n"
            else:
                result_text += f"- {call.name} (from {call.module_name})\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error finding cross-module calls: {e}"
        )]

async def handle_analyze_function_complexity(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Analyze function complexity metrics"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    module_name = arguments.get("module_name")
    min_complexity = arguments.get("min_complexity", 5)
    limit = arguments.get("limit", 50)
    
    try:
        # Build base query
        query = code_service.db_session.query(Function)
        
        # Filter by module if specified
        if module_name:
            module = code_service.query_service.get_module_by_name(module_name)
            if not module:
                return [types.TextContent(
                    type="text",
                    text=f"Module not found: {module_name}"
                )]
            query = query.filter(Function.module_id == module.id)
        
        functions = query.all()
        
        if not functions:
            return [types.TextContent(
                type="text",
                text="No functions found for complexity analysis"
            )]
        
        # Calculate complexity metrics
        complex_functions = []
        
        for func in functions:
            complexity_score = 0
            
            # Signature complexity (rough estimate)
            if func.function_signature:
                sig_len = len(func.function_signature)
                arrow_count = func.function_signature.count("->")
                complexity_score += (sig_len // 20) + (arrow_count * 2)
            
            # Try to get call count
            try:
                from code_as_data.db.models import FunctionCalled
                if FunctionCalled:
                    call_count = (code_service.db_session.query(FunctionCalled)
                                .filter(FunctionCalled.function_id == func.id)
                                .count())
                    complexity_score += call_count // 3
            except:
                pass
            
            # Check if function has where clauses (local functions)
            try:
                from code_as_data.db.models import WhereFunction
                if WhereFunction:
                    where_count = (code_service.db_session.query(WhereFunction)
                                 .filter(WhereFunction.parent_function_id == func.id)
                                 .count())
                    complexity_score += where_count * 2
            except:
                pass
            
            if complexity_score >= min_complexity:
                complex_functions.append((func, complexity_score))
        
        # Sort by complexity score
        complex_functions.sort(key=lambda x: x[1], reverse=True)
        complex_functions = complex_functions[:limit]
        
        if not complex_functions:
            return [types.TextContent(
                type="text",
                text=f"No functions found with complexity >= {min_complexity}"
            )]
        
        result_text = f"Function Complexity Analysis ({len(complex_functions)} functions):\n\n"
        
        for func, score in complex_functions:
            result_text += f"- {func.name} (complexity: {score})"
            if func.module:
                result_text += f" in {func.module.name}"
            if func.function_signature:
                result_text += f"\n  Signature: {func.function_signature[:100]}"
                if len(func.function_signature) > 100:
                    result_text += "..."
            result_text += "\n\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error analyzing function complexity: {e}"
        )]

async def handle_get_code_statistics(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Get comprehensive statistics about the codebase"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    include_details = arguments.get("include_details", False)
    
    try:
        result = "Codebase Statistics:\n\n"
        
        # Use efficient database queries for basic counts
        try:
            # Get module count efficiently
            module_count = code_service.db_session.query(Module).count()
            result += f"📁 Modules: {module_count:,}\n"
            
            # Get function count efficiently with single query
            function_count = code_service.db_session.query(Function).count()
            result += f"⚡ Functions: {function_count:,}\n"
            
        except Exception:
            # Fallback to QueryService if direct queries fail
            try:
                all_modules = code_service.query_service.get_all_modules()
                module_count = len(all_modules) if all_modules else 0
                result += f"📁 Modules: {module_count:,}\n"
                
                # Still avoid the expensive per-module loop in fallback
                function_count = code_service.db_session.query(Function).count()
                result += f"⚡ Functions: {function_count:,}\n"
            except Exception:
                result += "📁 Modules: Unable to count\n"
                result += "⚡ Functions: Unable to count\n"
                function_count = 0
                module_count = 0
        
        # Try to get other entity counts
        try:
            from code_as_data.db.models import Type, Class, Import, Instance, FunctionCalled
            
            if Type:
                type_count = code_service.db_session.query(Type).count()
                result += f"🏗️ Types: {type_count:,}\n"
            
            if Class:
                class_count = code_service.db_session.query(Class).count()
                result += f"📚 Classes: {class_count:,}\n"
            
            if Import:
                import_count = code_service.db_session.query(Import).count()
                result += f"📦 Imports: {import_count:,}\n"
            
            if Instance:
                instance_count = code_service.db_session.query(Instance).count()
                result += f"🔗 Instances: {instance_count:,}\n"
            
            if FunctionCalled:
                call_count = code_service.db_session.query(FunctionCalled).count()
                result += f"📞 Function Calls: {call_count:,}\n"
        except ImportError:
            pass
        
        if include_details:
            result += "\n--- Detailed Breakdown ---\n\n"
            
            # Top modules by function count using efficient query
            try:
                # Use single query with JOIN and GROUP BY for efficiency
                from sqlalchemy import func as sql_func
                top_modules_query = (
                    code_service.db_session.query(
                        Module.name,
                        sql_func.count(Function.id).label('function_count')
                    )
                    .join(Function, Module.id == Function.module_id, isouter=True)
                    .group_by(Module.id, Module.name)
                    .order_by(sql_func.count(Function.id).desc())
                    .limit(10)
                )
                
                top_modules = top_modules_query.all()
                
                if top_modules:
                    result += "Top 10 Modules by Function Count:\n"
                    for module_name, func_count in top_modules:
                        result += f"  • {module_name}: {func_count} functions\n"
                    result += "\n"
            except Exception as e:
                # Fallback to simplified approach if JOIN fails
                try:
                    # Use efficient query without per-module iteration
                    from sqlalchemy import func as sql_func
                    sample_modules_query = (
                        code_service.db_session.query(
                            Module.name,
                            sql_func.count(Function.id).label('function_count')
                        )
                        .outerjoin(Function, Module.id == Function.module_id)
                        .group_by(Module.id, Module.name)
                        .order_by(sql_func.count(Function.id).desc())
                        .limit(10)
                    )
                    
                    sample_modules = sample_modules_query.all()
                    if sample_modules:
                        result += "Top Modules by Function Count:\n"
                        for module_name, func_count in sample_modules:
                            result += f"  • {module_name}: {func_count} functions\n"
                        result += "\n"
                except Exception:
                    # If even the fallback fails, skip this section
                    pass
            
            # Function signature analysis using efficient query
            try:
                # Count functions with signatures using single query
                signed_functions = (
                    code_service.db_session.query(Function)
                    .filter(Function.function_signature.isnot(None))
                    .filter(Function.function_signature != '')
                    .count()
                )
                
                if function_count > 0:
                    result += f"Functions with signatures: {signed_functions:,} ({signed_functions/function_count*100:.1f}%)\n"
            except Exception:
                pass
            
            # Average functions per module
            if module_count > 0:
                avg_functions = function_count / module_count
                result += f"Average functions per module: {avg_functions:.1f}\n"
        
        return [types.TextContent(type="text", text=result)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error getting code statistics: {e}"
        )]

# Phase 2: Type System Analysis Tool Handlers

async def handle_list_types(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Get types by module or pattern with support for different type categories"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    module_name = arguments.get("module_name")
    pattern = arguments.get("pattern")
    type_category = arguments.get("type_category")
    limit = arguments.get("limit", 100)
    
    try:
        from code_as_data.db.models import Type
        if not Type:
            return [types.TextContent(
                type="text",
                text="Type model not available - code_as_data library not fully loaded"
            )]
        
        # Build query
        query = code_service.db_session.query(Type)
        
        # Filter by module if specified
        if module_name:
            module = code_service.query_service.get_module_by_name(module_name)
            if not module:
                return [types.TextContent(
                    type="text",
                    text=f"Module not found: {module_name}"
                )]
            query = query.filter(Type.module_id == module.id)
        
        # Filter by pattern if specified
        if pattern:
            type_like_pattern = build_like_pattern(pattern)
            query = query.filter(Type.type_name.like(type_like_pattern))
        
        # Filter by type category if specified
        if type_category:
            query = query.filter(Type.type_of_type == type_category)
        
        types_list = query.limit(limit).all()
        
        if not types_list:
            return [types.TextContent(
                type="text",
                text="No types found matching the specified criteria"
            )]
        
        result_text = f"Types found ({len(types_list)} results):\n\n"
        
        for type_obj in types_list:
            result_text += f"- {type_obj.type_name}"
            if type_obj.type_of_type:
                result_text += f" ({type_obj.type_of_type})"
            if type_obj.module:
                result_text += f" in {type_obj.module.name}"
            if type_obj.src_loc:
                result_text += f" at {type_obj.src_loc}"
            result_text += "\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error listing types: {e}"
        )]

async def handle_get_type_details(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Get detailed information about a specific type"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    type_name = arguments["type_name"]
    module_name = arguments.get("module_name")
    include_constructors = arguments.get("include_constructors", True)
    include_fields = arguments.get("include_fields", True)
    
    try:
        from code_as_data.db.models import Type, Constructor, Field
        if not Type:
            return [types.TextContent(
                type="text",
                text="Type model not available - code_as_data library not fully loaded"
            )]
        
        # Build query
        query = code_service.db_session.query(Type).filter(Type.type_name == type_name)
        
        # Filter by module if specified
        if module_name:
            module = code_service.query_service.get_module_by_name(module_name)
            if not module:
                return [types.TextContent(
                    type="text",
                    text=f"Module not found: {module_name}"
                )]
            query = query.filter(Type.module_id == module.id)
        
        types_list = query.all()
        
        if not types_list:
            return [types.TextContent(
                type="text",
                text=f"Type not found: {type_name}"
            )]
        
        result_text = f"Type Details for '{type_name}':\n\n"
        
        for type_obj in types_list:
            result_text += f"Name: {type_obj.type_name}\n"
            result_text += f"Category: {type_obj.type_of_type or 'Unknown'}\n"
            if type_obj.module:
                result_text += f"Module: {type_obj.module.name}\n"
            if type_obj.src_loc:
                result_text += f"Location: {type_obj.src_loc}\n"
            if type_obj.raw_code:
                result_text += f"Definition: {type_obj.raw_code[:200]}"
                if len(type_obj.raw_code) > 200:
                    result_text += "..."
                result_text += "\n"
            
            if include_constructors and Constructor:
                # Get constructors for this type
                constructors = (code_service.db_session.query(Constructor)
                              .filter(Constructor.type_id == type_obj.id)
                              .all())
                
                if constructors:
                    result_text += f"\nConstructors ({len(constructors)}):\n"
                    for constructor in constructors:
                        result_text += f"  • {constructor.name}\n"
                        
                        if include_fields and Field:
                            # Get fields for this constructor
                            fields = (code_service.db_session.query(Field)
                                    .filter(Field.constructor_id == constructor.id)
                                    .all())
                            
                            if fields:
                                for field in fields:
                                    result_text += f"    - {field.field_name or 'unnamed'}: {field.field_type_raw or 'Unknown type'}\n"
            
            result_text += "\n---\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error getting type details: {e}"
        )]

async def handle_search_types(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Search for types by name pattern with advanced filtering"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    pattern = arguments["pattern"]
    module_pattern = arguments.get("module_pattern")
    type_category = arguments.get("type_category")
    limit = arguments.get("limit", 50)
    
    try:
        from code_as_data.db.models import Type
        if not Type:
            return [types.TextContent(
                type="text",
                text="Type model not available - code_as_data library not fully loaded"
            )]
        
        # Build query
        like_pattern = build_like_pattern(pattern)
        query = code_service.db_session.query(Type).filter(Type.type_name.like(like_pattern))
        
        # Filter by module pattern if specified
        if module_pattern:
            module_like_pattern = build_like_pattern(module_pattern)
            query = query.join(Module).filter(Module.name.like(module_like_pattern))
        
        # Filter by type category if specified
        if type_category:
            query = query.filter(Type.type_of_type == type_category)
        
        types_list = query.limit(limit).all()
        
        if not types_list:
            return [types.TextContent(
                type="text",
                text=f"No types found matching pattern: {pattern}"
            )]
        
        result_text = f"Types matching '{pattern}' ({len(types_list)} found):\n\n"
        
        for type_obj in types_list:
            result_text += f"- {type_obj.type_name}"
            if type_obj.type_of_type:
                result_text += f" ({type_obj.type_of_type})"
            if type_obj.module:
                result_text += f" in {type_obj.module.name}"
            result_text += "\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error searching types: {e}"
        )]

async def handle_get_type_dependencies(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Analyze type dependencies and relationships"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    type_name = arguments["type_name"]
    module_name = arguments.get("module_name")
    include_dependents = arguments.get("include_dependents", False)
    depth = arguments.get("depth", 2)
    
    try:
        from code_as_data.db.models import Type
        if not Type:
            return [types.TextContent(
                type="text",
                text="Type model not available - code_as_data library not fully loaded"
            )]
        
        # Find the target type
        query = code_service.db_session.query(Type).filter(Type.type_name == type_name)
        
        if module_name:
            module = code_service.query_service.get_module_by_name(module_name)
            if not module:
                return [types.TextContent(
                    type="text",
                    text=f"Module not found: {module_name}"
                )]
            query = query.filter(Type.module_id == module.id)
        
        target_type = query.first()
        
        if not target_type:
            return [types.TextContent(
                type="text",
                text=f"Type not found: {type_name}"
            )]
        
        result_text = f"Type Dependencies for '{type_name}':\n\n"
        
        # Try to analyze dependencies from type definition
        if target_type.raw_code:
            result_text += f"Definition: {target_type.raw_code[:300]}"
            if len(target_type.raw_code) > 300:
                result_text += "..."
            result_text += "\n\n"
        
        # Try to get type dependencies if available
        try:
            from code_as_data.db.models import TypeDependency
            if TypeDependency:
                # Dependencies this type has
                dependencies = (code_service.db_session.query(TypeDependency)
                              .filter(TypeDependency.dependent_id == target_type.id)
                              .join(Type, TypeDependency.dependency_id == Type.id)
                              .all())
                
                if dependencies:
                    result_text += f"Depends on ({len(dependencies)} types):\n"
                    for dep in dependencies:
                        if hasattr(dep, 'dependency') and dep.dependency:
                            result_text += f"  → {dep.dependency.type_name}\n"
                    result_text += "\n"
                
                if include_dependents:
                    # Types that depend on this type
                    dependents = (code_service.db_session.query(TypeDependency)
                                .filter(TypeDependency.dependency_id == target_type.id)
                                .join(Type, TypeDependency.dependent_id == Type.id)
                                .all())
                    
                    if dependents:
                        result_text += f"Used by ({len(dependents)} types):\n"
                        for dep in dependents:
                            if hasattr(dep, 'dependent') and dep.dependent:
                                result_text += f"  ← {dep.dependent.type_name}\n"
                        result_text += "\n"
            else:
                result_text += "Type dependency analysis not available.\n"
        except Exception as e:
            result_text += f"Note: Advanced dependency analysis not available: {e}\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error analyzing type dependencies: {e}"
        )]

async def handle_analyze_type_usage(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Analyze how types are used throughout the codebase"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    type_name = arguments.get("type_name")
    module_name = arguments.get("module_name")
    usage_threshold = arguments.get("usage_threshold", 1)
    limit = arguments.get("limit", 50)
    
    try:
        from code_as_data.db.models import Type
        if not Type:
            return [types.TextContent(
                type="text",
                text="Type model not available - code_as_data library not fully loaded"
            )]
        
        if type_name:
            # Analyze specific type usage
            query = code_service.db_session.query(Type).filter(Type.type_name == type_name)
            
            if module_name:
                module = code_service.query_service.get_module_by_name(module_name)
                if not module:
                    return [types.TextContent(
                        type="text",
                        text=f"Module not found: {module_name}"
                    )]
                query = query.filter(Type.module_id == module.id)
            
            target_type = query.first()
            
            if not target_type:
                return [types.TextContent(
                    type="text",
                    text=f"Type not found: {type_name}"
                )]
            
            result_text = f"Usage Analysis for Type '{type_name}':\n\n"
            result_text += f"Category: {target_type.type_of_type or 'Unknown'}\n"
            if target_type.module:
                result_text += f"Defined in: {target_type.module.name}\n"
            
            # Try to find usage in function signatures
            functions_using_type = (code_service.db_session.query(Function)
                                  .filter(Function.function_signature.like(f"%{type_name}%"))
                                  .limit(20)
                                  .all())
            
            if functions_using_type:
                result_text += f"\nUsed in function signatures ({len(functions_using_type)} functions):\n"
                for func in functions_using_type:
                    result_text += f"  • {func.name}"
                    if func.module:
                        result_text += f" (in {func.module.name})"
                    result_text += "\n"
            
        else:
            # General type usage statistics
            query = code_service.db_session.query(Type)
            
            if module_name:
                module = code_service.query_service.get_module_by_name(module_name)
                if not module:
                    return [types.TextContent(
                        type="text",
                        text=f"Module not found: {module_name}"
                    )]
                query = query.filter(Type.module_id == module.id)
            
            types_list = query.limit(limit).all()
            
            if not types_list:
                return [types.TextContent(
                    type="text",
                    text="No types found for usage analysis"
                )]
            
            result_text = f"Type Usage Analysis ({len(types_list)} types):\n\n"
            
            # Group by type category
            type_categories = {}
            for type_obj in types_list:
                category = type_obj.type_of_type or "Unknown"
                if category not in type_categories:
                    type_categories[category] = []
                type_categories[category].append(type_obj)
            
            for category, types_in_category in type_categories.items():
                result_text += f"{category}: {len(types_in_category)} types\n"
                for type_obj in types_in_category[:10]:  # Show first 10
                    result_text += f"  • {type_obj.type_name}"
                    if type_obj.module:
                        result_text += f" (in {type_obj.module.name})"
                    result_text += "\n"
                if len(types_in_category) > 10:
                    result_text += f"  ... and {len(types_in_category) - 10} more\n"
                result_text += "\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error analyzing type usage: {e}"
        )]

# Phase 2: Class Analysis Tool Handlers

async def handle_list_classes(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Get class definitions with filtering by module or pattern"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    module_name = arguments.get("module_name")
    pattern = arguments.get("pattern")
    limit = arguments.get("limit", 100)
    
    try:
        from code_as_data.db.models import Class
        if not Class:
            return [types.TextContent(
                type="text",
                text="Class model not available - code_as_data library not fully loaded"
            )]
        
        # Build query
        query = code_service.db_session.query(Class)
        
        # Filter by module if specified
        if module_name:
            module = code_service.query_service.get_module_by_name(module_name)
            if not module:
                return [types.TextContent(
                    type="text",
                    text=f"Module not found: {module_name}"
                )]
            query = query.filter(Class.module_id == module.id)
        
        # Filter by pattern if specified
        if pattern:
            class_like_pattern = build_like_pattern(pattern)
            query = query.filter(Class.class_name.like(class_like_pattern))
        
        classes_list = query.limit(limit).all()
        
        if not classes_list:
            return [types.TextContent(
                type="text",
                text="No classes found matching the specified criteria"
            )]
        
        result_text = f"Classes found ({len(classes_list)} results):\n\n"
        
        for class_obj in classes_list:
            result_text += f"- {class_obj.class_name}"
            if class_obj.module:
                result_text += f" in {class_obj.module.name}"
            if class_obj.src_location:
                result_text += f" at {class_obj.src_location}"
            result_text += "\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error listing classes: {e}"
        )]

async def handle_get_class_details(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Get detailed information about a specific class"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    class_name = arguments["class_name"]
    module_name = arguments.get("module_name")
    include_instances = arguments.get("include_instances", True)
    
    try:
        from code_as_data.db.models import Class, Instance
        if not Class:
            return [types.TextContent(
                type="text",
                text="Class model not available - code_as_data library not fully loaded"
            )]
        
        # Build query
        query = code_service.db_session.query(Class).filter(Class.class_name == class_name)
        
        # Filter by module if specified
        if module_name:
            module = code_service.query_service.get_module_by_name(module_name)
            if not module:
                return [types.TextContent(
                    type="text",
                    text=f"Module not found: {module_name}"
                )]
            query = query.filter(Class.module_id == module.id)
        
        classes_list = query.all()
        
        if not classes_list:
            return [types.TextContent(
                type="text",
                text=f"Class not found: {class_name}"
            )]
        
        result_text = f"Class Details for '{class_name}':\n\n"
        
        for class_obj in classes_list:
            result_text += f"Name: {class_obj.class_name}\n"
            if class_obj.module:
                result_text += f"Module: {class_obj.module.name}\n"
            if class_obj.src_location:
                result_text += f"Location: {class_obj.src_location}\n"
            if class_obj.class_definition:
                result_text += f"Definition: {class_obj.class_definition[:300]}"
                if len(class_obj.class_definition) > 300:
                    result_text += "..."
                result_text += "\n"
            
            if include_instances and Instance:
                # Try to find instances of this class
                # Note: This might need adjustment based on the actual schema
                instances = (code_service.db_session.query(Instance)
                           .filter(Instance.instance_definition.like(f"%{class_name}%"))
                           .limit(10)
                           .all())
                
                if instances:
                    result_text += f"\nInstances ({len(instances)} found):\n"
                    for instance in instances:
                        result_text += f"  • Instance"
                        if instance.module:
                            result_text += f" in {instance.module.name}"
                        if instance.src_loc:
                            result_text += f" at {instance.src_loc}"
                        result_text += "\n"
                        if instance.instance_signature:
                            result_text += f"    Signature: {instance.instance_signature[:100]}"
                            if len(instance.instance_signature) > 100:
                                result_text += "..."
                            result_text += "\n"
            
            result_text += "\n---\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error getting class details: {e}"
        )]

async def handle_search_classes(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Search for classes by name pattern with module filtering"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    pattern = arguments["pattern"]
    module_pattern = arguments.get("module_pattern")
    limit = arguments.get("limit", 50)
    
    try:
        from code_as_data.db.models import Class
        if not Class:
            return [types.TextContent(
                type="text",
                text="Class model not available - code_as_data library not fully loaded"
            )]
        
        # Build query
        like_pattern = build_like_pattern(pattern)
        query = code_service.db_session.query(Class).filter(Class.class_name.like(like_pattern))
        
        # Filter by module pattern if specified
        if module_pattern:
            module_like_pattern = build_like_pattern(module_pattern)
            query = query.join(Module).filter(Module.name.like(module_like_pattern))
        
        classes_list = query.limit(limit).all()
        
        if not classes_list:
            return [types.TextContent(
                type="text",
                text=f"No classes found matching pattern: {pattern}"
            )]
        
        result_text = f"Classes matching '{pattern}' ({len(classes_list)} found):\n\n"
        
        for class_obj in classes_list:
            result_text += f"- {class_obj.class_name}"
            if class_obj.module:
                result_text += f" in {class_obj.module.name}"
            if class_obj.src_location:
                result_text += f" at {class_obj.src_location}"
            result_text += "\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error searching classes: {e}"
        )]

# Phase 2: Import Analysis Tool Handlers

async def handle_analyze_imports(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Analyze import patterns and dependencies for modules"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    module_name = arguments.get("module_name")
    import_pattern = arguments.get("import_pattern")
    include_qualified = arguments.get("include_qualified", True)
    limit = arguments.get("limit", 100)
    
    try:
        from code_as_data.db.models import Import
        if not Import:
            return [types.TextContent(
                type="text",
                text="Import model not available - code_as_data library not fully loaded"
            )]
        
        # Build query
        query = code_service.db_session.query(Import)
        
        # Filter by module if specified
        if module_name:
            module = code_service.query_service.get_module_by_name(module_name)
            if not module:
                return [types.TextContent(
                    type="text",
                    text=f"Module not found: {module_name}"
                )]
            query = query.filter(Import.module_id == module.id)
        
        # Filter by import pattern if specified
        if import_pattern:
            import_like_pattern = build_like_pattern(import_pattern)
            query = query.filter(Import.module_name.like(import_like_pattern))
        
        imports_list = query.limit(limit).all()
        
        if not imports_list:
            return [types.TextContent(
                type="text",
                text="No imports found matching the specified criteria"
            )]
        
        result_text = f"Import Analysis ({len(imports_list)} imports found):\n\n"
        
        # Group by importing module
        imports_by_module = {}
        for imp in imports_list:
            if imp.module:
                module_key = imp.module.name
                if module_key not in imports_by_module:
                    imports_by_module[module_key] = []
                imports_by_module[module_key].append(imp)
        
        for importing_module, module_imports in imports_by_module.items():
            result_text += f"Module: {importing_module} ({len(module_imports)} imports)\n"
            
            for imp in module_imports:
                result_text += f"  • {imp.module_name}"
                
                if imp.package_name:
                    result_text += f" (from {imp.package_name})"
                
                if include_qualified:
                    if imp.qualified_style:
                        result_text += " [qualified]"
                    if imp.as_module_name:
                        result_text += f" as {imp.as_module_name}"
                    if imp.is_hiding:
                        result_text += " [hiding]"
                
                if imp.src_loc:
                    result_text += f" at {imp.src_loc}"
                
                result_text += "\n"
            
            result_text += "\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error analyzing imports: {e}"
        )]

async def handle_get_import_graph(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Generate module import relationship graph"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    root_module = arguments.get("root_module")
    depth = arguments.get("depth", 3)
    include_external = arguments.get("include_external", False)
    limit = arguments.get("limit", 50)
    
    try:
        from code_as_data.db.models import Import
        if not Import:
            return [types.TextContent(
                type="text",
                text="Import model not available - code_as_data library not fully loaded"
            )]
        
        if root_module:
            # Start from specific module
            module = code_service.query_service.get_module_by_name(root_module)
            if not module:
                return [types.TextContent(
                    type="text",
                    text=f"Root module not found: {root_module}"
                )]
            
            # Get imports from this module
            imports = (code_service.db_session.query(Import)
                      .filter(Import.module_id == module.id)
                      .limit(limit)
                      .all())
            
            result_text = f"Import Graph starting from '{root_module}':\n\n"
            result_text += f"{root_module}\n"
            
            for imp in imports:
                if not include_external and imp.package_name:
                    continue
                
                result_text += f"  ├─ {imp.module_name}"
                if imp.package_name:
                    result_text += f" (from {imp.package_name})"
                if imp.qualified_style:
                    result_text += " [qualified]"
                result_text += "\n"
        
        else:
            # General import statistics
            query = code_service.db_session.query(Import)
            
            if not include_external:
                query = query.filter(Import.package_name.is_(None))
            
            imports_list = query.limit(limit).all()
            
            if not imports_list:
                return [types.TextContent(
                    type="text",
                    text="No imports found for graph generation"
                )]
            
            result_text = f"Import Graph Overview ({len(imports_list)} imports):\n\n"
            
            # Group by most imported modules
            import_counts = {}
            for imp in imports_list:
                target = imp.module_name
                if target not in import_counts:
                    import_counts[target] = 0
                import_counts[target] += 1
            
            # Sort by popularity
            top_imports = sorted(import_counts.items(), key=lambda x: x[1], reverse=True)[:20]
            
            result_text += "Most Imported Modules:\n"
            for module_name, count in top_imports:
                result_text += f"  • {module_name}: imported {count} times\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error generating import graph: {e}"
        )]

async def handle_find_unused_imports(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Find potentially unused imports in modules"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    module_name = arguments.get("module_name")
    package_pattern = arguments.get("package_pattern")
    limit = arguments.get("limit", 100)
    
    try:
        from code_as_data.db.models import Import
        if not Import:
            return [types.TextContent(
                type="text",
                text="Import model not available - code_as_data library not fully loaded"
            )]
        
        # Build query
        query = code_service.db_session.query(Import)
        
        # Filter by module if specified
        if module_name:
            module = code_service.query_service.get_module_by_name(module_name)
            if not module:
                return [types.TextContent(
                    type="text",
                    text=f"Module not found: {module_name}"
                )]
            query = query.filter(Import.module_id == module.id)
        
        # Filter by package pattern if specified
        if package_pattern:
            package_like_pattern = build_like_pattern(package_pattern)
            query = query.filter(Import.package_name.like(package_like_pattern))
        
        imports_list = query.limit(limit).all()
        
        if not imports_list:
            return [types.TextContent(
                type="text",
                text="No imports found for analysis"
            )]
        
        result_text = f"Potentially Unused Imports Analysis ({len(imports_list)} imports checked):\n\n"
        
        # This is a simplified analysis - in a real implementation, you'd need
        # to check if imported symbols are actually used in the module
        suspicious_imports = []
        
        for imp in imports_list:
            # Simple heuristics for potentially unused imports
            is_suspicious = False
            reasons = []
            
            # Check if import is qualified but might not be used
            if imp.qualified_style and imp.as_module_name:
                # This would need actual usage analysis
                reasons.append("qualified import (needs usage verification)")
                is_suspicious = True
            
            # Check for very specific imports that might be unused
            if imp.is_hiding and imp.hiding_specs:
                reasons.append("hiding import (check if necessary)")
                is_suspicious = True
            
            # External packages that might be over-imported
            if imp.package_name and "test" not in imp.package_name.lower():
                reasons.append("external package (verify necessity)")
                is_suspicious = True
            
            if is_suspicious:
                suspicious_imports.append((imp, reasons))
        
        if not suspicious_imports:
            result_text += "No obviously suspicious imports found.\n"
            result_text += "Note: This is a basic analysis. For comprehensive unused import detection,\n"
            result_text += "use dedicated tools like HLint or manual code review.\n"
        else:
            result_text += f"Found {len(suspicious_imports)} potentially unused imports:\n\n"
            
            for imp, reasons in suspicious_imports:
                if imp.module:
                    result_text += f"Module: {imp.module.name}\n"
                result_text += f"  Import: {imp.module_name}"
                if imp.package_name:
                    result_text += f" (from {imp.package_name})"
                result_text += "\n"
                for reason in reasons:
                    result_text += f"    - {reason}\n"
                if imp.src_loc:
                    result_text += f"    Location: {imp.src_loc}\n"
                result_text += "\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error finding unused imports: {e}"
        )]

async def handle_get_import_details(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Get detailed information about imports in a module"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    module_name = arguments["module_name"]
    include_source_info = arguments.get("include_source_info", True)
    
    try:
        from code_as_data.db.models import Import
        if not Import:
            return [types.TextContent(
                type="text",
                text="Import model not available - code_as_data library not fully loaded"
            )]
        
        # Find the module
        module = code_service.query_service.get_module_by_name(module_name)
        if not module:
            return [types.TextContent(
                type="text",
                text=f"Module not found: {module_name}"
            )]
        
        # Get all imports for this module
        imports = (code_service.db_session.query(Import)
                  .filter(Import.module_id == module.id)
                  .all())
        
        if not imports:
            return [types.TextContent(
                type="text",
                text=f"No imports found in module: {module_name}"
            )]
        
        result_text = f"Import Details for Module '{module_name}' ({len(imports)} imports):\n\n"
        
        # Group imports by type
        internal_imports = []
        external_imports = []
        qualified_imports = []
        hiding_imports = []
        
        for imp in imports:
            if imp.package_name:
                external_imports.append(imp)
            else:
                internal_imports.append(imp)
            
            if imp.qualified_style:
                qualified_imports.append(imp)
            
            if imp.is_hiding:
                hiding_imports.append(imp)
        
        # Internal imports
        if internal_imports:
            result_text += f"Internal Imports ({len(internal_imports)}):\n"
            for imp in internal_imports:
                result_text += f"  • {imp.module_name}"
                if imp.as_module_name:
                    result_text += f" as {imp.as_module_name}"
                if include_source_info and imp.src_loc:
                    result_text += f" (at {imp.src_loc})"
                result_text += "\n"
            result_text += "\n"
        
        # External imports
        if external_imports:
            result_text += f"External Imports ({len(external_imports)}):\n"
            for imp in external_imports:
                result_text += f"  • {imp.module_name} (from {imp.package_name})"
                if imp.as_module_name:
                    result_text += f" as {imp.as_module_name}"
                if include_source_info and imp.src_loc:
                    result_text += f" (at {imp.src_loc})"
                result_text += "\n"
            result_text += "\n"
        
        # Qualified imports
        if qualified_imports:
            result_text += f"Qualified Imports ({len(qualified_imports)}):\n"
            for imp in qualified_imports:
                result_text += f"  • qualified {imp.module_name}"
                if imp.as_module_name:
                    result_text += f" as {imp.as_module_name}"
                result_text += "\n"
            result_text += "\n"
        
        # Hiding imports
        if hiding_imports:
            result_text += f"Hiding Imports ({len(hiding_imports)}):\n"
            for imp in hiding_imports:
                result_text += f"  • {imp.module_name} hiding"
                if imp.hiding_specs:
                    result_text += f" ({imp.hiding_specs})"
                result_text += "\n"
            result_text += "\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error getting import details: {e}"
        )]

# Phase 1: Advanced Pattern Analysis Tool Handlers

async def handle_find_similar_functions(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Find functions similar to a given function based on signature and code"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    function_name = arguments["function_name"]
    module_name = arguments.get("module_name")
    similarity_threshold = arguments.get("similarity_threshold", 0.7)
    limit = arguments.get("limit", 10)
    
    try:
        # Get the target function
        module_id = None
        if module_name:
            module = code_service.query_service.get_module_by_name(module_name)
            if not module:
                return [types.TextContent(
                    type="text",
                    text=f"Module not found: {module_name}"
                )]
            module_id = module.id
        
        functions = code_service.query_service.get_function_by_name(function_name, module_id)
        if not functions:
            return [types.TextContent(
                type="text",
                text=f"Function not found: {function_name}"
            )]
        
        target_function = functions[0]
        
        # Use QueryService's find_similar_functions method
        similar_functions = code_service.query_service.find_similar_functions(
            target_function.id, 
            threshold=similarity_threshold
        )
        
        # Apply limit
        similar_functions = similar_functions[:limit]
        
        if not similar_functions:
            return [types.TextContent(
                type="text",
                text=f"No similar functions found for '{function_name}' with threshold {similarity_threshold}"
            )]
        
        result_text = f"Similar Functions to '{target_function.name}' (threshold: {similarity_threshold}):\n\n"
        
        for similar in similar_functions:
            func_info = similar["function"]
            score = similar["similarity_score"]
            result_text += f"• {func_info['name']} (similarity: {score:.3f})"
            if func_info.get("module"):
                result_text += f" in {func_info['module']}"
            result_text += "\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error finding similar functions: {e}"
        )]

async def handle_find_code_patterns(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Find recurring code patterns across functions"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    pattern_code = arguments["pattern_code"]
    min_matches = arguments.get("min_matches", 3)
    module_pattern = arguments.get("module_pattern")
    limit = arguments.get("limit", 20)
    
    try:
        # Use QueryService's find_code_patterns method
        pattern_results = code_service.query_service.find_code_patterns(
            pattern_code, 
            min_matches=min_matches
        )
        
        # Apply module pattern filter if specified
        if module_pattern:
            filtered_results = []
            for result in pattern_results:
                func_info = result["function"]
                if func_info.get("module") and module_pattern in func_info["module"]:
                    filtered_results.append(result)
            pattern_results = filtered_results
        
        # Apply limit
        pattern_results = pattern_results[:limit]
        
        if not pattern_results:
            return [types.TextContent(
                type="text",
                text=f"No code patterns found matching the specified criteria"
            )]
        
        result_text = f"Code Pattern Analysis ({len(pattern_results)} functions contain pattern):\n\n"
        result_text += f"Pattern searched:\n{pattern_code}\n\n"
        
        for result in pattern_results:
            func_info = result["function"]
            matches = result["matches"]
            matched_lines = result.get("matched_lines", [])
            
            result_text += f"• {func_info['name']}"
            if func_info.get("module"):
                result_text += f" in {func_info['module']}"
            result_text += f" ({matches} matches)\n"
            
            # Show first few matched lines
            for i, (line_num, line_content) in enumerate(matched_lines[:3]):
                result_text += f"    Line {line_num}: {line_content.strip()}\n"
            
            if len(matched_lines) > 3:
                result_text += f"    ... and {len(matched_lines) - 3} more matches\n"
            
            result_text += "\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error finding code patterns: {e}"
        )]

async def handle_group_similar_functions(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Group functions by similarity to identify common patterns"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    similarity_threshold = arguments.get("similarity_threshold", 0.7)
    module_pattern = arguments.get("module_pattern")
    min_group_size = arguments.get("min_group_size", 2)
    limit = arguments.get("limit", 10)
    
    try:
        # Use QueryService's group_similar_functions method
        function_groups = code_service.query_service.group_similar_functions(
            similarity_threshold=similarity_threshold
        )
        
        # Apply module pattern filter if specified
        if module_pattern:
            filtered_groups = []
            for group in function_groups:
                filtered_functions = []
                for func in group["functions"]:
                    if func.get("module") and module_pattern in func["module"]:
                        filtered_functions.append(func)
                
                if len(filtered_functions) >= min_group_size:
                    group["functions"] = filtered_functions
                    filtered_groups.append(group)
            function_groups = filtered_groups
        
        # Filter by minimum group size
        function_groups = [g for g in function_groups if len(g["functions"]) >= min_group_size]
        
        # Apply limit
        function_groups = function_groups[:limit]
        
        if not function_groups:
            return [types.TextContent(
                type="text",
                text=f"No function groups found with similarity >= {similarity_threshold} and group size >= {min_group_size}"
            )]
        
        result_text = f"Function Similarity Groups (threshold: {similarity_threshold}):\n\n"
        
        for i, group in enumerate(function_groups, 1):
            functions = group["functions"]
            similarity = group["similarity"]
            
            result_text += f"Group {i} ({len(functions)} functions, similarity: {similarity:.3f}):\n"
            for func in functions:
                result_text += f"  • {func['name']}"
                if func.get("module"):
                    result_text += f" in {func['module']}"
                result_text += "\n"
            result_text += "\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error grouping similar functions: {e}"
        )]

# Phase 1: Advanced Type Analysis Tool Handlers

async def handle_build_type_dependency_graph(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Build a comprehensive type dependency graph showing relationships between types"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    root_type = arguments.get("root_type")
    module_pattern = arguments.get("module_pattern")
    include_external = arguments.get("include_external", False)
    max_depth = arguments.get("max_depth", 3)
    
    try:
        # Use QueryService's build_type_dependency_graph method
        graph_data = code_service.query_service.build_type_dependency_graph()
        graph = graph_data["graph"]
        type_name_index = graph_data["type_name_index"]
        
        result_text = "Type Dependency Graph:\n\n"
        
        if root_type:
            # Show subgraph starting from specific type
            if root_type in type_name_index:
                result_text += f"Dependencies for type '{root_type}':\n\n"
                
                for type_id in type_name_index[root_type]:
                    type_node = graph.get(type_id, {})
                    if module_pattern and module_pattern not in type_node.get("module_name", ""):
                        continue
                    
                    result_text += f"• {type_node.get('type_name', 'Unknown')}"
                    if type_node.get("module_name"):
                        result_text += f" (in {type_node['module_name']})"
                    result_text += "\n"
                    
                    # Show direct dependencies
                    edges = type_node.get("edges", [])
                    if edges:
                        result_text += "  Dependencies:\n"
                        for edge in edges[:10]:  # Limit to first 10
                            if edge in graph:
                                edge_node = graph[edge]
                                result_text += f"    → {edge_node.get('type_name', edge)}"
                                if edge_node.get("module_name"):
                                    result_text += f" (in {edge_node['module_name']})"
                                result_text += "\n"
                            elif not include_external:
                                # Skip external dependencies
                                continue
                            else:
                                result_text += f"    → {edge} (external)\n"
                        
                        if len(edges) > 10:
                            result_text += f"    ... and {len(edges) - 10} more dependencies\n"
                    
                    result_text += "\n"
            else:
                result_text += f"Type '{root_type}' not found in dependency graph\n"
        else:
            # Show general graph statistics
            total_types = len(graph)
            total_dependencies = sum(len(node.get("edges", [])) for node in graph.values())
            
            result_text += f"Graph Statistics:\n"
            result_text += f"• Total types: {total_types}\n"
            result_text += f"• Total dependencies: {total_dependencies}\n"
            result_text += f"• Average dependencies per type: {total_dependencies/total_types:.1f}\n\n"
            
            # Show most connected types
            type_connections = []
            for type_id, node in graph.items():
                type_name = node.get("type_name", "Unknown")
                module_name = node.get("module_name", "")
                edge_count = len(node.get("edges", []))
                
                if module_pattern and module_pattern not in module_name:
                    continue
                
                type_connections.append((type_name, module_name, edge_count))
            
            # Sort by connection count
            type_connections.sort(key=lambda x: x[2], reverse=True)
            
            result_text += f"Most Connected Types (top 10):\n"
            for type_name, module_name, edge_count in type_connections[:10]:
                result_text += f"• {type_name}"
                if module_name:
                    result_text += f" (in {module_name})"
                result_text += f" - {edge_count} dependencies\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error building type dependency graph: {e}"
        )]

async def handle_get_nested_types(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Get all nested type definitions for specified types"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    type_names = arguments["type_names"]
    gateway_name = arguments["gateway_name"]
    exclude_pattern = arguments.get("exclude_pattern")
    include_raw_definitions = arguments.get("include_raw_definitions", True)
    
    try:
        # Use QueryService's get_all_nested_types method
        nested_types = code_service.query_service.get_all_nested_types(
            type_names=type_names,
            gateway_name=gateway_name,
            should_not_match=exclude_pattern
        )
        
        if not nested_types:
            return [types.TextContent(
                type="text",
                text=f"No nested types found for {type_names} under gateway '{gateway_name}'"
            )]
        
        result_text = f"Nested Types for {type_names} (gateway: {gateway_name}):\n\n"
        result_text += f"Found {len(nested_types)} type definitions:\n\n"
        
        for i, type_def in enumerate(nested_types, 1):
            if include_raw_definitions:
                result_text += f"=== Type Definition {i} ===\n"
                result_text += f"{type_def}\n\n"
            else:
                # Extract just the type name from the definition
                lines = type_def.split('\n')
                if lines:
                    first_line = lines[0].strip()
                    result_text += f"{i}. {first_line}\n"
        
        if not include_raw_definitions:
            result_text += f"\nUse 'include_raw_definitions: true' to see full type definitions.\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error getting nested types: {e}"
        )]

async def handle_analyze_type_relationships(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Analyze deep type relationships and dependencies"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    type_name = arguments["type_name"]
    source_module = arguments["source_module"]
    analysis_depth = arguments.get("analysis_depth", 2)
    include_dependents = arguments.get("include_dependents", True)
    module_filter = arguments.get("module_filter")
    
    try:
        # Use QueryService's get_subgraph_by_type method
        subgraph_nodes = code_service.query_service.get_subgraph_by_type(
            type_name=type_name,
            src_module_name=source_module,
            module_pattern=module_filter
        )
        
        if not subgraph_nodes:
            return [types.TextContent(
                type="text",
                text=f"No type relationships found for '{type_name}' in module '{source_module}'"
            )]
        
        result_text = f"Type Relationship Analysis for '{type_name}' (module: {source_module}):\n\n"
        result_text += f"Found {len(subgraph_nodes)} related types:\n\n"
        
        # Get the graph to show details
        graph_data = code_service.query_service.build_type_dependency_graph()
        graph = graph_data["graph"]
        
        for i, node_id in enumerate(subgraph_nodes, 1):
            if node_id in graph:
                node = graph[node_id]
                result_text += f"{i}. {node.get('type_name', 'Unknown')}"
                if node.get("module_name"):
                    result_text += f" (in {node['module_name']})"
                result_text += "\n"
                
                # Show dependencies
                edges = node.get("edges", [])
                if edges:
                    result_text += "   Dependencies:\n"
                    for edge in edges[:5]:  # Limit to first 5
                        if edge in graph:
                            edge_node = graph[edge]
                            result_text += f"     → {edge_node.get('type_name', edge)}\n"
                        else:
                            result_text += f"     → {edge} (external)\n"
                    
                    if len(edges) > 5:
                        result_text += f"     ... and {len(edges) - 5} more\n"
                
                result_text += "\n"
            else:
                result_text += f"{i}. {node_id} (external)\n\n"
        
        # Show reverse dependencies if requested
        if include_dependents:
            result_text += "=== Reverse Dependencies ===\n"
            dependents = []
            
            # Find types that depend on our target type
            for node_id, node in graph.items():
                edges = node.get("edges", [])
                for edge in edges:
                    if edge in subgraph_nodes and node_id not in subgraph_nodes:
                        dependents.append((node_id, node))
                        break
            
            if dependents:
                result_text += f"Found {len(dependents)} types that depend on '{type_name}':\n\n"
                for node_id, node in dependents[:10]:  # Limit to first 10
                    result_text += f"• {node.get('type_name', 'Unknown')}"
                    if node.get("module_name"):
                        result_text += f" (in {node['module_name']})"
                    result_text += "\n"
                
                if len(dependents) > 10:
                    result_text += f"... and {len(dependents) - 10} more\n"
            else:
                result_text += f"No types found that depend on '{type_name}'\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error analyzing type relationships: {e}"
        )]

# Phase 1: Source Location Tool Handlers

async def handle_find_element_by_location(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Find code elements (functions, types, classes, imports) by source location"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    file_path = arguments["file_path"]
    line_number = arguments["line_number"]
    base_directory = arguments.get("base_directory", "")
    element_types = arguments.get("element_types", ["all"])
    
    try:
        result_text = f"Code Elements at {file_path}:{line_number}:\n\n"
        found_elements = []
        
        # Search for functions if requested
        if "function" in element_types or "all" in element_types:
            function = code_service.query_service.find_function_by_src_loc(
                base_dir_path=base_directory,
                path=file_path,
                line=line_number
            )
            if function:
                found_elements.append(("Function", function.name, function.function_signature or "No signature", function.src_loc))
        
        # Search for types if requested
        if "type" in element_types or "all" in element_types:
            type_def = code_service.query_service.find_type_by_src_loc(
                base_dir_path=base_directory,
                path=file_path,
                line=line_number
            )
            if type_def:
                found_elements.append(("Type", type_def.type_name, type_def.type_of_type or "Unknown category", type_def.src_loc))
        
        # Search for classes if requested
        if "class" in element_types or "all" in element_types:
            class_def = code_service.query_service.find_class_by_src_loc(
                base_dir_path=base_directory,
                path=file_path,
                line=line_number
            )
            if class_def:
                found_elements.append(("Class", class_def.class_name, "Class definition", class_def.src_location))
        
        # Search for imports if requested
        if "import" in element_types or "all" in element_types:
            import_stmt = code_service.query_service.find_import_by_src_loc(
                base_dir_path=base_directory,
                path=file_path,
                line=line_number
            )
            if import_stmt:
                import_desc = f"import {import_stmt.module_name}"
                if import_stmt.package_name:
                    import_desc += f" (from {import_stmt.package_name})"
                found_elements.append(("Import", import_stmt.module_name, import_desc, import_stmt.src_loc))
        
        if not found_elements:
            return [types.TextContent(
                type="text",
                text=f"No code elements found at {file_path}:{line_number}"
            )]
        
        for element_type, name, description, location in found_elements:
            result_text += f"• {element_type}: {name}\n"
            result_text += f"  Description: {description}\n"
            result_text += f"  Location: {location}\n\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error finding elements by location: {e}"
        )]

async def handle_get_location_context(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Get comprehensive context around a source location"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    file_path = arguments["file_path"]
    line_number = arguments["line_number"]
    context_radius = arguments.get("context_radius", 5)
    include_dependencies = arguments.get("include_dependencies", True)
    
    try:
        result_text = f"Context for {file_path}:{line_number} (±{context_radius} lines):\n\n"
        
        # Find the closest function to this location
        function = code_service.query_service.find_function_by_src_loc(
            base_dir_path="",
            path=file_path,
            line=line_number
        )
        
        if function:
            result_text += f"=== Function Context ===\n"
            result_text += f"Function: {function.name}\n"
            if function.function_signature:
                result_text += f"Signature: {function.function_signature}\n"
            if function.module:
                result_text += f"Module: {function.module.name}\n"
            result_text += f"Location: {function.src_loc}\n\n"
            
            if include_dependencies:
                # Get function dependencies using QueryService
                try:
                    functions_used = code_service.query_service.get_functions_used(function.id)
                    types_used = code_service.query_service.get_types_and_functions(function.id)
                    
                    local_functions = functions_used.get("local_functions", [])
                    other_functions = functions_used.get("other_functions", [])
                    local_types = types_used.get("local_types", [])
                    non_local_types = types_used.get("non_local_types", [])
                    
                    if local_functions:
                        result_text += f"Local Functions Used ({len(local_functions)}):\n"
                        for func in local_functions[:5]:  # Limit to first 5
                            result_text += f"  • {func.name}\n"
                        if len(local_functions) > 5:
                            result_text += f"  ... and {len(local_functions) - 5} more\n"
                        result_text += "\n"
                    
                    if other_functions:
                        result_text += f"External Functions Used ({len(other_functions)}):\n"
                        for func in other_functions[:5]:  # Limit to first 5
                            result_text += f"  • {func.get('function_name', 'Unknown')}"
                            if func.get('module_name'):
                                result_text += f" (from {func['module_name']})"
                            result_text += "\n"
                        if len(other_functions) > 5:
                            result_text += f"  ... and {len(other_functions) - 5} more\n"
                        result_text += "\n"
                    
                    if local_types:
                        result_text += f"Local Types Used ({len(local_types)}):\n"
                        for type_obj in local_types[:5]:  # Limit to first 5
                            result_text += f"  • {type_obj.type_name}\n"
                        if len(local_types) > 5:
                            result_text += f"  ... and {len(local_types) - 5} more\n"
                        result_text += "\n"
                    
                    if non_local_types:
                        result_text += f"External Types Used ({len(non_local_types)}):\n"
                        for type_info in non_local_types[:5]:  # Limit to first 5
                            result_text += f"  • {type_info.get('type_name', 'Unknown')}"
                            if type_info.get('module_name'):
                                result_text += f" (from {type_info['module_name']})"
                            result_text += "\n"
                        if len(non_local_types) > 5:
                            result_text += f"  ... and {len(non_local_types) - 5} more\n"
                        result_text += "\n"
                
                except Exception as dep_error:
                    result_text += f"Note: Could not analyze dependencies: {dep_error}\n\n"
        
        # Check for types at this location
        type_def = code_service.query_service.find_type_by_src_loc(
            base_dir_path="",
            path=file_path,
            line=line_number
        )
        
        if type_def:
            result_text += f"=== Type Context ===\n"
            result_text += f"Type: {type_def.type_name}\n"
            result_text += f"Category: {type_def.type_of_type or 'Unknown'}\n"
            if type_def.module:
                result_text += f"Module: {type_def.module.name}\n"
            result_text += f"Location: {type_def.src_loc}\n"
            if type_def.raw_code:
                # Show first few lines of the type definition
                lines = type_def.raw_code.split('\n')[:5]
                result_text += f"Definition:\n"
                for line in lines:
                    result_text += f"  {line}\n"
                if len(type_def.raw_code.split('\n')) > 5:
                    result_text += "  ...\n"
            result_text += "\n"
        
        # Check for classes at this location
        class_def = code_service.query_service.find_class_by_src_loc(
            base_dir_path="",
            path=file_path,
            line=line_number
        )
        
        if class_def:
            result_text += f"=== Class Context ===\n"
            result_text += f"Class: {class_def.class_name}\n"
            if class_def.module:
                result_text += f"Module: {class_def.module.name}\n"
            result_text += f"Location: {class_def.src_location}\n"
            if class_def.class_definition:
                # Show first few lines of the class definition
                lines = class_def.class_definition.split('\n')[:3]
                result_text += f"Definition:\n"
                for line in lines:
                    result_text += f"  {line}\n"
                if len(class_def.class_definition.split('\n')) > 3:
                    result_text += "  ...\n"
            result_text += "\n"
        
        # Check for imports at this location
        import_stmt = code_service.query_service.find_import_by_src_loc(
            base_dir_path="",
            path=file_path,
            line=line_number
        )
        
        if import_stmt:
            result_text += f"=== Import Context ===\n"
            result_text += f"Import: {import_stmt.module_name}\n"
            if import_stmt.package_name:
                result_text += f"Package: {import_stmt.package_name}\n"
            if import_stmt.qualified_style:
                result_text += f"Style: Qualified\n"
            if import_stmt.as_module_name:
                result_text += f"Alias: {import_stmt.as_module_name}\n"
            result_text += f"Location: {import_stmt.src_loc}\n\n"
        
        if not function and not type_def and not class_def and not import_stmt:
            result_text += "No code elements found at this location.\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error getting location context: {e}"
        )]

# Phase 1: Function Context Tool Handlers

async def handle_get_function_context(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Get complete context for a function including all used types and functions"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    function_name = arguments["function_name"]
    module_name = arguments.get("module_name")
    include_prompts = arguments.get("include_prompts", True)
    include_local_definitions = arguments.get("include_local_definitions", True)
    include_external_references = arguments.get("include_external_references", True)
    
    try:
        # Get the target function
        module_id = None
        if module_name:
            module = code_service.query_service.get_module_by_name(module_name)
            if not module:
                return [types.TextContent(
                    type="text",
                    text=f"Module not found: {module_name}"
                )]
            module_id = module.id
        
        functions = code_service.query_service.get_function_by_name(function_name, module_id)
        if not functions:
            return [types.TextContent(
                type="text",
                text=f"Function not found: {function_name}"
            )]
        
        target_function = functions[0]
        
        result_text = f"Complete Context for Function '{target_function.name}':\n\n"
        
        # Basic function information
        result_text += f"=== Function Information ===\n"
        result_text += f"Name: {target_function.name}\n"
        if target_function.function_signature:
            result_text += f"Signature: {target_function.function_signature}\n"
        if target_function.module:
            result_text += f"Module: {target_function.module.name}\n"
        result_text += f"Location: {target_function.src_loc}\n"
        if target_function.raw_string:
            result_text += f"Code Length: {len(target_function.raw_string)} characters\n"
        result_text += "\n"
        
        # Get function and type usage using QueryService methods
        if include_prompts:
            # Use QueryService's prompt generation methods
            local_functions_prompt, non_local_functions_prompt = code_service.query_service.get_functions_used_prompt(target_function.id)
            local_types_prompt, non_local_types_prompt = code_service.query_service.get_types_used_in_function_prompt(target_function.id)
            
            if include_local_definitions and local_functions_prompt:
                result_text += f"=== Local Functions Used ===\n"
                result_text += local_functions_prompt
                result_text += "\n\n"
            
            if include_local_definitions and local_types_prompt:
                result_text += f"=== Local Types Used ===\n"
                result_text += local_types_prompt
                result_text += "\n\n"
            
            if include_external_references and non_local_functions_prompt:
                result_text += f"=== External Functions Used ===\n"
                result_text += non_local_functions_prompt
                result_text += "\n\n"
            
            if include_external_references and non_local_types_prompt:
                result_text += f"=== External Types Used ===\n"
                result_text += non_local_types_prompt
                result_text += "\n\n"
        else:
            # Get raw data without prompts
            functions_used = code_service.query_service.get_functions_used(target_function.id)
            types_used = code_service.query_service.get_types_and_functions(target_function.id)
            
            local_functions = functions_used.get("local_functions", [])
            other_functions = functions_used.get("other_functions", [])
            local_types = types_used.get("local_types", [])
            non_local_types = types_used.get("non_local_types", [])
            
            if include_local_definitions and local_functions:
                result_text += f"=== Local Functions Used ({len(local_functions)}) ===\n"
                for func in local_functions:
                    result_text += f"• {func.name}"
                    if func.function_signature:
                        result_text += f" :: {func.function_signature}"
                    result_text += "\n"
                result_text += "\n"
            
            if include_local_definitions and local_types:
                result_text += f"=== Local Types Used ({len(local_types)}) ===\n"
                for type_obj in local_types:
                    result_text += f"• {type_obj.type_name}"
                    if type_obj.type_of_type:
                        result_text += f" ({type_obj.type_of_type})"
                    result_text += "\n"
                result_text += "\n"
            
            if include_external_references and other_functions:
                result_text += f"=== External Functions Used ({len(other_functions)}) ===\n"
                for func in other_functions:
                    result_text += f"• {func.get('function_name', 'Unknown')}"
                    if func.get('module_name'):
                        result_text += f" (from {func['module_name']})"
                    result_text += "\n"
                result_text += "\n"
            
            if include_external_references and non_local_types:
                result_text += f"=== External Types Used ({len(non_local_types)}) ===\n"
                for type_info in non_local_types:
                    result_text += f"• {type_info.get('type_name', 'Unknown')}"
                    if type_info.get('module_name'):
                        result_text += f" (from {type_info['module_name']})"
                    result_text += "\n"
                result_text += "\n"
        
        # Show function implementation if available
        if target_function.raw_string:
            result_text += f"=== Function Implementation ===\n"
            result_text += "```haskell\n"
            result_text += target_function.raw_string
            result_text += "\n```\n\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error getting function context: {e}"
        )]

async def handle_generate_function_imports(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Generate all necessary import statements for a function or code element"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    element_name = arguments["element_name"]
    source_module = arguments["source_module"]
    element_type = arguments.get("element_type", "any")
    import_style = arguments.get("import_style", "haskell")
    
    try:
        # Use QueryService's generate_imports_for_element method
        import_statements = code_service.query_service.generate_imports_for_element(
            element_name=element_name,
            source_module=source_module,
            element_type=element_type
        )
        
        if not import_statements:
            return [types.TextContent(
                type="text",
                text=f"No import statements needed for '{element_name}' in module '{source_module}'"
            )]
        
        result_text = f"Import Statements for '{element_name}' (type: {element_type}):\n\n"
        
        if import_style == "haskell":
            result_text += "```haskell\n"
            for stmt in import_statements:
                result_text += f"{stmt}\n"
            result_text += "```\n\n"
        else:
            for i, stmt in enumerate(import_statements, 1):
                result_text += f"{i}. {stmt}\n"
        
        result_text += f"\nGenerated {len(import_statements)} import statement(s) for '{element_name}' in module '{source_module}'.\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error generating imports: {e}"
        )]

# Phase 2: Enhanced Query Capabilities Tool Handlers

async def handle_execute_custom_query(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Execute custom SQL queries on the code database with parameters"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    query = arguments["query"]
    parameters = arguments.get("parameters", {})
    limit = arguments.get("limit", 100)
    
    try:
        # Use QueryService's execute_custom_query method
        results = code_service.query_service.execute_custom_query(
            query_str=query,
            params=parameters
        )
        
        # Apply limit
        results = results[:limit]
        
        if not results:
            return [types.TextContent(
                type="text",
                text="No results returned from the query"
            )]
        
        result_text = f"Custom Query Results ({len(results)} rows):\n\n"
        result_text += f"Query: {query}\n"
        if parameters:
            result_text += f"Parameters: {parameters}\n"
        result_text += "\n"
        
        # Show column headers if available
        if results and isinstance(results[0], dict):
            headers = list(results[0].keys())
            result_text += " | ".join(headers) + "\n"
            result_text += "-" * (len(" | ".join(headers))) + "\n"
            
            for row in results:
                values = [str(row.get(header, "")) for header in headers]
                result_text += " | ".join(values) + "\n"
        else:
            # Simple list format
            for i, row in enumerate(results, 1):
                result_text += f"{i}. {row}\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error executing custom query: {e}"
        )]

async def handle_pattern_match_code(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Advanced pattern matching to find code structures"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    pattern_type = arguments["pattern_type"]
    pattern_config = arguments["pattern_config"]
    limit = arguments.get("limit", 50)
    
    try:
        # Use QueryService's pattern_match method
        results = code_service.query_service.pattern_match({
            "type": pattern_type,
            **pattern_config
        })
        
        # Apply limit
        results = results[:limit]
        
        if not results:
            return [types.TextContent(
                type="text",
                text=f"No {pattern_type} patterns found matching the specified criteria"
            )]
        
        result_text = f"Pattern Matching Results ({pattern_type}):\n\n"
        result_text += f"Configuration: {pattern_config}\n"
        result_text += f"Found {len(results)} matches:\n\n"
        
        if pattern_type == "function_call":
            for result in results:
                caller = result.get("caller", {})
                callee = result.get("callee", {})
                
                result_text += f"• {caller.get('name', 'Unknown')}"
                if caller.get('module'):
                    result_text += f" (in {caller['module']})"
                
                result_text += f" → {callee.get('name', 'Unknown')}"
                if callee.get('module'):
                    result_text += f" (in {callee['module']})"
                result_text += "\n"
        
        elif pattern_type == "type_usage":
            for result in results:
                function = result.get("function", {})
                type_name = result.get("type", "Unknown")
                
                result_text += f"• Type '{type_name}' used in {function.get('name', 'Unknown')}"
                if function.get('module'):
                    result_text += f" (in {function['module']})"
                result_text += "\n"
        
        elif pattern_type == "code_structure":
            for result in results:
                parent_function = result.get("parent_function", {})
                nested_functions = result.get("nested_functions", [])
                
                result_text += f"• {parent_function.get('name', 'Unknown')}"
                if parent_function.get('module'):
                    result_text += f" (in {parent_function['module']})"
                
                if nested_functions:
                    result_text += f" - {len(nested_functions)} nested functions:\n"
                    for nested in nested_functions:
                        result_text += f"    - {nested.get('name', 'Unknown')}\n"
                else:
                    result_text += "\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error pattern matching: {e}"
        )]

async def handle_analyze_cross_module_dependencies(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Comprehensive analysis of cross-module dependencies and coupling"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    analysis_type = arguments.get("analysis_type", "dependencies")
    module_pattern = arguments.get("module_pattern")
    include_metrics = arguments.get("include_metrics", True)
    threshold = arguments.get("threshold", 1)
    limit = arguments.get("limit", 50)
    
    try:
        result_text = f"Cross-Module {analysis_type.title()} Analysis:\n\n"
        
        if analysis_type == "dependencies":
            # Use QueryService's find_cross_module_dependencies method
            dependencies = code_service.query_service.find_cross_module_dependencies()
            
            # Apply module pattern filter if specified
            if module_pattern:
                filtered_deps = []
                for dep in dependencies:
                    caller_module = dep["caller_module"]["name"]
                    callee_module = dep["callee_module"]["name"]
                    if module_pattern in caller_module or module_pattern in callee_module:
                        filtered_deps.append(dep)
                dependencies = filtered_deps
            
            # Apply threshold filter
            dependencies = [d for d in dependencies if d["call_count"] >= threshold]
            
            # Sort by call count and apply limit
            dependencies.sort(key=lambda x: x["call_count"], reverse=True)
            dependencies = dependencies[:limit]
            
            if not dependencies:
                return [types.TextContent(
                    type="text",
                    text=f"No cross-module dependencies found matching criteria"
                )]
            
            result_text += f"Found {len(dependencies)} cross-module dependencies:\n\n"
            
            for dep in dependencies:
                caller = dep["caller_module"]
                callee = dep["callee_module"]
                calls = dep["call_count"]
                
                result_text += f"• {caller['name']} → {callee['name']} ({calls} calls)\n"
        
        elif analysis_type == "coupling":
            # Use QueryService's analyze_module_coupling method
            coupling_analysis = code_service.query_service.analyze_module_coupling()
            
            result_text += f"Module Coupling Analysis:\n\n"
            result_text += f"Total Modules: {coupling_analysis['module_count']}\n"
            result_text += f"Total Cross-Module Calls: {coupling_analysis['total_cross_module_calls']}\n"
            result_text += f"Total Dependencies: {coupling_analysis['dependency_count']}\n\n"
            
            if include_metrics:
                module_metrics = coupling_analysis["module_metrics"]
                
                # Apply module pattern filter if specified
                if module_pattern:
                    module_metrics = [m for m in module_metrics if module_pattern in m["name"]]
                
                # Apply threshold and limit
                module_metrics = [m for m in module_metrics if m["total"] >= threshold]
                module_metrics = module_metrics[:limit]
                
                result_text += f"Module Coupling Metrics (top {len(module_metrics)}):\n"
                for module in module_metrics:
                    result_text += f"• {module['name']}:\n"
                    result_text += f"    Incoming: {module['incoming']} calls\n"
                    result_text += f"    Outgoing: {module['outgoing']} calls\n"
                    result_text += f"    Total: {module['total']} calls\n\n"
        
        elif analysis_type == "complexity":
            # Use QueryService's find_complex_functions method
            complex_functions = code_service.query_service.find_complex_functions(
                complexity_threshold=threshold
            )
            
            # Apply module pattern filter if specified
            if module_pattern:
                filtered_functions = []
                for func in complex_functions:
                    module_name = func["function"].get("module", "")
                    if module_pattern in module_name:
                        filtered_functions.append(func)
                complex_functions = filtered_functions
            
            # Apply limit
            complex_functions = complex_functions[:limit]
            
            if not complex_functions:
                return [types.TextContent(
                    type="text",
                    text=f"No complex functions found matching criteria"
                )]
            
            result_text += f"Found {len(complex_functions)} complex functions:\n\n"
            
            for func_data in complex_functions:
                func = func_data["function"]
                metrics = func_data["metrics"]
                
                result_text += f"• {func['name']}"
                if func.get("module"):
                    result_text += f" (in {func['module']})"
                result_text += f"\n"
                
                if include_metrics:
                    result_text += f"    Cyclomatic Complexity: {metrics['cyclomatic_complexity']}\n"
                    result_text += f"    Dependencies: {metrics['dependency_count']}\n"
                    result_text += f"    Nested Functions: {metrics['nested_functions']}\n"
                    result_text += f"    Total Complexity: {metrics['total_complexity']}\n"
                
                result_text += "\n"
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error analyzing cross-module dependencies: {e}"
        )]

async def handle_enhanced_function_call_graph(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Generate enhanced function call graphs with advanced options"""
    if not code_service.initialized:
        return [types.TextContent(
            type="text",
            text="Error: Database not initialized. Check that FDEP_PATH is configured and restart the server."
        )]
    
    function_name = arguments["function_name"]
    module_name = arguments.get("module_name")
    max_depth = arguments.get("max_depth", 3)
    graph_format = arguments.get("graph_format", "tree")
    include_signatures = arguments.get("include_signatures", False)
    filter_modules = arguments.get("filter_modules", [])
    
    try:
        # Get the target function
        module_id = None
        if module_name:
            module = code_service.query_service.get_module_by_name(module_name)
            if not module:
                return [types.TextContent(
                    type="text",
                    text=f"Module not found: {module_name}"
                )]
            module_id = module.id
        
        functions = code_service.query_service.get_function_by_name(function_name, module_id)
        if not functions:
            return [types.TextContent(
                type="text",
                text=f"Function not found: {function_name}"
            )]
        
        target_function = functions[0]
        
        # Use QueryService's get_function_call_graph method with enhanced options
        call_graph = code_service.query_service.get_function_call_graph(
            target_function.id, 
            depth=max_depth
        )
        
        if not call_graph:
            return [types.TextContent(
                type="text",
                text=f"No call graph available for function '{function_name}'"
            )]
        
        result_text = f"Enhanced Call Graph for '{target_function.name}' (depth: {max_depth}, format: {graph_format}):\n\n"
        
        if graph_format == "tree":
            result_text += _format_call_graph_tree(call_graph, include_signatures, filter_modules, 0)
        elif graph_format == "flat":
            result_text += _format_call_graph_flat(call_graph, include_signatures, filter_modules)
        else:  # graph format
            result_text += _format_call_graph_graph(call_graph, include_signatures, filter_modules)
        
        return [types.TextContent(type="text", text=result_text)]
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error generating enhanced call graph: {e}"
        )]

def _format_call_graph_tree(node, include_signatures, filter_modules, indent_level):
    """Format call graph as a tree structure"""
    indent = "  " * indent_level
    result = f"{indent}• {node.get('name', 'Unknown')}"
    
    if node.get('module'):
        # Apply module filter if specified
        if filter_modules and not any(pattern in node['module'] for pattern in filter_modules):
            return ""
        result += f" (in {node['module']})"
    
    if include_signatures and 'signature' in node:
        result += f" :: {node['signature']}"
    
    result += "\n"
    
    # Process called functions
    calls = node.get('calls', [])
    if calls:
        for call in calls:
            child_result = _format_call_graph_tree(call, include_signatures, filter_modules, indent_level + 1)
            if child_result:  # Only add if not filtered out
                result += child_result
    
    return result

def _format_call_graph_flat(node, include_signatures, filter_modules):
    """Format call graph as a flat list"""
    result = ""
    visited = set()
    
    def collect_functions(current_node, depth=0):
        nonlocal result, visited
        
        func_id = current_node.get('id', current_node.get('name', 'unknown'))
        if func_id in visited:
            return
        visited.add(func_id)
        
        # Apply module filter if specified
        if filter_modules and current_node.get('module'):
            if not any(pattern in current_node['module'] for pattern in filter_modules):
                return
        
        prefix = "  " * depth
        result += f"{prefix}• {current_node.get('name', 'Unknown')}"
        
        if current_node.get('module'):
            result += f" (in {current_node['module']})"
        
        if include_signatures and 'signature' in current_node:
            result += f" :: {current_node['signature']}"
        
        result += "\n"
        
        # Process called functions
        calls = current_node.get('calls', [])
        for call in calls:
            collect_functions(call, depth + 1)
    
    collect_functions(node)
    return result

def _format_call_graph_graph(node, include_signatures, filter_modules):
    """Format call graph as a graph with connections"""
    result = "Nodes:\n"
    connections = []
    visited = set()
    
    def collect_graph_data(current_node):
        nonlocal result, connections, visited
        
        func_id = current_node.get('id', current_node.get('name', 'unknown'))
        if func_id in visited:
            return
        visited.add(func_id)
        
        # Apply module filter if specified
        if filter_modules and current_node.get('module'):
            if not any(pattern in current_node['module'] for pattern in filter_modules):
                return
        
        # Add node
        result += f"  {current_node.get('name', 'Unknown')}"
        if current_node.get('module'):
            result += f" (in {current_node['module']})"
        if include_signatures and 'signature' in current_node:
            result += f" :: {current_node['signature']}"
        result += "\n"
        
        # Collect connections
        calls = current_node.get('calls', [])
        for call in calls:
            caller_name = current_node.get('name', 'Unknown')
            callee_name = call.get('name', 'Unknown')
            connections.append(f"  {caller_name} → {callee_name}")
            collect_graph_data(call)
    
    collect_graph_data(node)
    
    if connections:
        result += "\nConnections:\n"
        for connection in connections:
            result += connection + "\n"
    
    return result



async def main():
    """Main entry point for the MCP server"""
    logger.debug("main() function called")
    try:
        logger.info("Starting FDEP MCP Server")
        logger.debug("FDEP MCP Server startup initiated")
        
        # Initialize code service (database connection only)
        logger.debug("Initializing code analysis service...")
        if code_service.initialize():
            logger.info("Code analysis service initialized successfully")
        else:
            logger.warning("Failed to initialize code analysis service - tools will show errors")
        
        logger.info("Starting MCP protocol server...")
        logger.debug("Creating stdio server context...")
        
        # Run the server
        async with stdio_server() as (read_stream, write_stream):
            logger.debug("stdio server context created, starting MCP server run loop")
            await mcp_server.run(
                read_stream,
                write_stream,
                mcp_server.create_initialization_options()
            )
            logger.debug("MCP server run loop completed")
    except KeyboardInterrupt:
        logger.info("Server interrupted by user")
        logger.debug("KeyboardInterrupt received")
    except Exception as e:
        logger.error(f"Server failed to start: {e}")
        logger.debug(f"Server startup exception: {type(e).__name__}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        logger.debug("Server startup failed with exception")
    finally:
        # Ensure cleanup on exit
        logger.info("Cleaning up resources...")
        logger.debug("Starting cleanup process...")
        code_service.cleanup()
        logger.debug("Code service cleanup completed")
        logger.info("Server shutdown complete")
        logger.debug("Main function cleanup completed")
        # Don't exit with error code for MCP compatibility
        sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())