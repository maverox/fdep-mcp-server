#!/usr/bin/env python3
"""
Combined script to set up database schema and import FDEP dump files.
"""
import os
import sys
import argparse
import time
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# Import code_as_data modules after setting environment
from code_as_data.db.connection import SessionLocal, engine, Base
from code_as_data.db.models import *
from code_as_data.services.dump_service import DumpService
from sqlalchemy import text

from fdep_mcp.config import config  # Add the project root to the path

def setup_database(drop_tables: bool = False, verbose: bool = False):
    """
    Set up the database schema.

    Args:
        drop_tables: Whether to drop existing tables
        verbose: Whether to show verbose output
    """
    if drop_tables:
        if verbose:
            print("Dropping existing tables...")
        Base.metadata.drop_all(engine)

    if verbose:
        print("Creating database tables...")
    Base.metadata.create_all(engine)
    
    if verbose:
        print("Database schema setup complete.")


def validate_fdep_directory(fdep_path: str) -> bool:
    """
    Validate that the provided FDEP directory exists and contains valid FDEP data.
    
    Args:
        fdep_path: Path to the FDEP directory
        
    Returns:
        True if valid, False otherwise
    """
    if not fdep_path or not os.path.exists(fdep_path):
        return False
    
    if not os.path.isdir(fdep_path):
        return False
    
    # Check for JSON files (should contain .json files)
    for root, dirs, files in os.walk(fdep_path):
        if any(f.endswith('.json') for f in files):
            return True
    
    return False


def import_dumps(fdep_path: str, clear_db: bool = False, verbose: bool = False):
    """
    Import dump files into the database.

    Args:
        fdep_path: Path to the fdep files
        clear_db: Whether to clear the database before importing
        verbose: Whether to show verbose output
    """
    # Validate path
    if not validate_fdep_directory(fdep_path):
        print(f"Error: Invalid FDEP directory: {fdep_path}")
        sys.exit(1)

    if verbose:
        print(f"Using FDEP directory: {fdep_path}")
        print(f"Target database: {config.db_name} on {config.db_host}:{config.db_port}")

    # Initialize dump service
    dump_service = DumpService(fdep_path, fdep_path)
    
    # Initialize database session
    db = SessionLocal()
    
    try:
        # Clear database if requested
        if clear_db:
            if verbose:
                print("Clearing existing data...")
            
            # Execute raw SQL to delete data while maintaining schema
            truncate_sql = text(
                "TRUNCATE TABLE module, function, where_function, import, type, constructor, "
                "field, class, instance, instance_function, function_dependency, type_dependency "
                "CASCADE"
            )
            db.execute(truncate_sql)
            db.commit()
            
            if verbose:
                print("Database cleared successfully")

        # Import dumps
        if verbose:
            print("Importing dump files...")
        
        start_time = time.time()

        # Process and insert data
        dump_service.insert_data()

        elapsed_time = time.time() - start_time
        
        if verbose:
            print(f"Import completed successfully in {elapsed_time:.2f} seconds.")
        
        # Verify data was imported
        try:
            result = db.execute(text("SELECT COUNT(*) FROM module"))
            module_count = result.scalar()
            result = db.execute(text("SELECT COUNT(*) FROM function"))
            function_count = result.scalar()
            
            if verbose:
                print(f"Verification: {module_count} modules and {function_count} functions imported")
            else:
                print(f"Import completed: {module_count} modules, {function_count} functions")
        except Exception as e:
            if verbose:
                print(f"Could not verify import: {e}")
                
    except Exception as e:
        print(f"Error during import: {e}")
        if verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Set up database schema and import FDEP dump files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
        Examples:
          %(prog)s /path/to/fdep/data                    # Import with existing schema
          %(prog)s /path/to/fdep/data --setup            # Set up schema first, then import
          %(prog)s /path/to/fdep/data --setup --drop     # Drop tables, recreate, then import
          %(prog)s /path/to/fdep/data --clear --verbose  # Clear data and import with verbose output
          
          # Using environment variable
          export FDEP_PATH=/path/to/fdep/data
          %(prog)s --setup --verbose
        """
    )
    
    parser.add_argument(
        "fdep_path",
        nargs="?",
        help="Path to the FDEP files directory (can also be set via FDEP_PATH environment variable)"
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Set up database schema before importing"
    )
    parser.add_argument(
        "--drop",
        action="store_true",
        help="Drop existing tables before setting up schema (requires --setup)"
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear existing data before importing (preserves schema)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show verbose output"
    )
    
    args = parser.parse_args()
    
    # Validate arguments
    if args.drop and not args.setup:
        print("Error: --drop requires --setup", file=sys.stderr)
        sys.exit(1)
    
    # Determine FDEP path from argument or config (which loads from env)
    fdep_path = args.fdep_path or config.fdep_path
    
    if not fdep_path:
        print("Error: FDEP path must be provided as argument or via FDEP_PATH environment variable", file=sys.stderr)
        parser.print_help()
        sys.exit(1)
    
    if args.verbose:
        print(f"Database configuration:")
        print(f"  Host: {config.db_host}")
        print(f"  Port: {config.db_port}")
        print(f"  Database: {config.db_name}")
        print(f"  User: {config.db_user}")
        print()
    
    # Set up database schema if requested
    if args.setup:
        setup_database(drop_tables=args.drop, verbose=args.verbose)
    
    # Import the data
    import_dumps(fdep_path, args.clear, args.verbose)


if __name__ == "__main__":
    main()
