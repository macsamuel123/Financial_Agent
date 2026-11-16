import os
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from app.db import get_connection
from app.tools import calculate_tfsa_room, get_member_accounts, list_members
from app.governance import mask_pii, set_rls_context, log_agent_action
from app.config import embedding, SCORE_THRESHOLD

# Load environment variables
load_dotenv()

# Initialize FastMCP server
mcp = FastMCP("Canadian Financial Intelligence MCP")


@mcp.tool()
def mcp_list_members(session_id: str = "default-session") -> str:
    """List all registered members in the system with their IDs and masked names."""
    conn = get_connection()
    try:
        # Admin scope required to list directory; mask PII before returning
        set_rls_context(conn, "ADMIN")
        results = list_members(conn)
        safe_results = mask_pii(results)

        log_agent_action(
            session_id=session_id,
            actor_member_id=None,
            tool_name="mcp_list_members",
            input_payload={},
            output_summary=f"Listed {len(safe_results)} masked member records",
            status="SUCCESS",
        )
        return str(safe_results)
    except Exception as e:
        log_agent_action(
            session_id=session_id,
            actor_member_id=None,
            tool_name="mcp_list_members",
            input_payload={},
            output_summary=str(e),
            status="ERROR",
        )
        raise
    finally:
        conn.close()


@mcp.tool()
def mcp_get_member_accounts(member_id: int, session_id: str = "default-session") -> str:
    """Retrieve all masked financial accounts (TFSA, savings, checkings) for a specific member."""
    conn = get_connection()
    try:
        # Enforce Postgres RLS isolation for this member
        set_rls_context(conn, member_id)
        results = get_member_accounts(conn, member_id)
        safe_results = mask_pii(results)

        log_agent_action(
            session_id=session_id,
            actor_member_id=member_id,
            tool_name="mcp_get_member_accounts",
            input_payload={"member_id": member_id},
            output_summary=f"Returned {len(safe_results)} masked accounts",
            status="SUCCESS",
        )
        return str(safe_results)
    except Exception as e:
        log_agent_action(
            session_id=session_id,
            actor_member_id=member_id,
            tool_name="mcp_get_member_accounts",
            input_payload={"member_id": member_id},
            output_summary=str(e),
            status="ERROR",
        )
        raise
    finally:
        conn.close()


@mcp.tool()
def mcp_calculate_tfsa_room(member_id: int, year: int = 2025, session_id: str = "default-session") -> str:
    """Calculate precise TFSA contribution room for a member for a given year based on CRA rules."""
    conn = get_connection()
    try:
        # Enforce Postgres RLS isolation for this member
        set_rls_context(conn, member_id)
        room = calculate_tfsa_room(conn, member_id, year)
        result_str = f"Member {member_id} TFSA contribution room for {year}: ${room:,.2f}"

        log_agent_action(
            session_id=session_id,
            actor_member_id=member_id,
            tool_name="mcp_calculate_tfsa_room",
            input_payload={"member_id": member_id, "year": year},
            output_summary=result_str,
            status="SUCCESS",
        )
        return result_str
    except ValueError as e:
        err_msg = str(e)
        log_agent_action(
            session_id=session_id,
            actor_member_id=member_id,
            tool_name="mcp_calculate_tfsa_room",
            input_payload={"member_id": member_id, "year": year},
            output_summary=err_msg,
            status="ERROR",
        )
        return err_msg
    except Exception as e:
        log_agent_action(
            session_id=session_id,
            actor_member_id=member_id,
            tool_name="mcp_calculate_tfsa_room",
            input_payload={"member_id": member_id, "year": year},
            output_summary=str(e),
            status="ERROR",
        )
        raise
    finally:
        conn.close()

@mcp.tool()
def mcp_search_cra_knowledge(search_query: str, session_id: str = "default-session") -> str:
    """Semantic search over official CRA TFSA guidance stored in pgvector.
    Returns up to 3 chunks with page numbers and similarity scores."""
    conn = get_connection()
    try:
        query_vector = embedding.embed_query(search_query)

        with conn.cursor() as cursor:
            # <=> is cosine distance, so similarity = 1 - distance
            cursor.execute(
                """
                SELECT chunk_text, source_document, page,
                       1 - (embedding <=> %s::vector) AS similarity
                FROM cra_knowledge_base.documents
                ORDER BY embedding <=> %s::vector
                LIMIT 3;
                """,
                (str(query_vector), str(query_vector)),
            )
            rows = cursor.fetchall()

        results = [
            {"text": text, "source": source, "page": page, "similarity": round(float(sim), 3)}
            for text, source, page, sim in rows
            if sim >= SCORE_THRESHOLD
        ]

        log_agent_action(
            session_id=session_id,
            actor_member_id=None,
            tool_name="mcp_search_cra_knowledge",
            input_payload={"search_query": search_query},
            output_summary=f"Retrieved {len(results)} chunks above {SCORE_THRESHOLD} similarity",
            status="SUCCESS",
        )

        if not results:
            return "No relevant CRA guidance found for this question."
        return str(mask_pii(results))
    except Exception as e:
        log_agent_action(
            session_id=session_id,
            actor_member_id=None,
            tool_name="mcp_search_cra_knowledge",
            input_payload={"search_query": search_query},
            output_summary=str(e),
            status="ERROR",
        )
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    mcp.run()