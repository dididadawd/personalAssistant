# app/api/payments_routes.py
"""
API Routes for Payments endpoints.
"""

from flask import Blueprint, request, jsonify

# Import database functions
from ..db.json_manager import find_records, add_record, delete_record

# Import utilities
from ..utils.logger import log_and_print
from ..services.main_service import add_payment

# Import agent
from ..core.agent import active_agent as agent_class

# Create blueprint
payments_bp = Blueprint('payments', __name__)

# Global reference to the active agent instance
_active_agent = None

def set_active_agent(agent):
    global _active_agent
    _active_agent = agent

def get_agent():
    if _active_agent:
        return _active_agent
    from flask import current_app
    return current_app.config.get('ACTIVE_AGENT')


# =========================================================================================
#  Payment Routes
# =========================================================================================

@payments_bp.route('', methods=['GET'])
def get_payments():
    """Get all payments."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500

    try:
        payments = find_records(agent, 'payments', {})
        if isinstance(payments, str):
            return jsonify({"payments": []})
        return jsonify({"payments": payments or []})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@payments_bp.route('/add', methods=['POST'])
def add_payment_endpoint():
    """Add a new payment."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500

    try:
        data = request.json or {}
        name = data.get("name")
        amount = data.get("amount")
        payment_day = data.get("payment_day")
        currency = data.get("currency", "ILS")

        if not name or not amount or not payment_day:
            return jsonify({"error": "Missing required fields"}), 400

        result = add_payment(agent, name, amount, payment_day, currency)
        return jsonify(result)
    except Exception as e:
        log_and_print(f"Error adding payment: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500


@payments_bp.route('/delete', methods=['POST'])
def delete_payment_endpoint():
    """Delete a payment."""
    agent = get_agent()
    if not agent:
        return jsonify({"error": "Agent not loaded"}), 500

    try:
        data = request.json or {}
        query = data.get("query", {})

        if not query:
            return jsonify({"error": "Missing query parameter"}), 400

        result = delete_record(agent, 'payments', query)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Export the blueprint
__all__ = ['payments_bp', 'set_active_agent']