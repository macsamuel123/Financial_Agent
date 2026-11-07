from pydantic import BaseModel
from fastapi import FastAPI
from app.pipeline import orchestrate
from app.tools import list_members
from app.db import get_connection

app =FastAPI(title="TFSA Assistant")

class AskRequest(BaseModel):
    question :str
    member_id: int | None =None

@app.post("/ask")
def ask(request: AskRequest):
    return orchestrate(request.question, request.member_id)

@app.get("/health")
def health():
    return {"status":"ok"}

@app.get("/members")
def members():
    with get_connection() as conn:
        return list_members(conn)