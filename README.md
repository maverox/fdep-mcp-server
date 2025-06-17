# FDEP MCP Server

**A Static Code Analysis Model Context Protocol (MCP) server** delivering 40+ comprehensive analysis tools for enterprise-scale Haskell codebases. Seamlessly integrates with MCP-compatible AI tools and clients to provide real-time code intelligence and architectural insights.

🔌 **MCP Protocol Compliant** | 🏗️ **40+ Analysis Tools** | ⚡ **Real-time Queries**

## ✨ MCP Server Features

📊 **40+ Comprehensive Analysis Tools**
- **Module Analysis**: 7 tools for module structure and dependencies
- **Function Analysis**: 8 tools for call graphs and complexity metrics
- **Type System**: 6 tools for type relationships and usage patterns
- **Class Analysis**: 3 tools for typeclass and instance analysis
- **Import Analysis**: 4 tools for dependency visualization
- **Advanced Queries**: 4 tools for complex JSON-based code queries
- **Pattern Analysis**: 5 tools for code pattern detection
- **Source Location**: 3 tools for location-based analysis
- **Enhanced Analysis**: 3 tools for advanced structural analysis


## 🚀 Quick Start

### Prerequisites
- Python 3.13+
- UV package manager
- PostgreSQL database (must be running)
- FDEP output from Spider plugin (for comprehensive analysis)

### Database Setup

**Before installation**, ensure PostgreSQL is running and create the required database:

```bash
# Start PostgreSQL (if not already running)
# On macOS with Homebrew:
brew services start postgresql

# On Ubuntu/Debian:
sudo systemctl start postgresql

# Create the database
createdb code_as_data
```

### Installation

1. **Install via UV (recommended)**:
```bash

# Clone and install in development mode
git clone https://github.com/juspay/fdep-mcp-server.git
cd fdep_mcp
uv venv
source .venv/bin/activate  # or `.venv\Scripts\activate` on Windows

uv pip install .

python scripts/setup_database.py --setup --verbose # Initialize database and import FDEP data
```

2. **Configure environment**:
```bash
cp .env.example .env
# Edit .env with your database settings and FDEP_PATH
```

## 🔌 MCP Client Configuration

After installation, configure your preferred MCP client to connect to the FDEP server:

### **Claude Code**
Add to your `~/.claude/settings.json`:
```json
{
  "mcpServers": {
    "fdep": {
      "command": "fdep-mcp-server",
      "args": [],
      "env": {
        "FDEP_PATH": "/path/to/your/fdep/output"
      }
    }
  }
}
```

**Note**: The first time running the server takes time as it imports and processes the FDEP data.

### **Cursor**
Add to your Cursor settings (`Cmd/Ctrl + ,` → Extensions → MCP):
```json
{
  "mcp.servers": {
    "fdep-haskell-analysis": {
      "command": "fdep-mcp-server",
      "args": [],
      "env": {
        "FDEP_PATH": "/path/to/your/fdep/output",
        "LOG_LEVEL": "INFO"
      }
    }
  }
}
```

**Note**: The first time running the server takes time as it imports and processes the FDEP data.

### **VS Code** 
Install the MCP extension and add to `settings.json`:
```json
{
  "mcp.servers": [
    {
      "name": "fdep-haskell-analysis",
      "command": "fdep-mcp-server", 
      "args": [],
      "env": {
        "FDEP_PATH": "/path/to/your/fdep/output"
      }
    }
  ]
}
```

**Note**: The first time running the server takes time as it imports and processes the FDEP data.

### **Cline**
Add to your Cline configuration:
```json
{
  "mcpServers": {
    "fdep": {
      "command": "fdep-mcp-server",
      "args": [],
      "env": {
        "FDEP_PATH": "/path/to/your/fdep/output"
      }
    }
  }
}
```

**Note**: The first time running the server takes time as it imports and processes the FDEP data.

### **Continue.dev**
Add to your `.continue/config.json`:
```json
{
  "mcpServers": [
    {
      "name": "fdep",
      "command": "fdep-mcp-server",
      "args": [],
      "env": {
        "FDEP_PATH": "/path/to/your/fdep/output"
      }
    }
  ]
}
```

**Note**: The first time running the server takes time as it imports and processes the FDEP data.

### **Generic MCP Client**
For any MCP-compatible client:
```json
{
  "server_name": "fdep-haskell-analysis",
  "command": "fdep-mcp-server",
  "args": [],
  "environment": {
    "FDEP_PATH": "/path/to/your/fdep/output",
    "DB_HOST": "localhost",
    "DB_NAME": "code_as_data",
    "LOG_LEVEL": "INFO"
  }
}
```

**Note**: The first time running the server takes time as it imports and processes the FDEP data.

### **Environment Variables for All Clients**
```bash
# Required
FDEP_PATH=/path/to/your/fdep/output

# Database (if different from defaults)
DB_HOST=localhost
DB_PORT=5432
DB_NAME=code_as_data
DB_USER=postgres
DB_PASSWORD=postgres

# Optional
LOG_LEVEL=INFO
DEV_MODE=false
```

### **Verify Connection**
After configuring your client, verify the connection:
1. Start your MCP client
2. Look for "fdep" in available tools/servers
3. Test with a simple query: `list_modules(limit=5)`
4. You should see your Haskell modules listed


## 🛠️ MCP Tools Available (40+ Total)

### **📁 Module Analysis (7 tools)**
| Tool | Description |
|------|-------------|
| `initialize_database` | Setup database and import FDEP data |
| `list_modules` | Get list of modules with filtering |
| `get_module_details` | Detailed module info with statistics |
| `get_functions_by_module` | List all functions in a module |
| `search_modules` | Pattern-based module search |
| `get_module_dependencies` | Module dependency analysis |
| `get_code_statistics` | Comprehensive codebase statistics |

### **⚡ Function Analysis (8 tools)**
| Tool | Description |
|------|-------------|
| `get_function_details` | Detailed function information |
| `search_functions` | Search functions by pattern |
| `get_most_called_functions` | Find frequently called functions |
| `get_function_call_graph` | Function call hierarchy |
| `get_function_callers` | Who calls this function |
| `get_function_callees` | What functions this calls |
| `analyze_function_complexity` | Function complexity metrics |
| `get_function_context` | Complete function context with dependencies |

### **🏗️ Type System Analysis (6 tools)**
| Tool | Description |
|------|-------------|
| `list_types` | Get types by module/pattern with categories |
| `get_type_details` | Type info with constructors/fields |
| `search_types` | Advanced type search with filtering |
| `get_type_dependencies` | Type dependency analysis |
| `analyze_type_usage` | Type usage patterns |
| `get_nested_types` | Get nested type definitions |

### **📚 Class Analysis (3 tools)**
| Tool | Description |
|------|-------------|
| `list_classes` | Get class definitions with filtering |
| `get_class_details` | Class info with instances |
| `search_classes` | Pattern-based class search |

### **📦 Import Analysis (4 tools)**
| Tool | Description |
|------|-------------|
| `analyze_imports` | Import patterns and dependencies |
| `get_import_graph` | Module import relationship graphs |
| `find_unused_imports` | Potential cleanup candidates |
| `get_import_details` | Comprehensive import information |

### **🔍 Advanced Queries (4 tools)**
| Tool | Description |
|------|-------------|
| `execute_query` | Basic SQL queries |
| `execute_advanced_query` | JSON-based complex queries with joins |
| `execute_custom_query` | Custom SQL queries with parameters |
| `find_cross_module_calls` | Cross-module function usage |

### **🎯 Pattern Analysis (5 tools)**
| Tool | Description |
|------|-------------|
| `find_similar_functions` | Find functions similar to a given function |
| `find_code_patterns` | Find recurring code patterns |
| `group_similar_functions` | Group functions by similarity |
| `build_type_dependency_graph` | Build comprehensive type dependency graph |
| `analyze_type_relationships` | Analyze deep type relationships |

### **📍 Source Location (3 tools)**
| Tool | Description |
|------|-------------|
| `find_element_by_location` | Find code elements by source location |
| `get_location_context` | Get context around a source location |
| `generate_function_imports` | Generate import statements for functions |

### **🔬 Enhanced Analysis (3 tools)**
| Tool | Description |
|------|-------------|
| `pattern_match_code` | Advanced pattern matching for code structures |
| `analyze_cross_module_dependencies` | Comprehensive dependency analysis |
| `enhanced_function_call_graph` | Enhanced call graphs with advanced options |

## 🔍 Example Queries

### **Basic Analysis**
```python
# Search for validation functions
search_functions(pattern="validation", limit=10)

# Get details about main functions  
get_function_details(function_name="main")

# Find most called functions
get_most_called_functions(limit=20)

# List modules in a specific area
list_modules(limit=50)
```

### **Advanced Analysis**
```python
# Get function call hierarchy
get_function_call_graph(function_name="processData", depth=3)

# Analyze type dependencies
get_type_dependencies(type_name="User", include_dependents=true)

# Find cross-module function calls
find_cross_module_calls(source_module="Services", target_module="Database")

# Complex JSON query
execute_advanced_query({
  "type": "function",
  "conditions": [
    {"field": "name", "operator": "like", "value": "%Handler%"}
  ],
  "limit": 50
})
```

### **Architectural Analysis**
```python
# Module dependency analysis
get_module_dependencies(module_name="Core.Services", include_dependents=true)

# Import relationship graph
get_import_graph(root_module="Main", depth=3)

# Complexity analysis
analyze_function_complexity(module_name="BusinessLogic", min_complexity=5)

# Comprehensive statistics
get_code_statistics(include_details=true)
```

## ⚙️ Configuration

### Environment Variables (.env)
```bash
# Database
DB_USER=postgres
DB_PASSWORD=postgres  
DB_HOST=localhost
DB_PORT=5432
DB_NAME=code_as_data

# FDEP Data Source
FDEP_PATH=/path/to/your/fdep/output

# Logging
LOG_LEVEL=INFO
```

### Spider Plugin Integration

For Haskell projects using GHC 9.2.8:

1. Add Spider flake input
2. Configure cabal with fdep and fieldInspector plugins
3. Run socket server during build
4. Generate FDEP output for analysis

---

### **Tool Distribution**
- 📁 **Module Analysis**: 7 tools  
- ⚡ **Function Analysis**: 8 tools  
- 🏗️ **Type System**: 6 tools  
- 📚 **Class Analysis**: 3 tools  
- 📦 **Import Analysis**: 4 tools  
- 🔍 **Advanced Queries**: 4 tools
- 🎯 **Pattern Analysis**: 5 tools
- 📍 **Source Location**: 3 tools
- 🔬 **Enhanced Analysis**: 3 tools

