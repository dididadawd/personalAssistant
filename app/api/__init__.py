# app/api/__init__.py
"""
Shared utilities and imports for API routes.
This module provides common imports needed across all API blueprints.
"""
# Initialize everything
import os

# --- Shared constants ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PERSONAS_DIR = os.path.join(BASE_DIR, "personas")

# Re-export for convenience
__all__ = ['PERSONAS_DIR']