import json
import re
from functools import wraps
from app.db import get_connection

def mask_pii(data):
    """Recursively mask sensitive PII fields and regex patterns before sending to the LLM."""
    if isinstance(data, str):
        # Mask Canadian SIN patterns (e.g., 123-456-789 or 123 456 789)
        data = re.sub(r'\b\d{3}[-\s]?\d{3}[-\s]?\d{3}\b', '[REDACTED-SIN]', data)
        # Mask email addresses
        data = re.sub(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', '[REDACTED-EMAIL]', data)
        return data
    elif isinstance(data, dict):
        masked = {}
        sensitive_keys = {'sin', 'ssn', 'email', 'phone', 'address', 'date_of_birth', 'birth_year'}
        for k, v in data.items():
            if k.lower() in sensitive_keys:
                masked[k] = '[MASKED]'
            elif k.lower() == 'account_id' and isinstance(v, (int, str)):
                s = str(v)
                masked[k] = f"****{s[-4:]}" if len(s) > 4 else "****"
            else:
                masked[k] = mask_pii(v)
        return masked
    elif isinstance(data, list):
        return [mask_pii(item) for item in data]
    return data

def set_rls_context(connection, actor_member_id: str | int):
    """Bind the current database transaction to a specific member ID for Postgres RLS."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('app.current_member_id', %s, true);",
            (str(actor_member_id),)
        )

def log_agent_action(session_id: str, actor_member_id: int | None, tool_name: str, input_payload: dict, output_summary: str, status: str):
    """Write an immutable audit entry to Postgres."""
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO agent_audit_log (session_id, actor_member_id, tool_name, input_payload, output_summary, status)
                VALUES (%s, %s, %s, %s::jsonb, %s, %s)
                """,
                (
                    session_id,
                    actor_member_id,
                    tool_name,
                    json.dumps(mask_pii(input_payload)),
                    str(output_summary)[:500],
                    status,
                ),
            )
        conn.commit()
    finally:
        conn.close()