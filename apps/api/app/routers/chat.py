from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.app.db import AppSetting, ChatMessage, Dataset, get_db
from apps.api.app.services.chat_advisor import answer_user_query, get_or_create_initial_messages
from apps.api.app.services.dataset_tools import OllamaUnavailableError

router = APIRouter(prefix="/datasets/{dataset_id}/chat", tags=["Chat Advisor"])


class ChatMessageResponse(BaseModel):
    id: str
    role: str
    content: str
    card_type: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    created_at: str


class ChatPromptRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)
    use_cot: Optional[bool] = None  # None = use server default from settings


@router.get("/messages", response_model=List[ChatMessageResponse])
def get_chat_messages(dataset_id: str, db: Session = Depends(get_db)):
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(
            status_code=404,
            detail={"code": "DATASET_NOT_FOUND", "message": f"Dataset '{dataset_id}' not found."},
        )

    msgs = get_or_create_initial_messages(dataset, db)
    return [
        ChatMessageResponse(
            id=m.id,
            role=m.role,
            content=m.content,
            card_type=m.card_type,
            payload=m.payload,
            created_at=m.created_at.isoformat(),
        )
        for m in msgs
    ]


@router.post("", response_model=ChatMessageResponse)
async def post_chat_message(
    dataset_id: str,
    req: ChatPromptRequest,
    db: Session = Depends(get_db),
):
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(
            status_code=404,
            detail={"code": "DATASET_NOT_FOUND", "message": f"Dataset '{dataset_id}' not found."},
        )

    # Resolve COT setting: per-request override > server default
    if req.use_cot is not None:
        use_cot = req.use_cot
    else:
        cot_row = db.query(AppSetting).filter(AppSetting.key == "cot_enabled").first()
        use_cot = cot_row.value.lower() in ("true", "1", "yes") if cot_row else True

    # Generate assistant answer FIRST; never persist anything if Ollama is unavailable or fails
    try:
        reply_text, card_type, card_payload = await answer_user_query(dataset, req.message, db, use_cot=use_cot)
    except OllamaUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "OLLAMA_UNAVAILABLE",
                "message": str(exc),
                "hint": "Start Ollama locally (`ollama serve`) and ensure the configured model is pulled, then try again.",
            },
        )

    user_msg = ChatMessage(
        dataset_id=dataset_id,
        role="user",
        content=req.message,
    )
    assistant_msg = ChatMessage(
        dataset_id=dataset_id,
        role="assistant",
        content=reply_text,
        card_type=card_type,
    )
    assistant_msg.payload = card_payload
    db.add(user_msg)
    db.add(assistant_msg)
    db.commit()
    db.refresh(assistant_msg)

    return ChatMessageResponse(
        id=assistant_msg.id,
        role=assistant_msg.role,
        content=assistant_msg.content,
        card_type=assistant_msg.card_type,
        payload=assistant_msg.payload,
        created_at=assistant_msg.created_at.isoformat(),
    )


@router.delete("/messages")
def reset_chat(dataset_id: str, db: Session = Depends(get_db)):
    db.query(ChatMessage).filter(ChatMessage.dataset_id == dataset_id).delete()
    db.commit()
    return {"status": "ok", "message": "Conversation reset."}

